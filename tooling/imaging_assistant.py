# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Evaluate one local model on the imaging replay question using viewer evidence."""
import argparse
import http.client
import json
from pathlib import Path
import sys
import time

from imaging_evidence_demo import query_project, S2Error

QUESTION = 'Did the two ePoikf replays erase its original deadline misses?'


def evidence_packet(rows):
    baselines = [row for row in rows if row['series'] == 'baseline']
    focus = [row for row in rows if row['collection'] == 'flirv_ePoikf']
    series = {row['series']: row for row in focus}
    if len(focus) != 3 or set(series) != {'baseline', 'repeat_1', 'repeat_2'}:
        raise ValueError('the viewer needs one ePoikf baseline and both replay records')
    packet = {'visible_baseline': f"{sum(r['misses'] for r in baselines)}/{sum(r['frames'] for r in baselines)}",
              'runs': [{'series': key, 'misses_over_frames': f"{row['misses']}/{row['frames']}",
                        'report': row['report']} for key, row in sorted(series.items())]}
    expected = {r['series']: r['misses_over_frames'] for r in packet['runs']}
    expected.update(erased=False, visible_baseline=packet['visible_baseline'],
                    baseline_report=series['baseline']['report'], replay_report=series['repeat_1']['report'])
    if series['repeat_2']['report'] != expected['replay_report']:
        raise ValueError('this evaluation expects both replays to cite one timing report')
    return packet, expected


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


def evaluate(folder, model):
    state = json.loads((folder / 'session.json').read_text())
    packet, expected = evidence_packet(query_project(folder, state))
    installed = {m['name']: m['digest'] for m in request('/api/tags')['models']}
    if model not in installed:
        raise ValueError(f'{model} is unavailable locally; check ollama list')
    schema = {'type': 'object', 'properties': {
        k: {'type': 'boolean' if k == 'erased' else 'string'} for k in expected},
        'required': list(expected), 'additionalProperties': False}
    prompt = ('Answer the question using only the supplied evidence. Copy each misses/frames ratio and '
              'the full baseline and replay report paths exactly. Separate runs preserve separate observations.\n'
              + QUESTION + '\n' + json.dumps(packet))
    start = time.monotonic()
    response = request('/api/chat', {'model': model, 'stream': False, 'format': schema,
                                     'options': {'temperature': 0, 'num_predict': 512},
                                     'messages': [{'role': 'user', 'content': prompt}]})
    raw = response['message']['content']
    try:
        answer = json.loads(raw)
    except json.JSONDecodeError:
        answer = raw
    return {'question': QUESTION, 'model': model, 'model_digest': installed[model],
            'elapsed_seconds': round(time.monotonic() - start, 2),
            'passed': response.get('done') is True and json.dumps(answer, sort_keys=True) == json.dumps(expected, sort_keys=True),
            'model_answer': answer, 'expected_from_viewer_query': expected}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('state', type=Path)
    parser.add_argument('--model', default='mistral:latest')
    args = parser.parse_args()
    try:
        result = evaluate(args.state, args.model)
    except (OSError, ValueError, KeyError, S2Error, http.client.HTTPException) as exc:
        parser.exit(1, f'imaging assistant: {exc}\n')
    print(json.dumps(result, indent=2))
    sys.exit(0 if result['passed'] else 2)
