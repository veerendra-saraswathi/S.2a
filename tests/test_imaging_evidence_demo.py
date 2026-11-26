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

    def test_resolution_coverage_after_reimport(self):
        from imaging_assistant import resolution_coverage
        from imaging_evidence_demo import import_project
        for clip, dims in [('4frnnp/run', ['640', '512']), ('six-clips/ePoikf', ['1920', '1200'])]:
            path = self.root / f'benchmarks/flir-{clip}.json'
            data = json.loads(path.read_text())
            data['encoder_args'] = ['-W', dims[0], '-H', dims[1]]
            path.write_text(json.dumps(data))
        folder = self.root / 'saved'
        first = resolution_coverage(import_project(self.root, folder))
        self.assertEqual(first, resolution_coverage(import_project(self.root, folder)))
        self.assertEqual(first['original_collections'], 6)
        self.assertEqual(first['observed_encoder_resolutions'], ['1920x1200', '640x512'])
        by_name = {r['collection']: r for r in first['collections']}
        self.assertEqual(by_name['flirv_ePoikf']['replays'], ['repeat_1', 'repeat_2'])
        self.assertEqual(by_name['flirv_5RSrbW']['encoder_resolution'], 'unknown')

    def test_review_pins_evidence_and_requires_editor(self):
        from imaging_evidence_demo import import_project
        from imaging_review_decision import ReviewService
        from s2_platform import Conflict, Forbidden
        folder = self.root / 'review'
        import_project(self.root, folder, enable_review=True)
        state = json.loads((folder / 'session.json').read_text())
        db = folder / 'demo.db'
        service = ReviewService(db)
        branch = state['branch']
        with self.assertRaises(Forbidden):
            service.call(state['viewer_token'], 'review.open', branch=branch, note='Needs review')
        token = (folder / 'reviewer.token').read_text()
        def record(note):
            return service.call(token, 'review.open', branch=branch, note=note)
        receipt = record('Needs review')
        self.assertEqual(record('Needs review'), receipt)
        with self.assertRaises(Conflict):
            record('Changed')
        self.assertEqual(len([ref for ref in receipt['evidence'] if 'sha256' in ref]), 7)
        self.assertEqual(ReviewService(db).call(state['viewer_token'], 'review.read', branch=branch), receipt)

    def test_new_evidence_preserves_prior_review(self):
        import hashlib
        import subprocess
        from imaging_assistant import resolution_coverage
        from imaging_evidence_demo import import_project

        cli = Path(__file__).resolve().parents[1] / 'tooling' / 'imaging_review_decision.py'
        report = self.root / 'benchmarks/flir-4frnnp/run.json'
        data = json.loads(report.read_text())
        data['encoder_args'] = ['-W', '640', '-H', '512']
        report.write_text(json.dumps(data))
        old_digest = hashlib.sha256(report.read_bytes()).hexdigest()
        old = self.root / 'original'
        import_project(self.root, old, enable_review=True)
        read_old = [sys.executable, str(cli), str(old)]
        receipt = subprocess.check_output(read_old + ['--credential', str(old / 'reviewer.token'),
                                                      '--open', 'Additional resolution evidence required.'])
        saved = json.loads(receipt)
        self.assertEqual(saved['status'], 'open')
        references = {ref['path']: ref['sha256'] for ref in saved['evidence'] if 'path' in ref}
        self.assertEqual(references['benchmarks/flir-4frnnp/run.json'], old_digest)

        data['encoder_args'] = ['-W', '1920', '-H', '1200']
        data['manifest_sha256'] = 'f' * 64
        report.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'source reports differ'):
            import_project(self.root, old)
        new = self.root / 'subsequent'
        coverage = resolution_coverage(import_project(self.root, new, enable_review=True))
        updated = next(row for row in coverage['collections'] if row['collection'] == 'flirv_4FRnNp')
        self.assertEqual(updated['encoder_resolution'], '1920x1200')
        self.assertNotEqual(updated['report_sha256'], old_digest)
        self.assertEqual(coverage['acceptance'], 'unassessed')
        self.assertEqual(json.loads(subprocess.check_output([sys.executable, str(cli), str(new)]))['status'],
                         'unrecorded')
        self.assertEqual(subprocess.check_output(read_old), receipt)

        same = json.loads(subprocess.check_output(read_old + ['--compare-evidence', str(old)]))
        self.assertEqual(same['matching_reports'], 7)
        comparison = json.loads(subprocess.check_output(read_old + ['--compare-evidence', str(new)]))
        self.assertTrue(comparison['wording_matches'])
        self.assertEqual(comparison['matching_reports'], 6)
        self.assertEqual(comparison['only_in_receipt'], [dict(path=updated['baseline_report'], sha256=old_digest)])
        self.assertEqual(comparison['only_in_comparison'],
                         [dict(path=updated['baseline_report'], sha256=updated['report_sha256'])])
        self.assertEqual(subprocess.check_output(read_old), receipt)

    def test_reopened_receipt_respects_current_field_permissions(self):
        from unittest.mock import patch
        from imaging_evidence_demo import Service, import_project
        from imaging_review_decision import ReviewService
        from s2_platform import Forbidden

        bootstrap = Service.bootstrap
        admins = []
        def provision(service, tenant, level, compartments):
            admin = bootstrap(service, tenant, level, compartments + ['REVIEW'])
            admins.append(admin['token'])
            return admin

        folder = self.root / 'policy-test'
        with patch.object(Service, 'bootstrap', provision):
            import_project(self.root, folder, enable_review=True)
        state = json.loads((folder / 'session.json').read_text())
        service = ReviewService(folder / 'demo.db')
        scope = {'branch': state['branch']}
        receipt = service.call((folder / 'reviewer.token').read_text(), 'review.open', note='Open', **scope)
        self.assertEqual(service.call(state['viewer_token'], 'review.read', **scope), receipt)
        service.call(admins[0], 'policy.restrict', resource='type/EvidenceReport/property/path',
                     label={'compartments': ['REVIEW']}, **scope)
        with self.assertRaises(Forbidden):
            service.call(state['viewer_token'], 'review.read', **scope)
        self.assertEqual(service.call(admins[0], 'review.read', **scope), receipt)

    def test_extra_report_extends_only_the_new_snapshot(self):
        import subprocess
        from imaging_assistant import compare_runs, resolution_coverage, evidence_packet, read_rows
        from imaging_evidence_demo import import_project
        original = self.root / 'original'
        original_rows = import_project(self.root, original, enable_review=True)
        review_cli = Path(__file__).resolve().parents[1] / 'tooling' / 'imaging_review_decision.py'
        read_receipt = [sys.executable, str(review_cli), str(original)]
        receipt = subprocess.check_output(read_receipt + ['--credential', str(original / 'reviewer.token'),
                                                         '--open', 'Coverage remains open'])
        extra = 'benchmarks/synthetic-extra.json'
        data = json.loads((self.root / 'benchmarks/flir-4frnnp/run.json').read_text())
        data.update(collection_id='synthetic-extra', manifest_sha256='f'*64,
                    encoder_args=['-W', '1920', '-H', '1200'])
        (self.root / extra).write_text(json.dumps(data))
        cli = review_cli.with_name('imaging_evidence_demo.py')
        new = self.root / 'extended'
        command = [sys.executable, str(cli), str(self.root), '--extra-report', extra, '--store', str(new)]
        self.assertEqual(subprocess.check_output(command), subprocess.check_output(command))
        rows = read_rows(new)
        self.assertEqual(resolution_coverage(rows)['original_collections'], 7)
        self.assertEqual(evidence_packet(rows)[0]['all_six_original_runs']['frames'], 6)
        diff = compare_runs(original_rows, rows)
        self.assertEqual(diff['unchanged_runs'], 8)
        self.assertEqual(len(diff['changed_entries']), 1)
        self.assertEqual(diff['changed_entries'][0]['after']['encoder_resolution'], '1920x1200')
        self.assertEqual(subprocess.check_output(read_receipt), receipt)
        with self.assertRaisesRegex(ValueError, 'duplicate baseline'):
            load_evidence(self.root, [extra, extra])
