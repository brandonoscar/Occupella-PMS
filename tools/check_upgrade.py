"""Upgrade check: this branch's migrations apply to a database that already holds money.

The other migration checks start from an empty database, but a real one has rows, and some
migrations only fail once rows exist: a NOT NULL column with no default, a new CHECK or UNIQUE
constraint that existing rows break. This check:

  1. refuses an edit to, or the deletion of, a migration the base branch already has. A
     database that applied it never re-runs it, so the edit would never reach it;
  2. builds the base branch's schema in a throwaway database and fills it with the base
     branch's own self-host seed: a synthetic 50-door PMC, its deposits and a month of rent;
  3. applies this branch's migrations on top;
  4. checks the money already posted is untouched: ledger history, and every account's
     balance and version, compared in the columns the base schema had;
  5. runs this branch's self-host smoke on the upgraded database: a second PMC, more rent, and
     every ledger invariant over all of it.

usage: python -m tools.check_upgrade --base <sha>   (uses $DATABASE_URL and dbmate on PATH)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from tools.check_rollback import Dbmate
from tools.scratch_db import scratch_database

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = "db/migrations"
SEED = "selfhost/smoke.py"
# Money already posted, which no migration may change. None means every column the table had.
UNTOUCHED: dict[str, list[str] | None] = {
    "pgledger_transfers": None,
    "pgledger_entries": None,
    "trust_ledger_accounts": None,
    "pgledger_accounts": ["id", "currency", "balance", "version"],
}


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


def base_migrations(root: Path, base: str) -> dict[str, str]:
    names = git(root, "ls-tree", "--name-only", base, f"{MIGRATIONS}/").split()
    return {
        Path(name).name: git(root, "show", f"{base}:{name}")
        for name in names
        if name.endswith(".sql")
    }


def edited_migrations(root: Path, base: str) -> list[str]:
    problems = []
    for name, text in sorted(base_migrations(root, base).items()):
        current = root / MIGRATIONS / name
        if not current.exists():
            problems.append(f"{name} is on the base branch but was deleted.")
        elif current.read_text() != text:
            problems.append(
                f"{name} is on the base branch but was edited. A committed migration is never "
                "edited: add a new one."
            )
    return problems


def columns(conn: psycopg.Connection[Any]) -> dict[str, list[str]]:
    found = {}
    for table, chosen in UNTOUCHED.items():
        found[table] = chosen or [
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
                (table,),
            )
        ]
    return found


def fingerprint(
    conn: psycopg.Connection[Any], tables: dict[str, list[str]]
) -> dict[str, tuple[int, str]]:
    """Row count and a digest of every row, per table, in the given columns."""
    prints = {}
    for table, names in tables.items():
        query = sql.SQL(
            "SELECT count(*), md5(coalesce(string_agg(r::text, E'\\n' ORDER BY r::text), ''))"
            " FROM (SELECT {} FROM {}) r"
        ).format(sql.SQL(", ").join(map(sql.Identifier, names)), sql.Identifier(table))
        row = conn.execute(query).fetchone()
        assert row is not None
        prints[table] = (int(row[0]), str(row[1]))
    return prints


def run(command: list[str], url: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    # The URL goes through the environment, never the command line.
    return subprocess.run(
        command, cwd=cwd, env={**os.environ, "DATABASE_URL": url}, capture_output=True, text=True
    )


def check(root: Path, base: str, server_url: str, dbmate: str) -> list[str]:
    problems = edited_migrations(root, base)
    if problems:
        return problems
    try:
        seed = git(root, "show", f"{base}:{SEED}")
    except subprocess.CalledProcessError:
        return [f"the base branch has no {SEED} to seed its schema with."]

    with tempfile.TemporaryDirectory() as tmp, scratch_database(server_url, "pms_upgrade") as url:
        base_dir = Path(tmp) / "migrations"
        base_dir.mkdir()
        for name, text in base_migrations(root, base).items():
            (base_dir / name).write_text(text)
        (Path(tmp) / "base_smoke.py").write_text(seed)

        built = Dbmate(dbmate, base_dir, url).run("--no-dump-schema", "up")
        if built.returncode != 0:
            return [f"the base branch's migrations did not apply: {built.stderr.strip()}"]
        seeded = run([sys.executable, str(Path(tmp) / "base_smoke.py")], url, root)
        print(seeded.stdout, end="")
        if seeded.returncode != 0:
            return [f"the base branch's seed failed on its own schema:\n{seeded.stderr.strip()}"]

        with psycopg.connect(url, autocommit=True) as conn:
            tables = columns(conn)
            before = fingerprint(conn, tables)
        if before["pgledger_entries"][0] == 0:
            return ["the base branch's seed posted nothing, so the upgrade proves nothing."]
        print("before: " + ", ".join(f"{t} {n} rows" for t, (n, _) in before.items()))

        upgraded = Dbmate(dbmate, root / MIGRATIONS, url).run("--no-dump-schema", "up")
        if upgraded.returncode != 0:
            return [
                "this branch's migrations fail on a database that already holds money "
                f"(they may pass on an empty one): {upgraded.stderr.strip()}"
            ]
        print(f"upgraded: {upgraded.stdout.strip() or 'no new migrations'}")

        with psycopg.connect(url, autocommit=True) as conn:
            after = fingerprint(conn, tables)
        for table in tables:
            if after[table] != before[table]:
                problems.append(f"{table}: the upgrade changed money that was already posted.")
        if problems:
            return problems

        smoke = run([sys.executable, "-m", "selfhost.smoke"], url, root)
        print(smoke.stdout, end="")
        if smoke.returncode != 0:
            problems.append(
                f"this branch's self-host smoke failed on the upgraded database:\n"
                f"{smoke.stdout.strip()}\n{smoke.stderr.strip()}"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upgrade check for migrations, with data")
    parser.add_argument("--base", required=True, help="the base branch's commit")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    dbmate = shutil.which("dbmate")
    if not url or not dbmate:
        print("FAIL: needs DATABASE_URL and dbmate on PATH.")
        return 1
    problems = check(args.root, args.base, url, dbmate)
    for problem in problems:
        print(f"FAIL: {problem}")
    if not problems:
        print("ok: this branch's migrations upgrade a database holding money and leave it intact.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
