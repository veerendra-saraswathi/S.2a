# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""SQLite reference catalog: immutable revisions and atomic manifest publication."""
import json
import sqlite3
from contextlib import contextmanager
from s2_platform import Conflict, NotFound, digest, new_id, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES(1);
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
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone()
            if exists and [row[0] for row in db.execute('SELECT version FROM schema_version')] != [1]:
                raise Conflict('Unsupported database schema version; explicit migration required')
            db.executescript(SCHEMA)

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
        row = db.execute("SELECT payload FROM revisions WHERE tenant=? AND id=?",
                         (tenant, revision_id)).fetchone()
        if row is None:
            raise NotFound("Revision not found")
        return json.loads(row[0])

    def put_revision(self, db, tenant, kind, payload):
        from s2_platform import canonical
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
