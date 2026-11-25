# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
from contextlib import redirect_stdout
import io
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
                    self.assertEqual(result['model_interpretation'], {'cause': cause})
                    self.assertEqual(result['reviewer_answer']['reports'], ['original.json', 'times.csv'])
                    self.assertIn('cause is undetermined', result['reviewer_answer']['summary'])

    def test_reviewer_answer_uses_each_selected_run(self):
        self.rows[2].update(misses=3, frames=120, report='second.csv')
        packet, _ = assistant.evidence_packet(self.rows)
        answer = assistant.reviewer_answer(packet, 'replay')
        self.assertIn('original 17/150, replay 1 0/150, replay 2 3/120', answer['summary'])
        self.assertEqual(answer['reports'], ['original.json', 'times.csv', 'second.csv'])

    def test_review_only_reads_viewer_evidence_without_model_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'session.json').write_text('{}')
            for case in ('replay', 'cause'):
                output = io.StringIO()
                with patch.object(assistant, 'request') as api, patch.object(
                        assistant, 'query_project', return_value=self.rows) as query, redirect_stdout(output):
                    self.assertEqual(assistant.main([tmp, '--review-only', '--case', case]), 0)
                api.assert_not_called()
                query.assert_called_once_with(Path(tmp), {})
                packet, _ = assistant.evidence_packet(self.rows)
                self.assertEqual(json.loads(output.getvalue()), assistant.reviewer_answer(packet, case))

    def test_miss_list_excludes_replays_and_cites_each_original(self):
        self.rows[1]['misses'] = 99
        result = assistant.missed_collections(self.rows)
        self.assertEqual((result['original_collections'], result['total_misses'], result['total_frames']),
                         (6, 32, 900))
        self.assertEqual([(r['misses'], r['report']) for r in result['collections_with_misses']],
                         [(17, 'original.json'), (10, 'vbrSzr.json'), (4, '6tLtjd.json'), (1, 'SCiKdG.json')])

    def test_no_misses_or_replays(self):
        originals = [dict(row, misses=0) for row in self.rows if row['series'] == 'baseline']
        self.assertEqual(assistant.missed_collections(originals)['collections_with_misses'], [])
        with self.assertRaises(ValueError):
            assistant.missed_collections(originals[:-1])

    def test_collection_lookup_preserves_each_run_and_its_build(self):
        rows = [dict(row, build='build-' + row['series']) for row in reversed(self.rows)]
        result = assistant.collection_history(rows, 'flirv_ePoikf')
        self.assertEqual([(r['series'], r['misses'], r['build'], r['report']) for r in result['runs']],
                         [('baseline', 17, 'build-baseline', 'original.json'),
                          ('repeat_1', 0, 'build-repeat_1', 'times.csv'),
                          ('repeat_2', 0, 'build-repeat_2', 'times.csv')])
        single = assistant.collection_history(rows, 'flirv_4FRnNp')
        self.assertEqual(len(single['runs']), 1)
        self.assertEqual(single['runs'][0]['misses'], 0)
        with self.assertRaisesRegex(ValueError, 'no visible runs'):
            assistant.collection_history(rows, 'unseen')

    def test_build_history_filters_and_keeps_series_separate(self):
        rows = [dict(row, build='abc1') for row in self.rows]
        rows.append(dict(rows[0], build='abc2', misses=99))
        result = assistant.build_history(rows, 'abc1')
        self.assertEqual(result['totals_by_series']['baseline'], dict(runs=6, misses=32, frames=900))
        self.assertEqual(result['totals_by_series']['repeat_2'], dict(runs=1, misses=0, frames=150))
        self.assertEqual(len(result['runs']), 8)
        self.assertEqual(assistant.build_history(rows[:-1], 'abc'), result)
        for key in ('abc', 'missing', ''):
            with self.subTest(key=key), self.assertRaises(ValueError):
                assistant.build_history(rows, key)
