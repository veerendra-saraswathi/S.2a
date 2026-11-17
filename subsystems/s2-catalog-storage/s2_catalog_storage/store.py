# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""SQLite reference catalog: immutable revisions and atomic manifest publication."""
import json
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from s2_platform import Conflict, NotFound, digest, new_id, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT INTO schema_version SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM schema_version);
CREATE TABLE IF NOT EXISTS storage_config(id INTEGER PRIMARY KEY CHECK(id=1), backend TEXT NOT NULL, warehouse TEXT);
CREATE TABLE IF NOT EXISTS tenants(id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS principals(
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL REFERENCES tenants(id), role TEXT NOT NULL,
 clearance INTEGER NOT NULL, compartments TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS tokens(
 hash TEXT PRIMARY KEY, principal TEXT NOT NULL REFERENCES principals(id));
CREATE TABLE IF NOT EXISTS projects(
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL REFERENCES tenants(id), name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS commits(
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL, project TEXT NOT NULL,
 parents TEXT NOT NULL, manifest TEXT NOT NULL, author TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS branches(
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL, project TEXT NOT NULL REFERENCES projects(id),
 name TEXT NOT NULL, base TEXT NOT NULL REFERENCES commits(id),
 head TEXT NOT NULL REFERENCES commits(id), UNIQUE(tenant,project,name));
CREATE TABLE IF NOT EXISTS revisions(
 id TEXT NOT NULL, tenant TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(tenant,id));
CREATE TABLE IF NOT EXISTS dataset_transactions(
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL, branch TEXT NOT NULL,
 resource TEXT NOT NULL, base TEXT NOT NULL, operation TEXT NOT NULL,
 payload TEXT NOT NULL, status TEXT NOT NULL, result TEXT, idem TEXT NOT NULL,
 request_hash TEXT NOT NULL, UNIQUE(tenant,branch,idem));
CREATE TABLE IF NOT EXISTS invocations(
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL, branch TEXT NOT NULL,
 base TEXT NOT NULL, action_revision TEXT NOT NULL, payload TEXT NOT NULL,
 effect_hash TEXT NOT NULL, status TEXT NOT NULL, result TEXT,
 idem TEXT NOT NULL, UNIQUE(tenant,branch,idem));
CREATE TABLE IF NOT EXISTS approval_decisions(
 invocation TEXT NOT NULL REFERENCES invocations(id), approver TEXT NOT NULL,
 effect_hash TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(invocation,approver));
CREATE TABLE IF NOT EXISTS audit_logs(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL, event TEXT NOT NULL,
 previous TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS immutable_audit_update BEFORE UPDATE ON audit_logs
 BEGIN SELECT RAISE(ABORT,'Audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS immutable_audit_delete BEFORE DELETE ON audit_logs
 BEGIN SELECT RAISE(ABORT,'Audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS immutable_revision_update BEFORE UPDATE ON revisions
 BEGIN SELECT RAISE(ABORT,'Revision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_revision_delete BEFORE DELETE ON revisions
 BEGIN SELECT RAISE(ABORT,'Revision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_commit_update BEFORE UPDATE ON commits
 BEGIN SELECT RAISE(ABORT,'Commit is immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_commit_delete BEFORE DELETE ON commits
 BEGIN SELECT RAISE(ABORT,'Commit is immutable'); END;
"""


class Store:
    def __init__(self, path, storage_backend=None, warehouse=None):
        self.path = str(Path(path).expanduser().resolve())
        self.file_storage = None
        with self.connect() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone()
            if exists and [row[0] for row in db.execute('SELECT version FROM schema_version')] not in ([1], [2], [3]):
                raise Conflict('Unsupported database schema version; explicit migration required')
            db.executescript(SCHEMA)
        from s2_platform import S2Error
        if storage_backend not in {None, 'json', 'parquet', 'iceberg'}:
            raise S2Error('Unknown dataset storage backend')
        with self.transaction() as db:
            config = db.execute('SELECT * FROM storage_config WHERE id=1').fetchone()
            backend = storage_backend or (config['backend'] if config else 'json')
            root = str(Path(warehouse).expanduser().resolve()) if warehouse else (
                config['warehouse'] if config and config['warehouse'] else self.path + '.warehouse')
            if config and config['backend'] != 'json':
                if backend != config['backend'] or root != config['warehouse']:
                    raise Conflict('File storage configuration is pinned; explicit migration is required')
            if backend == 'json' and warehouse:
                raise S2Error('warehouse requires parquet or iceberg storage')
            if backend != 'json':
                from s2_catalog_storage.file_storage import FileStorage
                self.file_storage = FileStorage(root, backend)
                # Old v0.1 code must refuse a database containing file-backed revisions.
                if db.execute('SELECT version FROM schema_version').fetchone()[0] < 2:
                    db.execute('DELETE FROM schema_version')
                    db.execute('INSERT INTO schema_version VALUES(2)')
            db.execute('INSERT INTO storage_config VALUES(1,?,?) '
                       'ON CONFLICT(id) DO UPDATE SET backend=excluded.backend,warehouse=excluded.warehouse',
                       (backend, root if backend != 'json' else None))
            self.storage_backend = backend

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if hasattr(self, 'storage_backend'):
                    config = db.execute('SELECT backend,warehouse FROM storage_config WHERE id=1').fetchone()
                    if config and config['backend'] != self.storage_backend:
                        raise Conflict('Storage configuration changed; restart this service')
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def branch(self, db, tenant, branch_id):
        row = db.execute("SELECT * FROM branches WHERE id=? AND tenant=?",
                         (branch_id, tenant)).fetchone()
        if row is None:
            raise NotFound("Branch not found")
        return row

    def manifest(self, db, tenant, commit_id):
        row = db.execute("SELECT manifest FROM commits WHERE id=? AND tenant=?",
                         (commit_id, tenant)).fetchone()
        if row is None:
            raise NotFound("Commit not found")
        return json.loads(row[0])

    def revision(self, db, tenant, revision_id):
        row = db.execute("SELECT kind,payload FROM revisions WHERE tenant=? AND id=?",
                         (tenant, revision_id)).fetchone()
        if row is None:
            raise NotFound("Revision not found")
        payload = json.loads(row['payload'])
        if row['kind'] == 'dataset_snapshot' and 'storage' in payload and 'rows' not in payload:
            if self.file_storage is None:
                from s2_catalog_storage.file_storage import StorageError
                raise StorageError('File-backed revision requires its configured warehouse')
            payload['rows'] = self.file_storage.read(tenant, payload['storage'])
        return payload

    def put_revision(self, db, tenant, kind, payload):
        from s2_platform import canonical
        if kind == 'dataset_snapshot':
            payload = {key: value for key, value in payload.items() if key != 'storage'}
            if self.file_storage is not None:
                descriptor = self.file_storage.write(tenant, payload)
                payload = {key: value for key, value in payload.items() if key != 'rows'}
                payload['storage'] = descriptor
        revision = digest({"kind": kind, "payload": payload})
        db.execute("INSERT OR IGNORE INTO revisions VALUES(?,?,?,?)",
                   (revision, tenant, kind, canonical(payload)))
        return revision

    def create_commit(self, db, tenant, project, parents, manifest, actor):
        from s2_platform import canonical
        commit = new_id()
        db.execute("INSERT INTO commits VALUES(?,?,?,?,?,?,?)",
                   (commit, tenant, project, canonical(parents), canonical(manifest), actor, now()))
        return commit

    def publish(self, db, ctx, branch_id, expected, manifest, extra_parent=None):
        branch = self.branch(db, ctx.tenant, branch_id)
        if branch["head"] != expected:
            raise Conflict("Branch head changed; refresh and revalidate")
        parents = [expected] + ([extra_parent] if extra_parent else [])
        commit = self.create_commit(db, ctx.tenant, branch["project"], parents, manifest, ctx.principal)
        count = db.execute("UPDATE branches SET head=? WHERE id=? AND tenant=? AND head=?",
                           (commit, branch_id, ctx.tenant, expected)).rowcount
        if count != 1:
            raise Conflict("Concurrent publication")
        return commit

    def storage_report(self):
        # Prevent the report from classifying an active local writer as an orphan.
        with self.transaction() as db:
            if self.file_storage is None:
                return {'backend': 'json', 'referenced_artifacts': 0, 'broken_references': [],
                        'unreferenced_artifacts': [], 'deleted': 0}
            references = []
            for row in db.execute("SELECT tenant,id,payload FROM revisions WHERE kind='dataset_snapshot'"):
                payload = json.loads(row['payload'])
                if 'storage' in payload:
                    references.append((row['tenant'], row['id'], payload['storage']))
            return {'backend': self.storage_backend, **self.file_storage.report(references)}
