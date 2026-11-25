# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tooling'))
from imaging_evidence_demo import CLIPS, load_evidence, run


class ImagingEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for i, clip in enumerate(CLIPS):
            path = ('flir-4frnnp/run.json' if i == 0 else f'flir-six-clips/{clip}.json')
            path = self.root / 'benchmarks' / path
            path.parent.mkdir(exist_ok=True, parents=True)
            record = {'schema': 's07.codec-run.v1', 'profile': 'fixed-2mbps',
                      'collection_id': 'flirv_' + clip, 'manifest_sha256': f'{i:064x}',
                      'binary_sha256': {'ewc_enc': 'a'*64, 'ewc_dec': 'b'*64},
                      'frame_count': 1, 'metrics': {'encoder_deadline_ms': 10,
                      'deadline_misses': 1, 'frames': [{'file': '000.raw', 'encode_ms': 11}]}}
            path.write_text(json.dumps(record))
        self.csv = self.root / 'benchmarks/flir-epoikf-repeat/frame-times.csv'
        self.csv.parent.mkdir()
        self.csv.write_text('frame_index,file,baseline_ms,repeat_1_ms,repeat_2_ms\n0,000.raw,11,10,5\n')

    def test_materialized_runs_keep_baseline_and_repeat_distinct(self):
        rows = run(self.root)
        self.assertEqual(len(rows), 8)
        self.assertEqual(len({r['build'] for r in rows}), 1)
        epoikf = [r for r in rows if r['collection'] == 'flirv_ePoikf']
        self.assertEqual([(r['series'], r['misses']) for r in epoikf],
                         [('baseline', 1), ('repeat_1', 0), ('repeat_2', 0)])
        self.assertEqual(len({r['report'] for r in epoikf}), 2)

    def test_mismatched_replay_is_rejected(self):
        self.csv.write_text(self.csv.read_text().replace('000.raw', 'wrong.raw'))
        with self.assertRaisesRegex(ValueError, 'baseline evidence'):
            load_evidence(self.root)

    def test_reimport_and_query_from_another_process(self):
        import subprocess
        cli = Path(__file__).resolve().parents[1] / 'tooling' / 'imaging_evidence_demo.py'
        state = self.root / 'saved'
        command = [sys.executable, str(cli), str(self.root), '--store', str(state)]
        first = subprocess.check_output(command)
        identity = (state / 'session.json').read_bytes()
        self.assertEqual(subprocess.check_output(command), first)
        self.assertEqual((state / 'session.json').read_bytes(), identity)
        self.csv.write_text(self.csv.read_text().replace(',10,5', ',11,5'))
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        (self.root / 'benchmarks').rename(self.root / 'unavailable')
        result = subprocess.check_output([sys.executable, str(cli), '--query', str(state)])
        self.assertEqual(result, first)
        self.assertEqual(result.count(b' | build '), 8)
