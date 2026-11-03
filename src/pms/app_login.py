"""The login the app connects to the database with: `python -m pms.app_login`.

A self-hosted install (compose.selfhost.yaml) runs it after the migrations, as the database's
owner. It makes (or, run again, gives a new password to) the login role `pms_app`, which holds no
privilege of its own and may only become trust_app (`SET ROLE`, never inherited). The app, the
nightly run and every command in the app's container connect as it and act as trust_app, so even
SQL that ran `RESET ROLE` would be left able to read and write nothing; nothing in the app's
container connects as the superuser.

The password comes from PMS_APP_DB_PASSWORD, never the command line, and is never printed. It is
sent as its SCRAM-SHA-256 verifier, worked out here, so the password itself is in no statement the
server could log.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sys
from typing import Any

import psycopg
from psycopg import sql

ROLE = "pms_app"
ACTS_AS = "trust_app"
ITERATIONS = 4096  # as Postgres's own scram_iterations by default


def scram_verifier(password: str, salt: bytes | None = None, iterations: int = ITERATIONS) -> str:
    """The password as Postgres keeps it (RFC 5802/7677, its pg_authid.rolpassword format):
    SCRAM-SHA-256$<iterations>:<salt>$<StoredKey>:<ServerKey>."""
    salt = secrets.token_bytes(16) if salt is None else salt
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()

    def b64(raw: bytes) -> str:
        return base64.b64encode(raw).decode()

    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def ensure(conn: psycopg.Connection[Any], password: str, role: str = ROLE) -> None:
    """Makes the login role, or gives it the new password, and lets it become trust_app and
    nothing else. Run as the database's owner."""
    if not password:
        raise ValueError("the app's database password is empty")
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    conn.execute(
        sql.SQL(
            "{} ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION"
            " NOBYPASSRLS PASSWORD {}"
        ).format(
            sql.SQL("ALTER" if exists else "CREATE"),
            sql.Identifier(role),
            sql.Literal(scram_verifier(password)),
        )
    )
    # Any other membership it was given by hand goes: it may become trust_app alone.
    for (other,) in conn.execute(
        "SELECT r.rolname FROM pg_auth_members AS m JOIN pg_roles AS r ON r.oid = m.roleid"
        " WHERE m.member = (SELECT oid FROM pg_roles WHERE rolname = %s) AND r.rolname <> %s",
        (role, ACTS_AS),
    ).fetchall():
        conn.execute(
            sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(other), sql.Identifier(role))
        )
    conn.execute(
        sql.SQL("GRANT {} TO {} WITH INHERIT FALSE, SET TRUE").format(
            sql.Identifier(ACTS_AS), sql.Identifier(role)
        )
    )


def main() -> int:
    password = os.environ.get("PMS_APP_DB_PASSWORD", "")
    if not password:
        print(
            "FAIL: set PMS_APP_DB_PASSWORD (in .env): the app's database password", file=sys.stderr
        )
        return 2
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        ensure(conn, password, ROLE)
    print(f"ok: the app connects as {ROLE}, which may only act as {ACTS_AS}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
