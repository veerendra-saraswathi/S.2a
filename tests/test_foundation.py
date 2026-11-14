# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
import concurrent.futures
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'platform')]
sys.path[:0] = [str(p) for p in (ROOT / 'subsystems').iterdir() if p.is_dir()]
from s2.service import Service
from s2_platform import Conflict, Forbidden, NotFound, S2Error

SCHEMA = {'properties': {'id': {'type': 'string', 'required': True},
                          'value': {'type': 'integer', 'required': True},
                          'secret': {'type': 'string', 'marking': {'level': 2, 'compartments': ['ENG']}}}}
PUBLIC = {'properties': {'id': {'type': 'string', 'required': True},
                          'value': {'type': 'integer', 'required': True}}}


class FoundationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 's2.db'
        self.service = Service(self.db)
        self.admin = self.service.bootstrap('A', 3, ['ENG'])
        self.token = self.admin['token']
        self.project = self.call('projects.create', name='Engineering')
        self.branch = self.project['branch']

    def tearDown(self):
        self.temp.cleanup()

    def call(self, op, **params):
        return self.service.call(self.token, op, **params)

    def head(self, branch=None):
        return self.call('branches.head', branch=branch or self.branch)['commit']

    def member(self, role='viewer', clearance=0, compartments=None, grant=True):
        p = self.call('principals.create', role=role, clearance=clearance, compartments=compartments or [])
        if grant:
            self.call('projects.grant', project=self.project['project'], principal=p['principal_id'])
        return p

    def dataset(self, schema=None):
        return self.call('datasets.create', branch=self.branch, expected_head=self.head(),
                         dataset='readings', schema=schema or PUBLIC, primary_key='id')

    def tx(self, rows, operation='APPEND', key='key', commit=True):
        tx = self.call('datasets.begin', branch=self.branch, expected_head=self.head(), dataset='readings',
                       operation=operation, idempotency_key=key)['transaction']
        self.call('datasets.stage', branch=self.branch, transaction=tx, rows=rows)
        return self.call('datasets.commit', branch=self.branch, transaction=tx) if commit else tx

    def define(self, schema=None, name='Asset', branch=None):
        branch = branch or self.branch
        return self.call('ontology.define', branch=branch, expected_head=self.head(branch),
                         name=name, definition=schema or PUBLIC)

    def obj(self, oid='one', value=1, branch=None, label=None, **extra):
        branch = branch or self.branch
        return self.call('objects.put', branch=branch, expected_head=self.head(branch), object_id=oid,
                         object_type='Asset', values={'id': oid, 'value': value, **extra}, label=label)

    def action(self, edits, approvals=0):
        self.call('actions.define', branch=self.branch, expected_head=self.head(), name='SetValue',
                  object_type='Asset', writable_properties=['value'], approvals=approvals)
        return self.call('actions.submit', branch=self.branch, expected_head=self.head(), action='SetValue',
                         edits=edits, idempotency_key='invocation')

    def test_cross_tenant_denied_even_with_guessed_branch(self):
        other = self.service.bootstrap('B', 3, ['ENG'])
        with self.assertRaises(NotFound):
            self.service.call(other['token'], 'branches.head', branch=self.branch)
        with self.assertRaises(Forbidden):
            self.service.call('invalid', 'branches.head', branch=self.branch)

    def test_project_membership_and_role(self):
        member = self.member(grant=False)
        with self.assertRaises(Forbidden):
            self.service.call(member['token'], 'branches.head', branch=self.branch)
        self.call('projects.grant', project=self.project['project'], principal=member['principal_id'])
        self.service.call(member['token'], 'branches.head', branch=self.branch)
        with self.assertRaises(Forbidden):
            self.service.call(member['token'], 'branches.fork', branch=self.branch, name='feature/no')

    def test_revoked_token_denied_on_next_request(self):
        user = self.member()
        self.call('principals.revoke', principal=user['principal_id'])
        with self.assertRaises(Forbidden):
            self.service.call(user['token'], 'branches.head', branch=self.branch)

    def test_admin_cannot_issue_higher_clearance(self):
        with self.assertRaises(Forbidden):
            self.call('principals.create', role='admin', clearance=4)

    def test_dataset_append_update_delete_and_parent(self):
        initial = self.dataset()
        appended = self.tx([{'values': {'id': 'a', 'value': 1}}])
        self.tx([{'values': {'id': 'a', 'value': 2}}], 'UPDATE', 'update')
        rows = self.call('datasets.read', branch=self.branch, dataset='readings')['rows']
        self.assertEqual(rows, [{'id': 'a', 'value': 2}])
        self.tx([{'values': {'id': 'a'}}], 'DELETE', 'delete')
        self.assertEqual(self.call('datasets.read', branch=self.branch, dataset='readings')['rows'], [])
        with self.service.store.connect() as db:
            old = self.service.store.revision(db, self.admin['tenant_id'], appended['snapshot'])
            self.assertEqual(old['parent'], initial['snapshot'])
            self.assertEqual(old['rows'][0]['values']['value'], 1)

    def test_duplicate_keys_and_invalid_types_rejected_without_publication(self):
        self.dataset()
        before = self.head()
        for value in ['wrong', True]:
            with self.assertRaises(S2Error):
                self.tx([{'values': {'id': 'a', 'value': value}}], key=str(value))
        with self.assertRaises(Conflict):
            self.tx([{'values': {'id': 'a', 'value': 1}}, {'values': {'id': 'a', 'value': 2}}], key='duplicate')
        self.assertEqual(self.head(), before)

    def test_transaction_requires_prepare_and_supports_abort(self):
        self.dataset()
        tx = self.call('datasets.begin', branch=self.branch, expected_head=self.head(), dataset='readings',
                       operation='APPEND', idempotency_key='open')['transaction']
        with self.assertRaises(Conflict):
            self.call('datasets.commit', branch=self.branch, transaction=tx)
        self.call('datasets.abort', branch=self.branch, transaction=tx)
        with self.assertRaises(Conflict):
            self.call('datasets.stage', branch=self.branch, transaction=tx, rows=[])

    def test_stale_transaction_cannot_overwrite_new_head(self):
        self.dataset()
        tx = self.tx([{'values': {'id': 'a', 'value': 1}}], commit=False)
        self.define()
        current = self.head()
        with self.assertRaises(Conflict):
            self.call('datasets.commit', branch=self.branch, transaction=tx)
        self.assertEqual(self.head(), current)
        self.assertEqual(self.call('datasets.read', branch=self.branch, dataset='readings')['rows'], [])

    def test_dataset_commit_retry_is_idempotent(self):
        self.dataset()
        tx = self.tx([{'values': {'id': 'a', 'value': 1}}], commit=False)
        first = self.call('datasets.commit', branch=self.branch, transaction=tx)
        self.assertEqual(first, self.call('datasets.commit', branch=self.branch, transaction=tx))
        self.assertEqual(len(self.call('datasets.read', branch=self.branch, dataset='readings')['rows']), 1)

    def test_dataset_row_and_column_markings(self):
        self.dataset(SCHEMA)
        self.tx([{'values': {'id': 'a', 'value': 1, 'secret': 'restricted'}},
                 {'values': {'id': 'b', 'value': 2}, 'marking': {'level': 2, 'compartments': ['ENG']}}])
        user = self.member()
        rows = self.service.call(user['token'], 'datasets.read', branch=self.branch, dataset='readings')['rows']
        self.assertEqual(rows, [{'id': 'a', 'value': 1}])

    def test_interface_contract_and_schema_compatibility(self):
        self.call('ontology.interface', branch=self.branch, expected_head=self.head(), name='Identified',
                  definition={'properties': {'id': {'type': 'string', 'required': True}}})
        with self.assertRaises(S2Error):
            self.define({'interfaces': ['Identified'], 'properties': {'value': {'type': 'integer'}}})
        self.define({**PUBLIC, 'interfaces': ['Identified']})
        self.obj()
        with self.assertRaises(Conflict):
            self.define({'properties': {'id': {'type': 'integer', 'required': True}}})

    def test_object_redaction_and_hidden_link_endpoints(self):
        self.define(SCHEMA)
        self.obj(secret='hidden')
        self.obj('two', label={'level': 2, 'compartments': ['ENG']})
        self.call('ontology.link_type', branch=self.branch, expected_head=self.head(), name='Related',
                  source_type='Asset', target_type='Asset')
        self.call('links.put', branch=self.branch, expected_head=self.head(), link_id='link', link_type='Related',
                  source='one', target='two')
        user = self.member()
        result = self.service.call(user['token'], 'objects.list', branch=self.branch)
        self.assertEqual(len(result['objects']), 1)
        self.assertNotIn('secret', result['objects'][0]['values'])
        with self.assertRaises(NotFound):
            self.service.call(user['token'], 'objects.get', branch=self.branch, object_id='two')
        self.assertEqual(self.service.call(user['token'], 'links.list', branch=self.branch)['links'], [])

    def test_fork_isolation_and_three_way_nonconflicting_merge(self):
        self.define()
        fork = self.call('branches.fork', branch=self.branch, name='feature/branch')['branch']
        self.obj('left')
        self.obj('right', branch=fork)
        with self.assertRaises(NotFound):
            self.call('objects.get', branch=self.branch, object_id='right')
        self.call('branches.merge', branch=self.branch, source=fork, expected_head=self.head(), expected_source=self.head(fork))
        self.assertEqual(len(self.call('objects.list', branch=self.branch)['objects']), 2)

    def test_merge_conflict_preserves_target(self):
        self.define()
        fork = self.call('branches.fork', branch=self.branch, name='feature/conflict')['branch']
        self.obj('same', 1)
        self.obj('same', 2, branch=fork)
        previous = self.head()
        with self.assertRaises(Conflict):
            self.call('branches.merge', branch=self.branch, source=fork, expected_head=previous, expected_source=self.head(fork))
        self.assertEqual(self.head(), previous)

    def test_action_atomic_edits_and_idempotent_execution(self):
        self.define()
        one, two = self.obj(), self.obj('two')
        invocation = self.action([{'object': oid, 'expected_revision': obj['revision'], 'set': {'value': 10}}
                                  for oid, obj in [('one', one), ('two', two)]])
        result = self.call('actions.execute', branch=self.branch, invocation=invocation['invocation'])
        self.assertEqual(result, self.call('actions.execute', branch=self.branch, invocation=invocation['invocation']))
        for oid in ['one', 'two']:
            self.assertEqual(self.call('objects.get', branch=self.branch, object_id=oid)['values']['value'], 10)

    def test_action_invalid_second_edit_leaves_first_unchanged(self):
        self.define()
        one, two = self.obj(), self.obj('two')
        with self.assertRaises(S2Error):
            self.action([{'object': 'one', 'expected_revision': one['revision'], 'set': {'value': 10}},
                         {'object': 'two', 'expected_revision': two['revision'], 'set': {'value': 'bad'}}])
        self.assertEqual(self.call('objects.get', branch=self.branch, object_id='one')['values']['value'], 1)

    def test_action_approval_quorum_self_approval_and_revalidation(self):
        self.define()
        obj = self.obj()
        inv = self.action([{'object': 'one', 'expected_revision': obj['revision'], 'set': {'value': 8}}], approvals=2)
        with self.assertRaises(Forbidden):
            self.call('actions.approve', branch=self.branch, invocation=inv['invocation'], effect_hash=inv['effect_hash'])
        reviewers = [self.member('admin') for _ in range(2)]
        for reviewer in reviewers:
            self.service.call(reviewer['token'], 'actions.approve', branch=self.branch,
                              invocation=inv['invocation'], effect_hash=inv['effect_hash'])
        self.call('principals.revoke', principal=reviewers[0]['principal_id'])
        with self.assertRaises(Conflict):
            self.call('actions.execute', branch=self.branch, invocation=inv['invocation'])
        self.assertEqual(self.call('objects.get', branch=self.branch, object_id='one')['values']['value'], 1)

    def test_stale_approved_action_does_not_execute(self):
        self.define()
        obj = self.obj()
        inv = self.action([{'object': 'one', 'expected_revision': obj['revision'], 'set': {'value': 8}}])
        self.obj('other')
        with self.assertRaises(Conflict):
            self.call('actions.execute', branch=self.branch, invocation=inv['invocation'])
        self.assertEqual(self.call('objects.get', branch=self.branch, object_id='one')['values']['value'], 1)

    def test_concurrent_publications_have_one_winner(self):
        self.define()
        head = self.head()
        barrier = threading.Barrier(2)
        def write(oid):
            service = Service(self.db)
            barrier.wait(timeout=10)
            try:
                service.call(self.token, 'objects.put', branch=self.branch, expected_head=head,
                             object_id=oid, object_type='Asset', values={'id': oid, 'value': 1})
                return 'ok'
            except Conflict:
                return 'conflict'
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, ['first', 'second']))
        self.assertCountEqual(results, ['ok', 'conflict'])
        self.assertEqual(len(self.call('objects.list', branch=self.branch)['objects']), 1)

    def test_audit_failure_rolls_back_mutation(self):
        self.define()
        head = self.head()
        original = self.service.security.audit
        def fail(*args, **kwargs):
            raise RuntimeError('Injected audit storage failure')
        self.service.security.audit = fail
        try:
            with self.assertRaises(RuntimeError):
                self.obj()
        finally:
            self.service.security.audit = original
        self.assertEqual(self.head(), head)
        with self.assertRaises(NotFound):
            self.call('objects.get', branch=self.branch, object_id='one')

    def test_restart_and_immutable_audit_verification(self):
        self.define()
        self.obj()
        restarted = Service(self.db)
        self.assertEqual(restarted.call(self.token, 'objects.get', branch=self.branch, object_id='one')['values']['value'], 1)
        self.assertTrue(self.call('audit.verify')['valid'])
        with self.service.store.transaction() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE audit_logs SET hash=?', ('bad',))
        with self.service.store.transaction() as db:
            db.execute('DROP TRIGGER immutable_audit_update')
            db.execute('UPDATE audit_logs SET hash=? WHERE seq=(SELECT MIN(seq) FROM audit_logs)', ('tampered',))
        self.assertFalse(self.call('audit.verify')['valid'])

    def test_current_markings_apply_to_preexisting_branches(self):
        self.define()
        self.obj()
        self.dataset()
        self.tx([{'values': {'id': 'a', 'value': 1}}])
        fork = self.call('branches.fork', branch=self.branch, name='scenario/old')['branch']
        user = self.member()
        self.call('policy.restrict', branch=self.branch, resource='object/one',
                  label={'level': 2, 'compartments': ['ENG']})
        self.call('policy.restrict', branch=self.branch, resource='dataset/readings/property/value',
                  label={'level': 2, 'compartments': ['ENG']})
        for branch in (self.branch, fork):
            with self.assertRaises(NotFound):
                self.service.call(user['token'], 'objects.get', branch=branch, object_id='one')
            rows = self.service.call(user['token'], 'datasets.read', branch=branch, dataset='readings')['rows']
            self.assertEqual(rows, [{'id': 'a'}])
        with self.assertRaises(Forbidden):
            self.call('policy.restrict', branch=fork, resource='object/one', label={'level': 0})

    def test_policy_change_invalidates_action_execution(self):
        self.define()
        obj = self.obj()
        actor = self.member('editor')
        self.call('actions.define', branch=self.branch, expected_head=self.head(), name='SetValue',
                  object_type='Asset', writable_properties=['value'])
        inv = self.service.call(actor['token'], 'actions.submit', branch=self.branch,
                                expected_head=self.head(), action='SetValue', idempotency_key='policy-change',
                                edits=[{'object': 'one', 'expected_revision': obj['revision'], 'set': {'value': 9}}])
        self.call('policy.restrict', branch=self.branch, resource='type/Asset/property/value',
                  label={'level': 2, 'compartments': ['ENG']})
        with self.assertRaises(Forbidden):
            self.service.call(actor['token'], 'actions.execute', branch=self.branch, invocation=inv['invocation'])
        self.assertEqual(self.call('objects.get', branch=self.branch, object_id='one')['values']['value'], 1)

    def test_successful_multiparty_approval(self):
        self.define()
        obj = self.obj()
        inv = self.action([{'object': 'one', 'expected_revision': obj['revision'], 'set': {'value': 7}}], approvals=2)
        for _ in range(2):
            reviewer = self.member('admin')
            self.service.call(reviewer['token'], 'actions.approve', branch=self.branch,
                              invocation=inv['invocation'], effect_hash=inv['effect_hash'])
        result = self.call('actions.execute', branch=self.branch, invocation=inv['invocation'])
        self.assertEqual(result['status'], 'COMMITTED')
        self.assertEqual(self.call('objects.get', branch=self.branch, object_id='one')['values']['value'], 7)

    def test_second_object_storage_failure_rolls_back_entire_action(self):
        self.define()
        one, two = self.obj(), self.obj('two')
        inv = self.action([{'object': oid, 'expected_revision': obj['revision'], 'set': {'value': 11}}
                           for oid, obj in [('one', one), ('two', two)]])
        head = self.head()
        original = self.service.store.put_revision
        def injected(db, tenant, kind, payload):
            if kind == 'object' and payload['id'] == 'two':
                raise RuntimeError('Injected second object persistence failure')
            return original(db, tenant, kind, payload)
        self.service.store.put_revision = injected
        try:
            with self.assertRaises(RuntimeError):
                self.call('actions.execute', branch=self.branch, invocation=inv['invocation'])
        finally:
            self.service.store.put_revision = original
        self.assertEqual(self.head(), head)
        for oid in ['one', 'two']:
            self.assertEqual(self.call('objects.get', branch=self.branch, object_id=oid)['values']['value'], 1)
        self.assertEqual(self.call('actions.execute', branch=self.branch, invocation=inv['invocation'])['status'], 'COMMITTED')

    def test_loopback_api_authentication_validation_and_tenant_context(self):
        from s2.http import server
        httpd = server(self.service, 0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{httpd.server_port}'
        try:
            with urllib.request.urlopen(base + '/health') as response:
                self.assertEqual(response.status, 200)
            body = json.dumps({'operation': 'branches.head', 'params': {'branch': self.branch}}).encode()
            request = urllib.request.Request(base + '/v1/operations', data=body)
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(request)
            self.assertEqual(cm.exception.code, 401)
            request.add_header('Authorization', 'Bearer ' + self.token)
            with urllib.request.urlopen(request) as response:
                self.assertEqual(json.load(response)['branch'], self.branch)
            bad = urllib.request.Request(base + '/v1/operations', data=b'{"operation":"bootstrap","params":{}}',
                                         headers={'Authorization': 'Bearer ' + self.token})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(bad)
            self.assertEqual(cm.exception.code, 404)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
