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
import errno
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'platform')]
sys.path[:0] = [str(p) for p in (ROOT / 'subsystems').iterdir() if p.is_dir()]
from s2.service import Service
from s2_platform import S2Error

RESOLUTION_ID = 'compression-input-resolution'
RESOLUTION_WORDING = 'an indicative input resolution of approximately 1920 × 1200 pixels'

TYPES = {
    'Requirement': {'key': 'string', 'wording': 'string'},
    'Collection': {'key': 'string', 'name': 'string', 'encoder_resolution': 'string'},
    'CodecBuild': {'key': 'string', 'encoder': 'string', 'decoder': 'string'},
    'EvidenceReport': {'key': 'string', 'path': 'string', 'sha256': 'string'},
    'CodecRun': {'key': 'string', 'series': 'string', 'frames': 'integer', 'misses': 'integer'},
}
RELATIONS = {'AddressesRequirement': 'Requirement', 'UsesCollection': 'Collection', 'UsesBuild': 'CodecBuild', 'CitesReport': 'EvidenceReport'}
CLIPS = ('4FRnNp', '5RSrbW', '6tLtjd', 'SCiKdG', 'ePoikf', 'vbrSzr')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def duration(value):
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError('invalid encoder time')
    return value


def encoder_resolution(args):
    dimensions = []
    for flag in ('-W', '-H'):
        if not isinstance(args, list) or args.count(flag) != 1:
            return 'unknown'
        index = args.index(flag) + 1
        if index >= len(args) or not re.fullmatch(r'[1-9][0-9]*', str(args[index])):
            return 'unknown'
        dimensions.append(str(args[index]))
    return 'x'.join(dimensions)


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
                      (key, 'CitesReport', report), (key, 'AddressesRequirement', RESOLUTION_ID)])

    store('Requirement', RESOLUTION_ID, wording=RESOLUTION_WORDING)
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
        collection = store('Collection', manifest, name=data['collection_id'],
                           encoder_resolution=encoder_resolution(data.get('encoder_args')))
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


def create_project(folder, objects, edges, enable_review=False):
    service = Service(folder / 'demo.db', storage_backend='json')
    token = service.bootstrap('Imaging laboratory', 3, ['ENG'])['token']
    project = service.call(token, 'projects.create', name='Codec evidence')
    branch = project['branch']

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

    reader = service.call(token, 'principals.create', role='viewer', clearance=3, compartments=['ENG'])
    service.call(token, 'projects.grant', project=project['project'], principal=reader['principal_id'])
    if enable_review:
        reviewer = service.call(token, 'principals.create', role='editor', clearance=3, compartments=['ENG'])
        service.call(token, 'projects.grant', project=project['project'], principal=reviewer['principal_id'])
        credential = folder / 'reviewer.token'
        credential.touch(mode=0o600)
        credential.write_text(reviewer['token'])
    return {'branch': branch, 'viewer_token': reader['token'],
            'source_reports': sorted(objects['EvidenceReport'])}


def query_project(folder, state):
    if not (folder / 'demo.db').is_file():
        raise ValueError('missing evidence database')
    service = Service(folder / 'demo.db', storage_backend='json')

    def call(op):
        return service.call(state['viewer_token'], op, branch=state['branch'])

    # Answer from materialized, permission-filtered objects and links.
    records = {o['id']: o for o in call('objects.list')['objects']}
    related = {(e['source'], e['type']): records[e['target']]['values']
               for e in call('links.list')['links']}
    rows = []
    for oid, obj in records.items():
        if obj['type'] == 'CodecRun':
            values = obj['values']
            rows.append({'collection': related[oid, 'UsesCollection']['name'],
                         'encoder_resolution': related[oid, 'UsesCollection'].get('encoder_resolution', 'unknown'),
                         'requirement': related.get((oid, 'AddressesRequirement')),
                         'series': values['series'], 'frames': values['frames'],
                         'misses': values['misses'], 'build': related[oid, 'UsesBuild']['key'],
                         'report': related[oid, 'CitesReport']['path'],
                         'report_sha256': related[oid, 'CitesReport']['sha256']})
    return sorted(rows, key=lambda r: (r['collection'], r['series']))


def import_project(repo, folder, enable_review=False):
    objects, edges = load_evidence(repo)
    # A completed snapshot is immutable. Build privately, then publish as one directory.
    if not folder.exists():
        folder.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=folder.parent, prefix='.s2-import-') as temp:
            stage = Path(temp)
            state = create_project(stage, objects, edges, enable_review)
            credentials = stage / 'session.json'
            credentials.touch(mode=0o600)
            credentials.write_text(json.dumps(state))
            try:
                stage.rename(folder)
            except OSError as exc:
                if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                    raise
    state = json.loads((folder / 'session.json').read_text())
    if enable_review and not (folder / 'reviewer.token').is_file():
        raise ValueError('choose a new state directory to enable reviewer access')
    if state['source_reports'] != sorted(objects['EvidenceReport']):
        raise ValueError('source reports differ from this saved snapshot; choose a new state directory')
    return query_project(folder, state)


def run(repo):
    with tempfile.TemporaryDirectory(prefix='s2-imaging-') as tmp:
        return import_project(repo, Path(tmp) / 'project')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('s07', nargs='?', type=Path, help='S0.7 checkout containing the baseline and replay reports')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--store', type=Path, help='create or reuse this local evidence snapshot')
    mode.add_argument('--query', type=Path, help='open a saved snapshot without the source checkout')
    parser.add_argument('--enable-review', action='store_true', help='provision a local editor credential in a new snapshot')
    args = parser.parse_args()
    if args.enable_review and not args.store:
        parser.error('--enable-review requires --store')
    if (args.s07 is None) != (args.query is not None):
        parser.error('provide either an S0.7 checkout or --query STATE')
    try:
        if args.query is not None:
            rows = query_project(args.query, json.loads((args.query / 'session.json').read_text()))
        elif args.store is not None:
            rows = import_project(args.s07, args.store, args.enable_review)
        else:
            rows = run(args.s07)
    except (ValueError, KeyError, OSError, S2Error) as exc:
        parser.exit(1, f'imaging evidence: {exc}\n')
    for row in rows:
        print('{collection} {series}: {misses}/{frames} misses | build {build:.12} | {report}'.format(**row))
    baseline = [r for r in rows if r['series'] == 'baseline']
    print(f"Baseline: {sum(r['misses'] for r in baseline)} misses / {sum(r['frames'] for r in baseline)} frames")
