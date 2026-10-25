"""What the ZAP scan (zap.yml) needs from a running PMS: a staff member's session to scan as, and
the API's schema to attack as them.

`python -m tools.zap_session` builds the demo PMC (src/pms/demo.py: synthetic, something on every
page) on the database in $DATABASE_URL, adds a staff member to it, accepts their invite over HTTP
at --origin with a throwaway password and authenticator as the portal does, and writes:

  --cookie-file  the Cookie header's value for that session (read only by its owner), which the
                 workflow hands ZAP through its environment (ZAP_AUTH_HEADER_VALUE), never on a
                 command line or in the log;
  --schema-file  the API's OpenAPI schema as the app serves it, naming --origin as its server,
                 less the operations that would end the scan's own session (LEFT_OUT).

The database is CI's throwaway one: no real data, and the session dies with it. Without
--origin (scripts/check.sh) it does all this on a throwaway database of its own, migrated, with
the API served as `python -m pms.api` serves it, and checks the session signs in.

usage: PYTHONPATH=src python -m tools.zap_session [--origin http://127.0.0.1:8123] \\
           --cookie-file cookie.txt --schema-file openapi.json
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import psycopg
import pyotp
from psycopg import sql

from pms import accounts, demo
from pms.api import staff
from pms.api.deps import COOKIE
from tools import speed
from tools.check_rollback import Dbmate
from tools.scratch_db import scratch_database

SCANNER = "ZAP Scanner"
# What the active scan never sends, each with why: signing out ends the session it scans as, and
# disabling a login could disable its own. Everything else it attacks.
LEFT_OUT = {
    ("post", "/api/sign-out"): "ends the scan's own session",
    ("post", "/api/logins/{login_id}/disable"): "could disable the scan's own login",
}


def sign_in(conn: psycopg.Connection[Any], built: demo.Demo, origin: str) -> str:
    """A new staff member of the demo PMC signed in over HTTP; their session token."""
    b = demo.Builder(conn, built.pmc_id)
    with b.as_staff(built.manager) as s:
        member = staff.add(s, conn, staff.NewMember(name=SCANNER)).id
        invite = accounts.create_invite(
            conn, built.pmc_id, "staff", member, "zap.scanner@example.com", s.person_id
        )
    # Every change is JSON from the app's own origin, as the portal sends it.
    with httpx2.Client(base_url=origin, headers={"Origin": origin}, timeout=60) as client:
        # A throwaway password for a synthetic login in a throwaway database.
        password = secrets.token_urlsafe(16)
        accepted = client.post(
            "/api/invites/accept", json={"token": invite.token, "password": password}
        )
        accepted.raise_for_status()
        secret = parse_qs(urlsplit(accepted.json()["totp_uri"]).query)["secret"][0]
        confirmed = client.post(
            "/api/invites/confirm", json={"token": invite.token, "code": pyotp.TOTP(secret).now()}
        )
        confirmed.raise_for_status()
        token = client.cookies.get(COOKIE)
    if not token:
        raise RuntimeError("signing in set no session cookie")
    return str(token)


def schema(origin: str) -> dict[str, Any]:
    """The API's schema as the app at `origin` serves it, naming it as the server, less
    LEFT_OUT."""
    with httpx2.Client(base_url=origin, timeout=60) as client:
        served = client.get("/openapi.json")
        served.raise_for_status()
        made: dict[str, Any] = served.json()
    for method, path in LEFT_OUT:
        operations = made["paths"].get(path, {})
        if operations.pop(method, None) is None:
            raise RuntimeError(f"{method.upper()} {path} isn't in the schema; update LEFT_OUT")
        if not operations:
            del made["paths"][path]
    made["servers"] = [{"url": origin}]
    return made


def write_private(path: Path, text: str) -> None:
    """`text` in a file only its owner may read, made so before anything is in it."""
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(text)


def prepare(url: str, origin: str, cookie_file: Path, schema_file: Path) -> int:
    """The demo PMC on the migrated database at `url`, its scanner signed in at `origin`; the
    session and the schema written. The number of operations the schema keeps."""
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier("trust_app")))
        built = demo.build(conn, datetime.now(UTC).date(), origin)
        token = sign_in(conn, built, origin)
    write_private(cookie_file, f"{COOKIE}={token}")
    made = schema(origin)
    schema_file.write_text(json.dumps(made, indent=1))
    return sum(len(ops) for ops in made["paths"].values())


def on_its_own(server: str, cookie_file: Path, schema_file: Path) -> int:
    """`prepare` on a throwaway database of its own with the API served on it, checking the
    session is a signed-in staff member's."""
    dbmate = shutil.which("dbmate")
    if dbmate is None:
        raise RuntimeError("dbmate is not on PATH")
    with scratch_database(server, "pms_zap") as url:
        migrated = Dbmate(dbmate, speed.MIGRATIONS, url).run("--no-dump-schema", "up")
        if migrated.returncode != 0:
            raise RuntimeError(f"dbmate up failed:\n{migrated.stderr}")
        with speed.serving(url) as served:
            operations = prepare(url, served.origin, cookie_file, schema_file)
            me = httpx2.get(f"{served.origin}/api/me", headers={"Cookie": cookie_file.read_text()})
            if me.status_code != 200 or me.json().get("kind") != "staff":
                raise RuntimeError(f"the session isn't a staff member's: {me.status_code}")
    return operations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--origin")
    parser.add_argument("--cookie-file", type=Path, required=True)
    parser.add_argument("--schema-file", type=Path, required=True)
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    if not url:
        print(
            "FAIL: needs DATABASE_URL (a migrated throwaway database; with no --origin, a server)."
        )
        return 1
    try:
        if args.origin:
            operations = prepare(url, args.origin, args.cookie_file, args.schema_file)
        else:
            operations = on_its_own(url, args.cookie_file, args.schema_file)
    except (RuntimeError, speed.ServerDown) as failed:
        print(f"FAIL: {failed}")
        return 1
    where = args.origin or "a throwaway database"
    print(f"ok: {SCANNER} signed in to the demo PMC at {where}; {operations} operations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
