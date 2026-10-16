"""Run one deterministic shard of the test suite, with per-module timing.

Why this exists: the mandatory "full suite" job needs more than 90 minutes on a CI runner while the
same suite finishes in about 11 minutes locally, and raising the timeout only postpones the same wall
clock. Splitting the suite across parallel jobs was tried and **rejected on evidence**: running the
modules in isolated batches reproduces failures that the single discovery run does not have (nine
errors in ``test_project_discovery_production`` in a shard, while the same module passes both alone
and inside the full run), so the suite's execution is not yet interchangeable. This tool is
therefore a **timing and diagnosis** instrument: it partitions the suite deterministically, proves
the partition is complete, and prints per-module durations so a slow CI run names its slow tests.
Do not wire it into CI as a substitute for the single full-suite job. It still runs every test of
its shard -- nothing is excluded and no check is weakened:

* every ``test_*.py`` file under ``tests/`` (including subpackages) is enumerated and sorted, so the
  partition is stable across runs and machines;
* shard *i* of *n* takes every *n*-th file: the union of the shards is exactly the full set, with no
  file in two shards (``--verify`` proves it without running anything);
* each module is timed and the shard prints a sorted table, so a CI run names the slow tests instead
  of leaving "it is just slow" as an explanation;
* the process starts in the repository root with that root on ``sys.path``, exactly like the
  single-process discovery run, because tests import ``app.*`` directly.

Usage::

    python -B -X utf8 scripts/run_test_shard.py --index 1 --count 4
    python -B -X utf8 scripts/run_test_shard.py --verify --count 4
    python -B -X utf8 scripts/run_test_shard.py --index 1 --count 4 --list
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def test_files(root: pathlib.Path) -> list[pathlib.Path]:
    """Every test module under ``root``, in a stable order."""

    return sorted(path for path in root.rglob("test_*.py") if path.name != "__init__.py")


def shard_of(files: list[pathlib.Path], index: int, count: int) -> list[pathlib.Path]:
    return [path for position, path in enumerate(files) if position % count == index - 1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one shard of the Forge AI test suite.")
    parser.add_argument("--index", type=int, default=1, help="1-based shard number")
    parser.add_argument("--count", type=int, default=1, help="total number of shards")
    parser.add_argument("--tests", default="tests", help="test directory")
    parser.add_argument("--list", action="store_true", help="print the partition and exit")
    parser.add_argument("--verify", action="store_true", help="prove the partition covers everything")
    parser.add_argument("--slowest", type=int, default=15, help="how many modules to list by time")
    args = parser.parse_args()

    if args.count < 1 or not 1 <= args.index <= args.count:
        print(f"invalid shard {args.index}/{args.count}", file=sys.stderr)
        return 2

    root = pathlib.Path(args.tests)
    files = test_files(root)
    if not files:
        print(f"no test modules found under {root}", file=sys.stderr)
        return 2

    shards = [shard_of(files, index, args.count) for index in range(1, args.count + 1)]

    if args.verify:
        union = [path for shard in shards for path in shard]
        duplicates = sorted({str(path) for path in union if union.count(path) > 1})
        missing = sorted({str(path) for path in union} ^ {str(path) for path in files}) if False else sorted(
            {str(path) for path in files} - {str(path) for path in union}
        )
        report = {
            "modules": len(files),
            "shards": args.count,
            "sizes": [len(shard) for shard in shards],
            "duplicates": duplicates,
            "missing": missing,
            "complete": not duplicates and not missing,
        }
        print(json.dumps(report, indent=1))
        return 0 if report["complete"] else 1

    shard = shards[args.index - 1]
    if args.list:
        print(
            json.dumps(
                {"index": args.index, "count": args.count, "modules": [path.stem for path in shard]},
                indent=1,
            )
        )
        return 0

    print(f"shard {args.index}/{args.count}: {len(shard)} module(s) of {len(files)}", flush=True)
    loader = unittest.TestLoader()
    timings: list[tuple[float, str, int, int, int]] = []
    total_run = total_failures = total_errors = total_skipped = 0

    for path in shard:
        module_suite = loader.discover(start_dir=str(path.parent), pattern=path.name)
        started = time.monotonic()
        result = unittest.TextTestRunner(verbosity=0).run(module_suite)
        elapsed = time.monotonic() - started
        total_run += result.testsRun
        total_failures += len(result.failures)
        total_errors += len(result.errors)
        total_skipped += len(result.skipped)
        timings.append((elapsed, path.stem, result.testsRun, len(result.failures), len(result.errors)))
        if result.failures or result.errors:
            print(f"  FAIL {path.as_posix()} ({elapsed:.1f}s)", flush=True)
            for case, trace in list(result.failures) + list(result.errors):
                print(f"    {case}", flush=True)
                print("      " + (trace.strip().splitlines() or [""])[-1], flush=True)

    print(f"\n--- slowest modules in shard {args.index}/{args.count} ---", flush=True)
    for elapsed, name, tests, failures, errors in sorted(timings, reverse=True)[: args.slowest]:
        print(f"{elapsed:8.1f}s  {tests:4} tests  fail={failures} err={errors}  {name}", flush=True)

    print(
        f"\nshard {args.index}/{args.count} total: Ran {total_run} tests in {sum(t[0] for t in timings):.1f}s "
        f"(failures={total_failures}, errors={total_errors}, skipped={total_skipped})",
        flush=True,
    )
    return 1 if (total_failures or total_errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
