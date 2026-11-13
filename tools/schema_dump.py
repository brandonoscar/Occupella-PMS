"""A schema dump as the checks compare it: without what pg_dump writes when nothing in the schema
changed.

- "-- Dumped from database version ..." and "-- Dumped by pg_dump version ...": the server's and
  pg_dump's builds, which differ between a laptop and CI with the schema the same.
- "\\restrict <key>" and "\\unrestrict <key>": pg_dump 16.10 and later fence a plain dump with a
  random key, different every time. dbmate writes its own fixed key ("dbmate") instead, which
  db/schema.sql keeps.
- "pg_attver=<n>" among a column's options: PostgreSQL 16.16 (CI's postgres:16 image from
  2026-11-13) dumps each column's attver, an internal number that changes whenever its table is
  made again. A rollback and re-apply, or a restore, then read as a different schema, and
  db/schema.sql would differ by server.

Everything else is compared as dumped. scripts/dump_schema.sh writes db/schema.sql through this
(`python -m tools.schema_dump db/schema.sql`), and tools/check_rollback.py and
tools/check_backup.py compare dumps through `normal`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DROPPED_LINES = re.compile(
    r"^(?:-- Dumped (?:from database|by pg_dump) version .*"
    r"|\\(?:un)?restrict (?!dbmate$).*"
    r"|ALTER TABLE ONLY \S+ ALTER COLUMN \S+ SET \(pg_attver=\d+\);)\n",
    re.MULTILINE,
)
# pg_attver beside other options of a column: the other options stay.
ATTVER_OPTION = re.compile(r"pg_attver=\d+, |, pg_attver=\d+")


def normal(dump: str) -> str:
    return ATTVER_OPTION.sub("", DROPPED_LINES.sub("", dump))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m tools.schema_dump <schema.sql>   (rewrites it in place)")
        return 2
    path = Path(args[0])
    path.write_text(normal(path.read_text()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
