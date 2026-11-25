# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Bind S0.7 benchmark evidence into a disposable, local S2 project."""
import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'platform')]
sys.path[:0] = [str(p) for p in (ROOT / 'subsystems').iterdir() if p.is_dir()]
from s2.service import Service

TYPES = {
    'Collection': {'key': 'string', 'name': 'string'},
    'CodecBuild': {'key': 'string', 'encoder': 'string', 'decoder': 'string'},
    'EvidenceReport': {'key': 'string', 'path': 'string', 'sha256': 'string'},
    'CodecRun': {'key': 'string', 'series': 'string', 'frames': 'integer', 'misses': 'integer'},
}
RELATIONS = {'UsesCollection': 'Collection', 'UsesBuild': 'CodecBuild', 'CitesReport': 'EvidenceReport'}
CLIPS = ('4FRnNp', '5RSrbW', '6tLtjd', 'SCiKdG', 'ePoikf', 'vbrSzr')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def duration(value):
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError('invalid encoder time')
    return value


def load_evidence(repo):
    objects = {name: {} for name in TYPES}
    links = []
    def store(kind, key, **values):
        row = dict(key=key, **values)
        if objects[kind].setdefault(key, row) != row:
            raise ValueError(f'conflicting {kind} identity: {key}')
        return key

    def record(path):
        raw = (repo / path).read_bytes()
        sha = digest(raw)
        return raw, store('EvidenceReport', sha, path=path, sha256=sha)

    def add_run(key, series, times, threshold, collection, build, report):
        store('CodecRun', key, series=series, frames=len(times),
              misses=sum(t > threshold for t in times))
        links.extend([(key, 'UsesCollection', collection), (key, 'UsesBuild', build),
                      (key, 'CitesReport', report)])

    for clip in CLIPS:
        path = ('benchmarks/flir-4frnnp/run.json' if clip == CLIPS[0] else
                f'benchmarks/flir-six-clips/{clip}.json')
        raw, report = record(path)
        data = json.loads(raw)
        if (data['schema'], data['profile'], data['collection_id']) != (
                's07.codec-run.v1', 'fixed-2mbps', 'flirv_' + clip):
            raise ValueError(f'unexpected report identity: {path}')
        encoder, decoder = (data['binary_sha256'][k] for k in ('ewc_enc', 'ewc_dec'))
        manifest = data['manifest_sha256']
        if not all(re.fullmatch('[0-9a-f]{64}', h) for h in (encoder, decoder, manifest)):
            raise ValueError(f'invalid evidence hash: {path}')
        build = store('CodecBuild', digest((encoder + decoder).encode()), encoder=encoder, decoder=decoder)
        collection = store('Collection', manifest, name=data['collection_id'])
        metrics = data['metrics']
        threshold = duration(metrics['encoder_deadline_ms'])
        frames = metrics['frames']
        times = [duration(f['encode_ms']) for f in frames]
        if not threshold or not times or len(times) != data['frame_count']:
            raise ValueError(f'invalid frame count or deadline: {path}')
        add_run(report, 'baseline', times, threshold, collection, build, report)
        if objects['CodecRun'][report]['misses'] != metrics['deadline_misses']:
            raise ValueError(f'inconsistent deadline summary: {path}')
        if clip == 'ePoikf':
            raw_csv, csv_report = record('benchmarks/flir-epoikf-repeat/frame-times.csv')
            rows = list(csv.DictReader(io.StringIO(raw_csv.decode())))
            if len(rows) != len(frames) or any(
                    int(row['frame_index']) != i or row['file'] != frames[i]['file']
                    or duration(row['baseline_ms']) != times[i] for i, row in enumerate(rows)):
                raise ValueError('replay rows differ from baseline evidence')
            for series in ('repeat_1', 'repeat_2'):
                replay = [duration(row[series + '_ms']) for row in rows]
                add_run(csv_report + '-' + series, series, replay, threshold, collection, build, csv_report)
    return objects, links


def run(repo):
    objects, edges = load_evidence(repo)
    with tempfile.TemporaryDirectory(prefix='s2-imaging-') as tmp:
        service = Service(Path(tmp) / 'demo.db', storage_backend='json')
        token = service.bootstrap('Imaging laboratory', 3, ['ENG'])['token']
        branch = service.call(token, 'projects.create', name='Codec evidence')['branch']

        def call(op, **kw):
            return service.call(token, op, branch=branch, **kw)

        def change(op, **kw):
            return call(op, expected_head=call('branches.head')['commit'], **kw)

        ids = {}
        for kind, fields in TYPES.items():
            dataset = kind.lower()
            schema = {'properties': {k: {'type': v, 'required': True} for k, v in fields.items()}}
            change('datasets.create', dataset=dataset, primary_key='key', schema=schema)
            tx = change('datasets.begin', dataset=dataset, operation='APPEND',
                        idempotency_key='ingest-' + dataset)['transaction']
            call('datasets.stage', transaction=tx, rows=[{'values': r} for r in objects[kind].values()])
            call('datasets.commit', transaction=tx)
            change('ontology.define', name=kind, definition=schema)
            change('bindings.define', binding=dataset, dataset=dataset, object_type=kind,
                   namespace='imaging-' + dataset, mappings={k: k for k in fields})
            change('bindings.materialize', binding=dataset, idempotency_key='bind-' + dataset)
            ids[kind] = {o['values']['key']: o['id'] for o in call('objects.list', object_type=kind)['objects']}
        for name, kind in RELATIONS.items():
            change('ontology.link_type', name=name, source_type='CodecRun', target_type=kind)
        for key, name, target in edges:
            change('links.put', link_id=digest((key + name).encode()), link_type=name,
                   source=ids['CodecRun'][key], target=ids[RELATIONS[name]][target])

        # Answer from materialized, permission-filtered objects and links.
        records = {o['id']: o for o in call('objects.list')['objects']}
        related = {(e['source'], e['type']): records[e['target']]['values']
                   for e in call('links.list')['links']}
        rows = []
        for oid, obj in records.items():
            if obj['type'] == 'CodecRun':
                values = obj['values']
                rows.append({'collection': related[oid, 'UsesCollection']['name'],
                             'series': values['series'], 'frames': values['frames'],
                             'misses': values['misses'], 'build': related[oid, 'UsesBuild']['key'],
                             'report': related[oid, 'CitesReport']['path']})
        return sorted(rows, key=lambda r: (r['collection'], r['series']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('s07', type=Path, help='S0.7 checkout containing the baseline and replay reports')
    args = parser.parse_args()
    try:
        rows = run(args.s07)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, f'imaging evidence: {exc}\n')
    for row in rows:
        print('{collection} {series}: {misses}/{frames} misses | build {build:.12} | {report}'.format(**row))
    baseline = [r for r in rows if r['series'] == 'baseline']
    print(f"Baseline: {sum(r['misses'] for r in baseline)} misses / {sum(r['frames'] for r in baseline)} frames")
