# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
import copy
import unittest
from unittest.mock import patch
import test_foundation as baseline
from test_foundation import PUBLIC
from test_file_storage import HAS_PARQUET, HAS_ICEBERG
from s2.service import Service
from s2_platform import Conflict, Forbidden, NotFound, S2Error


TARGET = {'properties': {**PUBLIC['properties'], 'status': {'type': 'string', 'required': True}}}


class BindingTests(unittest.TestCase):
    storage_backend = None
    setUp = baseline.FoundationTest.setUp
    tearDown = baseline.FoundationTest.tearDown
    call = baseline.FoundationTest.call
    head = baseline.FoundationTest.head
    member = baseline.FoundationTest.member
    dataset = baseline.FoundationTest.dataset
    tx = baseline.FoundationTest.tx
    define = baseline.FoundationTest.define

    def setup_binding(self, rows=None, schema=None, target=None, **overrides):
        self.dataset(schema or PUBLIC)
        self.tx(rows if rows is not None else [{'values': {'id': 'pump-01', 'value': 10}},
                                               {'values': {'id': 'pump-02', 'value': 20}}])
        self.define(target or TARGET)
        params = dict(binding='assets', dataset='readings', object_type='Asset', namespace='lab',
                      mappings={'id': 'id', 'value': 'value'}, action_properties=['status'],
                      initial_values={'status': 'pending'})
        params.update(overrides)
        self.call('bindings.define', branch=self.branch, expected_head=self.head(), **params)

    def build(self, key='build-1', branch=None, base=None, token=None):
        branch = branch or self.branch
        return self.service.call(token or self.token, 'bindings.materialize', branch=branch,
                                 expected_head=base or self.head(branch), binding='assets', idempotency_key=key)

    def objects(self, branch=None, token=None):
        return self.service.call(token or self.token, 'objects.list', branch=branch or self.branch)['objects']

    def test_materialize_pins_provenance_and_restart_retry(self):
        self.setup_binding()
        base = self.head()
        result = self.build(base=base)
        self.assertEqual(len(result['objects']), 2)
        self.assertEqual({o['values']['status'] for o in self.objects()}, {'pending'})
        oid = self.objects()[0]['id']
        p = self.call('bindings.provenance', branch=self.branch, object_id=oid)['source_binding']
        self.assertEqual(p['dataset_revision'], result['dataset_revision'])
        self.assertEqual(p['binding_revision'], result['binding_revision'])
        self.assertEqual(p['base_commit'], base)
        self.service = Service(self.db)
        self.assertEqual(self.build(base=base), result)
        self.assertEqual(self.head(), result['commit'])
        self.assertTrue(self.call('audit.verify')['valid'])
        with self.service.store.transaction() as db:
            self.assertEqual(db.execute('SELECT version FROM schema_version').fetchone()[0], 3)

    def test_refresh_preserves_approved_action_and_provenance(self):
        self.setup_binding()
        self.build()
        obj = self.objects()[0]
        self.call('actions.define', branch=self.branch, expected_head=self.head(), name='Inspect',
                  object_type='Asset', writable_properties=['status'], approvals=1)
        inv = self.call('actions.submit', branch=self.branch, expected_head=self.head(), action='Inspect',
                        edits=[{'object': obj['id'], 'expected_revision': obj['revision'],
                                'set': {'status': 'inspected'}}], idempotency_key='inspect')
        reviewer = self.member('admin', 3, ['ENG'])
        self.service.call(reviewer['token'], 'actions.approve', branch=self.branch,
                          invocation=inv['invocation'], effect_hash=inv['effect_hash'])
        self.call('actions.execute', branch=self.branch, invocation=inv['invocation'])
        self.tx([{'values': {'id': obj['values']['id'], 'value': 99}}], operation='UPDATE', key='refresh')
        self.build(key='build-2')
        updated = self.call('objects.get', branch=self.branch, object_id=obj['id'])
        self.assertEqual(updated['values'], {**obj['values'], 'value': 99, 'status': 'inspected'})
        self.assertIsNotNone(self.call('bindings.provenance', branch=self.branch,
                                      object_id=obj['id'])['source_binding'])

    def test_source_owned_field_rejects_manual_and_action_edits(self):
        self.setup_binding()
        self.build()
        obj = self.objects()[0]
        with self.assertRaises(Forbidden):
            self.call('objects.put', branch=self.branch, expected_head=self.head(), object_id=obj['id'],
                      object_type='Asset', expected_revision=obj['revision'], values={**obj['values'], 'value': 7})
        self.call('actions.define', branch=self.branch, expected_head=self.head(), name='WrongOwner',
                  object_type='Asset', writable_properties=['value'])
        with self.assertRaises(Forbidden):
            self.call('actions.submit', branch=self.branch, expected_head=self.head(), action='WrongOwner',
                      edits=[{'object': obj['id'], 'expected_revision': obj['revision'], 'set': {'value': 7}}],
                      idempotency_key='bad-owner')

    def test_invalid_second_row_has_no_partial_objects(self):
        source = copy.deepcopy(PUBLIC)
        source['properties']['value']['required'] = False
        self.setup_binding(rows=[{'values': {'id': 'a', 'value': 1}}, {'values': {'id': 'b'}}], schema=source)
        base = self.head()
        with self.assertRaises(S2Error):
            self.build()
        self.assertEqual(self.head(), base)
        self.assertEqual(self.objects(), [])

    def test_second_storage_failure_rolls_back_and_retry_succeeds(self):
        self.setup_binding()
        base = self.head()
        original = self.service.store.put_revision
        calls = []
        def fail(db, tenant, kind, payload):
            if kind == 'object':
                calls.append(kind)
                if len(calls) == 2:
                    raise RuntimeError('injected write failure')
            return original(db, tenant, kind, payload)
        with patch.object(self.service.store, 'put_revision', side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.build(base=base)
        self.assertEqual(self.head(), base)
        self.assertEqual(self.objects(), [])
        self.assertEqual(len(self.build(base=base)['objects']), 2)

    def test_audit_failure_rolls_back_build_and_idempotency_record(self):
        self.setup_binding()
        base = self.head()
        with patch.object(self.service.security, 'audit', side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):
                self.build(base=base)
        self.assertEqual(self.head(), base)
        self.assertEqual(self.objects(), [])
        self.assertEqual(len(self.build(base=base)['objects']), 2)

    def test_branch_isolation_and_merge_is_explicitly_unsupported(self):
        self.setup_binding()
        child = self.call('branches.fork', branch=self.branch, name='scenario')['branch']
        main_head = self.head()
        self.build(branch=child)
        self.assertEqual(self.head(), main_head)
        self.assertEqual(self.objects(), [])
        self.assertEqual(len(self.objects(child)), 2)
        with self.assertRaises(Conflict):
            self.call('branches.merge', branch=self.branch, source=child,
                      expected_head=self.head(), expected_source=self.head(child))

    def test_source_disappearance_rejects_entire_refresh(self):
        self.setup_binding()
        self.build()
        old = self.objects()
        self.tx([{'values': {'id': 'pump-01'}}], operation='DELETE', key='delete')
        base = self.head()
        with self.assertRaisesRegex(Conflict, 'reconciliation'):
            self.build(key='deleted')
        self.assertEqual(self.head(), base)
        self.assertEqual(self.objects(), old)

    def test_stale_base_and_reused_key_rejected(self):
        self.setup_binding()
        base = self.head()
        self.build(base=base)
        with self.assertRaises(Conflict):
            self.build(key='new-stale', base=base)
        with self.assertRaises(Conflict):
            self.build(key='build-1')

    def test_schema_change_requires_migration(self):
        self.setup_binding()
        changed = copy.deepcopy(TARGET)
        changed['properties']['note'] = {'type': 'string'}
        self.define(changed)
        with self.assertRaisesRegex(Conflict, 'Schema changed'):
            self.build()
        self.assertEqual(self.objects(), [])

    def test_restricted_rows_fail_whole_build(self):
        self.setup_binding(rows=[{'values': {'id': 'a', 'value': 1}},
                                 {'values': {'id': 'b', 'value': 2}, 'marking': {'level': 2}}])
        user = self.member('editor')
        with self.assertRaises(Forbidden):
            self.build(token=user['token'])
        self.assertEqual(self.objects(), [])

    def test_markings_propagate_and_current_policy_applies_to_old_branch(self):
        self.setup_binding()
        self.build()
        child = self.call('branches.fork', branch=self.branch, name='historical')['branch']
        viewer = self.member()
        self.assertEqual(len(self.objects(child, viewer['token'])), 2)
        self.call('policy.restrict', branch=self.branch, resource='dataset/readings/property/value',
                  label={'level': 2, 'compartments': ['ENG']})
        self.assertEqual(self.objects(child, viewer['token']), [])
        oid = self.objects()[0]['id']
        with self.assertRaises(Forbidden):
            self.service.call(viewer['token'], 'bindings.provenance', branch=child, object_id=oid)

    def test_source_column_and_row_markings_propagate_at_publication(self):
        source = copy.deepcopy(PUBLIC)
        source['properties']['value']['marking'] = {'level': 2, 'compartments': ['ENG']}
        self.setup_binding(schema=source)
        self.build()
        self.assertEqual(self.objects(token=self.member()['token']), [])
        self.assertEqual(len(self.objects()), 2)

    def test_retry_revalidates_current_access(self):
        self.setup_binding()
        user = self.member('editor')
        base = self.head()
        self.build(base=base, token=user['token'])
        self.call('policy.restrict', branch=self.branch, resource='dataset/readings', label={'level': 2})
        with self.assertRaises(Forbidden):
            self.build(base=base, token=user['token'])

    def test_identity_collision_does_not_adopt_manual_object(self):
        from s2_platform import digest, canonical
        self.setup_binding()
        oid = 'bound-' + digest({'namespace': 'lab', 'key': canonical('pump-01')})
        self.call('objects.put', branch=self.branch, expected_head=self.head(), object_id=oid,
                  object_type='Asset', values={'id': 'manual', 'value': 8, 'status': 'manual'})
        base = self.head()
        with self.assertRaises(Conflict):
            self.build()
        self.assertEqual(self.head(), base)
        self.assertEqual(len(self.objects()), 1)

    def test_other_tenant_membership_and_viewer_denied(self):
        self.setup_binding()
        outsider = self.service.bootstrap('Other', 3, ['ENG'])
        with self.assertRaises(NotFound):
            self.build(token=outsider['token'])
        with self.assertRaises(Forbidden):
            self.build(token=self.member('editor', grant=False)['token'])
        with self.assertRaises(Forbidden):
            self.build(token=self.member()['token'])

    def test_overlapping_owners_and_missing_defaults_rejected(self):
        self.dataset()
        self.define(TARGET)
        common = dict(branch=self.branch, expected_head=self.head(), binding='assets', dataset='readings',
                      object_type='Asset', namespace='lab', mappings={'id': 'id', 'value': 'value'})
        with self.assertRaises(S2Error):
            self.call('bindings.define', **common, action_properties=['status', 'value'])
        with self.assertRaises(S2Error):
            self.call('bindings.define', **common, action_properties=['status'])

    def test_concurrent_builds_have_one_publisher(self):
        from concurrent.futures import ThreadPoolExecutor
        self.setup_binding()
        base = self.head()
        def attempt(key):
            try:
                return self.build(key=key, base=base)['status']
            except Conflict:
                return 'CONFLICT'
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, ['first', 'second']))
        self.assertCountEqual(outcomes, ['COMMITTED', 'CONFLICT'])
        self.assertEqual(len(self.objects()), 2)

    def test_live_source_row_policy_hides_only_derived_row_and_links(self):
        from s2_platform import canonical
        self.setup_binding()
        self.build()
        a, b = self.objects()
        self.call('ontology.link_type', branch=self.branch, expected_head=self.head(), name='Related',
                  source_type='Asset', target_type='Asset')
        self.call('links.put', branch=self.branch, expected_head=self.head(), link_id='relation',
                  link_type='Related', source=a['id'], target=b['id'])
        viewer = self.member()
        self.call('policy.restrict', branch=self.branch,
                  resource='dataset/readings/row/' + canonical(a['values']['id']), label={'level': 2})
        self.assertEqual(len(self.objects(token=viewer['token'])), 1)
        self.assertEqual(self.service.call(viewer['token'], 'links.list', branch=self.branch)['links'], [])

    def test_binding_limit_rejected_without_publication(self):
        self.setup_binding(rows=[{'values': {'id': str(i), 'value': i}} for i in range(1001)])
        base = self.head()
        with self.assertRaisesRegex(S2Error, '1000'):
            self.build()
        self.assertEqual(self.head(), base)
        self.assertEqual(self.objects(), [])


@unittest.skipUnless(HAS_PARQUET, 'Parquet dependencies not installed')
class ParquetBindingTests(BindingTests):
    storage_backend = 'parquet'


@unittest.skipUnless(HAS_ICEBERG, 'Iceberg dependencies not installed')
class IcebergBindingTests(BindingTests):
    storage_backend = 'iceberg'
