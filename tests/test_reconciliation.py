# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
import unittest
from unittest.mock import patch
import test_bindings as base
from test_file_storage import HAS_PARQUET, HAS_ICEBERG
from s2.service import Service
from s2_platform import Conflict, Forbidden, S2Error


class ReconciliationTests(unittest.TestCase):
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

    def missing(self):
        self.setup_binding()
        self.build()
        old = self.objects()
        self.tx([{'values': {'id': 'pump-01'}}], operation='DELETE', key='delete')
        return old

    def preview(self, branch=None):
        branch = branch or self.branch
        return self.call('bindings.preview', branch=branch, expected_head=self.head(branch), binding='assets')

    def retain(self, preview=None, token=None, **overrides):
        preview = preview or self.preview()
        params = dict(branch=self.branch, expected_head=preview['commit'], binding='assets',
                      idempotency_key='retention', missing_policy='retain',
                      expected_missing=preview['missing'], reason='Retain engineering history pending source review')
        params.update(overrides)
        return self.service.call(token or self.token, 'bindings.materialize', **params)

    def test_retains_values_and_last_observation_with_absence_evidence(self):
        old = self.missing()
        preview = self.preview()
        self.assertEqual(len(preview['missing']), 1)
        self.retain(preview)
        oid = next(iter(preview['missing']))
        obj = self.call('objects.get', branch=self.branch, object_id=oid)
        self.assertEqual(obj['source_presence'], 'MISSING')
        self.assertEqual(obj['values'], next(o['values'] for o in old if o['id'] == oid))
        p = self.call('bindings.provenance', branch=self.branch, object_id=oid)
        self.assertNotEqual(p['source_binding']['dataset_revision'], preview['dataset_revision'])
        self.assertEqual(p['source_state']['last_reconciliation']['absence_dataset_revision'], preview['dataset_revision'])
        self.assertTrue(self.call('audit.verify')['valid'])

    def test_returning_row_restores_same_identity_and_operational_edit(self):
        self.missing()
        preview = self.preview()
        oid = next(iter(preview['missing']))
        self.retain(preview)
        obj = self.call('objects.get', branch=self.branch, object_id=oid)
        self.call('actions.define', branch=self.branch, expected_head=self.head(), name='Review',
                  object_type='Asset', writable_properties=['status'])
        action = self.call('actions.submit', branch=self.branch, expected_head=self.head(), action='Review',
            edits=[{'object': oid, 'expected_revision': obj['revision'], 'set': {'status': 'reviewed'}}],
            idempotency_key='review')
        self.call('actions.execute', branch=self.branch, invocation=action['invocation'])
        self.assertEqual(self.call('objects.get', branch=self.branch, object_id=oid)['source_presence'], 'MISSING')
        self.tx([{'values': {'id': 'pump-01', 'value': 88}}], key='return')
        self.build(key='restore')
        obj = self.call('objects.get', branch=self.branch, object_id=oid)
        self.assertEqual(obj['source_presence'], 'PRESENT')
        self.assertEqual(obj['values']['status'], 'reviewed')
        self.assertEqual(obj['values']['value'], 88)
        p = self.call('bindings.provenance', branch=self.branch, object_id=oid)
        self.assertEqual(p['source_state']['last_reconciliation']['absence_dataset_revision'], preview['dataset_revision'])

    def test_exact_set_and_revision_required(self):
        self.missing()
        preview = self.preview()
        oid = next(iter(preview['missing']))
        for expected in ({oid: 'wrong'}, {**preview['missing'], 'extra': 'revision'}):
            with self.assertRaises(Conflict):
                self.retain(preview, expected_missing=expected)
        self.assertEqual(self.head(), preview['commit'])

    def test_requires_admin_reason_and_explicit_policy(self):
        self.missing()
        preview = self.preview()
        with self.assertRaises(Forbidden):
            self.retain(preview, token=self.member('editor', 3, ['ENG'])['token'])
        for overrides in ({'reason': ' '}, {'expected_missing': {}}, {'missing_policy': 'delete'},
                          {'missing_policy': 'reject'}):
            with self.assertRaises(S2Error):
                self.retain(preview, **overrides)
        with self.assertRaises(Conflict):
            self.build(key='default-still-rejects')

    def test_stale_preview_rejected_after_operational_edit(self):
        self.missing()
        preview = self.preview()
        oid = next(iter(preview['missing']))
        obj = self.call('objects.get', branch=self.branch, object_id=oid)
        self.call('objects.put', branch=self.branch, expected_head=self.head(), object_id=oid,
                  object_type='Asset', values={**obj['values'], 'status': 'reviewed'}, expected_revision=obj['revision'])
        with self.assertRaises(Conflict):
            self.retain(preview)

    def test_restart_retry_and_changed_reason_conflict(self):
        self.missing()
        preview = self.preview()
        result = self.retain(preview)
        self.service = Service(self.db)
        self.assertEqual(self.retain(preview), result)
        self.assertEqual(self.head(), result['commit'])
        with self.assertRaises(Conflict):
            self.retain(preview, reason='Different decision')
        with self.service.store.transaction() as db:
            self.assertEqual(db.execute('SELECT version FROM schema_version').fetchone()[0], 4)

    def test_preview_and_retention_enforce_missing_object_policy(self):
        self.missing()
        preview = self.preview()
        oid = next(iter(preview['missing']))
        low = self.member('admin')
        self.call('policy.restrict', branch=self.branch, resource='object/' + oid, label={'level': 2})
        with self.assertRaises(Forbidden):
            self.service.call(low['token'], 'bindings.preview', branch=self.branch,
                              expected_head=self.head(), binding='assets')
        with self.assertRaises(Forbidden):
            self.retain(preview, token=low['token'])

    def test_live_policy_still_protects_retained_object(self):
        self.missing()
        preview = self.preview()
        self.retain(preview)
        oid = next(iter(preview['missing']))
        child = self.call('branches.fork', branch=self.branch, name='old')['branch']
        low = self.member()
        self.call('policy.restrict', branch=self.branch, resource='dataset/readings', label={'level': 2})
        self.assertEqual(self.objects(branch=child, token=low['token']), [])
        with self.assertRaises(Forbidden):
            self.service.call(low['token'], 'bindings.provenance', branch=child, object_id=oid)

    def test_audit_failure_rolls_back_retention_and_format_upgrade(self):
        self.missing()
        preview = self.preview()
        old = self.objects()
        with patch.object(self.service.security, 'audit', side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):
                self.retain(preview)
        self.assertEqual(self.head(), preview['commit'])
        self.assertEqual(self.objects(), old)
        with self.service.store.transaction() as db:
            self.assertEqual(db.execute('SELECT version FROM schema_version').fetchone()[0], 3)
        self.retain(preview)

    def test_branch_reconciliation_does_not_change_main(self):
        self.missing()
        child = self.call('branches.fork', branch=self.branch, name='scenario')['branch']
        preview = self.preview(child)
        main = self.head()
        self.retain(preview, branch=child)
        self.assertEqual(self.head(), main)
        self.assertFalse(any('source_presence' in obj for obj in self.objects()))
        self.assertTrue(any(obj.get('source_presence') == 'MISSING' for obj in self.objects(child)))

    def test_new_binding_does_not_downgrade_format(self):
        self.missing()
        self.retain()
        self.call('bindings.define', branch=self.branch, expected_head=self.head(), binding='another',
                  dataset='readings', object_type='Asset', namespace='another',
                  mappings={'id': 'id', 'value': 'value'}, action_properties=['status'],
                  initial_values={'status': 'pending'})
        self.service = Service(self.db)
        with self.service.store.transaction() as db:
            self.assertEqual(db.execute('SELECT version FROM schema_version').fetchone()[0], 4)


@unittest.skipUnless(HAS_PARQUET, 'Parquet dependencies not installed')
class ParquetReconciliationTests(ReconciliationTests):
    storage_backend = 'parquet'


@unittest.skipUnless(HAS_ICEBERG, 'Iceberg dependencies not installed')
class IcebergReconciliationTests(ReconciliationTests):
    storage_backend = 'iceberg'
