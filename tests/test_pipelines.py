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


class PipelineTests(unittest.TestCase):
    storage_backend=None
    setUp=base.BindingTests.setUp
    tearDown=base.BindingTests.tearDown
    call=base.BindingTests.call
    head=base.BindingTests.head
    member=base.BindingTests.member
    dataset=base.BindingTests.dataset
    tx=base.BindingTests.tx
    define=base.BindingTests.define
    build=base.BindingTests.build
    objects=base.BindingTests.objects

    def prepare(self):
        self.dataset()
        self.tx([{'values':{'id':'a','value':10}},{'values':{'id':'b','value':20}}])
        self.call('datasets.create',branch=self.branch,expected_head=self.head(),dataset='clean',schema=base.PUBLIC,primary_key='id')
        self.spec={'input':'readings','output':'clean','mappings':{'id':'id','value':'value'},'filter':None,
                   'expectations':{'min_rows':1,'max_rows':1000,'ranges':{'value':[0,100]}}}
        self.define_pipeline()

    def define_pipeline(self):
        return self.call('pipelines.define',branch=self.branch,expected_head=self.head(),pipeline='cleaning',definition=self.spec)

    def params(self):return dict(branch=self.branch,expected_head=self.head(),pipeline='cleaning')
    def preview(self):
        params=self.params();return params,self.call('pipelines.preview',**params)
    def run_pipeline(self,params=None,plan=None,key='run',token=None):
        if params is None:params,plan=self.preview()
        return self.service.call(token or self.token,'pipelines.run',**params,plan_hash=plan['plan_hash'],idempotency_key=key)
    def read(self,dataset='clean',token=None):
        return self.service.call(token or self.token,'datasets.read',branch=self.branch,dataset=dataset)

    def test_preview_atomic_replacement_and_pinned_receipt(self):
        self.prepare()
        with self.service.store.connect() as db:
            before=db.execute('SELECT COUNT(*) FROM commits').fetchone()[0]
        params,plan=self.preview();self.assertTrue(plan['can_publish']);self.assertEqual(self.head(),params['expected_head'])
        result=self.run_pipeline(params,plan)
        self.assertEqual(self.read()['rows'],self.read('readings')['rows'])
        self.assertEqual(self.call('pipelines.get',branch=self.branch,run=result['run']),result)
        with self.service.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM commits').fetchone()[0],before+1)
            self.assertEqual(db.execute('SELECT version FROM schema_version').fetchone()[0],6)
            snapshot=self.service.store.revision(db,self.admin['tenant_id'],result['snapshot'])
            self.assertEqual(snapshot['source_pipeline']['input_revision'],plan['input_revision'])
        self.assertTrue(self.call('audit.verify')['valid'])

    def test_repeat_after_refresh_replaces_not_appends(self):
        self.prepare();self.run_pipeline()
        self.tx([{'values':{'id':'a','value':30}}],operation='UPDATE',key='refresh')
        self.run_pipeline(key='refresh-run')
        self.assertEqual(self.read()['rows'],[{'id':'a','value':30},{'id':'b','value':20}])

    def test_versioned_filter_and_branch_isolation(self):
        self.prepare();original=self.head()
        child=self.call('branches.fork',branch=self.branch,name='scenario')['branch']
        parent=self.branch;self.branch=child
        self.spec['filter']={'property':'value','equals':20};self.define_pipeline();self.run_pipeline()
        self.assertEqual(self.read()['rows'],[{'id':'b','value':20}])
        self.branch=parent;self.assertEqual(self.head(),original);self.assertEqual(self.read()['rows'],[])

    def test_health_failure_preserves_previous_output(self):
        self.prepare();self.run_pipeline();before=self.read()
        self.spec['expectations']['ranges']['value']=[0,15];self.define_pipeline()
        params,plan=self.preview();self.assertFalse(plan['can_publish'])
        self.assertEqual(plan['errors'],[{'record':2,'code':'RANGE','property':'value'}])
        with self.assertRaises(Conflict):self.run_pipeline(params,plan,key='bad')
        self.assertEqual(self.read()['snapshot'],before['snapshot'])

    def test_row_count_failure_and_explicit_empty_output(self):
        self.prepare();self.spec['filter']={'property':'id','equals':'absent'};self.define_pipeline()
        params,plan=self.preview();self.assertIn({'code':'ROW_COUNT'},plan['errors'])
        with self.assertRaises(Conflict):self.run_pipeline(params,plan)
        self.spec['expectations']['min_rows']=0;self.define_pipeline();self.run_pipeline()
        self.assertEqual(self.read()['rows'],[])

    def test_invalid_definitions_and_identity_changes(self):
        self.prepare();original=copy.deepcopy(self.spec)
        variants=[{'mappings':{'id':'value','value':'value'}}, {'mappings':{'id':'id'}},
                  {'filter':{'property':'value','equals':'10'}}, {'output':'readings'},
                  {'expectations':{'min_rows':True,'max_rows':10,'ranges':{}}},
                  {'expectations':{'min_rows':0,'max_rows':10,'ranges':{'id':[0,2]}}},
                  {'expectations':{'min_rows':0,'max_rows':10,'ranges':{'value':[float('nan'),2]}}}]
        for update in variants:
            self.spec={**original,**update}
            with self.assertRaises(S2Error):self.define_pipeline()
        self.spec={**original,'code':'arbitrary'}
        with self.assertRaises(S2Error):self.define_pipeline()

    def test_stale_head_and_wrong_plan(self):
        self.prepare();params,plan=self.preview()
        with self.assertRaises(Conflict):self.run_pipeline(params,{**plan,'plan_hash':'wrong'})
        self.tx([{'values':{'id':'a','value':11}}],operation='UPDATE',key='change')
        with self.assertRaises(Conflict):self.run_pipeline(params,plan)

    def test_live_policy_invalidates_plan_and_replay(self):
        self.prepare();low=self.member('editor');params=self.params()
        plan=self.service.call(low['token'],'pipelines.preview',**params)
        self.call('policy.restrict',branch=self.branch,resource='dataset/readings/property/value',label={'level':2})
        with self.assertRaises(Forbidden):self.run_pipeline(params,plan,token=low['token'])
        highparams,highplan=self.preview()
        self.call('policy.restrict',branch=self.branch,resource='dataset/readings/row/"a"',label={'level':2})
        with self.assertRaises(Conflict):self.run_pipeline(highparams,highplan)
        self.run_pipeline()

    def test_current_policy_protects_derived_reads_and_receipts(self):
        self.prepare();low=self.member('editor');params=self.params()
        plan=self.service.call(low['token'],'pipelines.preview',**params)
        result=self.run_pipeline(params,plan,token=low['token'])
        self.call('policy.restrict',branch=self.branch,resource='dataset/readings/property/value',label={'level':2})
        with self.assertRaises(Forbidden):self.read(token=low['token'])
        with self.assertRaises(Forbidden):self.run_pipeline(params,plan,token=low['token'])
        with self.assertRaises(Forbidden):self.service.call(low['token'],'pipelines.get',branch=self.branch,run=result['run'])

    def test_static_source_markings_protect_even_empty_output(self):
        self.prepare()
        self.tx([{'values':{'id':'a','value':10},'marking':{'level':2}}],operation='UPDATE',key='marked')
        self.spec['filter']={'property':'id','equals':'absent'};self.spec['expectations']['min_rows']=0
        self.define_pipeline();self.run_pipeline()
        with self.assertRaises(Forbidden):self.read(token=self.member()['token'])

    def test_downstream_objects_retain_upstream_live_dependencies(self):
        self.prepare();self.run_pipeline();self.define(base.TARGET)
        self.call('bindings.define',branch=self.branch,expected_head=self.head(),binding='assets',dataset='clean',
                  object_type='Asset',namespace='lab',mappings={'id':'id','value':'value'},action_properties=['status'],initial_values={'status':'pending'})
        self.build();low=self.member();self.assertEqual(len(self.objects(token=low['token'])),2)
        self.call('policy.restrict',branch=self.branch,resource='dataset/readings',label={'level':2})
        self.assertEqual(self.objects(token=low['token']),[])

    def test_replay_after_restart_and_changed_request(self):
        self.prepare();params,plan=self.preview();result=self.run_pipeline(params,plan)
        self.service=Service(self.db);self.assertEqual(self.run_pipeline(params,plan),result)
        self.assertEqual(self.head(),result['commit'])
        with self.assertRaises(Conflict):self.run_pipeline(params,{**plan,'plan_hash':'other'})

    def test_roles_tenants_and_receipt_owner(self):
        self.prepare();params=self.params()
        for user,error in [(self.member(),Forbidden),(self.member('editor',grant=False),Forbidden),
                           (self.service.bootstrap('other',3,['ENG']),NotFound)]:
            with self.assertRaises(error):self.service.call(user['token'],'pipelines.preview',**params)
        with self.assertRaises(Forbidden):
            self.service.call(self.member('editor')['token'],'pipelines.define',branch=self.branch,expected_head=self.head(),pipeline='p',definition=self.spec)
        result=self.run_pipeline()
        with self.assertRaises(NotFound):
            self.service.call(self.member('admin',3,['ENG'])['token'],'pipelines.get',branch=self.branch,run=result['run'])

    def test_audit_failure_rolls_back_output_and_receipt(self):
        self.prepare();params,plan=self.preview()
        with patch.object(self.service.security,'audit',side_effect=RuntimeError('audit failure')):
            with self.assertRaises(RuntimeError):self.run_pipeline(params,plan)
        self.assertEqual(self.head(),params['expected_head']);self.assertEqual(self.read()['rows'],[])
        with self.service.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM pipeline_runs').fetchone()[0],0)
        self.run_pipeline(params,plan)

    def test_storage_failure_preserves_head(self):
        self.prepare();params,plan=self.preview()
        with patch.object(self.service.store,'put_revision',side_effect=RuntimeError('storage failure')):
            with self.assertRaises(RuntimeError):self.run_pipeline(params,plan)
        self.assertEqual(self.head(),params['expected_head']);self.run_pipeline(params,plan)

    def test_concurrent_publications_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        self.prepare();params,plan=self.preview()
        def attempt(key):
            try:return self.run_pipeline(params,plan,key)['status']
            except Conflict:return 'CONFLICT'
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertCountEqual(list(pool.map(attempt,['one','two'])),['COMMITTED','CONFLICT'])

    def test_pipeline_merge_is_explicitly_blocked(self):
        self.prepare();child=self.call('branches.fork',branch=self.branch,name='scenario')['branch']
        params=dict(branch=self.branch,source=child,expected_head=self.head(),expected_source=self.head(child))
        with self.assertRaises(Conflict):self.call('branches.merge',**params)
        with self.assertRaises(Conflict):self.call('branches.merge_preview',**params,reason='Review')

    def test_transitive_pipeline_policy_and_cycle_rejection(self):
        self.prepare();self.run_pipeline()
        self.call('datasets.create',branch=self.branch,expected_head=self.head(),dataset='final',schema=base.PUBLIC,primary_key='id')
        second={**self.spec,'input':'clean','output':'final'}
        self.call('pipelines.define',branch=self.branch,expected_head=self.head(),pipeline='second',definition=second)
        params={**self.params(),'pipeline':'second'};plan=self.call('pipelines.preview',**params)
        self.call('pipelines.run',**params,plan_hash=plan['plan_hash'],idempotency_key='second')
        with self.assertRaises(Conflict):
            self.call('pipelines.define',branch=self.branch,expected_head=self.head(),pipeline='cycle',definition={**self.spec,'input':'final','output':'readings'})
        self.call('policy.restrict',branch=self.branch,resource='dataset/readings',label={'level':2})
        with self.assertRaises(Forbidden):self.read('final',token=self.member()['token'])

    def test_projection_renames_fields_without_coercion(self):
        self.prepare()
        schema={'properties':{'asset':{'type':'string','required':True},'reading':{'type':'integer','required':True}}}
        self.call('datasets.create',branch=self.branch,expected_head=self.head(),dataset='renamed',schema=schema,primary_key='asset')
        spec={**self.spec,'output':'renamed','mappings':{'asset':'id','reading':'value'},
              'expectations':{'min_rows':1,'max_rows':10,'ranges':{'reading':[0,100]}}}
        self.call('pipelines.define',branch=self.branch,expected_head=self.head(),pipeline='rename',definition=spec)
        params={**self.params(),'pipeline':'rename'};plan=self.call('pipelines.preview',**params)
        self.call('pipelines.run',**params,plan_hash=plan['plan_hash'],idempotency_key='rename')
        self.assertEqual(self.read('renamed')['rows'],[{'asset':'a','reading':10},{'asset':'b','reading':20}])

    def test_missing_required_projection_rejects_whole_run(self):
        source=copy.deepcopy(base.PUBLIC);source['properties']['value']['required']=False
        self.dataset(source);self.tx([{'values':{'id':'a'}}])
        self.call('datasets.create',branch=self.branch,expected_head=self.head(),dataset='clean',schema=base.PUBLIC,primary_key='id')
        self.spec={'input':'readings','output':'clean','mappings':{'id':'id','value':'value'},'filter':None,
                   'expectations':{'min_rows':0,'max_rows':1000,'ranges':{}}}
        self.define_pipeline();params,plan=self.preview()
        self.assertEqual(plan['errors'],[{'record':1,'code':'OUTPUT_SCHEMA'}])
        with self.assertRaises(Conflict):self.run_pipeline(params,plan)
        self.assertEqual(self.read()['rows'],[])

    def test_input_row_bound(self):
        self.prepare()
        self.tx([{'values':{'id':'x'+str(i),'value':1}} for i in range(999)],key='large')
        with self.assertRaises(S2Error):self.preview()

    def test_pipeline_restriction_applies_to_existing_output(self):
        self.prepare();self.run_pipeline()
        self.call('policy.restrict',branch=self.branch,resource='pipeline/cleaning',label={'level':2})
        with self.assertRaises(Forbidden):self.read(token=self.member()['token'])

    def test_output_row_restriction_changes_plan(self):
        self.prepare();params,plan=self.preview()
        self.call('policy.restrict',branch=self.branch,resource='dataset/clean/row/"a"',label={'level':2})
        with self.assertRaises(Conflict):self.run_pipeline(params,plan)
        self.run_pipeline()
        self.assertEqual(self.read(token=self.member()['token'])['rows'],[{'id':'b','value':20}])


@unittest.skipUnless(HAS_PARQUET,'Parquet dependencies not installed')
class ParquetPipelineTests(PipelineTests):storage_backend='parquet'

@unittest.skipUnless(HAS_ICEBERG,'Iceberg dependencies not installed')
class IcebergPipelineTests(PipelineTests):storage_backend='iceberg'
