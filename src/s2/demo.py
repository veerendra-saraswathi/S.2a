# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Synthetic manufacturing example; no network or physical system effects."""

def run(service):
    admin = service.bootstrap('Synthetic manufacturing')
    token = admin['token']
    def call(op, **params):
        return service.call(token, op, **params)
    project = call('projects.create', name='Maintenance pilot')
    branch = project['branch']
    def head():
        return call('branches.head', branch=branch)['commit']
    schema = {'properties': {'asset_id': {'type': 'string', 'required': True},
                             'status': {'type': 'string', 'required': True}}}
    call('datasets.create', branch=branch, expected_head=head(), dataset='assets', schema=schema, primary_key='asset_id')
    transaction = call('datasets.begin', branch=branch, expected_head=head(), dataset='assets',
                       operation='APPEND', idempotency_key='demo-ingest')['transaction']
    call('datasets.stage', branch=branch, transaction=transaction,
         rows=[{'values': {'asset_id': 'pump-01', 'status': 'inspection-due'}}])
    call('datasets.commit', branch=branch, transaction=transaction)
    call('ontology.define', branch=branch, expected_head=head(), name='Asset', definition=schema)
    # Gate 1 explicitly materializes the object; automated source binding arrives in Gate 2.
    obj = call('objects.put', branch=branch, expected_head=head(), object_id='pump-01',
               object_type='Asset', values={'asset_id': 'pump-01', 'status': 'inspection-due'})
    call('actions.define', branch=branch, expected_head=head(), name='RecordInspection',
         object_type='Asset', writable_properties=['status'])
    invocation = call('actions.submit', branch=branch, expected_head=head(), action='RecordInspection',
                      edits=[{'object': 'pump-01', 'expected_revision': obj['revision'],
                              'set': {'status': 'inspected'}}], idempotency_key='demo-inspection')
    result = call('actions.execute', branch=branch, invocation=invocation['invocation'])
    fork = call('branches.fork', branch=branch, name='scenario/maintenance')
    return {'tenant': admin['tenant_id'], 'project': project['project'], 'branch': branch,
            'scenario_branch': fork['branch'], 'action': result,
            'object': call('objects.get', branch=branch, object_id='pump-01'),
            'audit': call('audit.verify'), 'external_effects': False}
