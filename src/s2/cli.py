# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
import argparse
import json
import os
from pathlib import Path
from s2.service import Service


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
    args = parser.parse_args()
    os.umask(0o077)
    Path(args.db).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    service = Service(args.db, storage_backend=args.storage, warehouse=args.warehouse)
    if args.command == 'bootstrap':
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
