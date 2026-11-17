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
            raise Conflict('Binding definitions are immutable; migration is not yet supported')
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
        db.execute('DELETE FROM schema_version')
        db.execute('INSERT INTO schema_version VALUES(3)')
        return {'commit': self.store.publish(db, ctx, branch, expected_head, manifest),
                'revision': manifest[key]}

    def materialize(self, db, ctx, branch, expected_head, binding, idempotency_key):
        if not isinstance(binding, str) or not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise S2Error('Binding and bounded idempotency key required')
        request = digest({'actor': ctx.principal, 'base': expected_head, 'binding': binding})
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
        if len(source['rows']) > 1000:
            raise S2Error('Reference materialization is limited to 1000 rows')
        if (manifest['type/' + spec['object_type']] != spec['type_revision'] or
                digest(self.store.revision(db, ctx.tenant, manifest['dataset/' + spec['dataset']])['schema'])
                != spec['source_schema_hash']):
            raise Conflict('Schema changed; explicit binding migration required')
        owned = {}
        for key in manifest:
            if key.startswith('object/'):
                obj = self.store.revision(db, ctx.tenant, manifest[key])
                if obj.get('source_binding', {}).get('binding') == binding:
                    owned[key[7:]] = obj
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
            candidates[oid] = obj
        if set(owned) - set(candidates):
            raise Conflict('Source rows disappeared; deletion reconciliation is required')
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

    def provenance(self, db, ctx, branch, object_id):
        head = self.store.branch(db, ctx.tenant, branch)['head']
        manifest = self.store.manifest(db, ctx.tenant, head)
        obj = self.kernel.resource(db, ctx, manifest, 'object/' + object_id)
        self.security.check_marking(ctx, obj['marking'])
        self.kernel.check_definition_access(ctx, self.kernel.resource(db, ctx, manifest, 'type/' + obj['type']))
        return {'commit': head, 'object_revision': manifest['object/' + object_id],
                'source_binding': obj.get('source_binding')}
