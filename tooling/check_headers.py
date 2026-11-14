# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Check first-party executable source headers without scanning Git internals."""
from pathlib import Path
root = Path(__file__).resolve().parents[1]
paths = [root / 'pyproject.toml', root / '.github/workflows/ci.yml']
for folder in ('src', 'platform', 'subsystems', 'tests', 'tooling'):
    paths.extend((root / folder).rglob('*.py'))
failures = []
for path in paths:
    text = path.read_text()
    if ('SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary' not in text[:500] or
            'Copyright (c) 2026 Veerendra Kiran Bidarkota.' not in text[:500]):
        failures.append(str(path.relative_to(root)))
if failures:
    raise SystemExit('Missing proprietary headers: ' + ', '.join(failures))
print(f'Proprietary headers verified: {len(paths)} files')
