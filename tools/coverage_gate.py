"""Coverage gate for the coverage workflow.

Checks, all read from ci/thresholds.toml:
  - the money module (PL/pgSQL functions listed in [coverage.money], plus any Python paths
    listed there) meets its statement/line and branch floors;
  - overall coverage (Python lines, PL/pgSQL statements, PL/pgSQL branches) is not below the
    stored baseline, and the baseline has been raised to match when coverage went up, so the
    ratchet only moves up.
Also writes the PL/pgSQL Cobertura report that diff-cover reads for the changed-lines check.

usage:
  python -m tools.coverage_gate --sql-raw coverage/sql-raw.json \
      --python-xml coverage/python.xml --sql-xml-out coverage/sql.xml [--summary FILE]
  python -m tools.coverage_gate --print coverage.changed_lines_min
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from tools import sql_coverage

ROOT = Path(__file__).resolve().parent.parent
THRESHOLDS = ROOT / "ci/thresholds.toml"


@dataclass
class Result:
    lines: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def floor1(value: float) -> float:
    return math.floor(value * 10 + 1e-9) / 10


def python_rates(xml_text: str) -> dict[str, tuple[int, int, int, int]]:
    """Per file and in total: (lines valid, lines covered, branches valid, branches covered)."""
    root = ElementTree.fromstring(xml_text)
    rates: dict[str, tuple[int, int, int, int]] = {}
    total = [0, 0, 0, 0]
    for cls in root.iter("class"):
        lines = list(cls.iter("line"))
        valid = len(lines)
        covered = sum(1 for line in lines if int(line.get("hits", "0")) > 0)
        b_valid = b_covered = 0
        for line in lines:
            condition = line.get("condition-coverage")
            if line.get("branch") == "true" and condition:
                done, _, of = condition.split("(")[1].rstrip(")").partition("/")
                b_valid += int(of)
                b_covered += int(done)
        rates[cls.get("filename", "")] = (valid, covered, b_valid, b_covered)
        for i, value in enumerate((valid, covered, b_valid, b_covered)):
            total[i] += value
    rates["*"] = (total[0], total[1], total[2], total[3])
    return rates


def evaluate(
    functions: list[dict[str, Any]], python_xml: str, thresholds: dict[str, Any]
) -> Result:
    result = Result()
    coverage = thresholds["coverage"]
    money = coverage["money"]
    summaries = [sql_coverage.summarize(function) for function in functions]

    stmts = sum(s.statements for s in summaries)
    stmts_hit = sum(s.statements_hit for s in summaries)
    branches = sum(s.branches for s in summaries)
    branches_hit = sum(s.branches_hit for s in summaries)
    rates = python_rates(python_xml)
    py_valid, py_covered, _, _ = rates["*"]
    current = {
        "python_lines": sql_coverage.percent(py_covered, py_valid),
        "sql_statements": sql_coverage.percent(stmts_hit, stmts),
        "sql_branches": sql_coverage.percent(branches_hit, branches),
    }

    result.lines.append("| Measure | Covered | Baseline |")
    result.lines.append("|---|---|---|")
    for key, value in current.items():
        baseline = float(coverage["baseline"][key])
        result.lines.append(f"| {key} | {value:.2f}% | {baseline:.1f}% |")
        if value + 1e-9 < baseline:
            result.failures.append(
                f"{key} coverage dropped to {value:.2f}%, below the baseline {baseline:.1f}%."
            )
        elif floor1(value) > baseline:
            result.failures.append(
                f"{key} coverage rose to {value:.2f}%. Raise [coverage.baseline] {key} in "
                f"ci/thresholds.toml to {floor1(value):.1f} so it can't slip back."
            )

    result.lines.append("")
    result.lines.append("| Money function | Statements | Branches |")
    result.lines.append("|---|---|---|")
    m_stmts = m_hit = m_branches = m_bhit = 0
    for name in money["functions"]:
        matches = [s for s in summaries if s.name == name]
        if not matches:
            result.failures.append(f"money function {name} is listed but not in the schema.")
        for s in matches:
            m_stmts += s.statements
            m_hit += s.statements_hit
            m_branches += s.branches
            m_bhit += s.branches_hit
            result.lines.append(
                f"| {s.name}({s.args}) | {s.statements_hit}/{s.statements} "
                f"| {s.branches_hit}/{s.branches} |"
            )
    for label, hit, total, floor in (
        ("statements", m_hit, m_stmts, money["statements_min"]),
        ("branches", m_bhit, m_branches, money["branches_min"]),
    ):
        value = sql_coverage.percent(hit, total)
        result.lines.append(f"| **money module {label}** | **{value:.2f}%** | floor {floor}% |")
        if value + 1e-9 < floor:
            result.failures.append(f"money module {label} {value:.2f}% is below {floor}%.")

    for path in money["python_paths"]:
        if path not in rates:
            result.failures.append(f"money Python path {path} has no coverage data.")
            continue
        valid, covered, b_valid, b_covered = rates[path]
        for label, value, floor in (
            ("lines", sql_coverage.percent(covered, valid), money["statements_min"]),
            ("branches", sql_coverage.percent(b_covered, b_valid), money["branches_min"]),
        ):
            if value + 1e-9 < floor:
                result.failures.append(f"{path} {label} {value:.2f}% is below {floor}%.")
    return result


def lookup(thresholds: dict[str, Any], dotted: str) -> Any:
    value: Any = thresholds
    for part in dotted.split("."):
        value = value[part]
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Coverage gate")
    parser.add_argument("--thresholds", type=Path, default=THRESHOLDS)
    parser.add_argument("--print", dest="print_key")
    parser.add_argument("--sql-raw", type=Path)
    parser.add_argument("--python-xml", type=Path)
    parser.add_argument("--sql-xml-out", type=Path)
    parser.add_argument("--migrations", type=Path, default=ROOT / "db/migrations")
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    thresholds = tomllib.loads(args.thresholds.read_text())

    if args.print_key:
        print(lookup(thresholds, args.print_key))
        return 0
    if not (args.sql_raw and args.python_xml and args.sql_xml_out):
        parser.error("--sql-raw, --python-xml and --sql-xml-out are required")

    functions = json.loads(args.sql_raw.read_text())
    files = sql_coverage.read_migrations(args.migrations, ROOT)
    args.sql_xml_out.parent.mkdir(parents=True, exist_ok=True)
    args.sql_xml_out.write_text(sql_coverage.cobertura(functions, files))

    result = evaluate(functions, args.python_xml.read_text(), thresholds)
    report = "\n".join(["## Coverage", "", *result.lines, ""])
    if result.failures:
        report += "\n### Failed\n\n" + "\n".join(f"- {f}" for f in result.failures) + "\n"
    print(report)
    if args.summary:
        with args.summary.open("a") as handle:
            handle.write(report)
    return 1 if result.failures else 0


if __name__ == "__main__":
    sys.exit(main())
