"""The score of Python mutation testing (mutmut, nightly in mutation.yml).

mutmut (BSD-3-Clause) changes the modules pyproject.toml [tool.mutmut] names one small change at
a time and runs the tests that call the changed function. This scores its run: the share of
mutants a test caught (failed or hung), against the floor in ci/thresholds.toml [mutation]
python_baseline_score. A mutant no test reaches counts as not caught, so code without tests
lowers the score too. Many survivors are equivalent (the case of a SQL keyword, which Postgres
ignores); the rest are missing tests.

usage: python -m tools.python_mutation mutants/mutmut-cicd-stats.json [--survivors FILE]
                                       [--summary FILE]
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def score(stats: dict[str, Any]) -> tuple[float, int, int]:
    """The score in percent, the mutants caught and the mutants scored."""
    caught = stats["killed"] + stats["timeout"] + stats["suspicious"]
    scored = stats["total"] - stats["skipped"]
    return (100.0 * caught / scored if scored else 100.0), caught, scored


def report(stats: dict[str, Any], baseline: float, survivors: str) -> tuple[str, bool]:
    value, caught, scored = score(stats)
    lines = [
        "## Python mutation testing",
        "",
        f"Score: **{value:.1f}%** ({caught} of {scored} caught: {stats['killed']} killed,"
        f" {stats['timeout']} hung; {stats['survived']} survived, {stats['no_tests']} reached"
        f" by no test). Baseline: {baseline:.1f}%.",
        "",
    ]
    if stats["segfault"] or stats["check_was_interrupted_by_user"]:
        lines.append("A run that crashed or was interrupted scores nothing.")
    if survivors.strip():
        lines += ["Not caught (`mutmut show <name>` shows each):", "", "```", survivors.strip()]
        lines += ["```", ""]
    ok = value + 1e-9 >= baseline and not stats["segfault"]
    ok = ok and not stats["check_was_interrupted_by_user"]
    if value + 1e-9 < baseline:
        lines.append(f"FAIL: score {value:.1f}% is below the baseline {baseline:.1f}%.")
    elif ok and int(value * 10) / 10 > baseline:
        lines.append(
            "The score rose. Raise [mutation] python_baseline_score in ci/thresholds.toml to"
            f" {int(value * 10) / 10:.1f} in a PR."
        )
    return "\n".join(lines) + "\n", ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stats", type=Path)
    parser.add_argument("--survivors", type=Path, help="mutmut results, to list")
    parser.add_argument("--summary", type=Path, help="append the report here (job summary)")
    args = parser.parse_args(argv)
    stats = json.loads(args.stats.read_text())
    thresholds = tomllib.loads((ROOT / "ci/thresholds.toml").read_text())
    baseline = float(thresholds["mutation"]["python_baseline_score"])
    survivors = args.survivors.read_text() if args.survivors else ""
    text, ok = report(stats, baseline, survivors)
    print(text)
    if args.summary:
        with args.summary.open("a") as summary:
            summary.write(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
