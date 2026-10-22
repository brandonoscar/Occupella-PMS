"""Backup check: a database holding money comes back from scripts/backup.sh whole.

docs/backup.md says how a company backs up its books; this proves the restore works:

  1. applies this branch's migrations to a throwaway database and fills it with money: the
     self-host seed (a synthetic 50-door PMC, its deposits and a month of rent, as
     tools/check_upgrade.py seeds) and the demo PMC (src/pms/demo.py: leases, charges, bills,
     reconciliations, logins, documents' bytes and the audit log, built as staff build them);
  2. dumps it with `scripts/backup.sh dump` and restores the file with `scripts/backup.sh
     restore` into a second, empty database;
  3. checks the copy against the original: the same schema (tables, functions, triggers, grants
     and policies, from pg_dump --schema-only), every table and sequence row for row, and a
     ledger that ties out (the self-host smoke's health checks).

usage: PYTHONPATH=src python -m tools.check_backup   (uses $DATABASE_URL, dbmate and pg_dump 16)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from pms import demo
from selfhost import smoke
from tools.check_rollback import Dbmate
from tools.scratch_db import scratch_database

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = "db/migrations"
SCRIPT = "scripts/backup.sh"
# libpq's variable for each part of a connection URL the checks use.
LIBPQ = {
    "host": "PGHOST",
    "port": "PGPORT",
    "user": "PGUSER",
    "password": "PGPASSWORD",
    "dbname": "PGDATABASE",
    "sslmode": "PGSSLMODE",
}
# pg_dump 16.10 and later fence a plain dump with a random key, different every time.
RANDOM_LINES = re.compile(r"^\\(un)?restrict .*$", re.MULTILINE)
CHECK_LINE = re.compile(r"^\s*CONSTRAINT \w+ CHECK \(")
PARENTHESES = re.compile(r"[()]")


def libpq_env(url: str) -> dict[str, str]:
    """The environment that points pg_dump, pg_restore and psql at `url`: the script takes its
    database from libpq's variables, never from its command line."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    for key, value in conninfo_to_dict(url).items():
        if key not in LIBPQ:
            raise ValueError(f"DATABASE_URL sets {key}, which this check can't pass on")
        env[LIBPQ[key]] = str(value)
    return env


def backup(root: Path, command: str, archive: Path, url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(root / SCRIPT), command, str(archive)],
        env=libpq_env(url),
        capture_output=True,
        text=True,
    )


def fill(url: str) -> None:
    """Money in every shape the PMS keeps it, synthetic: the self-host seed, then the demo."""
    with psycopg.connect(url, autocommit=True) as conn:
        seeded = smoke.seed(conn)
        smoke.post_month_of_rent(conn, seeded, date(2026, 2, 1))
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier("trust_app")))
        demo.build(conn)


def schema(url: str) -> list[str]:
    dumped = subprocess.run(
        ["pg_dump", "--schema-only", "--no-password"],
        env=libpq_env(url),
        capture_output=True,
        text=True,
        check=True,
    )
    return RANDOM_LINES.sub("", dumped.stdout).splitlines()


def schema_differences(original: list[str], restored: list[str]) -> list[str]:
    """The lines where the restored schema differs from the original's. Postgres parses a CHECK
    constraint again as it is restored and drops the parentheses of an AND inside an AND
    ("(a AND b) AND c" comes back "a AND b AND c"), so a CHECK line equal once its parentheses
    are dropped is the same constraint. Any other difference counts."""
    if len(original) != len(restored):
        return [f"{len(original)} lines in the original's schema, {len(restored)} restored"]
    return [
        f"line {number}: {before.strip()!r} came back {after.strip()!r}"
        for number, (before, after) in enumerate(zip(original, restored, strict=True), 1)
        if before != after
        and not (
            CHECK_LINE.match(before)
            and CHECK_LINE.match(after)
            and PARENTHESES.sub("", before) == PARENTHESES.sub("", after)
        )
    ]


def fingerprint(conn: psycopg.Connection[Any]) -> dict[str, tuple[int, str]]:
    """Row count and a digest of every row, for every table outside the catalogs; for each
    sequence, where it stands (its last value and whether that was used)."""
    relations = conn.execute(
        "SELECT n.nspname, c.relname, c.relkind = 'S'"
        " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE c.relkind IN ('r', 'p', 'S')"
        "   AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
        "   AND n.nspname NOT LIKE 'pg_toast%'"
        " ORDER BY 1, 2"
    ).fetchall()
    prints = {}
    for schema_name, name, sequence in relations:
        rows = (
            sql.SQL("(SELECT last_value, is_called FROM {})") if sequence else sql.SQL("{}")
        ).format(sql.Identifier(schema_name, name))
        query = sql.SQL(
            "SELECT count(*), md5(coalesce(string_agg(r::text, E'\\n' ORDER BY r::text), ''))"
            " FROM {} r"
        ).format(rows)
        row = conn.execute(query).fetchone()
        assert row is not None
        prints[f"{schema_name}.{name}"] = (int(row[0]), str(row[1]))
    return prints


def compare(
    original: dict[str, tuple[int, str]], restored: dict[str, tuple[int, str]]
) -> list[str]:
    problems = []
    for name in sorted(original.keys() | restored.keys()):
        if name not in restored:
            problems.append(f"{name} is missing from the restored copy.")
        elif name not in original:
            problems.append(f"{name} is in the restored copy but not the original.")
        elif original[name][0] != restored[name][0]:
            problems.append(
                f"{name}: {original[name][0]} rows in the original, {restored[name][0]} restored."
            )
        elif original[name] != restored[name]:
            problems.append(f"{name}: the rows restored differ from the original's.")
    return problems


def check(root: Path, server_url: str, dbmate: str) -> list[str]:
    with (
        tempfile.TemporaryDirectory() as tmp,
        scratch_database(server_url, "pms_backup") as original_url,
        scratch_database(server_url, "pms_restore") as restored_url,
    ):
        built = Dbmate(dbmate, root / MIGRATIONS, original_url).run("--no-dump-schema", "up")
        if built.returncode != 0:
            return [f"the migrations did not apply: {built.stderr.strip()}"]
        fill(original_url)
        with psycopg.connect(original_url, autocommit=True) as conn:
            original = fingerprint(conn)
            if smoke.health(conn):
                return ["the original's ledger doesn't tie out, so the restore proves nothing."]
        print(
            f"original: {len(original)} tables and sequences,"
            f" {original['public.pgledger_transfers'][0]} transfers,"
            f" {original['public.trust_document_files'][0]} documents' files"
        )

        archive = Path(tmp) / "pms.dump"
        for command in ("dump", "restore"):
            url = original_url if command == "dump" else restored_url
            ran = backup(root, command, archive, url)
            print(ran.stderr, end="")
            if ran.returncode != 0:
                return [f"{SCRIPT} {command} failed: {ran.stderr.strip()}"]

        problems = [
            f"the restored schema (tables, functions, triggers, grants, policies) differs: {line}"
            for line in schema_differences(schema(original_url), schema(restored_url))
        ]
        with psycopg.connect(restored_url, autocommit=True) as conn:
            problems += compare(original, fingerprint(conn))
            problems += [f"restored: {problem}" for problem in smoke.health(conn)]
    return problems


def main(argv: list[str] | None = None) -> int:
    del argv
    url = os.environ.get("DATABASE_URL")
    dbmate = shutil.which("dbmate")
    if not url or not dbmate or not shutil.which("pg_dump"):
        print("FAIL: needs DATABASE_URL, and dbmate and pg_dump 16 on PATH.")
        return 1
    problems = check(ROOT, url, dbmate)
    for problem in problems:
        print(f"FAIL: {problem}")
    if not problems:
        print(
            "ok: the backup restores into an empty database with the same schema, every row,"
            " and a ledger that ties out."
        )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
