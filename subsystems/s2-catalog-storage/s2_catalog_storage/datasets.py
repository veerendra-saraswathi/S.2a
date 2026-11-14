# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
import json
from s2_platform import S2Error, Conflict, Forbidden, NotFound, canonical, digest, new_id, marking, dominates


class Datasets:
    def __init__(self, store, security, kernel):
        self.store, self.security, self.kernel = store, security, kernel

    def create(self, db, ctx, branch, expected_head, dataset, schema, primary_key):
        self.kernel.validate_definition(schema)
        self.kernel.check_definition_access(ctx, schema)
        if primary_key not in schema['properties'] or not schema['properties'][primary_key].get('required'):
            raise S2Error('Primary key must be a required property')
        if schema['properties'][primary_key]['type'] not in {'string', 'integer'}:
            raise S2Error('Primary key must be string or integer')
        manifest = self.store.manifest(db, ctx.tenant, expected_head)
        key = 'dataset/' + dataset
        self.security.check_marking(ctx, self.security.effective(db, ctx, key, schema.get('marking')))
        for prop, spec in schema['properties'].items():
            self.security.check_marking(ctx, self.security.effective(db, ctx, key + '/property/' + prop, spec.get('marking')))
        if key in manifest:
            raise Conflict('Dataset already exists')
        payload = {'schema': schema, 'primary_key': primary_key, 'rows': [], 'parent': None}
        manifest[key] = self.store.put_revision(db, ctx.tenant, 'dataset_snapshot', payload)
        head = self.store.publish(db, ctx, branch, expected_head, manifest)
        return {'commit': head, 'snapshot': manifest[key]}

    def begin(self, db, ctx, branch, expected_head, dataset, operation, idempotency_key):
        if operation not in {'APPEND', 'UPDATE', 'DELETE'} or not idempotency_key:
            raise S2Error('Invalid operation or idempotency key')
        request = digest({'base': expected_head, 'dataset': dataset, 'operation': operation, 'actor': ctx.principal})
        existing = db.execute('SELECT * FROM dataset_transactions WHERE tenant=? AND branch=? AND idem=?',
                              (ctx.tenant, branch, idempotency_key)).fetchone()
        if existing:
            if existing['request_hash'] != request:
                raise Conflict('Idempotency key reused for a different request')
            return {'transaction': existing['id'], 'status': existing['status']}
        if self.store.branch(db, ctx.tenant, branch)['head'] != expected_head:
            raise Conflict('Stale branch head')
        manifest = self.store.manifest(db, ctx.tenant, expected_head)
        snapshot = self.kernel.resource(db, ctx, manifest, 'dataset/' + dataset)
        self.kernel.check_definition_access(ctx, snapshot['schema'])
        tx = new_id()
        db.execute('INSERT INTO dataset_transactions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (tx, ctx.tenant, ctx.principal, branch, dataset, expected_head, operation,
                    '[]', 'OPEN', None, idempotency_key, request))
        return {'transaction': tx, 'status': 'OPEN'}

    def _transaction(self, db, ctx, branch, transaction):
        row = db.execute('SELECT * FROM dataset_transactions WHERE id=? AND tenant=? AND branch=? AND actor=?',
                         (transaction, ctx.tenant, branch, ctx.principal)).fetchone()
        if row is None:
            raise NotFound('Transaction not found')
        return row

    def stage(self, db, ctx, branch, transaction, rows):
        tx = self._transaction(db, ctx, branch, transaction)
        if not isinstance(rows, list) or len(rows) > 10000:
            raise S2Error('rows must be a list of at most 10000 entries')
        if tx['status'] == 'PREPARED' and tx['payload'] == canonical(rows):
            return {'transaction': transaction, 'status': 'PREPARED'}
        if tx['status'] != 'OPEN':
            raise Conflict('Transaction is not open')
        # Validate the full prospective state before declaring it prepared.
        self._apply(db, ctx, tx, rows)
        db.execute('UPDATE dataset_transactions SET payload=?,status=? WHERE id=?',
                   (canonical(rows), 'PREPARED', transaction))
        return {'transaction': transaction, 'status': 'PREPARED'}

    def _apply(self, db, ctx, tx, rows):
        manifest = self.store.manifest(db, ctx.tenant, tx['base'])
        old = self.kernel.resource(db, ctx, manifest, 'dataset/' + tx['resource'])
        self.kernel.check_definition_access(ctx, old['schema'])
        pk = old['primary_key']
        indexed = {canonical(row['values'][pk]): row for row in old['rows']}
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('values'), dict) or pk not in row['values']:
                raise S2Error('Each row requires values containing the primary key')
            key = canonical(row['values'][pk])
            if key in seen:
                raise Conflict('Duplicate key in mutation batch')
            seen.add(key)
            previous = indexed.get(key)
            if previous:
                self.security.check_marking(ctx, previous['marking'])
            if tx['operation'] == 'DELETE':
                if set(row['values']) != {pk} or previous is None:
                    raise Conflict('DELETE requires an existing key only')
                del indexed[key]
                continue
            self.kernel.validate_values(old['schema'], row['values'])
            label = self.security.effective(db, ctx, 'dataset/' + tx['resource'] + '/row/' + key, row.get('marking'))
            self.security.check_marking(ctx, label)
            if tx['operation'] == 'APPEND' and previous:
                raise Conflict('APPEND cannot overwrite an existing key')
            if tx['operation'] == 'UPDATE' and previous is None:
                raise Conflict('UPDATE requires an existing key')
            if previous and not dominates(label, previous['marking']):
                raise Forbidden('Row marking downgrade denied')
            indexed[key] = {'values': row['values'], 'marking': label}
        result = {**old, 'rows': [indexed[k] for k in sorted(indexed)],
                  'parent': manifest['dataset/' + tx['resource']]}
        return manifest, result

    def commit(self, db, ctx, branch, transaction):
        tx = self._transaction(db, ctx, branch, transaction)
        if tx['status'] == 'COMMITTED':
            return json.loads(tx['result'])
        if tx['status'] != 'PREPARED':
            raise Conflict('Transaction must be prepared before COMMIT')
        manifest, snapshot = self._apply(db, ctx, tx, json.loads(tx['payload']))
        revision = self.store.put_revision(db, ctx.tenant, 'dataset_snapshot', snapshot)
        manifest['dataset/' + tx['resource']] = revision
        commit = self.store.publish(db, ctx, branch, tx['base'], manifest)
        result = {'commit': commit, 'snapshot': revision, 'status': 'COMMITTED'}
        db.execute('UPDATE dataset_transactions SET status=?,result=? WHERE id=?',
                   ('COMMITTED', canonical(result), transaction))
        return result

    def abort(self, db, ctx, branch, transaction):
        tx = self._transaction(db, ctx, branch, transaction)
        if tx['status'] == 'COMMITTED':
            raise Conflict('A committed transaction cannot be aborted')
        db.execute('UPDATE dataset_transactions SET status=? WHERE id=?', ('ABORTED', transaction))
        return {'transaction': transaction, 'status': 'ABORTED'}

    def read(self, db, ctx, branch, dataset):
        head = self.store.branch(db, ctx.tenant, branch)['head']
        manifest = self.store.manifest(db, ctx.tenant, head)
        snapshot = self.kernel.resource(db, ctx, manifest, 'dataset/' + dataset)
        self.security.check_marking(ctx, snapshot['schema'].get('marking'))
        props = snapshot['schema']['properties']
        rows = [{name: value for name, value in row['values'].items()
                 if self.security.allowed(ctx, props[name].get('marking'))}
                for row in snapshot['rows'] if self.security.allowed(ctx, row['marking'])]
        return {'commit': head, 'snapshot': manifest['dataset/' + dataset], 'rows': rows}
