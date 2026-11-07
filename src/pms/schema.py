"""Whether the database's migrations are the ones this code ships (docs/edge-cases.md H-041).

dbmate records each migration it applied in schema_migrations. Code started on a database a newer
PMS migrated (an older image tag, or a newer backup restored under one) would post money on a
schema it was never tested on, and code started on one not migrated yet would fail its first
request that reaches what is missing. So each connection the app's pool opens, the nightly run
and the webhook courier first compare the database's migrations to the code's
(`trust_schema_versions()`, migration 20261105001100, which the app's role may call) and refuse
either, saying which and what to do. Code shipped without its migrations directory can't tell,
and checks nothing.

They also refuse a database whose own HTTP API can reach the books (`exposed`; the OWASP review
of 11/06). Supabase serves its projects' public schema through a Data API as `anon` and
`authenticated`, and grants each new table, sequence and function to them and to `service_role`
by default; the migrations revoke what PUBLIC holds, never those grants, and the books have no
row-level security. So anything the roles such an API answers as hold in the schema, or may act
as through a role they were granted, is refused, naming the first and how many more.
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


class Exposed(Mismatch):
    """The database's own API (Supabase's, any PostgREST) can reach the books."""


# The roles an API in the database answers as: PostgREST logs in as authenticator and becomes
# anon or authenticated (or, on Supabase, service_role), and each of those may act as any role it
# was granted. What any of them holds on the schema's tables, sequences and functions is exposed.
EXPOSED = """
WITH RECURSIVE api(oid) AS (
    SELECT oid FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'authenticator')
  UNION
    SELECT m.roleid FROM pg_auth_members AS m JOIN api ON m.member = api.oid
)
SELECT r.rolname, held.privilege, held.object
FROM (
    SELECT a.grantee, a.privilege_type, c.relname
    FROM pg_class AS c, aclexplode(c.relacl) AS a
    WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'v', 'm', 'S', 'p', 'f')
  UNION ALL
    SELECT a.grantee, a.privilege_type, p.proname
    FROM pg_proc AS p, aclexplode(p.proacl) AS a
    WHERE p.pronamespace = 'public'::regnamespace
) AS held(grantee, privilege, object)
JOIN pg_roles AS r ON r.oid = held.grantee
WHERE held.grantee IN (SELECT oid FROM api)
ORDER BY r.rolname COLLATE "C", held.object COLLATE "C", held.privilege COLLATE "C"
"""


def exposed(conn: psycopg.Connection[Any]) -> None:
    """Refuses a database whose own HTTP API can reach anything of the books."""
    found = conn.execute(EXPOSED).fetchall()
    if found:
        (role, privilege, name), more = found[0], len(found) - 1
        raise Exposed(
            f"the database's own API can reach the books: {role} holds {privilege} on {name}"
            + (f" and {more} more" if more else "")
            + ". Revoke what the API's roles hold in schema public, or turn that API off"
            " (docs/install.md, 'A database with its own API')"
        )


def shipped(directory: Path | None = None) -> frozenset[str] | None:
    """The versions of the migrations this code ships; None when it ships none to read."""
    directory = MIGRATIONS if directory is None else directory
    if not directory.is_dir():
        return None
    return frozenset(
        found.group(1) for name in os.listdir(directory) if (found := VERSION.match(name))
    )


def check(conn: psycopg.Connection[Any], known: frozenset[str] | None = None) -> None:
    """Refuses a database a newer PMS migrated, one missing a migration this code ships, or one
    whose own API can reach the books."""
    exposed(conn)
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
