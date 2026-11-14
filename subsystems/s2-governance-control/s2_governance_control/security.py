# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
import hashlib
import json
import secrets
from s2_platform import Context, Forbidden, S2Error, canonical, digest, dominates, marking, new_id, now


class Security:
    def authenticate(self, db, token):
        if not isinstance(token, str) or not token:
            raise Forbidden("Invalid credential")
        hashed = hashlib.sha256(token.encode()).hexdigest()
        row = db.execute("SELECT p.* FROM principals p JOIN tokens t ON p.id=t.principal "
                         "WHERE t.hash=? AND p.active=1", (hashed,)).fetchone()
        if row is None:
            raise Forbidden("Invalid credential")
        return Context(row["tenant"], row["id"], row["role"], row["clearance"],
                       tuple(json.loads(row["compartments"])))

    def require(self, ctx, operation):
        roles = {"read": {"viewer", "editor", "admin"},
                 "write": {"editor", "admin"}, "admin": {"admin"}}
        if ctx.role not in roles[operation]:
            raise Forbidden("Operation not permitted")

    def allowed(self, ctx, label):
        return dominates({"level": ctx.clearance, "compartments": ctx.compartments}, marking(label))

    def check_marking(self, ctx, label):
        if not self.allowed(ctx, label):
            raise Forbidden("Classification or compartment access denied")

    def issue(self, db, tenant, role, clearance=0, compartments=None):
        if role not in {"admin", "editor", "viewer"}:
            raise S2Error("Invalid role")
        label = marking({"level": clearance, "compartments": compartments or []})
        principal, token = new_id(), secrets.token_urlsafe(32)
        db.execute("INSERT INTO principals(id,tenant,role,clearance,compartments) VALUES(?,?,?,?,?)",
                   (principal, tenant, role, label["level"], canonical(label["compartments"])))
        db.execute("INSERT INTO tokens VALUES(?,?)", (hashlib.sha256(token.encode()).hexdigest(), principal))
        return {"principal_id": principal, "token": token}

    def audit(self, db, ctx, operation, resource, details=None):
        prev = db.execute("SELECT hash FROM audit_logs WHERE tenant=? ORDER BY seq DESC LIMIT 1",
                          (ctx.tenant,)).fetchone()
        previous = prev[0] if prev else "0" * 64
        event = {"actor": ctx.principal, "tenant": ctx.tenant, "operation": operation,
                 "resource": resource, "details": details or {}, "timestamp": now()}
        value = digest({"previous": previous, "event": event})
        db.execute("INSERT INTO audit_logs(tenant,event,previous,hash) VALUES(?,?,?,?)",
                   (ctx.tenant, canonical(event), previous, value))

    def verify(self, db, tenant):
        previous, count = "0" * 64, 0
        for row in db.execute("SELECT * FROM audit_logs WHERE tenant=? ORDER BY seq", (tenant,)):
            event = json.loads(row["event"])
            if row["previous"] != previous or row["hash"] != digest({"previous": previous, "event": event}):
                return {"valid": False, "events": count}
            previous, count = row["hash"], count + 1
        return {"valid": True, "events": count, "head_hash": previous}

    def effective(self, db, ctx, resource, base):
        label = marking(base)
        row = db.execute('SELECT label FROM classification_overrides WHERE tenant=? AND project=? AND resource=?',
                         (ctx.tenant, ctx.project, resource)).fetchone()
        if row:
            floor = json.loads(row[0])
            label = {'level': max(label['level'], floor['level']),
                     'compartments': sorted(set(label['compartments']) | set(floor['compartments']))}
        return label
