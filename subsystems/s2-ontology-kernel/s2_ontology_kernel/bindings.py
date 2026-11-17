# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Bounded, atomic snapshot-to-object materialization for the local reference core."""
import json
import re
from s2_platform import S2Error, Conflict, canonical, digest, marking


def joined(*labels):
    labels = [marking(label) for label in labels]
    return {'level': max((x['level'] for x in labels), default=0),
            'compartments': sorted({c for x in labels for c in x['compartments']})}


class Bindings:
    def __init__(self, store, security, kernel):
        self.store, self.security, self.kernel = store, security, kernel

    def _inputs(self, db, ctx, manifest, binding):
        if not isinstance(binding, str) or not binding:
            raise S2Error('Binding name required')
        key = 'binding/' + binding
        spec = self.kernel.resource(db, ctx, manifest, key)
        self.security.check_marking(ctx, self.security.effective(db, ctx, key, spec['marking']))
        source = self.kernel.resource(db, ctx, manifest, 'dataset/' + spec['dataset'])
        target = self.kernel.resource(db, ctx, manifest, 'type/' + spec['object_type'])
        # Complete snapshots only: never materialize a silently filtered dataset.
        self.kernel.check_definition_access(ctx, source['schema'])
        self.kernel.check_definition_access(ctx, target)
        for row in source['rows']:
            self.security.check_marking(ctx, row['marking'])
        return spec, source, target

    def define(self, db, ctx, branch, expected_head, binding, dataset, object_type,
               namespace, mappings, action_properties=None, initial_values=None):
        for value in (binding, dataset, object_type, namespace):
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', value):
                raise S2Error('Binding names and references require 1 to 100 letters, digits, dots, dashes or underscores')
        if not isinstance(mappings, dict) or not mappings or any(
                not isinstance(k, str) or not isinstance(v, str) for k, v in mappings.items()):
            raise S2Error('mappings must map target property names to source field names')
        action_properties = [] if action_properties is None else action_properties
        initial_values = {} if initial_values is None else initial_values
        if (not isinstance(action_properties, list) or any(not isinstance(p, str) for p in action_properties)
                or len(set(action_properties)) != len(action_properties) or not isinstance(initial_values, dict)):
            raise S2Error('Invalid Action property list or initial values')
        manifest = self.store.manifest(db, ctx.tenant, expected_head)
        key = 'binding/' + binding
        if key in manifest:
            raise Conflict('Binding already exists; use reviewed migration')
        source = self.kernel.resource(db, ctx, manifest, 'dataset/' + dataset)
        target = self.kernel.resource(db, ctx, manifest, 'type/' + object_type)
        self.kernel.check_definition_access(ctx, source['schema'])
        self.kernel.check_definition_access(ctx, target)
        props = target['properties']
        if (set(mappings) & set(action_properties) or set(mappings) | set(action_properties) != set(props)
                or set(initial_values) - set(action_properties)):
            raise S2Error('Every target property needs exactly one owner; defaults belong to Action properties')
        for dest, src in mappings.items():
            actual = source['schema']['properties'].get(src)
            if actual is None or actual['type'] != props[dest]['type']:
                raise S2Error('Mapped fields must exist and have identical scalar types')
        self.kernel.validate_values({'properties': {p: props[p] for p in action_properties}}, initial_values)
        label = joined(source['schema'].get('marking'), target.get('marking'),
                       *(p.get('marking') for p in source['schema']['properties'].values()),
                       *(p.get('marking') for p in props.values()))
        self.security.check_marking(ctx, self.security.effective(db, ctx, key, label))
        spec = {'dataset': dataset, 'object_type': object_type, 'namespace': namespace,
                'primary_key': source['primary_key'], 'mappings': mappings,
                'action_properties': sorted(action_properties), 'initial_values': initial_values,
                'type_revision': manifest['type/' + object_type],
                'source_schema_hash': digest(self.store.revision(db, ctx.tenant,
                    manifest['dataset/' + dataset])['schema']), 'marking': label}
        manifest[key] = self.store.put_revision(db, ctx.tenant, 'source_binding', spec)
        # Prevent earlier executables from opening catalogs with derived-policy objects.
        if db.execute('SELECT version FROM schema_version').fetchone()[0] < 3:
            db.execute('DELETE FROM schema_version')
            db.execute('INSERT INTO schema_version VALUES(3)')
        return {'commit': self.store.publish(db, ctx, branch, expected_head, manifest),
                'revision': manifest[key]}

    def _owned(self, db, ctx, manifest, binding):
        owned = {}
        for key in manifest:
            if key.startswith('object/'):
                raw = self.store.revision(db, ctx.tenant, manifest[key])
                if raw.get('source_binding', {}).get('binding') == binding:
                    obj = self.kernel.resource(db, ctx, manifest, key)
                    self.security.check_marking(ctx, obj['marking'])
                    owned[key[7:]] = obj
        return owned

    def preview(self, db, ctx, branch, expected_head, binding):
        manifest = self.store.manifest(db, ctx.tenant, expected_head)
        spec, source, target = self._inputs(db, ctx, manifest, binding)
        owned = self._owned(db, ctx, manifest, binding)
        present = {'bound-' + digest({'namespace': spec['namespace'],
                    'key': canonical(row['values'][spec['primary_key']])}) for row in source['rows']}
        return {'commit': expected_head, 'dataset_revision': manifest['dataset/' + spec['dataset']],
                'missing': {oid: manifest['object/' + oid] for oid in sorted(set(owned) - present)},
                'note': 'Absence from this snapshot is not evidence of physical retirement.'}

    def materialize(self, db, ctx, branch, expected_head, binding, idempotency_key,
                    missing_policy='reject', expected_missing=None, reason=None):
        if not isinstance(binding, str) or not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise S2Error('Binding and bounded idempotency key required')
        if missing_policy not in ('reject', 'retain'):
            raise S2Error('missing_policy must be reject or retain')
        request_data = {'actor': ctx.principal, 'base': expected_head, 'binding': binding}
        if missing_policy == 'retain':
            self.security.require(ctx, 'admin')
            if (not isinstance(expected_missing, dict) or not expected_missing or
                    any(not isinstance(k, str) or not isinstance(v, str) for k, v in expected_missing.items()) or
                    not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000):
                raise S2Error('Retention requires a nonempty preview revision map and a reason of 1 to 1000 characters')
            request_data.update(missing_policy=missing_policy, expected_missing=expected_missing, reason=reason)
        elif expected_missing is not None or reason is not None:
            raise S2Error('Reconciliation parameters require retain policy')
        request = digest(request_data)
        replay = db.execute('SELECT * FROM binding_builds WHERE tenant=? AND branch=? AND idem=?',
                            (ctx.tenant, branch, idempotency_key)).fetchone()
        if replay and replay['request_hash'] != request:
            raise Conflict('Idempotency key reused for a different build')
        if not replay and self.store.branch(db, ctx.tenant, branch)['head'] != expected_head:
            raise Conflict('Stale branch head')
        manifest = self.store.manifest(db, ctx.tenant, expected_head)
        spec, source, target = self._inputs(db, ctx, manifest, binding)
        if replay:
            result = json.loads(replay['result'])
            published = self.store.manifest(db, ctx.tenant, result['commit'])
            for oid in result['objects']:
                obj = self.kernel.resource(db, ctx, published, 'object/' + oid)
                self.security.check_marking(ctx, obj['marking'])
            return result
        if self.store.branch(db, ctx.tenant, branch)['head'] != expected_head:
            raise Conflict('Stale branch head')
        candidates = self._candidates(db, ctx, manifest, binding, spec, source, target,
                                      expected_head, missing_policy, expected_missing, reason)
        if any('source_state' in obj for obj in candidates.values()):
            self._format(db, 4)
        versions = {}
        for oid, obj in candidates.items():
            versions[oid] = self.store.put_revision(db, ctx.tenant, 'object', obj)
            manifest['object/' + oid] = versions[oid]
        self.kernel.validate_manifest(db, ctx.tenant, manifest)
        result = {'commit': self.store.publish(db, ctx, branch, expected_head, manifest),
                  'status': 'COMMITTED', 'objects': versions,
                  'dataset_revision': manifest['dataset/' + spec['dataset']],
                  'binding_revision': manifest['binding/' + binding], 'type_revision': spec['type_revision']}
        db.execute('INSERT INTO binding_builds VALUES(?,?,?,?,?)',
                   (ctx.tenant, branch, idempotency_key, request, canonical(result)))
        return result

    def _format(self, db, version):
        if db.execute('SELECT version FROM schema_version').fetchone()[0] < version:
            db.execute('DELETE FROM schema_version')
            db.execute('INSERT INTO schema_version VALUES(?)', (version,))

    def _candidates(self, db, ctx, manifest, binding, spec, source, target,
                    expected_head, missing_policy='reject', expected_missing=None, reason=None):
        if len(source['rows']) > 1000:
            raise S2Error('Reference materialization is limited to 1000 rows')
        if (manifest['type/' + spec['object_type']] != spec['type_revision'] or
                digest(self.store.revision(db, ctx.tenant, manifest['dataset/' + spec['dataset']])['schema'])
                != spec['source_schema_hash']):
            raise Conflict('Schema changed; explicit binding migration required')
        owned = self._owned(db, ctx, manifest, binding)
        if len(owned) > 1000:
            raise S2Error('Reference reconciliation is limited to 1000 owned objects')
        candidates, seen = {}, set()
        for row in source['rows']:
            identity = canonical(row['values'][spec['primary_key']])
            if identity in seen:
                raise Conflict('Duplicate source identity')
            seen.add(identity)
            oid = 'bound-' + digest({'namespace': spec['namespace'], 'key': identity})
            key = 'object/' + oid
            old = self.kernel.resource(db, ctx, manifest, key) if key in manifest else None
            if old and old.get('source_binding', {}).get('binding') != binding:
                raise Conflict('Object identity is already owned by another writer')
            values = ({p: v for p, v in old['values'].items() if p in spec['action_properties']}
                      if old else dict(spec['initial_values']))
            if old:
                prior_spec = self.store.revision(db, ctx.tenant, old['source_binding']['binding_revision'])
                for prop in set(spec['action_properties']) - set(prior_spec['action_properties']):
                    if prop in spec['initial_values']:
                        values[prop] = spec['initial_values'][prop]
            for dest, src in spec['mappings'].items():
                if src in row['values']:
                    values[dest] = row['values'][src]
            dependencies = ['binding/' + binding, 'dataset/' + spec['dataset'],
                            'dataset/' + spec['dataset'] + '/row/' + identity]
            dependencies.extend('dataset/' + spec['dataset'] + '/property/' + prop
                                for prop in source['schema']['properties'])
            label = joined(spec['marking'], source['schema'].get('marking'), row['marking'],
                           target.get('marking'), old['marking'] if old else None,
                           *(p.get('marking') for p in source['schema']['properties'].values()),
                           *(p.get('marking') for p in target['properties'].values()))
            for resource in dependencies:
                label = joined(label, self.security.effective(db, ctx, resource, None))
            obj = self.kernel.check_object_write(db, ctx, manifest, oid, spec['object_type'], values,
                                                label, manifest.get(key), materializing=True)
            obj['source_binding'] = {
                'binding': binding, 'binding_revision': manifest['binding/' + binding],
                'dataset': spec['dataset'], 'dataset_revision': manifest['dataset/' + spec['dataset']],
                'source_key': row['values'][spec['primary_key']], 'type_revision': spec['type_revision'],
                'source_properties': sorted(spec['mappings']), 'property_sources': spec['mappings'],
                'policy_resources': dependencies, 'base_commit': expected_head}
            if old and 'source_state' in old:
                obj['source_state'] = {**old['source_state'], 'presence': 'PRESENT',
                                      'observed_dataset_revision': manifest['dataset/' + spec['dataset']]}
            candidates[oid] = obj
        missing = {oid: manifest['object/' + oid] for oid in sorted(set(owned) - set(candidates))}
        if missing_policy == 'retain':
            if missing != expected_missing:
                raise Conflict('Missing object set or revisions differ from the reviewed preview')
            for oid in missing:
                old = owned[oid]
                label = joined(old['marking'], source['schema'].get('marking'), target.get('marking'),
                    *(p.get('marking') for p in source['schema']['properties'].values()),
                    *(p.get('marking') for p in target['properties'].values()))
                obj = self.kernel.check_object_write(db, ctx, manifest, oid, old['type'], old['values'],
                                                     label, missing[oid], materializing=True)
                obj['source_state'] = {'presence': 'MISSING', 'observed_dataset_revision':
                    manifest['dataset/' + spec['dataset']], 'last_reconciliation': {
                        'policy': 'retain', 'reason': reason.strip(), 'actor': ctx.principal,
                        'base_commit': expected_head, 'prior_object_revision': missing[oid],
                        'absence_dataset_revision': manifest['dataset/' + spec['dataset']]}}
                candidates[oid] = obj
        elif missing:
            raise Conflict('Source rows disappeared; deletion reconciliation is required')
        if len(candidates) > 1000:
            raise S2Error('Reference materialization is limited to 1000 resulting objects')
        return candidates

    def provenance(self, db, ctx, branch, object_id):
        head = self.store.branch(db, ctx.tenant, branch)['head']
        manifest = self.store.manifest(db, ctx.tenant, head)
        obj = self.kernel.resource(db, ctx, manifest, 'object/' + object_id)
        self.security.check_marking(ctx, obj['marking'])
        self.kernel.check_definition_access(ctx, self.kernel.resource(db, ctx, manifest, 'type/' + obj['type']))
        return {'commit': head, 'object_revision': manifest['object/' + object_id],
                'source_binding': obj.get('source_binding'), 'source_state': obj.get('source_state'),
                'binding_migration': self.store.revision(db, ctx.tenant,
                    obj['source_binding']['binding_revision']).get('migration') if 'source_binding' in obj else None}

    def _migration_plan(self, db, ctx, branch, expected_head, binding, mappings,
                        action_properties, initial_values, reason):
        self.security.require(ctx, 'admin')
        if self.store.branch(db, ctx.tenant, branch)['head'] != expected_head:
            raise Conflict('Stale migration base')
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
            raise S2Error('Migration reason requires 1 to 1000 characters')
        if (not isinstance(mappings, dict) or not mappings or
                any(not isinstance(k, str) or not isinstance(v, str) for k, v in mappings.items()) or
                not isinstance(action_properties, list) or any(not isinstance(p, str) for p in action_properties) or
                len(set(action_properties)) != len(action_properties) or not isinstance(initial_values, dict)):
            raise S2Error('Invalid migration mapping, Action properties or defaults')
        manifest = self.store.manifest(db, ctx.tenant, expected_head)
        old, source, target = self._inputs(db, ctx, manifest, binding)
        previous = manifest['binding/' + binding]
        prior_type = self.store.revision(db, ctx.tenant, old['type_revision'])
        self.kernel.no_downgrade(prior_type, target)
        props = target['properties']
        if (set(mappings) & set(action_properties) or set(mappings) | set(action_properties) != set(props)
                or set(initial_values) - set(action_properties)):
            raise S2Error('Every property needs exactly one owner')
        if (not set(old['mappings']).issubset(mappings) or
                not set(old['action_properties']).issubset(action_properties)):
            raise Conflict('Existing property ownership cannot change or be removed')
        # Defaults for existing Action properties are frozen; their values remain operationally owned.
        for prop in old['action_properties']:
            if ((prop in initial_values) != (prop in old['initial_values']) or
                    initial_values.get(prop) != old['initial_values'].get(prop)):
                raise Conflict('Existing Action defaults cannot change in this migration slice')
        if source['primary_key'] != old['primary_key']:
            raise Conflict('Identity changes require a separate migration workflow')
        if any(mappings.get(dest) != src for dest, src in old['mappings'].items() if src == old['primary_key']):
            raise Conflict('Primary-key property mappings cannot change')
        for dest, src in mappings.items():
            actual = source['schema']['properties'].get(src)
            if actual is None or actual['type'] != props[dest]['type']:
                raise S2Error('Mapped fields must exist with identical scalar types')
        self.kernel.validate_values({'properties': {p: props[p] for p in action_properties}}, initial_values)
        label = joined(old['marking'], source['schema'].get('marking'), target.get('marking'),
                       *(p.get('marking') for p in source['schema']['properties'].values()),
                       *(p.get('marking') for p in props.values()),
                       self.security.effective(db, ctx, 'binding/' + binding, None))
        self.security.check_marking(ctx, label)
        proposed = {**old, 'mappings': mappings, 'action_properties': sorted(action_properties),
                    'initial_values': initial_values, 'marking': label,
                    'type_revision': manifest['type/' + old['object_type']],
                    'source_schema_hash': digest(self.store.revision(db, ctx.tenant,
                        manifest['dataset/' + old['dataset']])['schema']),
                    'migration': {'previous_binding_revision': previous, 'base_commit': expected_head,
                                  'actor': ctx.principal, 'reason': reason.strip()}}
        # A dry-run computes canonical revisions without writing revisions or a commit.
        revision = digest({'kind': 'source_binding', 'payload': proposed})
        manifest['binding/' + binding] = revision
        candidates = self._candidates(db, ctx, manifest, binding, proposed, source, target, expected_head)
        effects = {oid: {'previous_revision': manifest.get('object/' + oid),
                         'proposed_revision': digest({'kind': 'object', 'payload': obj})}
                   for oid, obj in sorted(candidates.items())}
        plan_hash = digest({'base': expected_head, 'binding_revision': revision,
                            'dataset_revision': manifest['dataset/' + old['dataset']], 'effects': effects})
        summary = {'commit': expected_head, 'binding': binding, 'previous_binding_revision': previous,
                   'proposed_binding_revision': revision, 'dataset_revision': manifest['dataset/' + old['dataset']],
                   'type_revision': proposed['type_revision'], 'plan_hash': plan_hash, 'objects': effects,
                   'mappings': mappings, 'action_properties': sorted(action_properties),
                   'initial_values': initial_values, 'reason': reason.strip()}
        return manifest, proposed, candidates, summary

    def migration_preview(self, db, ctx, branch, expected_head, binding, mappings,
                          action_properties, initial_values, reason):
        return self._migration_plan(db, ctx, branch, expected_head, binding, mappings,
                                    action_properties, initial_values, reason)[3]

    def migrate(self, db, ctx, branch, expected_head, binding, mappings, action_properties,
                initial_values, reason, plan_hash, idempotency_key):
        self.security.require(ctx, 'admin')
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise S2Error('Bounded idempotency key required')
        request = digest({'actor': ctx.principal, 'base': expected_head, 'binding': binding,
                          'mappings': mappings, 'action_properties': action_properties,
                          'initial_values': initial_values, 'reason': reason, 'plan_hash': plan_hash})
        replay = db.execute('SELECT * FROM binding_migrations WHERE tenant=? AND branch=? AND idem=?',
                            (ctx.tenant, branch, idempotency_key)).fetchone()
        if replay:
            if replay['request_hash'] != request:
                raise Conflict('Idempotency key reused for a different migration')
            manifest = self.store.manifest(db, ctx.tenant, expected_head)
            self._inputs(db, ctx, manifest, binding)
            self._owned(db, ctx, manifest, binding)
            result = json.loads(replay['result'])
            published = self.store.manifest(db, ctx.tenant, result['commit'])
            self._inputs(db, ctx, published, binding)
            self._owned(db, ctx, published, binding)
            return result
        manifest, spec, candidates, summary = self._migration_plan(
            db, ctx, branch, expected_head, binding, mappings, action_properties, initial_values, reason)
        if plan_hash != summary['plan_hash']:
            raise Conflict('Migration effects changed; obtain and review a new preview')
        self.store.put_revision(db, ctx.tenant, 'source_binding', spec)
        versions = {}
        for oid, obj in candidates.items():
            versions[oid] = self.store.put_revision(db, ctx.tenant, 'object', obj)
            manifest['object/' + oid] = versions[oid]
        self.kernel.validate_manifest(db, ctx.tenant, manifest)
        self._format(db, 5)
        result = {'commit': self.store.publish(db, ctx, branch, expected_head, manifest),
                  'status': 'COMMITTED', 'binding_revision': manifest['binding/' + binding],
                  'previous_binding_revision': summary['previous_binding_revision'],
                  'plan_hash': plan_hash, 'objects': versions}
        db.execute('INSERT INTO binding_migrations VALUES(?,?,?,?,?)',
                   (ctx.tenant, branch, idempotency_key, request, canonical(result)))
        return result
