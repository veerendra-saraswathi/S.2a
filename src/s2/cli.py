# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
import argparse
import json
import os
from pathlib import Path
from s2.service import Service
from s2_platform import S2Error


def main():
    parser = argparse.ArgumentParser(description='Samanvaya 2 Gate 1 development core')
    parser.add_argument('--db', default='.local/s2.db')
    parser.add_argument('--storage', choices=['json', 'parquet', 'iceberg'], default=None)
    parser.add_argument('--warehouse', default=None)
    commands = parser.add_subparsers(dest='command', required=True)
    bootstrap = commands.add_parser('bootstrap')
    bootstrap.add_argument('--tenant', required=True)
    bootstrap.add_argument('--clearance', type=int, default=0)
    bootstrap.add_argument('--compartment', action='append', default=[])
    serve = commands.add_parser('serve')
    serve.add_argument('--port', type=int, default=8080)
    commands.add_parser('demo')
    commands.add_parser('storage-report')
    for name in ('ingest-preview', 'ingest-apply'):
        ingest = commands.add_parser(name)
        ingest.add_argument('--file', required=True)
        ingest.add_argument('--format', choices=['csv', 'json'], required=True)
        ingest.add_argument('--branch', required=True)
        ingest.add_argument('--expected-head', required=True)
        ingest.add_argument('--dataset', required=True)
        ingest.add_argument('--marking', default='{}', help='JSON classification label')
        if name == 'ingest-apply':
            ingest.add_argument('--plan-hash', required=True)
            ingest.add_argument('--key', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    Path(args.db).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    service = Service(args.db, storage_backend=args.storage, warehouse=args.warehouse)
    if args.command.startswith('ingest-'):
        token = os.environ.get('S2_TOKEN')
        if not token:
            parser.error('Set S2_TOKEN to an authorized token; it is not accepted on the command line')
        try:
            with Path(args.file).open('rb') as file:
                raw = file.read(1048577)
            if len(raw) > 1048576:
                raise S2Error('File exceeds 1 MiB')
            params = dict(branch=args.branch, expected_head=args.expected_head, dataset=args.dataset,
                          content=raw.decode('utf-8'), format=args.format, source_name=Path(args.file).name,
                          label=json.loads(args.marking))
            operation = 'ingestion.preview'
            if args.command == 'ingest-apply':
                operation = 'ingestion.apply'
                params.update(plan_hash=args.plan_hash, idempotency_key=args.key)
            result = service.call(token, operation, **params)
        except (OSError, UnicodeError, ValueError, S2Error) as exc:
            parser.exit(2, f'Ingestion failed: {type(exc).__name__}\n')
        print(json.dumps(result, indent=2))
        if not result['can_publish']:
            raise SystemExit(1)
    elif args.command == 'bootstrap':
        result = service.bootstrap(args.tenant, args.clearance, args.compartment)
        print(json.dumps(result, indent=2))
    elif args.command == 'storage-report':
        result = service.store.storage_report()
        print(json.dumps(result, indent=2))
        if result['broken_references']:
            raise SystemExit(1)
    elif args.command == 'serve':
        from s2.http import server
        httpd = server(service, args.port)
        print(f'S2 development API: http://127.0.0.1:{httpd.server_port}', flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
    else:
        from s2.demo import run
        print(json.dumps(run(service), indent=2))
