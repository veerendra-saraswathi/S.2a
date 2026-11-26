# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Evaluate one local model on an imaging replay question using viewer evidence."""
from collections import Counter
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import sys
import time

from imaging_evidence_demo import CLIPS, RESOLUTION_ID, query_project, S2Error

QUESTION = 'Did the two ePoikf replays erase its original deadline misses?'


def original_runs(rows):
    baselines = [row for row in rows if row['series'] == 'baseline']
    if sorted(r['collection'] for r in baselines) != sorted('flirv_' + clip for clip in CLIPS):
        raise ValueError('this evaluation requires one visible original run per benchmark clip')

    return baselines


def resolution_coverage(rows):
    baselines = original_runs(rows)
    entries = []
    recorded_wordings = set()
    for row in baselines:
        requirement = row.get('requirement') or {}
        linked = requirement.get('key') == RESOLUTION_ID
        if linked:
            recorded_wordings.add(requirement['wording'])
        entries.append({'collection': row['collection'],
                        'encoder_resolution': row.get('encoder_resolution', 'unknown') if linked else 'unknown',
                        'baseline_report': row['report'], 'report_sha256': row['report_sha256'],
                        'replays': sorted(r['series'] for r in rows if r['collection'] == row['collection']
                                          and r['series'] != 'baseline')})
    if len(recorded_wordings) > 1:
        raise ValueError('conflicting saved wording for the resolution requirement')
    return {'requirement_id': RESOLUTION_ID, 'wording': next(iter(recorded_wordings), None),
            'acceptance': 'unassessed', 'original_collections': len(entries),
            'observed_encoder_resolutions': sorted({e['encoder_resolution'] for e in entries} - {'unknown'}),
            'collections': entries}


def measurement(row):
    return {key: row[key] for key in ('collection', 'misses', 'frames', 'report')}


def missed_collections(rows):
    originals = original_runs(rows)
    missed = sorted((row for row in originals if row['misses'] > 0),
                    key=lambda row: (-row['misses'], row['collection']))
    return {
        'original_collections': len(originals),
        'total_misses': sum(row['misses'] for row in originals),
        'total_frames': sum(row['frames'] for row in originals),
        'collections_with_misses': [measurement(row) for row in missed],
    }


def collection_history(rows, collection):
    selected = [row for row in rows if row['collection'] == collection]
    if not selected:
        raise ValueError(f'no visible runs for collection {collection!r}')
    fields = ('series', 'misses', 'frames', 'build', 'report')
    return {'collection': collection,
            'runs': [{key: row[key] for key in fields}
                     for row in sorted(selected, key=lambda row: row['series'])]}


def report_history(rows, path):
    selected = sorted((row for row in rows if row['report'] == path),
                      key=lambda row: (row['collection'], row['series']))
    if not selected:
        raise ValueError(f'no visible runs cite report {path!r}')
    fields = ('collection', 'series', 'misses', 'frames', 'build')
    return {'report': path, 'runs': [{key: row[key] for key in fields} for row in selected]}


def verify_report(rows, report, local_file):
    digests = {row['report_sha256'] for row in rows if row['report'] == report}
    if not digests:
        raise ValueError(f'no visible runs cite report {report!r}')
    if len(digests) != 1:
        raise ValueError(f'multiple recorded digests for report {report!r}')
    expected = digests.pop()
    digest = hashlib.sha256()
    with local_file.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    actual = digest.hexdigest()
    return {'recorded_sha256': expected, 'file_sha256': actual, 'matches': actual == expected}


def verify_reports(rows, root):
    root = root.resolve(strict=True)
    if not root.is_dir() or not rows:
        raise ValueError('verification requires a source directory and visible report citations')
    reports = []
    for report in sorted({row['report'] for row in rows}):
        item = {'report': report}
        try:
            path = (root / report).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError('expected a regular file within the source directory')
            item.update(verify_report(rows, report, path))
        except (OSError, RuntimeError, ValueError) as exc:
            item.update(matches=False, error=str(exc))
        reports.append(item)
    return {'visible_runs': len(rows), 'unique_reports': len(reports),
            'matched_reports': sum(item['matches'] for item in reports), 'reports': reports}


def build_history(rows, prefix):
    builds = {row['build'] for row in rows if prefix and row['build'].startswith(prefix)}
    if not builds:
        raise ValueError(f'no visible runs for build {prefix!r}')
    if len(builds) > 1:
        raise ValueError('ambiguous build prefix, supply more characters')
    build = builds.pop()
    selected = sorted((row for row in rows if row['build'] == build),
                      key=lambda row: (row['collection'], row['series']))
    totals = {}
    for row in selected:
        counts = totals.setdefault(row['series'], {'runs': 0, 'misses': 0, 'frames': 0})
        counts['runs'] += 1
        counts['misses'] += row['misses']
        counts['frames'] += row['frames']
    return {'build': build, 'totals_by_series': totals,
            'runs': [dict(measurement(row), series=row['series']) for row in selected]}


def evidence_packet(rows):
    baselines = original_runs(rows)
    focus = [row for row in rows if row['collection'] == 'flirv_ePoikf']
    series = {row['series']: row for row in focus}
    if len(focus) != 3 or set(series) != {'baseline', 'repeat_1', 'repeat_2'}:
        raise ValueError('the viewer needs one ePoikf baseline and both replay records')
    original = series['baseline']
    replays = {key: measurement(series[key]) for key in ('repeat_1', 'repeat_2')}
    packet = {
        'selected_clip_original_run': measurement(original),
        'selected_clip_replays': replays,
        'all_six_original_runs': {
            'misses': sum(row['misses'] for row in baselines),
            'frames': sum(row['frames'] for row in baselines),
            'reports': [row['report'] for row in sorted(baselines, key=lambda r: r['collection'])],
        },
    }
    expected = {
        'original_observation': 'preserved',
        'both_replays_had_zero_misses': all(row['misses'] == 0 for row in replays.values()),
    }
    return packet, expected


def reviewer_answer(packet, case):
    original = packet['selected_clip_original_run']
    replays = packet['selected_clip_replays']
    runs = [original, replays['repeat_1'], replays['repeat_2']]
    observations = ', '.join(
        f"{label} {run['misses']}/{run['frames']}"
        for label, run in zip(('original', 'replay 1', 'replay 2'), runs)
    )
    conclusion = ('The cause is undetermined from these counts.' if case == 'cause'
                  else 'The original observation is preserved.')
    return {
        'summary': f"{conclusion} {original['collection']} deadline misses/frames: {observations}.",
        'reports': list(dict.fromkeys(run['report'] for run in runs)),
    }


def request(path, payload=None):
    conn = http.client.HTTPConnection('127.0.0.1', 11434, timeout=180)
    try:
        conn.request('GET' if payload is None else 'POST', path,
                     body=None if payload is None else json.dumps(payload),
                     headers={'Content-Type': 'application/json'})
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError(f'Ollama {path} returned HTTP {response.status}: {response.read(400).decode(errors="replace")}')
        return json.load(response)
    finally:
        conn.close()


def read_rows(folder):
    state = json.loads((folder / 'session.json').read_text())
    return query_project(folder, state)


def read_evidence(folder):
    return evidence_packet(read_rows(folder))


def evaluate(folder, model, case='replay'):
    packet, expected = read_evidence(folder)
    installed = {m['name']: m['digest'] for m in request('/api/tags')['models']}
    if model not in installed:
        raise ValueError(f'{model} is unavailable locally; check ollama list')
    if case == 'replay':
        question = QUESTION
        instructions = ('Interpret the selected clip records. Each record is a separate observation. '
                        'Choose whether the original observation was preserved or erased, and whether both '
                        'replays recorded zero misses.')
        properties = {
            'original_observation': {'type': 'string', 'enum': ['preserved', 'erased']},
            'both_replays_had_zero_misses': {'type': 'boolean'},
        }
    elif case == 'cause':
        question = 'Which cause of the ePoikf deadline miss pattern can these measurements establish?'
        instructions = 'Select a cause only if the supplied measurements establish it.'
        properties = {'cause': {'type': 'string', 'enum': [
            'cpu_scheduling', 'thermal_conditions', 'frame_content', 'undetermined']}}
        expected = {'cause': 'undetermined'}
    else:
        raise ValueError(f'unknown evaluation case: {case}')
    schema = {'type': 'object', 'properties': properties,
              'required': list(properties), 'additionalProperties': False}
    prompt = '\n'.join((question, instructions, json.dumps(packet)))
    start = time.monotonic()
    response = request('/api/chat', {'model': model, 'stream': False, 'format': schema,
                                     'options': {'temperature': 0, 'num_predict': 512},
                                     'messages': [{'role': 'user', 'content': prompt}]})
    raw = response['message']['content']
    try:
        answer = json.loads(raw)
    except json.JSONDecodeError:
        answer = raw
    return {'question': question, 'model': model, 'model_digest': installed[model],
            'elapsed_seconds': round(time.monotonic() - start, 2),
            'passed': response.get('done') is True and json.dumps(answer, sort_keys=True) == json.dumps(expected, sort_keys=True),
            'reviewer_answer': reviewer_answer(packet, case),
            'verified_measurements': packet, 'model_interpretation': answer,
            'expected_interpretation': expected}


def review_summary(rows, audit):
    coverage = resolution_coverage(rows)
    originals = missed_collections(rows)
    sizes = Counter(item['encoder_resolution'] for item in coverage['collections'])
    lines = [
        "Resolution requirement: " + (coverage['wording'] or 'wording unavailable in snapshot'),
        "Recorded encoder dimensions (original collections): " +
        ', '.join(f'{size} ({count})' for size, count in sorted(sizes.items())),
        f"Resolution acceptance: {coverage['acceptance']}",
        f"Original deadline misses: {originals['total_misses']} / {originals['total_frames']} frames",
    ]
    for row in rows:
        if row['series'] != 'baseline':
            lines.append(f"{row['collection']} {row['series']}: {row['misses']} / {row['frames']} deadline misses/frames")
    lines.append(f"Source reports matching snapshot: {audit['matched_reports']} / {audit['unique_reports']}")
    for report in audit['reports']:
        if not report['matches']:
            lines.append(f"Verification failed: {report['report']}")
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('state', type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--review-only', action='store_true', help='print the cited viewer answer without running Ollama')
    mode.add_argument('--model', default='mistral:latest')
    mode.add_argument('--list-misses', action='store_true', help='list original runs with deadline misses')
    mode.add_argument('--collection', metavar='ID', help='show visible runs and evidence for one collection')
    mode.add_argument('--build', metavar='ID', help='show visible runs for a build key or unique prefix')
    mode.add_argument('--report', metavar='PATH', help='show visible runs citing the exact recorded report path')
    mode.add_argument('--requirement', choices=[RESOLUTION_ID], help='show recorded encoder dimensions by collection')
    mode.add_argument('--verify-reports', type=Path, metavar='SOURCE', help='verify all cited files under SOURCE')
    mode.add_argument('--summary', type=Path, metavar='SOURCE', help='print coverage, timing and file verification')
    parser.add_argument('--case', choices=('replay', 'cause'))
    parser.add_argument('--verify-file', type=Path, help='compare local file bytes with the selected report digest')
    args = parser.parse_args(argv)
    if args.verify_file is not None and args.report is None:
        parser.error('--verify-file requires --report')
    if (args.list_misses or args.collection is not None or args.build is not None or args.report is not None or args.requirement or args.verify_reports or args.summary) and args.case:
        parser.error('--case applies only to a replay or cause question')
    case = args.case or 'replay'
    try:
        if args.verify_reports or args.summary:
            rows = read_rows(args.state)
            result = verify_reports(rows, args.verify_reports or args.summary)
            print(review_summary(rows, result) if args.summary else json.dumps(result, indent=2))
            return 0 if result['matched_reports'] == result['unique_reports'] else 2
        if args.requirement:
            print(json.dumps(resolution_coverage(read_rows(args.state)), indent=2))
            return 0
        if args.report is not None:
            rows = read_rows(args.state)
            result = report_history(rows, args.report)
            if args.verify_file is not None:
                result['verification'] = verify_report(rows, args.report, args.verify_file)
            print(json.dumps(result, indent=2))
            return 2 if args.verify_file is not None and not result['verification']['matches'] else 0
        if args.build is not None:
            print(json.dumps(build_history(read_rows(args.state), args.build), indent=2))
            return 0
        if args.collection is not None:
            print(json.dumps(collection_history(read_rows(args.state), args.collection), indent=2))
            return 0
        if args.list_misses:
            print(json.dumps(missed_collections(read_rows(args.state)), indent=2))
            return 0
        if args.review_only:
            packet, _ = read_evidence(args.state)
            print(json.dumps(reviewer_answer(packet, case), indent=2))
            return 0
        result = evaluate(args.state, args.model, case)
    except (OSError, ValueError, KeyError, S2Error, http.client.HTTPException) as exc:
        parser.exit(1, f'imaging assistant: {exc}\n')
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 2


if __name__ == '__main__':
    sys.exit(main())
