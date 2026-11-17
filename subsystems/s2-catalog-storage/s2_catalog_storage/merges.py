# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Reviewed direct-fork merge with whole-resource conflicts and binding coherence."""
import json
from s2_platform import Conflict, S2Error, canonical, digest


class ReviewedMerges:
    def __init__(self, store, security, kernel, bindings, check_project, pipelines):
        self.store, self.security, self.kernel = store, security, kernel
        self.bindings, self.check_project = bindings, check_project
        self.pipelines = pipelines

    def _branches(self, db, ctx, branch, source):
        target = self.store.branch(db, ctx.tenant, branch)
        origin = self.store.branch(db, ctx.tenant, source)
        self.check_project(db, ctx, origin['project'])
        if branch == source or target['project'] != origin['project']:
            raise Conflict('Merge requires distinct branches in one project')
        return target, origin

    def _ancestors(self, db, ctx, head):
        seen, todo = set(), [head]
        while todo:
            item = todo.pop()
            if item in seen:
                continue
            seen.add(item)
            row = db.execute('SELECT parents FROM commits WHERE tenant=? AND id=?', (ctx.tenant, item)).fetchone()
            if row is None:
                raise Conflict('Missing commit ancestry')
            todo.extend(json.loads(row[0]))
        return seen

    def _authorize(self, db, ctx, manifest):
        effective = {}
        for key in sorted(manifest):
            value = self.kernel.resource(db, ctx, manifest, key)
            if key.startswith(('type/', 'interface/')):
                self.kernel.check_definition_access(ctx, value)
            elif key.startswith('dataset/'):
                self.kernel.check_definition_access(ctx, value['schema'])
                for row in value['rows']:
                    self.security.check_marking(ctx, row['marking'])
            elif key.startswith('binding/'):
                value['marking'] = self.security.effective(db, ctx, key, value['marking'])
                self.security.check_marking(ctx, value['marking'])
            elif key.startswith(('object/', 'link/', 'linktype/', 'pipeline/')):
                self.security.check_marking(ctx, value.get('marking'))
            effective[key] = digest(value)
        return effective

    def _coherent(self, db, ctx, manifest):
        self.kernel.validate_manifest(db, ctx.tenant, manifest)
        pipeline_proofs = self.pipelines.validate_merged_manifest(db, ctx, manifest)
        for key in manifest:
            if key.startswith('action/'):
                action = self.kernel.resource(db, ctx, manifest, key)
                schema = self.kernel.resource(db, ctx, manifest, 'type/' + action['object_type'])
                if not set(action['writable_properties']).issubset(schema['properties']):
                    raise Conflict('Merged Action references missing properties')
            if key.startswith('object/'):
                obj = self.kernel.resource(db, ctx, manifest, key)
                if 'source_binding' in obj and 'binding/' + obj['source_binding']['binding'] not in manifest:
                    raise Conflict('Derived object has no merged binding')
            if not key.startswith('binding/'):
                continue
            name = key[8:]
            spec, source, target = self.bindings._inputs(db, ctx, manifest, name)
            owned = self.bindings._owned(db, ctx, manifest, name)
            if len(source['rows']) > 1000 or len(owned) > 1000:
                raise S2Error('Reviewed binding merge is limited to 1000 source rows and owned objects per binding')
            snapshot = manifest['dataset/' + spec['dataset']]
            if (spec['type_revision'] != manifest['type/' + spec['object_type']] or
                    spec['source_schema_hash'] != digest(self.store.revision(db, ctx.tenant, snapshot)['schema'])):
                raise Conflict('Merged binding is stale against its schema; migrate before merge')
            rows = {'bound-' + digest({'namespace': spec['namespace'],
                    'key': canonical(row['values'][spec['primary_key']])}): row for row in source['rows']}
            if set(rows) - set(owned):
                raise Conflict('Merged source contains unmaterialized objects; build before merge')
            for oid, obj in owned.items():
                provenance = obj['source_binding']
                state = obj.get('source_state')
                if provenance['binding_revision'] != manifest[key] or provenance['type_revision'] != spec['type_revision']:
                    raise Conflict('Merged object and binding revisions disagree')
                if oid in rows:
                    if (provenance['dataset_revision'] != snapshot or
                            (state and (state['presence'] != 'PRESENT' or state['observed_dataset_revision'] != snapshot))):
                        raise Conflict('Merged object is stale against its source; build before merge')
                    expected = {dest: rows[oid]['values'][src] for dest, src in spec['mappings'].items()
                                if src in rows[oid]['values']}
                    actual = {p: obj['values'][p] for p in spec['mappings'] if p in obj['values']}
                    if canonical(actual) != canonical(expected):
                        raise Conflict('Merged source-owned values disagree with source snapshot')
                elif not state or state['presence'] != 'MISSING' or state['observed_dataset_revision'] != snapshot:
                    raise Conflict('Merged source absence requires reviewed reconciliation')

        return pipeline_proofs

    def _plan(self, db, ctx, branch, source, expected_head, expected_source, reason):
        self.security.require(ctx, 'admin')
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
            raise S2Error('Merge reason requires 1 to 1000 characters')
        target, origin = self._branches(db, ctx, branch, source)
        if target['head'] != expected_head or origin['head'] != expected_source:
            raise Conflict('Source or target branch changed')
        base = origin['base']
        if base not in self._ancestors(db, ctx, expected_head) or base not in self._ancestors(db, ctx, expected_source):
            raise Conflict('No supported direct-fork base')
        common = self.store.manifest(db, ctx.tenant, base)
        ours = self.store.manifest(db, ctx.tenant, expected_head)
        theirs = self.store.manifest(db, ctx.tenant, expected_source)
        # Authorize all inputs before returning resource names, counts or conflicts.
        policies = [self._authorize(db, ctx, m) for m in (common, ours, theirs)]
        merged, conflicts = dict(ours), []
        for key in sorted(set(common) | set(ours) | set(theirs)):
            old, left, right = common.get(key), ours.get(key), theirs.get(key)
            if right == old or left == right:
                continue
            if left != old or right is None:
                conflicts.append(key)
            else:
                merged[key] = right
        if conflicts:
            raise Conflict('Merge conflicts require explicit branch reconciliation: ' + ', '.join(conflicts))
        if merged == ours:
            raise Conflict('No source changes to publish')
        policies.append(self._authorize(db, ctx, merged))
        pipeline_proofs = self._coherent(db, ctx, merged)
        changes = {key: {'previous_revision': ours.get(key), 'proposed_revision': merged[key]}
                   for key in sorted(merged) if merged[key] != ours.get(key)}
        plan = {'branch': branch, 'source': source, 'base_commit': base,
                'target_commit': expected_head, 'source_commit': expected_source,
                'changes': changes, 'reason': reason.strip(), 'pipelines': pipeline_proofs}
        plan['plan_hash'] = digest({'plan': plan, 'manifest': merged, 'policy': policies, 'actor': ctx.principal})
        return merged, plan

    def preview(self, db, ctx, branch, source, expected_head, expected_source, reason):
        return self._plan(db, ctx, branch, source, expected_head, expected_source, reason)[1]

    def apply(self, db, ctx, branch, source, expected_head, expected_source, reason, plan_hash, idempotency_key):
        self.security.require(ctx, 'admin')
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise S2Error('Bounded idempotency key required')
        self._branches(db, ctx, branch, source)
        request = digest({'actor': ctx.principal, 'source': source, 'base': expected_head,
                          'source_head': expected_source, 'reason': reason, 'plan_hash': plan_hash})
        replay = db.execute('SELECT * FROM branch_merges WHERE tenant=? AND branch=? AND idem=?',
                            (ctx.tenant, branch, idempotency_key)).fetchone()
        if replay:
            if replay['request_hash'] != request:
                raise Conflict('Idempotency key reused for a different merge')
            result = json.loads(replay['result'])
            for commit in (expected_head, expected_source, result['commit']):
                self._authorize(db, ctx, self.store.manifest(db, ctx.tenant, commit))
            return result
        merged, plan = self._plan(db, ctx, branch, source, expected_head, expected_source, reason)
        if plan_hash != plan['plan_hash']:
            raise Conflict('Merge effects or policy changed; obtain and review a new preview')
        result = {'commit': self.store.publish(db, ctx, branch, expected_head, merged, expected_source),
                  'status': 'COMMITTED', 'source_commit': expected_source, 'target_parent': expected_head,
                  'plan_hash': plan_hash, 'changed_resources': len(plan['changes']), 'reason': reason.strip()}
        db.execute('INSERT INTO branch_merges VALUES(?,?,?,?,?)',
                   (ctx.tenant, branch, idempotency_key, request, canonical(result)))
        return result
