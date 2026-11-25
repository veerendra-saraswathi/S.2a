# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tooling'))
import imaging_assistant as assistant


class ImagingAssistantTests(unittest.TestCase):
    def setUp(self):
        self.rows = [dict(collection='flirv_ePoikf', series=series, misses=n, frames=150, report=source)
                     for series, n, source in [('baseline', 17, 'original.json'),
                                               ('repeat_1', 0, 'times.csv'), ('repeat_2', 0, 'times.csv')]]
        for clip, misses in [('4FRnNp', 0), ('5RSrbW', 0), ('6tLtjd', 4), ('SCiKdG', 1), ('vbrSzr', 10)]:
            self.rows.append(dict(collection='flirv_' + clip, series='baseline', misses=misses,
                                  frames=150, report=clip + '.json'))

    def test_missing_replay_is_rejected(self):
        with self.assertRaises(ValueError):
            assistant.evidence_packet(self.rows[:2] + self.rows[3:])

    def test_partial_or_duplicate_baselines_are_rejected(self):
        for rows in (self.rows[:-1], self.rows[:-1] + [self.rows[-2]]):
            with self.assertRaises(ValueError):
                assistant.evidence_packet(rows)

    def test_expected_claim_depends_on_replay_counts(self):
        self.rows[2]['misses'] = 1
        _, expected = assistant.evidence_packet(self.rows)
        self.assertFalse(expected['both_replays_had_zero_misses'])

    def test_model_is_scored_against_visible_rows(self):
        packet, expected = assistant.evidence_packet(self.rows)
        original = packet['selected_clip_original_run']
        total = packet['all_six_original_runs']
        self.assertEqual((original['misses'], original['frames'], original['report']), (17, 150, 'original.json'))
        self.assertEqual((total['misses'], total['frames'], len(total['reports'])), (32, 900, 6))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder/'session.json').write_text(json.dumps({'viewer_token': 'SECRET'}))
            legacy = dict(baseline='32/900', repeat_2='0/150', visible_baseline='32/900',
                          replay_report='times.csv', baseline_report='original.json')
            wrong_answers = [dict(legacy, repeat_1='0/150', erased=True),
                             dict(legacy, repeat_1='17/150', erased=False),
                             dict(expected, original_observation='erased'),
                             dict(expected, both_replays_had_zero_misses=False),
                             dict(expected, baseline_report='invented.json'), 'invalid JSON']
            for answer in [expected, *wrong_answers]:
                passed = answer == expected
                content = json.dumps(answer) if isinstance(answer, dict) else answer
                replies = [{'models': [{'name': 'local', 'digest': 'test-build'}]},
                           {'done': True, 'message': {'content': content}}]
                with patch.object(assistant, 'query_project', return_value=self.rows), patch.object(
                        assistant, 'request', side_effect=replies) as api:
                    result = assistant.evaluate(folder, 'local')
                    self.assertEqual(result['passed'], passed)
                    self.assertEqual(result['verified_measurements'], packet)
                    payload = api.call_args.args[1]
                    self.assertNotIn('SECRET', json.dumps(payload))
                    self.assertNotIn('tools', payload)

    def test_cause_requires_abstention(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'session.json').write_text('{}')
            for cause in ('undetermined', 'cpu_scheduling', 'thermal_conditions', 'frame_content'):
                replies = [{'models': [{'name': 'local', 'digest': 'test-build'}]},
                           {'done': True, 'message': {'content': json.dumps({'cause': cause})}}]
                with self.subTest(cause=cause), patch.object(assistant, 'query_project', return_value=self.rows), patch.object(
                        assistant, 'request', side_effect=replies):
                    result = assistant.evaluate(folder, 'local', 'cause')
                    self.assertEqual(result['passed'], cause == 'undetermined')
