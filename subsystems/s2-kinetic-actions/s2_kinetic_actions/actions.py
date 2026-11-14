# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
import json
from s2_platform import S2Error, Conflict, Forbidden, NotFound, canonical, digest, new_id, now


class Actions:
    def __init__(self, store, security, kernel):
        self.store, self.security, self.kernel = store, security, kernel

    def define(self, db, ctx, branch, expected_head, name, object_type, writable_properties, approvals=0):
        if type(approvals) is not int or not 0 <= approvals <= 10:
            raise S2Error('approvals must be between 0 and 10')
        manifest = self.store.manifest(db, ctx.tenant, expected_head)
        definition = self.kernel.resource(db, ctx, manifest, 'type/' + object_type)
        self.kernel.check_definition_access(ctx, definition)
        if (not isinstance(writable_properties, list) or not writable_properties or
                any(not isinstance(p, str) or p not in definition['properties'] for p in writable_properties)):
            raise S2Error('Invalid writable properties')
        action = {'object_type': object_type, 'writable_properties': sorted(set(writable_properties)),
                  'approvals': approvals, 'external_effects': False}
        revision = self.store.put_revision(db, ctx.tenant, 'action_type', action)
        manifest['action/' + name] = revision
        return {'commit': self.store.publish(db, ctx, branch, expected_head, manifest), 'revision': revision}

    def _effects(self, db, ctx, manifest, definition, edits):
        if not isinstance(edits, list) or not 1 <= len(edits) <= 100:
            raise S2Error('Action requires 1 to 100 edits')
        effects, seen = [], set()
        for edit in edits:
            if not isinstance(edit, dict) or set(edit) != {'object', 'expected_revision', 'set'}:
                raise S2Error('Each edit requires object, expected_revision and set')
            oid = edit['object']
            if not isinstance(oid, str) or oid in seen:
                raise S2Error('Action cannot contain duplicate object edits')
            seen.add(oid)
            old = self.kernel.resource(db, ctx, manifest, 'object/' + oid)
            if old['type'] != definition['object_type']:
                raise S2Error('Action object type mismatch')
            if not isinstance(edit['set'], dict) or set(edit['set']) - set(definition['writable_properties']):
                raise Forbidden('Action cannot modify those properties')
            values = {**old['values'], **edit['set']}
            effects.append(self.kernel.check_object_write(db, ctx, manifest, oid, old['type'], values,
                                                          old['marking'], edit['expected_revision']))
        return effects

    def submit(self, db, ctx, branch, expected_head, action, edits, idempotency_key):
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise S2Error('Idempotency key required')
        request = {'action': action, 'edits': edits}
        existing = db.execute('SELECT * FROM invocations WHERE tenant=? AND branch=? AND idem=?',
                              (ctx.tenant, branch, idempotency_key)).fetchone()
        if existing:
            if existing['actor'] != ctx.principal or existing['base'] != expected_head or existing['payload'] != canonical(request):
                raise Conflict('Idempotency key reused for a different invocation')
            return {'invocation': existing['id'], 'status': existing['status']}
        if self.store.branch(db, ctx.tenant, branch)['head'] != expected_head:
            raise Conflict('Stale branch head')
        manifest = self.store.manifest(db, ctx.tenant, expected_head)
        definition = self.kernel.resource(db, ctx, manifest, 'action/' + action)
        effects = self._effects(db, ctx, manifest, definition, edits)
        invocation = new_id()
        revision = manifest['action/' + action]
        effect_hash = digest({'base': expected_head, 'action_revision': revision, 'effects': effects})
        status = 'SUBMITTED' if definition['approvals'] else 'APPROVED'
        db.execute('INSERT INTO invocations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (invocation, ctx.tenant, ctx.principal, branch, expected_head, revision,
                    canonical(request), effect_hash, status, None, idempotency_key))
        return {'invocation': invocation, 'status': status, 'effect_hash': effect_hash}

    def _get(self, db, ctx, branch, invocation):
        row = db.execute('SELECT * FROM invocations WHERE id=? AND tenant=? AND branch=?',
                         (invocation, ctx.tenant, branch)).fetchone()
        if row is None:
            raise NotFound('Invocation not found')
        return row

    def approve(self, db, ctx, branch, invocation, effect_hash):
        self.security.require(ctx, 'admin')
        inv = self._get(db, ctx, branch, invocation)
        if inv['actor'] == ctx.principal:
            raise Forbidden('Submitters cannot approve their own Actions')
        if inv['status'] not in {'SUBMITTED', 'APPROVED'}:
            raise Conflict('Invocation no longer awaiting approval')
        if inv['effect_hash'] != effect_hash:
            raise Conflict('Proposed effect changed')
        if self.store.branch(db, ctx.tenant, branch)['head'] != inv['base']:
            raise Conflict('Branch changed; submit a new invocation')
        definition = self.store.revision(db, ctx.tenant, inv['action_revision'])
        manifest = self.store.manifest(db, ctx.tenant, inv['base'])
        self._effects(db, ctx, manifest, definition, json.loads(inv['payload'])['edits'])
        db.execute('INSERT OR IGNORE INTO approval_decisions VALUES(?,?,?,?)',
                   (invocation, ctx.principal, effect_hash, now()))
        approved = db.execute('SELECT COUNT(*) FROM approval_decisions WHERE invocation=?', (invocation,)).fetchone()[0]
        status = 'APPROVED' if approved >= definition['approvals'] else 'SUBMITTED'
        db.execute('UPDATE invocations SET status=? WHERE id=?', (status, invocation))
        return {'invocation': invocation, 'status': status, 'approvals': approved}

    def execute(self, db, ctx, branch, invocation):
        inv = self._get(db, ctx, branch, invocation)
        if inv['actor'] != ctx.principal:
            raise Forbidden('Only the submitting principal can execute this invocation')
        if inv['status'] == 'COMMITTED':
            return json.loads(inv['result'])
        if inv['status'] != 'APPROVED':
            raise Conflict('Required approvals are missing')
        manifest = self.store.manifest(db, ctx.tenant, inv['base'])
        request = json.loads(inv['payload'])
        definition = self.store.revision(db, ctx.tenant, inv['action_revision'])
        # Approval identities must remain active, authorized and able to inspect the effects.
        decisions = db.execute('SELECT p.* FROM approval_decisions a JOIN principals p ON p.id=a.approver '
                               'WHERE a.invocation=? AND a.effect_hash=? AND p.active=1 AND p.role=?',
                               (invocation, inv['effect_hash'], 'admin')).fetchall()
        from s2_platform import Context
        valid_approvals = 0
        for p in decisions:
            reviewer = Context(p['tenant'], p['id'], p['role'], p['clearance'], tuple(json.loads(p['compartments'])), ctx.project)
            try:
                self._effects(db, reviewer, manifest, definition, request['edits'])
                valid_approvals += 1
            except Forbidden:
                pass
        if valid_approvals < definition['approvals']:
            raise Conflict('Approval authorization no longer valid')
        effects = self._effects(db, ctx, manifest, definition, request['edits'])
        if digest({'base': inv['base'], 'action_revision': inv['action_revision'], 'effects': effects}) != inv['effect_hash']:
            raise Conflict('Effect digest mismatch')
        versions = {}
        for obj in effects:
            revision = self.store.put_revision(db, ctx.tenant, 'object', obj)
            manifest['object/' + obj['id']] = revision
            versions[obj['id']] = revision
        commit = self.store.publish(db, ctx, branch, inv['base'], manifest)
        result = {'invocation': invocation, 'status': 'COMMITTED', 'commit': commit, 'objects': versions}
        db.execute('UPDATE invocations SET status=?,result=? WHERE id=?', ('COMMITTED', canonical(result), invocation))
        return result
