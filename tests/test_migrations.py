# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
import copy
import unittest
from unittest.mock import patch
import test_bindings as base
from test_file_storage import HAS_PARQUET, HAS_ICEBERG
from s2.service import Service
from s2_platform import Conflict, Forbidden, NotFound, S2Error


class MigrationTests(unittest.TestCase):
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
        source = copy.deepcopy(base.PUBLIC)
        source['properties']['alternate'] = {'type': 'integer', 'required': True}
        self.setup_binding(schema=source, rows=[{'values': {'id': 'a', 'value': 1, 'alternate': 11}},
                                                {'values': {'id': 'b', 'value': 2, 'alternate': 22}}])
        self.build()
        target = copy.deepcopy(base.TARGET)
        target['properties']['review_note'] = {'type': 'string'}
        self.define(target)

    def params(self, branch=None):
        branch = branch or self.branch
        return dict(branch=branch, expected_head=self.head(branch), binding='assets',
                    mappings={'id': 'id', 'value': 'alternate'},
                    action_properties=['status', 'review_note'],
                    initial_values={'status': 'pending', 'review_note': 'unreviewed'},
                    reason='Adopt revised source reading and add review annotation')

    def preview(self, **overrides):
        params = self.params(); params.update(overrides)
        return params, self.call('bindings.migration_preview', **params)

    def migrate(self, params=None, preview=None, key='migration', token=None):
        if params is None:
            params, preview = self.preview()
        return self.service.call(token or self.token, 'bindings.migrate', **params,
                                 plan_hash=preview['plan_hash'], idempotency_key=key)

    def test_preview_has_no_revision_writes_and_apply_is_one_commit(self):
        self.prepare()
        with self.service.store.connect() as db:
            before = (db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0],
                      db.execute('SELECT COUNT(*) FROM commits').fetchone()[0])
        params, preview = self.preview()
        with self.service.store.connect() as db:
            self.assertEqual(before, (db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0],
                                     db.execute('SELECT COUNT(*) FROM commits').fetchone()[0]))
        result = self.migrate(params, preview)
        self.assertEqual(set(result['objects']), set(preview['objects']))
        self.assertEqual(sorted(o['values']['value'] for o in self.objects()), [11, 22])
        self.assertEqual({o['values']['review_note'] for o in self.objects()}, {'unreviewed'})
        with self.service.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM commits').fetchone()[0], before[1] + 1)
            self.assertEqual(db.execute('SELECT version FROM schema_version').fetchone()[0], 5)
        oid = next(iter(result['objects']))
        provenance = self.call('bindings.provenance', branch=self.branch, object_id=oid)
        self.assertEqual(provenance['binding_migration']['previous_binding_revision'], preview['previous_binding_revision'])
        self.assertEqual(provenance['source_binding']['binding_revision'], result['binding_revision'])

    def test_preserves_approved_action_and_post_migration_refresh(self):
        self.prepare()
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
        self.migrate()
        self.build(key='after-migration')
        new = self.call('objects.get', branch=self.branch, object_id=obj['id'])
        self.assertEqual(new['values']['status'], 'inspected')
        self.assertEqual(new['values']['review_note'], 'unreviewed')
        self.assertEqual(new['id'], obj['id'])

    def test_ownership_and_existing_default_changes_rejected(self):
        self.prepare()
        for overrides in (
            {'mappings': {'id': 'id'}, 'action_properties': ['status', 'value', 'review_note']},
            {'mappings': {'id': 'id', 'value': 'alternate', 'status': 'id'}, 'action_properties': ['review_note']},
            {'initial_values': {'status': 'changed', 'review_note': 'unreviewed'}}):
            with self.assertRaises(S2Error):
                self.preview(**overrides)

    def test_invalid_mapping_and_identity_mapping_rejected(self):
        self.prepare()
        for mappings in ({'id': 'id', 'value': 'id'}, {'id': 'id', 'value': 'absent'},
                         {'id': 'alternate', 'value': 'value'}):
            with self.assertRaises(S2Error):
                self.preview(mappings=mappings)

    def test_stale_plan_and_wrong_digest_rejected(self):
        self.prepare()
        params, preview = self.preview()
        with self.assertRaises(Conflict):
            self.migrate(params, {**preview, 'plan_hash': 'wrong'})
        self.tx([{'values': {'id': 'a', 'value': 3, 'alternate': 33}}], operation='UPDATE', key='change')
        with self.assertRaises(Conflict):
            self.migrate(params, preview)

    def test_policy_change_requires_new_preview_even_for_authorized_admin(self):
        self.prepare()
        params, preview = self.preview()
        self.call('policy.restrict', branch=self.branch, resource='dataset/readings/property/alternate', label={'level': 2})
        with self.assertRaises(Conflict):
            self.migrate(params, preview)
        self.migrate()
        low = self.member()
        self.assertEqual(self.objects(token=low['token']), [])

    def test_restart_replay_and_changed_request(self):
        self.prepare()
        params, preview = self.preview()
        result = self.migrate(params, preview)
        self.service = Service(self.db)
        self.assertEqual(self.migrate(params, preview), result)
        self.assertEqual(self.head(), result['commit'])
        with self.assertRaises(Conflict):
            self.migrate({**params, 'reason': 'Different purpose'}, preview)

    def test_replay_checks_current_authorization(self):
        self.prepare()
        low = self.member('admin')
        params = self.params()
        preview = self.service.call(low['token'], 'bindings.migration_preview', **params)
        self.migrate(params, preview, token=low['token'])
        self.call('policy.restrict', branch=self.branch, resource='dataset/readings', label={'level': 2})
        with self.assertRaises(Forbidden):
            self.migrate(params, preview, token=low['token'])

    def test_branch_isolation_and_admin_only(self):
        self.prepare()
        with self.assertRaises(Forbidden):
            self.service.call(self.member('editor', 3, ['ENG'])['token'], 'bindings.migration_preview', **self.params())
        other = self.service.bootstrap('other', 3, ['ENG'])
        with self.assertRaises(NotFound):
            self.service.call(other['token'], 'bindings.migration_preview', **self.params())
        child = self.call('branches.fork', branch=self.branch, name='migration')['branch']
        head, original = self.head(), self.objects()
        params = self.params(child)
        preview = self.call('bindings.migration_preview', **params)
        self.migrate(params, preview)
        self.assertEqual(self.head(), head)
        self.assertEqual(self.objects(), original)

    def test_audit_failure_rolls_back_binding_objects_and_format(self):
        self.prepare()
        params, preview = self.preview()
        old = self.objects()
        with patch.object(self.service.security, 'audit', side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):
                self.migrate(params, preview)
        self.assertEqual(self.head(), params['expected_head'])
        self.assertEqual(self.objects(), old)
        with self.service.store.connect() as db:
            self.assertEqual(db.execute('SELECT version FROM schema_version').fetchone()[0], 3)
        self.migrate(params, preview)

    def test_second_object_failure_rolls_back_whole_migration(self):
        self.prepare()
        params, preview = self.preview()
        old = self.objects()
        original = self.service.store.put_revision
        calls = []
        def write(db, tenant, kind, payload):
            if kind == 'object':
                calls.append(kind)
                if len(calls) == 2:
                    raise RuntimeError('write failure')
            return original(db, tenant, kind, payload)
        with patch.object(self.service.store, 'put_revision', side_effect=write):
            with self.assertRaises(RuntimeError):
                self.migrate(params, preview)
        self.assertEqual(self.head(), params['expected_head'])
        self.assertEqual(self.objects(), old)
        self.migrate(params, preview)

    def test_missing_source_rows_reject_migration(self):
        self.prepare()
        self.tx([{'values': {'id': 'a'}}], operation='DELETE', key='delete')
        with self.assertRaisesRegex(Conflict, 'reconciliation'):
            self.preview()

    def test_retention_after_migration_does_not_downgrade_format(self):
        self.prepare()
        self.migrate()
        self.tx([{'values': {'id': 'a'}}], operation='DELETE', key='delete')
        preview = self.call('bindings.preview', branch=self.branch, expected_head=self.head(), binding='assets')
        self.call('bindings.materialize', branch=self.branch, expected_head=self.head(), binding='assets',
                  idempotency_key='retain', missing_policy='retain', expected_missing=preview['missing'], reason='Keep history')
        self.service = Service(self.db)
        with self.service.store.connect() as db:
            self.assertEqual(db.execute('SELECT version FROM schema_version').fetchone()[0], 5)

    def test_concurrent_migrations_have_one_publisher(self):
        from concurrent.futures import ThreadPoolExecutor
        self.prepare()
        params, preview = self.preview()
        def attempt(key):
            try:
                return self.migrate(params, preview, key)['status']
            except Conflict:
                return 'CONFLICT'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ['one', 'two']))
        self.assertCountEqual(results, ['COMMITTED', 'CONFLICT'])

    def test_new_source_property_and_optional_action_field(self):
        self.prepare()
        target = copy.deepcopy(base.TARGET)
        target['properties'].update(review_note={'type': 'string'}, copied_value={'type': 'integer'})
        self.define(target)
        params, preview = self.preview(mappings={'id': 'id', 'value': 'alternate', 'copied_value': 'value'})
        self.migrate(params, preview)
        self.assertEqual(sorted(o['values']['copied_value'] for o in self.objects()), [1, 2])

    def test_missing_required_mapped_value_rejects_preview_atomically(self):
        source = copy.deepcopy(base.PUBLIC)
        source['properties']['alternate'] = {'type': 'integer'}
        self.setup_binding(schema=source, rows=[{'values': {'id': 'a', 'value': 1, 'alternate': 10}},
                                                {'values': {'id': 'b', 'value': 2}}])
        self.build()
        old, head = self.objects(), self.head()
        with self.assertRaises(S2Error):
            self.preview(action_properties=['status'], initial_values={'status': 'pending'})
        self.assertEqual(self.head(), head)
        self.assertEqual(self.objects(), old)


@unittest.skipUnless(HAS_PARQUET, 'Parquet dependencies not installed')
class ParquetMigrationTests(MigrationTests):
    storage_backend = 'parquet'


@unittest.skipUnless(HAS_ICEBERG, 'Iceberg dependencies not installed')
class IcebergMigrationTests(MigrationTests):
    storage_backend = 'iceberg'
