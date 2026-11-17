# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
import json
import unittest
from unittest.mock import patch
import test_bindings as base
from test_file_storage import HAS_PARQUET, HAS_ICEBERG
from s2.service import Service
from s2_platform import Conflict, Forbidden, NotFound, S2Error


class IngestionTests(unittest.TestCase):
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

    def params(self, content='id,value\na,12\nb,13\n', format='csv'):
        return dict(branch=self.branch, expected_head=self.head(), dataset='readings', content=content,
                    format=format, source_name='laboratory.csv' if format == 'csv' else 'laboratory.json')

    def preview(self, params): return self.call('ingestion.preview', **params)

    def apply(self, params, plan=None, key='import', token=None):
        if plan is None: plan = self.preview(params)
        return self.service.call(token or self.token, 'ingestion.apply', **params,
                                 plan_hash=plan['plan_hash'], idempotency_key=key)

    def test_csv_preview_atomic_publication_and_receipt(self):
        self.dataset(); params = self.params()
        with self.service.store.connect() as db:
            before = db.execute('SELECT COUNT(*) FROM commits').fetchone()[0]
        plan = self.preview(params)
        self.assertTrue(plan['can_publish']); self.assertEqual(plan['valid_records'], 2)
        self.assertEqual(self.head(), params['expected_head'])
        result = self.apply(params, plan)
        self.assertEqual(self.call('datasets.read', branch=self.branch, dataset='readings')['rows'],
                         [{'id': 'a', 'value': 12}, {'id': 'b', 'value': 13}])
        self.assertEqual(self.call('ingestion.get', branch=self.branch, run=result['run']), result)
        with self.service.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM commits').fetchone()[0], before+1)
        self.assertTrue(self.call('audit.verify')['valid'])

    def test_json_preserves_types_and_rejects_string_integer(self):
        self.dataset()
        params = self.params('[{"id":"a","value":12},{"id":"b","value":"13"}]', 'json')
        plan = self.preview(params)
        self.assertEqual(plan['errors'], [{'record': 2, 'code': 'SCHEMA_VALIDATION'}])
        with self.assertRaises(Conflict): self.apply(params, plan)
        self.assertEqual(self.head(), params['expected_head'])
        self.assertEqual(self.call('datasets.read', branch=self.branch, dataset='readings')['rows'], [])

    def test_duplicate_input_and_existing_keys(self):
        self.dataset(); self.apply(self.params())
        params = self.params('id,value\nc,1\nc,2\na,3\n')
        plan = self.preview(params)
        self.assertEqual(plan['errors'], [{'record': 2, 'code': 'DUPLICATE_KEY'}, {'record': 3, 'code': 'DUPLICATE_KEY'}])
        with self.assertRaises(Conflict): self.apply(params, plan, key='other')

    def test_malformed_files_are_rejected(self):
        self.dataset()
        for content, format in [('id,id\na,b\n','csv'), ('id,value\na,1,2','csv'),
                                ('id,value\n"a,1','csv'), ('{}','json'), ('[]','json'),
                                ('[{"id":"a","id":"b","value":1}]','json'),
                                ('[{"id":"a","value":NaN}]','json'), ('x','xml')]:
            with self.subTest(content=content), self.assertRaises(S2Error): self.preview(self.params(content, format))

    def test_limits_and_unicode(self):
        self.dataset()
        for content in ['é'*524289, '\ud800', 'id,value\n'+'a,1\n'*1001]:
            with self.assertRaises(S2Error): self.preview(self.params(content))
        self.assertFalse(self.preview(self.params('[null]', 'json'))['can_publish'])
        self.apply(self.params('id,value\nपंप,5\n'))
        self.assertEqual(self.call('datasets.read', branch=self.branch, dataset='readings')['rows'][0]['id'], 'पंप')

    def test_csv_declared_scalar_conversions(self):
        schema = {'properties': {**base.PUBLIC['properties'], 'active': {'type':'boolean'},
                                  'ratio': {'type':'number'}, 'note': {'type':'string'}}}
        self.dataset(schema)
        params = self.params('id,value,active,ratio,note\na,-12,true,1.5,"hello, world"\nb,0,false,2,\n')
        self.apply(params)
        rows = self.call('datasets.read', branch=self.branch, dataset='readings')['rows']
        self.assertEqual(rows[0], {'id':'a','value':-12,'active':True,'ratio':1.5,'note':'hello, world'})
        self.assertNotIn('note', rows[1])
        for value in ['01', '1.0', ' true', 'NaN']:
            self.assertFalse(self.preview(self.params('id,value\nc,'+value+'\n'))['can_publish'])

    def test_schema_errors_never_echo_raw_values(self):
        self.dataset()
        for content in ['[{"id":"private-value","value":true}]', '[null]', '[{"id":"a"}]',
                        '[{"id":"a","value":1,"secret":"private-value"}]', '[{"id":"a","value":1e999}]']:
            plan = self.preview(self.params(content, 'json'))
            self.assertFalse(plan['can_publish']); self.assertNotIn('private-value', json.dumps(plan))

    def test_stale_head_and_changed_input(self):
        self.dataset(); params = self.params(); plan = self.preview(params)
        with self.assertRaises(Conflict): self.apply({**params, 'content':'id,value\na,99\n'}, plan)
        self.tx([{'values': {'id':'z','value':1}}], key='other')
        with self.assertRaises(Conflict): self.apply(params, plan)

    def test_policy_change_invalidates_preview(self):
        self.dataset(); params = self.params(); plan = self.preview(params)
        self.call('policy.restrict', branch=self.branch, resource='dataset/readings/property/value', label={'level':2})
        with self.assertRaises(Conflict): self.apply(params, plan)
        self.apply(params)

    def test_row_policy_change_invalidates_preview(self):
        self.dataset(); params = self.params(); plan = self.preview(params)
        self.call('policy.restrict', branch=self.branch, resource='dataset/readings/row/"a"', label={'level':2})
        with self.assertRaises(Conflict): self.apply(params, plan)
        self.apply(params)

    def test_restart_idempotent_replay_and_changed_key_request(self):
        self.dataset(); params = self.params(); plan = self.preview(params)
        result = self.apply(params, plan)
        self.service = Service(self.db)
        self.assertEqual(self.apply(params, plan), result)
        self.assertEqual(self.head(), result['commit'])
        with self.assertRaises(Conflict): self.apply({**params, 'source_name':'other.csv'}, plan)

    def test_replay_and_receipt_recheck_current_policy(self):
        self.dataset(); low = self.member('editor'); params = self.params()
        plan = self.service.call(low['token'], 'ingestion.preview', **params)
        result = self.apply(params, plan, token=low['token'])
        self.call('policy.restrict', branch=self.branch, resource='dataset/readings', label={'level':2})
        with self.assertRaises(Forbidden): self.apply(params, plan, token=low['token'])
        with self.assertRaises(Forbidden):
            self.service.call(low['token'], 'ingestion.get', branch=self.branch, run=result['run'])

    def test_hidden_existing_rows_fail_before_duplicate_report(self):
        self.dataset(); self.tx([{'values': {'id':'a','value':9}, 'marking': {'level':2}}])
        with self.assertRaises(Forbidden):
            self.service.call(self.member('editor')['token'], 'ingestion.preview', **self.params())

    def test_roles_tenants_membership_and_receipt_ownership(self):
        self.dataset(); params = self.params()
        for user, error in [(self.member(), Forbidden), (self.member('editor',grant=False), Forbidden),
                             (self.service.bootstrap('other',3,['ENG']), NotFound)]:
            with self.assertRaises(error): self.service.call(user['token'], 'ingestion.preview', **params)
        result = self.apply(params)
        with self.assertRaises(NotFound):
            self.service.call(self.member('admin',3,['ENG'])['token'], 'ingestion.get', branch=self.branch, run=result['run'])

    def test_audit_failure_rolls_back_dataset_transaction_and_receipt(self):
        self.dataset(); params = self.params(); plan = self.preview(params)
        with patch.object(self.service.security, 'audit', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError): self.apply(params, plan)
        self.assertEqual(self.head(), params['expected_head'])
        with self.service.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ingestion_runs').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM dataset_transactions').fetchone()[0],0)
        self.apply(params, plan)

    def test_storage_failure_does_not_publish(self):
        self.dataset(); params = self.params(); plan = self.preview(params)
        with patch.object(self.service.store, 'put_revision', side_effect=RuntimeError('storage failed')):
            with self.assertRaises(RuntimeError): self.apply(params, plan)
        self.assertEqual(self.head(), params['expected_head'])
        self.apply(params, plan)

    def test_concurrent_publication_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        self.dataset(); params = self.params(); plan = self.preview(params)
        def attempt(key):
            try: return self.apply(params, plan, key)['status']
            except Conflict: return 'CONFLICT'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(attempt, ['one','two'])), ['COMMITTED','CONFLICT'])

    def test_branch_isolation_and_binding_materialization(self):
        self.setup_binding(rows=[])
        child = self.call('branches.fork', branch=self.branch, name='ingest')['branch']
        params = {**self.params(), 'branch':child, 'expected_head':self.head(child)}
        self.apply(params)
        self.build(branch=child)
        self.assertEqual(len(self.objects(child)),2)
        self.assertEqual(self.objects(),[])
        self.assertEqual(self.call('datasets.read', branch=self.branch, dataset='readings')['rows'],[])

    def test_json_success(self):
        self.dataset()
        result = self.apply(self.params('[{"id":"a","value":12}]', 'json'))
        self.assertEqual(result['records'], 1)
        self.assertEqual(self.call('datasets.read', branch=self.branch, dataset='readings')['rows'], [{'id':'a','value':12}])

    def test_cli_preview_apply_and_missing_token(self):
        import contextlib
        import io
        import os
        from pathlib import Path
        from s2.cli import main
        self.dataset()
        file = Path(self.temp.name)/'source.csv'
        file.write_bytes(b'id,value\na,1\n')
        params = self.params(file.read_text())
        argv = ['s2', '--db', str(self.db), 'ingest-preview', '--file', str(file), '--format', 'csv',
                '--branch', self.branch, '--expected-head', self.head(), '--dataset', 'readings']
        with patch.dict(os.environ, {'S2_TOKEN':self.token}), patch('sys.argv', argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out): main()
        plan = json.loads(out.getvalue())
        self.assertEqual(self.head(), params['expected_head'])
        argv[3] = 'ingest-apply'
        argv += ['--plan-hash', plan['plan_hash'], '--key', 'cli']
        with patch.dict(os.environ, {'S2_TOKEN':self.token}), patch('sys.argv', argv):
            with contextlib.redirect_stdout(io.StringIO()): main()
        self.assertEqual(self.call('datasets.read', branch=self.branch, dataset='readings')['rows'], [{'id':'a','value':1}])
        with patch.dict(os.environ, {}, clear=True), patch('sys.argv', argv), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit): main()

    def test_explicit_marking_propagates_to_rows(self):
        self.dataset(); params = {**self.params(), 'label': {'level':2,'compartments':['ENG']}}
        self.apply(params)
        low = self.member()
        self.assertEqual(self.service.call(low['token'],'datasets.read',branch=self.branch,dataset='readings')['rows'],[])


@unittest.skipUnless(HAS_PARQUET, 'Parquet dependencies not installed')
class ParquetIngestionTests(IngestionTests):
    storage_backend = 'parquet'

@unittest.skipUnless(HAS_ICEBERG, 'Iceberg dependencies not installed')
class IcebergIngestionTests(IngestionTests):
    storage_backend = 'iceberg'
