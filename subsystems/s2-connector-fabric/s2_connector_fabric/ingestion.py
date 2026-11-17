# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Bounded UTF-8 CSV/JSON ingestion; explicit schemas and atomic APPEND only."""
import csv
import hashlib
import io
import json
import math
import re
from s2_platform import S2Error, Conflict, NotFound, canonical, digest, marking, new_id, now


class Ingestion:
    def __init__(self, store, security, kernel, datasets):
        self.store, self.security, self.kernel, self.datasets = store, security, kernel, datasets

    def _access(self, db, ctx, commit, dataset):
        manifest = self.store.manifest(db, ctx.tenant, commit)
        snapshot = self.kernel.resource(db, ctx, manifest, 'dataset/' + dataset)
        self.kernel.check_definition_access(ctx, snapshot['schema'])
        for row in snapshot['rows']:
            self.security.check_marking(ctx, row['marking'])
        return snapshot

    def _parse(self, content, format):
        if not isinstance(content, str) or len(content) > 1048576:
            raise S2Error('Content must be UTF-8 text of at most 1 MiB')
        try:
            raw = content.encode('utf-8')
        except UnicodeError as exc:
            raise S2Error('Invalid UTF-8 text') from exc
        if len(raw) > 1048576:
            raise S2Error('Content exceeds 1 MiB')
        try:
            if format == 'json':
                def pairs(items):
                    result = {}
                    for key, value in items:
                        if key in result: raise ValueError('Duplicate JSON property')
                        result[key] = value
                    return result
                def invalid(value): raise ValueError('Non-finite JSON')
                rows = json.loads(content, object_pairs_hook=pairs, parse_constant=invalid)
                if not isinstance(rows, list): raise ValueError('Array required')
            elif format == 'csv':
                reader = csv.reader(io.StringIO(content, newline=''), strict=True)
                header = next(reader)
                if not header or len(set(header)) != len(header) or any(not x for x in header):
                    raise ValueError('Invalid CSV header')
                rows = []
                for values in reader:
                    if len(values) != len(header): raise ValueError('CSV width mismatch')
                    rows.append(dict(zip(header, values)))
                    if len(rows) > 1000: raise ValueError('Row limit')
            else:
                raise S2Error('Format must be csv or json')
        except (ValueError, TypeError, RecursionError, StopIteration, csv.Error) as exc:
            raise S2Error('Malformed or oversized input; require a JSON object array or rectangular CSV') from exc
        if not 1 <= len(rows) <= 1000:
            raise S2Error('Require 1 to 1000 data records')
        return rows, hashlib.sha256(raw).hexdigest(), len(raw)

    def _values(self, row, schema, format):
        if not isinstance(row, dict): raise S2Error('Invalid record')
        result = dict(row)
        if format == 'csv':
            for name, value in list(result.items()):
                spec = schema['properties'].get(name)
                if spec is None: raise S2Error('Unknown property')
                if value == '' and not spec.get('required'):
                    del result[name]
                    continue
                kind = spec['type']
                if kind == 'integer':
                    if not re.fullmatch(r'-?(0|[1-9][0-9]*)', value): raise S2Error('Invalid integer')
                    result[name] = int(value)
                elif kind == 'number':
                    result[name] = float(value)
                    if not math.isfinite(result[name]): raise S2Error('Non-finite number')
                elif kind == 'boolean':
                    if value not in ('true', 'false'): raise S2Error('Invalid boolean')
                    result[name] = value == 'true'
        self.kernel.validate_values(schema, result)
        return result

    def _plan(self, db, ctx, branch, expected_head, dataset, content, format, source_name, label=None):
        if not isinstance(source_name, str) or not 1 <= len(source_name.strip()) <= 200:
            raise S2Error('Source name requires 1 to 200 characters')
        if self.store.branch(db, ctx.tenant, branch)['head'] != expected_head:
            raise Conflict('Stale branch head')
        snapshot = self._access(db, ctx, expected_head, dataset)
        label = marking(label)
        self.security.check_marking(ctx, label)
        records, checksum, size = self._parse(content, format)
        existing = {canonical(r['values'][snapshot['primary_key']]) for r in snapshot['rows']}
        seen, errors, rows = set(), [], []
        for index, record in enumerate(records, 1):
            try:
                values = self._values(record, snapshot['schema'], format)
            except (S2Error, ValueError, OverflowError):
                errors.append({'record': index, 'code': 'SCHEMA_VALIDATION'})
                continue
            key = canonical(values[snapshot['primary_key']])
            effective = self.security.effective(db, ctx, 'dataset/' + dataset + '/row/' + key, label)
            # Authorization is an operation failure, never a row-rejection hint.
            self.security.check_marking(ctx, effective)
            if key in seen or key in existing:
                errors.append({'record': index, 'code': 'DUPLICATE_KEY'})
            else:
                rows.append({'values': values, 'marking': effective})
            seen.add(key)
        plan = {'branch': branch, 'base_commit': expected_head, 'dataset': dataset,
                'dataset_revision': self.store.manifest(db, ctx.tenant, expected_head)['dataset/' + dataset],
                'source_name': source_name.strip(), 'format': format, 'sha256': checksum,
                'bytes': size, 'records': len(records), 'valid_records': len(rows),
                'rejected_records': len(errors), 'errors': errors, 'can_publish': not errors,
                'operation': 'APPEND'}
        plan['plan_hash'] = digest({'plan': plan, 'actor': ctx.principal, 'policy_snapshot': snapshot, 'rows': rows})
        return plan, rows

    def preview(self, db, ctx, branch, expected_head, dataset, content, format, source_name, label=None):
        return self._plan(db, ctx, branch, expected_head, dataset, content, format, source_name, label)[0]

    def apply(self, db, ctx, branch, expected_head, dataset, content, format, source_name,
              plan_hash, idempotency_key, label=None):
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise S2Error('Bounded idempotency key required')
        # Parse/bound before hashing or replay lookup, but disclose no dataset facts here.
        _, checksum, _ = self._parse(content, format)
        request = digest({'actor': ctx.principal, 'base': expected_head, 'dataset': dataset,
                          'sha256': checksum, 'format': format, 'source': source_name,
                          'label': marking(label), 'plan_hash': plan_hash})
        replay = db.execute('SELECT * FROM ingestion_runs WHERE tenant=? AND branch=? AND idem=?',
                            (ctx.tenant, branch, idempotency_key)).fetchone()
        if replay:
            if replay['request_hash'] != request: raise Conflict('Idempotency key reused')
            result = json.loads(replay['result'])
            self._access(db, ctx, result['commit'], dataset)
            return result
        plan, rows = self._plan(db, ctx, branch, expected_head, dataset, content, format, source_name, label)
        if plan_hash != plan['plan_hash']: raise Conflict('Input, schema or policy changed; preview again')
        if not plan['can_publish']: raise Conflict('Rejected records must be corrected before publication')
        run = new_id()
        tx = self.datasets.begin(db, ctx, branch, expected_head, dataset, 'APPEND', 'ingestion:' + run)['transaction']
        self.datasets.stage(db, ctx, branch, tx, rows)
        committed = self.datasets.commit(db, ctx, branch, tx)
        result = {**plan, **committed, 'run': run, 'transaction': tx, 'actor': ctx.principal, 'created_at': now()}
        db.execute('INSERT INTO ingestion_runs VALUES(?,?,?,?,?,?,?)',
                   (ctx.tenant, branch, run, ctx.principal, idempotency_key, request, canonical(result)))
        return result

    def get(self, db, ctx, branch, run):
        row = db.execute('SELECT result FROM ingestion_runs WHERE tenant=? AND branch=? AND id=? AND actor=?',
                         (ctx.tenant, branch, run, ctx.principal)).fetchone()
        if row is None: raise NotFound('Ingestion run not found')
        result = json.loads(row['result'])
        self._access(db, ctx, result['commit'], result['dataset'])
        return result
