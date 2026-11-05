"""Whether the database's migrations are the ones this code ships (docs/edge-cases.md H-041).

dbmate records each migration it applied in schema_migrations. Code started on a database a newer
PMS migrated (an older image tag, or a newer backup restored under one) would post money on a
schema it was never tested on, and code started on one not migrated yet would fail its first
request that reaches what is missing. So each connection the app's pool opens, the nightly run
and the webhook courier first compare the database's migrations to the code's
(`trust_schema_versions()`, migration 20261105001100, which the app's role may call) and refuse
either, saying which and what to do. Code shipped without its migrations directory can't tell,
and checks nothing.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import psycopg

# The image sets DBMATE_MIGRATIONS_DIR (/app/db/migrations); a checkout has them beside src/.
MIGRATIONS = Path(
    os.environ.get("DBMATE_MIGRATIONS_DIR")
    or Path(__file__).resolve().parents[2] / "db" / "migrations"
)
VERSION = re.compile(r"^(\d{14})_.*\.sql$")


class Mismatch(RuntimeError):
    """The database's migrations aren't this code's; the message says which and what to do."""


def shipped(directory: Path = MIGRATIONS) -> frozenset[str] | None:
    """The versions of the migrations this code ships; None when it ships none to read."""
    if not directory.is_dir():
        return None
    return frozenset(
        found.group(1) for name in os.listdir(directory) if (found := VERSION.match(name))
    )


def check(conn: psycopg.Connection[Any], known: frozenset[str] | None = None) -> None:
    """Refuses a database a newer PMS migrated, or one missing a migration this code ships."""
    known = shipped() if known is None else known
    if known is None:
        return
    applied = {version for (version,) in conn.execute("SELECT trust_schema_versions()")}
    newer = applied - known
    if newer:
        raise Mismatch(
            f"the database was migrated by a newer PMS (migration {max(newer)}), which this one"
            " has never run on: run that version, or restore a backup made by this one"
        )
    missing = sorted(known - applied)
    if missing:
        more = f" and {len(missing) - 1} more" if len(missing) > 1 else ""
        raise Mismatch(
            f"the database lacks this PMS's migration {missing[0]}{more}: run the migrations"
            " first (dbmate up)"
        )
