"""Down/up check for migrations, run against a fully migrated database.

Ledger migrations are never rolled back (trust records are kept for years), so their down
section raises an exception that says "cannot be rolled back". For those, `dbmate rollback`
must fail and leave the schema exactly as it was. A migration whose down section does real work
must roll back cleanly. Walking from the newest migration, every reversible one is rolled back
in turn until the first irreversible one, which must refuse; then everything is re-applied and
the schema must match the starting point byte for byte.

usage: python -m tools.check_rollback [--migrations db/migrations]   (uses $DATABASE_URL)
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IRREVERSIBLE = re.compile(r"RAISE\s+EXCEPTION\s+'[^']*cannot be rolled back", re.IGNORECASE)


def down_section(text: str) -> str:
    _, marker, down = text.partition("-- migrate:down")
    return down if marker else ""


def is_irreversible(text: str) -> bool:
    return bool(IRREVERSIBLE.search(down_section(text)))


class Dbmate:
    def __init__(self, binary: str, migrations: Path, url: str) -> None:
        self.binary = binary
        self.migrations = migrations
        # The URL goes to dbmate through the environment, never the command line.
        self.env = {**os.environ, "DATABASE_URL": url}

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.binary, "--migrations-dir", str(self.migrations), *args],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def schema(self) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema.sql"
            dumped = self.run("--schema-file", str(path), "dump")
            if dumped.returncode != 0:
                raise RuntimeError(f"dbmate dump failed: {dumped.stderr}")
            return path.read_text()


def check(dbmate: Dbmate) -> list[str]:
    problems = []
    start = dbmate.schema()
    for migration in sorted(dbmate.migrations.glob("*.sql"), reverse=True):
        before = dbmate.schema()
        rolled = dbmate.run("--no-dump-schema", "rollback")
        if is_irreversible(migration.read_text()):
            if rolled.returncode == 0:
                problems.append(f"{migration.name} says it cannot be rolled back, but it was.")
            elif dbmate.schema() != before:
                problems.append(f"{migration.name}: the refused rollback changed the schema.")
            else:
                print(f"ok    {migration.name} refuses to roll back, schema unchanged")
            break
        if rolled.returncode != 0:
            problems.append(f"{migration.name}: rollback failed: {rolled.stderr.strip()}")
            break
        print(f"ok    {migration.name} rolled back")

    applied = dbmate.run("--no-dump-schema", "up")
    if applied.returncode != 0:
        problems.append(f"re-applying migrations failed: {applied.stderr.strip()}")
    elif dbmate.schema() != start:
        problems.append("after rolling back and re-applying, the schema differs from the start.")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Down/up check for migrations")
    parser.add_argument("--migrations", type=Path, default=ROOT / "db/migrations")
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    binary = shutil.which("dbmate")
    if not url or not binary:
        print("FAIL: needs DATABASE_URL and dbmate on PATH.")
        return 1
    problems = check(Dbmate(binary, args.migrations, url))
    for problem in problems:
        print(f"FAIL: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
