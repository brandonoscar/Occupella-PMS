"""Static checks of our PL/pgSQL, with the plpgsql_check extension.

PL/pgSQL is only parsed when it runs, so a type mismatch, a misspelled column or an unused
variable on a branch no test reaches ships unnoticed. plpgsql_check reads every statement of a
function without running it. This lints every trust_* PL/pgSQL function (ours, CLAUDE.md), each
trigger function against each table it is attached to, with every warning class on, and fails
on any finding. The pgledger and ULID functions are copied verbatim from upstream and can't be
edited, so they are listed but not linted.

It needs a server where plpgsql_check is installed (the coverage job's image,
ci/postgres-coverage.Dockerfile); no shared_preload_libraries needed. It applies the migrations
to a throwaway database, so the schema itself never depends on the extension.

usage: python -m tools.sql_lint   (uses $DATABASE_URL and dbmate on PATH)
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from tools.check_rollback import Dbmate
from tools.scratch_db import scratch_database

ROOT = Path(__file__).resolve().parent.parent
OURS = "trust\\_%"

# Each PL/pgSQL function in the schema, once per table it is a trigger on (relid 0 if none).
TARGETS = """
SELECT p.oid::int, p.oid::regprocedure::text, coalesce(t.tgrelid, 0)::int,
       coalesce(t.tgrelid::regclass::text, ''), p.proname LIKE %s AS ours
FROM pg_proc p
JOIN pg_language l ON l.oid = p.prolang
LEFT JOIN pg_trigger t ON t.tgfoid = p.oid AND NOT t.tgisinternal
WHERE l.lanname = 'plpgsql' AND p.pronamespace = 'public'::regnamespace
  AND NOT EXISTS (
      SELECT 1 FROM pg_depend d
      WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'
  )
ORDER BY 2, 4
"""

FINDINGS = """
SELECT level, sqlstate, lineno, message, hint
FROM plpgsql_check_function_tb(
    %s::oid::regprocedure, %s::oid::regclass,
    fatal_errors => false, other_warnings => true, performance_warnings => true,
    extra_warnings => true, security_warnings => true, compatibility_warnings => true
)
"""


@dataclass(frozen=True)
class Target:
    oid: int
    signature: str
    relid: int
    table: str
    ours: bool

    @property
    def label(self) -> str:
        return f"{self.signature} on {self.table}" if self.table else self.signature


def targets(conn: psycopg.Connection[Any]) -> list[Target]:
    return [Target(*row) for row in conn.execute(TARGETS, (OURS,)).fetchall()]


def findings(conn: psycopg.Connection[Any], target: Target) -> list[str]:
    found = []
    for level, sqlstate, lineno, message, hint in conn.execute(
        FINDINGS, (target.oid, target.relid)
    ).fetchall():
        where = f" line {lineno}" if lineno else ""
        advice = f" ({hint})" if hint else ""
        found.append(f"{target.label}{where}: {level} {sqlstate}: {message}{advice}")
    return found


def lint(conn: psycopg.Connection[Any]) -> list[str]:
    conn.execute("CREATE EXTENSION IF NOT EXISTS plpgsql_check")
    found = targets(conn)
    ours = [target for target in found if target.ours]
    upstream = sorted({target.signature for target in found if not target.ours})
    if not ours:
        return ["found no trust_* PL/pgSQL functions to lint; the query or the schema is wrong."]
    problems = []
    for target in ours:
        problems += findings(conn, target)
        print(f"linted {target.label}")
    print(f"not linted, upstream and verbatim: {', '.join(upstream) or 'none'}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint our PL/pgSQL with plpgsql_check")
    parser.add_argument("--migrations", type=Path, default=ROOT / "db/migrations")
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    dbmate = shutil.which("dbmate")
    if not url or not dbmate:
        print("FAIL: needs DATABASE_URL and dbmate on PATH.")
        return 1
    with scratch_database(url, "pms_lint") as scratch:
        built = Dbmate(dbmate, args.migrations, scratch).run("--no-dump-schema", "up")
        if built.returncode != 0:
            print(f"FAIL: migrations did not apply: {built.stderr.strip()}")
            return 1
        with psycopg.connect(scratch, autocommit=True) as conn:
            problems = lint(conn)
    for problem in problems:
        print(f"FAIL: {problem}")
    if not problems:
        print("ok: plpgsql_check finds nothing in our PL/pgSQL.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
