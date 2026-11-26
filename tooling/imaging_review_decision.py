# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Record or reopen the local imaging lab's resolution coverage decision."""
import argparse
import json
from pathlib import Path

from imaging_evidence_demo import RESOLUTION_ID, Service, S2Error
from s2_platform import Conflict, now


class ReviewService(Service):
    def __init__(self, database):
        super().__init__(database, storage_backend='json')
        self.routes.update({'review.open': ('write', self.record), 'review.read': ('read', self.read)})
        with self.store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS imaging_review_decisions '
                       '(branch TEXT, requirement TEXT, receipt TEXT NOT NULL, PRIMARY KEY(branch, requirement))')

    def read(self, db, ctx, branch):
        self.security.check_marking(ctx, {'level': 3, 'compartments': ['ENG']})
        row = db.execute('SELECT receipt FROM imaging_review_decisions WHERE branch=? AND requirement=?',
                         (branch, RESOLUTION_ID)).fetchone()
        if row is None:
            return {'status': 'unrecorded', 'requirement': RESOLUTION_ID}
        receipt = json.loads(row['receipt'])
        head, manifest = self._head_manifest(db, ctx, branch)
        for ref in receipt['evidence']:
            self.kernel.visible_object(db, ctx, manifest, ref['object_id'])
        return receipt

    def record(self, db, ctx, branch, note):
        if not isinstance(note, str) or not note.strip():
            raise S2Error('a reviewer note is required')
        note = note.strip()
        previous = self.read(db, ctx, branch)
        if previous['status'] == 'open':
            if previous['reviewer'] == ctx.principal and previous['note'] == note:
                return previous
            raise Conflict('a decision is already recorded for this snapshot')
        objects = self.list_objects(db, ctx, branch)
        evidence = visible_evidence(objects)
        if not any('wording' in ref for ref in evidence) or not any('path' in ref for ref in evidence):
            raise S2Error('snapshot requires visible resolution wording and evidence reports')
        receipt = {'requirement': RESOLUTION_ID, 'status': 'open', 'note': note,
                   'reviewer': ctx.principal, 'recorded_at': now(), 'evidence_commit': objects['commit'],
                   'evidence': evidence}
        db.execute('INSERT INTO imaging_review_decisions VALUES(?,?,?)', (branch, RESOLUTION_ID, json.dumps(receipt)))
        return receipt


def visible_evidence(objects):
    evidence = []
    for obj in objects['objects']:
        values = obj['values']
        if obj['type'] == 'Requirement' and values['key'] == RESOLUTION_ID:
            evidence.append({'object_id': obj['id'], 'revision': obj['revision'], 'wording': values['wording']})
        elif obj['type'] == 'EvidenceReport':
            evidence.append({'object_id': obj['id'], 'revision': obj['revision'],
                             'path': values['path'], 'sha256': values['sha256']})
    return evidence


def compare_receipt(receipt, folder):
    if receipt['status'] == 'unrecorded':
        raise ValueError('no decision recorded in the first snapshot')
    state = json.loads((folder / 'session.json').read_text())
    database = folder / 'demo.db'
    if not database.is_file():
        raise ValueError('missing comparison database')
    objects = Service(database, storage_backend='json').call(
        state['viewer_token'], 'objects.list', branch=state['branch'])
    current = visible_evidence(objects)
    def citations(evidence):
        return {(ref['path'], ref['sha256']) for ref in evidence if 'path' in ref}

    original = citations(receipt['evidence'])
    comparison = citations(current)
    saved_wording = [r['wording'] for r in receipt['evidence'] if 'wording' in r]
    current_wording = [r['wording'] for r in current if 'wording' in r]
    def describe(items):
        return [dict(path=path, sha256=sha) for path, sha in sorted(items)]

    return {'requirement': receipt['requirement'], 'recorded_status': receipt['status'],
            'recorded_evidence_commit': receipt['evidence_commit'], 'comparison_commit': objects['commit'],
            'wording_matches': saved_wording == current_wording,
            'recorded_reports': len(original), 'matching_reports': len(original & comparison),
            'only_in_receipt': describe(original - comparison),
            'only_in_comparison': describe(comparison - original)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('state', type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--compare-evidence', type=Path, metavar='STATE', help='compare receipt citations with another snapshot')
    mode.add_argument('--open', metavar='NOTE', help='record a reviewer decision to keep coverage open')
    parser.add_argument('--credential', type=Path, help='file containing the editor credential')
    args = parser.parse_args()
    if (args.open is None) != (args.credential is None):
        parser.error('--open and --credential must be supplied together')
    try:
        state = json.loads((args.state / 'session.json').read_text())
        database = args.state / 'demo.db'
        if not database.is_file():
            raise ValueError('missing evidence database')
        service = ReviewService(database)
        if args.open is None:
            result = service.call(state['viewer_token'], 'review.read', branch=state['branch'])
            if args.compare_evidence is not None:
                result = compare_receipt(result, args.compare_evidence)
        else:
            result = service.call(args.credential.read_text().strip(), 'review.open',
                                  branch=state['branch'], note=args.open)
    except (ValueError, OSError, KeyError, S2Error) as exc:
        parser.exit(1, f'imaging review: {exc}\n')
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
