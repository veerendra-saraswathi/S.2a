# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
from dataclasses import replace
import inspect
import json
import sqlite3
from s2_platform import Context, S2Error, Forbidden, Conflict, NotFound, canonical, new_id, marking, dominates
from s2_catalog_storage.store import Store
from s2_catalog_storage.datasets import Datasets
from s2_governance_control.security import Security
from s2_ontology_kernel.kernel import Kernel
from s2_ontology_kernel.bindings import Bindings
from s2_kinetic_actions.actions import Actions


class Service:
    def __init__(self, database, storage_backend=None, warehouse=None):
        self.store = Store(database, storage_backend=storage_backend, warehouse=warehouse)
        self.security = Security()
        self.kernel = Kernel(self.store, self.security)
        self.datasets = Datasets(self.store, self.security, self.kernel)
        self.actions = Actions(self.store, self.security, self.kernel)
        self.bindings = Bindings(self.store, self.security, self.kernel)
        self.routes = {
            'projects.create': ('admin', self.create_project),
            'projects.grant': ('admin', self.grant_project),
            'principals.create': ('admin', self.create_principal),
            'principals.revoke': ('admin', self.revoke_principal),
            'branches.head': ('read', self.branch_head),
            'branches.fork': ('write', self.fork_branch),
            'branches.merge': ('admin', self.merge_branch),
            'datasets.create': ('write', self.datasets.create),
            'datasets.begin': ('write', self.datasets.begin),
            'datasets.stage': ('write', self.datasets.stage),
            'datasets.commit': ('write', self.datasets.commit),
            'datasets.abort': ('write', self.datasets.abort),
            'datasets.read': ('read', self.datasets.read),
            'ontology.define': ('write', self.define_type),
            'ontology.interface': ('write', self.define_interface),
            'ontology.link_type': ('write', self.define_link_type),
            'objects.put': ('write', self.put_object),
            'objects.get': ('read', self.get_object),
            'objects.list': ('read', self.list_objects),
            'links.put': ('write', self.put_link),
            'links.list': ('read', self.list_links),
            'actions.define': ('write', self.actions.define),
            'actions.submit': ('write', self.actions.submit),
            'actions.approve': ('admin', self.actions.approve),
            'actions.execute': ('write', self.actions.execute),
            'audit.verify': ('admin', self.verify_audit),
            'policy.restrict': ('admin', self.restrict_policy),
            'bindings.define': ('write', self.bindings.define),
            'bindings.materialize': ('write', self.bindings.materialize),
            'bindings.provenance': ('read', self.bindings.provenance),
            'bindings.preview': ('read', self.bindings.preview),
        }
        with self.store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS project_memberships('
                       'project TEXT NOT NULL REFERENCES projects(id), principal TEXT NOT NULL REFERENCES principals(id), '
                       'PRIMARY KEY(project,principal))')
            db.execute('CREATE TABLE IF NOT EXISTS classification_overrides('
                       'tenant TEXT NOT NULL, project TEXT NOT NULL, resource TEXT NOT NULL, label TEXT NOT NULL, '
                       'PRIMARY KEY(tenant,project,resource))')
            db.execute('CREATE TABLE IF NOT EXISTS binding_builds('
                       'tenant TEXT NOT NULL, branch TEXT NOT NULL, idem TEXT NOT NULL, '
                       'request_hash TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(tenant,branch,idem))')

    def bootstrap(self, name, clearance=0, compartments=None):
        """Host-administrator CLI only. Deliberately absent from the network API."""
        if not isinstance(name, str) or not name.strip():
            raise S2Error('Tenant name required')
        with self.store.transaction() as db:
            tenant = new_id()
            db.execute('INSERT INTO tenants VALUES(?,?)', (tenant, name))
            issued = self.security.issue(db, tenant, 'admin', clearance, compartments)
            ctx = self.security.authenticate(db, issued['token'])
            self.security.audit(db, ctx, 'tenant.bootstrap', tenant)
            return {'tenant_id': tenant, **issued}

    def call(self, token, operation, /, **params):
        if operation not in self.routes:
            raise NotFound('Unknown operation')
        role, handler = self.routes[operation]
        try:
            inspect.signature(handler).bind(None, None, **params)
        except TypeError as exc:
            raise S2Error('Invalid operation parameters') from exc
        ctx = None
        try:
            with self.store.transaction() as db:
                ctx = self.security.authenticate(db, token)
                self.security.require(ctx, role)
                if 'branch' in params:
                    branch = self.store.branch(db, ctx.tenant, params['branch'])
                    self.check_project(db, ctx, branch['project'])
                    ctx = replace(ctx, project=branch['project'])
                    # No caller can manufacture a manifest from another project/branch.
                    expected = params.get('expected_head')
                    if expected and expected != branch['head']:
                        # Idempotent transaction/invocation retries validate their own pinned base.
                        if operation not in {'datasets.begin', 'actions.submit', 'bindings.materialize'}:
                            raise Conflict('Stale branch head')
                result = handler(db, ctx, **params)
                self.security.audit(db, ctx, operation, params.get('branch', ctx.tenant),
                                    {'result': 'success', 'commit': result.get('commit'),
                                     'transaction': result.get('transaction'), 'invocation': result.get('invocation')})
                return result
        except (S2Error, sqlite3.IntegrityError) as exc:
            if ctx is not None:
                with self.store.transaction() as audit_db:
                    self.security.audit(audit_db, ctx, operation, 'request',
                                        {'result': 'denied' if isinstance(exc, Forbidden) else 'rejected',
                                         'error_type': type(exc).__name__})
            if isinstance(exc, sqlite3.IntegrityError):
                raise Conflict('Uniqueness or integrity constraint failed') from exc
            raise

    def check_project(self, db, ctx, project):
        row = db.execute('SELECT id FROM projects WHERE id=? AND tenant=?', (project, ctx.tenant)).fetchone()
        if row is None:
            raise NotFound('Project not found')
        if ctx.role != 'admin' and not db.execute(
                'SELECT 1 FROM project_memberships WHERE project=? AND principal=?',
                (project, ctx.principal)).fetchone():
            raise Forbidden('Project membership required')

    def create_project(self, db, ctx, name):
        if not isinstance(name, str) or not name.strip():
            raise S2Error('Project name required')
        project, branch = new_id(), new_id()
        db.execute('INSERT INTO projects VALUES(?,?,?)', (project, ctx.tenant, name))
        commit = self.store.create_commit(db, ctx.tenant, project, [], {}, ctx.principal)
        db.execute('INSERT INTO branches VALUES(?,?,?,?,?,?)', (branch, ctx.tenant, project, 'main', commit, commit))
        return {'project': project, 'branch': branch, 'commit': commit}

    def grant_project(self, db, ctx, project, principal):
        self.check_project(db, ctx, project)
        if not db.execute('SELECT 1 FROM principals WHERE tenant=? AND id=?', (ctx.tenant, principal)).fetchone():
            raise NotFound('Principal not found')
        db.execute('INSERT OR IGNORE INTO project_memberships VALUES(?,?)', (project, principal))
        return {'granted': True}

    def create_principal(self, db, ctx, role, clearance=0, compartments=None):
        label = marking({'level': clearance, 'compartments': compartments or []})
        self.security.check_marking(ctx, label)
        return self.security.issue(db, ctx.tenant, role, clearance, compartments)

    def revoke_principal(self, db, ctx, principal):
        if principal == ctx.principal:
            raise Conflict('Cannot revoke the active administrator through this operation')
        count = db.execute('UPDATE principals SET active=0 WHERE tenant=? AND id=?',
                           (ctx.tenant, principal)).rowcount
        if not count:
            raise NotFound('Principal not found')
        return {'revoked': True}

    def branch_head(self, db, ctx, branch):
        row = self.store.branch(db, ctx.tenant, branch)
        return {'branch': branch, 'name': row['name'], 'commit': row['head'], 'project': row['project']}

    def fork_branch(self, db, ctx, branch, name):
        if not isinstance(name, str) or not name.strip():
            raise S2Error('Branch name required')
        source = self.store.branch(db, ctx.tenant, branch)
        target = new_id()
        db.execute('INSERT INTO branches VALUES(?,?,?,?,?,?)',
                   (target, ctx.tenant, source['project'], name, source['head'], source['head']))
        return {'branch': target, 'commit': source['head']}

    def merge_branch(self, db, ctx, branch, source, expected_head, expected_source):
        target = self.store.branch(db, ctx.tenant, branch)
        origin = self.store.branch(db, ctx.tenant, source)
        self.check_project(db, ctx, origin['project'])
        if target['project'] != origin['project'] or branch == source:
            raise Conflict('Merge requires distinct branches in one project')
        if origin['head'] != expected_source:
            raise Conflict('Source branch changed')
        def ancestors(commit):
            found, pending = set(), [commit]
            while pending:
                item = pending.pop()
                if item in found:
                    continue
                found.add(item)
                row = db.execute('SELECT parents FROM commits WHERE id=? AND tenant=?', (item, ctx.tenant)).fetchone()
                pending.extend(json.loads(row[0]))
            return found
        # Gate 1 supports direct-fork merges, including fast-forward and divergent changes.
        base = origin['base']
        if base not in ancestors(target['head']):
            raise Conflict('No supported direct-fork base; explicit rebase required')
        common = self.store.manifest(db, ctx.tenant, base)
        ours = self.store.manifest(db, ctx.tenant, expected_head)
        theirs = self.store.manifest(db, ctx.tenant, expected_source)
        if any(key.startswith('binding/') for key in set(ours) | set(theirs)):
            raise Conflict('Binding-aware branch merge requires a future reconciliation workflow')
        merged = dict(ours)
        conflicts = []
        for key in set(common) | set(ours) | set(theirs):
            old, left, right = common.get(key), ours.get(key), theirs.get(key)
            if right == old or left == right:
                continue
            if left != old:
                conflicts.append(key)
            elif right is None:
                raise Conflict('Resource deletion merge requires migration')
            else:
                value = self.kernel.resource(db, ctx, theirs, key)
                if key.startswith(('type/', 'interface/')):
                    self.kernel.check_definition_access(ctx, value)
                    if left:
                        self.kernel.no_downgrade(self.store.revision(db, ctx.tenant, left), value)
                elif key.startswith('dataset/'):
                    self.kernel.check_definition_access(ctx, value['schema'])
                    for row in value['rows']:
                        self.security.check_marking(ctx, row['marking'])
                elif key.startswith(('object/', 'link/', 'linktype/')):
                    self.security.check_marking(ctx, value.get('marking'))
                merged[key] = right
        if conflicts:
            raise Conflict('Merge conflicts: ' + ', '.join(sorted(conflicts)))
        # Validate caller access to all merged objects, not just the object's row marking.
        for key in merged:
            if key.startswith('object/'):
                obj = self.kernel.resource(db, ctx, merged, key)
                self.security.check_marking(ctx, obj['marking'])
                schema = self.kernel.resource(db, ctx, merged, 'type/' + obj['type'])
                self.kernel.check_definition_access(ctx, schema)
        self.kernel.validate_manifest(db, ctx.tenant, merged)
        return {'commit': self.store.publish(db, ctx, branch, expected_head, merged, expected_source)}

    def _define(self, db, ctx, branch, expected_head, name, definition, kind):
        self.kernel.validate_definition(definition)
        self.kernel.check_definition_access(ctx, definition)
        manifest = self.store.manifest(db, ctx.tenant, expected_head)
        key = kind + '/' + name
        self.security.check_marking(ctx, self.security.effective(db, ctx, key, definition.get('marking')))
        for prop, spec in definition['properties'].items():
            self.security.check_marking(ctx, self.security.effective(db, ctx, key + '/property/' + prop, spec.get('marking')))
        if key in manifest:
            previous = self.kernel.resource(db, ctx, manifest, key)
            self.kernel.check_definition_access(ctx, previous)
            self.kernel.no_downgrade(previous, definition)
        manifest[key] = self.store.put_revision(db, ctx.tenant, kind, definition)
        self.kernel.validate_manifest(db, ctx.tenant, manifest)
        return {'commit': self.store.publish(db, ctx, branch, expected_head, manifest), 'revision': manifest[key]}

    def define_type(self, db, ctx, branch, expected_head, name, definition):
        return self._define(db, ctx, branch, expected_head, name, definition, 'type')

    def define_interface(self, db, ctx, branch, expected_head, name, definition):
        return self._define(db, ctx, branch, expected_head, name, definition, 'interface')

    def put_object(self, db, ctx, branch, expected_head, object_id, object_type, values,
                   label=None, expected_revision=None):
        manifest = self.store.manifest(db, ctx.tenant, expected_head)
        obj = self.kernel.check_object_write(db, ctx, manifest, object_id, object_type, values,
                                             marking(label), expected_revision)
        revision = self.store.put_revision(db, ctx.tenant, 'object', obj)
        manifest['object/' + object_id] = revision
        return {'commit': self.store.publish(db, ctx, branch, expected_head, manifest), 'revision': revision}

    def _head_manifest(self, db, ctx, branch):
        head = self.store.branch(db, ctx.tenant, branch)['head']
        return head, self.store.manifest(db, ctx.tenant, head)

    def get_object(self, db, ctx, branch, object_id):
        head, manifest = self._head_manifest(db, ctx, branch)
        return {'commit': head, **self.kernel.visible_object(db, ctx, manifest, object_id)}

    def list_objects(self, db, ctx, branch, object_type=None):
        head, manifest = self._head_manifest(db, ctx, branch)
        objects = []
        for key in sorted(manifest):
            if key.startswith('object/'):
                try:
                    obj = self.kernel.visible_object(db, ctx, manifest, key[7:])
                    if object_type is None or obj['type'] == object_type:
                        objects.append(obj)
                except NotFound:
                    pass
        return {'commit': head, 'objects': objects}

    def define_link_type(self, db, ctx, branch, expected_head, name, source_type, target_type, label=None):
        manifest = self.store.manifest(db, ctx.tenant, expected_head)
        for kind in (source_type, target_type):
            self.kernel.check_definition_access(ctx, self.kernel.resource(db, ctx, manifest, 'type/' + kind))
        self.security.check_marking(ctx, label)
        key = 'linktype/' + name
        definition = {'source': source_type, 'target': target_type, 'marking': marking(label)}
        if key in manifest and self.store.revision(db, ctx.tenant, manifest[key]) != definition:
            raise Conflict('Link type changes require migration')
        manifest[key] = self.store.put_revision(db, ctx.tenant, 'link_type', definition)
        return {'commit': self.store.publish(db, ctx, branch, expected_head, manifest)}

    def put_link(self, db, ctx, branch, expected_head, link_id, link_type, source, target, label=None):
        manifest = self.store.manifest(db, ctx.tenant, expected_head)
        if 'link/' + link_id in manifest:
            raise Conflict('Link already exists')
        definition = self.kernel.resource(db, ctx, manifest, 'linktype/' + link_type)
        self.security.check_marking(ctx, definition['marking'])
        self.security.check_marking(ctx, label)
        for endpoint, oid in [('source', source), ('target', target)]:
            obj = self.kernel.visible_object(db, ctx, manifest, oid)
            if obj['type'] != definition[endpoint]:
                raise S2Error('Link endpoint type mismatch')
        manifest['link/' + link_id] = self.store.put_revision(db, ctx.tenant, 'link',
            {'id': link_id, 'type': link_type, 'source': source, 'target': target, 'marking': marking(label)})
        return {'commit': self.store.publish(db, ctx, branch, expected_head, manifest)}

    def list_links(self, db, ctx, branch):
        head, manifest = self._head_manifest(db, ctx, branch)
        links = []
        for key in sorted(manifest):
            if not key.startswith('link/'):
                continue
            link = self.kernel.resource(db, ctx, manifest, key)
            definition = self.kernel.resource(db, ctx, manifest, 'linktype/' + link['type'])
            if not self.security.allowed(ctx, link['marking']) or not self.security.allowed(ctx, definition['marking']):
                continue
            try:
                self.kernel.visible_object(db, ctx, manifest, link['source'])
                self.kernel.visible_object(db, ctx, manifest, link['target'])
                links.append(link)
            except NotFound:
                pass
        return {'commit': head, 'links': links}

    def verify_audit(self, db, ctx):
        return self.security.verify(db, ctx.tenant)

    def restrict_policy(self, db, ctx, branch, resource, label):
        # This is live security policy, deliberately not a branch revision.
        if not isinstance(resource, str) or not resource:
            raise S2Error('Policy resource required')
        label = marking(label)
        self.security.check_marking(ctx, label)
        previous = self.security.effective(db, ctx, resource, None)
        if not dominates(label, previous):
            raise Forbidden('Policy downgrade denied')
        db.execute('INSERT INTO classification_overrides VALUES(?,?,?,?) '
                   'ON CONFLICT(tenant,project,resource) DO UPDATE SET label=excluded.label',
                   (ctx.tenant, ctx.project, resource, canonical(label)))
        return {'resource': resource, 'marking': label, 'scope': 'all-project-branches'}
