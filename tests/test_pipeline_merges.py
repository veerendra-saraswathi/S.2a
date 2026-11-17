# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
import json
import unittest
from unittest.mock import patch
import test_pipelines as base
from test_file_storage import HAS_PARQUET, HAS_ICEBERG
from s2.service import Service
from s2_platform import Conflict, Forbidden, NotFound


class PipelineMergeTests(unittest.TestCase):
    storage_backend = None
    setUp = base.PipelineTests.setUp
    tearDown = base.PipelineTests.tearDown
    call = base.PipelineTests.call
    head = base.PipelineTests.head
    member = base.PipelineTests.member
    dataset = base.PipelineTests.dataset
    tx = base.PipelineTests.tx
    define = base.PipelineTests.define
    build = base.PipelineTests.build
    objects = base.PipelineTests.objects
    define_pipeline = base.PipelineTests.define_pipeline
    params = base.PipelineTests.params
    preview = base.PipelineTests.preview
    run_pipeline = base.PipelineTests.run_pipeline
    read = base.PipelineTests.read

    def prepare(self, bound=False):
        base.PipelineTests.prepare(self)
        self.run_pipeline()
        if bound:
            self.define(base.base.TARGET)
            self.call('bindings.define', branch=self.branch, expected_head=self.head(), binding='assets', dataset='clean',
                      object_type='Asset', namespace='lab', mappings={'id':'id','value':'value'}, action_properties=['status'], initial_values={'status':'pending'})
            self.build()
        self.parent = self.branch
        self.child = self.call('branches.fork', branch=self.branch, name='scenario')['branch']

    def refresh(self, run=True, build=False):
        self.branch = self.child
        try:
            self.tx([{'values':{'id':'a','value':30}}], operation='UPDATE', key='refresh')
            if run: self.run_pipeline(key='refresh')
            if build: self.build(key='refresh')
        finally: self.branch = self.parent

    def merge_preview(self):
        params = dict(branch=self.parent, source=self.child, expected_head=self.head(self.parent),
                      expected_source=self.head(self.child), reason='Review scenario pipeline results')
        return params, self.call('branches.merge_preview', **params)

    def merge(self, params=None, plan=None, key='merge', token=None):
        if params is None: params, plan = self.merge_preview()
        return self.service.call(token or self.token, 'branches.merge_reviewed', **params,
                                 plan_hash=plan['plan_hash'], idempotency_key=key)

    def test_coherent_refresh_preview_and_two_parent_commit(self):
        self.prepare(); self.refresh()
        source_head = self.head(self.child)
        with self.service.store.connect() as db:
            before = tuple(db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('commits','revisions','pipeline_runs'))
        params, plan = self.merge_preview()
        self.assertEqual(len(plan['pipelines']),1)
        self.assertEqual(plan['pipelines'][0]['health'],'PASSED')
        with self.service.store.connect() as db:
            self.assertEqual(before,tuple(db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('commits','revisions','pipeline_runs')))
        result = self.merge(params,plan)
        self.assertEqual(self.head(self.child),source_head)
        self.assertEqual(self.read()['rows'],[{'id':'a','value':30},{'id':'b','value':20}])
        with self.service.store.connect() as db:
            parents=json.loads(db.execute('SELECT parents FROM commits WHERE id=?',(result['commit'],)).fetchone()[0])
            self.assertEqual(parents,[params['expected_head'],source_head])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM pipeline_runs').fetchone()[0],before[2])
        self.assertTrue(self.call('audit.verify')['valid'])

    def test_stale_input_without_run_rejected(self):
        self.prepare();self.refresh(run=False)
        with self.assertRaisesRegex(Conflict,'stale'):self.merge_preview()

    def test_revised_definition_requires_matching_output(self):
        self.prepare();self.branch=self.child
        self.spec['filter']={'property':'id','equals':'a'};self.define_pipeline();self.branch=self.parent
        with self.assertRaisesRegex(Conflict,'stale'):self.merge_preview()
        self.branch=self.child;self.run_pipeline(key='filter');self.branch=self.parent
        self.merge();self.assertEqual(self.read()['rows'],[{'id':'a','value':10}])

    def test_manual_output_edit_rejected_despite_retained_provenance(self):
        self.prepare();self.branch=self.child
        tx=self.call('datasets.begin',branch=self.branch,expected_head=self.head(),dataset='clean',operation='UPDATE',idempotency_key='manual')['transaction']
        self.call('datasets.stage',branch=self.branch,transaction=tx,rows=[{'values':{'id':'a','value':99}}])
        self.call('datasets.commit',branch=self.branch,transaction=tx);self.branch=self.parent
        with self.assertRaises(Conflict):self.merge_preview()

    def test_bound_objects_must_follow_pipeline_output(self):
        self.prepare(bound=True);self.refresh()
        with self.assertRaisesRegex(Conflict,'stale'):self.merge_preview()
        self.branch=self.child;self.build(key='refreshed');self.branch=self.parent
        self.merge();self.assertEqual(sorted(o['values']['value'] for o in self.objects()),[20,30])

    def test_target_operational_edit_conflicts_with_source_rebuild(self):
        self.prepare(bound=True);self.refresh(build=True)
        obj=self.objects()[0]
        self.call('objects.put',branch=self.parent,expected_head=self.head(),object_id=obj['id'],object_type='Asset',
                  values={**obj['values'],'status':'reviewed'},expected_revision=obj['revision'])
        before=self.head()
        with self.assertRaises(Conflict):self.merge_preview()
        self.assertEqual(self.head(),before)
        self.assertEqual(self.objects()[0]['values']['status'],'reviewed')

    def test_current_policy_changes_hash_and_blocks_low_clearance(self):
        self.prepare();self.refresh();params,plan=self.merge_preview()
        self.call('policy.restrict',branch=self.parent,resource='pipeline/cleaning',label={'level':2})
        with self.assertRaises(Conflict):self.merge(params,plan)
        with self.assertRaises(Forbidden):
            self.service.call(self.member('admin')['token'],'branches.merge_preview',**params)
        self.merge()

    def test_restart_replay_rechecks_source_policy(self):
        self.prepare();self.refresh();low=self.member('admin');params,_=self.merge_preview()
        plan=self.service.call(low['token'],'branches.merge_preview',**params)
        result=self.merge(params,plan,token=low['token']);self.service=Service(self.db)
        self.assertEqual(self.merge(params,plan,token=low['token']),result)
        self.call('policy.restrict',branch=self.parent,resource='dataset/readings/property/value',label={'level':2})
        with self.assertRaises(Forbidden):self.merge(params,plan,token=low['token'])

    def test_competing_pipeline_writers_rejected(self):
        self.prepare();self.branch=self.child
        self.call('pipelines.define',branch=self.branch,expected_head=self.head(),pipeline='other-writer',definition=self.spec)
        self.branch=self.parent
        with self.assertRaisesRegex(Conflict,'competing'):self.merge_preview()

    def test_definition_cycle_rejected(self):
        self.prepare();self.branch=self.child
        for name in ('x','y'):
            self.call('datasets.create',branch=self.branch,expected_head=self.head(),dataset=name,schema=base.base.PUBLIC,primary_key='id')
        for name,source,target in [('xy','x','y'),('yx','y','x')]:
            self.call('pipelines.define',branch=self.branch,expected_head=self.head(),pipeline=name,
                      definition={**self.spec,'input':source,'output':target})
        self.branch=self.parent
        with self.assertRaisesRegex(Conflict,'cycle'):self.merge_preview()

    def test_transitive_chain_requires_all_downstream_runs(self):
        self.prepare();self.branch=self.child
        self.call('datasets.create',branch=self.branch,expected_head=self.head(),dataset='final',schema=base.base.PUBLIC,primary_key='id')
        self.call('pipelines.define',branch=self.branch,expected_head=self.head(),pipeline='second',definition={**self.spec,'input':'clean','output':'final'})
        self.branch=self.parent
        with self.assertRaisesRegex(Conflict,'missing or stale'):self.merge_preview()
        self.branch=self.child
        params={**self.params(),'pipeline':'second'};plan=self.call('pipelines.preview',**params)
        self.call('pipelines.run',**params,plan_hash=plan['plan_hash'],idempotency_key='second')
        self.branch=self.parent
        params,plan=self.merge_preview();self.assertEqual([p['pipeline'] for p in plan['pipelines']],['cleaning','second'])
        self.merge(params,plan)
        self.assertEqual(self.read('final')['rows'],self.read()['rows'])

    def test_audit_failure_rolls_back_merge_and_replay(self):
        self.prepare();self.refresh();params,plan=self.merge_preview()
        with patch.object(self.service.security,'audit',side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):self.merge(params,plan)
        self.assertEqual(self.head(),params['expected_head'])
        with self.service.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM branch_merges').fetchone()[0],0)
        self.merge(params,plan)

    def test_concurrent_merge_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        self.prepare();self.refresh();params,plan=self.merge_preview()
        def attempt(key):
            try:return self.merge(params,plan,key)['status']
            except Conflict:return 'CONFLICT'
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertCountEqual(list(pool.map(attempt,['one','two'])),['COMMITTED','CONFLICT'])

    def test_stale_source_head_and_legacy_guard(self):
        self.prepare();self.refresh();params,plan=self.merge_preview()
        with self.assertRaises(Conflict):
            self.call('branches.merge',**{k:v for k,v in params.items() if k!='reason'})
        self.branch=self.child;self.run_pipeline(key='another');self.branch=self.parent
        with self.assertRaises(Conflict):self.merge(params,plan)

    def test_receipts_remain_on_origin_branch(self):
        self.prepare();self.branch=self.child
        result=self.run_pipeline(key='scenario');self.branch=self.parent;self.merge()
        with self.assertRaises(NotFound):self.call('pipelines.get',branch=self.parent,run=result['run'])
        self.assertEqual(self.call('pipelines.get',branch=self.child,run=result['run']),result)

    def alter_stored_output(self, change):
        # Fault-injection fixture: retain otherwise valid provenance/schema pins.
        with self.service.store.transaction() as db:
            ctx=self.service.security.authenticate(db,self.token)
            head=self.service.store.branch(db,ctx.tenant,self.child)['head']
            manifest=self.service.store.manifest(db,ctx.tenant,head)
            value=self.service.store.revision(db,ctx.tenant,manifest['dataset/clean'])
            change(value)
            manifest['dataset/clean']=self.service.store.put_revision(db,ctx.tenant,'dataset_snapshot',value)
            self.service.store.publish(db,ctx,self.child,head,manifest)

    def test_output_value_proof_detects_injected_mismatch(self):
        self.prepare()
        self.alter_stored_output(lambda value:value['rows'][0]['values'].update(value=99))
        with self.assertRaisesRegex(Conflict,'declared transform'):self.merge_preview()

    def test_missing_policy_dependencies_rejected(self):
        self.prepare()
        self.alter_stored_output(lambda value:value['source_pipeline'].update(policy_resources=[]))
        with self.assertRaisesRegex(Conflict,'policy dependencies'):self.merge_preview()

    def test_classification_downgrade_rejected(self):
        self.prepare();self.branch=self.child
        self.tx([{'values':{'id':'a','value':10},'marking':{'level':2}}],operation='UPDATE',key='restricted')
        self.run_pipeline(key='restricted');self.branch=self.parent
        self.alter_stored_output(lambda value:value['source_pipeline'].update(marking={'level':0}))
        with self.assertRaisesRegex(Conflict,'classification'):self.merge_preview()


@unittest.skipUnless(HAS_PARQUET,'Parquet dependencies not installed')
class ParquetPipelineMergeTests(PipelineMergeTests):storage_backend='parquet'

@unittest.skipUnless(HAS_ICEBERG,'Iceberg dependencies not installed')
class IcebergPipelineMergeTests(PipelineMergeTests):storage_backend='iceberg'
