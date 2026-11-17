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


def run(storage):
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
        report=service.store.storage_report();assert not report['broken_references']
        return {'storage':storage,'ingestion_run':intake['run'],'pipeline':result,'replay_same_result':result==replay,
                'objects_materialized':len(objects),'object':objects[0],'audit':call('audit.verify'),
                'storage_report':report,'external_effects':False,'database':'temporary; removed after demonstration'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage',choices=['json','parquet','iceberg'],default='json')
    args=parser.parse_args();print(json.dumps(run(args.storage),indent=2))
