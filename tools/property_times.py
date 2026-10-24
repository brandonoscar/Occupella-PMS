"""Each property test's seconds at the PR profile, kept in ci/property-times.toml, which the
nightly jobs and the coverage job's two shards deal the property tests out by
(PMS_TEST_SHARD_TIMES and PMS_TEST_ROTATION in tests/conftest.py, longest first) and
tests/tools/test_property_times.py checks: every property test has its time, and each nightly
job fits well inside its time limit.

Refresh it from a run with pytest's durations at the PR profile (the whole suite, or only the
tests that are new or changed):

    pytest tests/properties -p no:cacheprovider --durations=0 | tee durations.log
    python -m tools.property_times durations.log

The log's "call" lines replace those tests' seconds in the [seconds] table and add the new ones;
the others, and everything above the table, are kept. A test that no longer exists is taken out
by hand (the check names it).
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIMES = ROOT / "ci/property-times.toml"

# "151.21s call     tests/properties/test_audit_properties.py::test_audit_invariants..."
CALL = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)s call\s+(tests/properties/\S+::\S+?)(?:\[.*\])?\s*$")


def durations(log: str) -> dict[str, float]:
    """Each property test's call seconds in a pytest --durations log (a parametrized test's
    cases summed)."""
    seconds: dict[str, float] = {}
    for line in log.splitlines():
        match = CALL.match(line)
        if match:
            name = match.group(2)
            seconds[name] = seconds.get(name, 0.0) + float(match.group(1))
    return seconds


def rewrite(times: str, seconds: dict[str, float]) -> str:
    """The times file with `seconds` written into its [seconds] table (replacing those tests'
    times, adding new ones, keeping the rest), sorted by name; everything above it kept."""
    head, marker, _ = times.partition("[seconds]\n")
    if not marker:
        raise ValueError("the times file has no [seconds] table")
    merged = {**tomllib.loads(times)["seconds"], **seconds}
    table = "".join(f'"{name}" = {round(merged[name], 1)}\n' for name in sorted(merged))
    return head + marker + table


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m tools.property_times DURATIONS_LOG", file=sys.stderr)
        return 2
    seconds = durations(Path(argv[0]).read_text())
    if not seconds:
        print("no property test durations in the log (run pytest with --durations=0)")
        return 1
    TIMES.write_text(rewrite(TIMES.read_text(), seconds))
    print(f"ok: {len(seconds)} property tests' times written to {TIMES.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
