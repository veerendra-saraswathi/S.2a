# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Synchronous, reviewed dependency chains with in-memory previews."""
import copy
import json
from s2_platform import Conflict, NotFound, S2Error, canonical, digest, new_id, now
from s2_ontology_kernel.kernel import Kernel
from .pipelines import Pipelines


class PreviewStore:
    """Read-through overlay. No writes to the catalog or artifact warehouse."""
    def __init__(self, store, tenant, head, manifest):
        self.store, self.tenant, self.head = store, tenant, head
        self.current = dict(manifest)
        self.virtual = {}

    def branch(self, db, tenant, branch):
        return self.store.branch(db, tenant, branch)

    def manifest(self, db, tenant, head):
        if tenant == self.tenant and head == self.head:
            return dict(self.current)
        return self.store.manifest(db, tenant, head)

    def revision(self, db, tenant, revision):
        if tenant == self.tenant and revision in self.virtual:
            return copy.deepcopy(self.virtual[revision])
        return self.store.revision(db, tenant, revision)

    def adopt(self, dataset, snapshot):
        value = {k: v for k, v in snapshot.items() if k != 'storage'}
        reference = 'preview:' + digest(value)
        self.virtual[reference] = copy.deepcopy(value)
        self.current['dataset/' + dataset] = reference


class Orchestration:
    def __init__(self, store, security, kernel, pipelines):
        self.store, self.security, self.kernel, self.pipelines = store, security, kernel, pipelines

    def _order(self, db, ctx, manifest, targets):
        if (not isinstance(targets, list) or not 1 <= len(targets) <= 20 or
                any(not isinstance(x, str) or not x for x in targets) or len(set(targets)) != len(targets)):
            raise S2Error('Require 1 to 20 distinct target pipeline names')
        definitions = {key[9:]: self.kernel.resource(db, ctx, manifest, key)
                       for key in manifest if key.startswith('pipeline/')}
        if len(definitions) > 100:
            raise S2Error('Definition inventory is limited to 100 pipelines')
        producers = {}
        for name, definition in definitions.items():
            self.security.check_marking(ctx, definition['marking'])
            if definition['output'] in producers:
                raise Conflict('Competing pipeline output writers')
            producers[definition['output']] = name
        if any(name not in definitions for name in targets):
            raise NotFound('Target pipeline not found')
        pending = set(definitions)
        order = []
        while pending:
            ready = sorted(name for name in pending if producers.get(definitions[name]['input']) not in pending)
            if not ready:
                raise Conflict('Pipeline definition graph contains a cycle')
            order.extend(ready)
            pending.difference_update(ready)
        selected = set(targets)
        for name in reversed(order):
            if name in selected:
                parent = producers.get(definitions[name]['input'])
                if parent is not None:
                    selected.add(parent)
        if len(selected) > 20:
            raise S2Error('Selected dependency closure exceeds 20 stages')
        return [name for name in order if name in selected], definitions

    def _plan(self, db, ctx, branch, expected_head, targets):
        if self.store.branch(db, ctx.tenant, branch)['head'] != expected_head:
            raise Conflict('Stale branch head')
        manifest = self.store.manifest(db, ctx.tenant, expected_head)
        order, definitions = self._order(db, ctx, manifest, targets)
        overlay = PreviewStore(self.store, ctx.tenant, expected_head, manifest)
        simulated = Pipelines(overlay, self.security, Kernel(overlay, self.security))
        stages = []
        blocked = False
        for name in order:
            if blocked:
                stages.append({'pipeline': name, 'status': 'BLOCKED'})
                continue
            plan, snapshot, _ = simulated._plan(db, ctx, branch, expected_head, name)
            stages.append({'pipeline': name, 'pipeline_revision': manifest['pipeline/' + name],
                           'input': plan['input'], 'output': plan['output'],
                           'input_rows': plan['input_rows'], 'previous_output_rows': plan['previous_output_rows'],
                           'output_rows': plan['output_rows'], 'errors': plan['errors'],
                           'status': 'READY' if plan['can_publish'] else 'FAILED',
                           'preview_effect_hash': plan['plan_hash']})
            if plan['can_publish']:
                overlay.adopt(plan['output'], snapshot)
            else:
                blocked = True
        result = {'branch': branch, 'base_commit': expected_head, 'targets': sorted(targets),
                  'order': order, 'stages': stages, 'can_publish': not blocked,
                  'operation': 'ATOMIC_PIPELINE_CHAIN'}
        result['plan_hash'] = digest({'plan': result, 'actor': ctx.principal, 'definitions': definitions})
        return result

    def preview(self, db, ctx, branch, expected_head, targets):
        return self._plan(db, ctx, branch, expected_head, targets)

    def run(self, db, ctx, branch, expected_head, targets, plan_hash, idempotency_key):
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise S2Error('Bounded idempotency key required')
        if not isinstance(targets, list) or any(not isinstance(x, str) for x in targets):
            raise S2Error('Target names required')
        request = digest({'actor': ctx.principal, 'base': expected_head, 'targets': sorted(targets), 'plan_hash': plan_hash})
        replay = db.execute('SELECT result,request_hash FROM orchestration_runs WHERE tenant=? AND branch=? AND idem=?',
                            (ctx.tenant, branch, idempotency_key)).fetchone()
        if replay:
            if replay['request_hash'] != request:
                raise Conflict('Idempotency key reused')
            return self._receipt(db, ctx, json.loads(replay['result']))
        plan = self._plan(db, ctx, branch, expected_head, targets)
        if plan_hash != plan['plan_hash']:
            raise Conflict('Chain plan or policy changed; preview again')
        if not plan['can_publish']:
            raise Conflict('Pipeline chain health gate failed')
        run = new_id()
        stages = []
        for index, name in enumerate(plan['order']):
            head = self.store.branch(db, ctx.tenant, branch)['head']
            stage_plan = self.pipelines.preview(db, ctx, branch, head, name)
            # Each stage revalidates against real prior-stage revisions inside the same transaction.
            stages.append(self.pipelines.run(db, ctx, branch, head, name, stage_plan['plan_hash'],
                                             'chain:' + run + ':' + str(index)))
        result = {'branch': branch, 'base_commit': expected_head, 'targets': plan['targets'], 'order': plan['order'],
                  'plan_hash': plan_hash, 'run': run, 'status': 'COMMITTED', 'actor': ctx.principal,
                  'created_at': now(), 'commit': stages[-1]['commit'], 'stages': stages}
        db.execute('INSERT INTO orchestration_runs VALUES(?,?,?,?,?,?,?)',
                   (ctx.tenant, branch, run, ctx.principal, idempotency_key, request, canonical(result)))
        return result

    def _receipt(self, db, ctx, result):
        for stage in result['stages']:
            self.pipelines._receipt(db, ctx, stage)
        return result

    def get(self, db, ctx, branch, run):
        row = db.execute('SELECT result FROM orchestration_runs WHERE tenant=? AND branch=? AND id=? AND actor=?',
                         (ctx.tenant, branch, run, ctx.principal)).fetchone()
        if row is None:
            raise NotFound('Pipeline chain run not found')
        return self._receipt(db, ctx, json.loads(row['result']))
