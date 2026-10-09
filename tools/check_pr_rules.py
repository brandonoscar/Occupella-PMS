"""The standing rule (CLAUDE.md), checked on every PR's diff.

  1. A PR that adds or changes code also adds or changes tests.
  2. A migration that defines a function or trigger, or alters a ledger table, also changes a
     property test under tests/properties/.
  3. A migration that adds a report function (trust_report_*) also adds a golden file under
     tests/golden/.
  4. A change under an API package also changes tests under tests/api/.
Skips and unlinked xfails are refused at run time by tests/conftest.py, for every test.

usage: python -m tools.check_pr_rules --base <sha> --head <sha>
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE = re.compile(r"^(db/migrations/|tools/|selfhost/|scripts/|src/|Dockerfile$|compose\.ya?ml$)")
LEDGER_SQL = re.compile(
    r"\b(CREATE\s+(OR\s+REPLACE\s+)?(FUNCTION|TRIGGER)|ALTER\s+TABLE\s+(pgledger_|trust_ledger_))",
    re.IGNORECASE,
)
REPORT_SQL = re.compile(r"\bFUNCTION\s+(public\.)?trust_report_", re.IGNORECASE)
API_CODE = re.compile(r"^src/.*/api/|(^|/)openapi\.(json|ya?ml)$")


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


def changed_files(root: Path, base: str, head: str) -> dict[str, str]:
    """path -> status letter (A, M, D, R...), for the PR's own changes."""
    files: dict[str, str] = {}
    for line in git(root, "diff", "--name-status", "--no-renames", f"{base}...{head}").splitlines():
        status, _, path = line.partition("\t")
        files[path] = status[0]
    return files


def added_lines(root: Path, base: str, head: str, path: str) -> list[str]:
    diff = git(root, "diff", "-U0", f"{base}...{head}", "--", path)
    return [
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]


def check(files: dict[str, str], added: dict[str, list[str]]) -> list[str]:
    problems = []
    live = {path for path, status in files.items() if status != "D"}
    tests = {path for path in files if path.startswith("tests/")}
    code = sorted(path for path in files if CODE.search(path))
    if code and not tests:
        problems.append(
            "Code changed but no test did: " + ", ".join(code) + ". Add the tests that cover it."
        )

    migrations = sorted(p for p in live if p.startswith("db/migrations/") and p.endswith(".sql"))
    ledger = [p for p in migrations if any(LEDGER_SQL.search(line) for line in added.get(p, []))]
    if ledger and not any(p.startswith("tests/properties/") for p in tests):
        problems.append(
            "Ledger logic changed in " + ", ".join(ledger) + " but no property test under "
            "tests/properties/ did. Add or extend an invariant."
        )
    reports = [p for p in migrations if any(REPORT_SQL.search(line) for line in added.get(p, []))]
    if reports and not any(p.startswith("tests/golden/") for p in tests):
        problems.append(
            "A report function was added in " + ", ".join(reports) + " without a golden file "
            "under tests/golden/."
        )
    api = sorted(p for p in live if API_CODE.search(p))
    if api and not any(p.startswith("tests/api/") for p in tests):
        problems.append(
            "API code changed (" + ", ".join(api) + ") without API tests under tests/api/ "
            "(success path, auth failure, bad input)."
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    files = changed_files(args.root, args.base, args.head)
    added = {
        path: added_lines(args.root, args.base, args.head, path)
        for path, status in files.items()
        if status != "D" and path.startswith("db/migrations/")
    }
    problems = check(files, added)
    for problem in problems:
        print(f"FAIL: {problem}")
    if not problems:
        print(f"ok: {len(files)} changed file(s) follow the standing rule.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
