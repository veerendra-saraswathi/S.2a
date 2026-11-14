# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Loopback-only development API. Not an internet-facing production gateway."""
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from s2_platform import S2Error


def server(service, port=8080):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'S2-Development/0.1'

        def log_message(self, format, *args):
            pass  # Do not log bearer credentials or sensitive request bodies.

        def send_json(self, status, data):
            body = json.dumps(data, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/health':
                self.send_json(200, {'status': 'ok', 'mode': 'gate1-development'})
            else:
                self.send_json(404, {'error': 'Not found'})

        def do_POST(self):
            if self.path != '/v1/operations':
                self.send_json(404, {'error': 'Not found'})
                return
            if self.headers.get('Origin'):
                self.send_json(403, {'error': 'Browser cross-origin access disabled'})
                return
            credential = self.headers.get('Authorization', '')
            if not credential.startswith('Bearer '):
                self.send_json(401, {'error': 'Bearer credential required'})
                return
            try:
                if self.headers.get('Transfer-Encoding'):
                    raise ValueError('Unsupported encoding')
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1024 * 1024:
                    self.send_json(413, {'error': 'Request must contain 1 byte to 1 MiB'})
                    return
                self.connection.settimeout(5)
                def reject_constant(value):
                    raise ValueError('Nonfinite JSON number')
                request = json.loads(self.rfile.read(size), parse_constant=reject_constant)
                if (not isinstance(request, dict) or set(request) != {'operation', 'params'} or
                        not isinstance(request['operation'], str) or not isinstance(request['params'], dict)):
                    raise ValueError('Invalid envelope')
                result = service.call(credential[7:], request['operation'], **request['params'])
                self.send_json(200, result)
            except S2Error as exc:
                self.send_json(exc.status, {'error': str(exc)})
            except (ValueError, TypeError, KeyError, OverflowError):
                self.send_json(400, {'error': 'Invalid request'})
            except sqlite3.OperationalError:
                self.send_json(503, {'error': 'Storage unavailable; retry with the same idempotency key'})
            except TimeoutError:
                self.send_json(408, {'error': 'Request timed out'})
            except Exception:
                self.send_json(500, {'error': 'Internal error'})

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)
