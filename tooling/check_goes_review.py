"""Reproduce the published weather timing rows from the cited S0.7 revision."""
import argparse
import difflib
import json
import hashlib
from pathlib import Path
import runpy
import subprocess
import tempfile

REVISION = "2431b1bb356d75eecb9f99486b2a5bd3e9c37b44"
FOLDER = "benchmarks/goes19-c13-two-scans"
REVIEW = Path(__file__).resolve().parents[1] / "docs/reviews/GOES-19-timing-review.md"


def timing_records(checkout):
    git = ["git", "-C", str(checkout)]
    paths = subprocess.check_output(
        git + ["ls-tree", "-r", "--name-only", REVISION, "--", FOLDER], text=True
    ).splitlines()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for path in paths:
            relative = Path(path).relative_to(FOLDER)
            if relative.name != "compare_timings.py" and relative.suffix not in {".csv", ".json"}:
                continue
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.check_output(git + ["show", f"{REVISION}:{path}"]))
        compare = runpy.run_path(str(root / "compare_timings.py"))["compare"]
        _, runs = compare(root)
        baseline = json.loads((root / "run.json").read_bytes())
        scans = [frame["file"] for frame in baseline["metrics"]["frames"]]
        records = []
        for name, times, misses in runs:
            if name.startswith("replay-"):
                path, label, display = f"replays/{name}.json", name[:14], "Recorded replay"
            elif name.startswith("Terminal transcript "):
                path = "replays/reported-mac-times.csv"
                label, display = "CSV row " + name.removeprefix("Terminal transcript "), name
            else:
                path, label, display = "run.json", "Baseline report", name
            records.append(dict(key=name, display=display, label=label, revision=REVISION,
                                path=f"{FOLDER}/{path}", sha256=hashlib.sha256((root / path).read_bytes()).hexdigest(),
                                scan_1=scans[0], scan_2=scans[1], scan_1_us=round(times[0]*1000),
                                scan_2_us=round(times[1]*1000), misses=misses))
        return records


def evidence_rows(checkout):
    rows = []
    for record in timing_records(checkout):
        name, label, misses = record["display"], record["label"], record["misses"]
        first, second = record["scan_1_us"] / 1000, record["scan_2_us"] / 1000
        path = record["path"]
        url = f"https://github.com/veerendra-saraswathi/S0.7/blob/{REVISION}/{path}"
        rows.append(f"| {name} | {first:.3f} | {second:.3f} | {misses} | [{label}]({url}) |")
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--review", type=Path, default=REVIEW)
    args = parser.parse_args()
    expected = evidence_rows(args.checkout)
    table = [line for line in args.review.read_text().splitlines() if line.startswith("|")]
    diff = list(difflib.unified_diff(expected, table[2:], "pinned evidence", "review", lineterm=""))
    if diff:
        raise SystemExit("\n".join(diff))
    print(f"Verified {len(expected)} GOES timing rows and their source links at {REVISION[:7]}.")
