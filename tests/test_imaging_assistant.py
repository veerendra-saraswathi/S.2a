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
        self.rows.append(dict(collection='other', series='baseline', misses=1, frames=20, report='other.json'))

    def test_missing_replay_is_rejected(self):
        with self.assertRaises(ValueError):
            assistant.evidence_packet(self.rows[1:])

    def test_model_is_scored_against_visible_rows(self):
        packet, expected = assistant.evidence_packet(self.rows)
        self.assertEqual(packet['visible_baseline'], '18/170')
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder/'session.json').write_text(json.dumps({'viewer_token': 'SECRET'}))
            bad_citation = dict(expected, replay_report='invented.csv')
            for answer, passed in [(expected, True), (bad_citation, False), ('invalid JSON', False)]:
                content = json.dumps(answer) if isinstance(answer, dict) else answer
                replies = [{'models': [{'name': 'local', 'digest': 'test-build'}]},
                           {'done': True, 'message': {'content': content}}]
                with patch.object(assistant, 'query_project', return_value=self.rows), patch.object(
                        assistant, 'request', side_effect=replies) as api:
                    result = assistant.evaluate(folder, 'local')
                    self.assertEqual(result['passed'], passed)
                    payload = api.call_args.args[1]
                    self.assertNotIn('SECRET', json.dumps(payload))
                    self.assertNotIn('tools', payload)
