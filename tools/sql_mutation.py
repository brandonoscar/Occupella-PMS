"""Mutation testing for the money logic, which is PL/pgSQL.

mutmut and Stryker mutate Python and JavaScript; the ledger's logic lives in SQL, so this does
the same job for SQL. It makes one small change (a mutant) to a copy of the migrations, applies
them to a fresh database, and runs the test suite. A failing test kills the mutant. A mutant
whose SQL doesn't apply is invalid and left out of the score; it never counts as killed.
Survivors are listed, each one a missing test.

What gets mutated:
  - the bodies of the money functions listed in ci/thresholds.toml [coverage.money];
  - trigger definitions (constraint triggers too), ALTER FUNCTION ... SECURITY DEFINER, and
    GRANT/REVOKE statements in every migration but the verbatim upstream one (deleted one at a
    time: each is a guard on money).

usage:
  python -m tools.sql_mutation --list
  python -m tools.sql_mutation --shard 1/4 --out mutation/shard-1.json   (uses $DATABASE_URL)
  python -m tools.sql_mutation --only m006,m014 --out mutation/check.json
  python -m tools.sql_mutation --score mutation/*.json [--summary FILE]

Equivalent mutants (changes that can't alter behavior, such as revoking a privilege nobody was
granted) are listed in ci/thresholds.toml [mutation] equivalent, each with its reason. They are
not run and not scored. That list is reviewed like any threshold.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "db/migrations"

# (pattern, replacement, description). Patterns are applied inside mutable spans only, never in
# comments or string literals.
OPERATORS: list[tuple[str, str, str]] = [
    (r"(?<=\s)<(?=\s)", "<=", "< to <="),
    (r"(?<=\s)<=(?=\s)", "<", "<= to <"),
    (r"(?<=\s)>(?=\s)", ">=", "> to >="),
    (r"(?<=\s)>=(?=\s)", ">", ">= to >"),
    (r"(?<=\s)(<>|!=)(?=\s)", "=", "<> to ="),
    (r"(?<=\s)=(?=\s)", "<>", "= to <>"),
    (r"(?<=\s)\+(?=\s)", "-", "+ to -"),
    (r"(?<=\s)-(?=\s)", "+", "- to +"),
    (r"(?<=[\s,(])-(?=transfer_request\.amount)", "", "drop unary minus"),
    (r"\bIS DISTINCT FROM\b", "IS NOT DISTINCT FROM", "IS DISTINCT FROM negated"),
    (r"\bIS NOT DISTINCT FROM\b", "IS DISTINCT FROM", "IS NOT DISTINCT FROM negated"),
    (r"\bIS NULL\b", "IS NOT NULL", "IS NULL negated"),
    (r"\bIS NOT NULL\b", "IS NULL", "IS NOT NULL negated"),
    (r"\bNOT FOUND\b", "FOUND", "NOT FOUND to FOUND"),
    (r"\bIF NOT\b", "IF", "drop IF NOT"),
    (r"(?<=\s)AND(?=\s)", "OR", "AND to OR"),
    (r"(?<=\s)OR(?=\s)", "AND", "OR to AND"),
    (r"\bRAISE EXCEPTION\b", "RAISE NOTICE", "RAISE EXCEPTION to NOTICE"),
    (r"\bRETURN NEW\b", "RETURN NULL", "RETURN NEW to NULL"),
    (r"\s+FOR UPDATE\b", "", "drop FOR UPDATE"),
    (r"\s+ORDER BY unnest\b", "", "drop lock ordering"),
    (r"(?<=[\s(])0(?=[\s)])", "1", "0 to 1"),
    (r"(?<![\w.])1(?![\w.])", "0", "1 to 0"),
]

# Statements deleted whole, one at a time, from every migration we wrote. The first migration
# is pgledger copied verbatim from upstream (CLAUDE.md), so its statements are left alone.
GUARD_STATEMENTS = re.compile(
    r"^(CREATE (?:CONSTRAINT )?TRIGGER|ALTER FUNCTION|GRANT|REVOKE)\b.*?;\n",
    re.MULTILINE | re.DOTALL,
)
UPSTREAM_FILES = re.compile(r"_ledger_core\.sql$")
FUNCTION = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(\w+)\s*\(", re.IGNORECASE)
NOT_CODE = re.compile(r"--[^\n]*|'(?:[^']|'')*'")


@dataclass(frozen=True)
class Mutant:
    id: str
    file: str
    start: int
    end: int
    replacement: str
    description: str
    line: int


def function_bodies(text: str, names: set[str]) -> Iterator[tuple[int, int]]:
    """(start, end) of each `$$ ... $$` body of a function named in `names`."""
    for match in FUNCTION.finditer(text):
        if match.group(1) not in names:
            continue
        start = text.find("$$", match.end())
        end = text.find("$$", start + 2)
        if start >= 0 and end > start:
            yield start + 2, end


def code_positions(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """The parts of text[start:end] outside comments and string literals."""
    spans, cursor = [], start
    for match in NOT_CODE.finditer(text, start, end):
        spans.append((cursor, match.start()))
        cursor = match.end()
    spans.append((cursor, end))
    return [(a, b) for a, b in spans if b > a]


def generate(files: dict[str, str], money: set[str]) -> list[Mutant]:
    mutants: list[Mutant] = []

    def add(path: str, start: int, end: int, replacement: str, description: str) -> None:
        text = files[path]
        line = text.count("\n", 0, start) + 1
        mutants.append(
            Mutant(f"m{len(mutants) + 1:03d}", path, start, end, replacement, description, line)
        )

    for path in sorted(files):
        text = files[path]
        spans = list(function_bodies(text, money))
        if not UPSTREAM_FILES.search(path):
            for match in GUARD_STATEMENTS.finditer(text):
                first = match.group(0).splitlines()[0]
                add(path, match.start(), match.end(), "", f"delete `{first[:60]}`")
                if match.group(1).startswith("CREATE"):
                    spans.append((match.start(), match.end()))
        for span_start, span_end in sorted(spans):
            for code_start, code_end in code_positions(text, span_start, span_end):
                for pattern, replacement, description in OPERATORS:
                    for found in re.finditer(pattern, text[code_start:code_end]):
                        add(
                            path,
                            code_start + found.start(),
                            code_start + found.end(),
                            replacement,
                            description,
                        )
    return mutants


def apply(files: dict[str, str], mutant: Mutant) -> dict[str, str]:
    text = files[mutant.file]
    changed = text[: mutant.start] + mutant.replacement + text[mutant.end :]
    return {**files, mutant.file: changed}


def shard(mutants: list[Mutant], spec: str) -> list[Mutant]:
    index, _, count = spec.partition("/")
    i, n = int(index), int(count)
    if not 1 <= i <= n:
        raise ValueError(f"bad shard {spec!r}; use i/n with 1 <= i <= n")
    return [m for k, m in enumerate(mutants) if k % n == i - 1]


def read_files(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory.parent.parent)): path.read_text()
        for path in sorted(directory.glob("*.sql"))
    }


def write_files(files: dict[str, str], directory: Path) -> Path:
    target = directory / "db/migrations"
    target.mkdir(parents=True)
    for path, text in files.items():
        (target / Path(path).name).write_text(text)
    return target


def url_for(server: str, name: str) -> str:
    return urlunsplit(urlsplit(server)._replace(path="/" + name))


Runner = Callable[[list[str], dict[str, str], int], int]


def run_command(command: list[str], env: dict[str, str], timeout: int) -> int:
    try:
        return subprocess.run(
            command, env=env, capture_output=True, text=True, timeout=timeout
        ).returncode
    except subprocess.TimeoutExpired:
        return -1


def execute(
    mutant: Mutant,
    files: dict[str, str],
    server_url: str,
    dbmate: str,
    test_command: list[str],
    runner: Runner = run_command,
    timeout: int = 600,
) -> str:
    """'invalid' (doesn't apply), 'killed' (a test failed or hung) or 'survived'."""
    with tempfile.TemporaryDirectory() as tmp:
        migrations = write_files(apply(files, mutant), Path(tmp))
        scratch = f"pms_mutant_{uuid.uuid4().hex[:10]}"
        env = {**os.environ, "DATABASE_URL": url_for(server_url, scratch)}
        # --no-dump-schema: a mutant's schema must never overwrite db/schema.sql.
        applied = runner(
            [dbmate, "--migrations-dir", str(migrations), "--no-dump-schema", "up"], env, timeout
        )
        runner([dbmate, "drop"], env, timeout)
        if applied != 0:
            return "invalid"
        test_env = {
            **os.environ,
            "DATABASE_URL": server_url,
            "PMS_MIGRATIONS_DIR": str(migrations),
            "HYPOTHESIS_PROFILE": "mutation",
            "DBMATE_NO_DUMP_SCHEMA": "true",
        }
        return "survived" if runner(test_command, test_env, timeout) == 0 else "killed"


def key(mutant: Mutant) -> str:
    """How ci/thresholds.toml names a mutant: stable while the code around it doesn't change."""
    return f"{Path(mutant.file).name}: {mutant.description}"


def score(results: list[dict[str, Any]]) -> tuple[float, int, int, int]:
    killed = sum(1 for r in results if r["outcome"] == "killed")
    survived = sum(1 for r in results if r["outcome"] == "survived")
    invalid = sum(1 for r in results if r["outcome"] == "invalid")
    valid = killed + survived
    return (100.0 * killed / valid if valid else 100.0), killed, survived, invalid


def report(results: list[dict[str, Any]], baseline: float) -> tuple[str, bool]:
    value, killed, survived, invalid = score(results)
    lines = [
        "## SQL mutation testing",
        "",
        f"Score: **{value:.1f}%** ({killed} killed, {survived} survived, {invalid} invalid). "
        f"Baseline: {baseline:.1f}%.",
        "",
    ]
    equivalent = sum(1 for r in results if r["outcome"] == "equivalent")
    if equivalent:
        lines += [
            f"{equivalent} equivalent mutant(s) skipped, as listed in ci/thresholds.toml.",
            "",
        ]
    survivors = [r for r in results if r["outcome"] == "survived"]
    if survivors:
        lines += ["Survivors (each is a missing test):", "", "| Mutant | Where | Change |"]
        lines.append("|---|---|---|")
        lines += [
            f"| {r['id']} | {r['file']}:{r['line']} | {r['description']} |" for r in survivors
        ]
    ok = value + 1e-9 >= baseline
    if not ok:
        lines.append(f"\nFAIL: score {value:.1f}% is below the baseline {baseline:.1f}%.")
    elif int(value * 10) / 10 > baseline:
        lines.append(
            f"\nThe score rose. Raise [mutation] baseline_score in ci/thresholds.toml to "
            f"{int(value * 10) / 10:.1f} in a PR."
        )
    return "\n".join(lines) + "\n", ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SQL mutation testing")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--shard", default="1/1")
    parser.add_argument("--only", help="comma-separated mutant ids, e.g. m006,m014")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--score", nargs="*", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--migrations", type=Path, default=MIGRATIONS)
    parser.add_argument("--thresholds", type=Path, default=ROOT / "ci/thresholds.toml")
    args = parser.parse_args(argv)
    thresholds = tomllib.loads(args.thresholds.read_text())

    if args.score is not None:
        scored = [r for path in args.score for r in json.loads(path.read_text())]
        text, ok = report(scored, float(thresholds["mutation"]["baseline_score"]))
        print(text)
        if args.summary:
            with args.summary.open("a") as handle:
                handle.write(text)
        return 0 if ok else 1

    files = read_files(args.migrations)
    mutants = generate(files, set(thresholds["coverage"]["money"]["functions"]))
    if args.list:
        for m in mutants:
            print(f"{m.id} {m.file}:{m.line} {m.description}")
        print(f"{len(mutants)} mutants")
        return 0

    server_url, dbmate = os.environ.get("DATABASE_URL"), shutil.which("dbmate")
    if not server_url or not dbmate or not args.out:
        print("FAIL: needs DATABASE_URL, dbmate on PATH and --out.")
        return 1
    # The ledger's tests; the CI-tool tests (tests/tools) don't touch the mutated SQL.
    test_command = [
        sys.executable,
        "-m",
        "pytest",
        "-x",
        "-q",
        "-p",
        "no:cacheprovider",
        "--ignore=tests/tools",
    ]
    equivalent = set(thresholds["mutation"].get("equivalent", []))
    chosen = shard(mutants, args.shard)
    if args.only:
        wanted = set(args.only.split(","))
        chosen = [m for m in mutants if m.id in wanted]
    results: list[dict[str, Any]] = []
    for mutant in chosen:
        began = time.monotonic()
        if key(mutant) in equivalent:
            outcome = "equivalent"
        else:
            outcome = execute(mutant, files, server_url, dbmate, test_command)
        results.append({**asdict(mutant), "outcome": outcome})
        print(f"{mutant.id} {outcome:8} {time.monotonic() - began:5.1f}s {mutant.description}")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
