# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Synthetic CSV preview, rejection, import, replay and Ontology materialization."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'platform')]
sys.path[:0] = [str(p) for p in (ROOT/'subsystems').iterdir() if p.is_dir()]
from s2.service import Service


def run(storage):
    with tempfile.TemporaryDirectory(prefix='s2-ingestion-') as folder:
        service = Service(Path(folder)/'demo.db', storage_backend=storage)
        token = service.bootstrap('Synthetic laboratory', 3, ['ENG'])['token']
        def call(op, **params): return service.call(token, op, **params)
        branch = call('projects.create', name='Engineering intake')['branch']
        def head(): return call('branches.head', branch=branch)['commit']
        schema = {'properties': {'asset_id': {'type':'string','required':True},
                                 'reading': {'type':'integer','required':True}}}
        call('datasets.create', branch=branch, expected_head=head(), dataset='equipment', schema=schema, primary_key='asset_id')
        source = Path(folder)/'equipment.csv'
        source.write_text('asset_id,reading\npump-01,30\npump-02,25\n', encoding='utf-8')
        params = dict(branch=branch, expected_head=head(), dataset='equipment', format='csv',
                      source_name=source.name, content=source.read_bytes().decode('utf-8'))
        rejected = call('ingestion.preview', **{**params, 'content':'asset_id,reading\npump-01,invalid\n'})
        assert not rejected['can_publish'] and head() == params['expected_head']
        preview = call('ingestion.preview', **params)
        result = call('ingestion.apply', **params, plan_hash=preview['plan_hash'], idempotency_key='equipment-import')
        repeated = call('ingestion.apply', **params, plan_hash=preview['plan_hash'], idempotency_key='equipment-import')
        assert result == repeated
        assert call('ingestion.get', branch=branch, run=result['run']) == result
        target = {'properties': {**schema['properties'], 'status': {'type':'string','required':True}}}
        call('ontology.define', branch=branch, expected_head=head(), name='Asset', definition=target)
        call('bindings.define', branch=branch, expected_head=head(), binding='equipment-assets', dataset='equipment',
             object_type='Asset', namespace='lab', mappings={'asset_id':'asset_id','reading':'reading'},
             action_properties=['status'], initial_values={'status':'pending'})
        call('bindings.materialize', branch=branch, expected_head=head(), binding='equipment-assets', idempotency_key='build')
        objects = call('objects.list', branch=branch)['objects']
        assert len(objects) == 2
        report = service.store.storage_report()
        assert not report['broken_references']
        return {'storage':storage, 'rejected_preview':rejected['errors'], 'ingestion':result,
                'replay_same_result':result==repeated, 'objects_materialized':len(objects),
                'audit':call('audit.verify'), 'storage_report':report, 'external_effects':False,
                'database':'temporary; removed after demonstration'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage', choices=['json','parquet','iceberg'], default='json')
    args = parser.parse_args()
    print(json.dumps(run(args.storage), indent=2))
