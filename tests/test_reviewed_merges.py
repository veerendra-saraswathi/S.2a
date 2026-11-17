# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
import json
import unittest
from unittest.mock import patch
import test_bindings as base
from test_file_storage import HAS_PARQUET, HAS_ICEBERG
from s2.service import Service
from s2_platform import Conflict, Forbidden, NotFound

class MergeTests(unittest.TestCase):
    storage_backend = None
    setUp = base.BindingTests.setUp
    tearDown = base.BindingTests.tearDown
    call = base.BindingTests.call
    head = base.BindingTests.head
    member = base.BindingTests.member
    dataset = base.BindingTests.dataset
    tx = base.BindingTests.tx
    define = base.BindingTests.define
    setup_binding = base.BindingTests.setup_binding
    build = base.BindingTests.build
    objects = base.BindingTests.objects

    def prepare(self):
        self.setup_binding()
        self.build()
        self.child = self.call('branches.fork', branch=self.branch, name='scenario')['branch']

    def edit(self, branch, index=0, status='reviewed'):
        obj = self.objects(branch)[index]
        self.call('objects.put', branch=branch, expected_head=self.head(branch), object_id=obj['id'],
                  object_type='Asset', values={**obj['values'], 'status': status}, expected_revision=obj['revision'])

    def params(self):
        return dict(branch=self.branch, source=self.child, expected_head=self.head(),
                    expected_source=self.head(self.child), reason='Review engineering scenario')

    def preview(self):
        params = self.params()
        return params, self.call('branches.merge_preview', **params)

    def apply(self, params, plan, key='merge', token=None):
        return self.service.call(token or self.token, 'branches.merge_reviewed', **params,
                                 plan_hash=plan['plan_hash'], idempotency_key=key)

    def test_preview_and_two_parent_publication(self):
        self.prepare(); self.edit(self.child)
        with self.service.store.connect() as db:
            counts = tuple(db.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0] for t in ('commits', 'revisions'))
        params, plan = self.preview()
        with self.service.store.connect() as db:
            self.assertEqual(counts, tuple(db.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0] for t in ('commits', 'revisions')))
        result = self.apply(params, plan)
        self.assertEqual(self.head(self.child), params['expected_source'])
        self.assertEqual(self.objects()[0]['values']['status'], 'reviewed')
        with self.service.store.connect() as db:
            parents = json.loads(db.execute('SELECT parents FROM commits WHERE id=?', (result['commit'],)).fetchone()[0])
            self.assertEqual(parents, [params['expected_head'], params['expected_source']])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM commits').fetchone()[0], counts[0]+1)
        self.assertTrue(self.call('audit.verify')['valid'])

    def test_disjoint_objects_preserve_both_edits(self):
        self.prepare(); self.edit(self.child, 0, 'source'); self.edit(self.branch, 1, 'target')
        self.apply(*self.preview())
        self.assertEqual([o['values']['status'] for o in self.objects()], ['source', 'target'])

    def test_same_object_conflicts_without_publication(self):
        self.prepare(); self.edit(self.child, status='source'); self.edit(self.branch, status='approved')
        old = self.head()
        with self.assertRaises(Conflict): self.preview()
        self.assertEqual(self.head(), old)
        self.assertEqual(self.objects()[0]['values']['status'], 'approved')

    def source_update(self, materialize=False, delete=False):
        target = self.branch
        try:
            self.branch = self.child
            self.tx([{'values': {'id': 'pump-01'} if delete else {'id': 'pump-01', 'value': 31}}], operation='DELETE' if delete else 'UPDATE', key='refresh')
            if materialize: self.build(key='refreshed')
        finally:
            self.branch = target

    def test_unmaterialized_source_rejected(self):
        self.prepare(); self.source_update()
        with self.assertRaises(Conflict): self.preview()

    def test_materialized_source_merges(self):
        self.prepare(); self.source_update(materialize=True)
        self.apply(*self.preview())
        self.assertIn(31, [o['values']['value'] for o in self.objects()])

    def test_source_refresh_conflicts_with_operational_edit(self):
        self.prepare(); self.source_update(materialize=True); self.edit(self.branch, status='approved')
        with self.assertRaises(Conflict): self.preview()

    def test_absence_requires_reviewed_retention(self):
        self.prepare(); self.source_update(delete=True)
        with self.assertRaises(Conflict): self.preview()
        plan = self.call('bindings.preview', branch=self.child, expected_head=self.head(self.child), binding='assets')
        self.call('bindings.materialize', branch=self.child, expected_head=self.head(self.child), binding='assets',
                  idempotency_key='retain', missing_policy='retain', expected_missing=plan['missing'], reason='Keep laboratory history')
        self.apply(*self.preview())
        self.assertIn('MISSING', [o.get('source_presence', 'PRESENT') for o in self.objects()])

    def test_migrated_source_merges_with_pinned_provenance(self):
        import copy
        self.prepare()
        schema = copy.deepcopy(base.TARGET)
        schema['properties']['note'] = {'type': 'string'}
        self.call('ontology.define', branch=self.child, expected_head=self.head(self.child), name='Asset', definition=schema)
        with self.assertRaises(Conflict): self.preview()
        params = dict(branch=self.child, expected_head=self.head(self.child), binding='assets',
                      mappings={'id': 'id', 'value': 'value'}, action_properties=['status', 'note'],
                      initial_values={'status': 'pending', 'note': 'reviewed'}, reason='Add review note')
        plan = self.call('bindings.migration_preview', **params)
        self.call('bindings.migrate', **params, plan_hash=plan['plan_hash'], idempotency_key='migrate')
        self.apply(*self.preview())
        self.assertEqual({o['values']['note'] for o in self.objects()}, {'reviewed'})

    def test_approved_action_on_target_is_not_overwritten(self):
        self.prepare(); self.source_update(materialize=True)
        obj = self.objects()[0]
        self.call('actions.define', branch=self.branch, expected_head=self.head(), name='Inspect',
                  object_type='Asset', writable_properties=['status'], approvals=1)
        action = self.call('actions.submit', branch=self.branch, expected_head=self.head(), action='Inspect',
                          edits=[{'object': obj['id'], 'expected_revision': obj['revision'], 'set': {'status': 'inspected'}}],
                          idempotency_key='inspect')
        reviewer = self.member('admin', 3, ['ENG'])
        self.service.call(reviewer['token'], 'actions.approve', branch=self.branch,
                          invocation=action['invocation'], effect_hash=action['effect_hash'])
        self.call('actions.execute', branch=self.branch, invocation=action['invocation'])
        old = self.head()
        with self.assertRaises(Conflict): self.preview()
        self.assertEqual(self.head(), old)
        self.assertEqual(self.objects()[0]['values']['status'], 'inspected')

    def test_stale_source_and_target(self):
        self.prepare(); self.edit(self.child)
        params, plan = self.preview()
        self.edit(self.child, status='new source')
        with self.assertRaises(Conflict): self.apply(params, plan)
        params, plan = self.preview()
        self.edit(self.branch, 1)
        with self.assertRaises(Conflict): self.apply(params, plan)

    def test_wrong_hash_and_policy_change(self):
        self.prepare(); self.edit(self.child)
        params, plan = self.preview()
        with self.assertRaises(Conflict): self.apply(params, {**plan, 'plan_hash': 'wrong'})
        self.call('policy.restrict', branch=self.branch, resource='dataset/readings/property/value', label={'level': 2})
        with self.assertRaises(Conflict): self.apply(params, plan)
        self.apply(*self.preview())

    def test_restart_replay_and_changed_request(self):
        self.prepare(); self.edit(self.child)
        params, plan = self.preview(); result = self.apply(params, plan)
        self.service = Service(self.db)
        self.assertEqual(self.apply(params, plan), result)
        self.assertEqual(self.head(), result['commit'])
        with self.assertRaises(Conflict): self.apply({**params, 'reason': 'changed'}, plan)

    def test_current_policy_on_replay_and_no_hidden_conflict(self):
        self.prepare(); self.edit(self.child)
        low = self.member('admin')
        params = self.params()
        plan = self.service.call(low['token'], 'branches.merge_preview', **params)
        self.apply(params, plan, token=low['token'])
        self.call('policy.restrict', branch=self.branch, resource='dataset/readings', label={'level': 2})
        with self.assertRaises(Forbidden): self.apply(params, plan, token=low['token'])
        with self.assertRaises(Forbidden):
            self.service.call(low['token'], 'branches.merge_preview', **self.params())

    def test_roles_tenants_and_project_boundary(self):
        self.prepare(); self.edit(self.child)
        params = self.params()
        with self.assertRaises(Forbidden):
            self.service.call(self.member('editor')['token'], 'branches.merge_preview', **params)
        with self.assertRaises(NotFound):
            self.service.call(self.service.bootstrap('Other', 3, ['ENG'])['token'], 'branches.merge_preview', **params)
        other = self.call('projects.create', name='Other project')['branch']
        with self.assertRaises(Conflict):
            self.call('branches.merge_preview', **{**params, 'source': other, 'expected_source': self.head(other)})

    def test_audit_failure_rolls_back_commit_and_replay_record(self):
        self.prepare(); self.edit(self.child)
        params, plan = self.preview()
        with patch.object(self.service.security, 'audit', side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError): self.apply(params, plan)
        self.assertEqual(self.head(), params['expected_head'])
        with self.service.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM branch_merges').fetchone()[0], 0)
        self.apply(params, plan)

    def test_concurrent_publications_have_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        self.prepare(); self.edit(self.child)
        params, plan = self.preview()
        def attempt(key):
            try: return self.apply(params, plan, key)['status']
            except Conflict: return 'CONFLICT'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(attempt, ['one', 'two'])), ['COMMITTED', 'CONFLICT'])

    def test_noop_rejected_and_legacy_merge_stays_guarded(self):
        self.prepare()
        with self.assertRaises(Conflict): self.preview()
        self.edit(self.child)
        with self.assertRaises(Conflict):
            self.call('branches.merge', branch=self.branch, source=self.child, expected_head=self.head(), expected_source=self.head(self.child))

@unittest.skipUnless(HAS_PARQUET, 'Parquet dependencies not installed')
class ParquetMergeTests(MergeTests):
    storage_backend = 'parquet'

@unittest.skipUnless(HAS_ICEBERG, 'Iceberg dependencies not installed')
class IcebergMergeTests(MergeTests):
    storage_backend = 'iceberg'
