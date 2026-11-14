#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
# Copyright (c) 2026 Veerendra Kiran Bidarkota. All Rights Reserved.
# Proprietary Commercial Software. See LICENSE.
"""Dependency-free repository launcher."""
import sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / 'src'), str(root / 'platform')]
sys.path[:0] = [str(path) for path in sorted((root / 'subsystems').iterdir()) if path.is_dir()]
from s2.cli import main
if __name__ == '__main__':
    main()
