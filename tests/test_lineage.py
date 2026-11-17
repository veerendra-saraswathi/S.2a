# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
import unittest
import copy
import test_orchestration as base
import test_foundation as foundation
from test_file_storage import HAS_PARQUET, HAS_ICEBERG
from s2.service import Service
from s2_platform import Conflict, Forbidden, NotFound, S2Error


class LineageTests(unittest.TestCase):
    storage_backend=None
    setUp=base.OrchestrationTests.setUp
    tearDown=base.OrchestrationTests.tearDown
    call=base.OrchestrationTests.call
    head=base.OrchestrationTests.head
    member=base.OrchestrationTests.member
    dataset=base.OrchestrationTests.dataset
    tx=base.OrchestrationTests.tx
    define_pipeline=base.OrchestrationTests.define_pipeline
    define_second=base.OrchestrationTests.define_second
    params=base.OrchestrationTests.params
    preview=base.OrchestrationTests.preview
    run_pipeline=base.OrchestrationTests.run_pipeline
    read=base.OrchestrationTests.read
    chain_params=base.OrchestrationTests.chain_params
    chain_preview=base.OrchestrationTests.chain_preview
    chain_run=base.OrchestrationTests.chain_run

    def prepare(self):
        base.OrchestrationTests.prepare(self)
        self.chain_run()
        schema={'properties':{**foundation.PUBLIC['properties'],'status':{'type':'string','required':True}}}
        self.call('ontology.define',branch=self.branch,expected_head=self.head(),name='Asset',definition=schema)
        self.call('bindings.define',branch=self.branch,expected_head=self.head(),binding='assets',dataset='final',object_type='Asset',namespace='lab',
                  mappings={'id':'id','value':'value'},action_properties=['status'],initial_values={'status':'pending'})
        built=self.call('bindings.materialize',branch=self.branch,expected_head=self.head(),binding='assets',idempotency_key='build')
        self.oid=next(iter(built['objects']))

    def trace(self,resource=None,token=None,**kwargs):
        return self.service.call(token or self.token,'lineage.trace',branch=self.branch,expected_head=self.head(),resource=resource or 'object/'+self.oid,**kwargs)

    def test_object_graph_pins_full_chain_without_values(self):
        self.prepare();graph=self.trace()
        self.assertTrue(graph['complete']);self.assertEqual(len(graph['nodes']),10);self.assertEqual(len(graph['edges']),9)
        self.assertEqual(len([n for n in graph['nodes'] if n['kind']=='dataset']),3)
        self.assertEqual({n['resource'] for n in graph['nodes'] if n['kind']=='pipeline'},{'pipeline/cleaning','pipeline/a-final'})
        self.assertNotIn('values',str(graph));self.assertNotIn('pending',str(graph))
        self.assertIn(graph['root'],{n['id'] for n in graph['nodes']})
        self.assertEqual(graph['semantics'],'recorded_provenance')

    def test_dataset_root_and_source_leaf(self):
        self.prepare()
        self.assertEqual(len(self.trace('dataset/final')['nodes']),7)
        leaf=self.trace('dataset/readings');self.assertEqual(len(leaf['nodes']),1);self.assertEqual(leaf['edges'],[])

    def test_read_only_catalog_and_deterministic_restart(self):
        self.prepare()
        def counts():
            with self.service.store.connect() as db:
                return tuple(db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('commits','revisions','pipeline_runs','orchestration_runs'))
        before=counts();head=self.head();graph=self.trace()
        self.service=Service(self.db)
        self.assertEqual(self.trace(),graph);self.assertEqual(counts(),before);self.assertEqual(self.head(),head)
        self.assertTrue(self.call('audit.verify')['valid'])

    def test_historical_pins_survive_new_source_snapshot(self):
        self.prepare();original=self.trace()
        old_source=next(n['revision'] for n in original['nodes'] if n['resource']=='dataset/readings')
        self.tx([{'values':{'id':'a','value':35}}],operation='UPDATE',key='new-source')
        graph=self.trace();source=next(n for n in graph['nodes'] if n['resource']=='dataset/readings')
        self.assertEqual(source['revision'],old_source);self.assertFalse(source['matches_branch_head'])

    def test_live_source_column_policy_blocks_whole_graph(self):
        self.prepare();low=self.member();self.trace(token=low['token'])
        self.call('policy.restrict',branch=self.branch,resource='dataset/readings/property/value',label={'level':2})
        with self.assertRaises(Forbidden):self.trace(token=low['token'])
        self.assertEqual(len(self.trace()['nodes']),10)

    def test_live_pipeline_policy_and_binding_policy(self):
        self.prepare();low=self.member()
        self.call('policy.restrict',branch=self.branch,resource='pipeline/cleaning',label={'level':2})
        with self.assertRaises(Forbidden):self.trace('dataset/final',token=low['token'])
        self.call('policy.restrict',branch=self.branch,resource='binding/assets',label={'level':2})
        with self.assertRaises(Forbidden):self.trace(token=low['token'])

    def test_hidden_raw_rows_fail_instead_of_partial_graph(self):
        self.dataset()
        self.tx([{'values':{'id':'a','value':1}}, {'values':{'id':'b','value':2},'marking':{'level':2}}])
        with self.assertRaises(Forbidden):self.trace('dataset/readings',token=self.member()['token'])

    def test_bounds_and_invalid_roots(self):
        self.prepare()
        with self.assertRaises(S2Error):self.trace(max_nodes=3)
        with self.assertRaises(S2Error):self.trace(max_depth=1)
        for kwargs in ({'max_nodes':True},{'max_nodes':201},{'max_depth':0},{'max_depth':33}):
            with self.assertRaises(S2Error):self.trace(**kwargs)
        with self.assertRaises(S2Error):self.trace('pipeline/cleaning')
        with self.assertRaises(NotFound):self.trace('dataset/missing')

    def test_roles_tenants_membership(self):
        self.prepare()
        for user,error in [(self.member('viewer',grant=False),Forbidden),(self.service.bootstrap('other',3,['ENG']),NotFound)]:
            with self.assertRaises(error):self.trace(token=user['token'])
        self.assertTrue(self.trace(token=self.member()['token'])['complete'])

    def test_stale_requested_head(self):
        self.prepare();head=self.head()
        self.tx([{'values':{'id':'a','value':35}}],operation='UPDATE',key='change')
        with self.assertRaises(Conflict):self.call('lineage.trace',branch=self.branch,expected_head=head,resource='object/'+self.oid)

    def test_operational_edit_keeps_materialization_lineage(self):
        self.prepare();before=self.trace()
        obj=self.call('objects.get',branch=self.branch,object_id=self.oid)
        self.call('objects.put',branch=self.branch,expected_head=self.head(),object_id=self.oid,object_type='Asset',
                  values={**obj['values'],'status':'inspected'},expected_revision=obj['revision'])
        after=self.trace();self.assertNotEqual(after['root'],before['root'])
        self.assertEqual({n['revision'] for n in before['nodes'] if n['kind']=='dataset'},
                         {n['revision'] for n in after['nodes'] if n['kind']=='dataset'})

    def test_compatible_binding_migration_traces_new_pins(self):
        self.prepare()
        schema={'properties':{**foundation.PUBLIC['properties'],'status':{'type':'string','required':True},'note':{'type':'string'}}}
        self.call('ontology.define',branch=self.branch,expected_head=self.head(),name='Asset',definition=schema)
        params=dict(branch=self.branch,expected_head=self.head(),binding='assets',mappings={'id':'id','value':'value'},
                    action_properties=['status','note'],initial_values={'status':'pending','note':'unreviewed'},reason='Add review field')
        plan=self.call('bindings.migration_preview',**params)
        result=self.call('bindings.migrate',**params,plan_hash=plan['plan_hash'],idempotency_key='migration')
        binding=next(n for n in self.trace()['nodes'] if n['kind']=='binding')
        self.assertEqual(binding['revision'],result['binding_revision'])

    def test_manual_object_has_schema_leaf(self):
        self.prepare()
        self.call('objects.put',branch=self.branch,expected_head=self.head(),object_id='manual',object_type='Asset',values={'id':'m','value':1,'status':'pending'})
        graph=self.trace('object/manual');self.assertEqual(len(graph['nodes']),2);self.assertEqual(len(graph['edges']),1)

    def test_fork_trace_and_merged_history(self):
        self.prepare();parent=self.branch
        self.branch=self.call('branches.fork',branch=parent,name='scenario')['branch']
        self.tx([{'values':{'id':'a','value':35}}],operation='UPDATE',key='change')
        self.chain_run(key='refresh')
        self.call('bindings.materialize',branch=self.branch,expected_head=self.head(),binding='assets',idempotency_key='refresh')
        scenario=self.trace();child=self.branch;self.branch=parent
        params=dict(branch=parent,source=child,expected_head=self.head(),expected_source=self.head(child),reason='Publish')
        plan=self.call('branches.merge_preview',**params)
        self.call('branches.merge_reviewed',**params,plan_hash=plan['plan_hash'],idempotency_key='merge')
        self.assertEqual(self.trace()['nodes'],scenario['nodes'])

    def test_cross_project_provenance_reference_rejected(self):
        self.prepare();other=self.call('projects.create',name='Other')['branch'];foreign=self.head(other)
        with self.service.store.transaction() as db:
            ctx=self.service.security.authenticate(db,self.token)
            head=self.service.store.branch(db,ctx.tenant,self.branch)['head']
            manifest=self.service.store.manifest(db,ctx.tenant,head)
            obj=self.service.store.revision(db,ctx.tenant,manifest['object/'+self.oid])
            obj['source_binding']['base_commit']=foreign
            manifest['object/'+self.oid]=self.service.store.put_revision(db,ctx.tenant,'object',obj)
            self.service.store.publish(db,ctx,self.branch,head,manifest)
        with self.assertRaises(Conflict):self.trace()


@unittest.skipUnless(HAS_PARQUET,'Parquet dependencies not installed')
class ParquetLineageTests(LineageTests):storage_backend='parquet'

@unittest.skipUnless(HAS_ICEBERG,'Iceberg dependencies not installed')
class IcebergLineageTests(LineageTests):storage_backend='iceberg'
