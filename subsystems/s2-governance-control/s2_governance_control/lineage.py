# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Bounded, authorized graph of recorded object and dataset provenance."""
import json
from s2_platform import Conflict, NotFound, S2Error, digest


class Lineage:
    def __init__(self, store, security, kernel):
        self.store, self.security, self.kernel = store, security, kernel

    def trace(self, db, ctx, branch, expected_head, resource, max_nodes=100, max_depth=32):
        if (type(max_nodes) is not int or not 1 <= max_nodes <= 200 or
                type(max_depth) is not int or not 1 <= max_depth <= 32):
            raise S2Error('Lineage limits: 1-200 nodes and 1-32 dataset depth')
        if not isinstance(resource, str) or not resource.startswith(('dataset/', 'object/')):
            raise S2Error('Root must be dataset/<name> or object/<id>')
        current = self.store.branch(db, ctx.tenant, branch)
        if current['head'] != expected_head:
            raise Conflict('Stale branch head')
        manifest = self.store.manifest(db, ctx.tenant, expected_head)
        if resource not in manifest:
            raise NotFound('Lineage root not found')
        nodes, edges, active, visited = {}, set(), set(), {}

        def add(kind, key, revision, **metadata):
            identity = digest({'kind': kind, 'resource': key, 'revision': revision})
            if identity not in nodes:
                if len(nodes) >= max_nodes:
                    raise S2Error('Lineage node limit exceeded; no partial graph returned')
                nodes[identity] = {'id': identity, 'kind': kind, 'resource': key,
                                   'revision': revision, **metadata}
            return identity

        def edge(source, target, relation):
            edges.add((source, target, relation))

        def history(commit):
            row = db.execute('SELECT project,manifest FROM commits WHERE tenant=? AND id=?',
                             (ctx.tenant, commit)).fetchone()
            if row is None or row['project'] != current['project']:
                raise Conflict('Recorded provenance is outside this project or missing')
            return json.loads(row['manifest'])

        def definition(kind, name, revision):
            key = kind + '/' + name
            value = self.kernel.resource(db, ctx, {key: revision}, key)
            if kind == 'type':
                self.kernel.check_definition_access(ctx, value)
            else:
                label = self.security.effective(db, ctx, key, value.get('marking'))
                self.security.check_marking(ctx, label)
            return add(kind, key, revision, matches_branch_head=manifest.get(key) == revision), value

        def dataset(name, revision, depth=1):
            if depth > max_depth:
                raise S2Error('Lineage depth limit exceeded; no partial graph returned')
            key = 'dataset/' + name
            identity = (key, revision)
            if identity in active:
                raise Conflict('Recorded provenance contains a cycle')
            if identity in visited:
                return visited[identity]
            value = self.kernel.resource(db, ctx, {key: revision}, key)
            self.kernel.check_definition_access(ctx, value['schema'])
            for row in value['rows']:
                self.security.check_marking(ctx, row['marking'])
            node = add('dataset', key, revision, rows=len(value['rows']),
                       matches_branch_head=manifest.get(key) == revision,
                       provenance='recorded_pipeline' if 'source_pipeline' in value else 'source_snapshot')
            active.add(identity)
            provenance = value.get('source_pipeline')
            if provenance:
                historical = history(provenance['base_commit'])
                if (historical.get('dataset/' + provenance['input']) != provenance['input_revision'] or
                        historical.get('pipeline/' + provenance['pipeline']) != provenance['pipeline_revision']):
                    raise Conflict('Recorded pipeline pins disagree with their base commit')
                pipeline_node, spec = definition('pipeline', provenance['pipeline'], provenance['pipeline_revision'])
                if spec['input'] != provenance['input'] or spec['output'] != name:
                    raise Conflict('Recorded pipeline references disagree')
                upstream = dataset(provenance['input'], provenance['input_revision'], depth + 1)
                derivation = add('pipeline_derivation', key, revision,
                                 pipeline=provenance['pipeline'], base_commit=provenance['base_commit'])
                edge(pipeline_node, derivation, 'definition')
                edge(upstream, derivation, 'recorded_input')
                edge(derivation, node, 'recorded_output')
            active.remove(identity)
            visited[identity] = node
            return node

        if resource.startswith('dataset/'):
            root = dataset(resource[8:], manifest[resource])
        else:
            obj = self.kernel.resource(db, ctx, manifest, resource)
            self.security.check_marking(ctx, obj['marking'])
            current_type = self.kernel.resource(db, ctx, manifest, 'type/' + obj['type'])
            self.kernel.check_definition_access(ctx, current_type)
            root = add('object', resource, manifest[resource], object_type=obj['type'],
                       source_presence=obj.get('source_state', {}).get('presence', 'UNRECORDED'),
                       matches_branch_head=True)
            provenance = obj.get('source_binding')
            if provenance:
                historical = history(provenance['base_commit'])
                if historical.get('dataset/' + provenance['dataset']) != provenance['dataset_revision']:
                    raise Conflict('Recorded binding source disagrees with its base commit')
                binding_node, spec = definition('binding', provenance['binding'], provenance['binding_revision'])
                if spec['dataset'] != provenance['dataset'] or spec['object_type'] != obj['type']:
                    raise Conflict('Recorded binding references disagree')
                type_node, _ = definition('type', obj['type'], provenance['type_revision'])
                source = dataset(provenance['dataset'], provenance['dataset_revision'])
                edge(source, binding_node, 'recorded_source')
                edge(binding_node, root, 'materialized_from')
                edge(type_node, root, 'materialization_schema')
            else:
                type_node, _ = definition('type', obj['type'], manifest['type/' + obj['type']])
                edge(type_node, root, 'current_schema')
        return {'branch': branch, 'commit': expected_head, 'root': root,
                'semantics': 'recorded_provenance', 'complete': True,
                'nodes': sorted(nodes.values(), key=lambda n: n['id']),
                'edges': [{'source': a, 'target': b, 'relation': c} for a, b, c in sorted(edges)]}
