# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import test_foundation as baseline
from s2.service import Service
from s2_platform import Conflict, Forbidden, S2Error, new_id
from s2_catalog_storage.file_storage import StorageError

HAS_PARQUET = importlib.util.find_spec('pyarrow') is not None
HAS_ICEBERG = HAS_PARQUET and all(importlib.util.find_spec(m) is not None for m in ('pyiceberg', 'sqlalchemy'))
if os.environ.get('S2_REQUIRE_STORAGE_TESTS') == '1' and not HAS_ICEBERG:
    raise RuntimeError('Full storage test run requires the storage extra; tests must not be skipped')


class StorageCases:
    def raw_snapshot(self, snapshot=None):
        with self.service.store.connect() as db:
            if snapshot is None:
                head = self.service.store.branch(db, self.admin['tenant_id'], self.branch)['head']
                manifest = self.service.store.manifest(db, self.admin['tenant_id'], head)
                snapshot = manifest['dataset/readings']
            row = db.execute('SELECT payload FROM revisions WHERE tenant=? AND id=?',
                             (self.admin['tenant_id'], snapshot)).fetchone()
            return json.loads(row[0])

    def test_real_format_without_inline_rows(self):
        self.dataset()
        result = self.tx([{'values': {'id': 'a', 'value': 5}}])
        payload = self.raw_snapshot(result['snapshot'])
        self.assertNotIn('rows', payload)
        descriptor = payload['storage']
        self.assertEqual(descriptor['format'], self.storage_backend)
        store = self.service.store.file_storage
        seal = store.verify(self.admin['tenant_id'], descriptor)
        self.assertEqual(seal['row_count'], 1)
        entry = store.root / descriptor['artifact'] / seal['entry']
        if self.storage_backend == 'parquet':
            import pyarrow.parquet as pq
            self.assertEqual(entry.read_bytes()[:4], b'PAR1')
            self.assertEqual(pq.ParquetFile(entry).metadata.num_rows, 1)
        else:
            from pyiceberg.table import StaticTable
            iceberg = StaticTable.from_metadata(entry.as_uri())
            self.assertEqual(iceberg.metadata.format_version, 2)
            self.assertEqual(iceberg.current_snapshot().snapshot_id, seal['snapshot_id'])
            self.assertEqual(iceberg.scan().to_arrow().num_rows, 1)
        self.assertEqual(seal['columns'], {'id': 'c0', 'value': 'c1'})

    def test_dataset_fork_keeps_old_files_and_labels(self):
        self.dataset(baseline.SCHEMA)
        original = self.tx([{'values': {'id': 'a', 'value': 1, 'secret': 'private'}}])
        fork = self.call('branches.fork', branch=self.branch, name='scenario/files')['branch']
        changed = self.tx([{'values': {'id': 'a', 'value': 2}}], operation='UPDATE', key='update')
        self.assertNotEqual(original['snapshot'], changed['snapshot'])
        self.assertEqual(self.call('datasets.read', branch=fork, dataset='readings')['rows'][0]['value'], 1)
        self.assertEqual(self.call('datasets.read', branch=self.branch, dataset='readings')['rows'][0]['value'], 2)
        self.call('policy.restrict', branch=self.branch, resource='dataset/readings/property/value', label={'level': 3})
        viewer = self.member()
        self.assertEqual(self.service.call(viewer['token'], 'datasets.read', branch=fork, dataset='readings')['rows'], [{'id': 'a'}])
        self.assertEqual(self.service.store.storage_report()['broken_references'], [])

    def test_dataset_branch_merge_publishes_pinned_file_reference(self):
        self.dataset()
        fork = self.call('branches.fork', branch=self.branch, name='feature/files')['branch']
        original_branch = self.branch
        self.branch = fork
        self.tx([{'values': {'id': 'from-fork', 'value': 9}}])
        self.branch = original_branch
        self.define()
        self.call('branches.merge', branch=self.branch, source=fork, expected_head=self.head(), expected_source=self.head(fork))
        self.assertEqual(self.call('datasets.read', branch=self.branch, dataset='readings')['rows'], [{'id': 'from-fork', 'value': 9}])

    def test_canonical_round_trip_optional_unicode_and_numeric_representation(self):
        schema = {'properties': {'id': {'type': 'string', 'required': True},
                                 'n': {'type': 'number'}, 'flag': {'type': 'boolean'},
                                 'long': {'type': 'integer'}, 'nested.name': {'type': 'string'}}}
        self.dataset(schema)
        rows = [{'values': {'id': 'नमस्ते', 'n': 1, 'flag': False, 'long': 2**63-1, 'nested.name': 'α'}},
                {'values': {'id': 'b', 'n': 1.0}}, {'values': {'id': 'c'}}]
        result = self.tx(rows)
        with self.service.store.connect() as db:
            restored = self.service.store.revision(db, self.admin['tenant_id'], result['snapshot'])['rows']
        by_key = {r['values']['id']: r['values'] for r in restored}
        self.assertIs(type(by_key['नमस्ते']['n']), int)
        self.assertIs(type(by_key['b']['n']), float)
        self.assertNotIn('n', by_key['c'])
        self.assertEqual(by_key['नमस्ते']['long'], 2**63-1)

    def test_file_write_error_preserves_prepared_transaction_and_head(self):
        self.dataset()
        tx = self.tx([{'values': {'id': 'a', 'value': 1}}], commit=False)
        head = self.head()
        def fail(phase):
            if phase == 'data_written':
                raise OSError('Injected disk failure')
        with patch.object(self.service.store.file_storage, '_checkpoint', fail):
            with self.assertRaises(StorageError):
                self.call('datasets.commit', branch=self.branch, transaction=tx)
        self.assertEqual(self.head(), head)
        self.assertEqual(self.call('datasets.read', branch=self.branch, dataset='readings')['rows'], [])
        report = self.service.store.storage_report()
        self.assertEqual(len(report['unreferenced_artifacts']), 1)
        self.assertFalse(report['unreferenced_artifacts'][0]['sealed'])
        self.assertEqual(report['deleted'], 0)
        self.assertEqual(self.call('datasets.commit', branch=self.branch, transaction=tx)['status'], 'COMMITTED')

    def test_audit_failure_leaves_only_unpublished_sealed_artifact(self):
        self.dataset()
        tx = self.tx([{'values': {'id': 'a', 'value': 1}}], commit=False)
        head = self.head()
        with patch.object(self.service.security, 'audit', side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):
                self.call('datasets.commit', branch=self.branch, transaction=tx)
        self.assertEqual(self.head(), head)
        report = self.service.store.storage_report()
        self.assertEqual(len(report['unreferenced_artifacts']), 1)
        self.assertTrue(report['unreferenced_artifacts'][0]['sealed'])
        self.assertEqual(report['broken_references'], [])

    def test_process_crash_after_seal_before_publication_and_retry(self):
        self.dataset()
        tx = self.tx([{'values': {'id': 'a', 'value': 1}}], commit=False)
        before = self.head()
        paths = [str(baseline.ROOT/'src'), str(baseline.ROOT/'platform')]
        paths += [str(p) for p in (baseline.ROOT/'subsystems').iterdir() if p.is_dir()]
        code = '''import json, os, sys
request = json.load(sys.stdin)
sys.path[:0] = request['paths']
from s2.service import Service
service = Service(request['db'])
def checkpoint(phase):
    if phase == 'sealed':
        os._exit(73)
service.store.file_storage._checkpoint = checkpoint
service.call(request['token'], 'datasets.commit', branch=request['branch'], transaction=request['tx'])
'''
        request = {'paths': paths, 'db': str(self.db), 'token': self.token, 'branch': self.branch, 'tx': tx}
        process = subprocess.run([sys.executable, '-c', code], input=json.dumps(request), text=True,
                                 capture_output=True, timeout=60)
        self.assertEqual(process.returncode, 73, process.stderr)
        restarted = Service(self.db)
        self.assertEqual(restarted.call(self.token, 'branches.head', branch=self.branch)['commit'], before)
        report = restarted.store.storage_report()
        self.assertEqual(len(report['unreferenced_artifacts']), 1)
        self.assertTrue(report['unreferenced_artifacts'][0]['sealed'])
        self.assertEqual(report['broken_references'], [])
        result = restarted.call(self.token, 'datasets.commit', branch=self.branch, transaction=tx)
        self.assertEqual(result['status'], 'COMMITTED')
        count = len(list(restarted.store.file_storage.root.rglob('SEALED.json')))
        self.assertEqual(result, restarted.call(self.token, 'datasets.commit', branch=self.branch, transaction=tx))
        self.assertEqual(count, len(list(restarted.store.file_storage.root.rglob('SEALED.json'))))

    def test_process_crash_after_commit_before_response_replays_result(self):
        self.dataset()
        tx = self.tx([{'values': {'id': 'a', 'value': 1}}], commit=False)
        before = self.head()
        paths = [str(baseline.ROOT/'src'), str(baseline.ROOT/'platform')]
        paths += [str(p) for p in (baseline.ROOT/'subsystems').iterdir() if p.is_dir()]
        code = """import json, os, sys
from contextlib import contextmanager
request = json.load(sys.stdin)
sys.path[:0] = request['paths']
from s2.service import Service
service = Service(request['db'])
original = service.store.transaction
@contextmanager
def crash_after_commit():
    with original() as db:
        yield db
    os._exit(74)
service.store.transaction = crash_after_commit
service.call(request['token'], 'datasets.commit', branch=request['branch'], transaction=request['tx'])
"""
        process = subprocess.run([sys.executable, '-c', code],
            input=json.dumps({'paths': paths, 'db': str(self.db), 'token': self.token, 'branch': self.branch, 'tx': tx}),
            text=True, capture_output=True, timeout=60)
        self.assertEqual(process.returncode, 74, process.stderr)
        reopened = Service(self.db)
        after = reopened.call(self.token, 'branches.head', branch=self.branch)['commit']
        self.assertNotEqual(after, before)
        report = reopened.store.storage_report()
        self.assertEqual(report['unreferenced_artifacts'], [])
        self.assertEqual(report['broken_references'], [])
        retry = reopened.call(self.token, 'datasets.commit', branch=self.branch, transaction=tx)
        self.assertEqual(retry['commit'], after)
        self.assertEqual(report, reopened.store.storage_report())

    def test_missing_and_corrupt_files_fail_closed(self):
        self.dataset()
        self.tx([{'values': {'id': 'a', 'value': 1}}])
        descriptor = self.raw_snapshot()['storage']
        storage = self.service.store.file_storage
        seal = storage.verify(self.admin['tenant_id'], descriptor)
        path = storage.root / descriptor['artifact'] / seal['files'][0]['path']
        original = path.read_bytes()
        path.write_bytes(original + b'corrupt')
        with self.assertRaises(StorageError):
            self.call('datasets.read', branch=self.branch, dataset='readings')
        self.assertEqual(len(self.service.store.storage_report()['broken_references']), 1)
        path.unlink()
        with self.assertRaises(StorageError):
            self.call('datasets.read', branch=self.branch, dataset='readings')
        path.write_bytes(original)
        self.assertEqual(self.service.store.storage_report()['broken_references'], [])

    def test_wrong_tenant_and_unsafe_storage_references(self):
        self.dataset()
        descriptor = self.raw_snapshot()['storage']
        with self.assertRaises(StorageError):
            self.service.store.file_storage.read(new_id(), descriptor)
        forged = {**descriptor, 'artifact': self.admin['tenant_id'] + '/../outside'}
        with self.assertRaises(StorageError):
            self.service.store.file_storage.read(self.admin['tenant_id'], forged)
        directory = self.service.store.file_storage.root / descriptor['artifact']
        seal = directory / 'SEALED.json'
        original = seal.read_bytes()
        seal.unlink()
        other = Path(self.temp.name) / 'external-seal'
        other.write_bytes(original)
        seal.symlink_to(other)
        with self.assertRaises(StorageError):
            self.service.store.file_storage.read(self.admin['tenant_id'], descriptor)

    def test_cannot_silently_switch_backend_or_warehouse(self):
        self.dataset()
        with self.assertRaises(Conflict):
            Service(self.db, storage_backend='json')
        with self.assertRaises(Conflict):
            Service(self.db, warehouse=Path(self.temp.name)/'wrong')
        self.assertEqual(Service(self.db).store.storage_backend, self.storage_backend)

    def test_old_inline_snapshot_survives_opt_in_upgrade(self):
        old_db = Path(self.temp.name) / 'old.db'
        old = Service(old_db)
        admin = old.bootstrap('legacy')
        def call(op, **args):
            return old.call(admin['token'], op, **args)
        p = call('projects.create', name='legacy')
        call('datasets.create', branch=p['branch'], expected_head=p['commit'], dataset='old', schema=baseline.PUBLIC, primary_key='id')
        old_tx = call('datasets.begin', branch=p['branch'], expected_head=call('branches.head', branch=p['branch'])['commit'],
                      dataset='old', operation='APPEND', idempotency_key='old-data')['transaction']
        call('datasets.stage', branch=p['branch'], transaction=old_tx, rows=[{'values': {'id': 'legacy', 'value': 7}}])
        call('datasets.commit', branch=p['branch'], transaction=old_tx)
        old_fork = call('branches.fork', branch=p['branch'], name='scenario/legacy')['branch']
        head = call('branches.head', branch=p['branch'])['commit']
        upgraded = Service(old_db, storage_backend=self.storage_backend)
        with self.assertRaises(Conflict):
            old.call(admin['token'], 'branches.head', branch=p['branch'])
        def upgraded_call(op, **args):
            return upgraded.call(admin['token'], op, **args)
        self.assertEqual(upgraded_call('datasets.read', branch=p['branch'], dataset='old')['rows'], [{'id': 'legacy', 'value': 7}])
        tx = upgraded_call('datasets.begin', branch=p['branch'], expected_head=head,
                           dataset='old', operation='APPEND', idempotency_key='migration')['transaction']
        upgraded_call('datasets.stage', branch=p['branch'], transaction=tx, rows=[{'values': {'id': 'a', 'value': 5}}])
        result = upgraded_call('datasets.commit', branch=p['branch'], transaction=tx)
        self.assertEqual(upgraded_call('datasets.read', branch=old_fork, dataset='old')['rows'], [{'id': 'legacy', 'value': 7}])
        with upgraded.store.connect() as db:
            self.assertEqual(db.execute('SELECT version FROM schema_version').fetchone()[0], 2)
            payload = json.loads(db.execute('SELECT payload FROM revisions WHERE id=?', (result['snapshot'],)).fetchone()[0])
            self.assertIn('storage', payload)
            self.assertNotIn('rows', payload)

    def test_invalid_int64_value_does_not_publish(self):
        self.dataset()
        before = self.head()
        with self.assertRaises(S2Error):
            self.tx([{'values': {'id': 'a', 'value': 2**64}}])
        self.assertEqual(self.head(), before)


@unittest.skipUnless(HAS_PARQUET, 'Install storage dependencies for Parquet tests')
class ParquetFoundation(StorageCases, baseline.FoundationTest):
    storage_backend = 'parquet'


@unittest.skipUnless(HAS_ICEBERG, 'Install storage dependencies for Iceberg tests')
class IcebergFoundation(StorageCases, baseline.FoundationTest):
    storage_backend = 'iceberg'
