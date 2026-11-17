# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
import unittest
from unittest.mock import patch
from pathlib import Path
import test_pipelines as base
from test_file_storage import HAS_PARQUET, HAS_ICEBERG
from s2.service import Service
from s2_platform import Conflict, Forbidden, NotFound, S2Error


class OrchestrationTests(unittest.TestCase):
    storage_backend = None
    setUp = base.PipelineTests.setUp
    tearDown = base.PipelineTests.tearDown
    call = base.PipelineTests.call
    head = base.PipelineTests.head
    member = base.PipelineTests.member
    dataset = base.PipelineTests.dataset
    tx = base.PipelineTests.tx
    define_pipeline = base.PipelineTests.define_pipeline
    params = base.PipelineTests.params
    preview = base.PipelineTests.preview
    run_pipeline = base.PipelineTests.run_pipeline
    read = base.PipelineTests.read

    def prepare(self):
        base.PipelineTests.prepare(self)
        self.call('datasets.create', branch=self.branch, expected_head=self.head(), dataset='final', schema=base.base.PUBLIC, primary_key='id')
        self.second = {**self.spec, 'input':'clean', 'output':'final', 'filter':{'property':'id','equals':'a'}}
        self.define_second()

    def define_second(self):
        self.call('pipelines.define', branch=self.branch, expected_head=self.head(), pipeline='a-final', definition=self.second)

    def chain_params(self, targets=None):
        return dict(branch=self.branch, expected_head=self.head(), targets=targets or ['a-final'])

    def chain_preview(self, targets=None):
        params=self.chain_params(targets)
        return params,self.call('orchestration.preview',**params)

    def chain_run(self, params=None, plan=None, key='chain', token=None):
        if params is None: params,plan=self.chain_preview()
        return self.service.call(token or self.token,'orchestration.run',**params,plan_hash=plan['plan_hash'],idempotency_key=key)

    def counts(self):
        with self.service.store.connect() as db:
            return tuple(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                         for table in ('commits','revisions','pipeline_runs','orchestration_runs'))

    def test_preview_has_no_catalog_or_artifact_writes(self):
        self.prepare();before=self.counts()
        files={str(p.relative_to(self.temp.name)):p.stat().st_size for p in Path(self.temp.name).rglob('*') if p.is_file() and '.warehouse' in str(p)}
        params,plan=self.chain_preview()
        self.assertEqual(plan['order'],['cleaning','a-final'])
        self.assertEqual([s['output_rows'] for s in plan['stages']],[2,1])
        self.assertTrue(plan['can_publish']);self.assertEqual(self.head(),params['expected_head'])
        self.assertEqual(self.counts(),before)
        self.assertEqual(files,{str(p.relative_to(self.temp.name)):p.stat().st_size for p in Path(self.temp.name).rglob('*') if p.is_file() and '.warehouse' in str(p)})

    def test_ordered_run_pinned_stage_receipts(self):
        self.prepare();result=self.chain_run()
        self.assertEqual(result['order'],['cleaning','a-final'])
        self.assertEqual(self.read('final')['rows'],[{'id':'a','value':10}])
        self.assertEqual(result['stages'][1]['input_revision'],result['stages'][0]['snapshot'])
        self.assertEqual(result['commit'],self.head())
        self.assertEqual(self.call('orchestration.get',branch=self.branch,run=result['run']),result)
        self.assertTrue(self.call('audit.verify')['valid'])

    def test_shared_upstream_runs_only_once(self):
        self.prepare()
        self.call('datasets.create',branch=self.branch,expected_head=self.head(),dataset='sibling',schema=base.base.PUBLIC,primary_key='id')
        self.call('pipelines.define',branch=self.branch,expected_head=self.head(),pipeline='b-sibling',definition={**self.spec,'input':'clean','output':'sibling'})
        params,plan=self.chain_preview(['a-final','b-sibling'])
        self.assertEqual(plan['order'],['cleaning','a-final','b-sibling'])
        result=self.chain_run(params,plan)
        self.assertEqual(len(result['stages']),3)

    def test_only_selected_dependency_closure_runs(self):
        self.prepare()
        params,plan=self.chain_preview(['cleaning'])
        self.assertEqual(plan['order'],['cleaning'])
        self.chain_run(params,plan);self.assertEqual(self.read('final')['rows'],[])

    def test_upstream_failure_blocks_dependents_without_publication(self):
        self.prepare();self.spec['expectations']={'min_rows':1,'max_rows':1000,'ranges':{'value':[0,5]}}
        self.define_pipeline();before=self.counts();params,plan=self.chain_preview()
        self.assertEqual([s['status'] for s in plan['stages']],['FAILED','BLOCKED'])
        with self.assertRaises(Conflict):self.chain_run(params,plan)
        self.assertEqual(self.counts(),before)

    def test_downstream_health_failure_preserves_all_outputs(self):
        self.prepare();self.chain_run()
        before_clean=self.read();before_final=self.read('final')
        self.second['expectations']={'min_rows':1,'max_rows':1000,'ranges':{'value':[0,5]}}
        self.define_second();params,plan=self.chain_preview()
        self.assertEqual([s['status'] for s in plan['stages']],['READY','FAILED'])
        with self.assertRaises(Conflict):self.chain_run(params,plan,key='failed')
        self.assertEqual(self.read()['snapshot'],before_clean['snapshot'])
        self.assertEqual(self.read('final')['snapshot'],before_final['snapshot'])

    def test_second_stage_runtime_failure_rolls_back_first(self):
        self.prepare();params,plan=self.chain_preview();before=self.counts()
        original=self.service.pipelines.run;calls=[]
        def fail_second(*args,**kwargs):
            calls.append(1)
            if len(calls)==2:raise RuntimeError('second stage failed')
            return original(*args,**kwargs)
        with patch.object(self.service.pipelines,'run',side_effect=fail_second):
            with self.assertRaises(RuntimeError):self.chain_run(params,plan)
        self.assertEqual(self.counts(),before);self.assertEqual(self.head(),params['expected_head'])
        self.assertEqual(self.read()['rows'],[]);self.assertEqual(self.read('final')['rows'],[])
        self.chain_run(params,plan)

    def test_success_audit_failure_rolls_back_entire_chain(self):
        self.prepare();params,plan=self.chain_preview();before=self.counts()
        with patch.object(self.service.security,'audit',side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):self.chain_run(params,plan)
        self.assertEqual(self.counts(),before);self.assertEqual(self.head(),params['expected_head'])
        self.chain_run(params,plan)

    def test_restart_retry_does_not_rerun_stages(self):
        self.prepare();params,plan=self.chain_preview();result=self.chain_run(params,plan);before=self.counts()
        self.service=Service(self.db)
        self.assertEqual(self.chain_run(params,plan),result);self.assertEqual(self.counts(),before)
        with self.assertRaises(Conflict):self.chain_run(params,{**plan,'plan_hash':'wrong'})

    def test_stale_head_and_wrong_plan(self):
        self.prepare();params,plan=self.chain_preview()
        with self.assertRaises(Conflict):self.chain_run(params,{**plan,'plan_hash':'wrong'})
        self.tx([{'values':{'id':'a','value':11}}],operation='UPDATE',key='refresh')
        with self.assertRaises(Conflict):self.chain_run(params,plan)

    def test_policy_change_requires_new_preview(self):
        self.prepare();params,plan=self.chain_preview()
        self.call('policy.restrict',branch=self.branch,resource='dataset/readings/property/value',label={'level':2})
        with self.assertRaises(Conflict):self.chain_run(params,plan)
        self.chain_run()
        with self.assertRaises(Forbidden):self.read('final',token=self.member()['token'])

    def test_pipeline_policy_blocks_replay_and_receipt(self):
        self.prepare();low=self.member('editor');params=self.chain_params()
        plan=self.service.call(low['token'],'orchestration.preview',**params)
        result=self.chain_run(params,plan,token=low['token'])
        self.call('policy.restrict',branch=self.branch,resource='pipeline/cleaning',label={'level':2})
        with self.assertRaises(Forbidden):self.chain_run(params,plan,token=low['token'])
        with self.assertRaises(Forbidden):self.service.call(low['token'],'orchestration.get',branch=self.branch,run=result['run'])

    def test_branch_isolation(self):
        self.prepare();parent=self.branch;before=self.head()
        self.branch=self.call('branches.fork',branch=parent,name='chain')['branch']
        self.chain_run();self.assertEqual(len(self.read('final')['rows']),1)
        self.branch=parent;self.assertEqual(self.head(),before);self.assertEqual(self.read('final')['rows'],[])

    def test_roles_tenants_membership_and_receipt_ownership(self):
        self.prepare();params=self.chain_params()
        for user,error in [(self.member(),Forbidden),(self.member('editor',grant=False),Forbidden),
                           (self.service.bootstrap('other',3,['ENG']),NotFound)]:
            with self.assertRaises(error):self.service.call(user['token'],'orchestration.preview',**params)
        result=self.chain_run()
        with self.assertRaises(NotFound):self.service.call(self.member('admin',3,['ENG'])['token'],'orchestration.get',branch=self.branch,run=result['run'])

    def test_cycle_and_competing_writer_rejected(self):
        self.prepare()
        self.call('pipelines.define',branch=self.branch,expected_head=self.head(),pipeline='cycle',definition={**self.spec,'input':'final','output':'readings'})
        with self.assertRaisesRegex(Conflict,'cycle'):self.chain_preview()
        self.call('pipelines.define',branch=self.branch,expected_head=self.head(),pipeline='duplicate',definition=self.spec)
        with self.assertRaisesRegex(Conflict,'Competing'):self.chain_preview()

    def test_invalid_targets_and_unknown_pipeline(self):
        self.prepare()
        for targets in [[],['a-final','a-final'],[None],['x']*21,'a-final']:
            with self.assertRaises(S2Error):self.call('orchestration.preview',**{**self.chain_params(),'targets':targets})
        with self.assertRaises(NotFound):self.chain_preview(['missing'])

    def test_concurrent_chains_have_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        self.prepare();params,plan=self.chain_preview()
        def attempt(key):
            try:return self.chain_run(params,plan,key)['status']
            except Conflict:return 'CONFLICT'
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertCountEqual(list(pool.map(attempt,['one','two'])),['COMMITTED','CONFLICT'])

    def test_completed_chain_is_merge_coherent(self):
        self.prepare();parent=self.branch
        self.branch=self.call('branches.fork',branch=parent,name='chain')['branch'];child=self.branch
        self.chain_run();self.branch=parent
        params=dict(branch=parent,source=child,expected_head=self.head(),expected_source=self.head(child),reason='Publish complete chain')
        plan=self.call('branches.merge_preview',**params)
        self.assertEqual(len(plan['pipelines']),2)
        self.call('branches.merge_reviewed',**params,plan_hash=plan['plan_hash'],idempotency_key='merge')
        self.assertEqual(self.read('final')['rows'],[{'id':'a','value':10}])

    def test_refresh_replaces_downstream_outputs(self):
        self.prepare();self.chain_run()
        self.tx([{'values':{'id':'a','value':35}}],operation='UPDATE',key='refresh')
        params,plan=self.chain_preview()
        self.assertEqual([s['output_rows'] for s in plan['stages']],[2,1])
        self.chain_run(params,plan,key='refreshed')
        self.assertEqual(self.read('final')['rows'],[{'id':'a','value':35}])

    def test_target_order_is_normalized_for_preview_and_retry(self):
        self.prepare();params,plan=self.chain_preview(['a-final','cleaning'])
        reversed_params={**params,'targets':['cleaning','a-final']}
        self.assertEqual(self.call('orchestration.preview',**reversed_params),plan)
        result=self.chain_run(params,plan)
        self.assertEqual(self.chain_run(reversed_params,plan),result)

    def test_dependency_closure_bound(self):
        self.prepare();previous='final'
        for index in range(19):
            output='stage'+str(index)
            self.call('datasets.create',branch=self.branch,expected_head=self.head(),dataset=output,schema=base.base.PUBLIC,primary_key='id')
            self.call('pipelines.define',branch=self.branch,expected_head=self.head(),pipeline=output,
                      definition={**self.spec,'input':previous,'output':output})
            previous=output
        with self.assertRaisesRegex(S2Error,'20 stages'):self.chain_preview([previous])


@unittest.skipUnless(HAS_PARQUET,'Parquet dependencies not installed')
class ParquetOrchestrationTests(OrchestrationTests):storage_backend='parquet'

@unittest.skipUnless(HAS_ICEBERG,'Iceberg dependencies not installed')
class IcebergOrchestrationTests(OrchestrationTests):storage_backend='iceberg'
