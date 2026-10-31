"""API keys (ADR 0011, migration 0201): software, such as an AI, reads the PMS with a key and
nothing else; no key moves money.

An admin makes a key in Settings, AI, names it and sees its secret once: `occ_` and a random
token, kept only as its SHA-256 (trust_api_keys), with its first twelve characters to tell keys
apart in the list. An admin takes a key back (trust_api_key_revocations), once; from then on it
opens nothing.

Software sends the key as `Authorization: Bearer occ_...`. The app looks it up as trust_app, then
runs the rest of the request's transaction as trust_ai_agent (SET LOCAL ROLE): what that role
reads, the reports, and no grant that writes. The database holds that line, not this module. A
key names no staff member, and its company is always the key's: no request names one. A key is
not a session and a session is not a key: the cookie endpoints read only the cookie, these only
the header. Each key may make PER_MINUTE requests a minute in this process (429 past it, with
Retry-After). A key that doesn't exist, was taken back or is malformed is refused alike (401),
never saying which.
"""

from __future__ import annotations

import math
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, StringConstraints

from pms import accounts, exports
from pms.api.deps import Conn, Staff
from pms.api.staff import roles_of

router = APIRouter(prefix="/api/ai")

PREFIX = "occ_"
# The secret: "occ_" and 43 characters of a URL-safe token (32 random bytes).
SECRET = re.compile(r"occ_[A-Za-z0-9_-]{20,100}")
SHOWN = 12  # "occ_" and the next eight, in lists
NOT_VALID = "this key was taken back or doesn't exist"
ADMINS = "only an admin sees, makes and takes back API keys"

# Requests a key may make in a minute, counted in this process (the tests lower it).
PER_MINUTE = 120
WINDOW = 60.0
_lock = threading.Lock()
_recent: dict[UUID, deque[float]] = {}

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


# --- staff: an admin's keys ---------------------------------------------------------------------


class KeyRow(BaseModel):
    id: UUID
    name: str
    # The secret's first characters, to tell keys apart; never enough to use one.
    prefix: str
    made_at: datetime
    made_by: str
    revoked_at: datetime | None


class NewKey(BaseModel):
    name: Name


class MadeKey(BaseModel):
    id: UUID
    name: str
    prefix: str
    # Shown this once; the PMS keeps only its SHA-256.
    secret: str


def admin_only(session: Staff, conn: Conn) -> accounts.Session:
    if "admin" not in roles_of(conn, session):
        raise HTTPException(403, ADMINS)
    return session


Admin = Annotated[accounts.Session, Depends(admin_only)]


@router.get("/keys")
def keys(session: Admin, conn: Conn) -> list[KeyRow]:
    """The company's keys, newest first, each standing or when it was taken back."""
    rows = conn.execute(
        "SELECT k.id, k.name, k.prefix, k.created_at, s.display_name, r.created_at"
        " FROM trust_api_keys AS k JOIN trust_staff AS s ON s.id = k.recorded_by"
        " LEFT JOIN trust_api_key_revocations AS r ON r.key_id = k.id"
        " WHERE k.pmc_id = %s ORDER BY k.created_at DESC, k.id",
        (session.pmc_id,),
    ).fetchall()
    return [
        KeyRow(id=i, name=n, prefix=p, made_at=m, made_by=b, revoked_at=r)
        for i, n, p, m, b, r in rows
    ]


@router.post("/keys", status_code=201)
def make_key(session: Admin, conn: Conn, new: NewKey) -> MadeKey:
    """A new key; its secret is in this answer and nowhere else, ever."""
    secret = PREFIX + secrets.token_urlsafe(32)
    row = conn.execute(
        "INSERT INTO trust_api_keys (pmc_id, name, prefix, secret_sha256)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (session.pmc_id, new.name, secret[:SHOWN], accounts.digest(secret)),
    ).fetchone()
    assert row is not None
    return MadeKey(id=row[0], name=new.name, prefix=secret[:SHOWN], secret=secret)


@router.post("/keys/{key_id}/revoke", status_code=204)
def revoke_key(session: Admin, conn: Conn, key_id: UUID) -> None:
    """Takes the key back: it opens nothing from its next request on."""
    found = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM trust_api_key_revocations AS r WHERE r.key_id = k.id)"
        " FROM trust_api_keys AS k WHERE k.pmc_id = %s AND k.id = %s",
        (session.pmc_id, key_id),
    ).fetchone()
    if found is None:
        raise HTTPException(404, "no such API key")
    if found[0]:
        raise HTTPException(409, "this key was taken back already")
    try:
        conn.execute(
            "INSERT INTO trust_api_key_revocations (key_id, pmc_id) VALUES (%s, %s)",
            (key_id, session.pmc_id),
        )
    except psycopg.errors.UniqueViolation:
        # Another admin took it back at the same moment.
        raise HTTPException(409, "this key was taken back already") from None


# --- software, with a key -----------------------------------------------------------------------


@dataclass(frozen=True)
class Key:
    id: UUID
    pmc_id: UUID
    name: str
    company: str


def too_soon(key_id: UUID, now: float | None = None) -> float | None:
    """None when the key may make this request now (and it is counted); else how many seconds
    until it may. A key makes at most PER_MINUTE requests in any WINDOW seconds; `now` is the
    process's monotonic clock unless a test gives one."""
    if now is None:
        now = time.monotonic()
    with _lock:
        recent = _recent.setdefault(key_id, deque())
        while recent and recent[0] <= now - WINDOW:
            recent.popleft()
        if len(recent) >= PER_MINUTE:
            return recent[0] + WINDOW - now
        recent.append(now)
        return None


def refused() -> HTTPException:
    return HTTPException(401, NOT_VALID, headers={"WWW-Authenticate": "Bearer"})


def with_key(request: Request, conn: Conn) -> Key:
    """The standing key the request's Authorization header carries; the rest of the request's
    transaction runs as trust_ai_agent."""
    scheme, _, secret = request.headers.get("authorization", "").partition(" ")
    secret = secret.strip()
    if scheme.lower() != "bearer" or not SECRET.fullmatch(secret):
        raise refused()
    row = conn.execute(
        "SELECT k.id, k.pmc_id, k.name, p.display_name FROM trust_api_keys AS k"
        " JOIN trust_pmcs AS p ON p.pmc_id = k.pmc_id WHERE k.secret_sha256 = %s"
        " AND NOT EXISTS (SELECT 1 FROM trust_api_key_revocations AS r WHERE r.key_id = k.id)",
        (accounts.digest(secret),),
    ).fetchone()
    if row is None:
        raise refused()
    key = Key(id=row[0], pmc_id=row[1], name=row[2], company=row[3])
    wait = too_soon(key.id)
    if wait is not None:
        seconds = max(1, math.ceil(wait))
        raise HTTPException(
            429,
            f"this key made {PER_MINUTE} requests in the last minute: try again in {seconds}"
            f" second{'' if seconds == 1 else 's'}",
            headers={"Retry-After": str(seconds)},
        )
    # Until the transaction ends: whatever the request reads, it reads as the AI's role, which
    # holds no grant that writes (tests/test_privileges.py).
    conn.execute("SET LOCAL ROLE trust_ai_agent")
    return key


WithKey = Annotated[Key, Depends(with_key)]


class Whoami(BaseModel):
    company: str
    key: str


class Report(BaseModel):
    """A report's columns and rows, each value as text (a cent is never a float), or null."""

    columns: list[str]
    rows: list[list[str | None]]


@router.get("/me")
def me(key: WithKey) -> Whoami:
    """Whose books the key reads, and its name."""
    return Whoami(company=key.company, key=key.name)


@router.get("/reports/{name}")
def report(
    name: str,
    key: WithKey,
    conn: Conn,
    args: Annotated[list[str] | None, Query(alias="arg")] = None,
) -> Report:
    """A report (trust_report_<name>) of the key's company: the company is always its first
    argument, from the key, never the request; `arg` gives the rest, in order."""
    function = name if name.startswith("trust_report_") else f"trust_report_{name}"
    try:
        table = exports.run_report(conn, function, [key.pmc_id, *(args or [])])
    except exports.UnknownReport as unknown:
        raise HTTPException(404, str(unknown)) from None
    except exports.ExportError as wrong:
        raise HTTPException(422, str(wrong)) from None
    except psycopg.errors.InsufficientPrivilege:
        raise HTTPException(403, f"an API key may not run {function}") from None
    except (psycopg.errors.DataError, psycopg.errors.InvalidParameterValue) as bad:
        raise HTTPException(422, str(bad).splitlines()[0].removeprefix("trust: ")) from None
    return Report(
        columns=list(table.columns),
        rows=[[None if v is None else exports.text(v) for v in row] for row in table.rows],
    )
