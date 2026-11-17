# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Synthetic binding/Action/refresh demonstration in a disposable database."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'platform')]
sys.path[:0] = [str(p) for p in (ROOT / 'subsystems').iterdir() if p.is_dir()]
from s2.service import Service


def run(backend):
    with tempfile.TemporaryDirectory(prefix='s2-binding-demo-') as folder:
        service = Service(Path(folder) / 'demo.db', storage_backend=backend)
        token = service.bootstrap('Synthetic engineering demonstration', 3, ['ENG'])['token']
        def call(op, **params):
            return service.call(token, op, **params)
        branch = call('projects.create', name='Laboratory asset maintenance')['branch']
        def head():
            return call('branches.head', branch=branch)['commit']
        source = {'properties': {'asset_id': {'type': 'string', 'required': True},
                                 'reading': {'type': 'integer', 'required': True}}}
        target = {'properties': {**source['properties'], 'status': {'type': 'string', 'required': True}}}
        call('datasets.create', branch=branch, expected_head=head(), dataset='equipment',
             schema=source, primary_key='asset_id')
        def ingest(operation, reading, idem):
            tx = call('datasets.begin', branch=branch, expected_head=head(), dataset='equipment',
                      operation=operation, idempotency_key=idem)['transaction']
            call('datasets.stage', branch=branch, transaction=tx,
                 rows=[{'values': {'asset_id': 'pump-01', 'reading': reading}}])
            call('datasets.commit', branch=branch, transaction=tx)
        ingest('APPEND', 10, 'initial')
        call('ontology.define', branch=branch, expected_head=head(), name='Asset', definition=target)
        call('bindings.define', branch=branch, expected_head=head(), binding='equipment-assets',
             dataset='equipment', object_type='Asset', namespace='synthetic-lab',
             mappings={'asset_id': 'asset_id', 'reading': 'reading'},
             action_properties=['status'], initial_values={'status': 'pending'})
        base = head()
        build = call('bindings.materialize', branch=branch, expected_head=base,
                     binding='equipment-assets', idempotency_key='initial-build')
        retry = call('bindings.materialize', branch=branch, expected_head=base,
                     binding='equipment-assets', idempotency_key='initial-build')
        oid = next(iter(build['objects']))
        obj = call('objects.get', branch=branch, object_id=oid)
        call('actions.define', branch=branch, expected_head=head(), name='RecordInspection',
             object_type='Asset', writable_properties=['status'], approvals=1)
        invocation = call('actions.submit', branch=branch, expected_head=head(), action='RecordInspection',
                          edits=[{'object': oid, 'expected_revision': obj['revision'], 'set': {'status': 'inspected'}}],
                          idempotency_key='inspection')
        reviewer = call('principals.create', role='admin', clearance=3, compartments=['ENG'])
        service.call(reviewer['token'], 'actions.approve', branch=branch,
                     invocation=invocation['invocation'], effect_hash=invocation['effect_hash'])
        call('actions.execute', branch=branch, invocation=invocation['invocation'])
        ingest('UPDATE', 25, 'source-refresh')
        call('bindings.materialize', branch=branch, expected_head=head(),
             binding='equipment-assets', idempotency_key='refresh-build')
        final = call('objects.get', branch=branch, object_id=oid)
        assert final['values'] == {'asset_id': 'pump-01', 'reading': 25, 'status': 'inspected'}
        assert retry == build
        return {'storage': backend, 'object': final, 'replay_same_result': retry == build,
                'approved_status_preserved': True,
                'provenance': call('bindings.provenance', branch=branch, object_id=oid),
                'audit': call('audit.verify'), 'external_effects': False,
                'database': 'temporary; removed after demonstration'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage', choices=['json', 'parquet', 'iceberg'], default='json')
    print(json.dumps(run(parser.parse_args().storage), indent=2))
