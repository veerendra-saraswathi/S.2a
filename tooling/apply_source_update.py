#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Apply a packaged S2 source update to an exact, clean baseline without replacing Git metadata."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def git(target, *args):
    result = subprocess.run(['git', '-C', str(target), *args], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'Git command failed')
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', nargs='?', default=str(Path.home() / 'S.2a'))
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    bundle = Path(__file__).resolve().parent
    target = Path(args.target).expanduser().resolve()
    try:
        manifest = json.loads((bundle / 'update-manifest.json').read_text())
        patch = bundle / 'update.patch'
        if hashlib.sha256(patch.read_bytes()).hexdigest() != manifest['patch_sha256']:
            raise RuntimeError('Update patch integrity check failed')
        if Path(git(target, 'rev-parse', '--show-toplevel')).resolve() != target:
            raise RuntimeError('Target must be the repository root')
        if git(target, 'rev-parse', 'HEAD') != manifest['base_commit']:
            raise RuntimeError('Target is not the expected baseline ' + manifest['base_commit'][:7])
        if git(target, 'status', '--porcelain', '--untracked-files=all'):
            raise RuntimeError('Target has local changes. Commit or preserve them before applying this update.')
        git(target, 'apply', '--check', '--index', '--whitespace=error-all', str(patch))
        if args.check_only:
            print('Baseline, cleanliness, patch integrity and applicability: PASS')
            return
        git(target, 'apply', '--index', '--whitespace=error-all', str(patch))
        for item in manifest['files']:
            path = target / item['path']
            if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                raise RuntimeError('Post-apply verification failed; do not commit: ' + item['path'])
        print('S2 storage update applied and staged: ' + str(target))
        print('Git history, IDE settings and local databases were preserved.')
        print('Run the tests in START_HERE.md, then review and commit the staged update.')
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        print('Update stopped: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
