# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Synthetic ingest -> reviewed transform -> Ontology demonstration."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'platform')]
sys.path[:0]=[str(p) for p in (ROOT/'subsystems').iterdir() if p.is_dir()]
from s2.service import Service


def run(storage, merge=False):
    with tempfile.TemporaryDirectory(prefix='s2-pipeline-') as folder:
        service=Service(Path(folder)/'demo.db',storage_backend=storage)
        token=service.bootstrap('Synthetic laboratory',3,['ENG'])['token']
        def call(op,**params):return service.call(token,op,**params)
        branch=call('projects.create',name='Laboratory processing')['branch']
        def head():return call('branches.head',branch=branch)['commit']
        schema={'properties':{'asset_id':{'type':'string','required':True},'reading':{'type':'integer','required':True},
                              'accepted':{'type':'boolean','required':True}}}
        target={'properties':{k:v for k,v in schema['properties'].items() if k!='accepted'}}
        for name,definition in [('raw',schema),('clean',target)]:
            call('datasets.create',branch=branch,expected_head=head(),dataset=name,schema=definition,primary_key='asset_id')
        params=dict(branch=branch,expected_head=head(),dataset='raw',format='csv',source_name='laboratory.csv',
                    content='asset_id,reading,accepted\npump-01,30,true\npump-02,25,false\n')
        preview=call('ingestion.preview',**params)
        intake=call('ingestion.apply',**params,plan_hash=preview['plan_hash'],idempotency_key='intake')
        definition={'input':'raw','output':'clean','mappings':{'asset_id':'asset_id','reading':'reading'},
                    'filter':{'property':'accepted','equals':True},
                    'expectations':{'min_rows':1,'max_rows':1000,'ranges':{'reading':[0,100]}}}
        call('pipelines.define',branch=branch,expected_head=head(),pipeline='accepted-readings',definition=definition)
        params=dict(branch=branch,expected_head=head(),pipeline='accepted-readings')
        plan=call('pipelines.preview',**params)
        result=call('pipelines.run',**params,plan_hash=plan['plan_hash'],idempotency_key='transform')
        replay=call('pipelines.run',**params,plan_hash=plan['plan_hash'],idempotency_key='transform')
        assert result==replay==call('pipelines.get',branch=branch,run=result['run'])
        call('ontology.define',branch=branch,expected_head=head(),name='Asset',
             definition={'properties':{**target['properties'],'status':{'type':'string','required':True}}})
        call('bindings.define',branch=branch,expected_head=head(),binding='assets',dataset='clean',object_type='Asset',
             namespace='lab',mappings={'asset_id':'asset_id','reading':'reading'},action_properties=['status'],initial_values={'status':'pending'})
        call('bindings.materialize',branch=branch,expected_head=head(),binding='assets',idempotency_key='build')
        objects=call('objects.list',branch=branch)['objects'];assert len(objects)==1
        merge_result=None
        if merge:
            oid=objects[0]['id']
            call('actions.define',branch=branch,expected_head=head(),name='Inspect',object_type='Asset',
                 writable_properties=['status'],approvals=1)
            action=call('actions.submit',branch=branch,expected_head=head(),action='Inspect',
                        edits=[{'object':oid,'expected_revision':objects[0]['revision'],'set':{'status':'inspected'}}],
                        idempotency_key='inspection')
            reviewer=call('principals.create',role='admin',clearance=3,compartments=['ENG'])
            service.call(reviewer['token'],'actions.approve',branch=branch,invocation=action['invocation'],effect_hash=action['effect_hash'])
            call('actions.execute',branch=branch,invocation=action['invocation'])
            parent=branch
            branch=call('branches.fork',branch=branch,name='pipeline-scenario')['branch']
            tx=call('datasets.begin',branch=branch,expected_head=head(),dataset='raw',operation='UPDATE',idempotency_key='refresh')['transaction']
            call('datasets.stage',branch=branch,transaction=tx,rows=[{'values':{'asset_id':'pump-01','reading':40,'accepted':True}}])
            call('datasets.commit',branch=branch,transaction=tx)
            params=dict(branch=branch,expected_head=head(),pipeline='accepted-readings')
            plan=call('pipelines.preview',**params)
            scenario_run=call('pipelines.run',**params,plan_hash=plan['plan_hash'],idempotency_key='scenario-transform')
            call('bindings.materialize',branch=branch,expected_head=head(),binding='assets',idempotency_key='scenario-build')
            child=branch;source_head=head();branch=parent
            params=dict(branch=branch,source=child,expected_head=head(),expected_source=source_head,
                        reason='Publish reviewed laboratory pipeline refresh')
            plan=call('branches.merge_preview',**params)
            published=call('branches.merge_reviewed',**params,plan_hash=plan['plan_hash'],idempotency_key='scenario-merge')
            repeated=call('branches.merge_reviewed',**params,plan_hash=plan['plan_hash'],idempotency_key='scenario-merge')
            assert published==repeated
            assert call('branches.head',branch=child)['commit']==source_head
            objects=call('objects.list',branch=branch)['objects']
            assert objects[0]['id']==oid and objects[0]['values']['reading']==40
            assert objects[0]['values']['status']=='inspected'
            merge_result={'replay_same_result':True,'source_unchanged':True,'same_object_id':True,
                          'approved_status_preserved':True,'pipelines':plan['pipelines'],
                          'scenario_run':scenario_run['run'],'changed_resources':published['changed_resources']}
        report=service.store.storage_report();assert not report['broken_references']
        return {'storage':storage,'ingestion_run':intake['run'],'pipeline':result,'replay_same_result':result==replay,
                'objects_materialized':len(objects),'object':objects[0],'merge':merge_result,'audit':call('audit.verify'),
                'storage_report':report,'external_effects':False,'database':'temporary; removed after demonstration'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage',choices=['json','parquet','iceberg'],default='json')
    parser.add_argument('--merge',action='store_true',help='Also refresh and merge a reviewed pipeline scenario')
    args=parser.parse_args();print(json.dumps(run(args.storage,args.merge),indent=2))
