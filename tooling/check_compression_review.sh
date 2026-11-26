#!/bin/sh
# SPDX-License-Identifier: LicenseRef-Samanvaya-Proprietary
set -eu

if [ "$#" -ne 2 ]; then
    echo 'Usage: check_compression_review.sh SNAPSHOT S07_CHECKOUT' >&2
    exit 1
fi
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
review="$repo/docs/reviews/Samanvaya-compression-review.md"
generated=$(mktemp)
trap 'rm -f "$generated"' EXIT
"${PYTHON:-python3}" "$repo/tooling/imaging_assistant.py" "$1" --summary "$2" --markdown > "$generated" || {
    code=$?
    cat "$generated" >&2
    exit "$code"
}
if ! cmp -s "$review" "$generated"; then
    echo 'The tracked review differs from the regenerated report:' >&2
    diff -u "$review" "$generated" >&2 || true
    exit 2
fi
echo 'Verified source reports and reproduced the tracked compression review.'
