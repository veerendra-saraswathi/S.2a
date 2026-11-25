# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Evaluate one local model on an imaging replay question using viewer evidence."""
import argparse
import http.client
import json
from pathlib import Path
import sys
import time

from imaging_evidence_demo import CLIPS, query_project, S2Error

QUESTION = 'Did the two ePoikf replays erase its original deadline misses?'


def evidence_packet(rows):
    baselines = [row for row in rows if row['series'] == 'baseline']
    focus = [row for row in rows if row['collection'] == 'flirv_ePoikf']
    series = {row['series']: row for row in focus}
    if len(focus) != 3 or set(series) != {'baseline', 'repeat_1', 'repeat_2'}:
        raise ValueError('the viewer needs one ePoikf baseline and both replay records')
    if sorted(r['collection'] for r in baselines) != sorted('flirv_' + clip for clip in CLIPS):
        raise ValueError('this evaluation requires one visible original run per benchmark clip')

    def measurement(row):
        return {key: row[key] for key in ('collection', 'misses', 'frames', 'report')}

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


def read_evidence(folder):
    state = json.loads((folder / 'session.json').read_text())
    return evidence_packet(query_project(folder, state))


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('state', type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--review-only', action='store_true', help='print the cited viewer answer without running Ollama')
    mode.add_argument('--model', default='mistral:latest')
    parser.add_argument('--case', choices=('replay', 'cause'), default='replay')
    args = parser.parse_args(argv)
    try:
        if args.review_only:
            packet, _ = read_evidence(args.state)
            print(json.dumps(reviewer_answer(packet, args.case), indent=2))
            return 0
        result = evaluate(args.state, args.model, args.case)
    except (OSError, ValueError, KeyError, S2Error, http.client.HTTPException) as exc:
        parser.exit(1, f'imaging assistant: {exc}\n')
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 2


if __name__ == '__main__':
    sys.exit(main())
