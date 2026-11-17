# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Synthetic two-stage chain with quality rejection and explicit object build."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'),str(ROOT/'platform')]
sys.path[:0] = [str(p) for p in (ROOT/'subsystems').iterdir() if p.is_dir()]
from s2.service import Service


def run(storage):
    with tempfile.TemporaryDirectory(prefix='s2-chain-') as folder:
        service=Service(Path(folder)/'demo.db',storage_backend=storage)
        token=service.bootstrap('Synthetic laboratory',3,['ENG'])['token']
        def call(op,**params):return service.call(token,op,**params)
        branch=call('projects.create',name='Laboratory pipeline chain')['branch']
        def head():return call('branches.head',branch=branch)['commit']
        schema={'properties':{'asset_id':{'type':'string','required':True},'reading':{'type':'integer','required':True},
                              'accepted':{'type':'boolean','required':True}}}
        projected={'properties':{k:v for k,v in schema['properties'].items() if k!='accepted'}}
        for name,definition in [('raw',schema),('selected',projected),('validated',projected)]:
            call('datasets.create',branch=branch,expected_head=head(),dataset=name,schema=definition,primary_key='asset_id')
        intake=dict(branch=branch,expected_head=head(),dataset='raw',format='csv',source_name='laboratory.csv',
                    content='asset_id,reading,accepted\npump-01,130,true\npump-02,25,false\n')
        plan=call('ingestion.preview',**intake)
        call('ingestion.apply',**intake,plan_hash=plan['plan_hash'],idempotency_key='intake')
        definition={'input':'raw','output':'selected','mappings':{'asset_id':'asset_id','reading':'reading'},
                    'filter':{'property':'accepted','equals':True},
                    'expectations':{'min_rows':1,'max_rows':1000,'ranges':{'reading':[0,1000]}}}
        call('pipelines.define',branch=branch,expected_head=head(),pipeline='z-select',definition=definition)
        call('pipelines.define',branch=branch,expected_head=head(),pipeline='a-validate',
             definition={**definition,'input':'selected','output':'validated','filter':None,
                         'expectations':{'min_rows':1,'max_rows':1000,'ranges':{'reading':[0,100]}}})
        old=head()
        rejected=call('orchestration.preview',branch=branch,expected_head=old,targets=['a-validate'])
        assert not rejected['can_publish'] and head()==old
        assert call('datasets.read',branch=branch,dataset='selected')['rows']==[]
        tx=call('datasets.begin',branch=branch,expected_head=head(),dataset='raw',operation='UPDATE',idempotency_key='correct-reading')['transaction']
        call('datasets.stage',branch=branch,transaction=tx,rows=[{'values':{'asset_id':'pump-01','reading':30,'accepted':True}}])
        call('datasets.commit',branch=branch,transaction=tx)
        params=dict(branch=branch,expected_head=head(),targets=['a-validate'])
        plan=call('orchestration.preview',**params)
        result=call('orchestration.run',**params,plan_hash=plan['plan_hash'],idempotency_key='chain')
        repeated=call('orchestration.run',**params,plan_hash=plan['plan_hash'],idempotency_key='chain')
        assert result==repeated==call('orchestration.get',branch=branch,run=result['run'])
        assert result['order']==['z-select','a-validate']
        assert result['stages'][1]['input_revision']==result['stages'][0]['snapshot']
        call('ontology.define',branch=branch,expected_head=head(),name='Asset',
             definition={'properties':{**projected['properties'],'status':{'type':'string','required':True}}})
        call('bindings.define',branch=branch,expected_head=head(),binding='assets',dataset='validated',object_type='Asset',namespace='lab',
             mappings={'asset_id':'asset_id','reading':'reading'},action_properties=['status'],initial_values={'status':'pending'})
        call('bindings.materialize',branch=branch,expected_head=head(),binding='assets',idempotency_key='build')
        objects=call('objects.list',branch=branch)['objects'];assert len(objects)==1
        report=service.store.storage_report();assert not report['broken_references']
        return {'storage':storage,'rejected_stage_statuses':[s['status'] for s in rejected['stages']],
                'rejected_preview_left_outputs_unchanged':True,'chain':result,'replay_same_result':result==repeated,
                'downstream_input_pinned_to_upstream_output':True,'objects_materialized':len(objects),'object':objects[0],
                'audit':call('audit.verify'),'storage_report':report,'external_effects':False,
                'database':'temporary; removed after demonstration'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage',choices=['json','parquet','iceberg'],default='json')
    args=parser.parse_args();print(json.dumps(run(args.storage),indent=2))
