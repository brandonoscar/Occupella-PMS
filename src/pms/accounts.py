"""Logins, invites and sessions (migration 20261011000028; docs/adr/0002-portals-and-exports.md).

Staff invite a person (one of the PMC's staff, owners or tenants) by email. The invite is a
link carrying a random token; only its SHA-256 is stored, it expires after INVITE_DAYS, and it
is accepted once. Accepting sets the password, so a new invite is also how staff reset a
forgotten one. Staff also enroll an authenticator app (TOTP) before their invite is used up,
and every staff sign-in needs its code; a code works once.

Passwords are hashed with argon2id. A sign-in that fails for any reason (no such login, wrong
password, disabled, locked) gets the same answer, after the same work, so it doesn't tell an
attacker which emails have logins. Ten failures in a row lock the login for LOCK_MINUTES.

A session is a random token in an HttpOnly cookie; only its SHA-256 is stored. It lasts
SESSION_HOURS, and signing out, a new password or disabling the login ends it.

Everything runs on a connection acting as trust_app, inside the caller's transaction.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from psycopg import sql

INVITE_DAYS = 7
SESSION_HOURS = 12
MAX_FAILURES = 10
LOCK_MINUTES = 15
MIN_PASSWORD = 12
MAX_PASSWORD = 1024
KINDS = {"staff": "trust_staff", "owner": "trust_owners", "tenant": "trust_tenants"}
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")  # the same shape trust_logins checks
ISSUER = "Occupella PMS"

HASHER = PasswordHasher()
# Checked against when there is no login, so a missing one costs the same time as a wrong
# password.
NO_LOGIN_HASH = HASHER.hash(secrets.token_urlsafe(16))


class Refused(Exception):
    """A request refused for a reason the person may be told."""


class NotFound(Refused):
    """No such record, or one the caller may not see."""


class Conflict(Refused):
    """The request clashes with what is already recorded."""


class SignInRefused(Refused):
    """Wrong email or password, or a login that can't sign in. Always the same message."""

    def __init__(self) -> None:
        super().__init__("the email or password is wrong")


class CodeNeeded(Refused):
    """Staff: the password was right, and the authenticator code is missing or wrong."""


@dataclass(frozen=True)
class Session:
    login_id: UUID
    pmc_id: UUID
    kind: str
    person_id: UUID
    email: str


@dataclass(frozen=True)
class Signed:
    """A new session: the token for the cookie, and who it is for."""

    token: str
    session: Session


@dataclass(frozen=True)
class Invite:
    login_id: UUID
    token: str
    expires_at: datetime


@dataclass(frozen=True)
class InviteView:
    kind: str
    email: str
    name: str
    pmc_name: str
    expires_at: datetime


@dataclass(frozen=True)
class Enrollment:
    """A staff invite's password is set; the authenticator must be confirmed next."""

    totp_uri: str


def digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def check_password(password: str) -> None:
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise Refused(f"a password has {MIN_PASSWORD} to {MAX_PASSWORD} characters")


def _one(conn: psycopg.Connection[Any], query: str, params: tuple[Any, ...]) -> Any:
    row = conn.execute(query, params).fetchone()
    return None if row is None else row


# --- invites ------------------------------------------------------------------------------


def create_invite(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    kind: str,
    person_id: UUID,
    email: str,
    created_by: UUID | None,
) -> Invite:
    """An invite for a person of the PMC, making their login if they have none yet."""
    if kind not in KINDS:
        raise Refused(f"a login is for {', '.join(KINDS)}, not {kind!r}")
    email = normalize_email(email)
    if not EMAIL.fullmatch(email):
        raise Refused(f"{email!r} isn't an email address")
    person = _one(
        conn,
        sql.SQL("SELECT display_name FROM {} WHERE pmc_id = %s AND id = %s")
        .format(sql.Identifier(KINDS[kind]))
        .as_string(conn),
        (pmc_id, person_id),
    )
    if person is None:
        raise NotFound(f"no {kind} {person_id} in this PMC")
    login = _one(
        conn,
        f"SELECT id, email FROM trust_logins WHERE pmc_id = %s AND {kind}_id = %s",
        (pmc_id, person_id),
    )
    if login is None:
        clash = _one(
            conn, "SELECT 1 FROM trust_logins WHERE pmc_id = %s AND email = %s", (pmc_id, email)
        )
        if clash is not None:
            raise Conflict(f"{email} already signs in to this PMC as someone else")
        login_id = _one(
            conn,
            f"INSERT INTO trust_logins (pmc_id, kind, {kind}_id, email)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (pmc_id, kind, person_id, email),
        )[0]
    elif login[1] != email:
        raise Conflict(f"this {kind} signs in as {login[1]}")
    else:
        login_id = login[0]
    token = secrets.token_urlsafe(32)
    expires_at = _one(
        conn,
        "INSERT INTO trust_invites (pmc_id, login_id, token_hash, created_by, expires_at)"
        " VALUES (%s, %s, %s, %s, now() + make_interval(days => %s)) RETURNING expires_at",
        (pmc_id, login_id, digest(token), created_by, INVITE_DAYS),
    )[0]
    return Invite(login_id, token, expires_at)


def _open_invite(conn: psycopg.Connection[Any], token: str, lock: bool) -> Any:
    """The invite's row if it can still be used: not accepted, not expired, login enabled."""
    row = _one(
        conn,
        "SELECT i.id, i.pmc_id, l.id, l.kind, l.email, i.expires_at"
        " FROM trust_invites AS i JOIN trust_logins AS l ON l.id = i.login_id"
        " WHERE i.token_hash = %s AND i.accepted_at IS NULL AND i.expires_at > now()"
        " AND l.disabled_at IS NULL" + (" FOR UPDATE OF i" if lock else ""),
        (digest(token),),
    )
    if row is None:
        raise NotFound("this invite link can't be used: ask for a new one")
    return row


def names(conn: psycopg.Connection[Any], login_id: UUID) -> tuple[str, str]:
    """The person's name and their PMC's."""
    name, pmc_name = _one(
        conn,
        "SELECT coalesce(s.display_name, o.display_name, t.display_name), p.display_name"
        " FROM trust_logins AS l JOIN trust_pmcs AS p ON p.pmc_id = l.pmc_id"
        " LEFT JOIN trust_staff AS s ON s.id = l.staff_id"
        " LEFT JOIN trust_owners AS o ON o.id = l.owner_id"
        " LEFT JOIN trust_tenants AS t ON t.id = l.tenant_id WHERE l.id = %s",
        (login_id,),
    )
    return name, pmc_name


def view_invite(conn: psycopg.Connection[Any], token: str) -> InviteView:
    _, _, login_id, kind, email, expires_at = _open_invite(conn, token, lock=False)
    name, pmc_name = names(conn, login_id)
    return InviteView(kind, email, name, pmc_name, expires_at)


def accept_invite(conn: psycopg.Connection[Any], token: str, password: str) -> Signed | Enrollment:
    """Set the password. An owner or a tenant is signed in; staff enroll an authenticator
    next (confirm_invite), and their invite stays open until they do."""
    check_password(password)
    invite_id, pmc_id, login_id, kind, _, _ = _open_invite(conn, token, lock=True)
    totp_secret = pyotp.random_base32() if kind == "staff" else None
    conn.execute(
        "INSERT INTO trust_login_secrets (login_id, pmc_id, password_hash, totp_secret)"
        " VALUES (%s, %s, %s, %s)"
        " ON CONFLICT (login_id) DO UPDATE SET password_hash = excluded.password_hash,"
        " totp_secret = excluded.totp_secret, totp_confirmed_at = NULL, totp_last_step = NULL,"
        " failed_sign_ins = 0, locked_until = NULL, updated_at = now()",
        (login_id, pmc_id, HASHER.hash(password), totp_secret),
    )
    if totp_secret is not None:
        email = _one(conn, "SELECT email FROM trust_logins WHERE id = %s", (login_id,))[0]
        uri = pyotp.TOTP(totp_secret).provisioning_uri(name=email, issuer_name=ISSUER)
        return Enrollment(uri)
    return _finish_invite(conn, invite_id, login_id)


def confirm_invite(conn: psycopg.Connection[Any], token: str, code: str) -> Signed:
    """Staff: the first code from the authenticator app proves it is set up; then the invite
    is used and they are signed in."""
    invite_id, _, login_id, kind, _, _ = _open_invite(conn, token, lock=True)
    secret = _one(
        conn,
        "SELECT totp_secret, totp_last_step FROM trust_login_secrets WHERE login_id = %s"
        " FOR UPDATE",
        (login_id,),
    )
    if kind != "staff" or secret is None or secret[0] is None:
        raise Refused("set a password from this invite first")
    step = _code_step(secret[0], code, secret[1])
    if step is None:
        raise CodeNeeded("that code is wrong or was already used: try the next one")
    conn.execute(
        "UPDATE trust_login_secrets SET totp_confirmed_at = now(), totp_last_step = %s,"
        " updated_at = now() WHERE login_id = %s",
        (step, login_id),
    )
    return _finish_invite(conn, invite_id, login_id)


def _finish_invite(conn: psycopg.Connection[Any], invite_id: UUID, login_id: UUID) -> Signed:
    conn.execute("UPDATE trust_invites SET accepted_at = now() WHERE id = %s", (invite_id,))
    # A new password ends every session the old one opened.
    conn.execute(
        "UPDATE trust_sessions SET ended_at = now() WHERE login_id = %s AND ended_at IS NULL",
        (login_id,),
    )
    return _new_session(conn, login_id)


# --- signing in and out -------------------------------------------------------------------


def sign_in(
    conn: psycopg.Connection[Any], pmc_id: UUID, email: str, password: str, code: str | None
) -> Signed:
    """A new session, or SignInRefused (the same for every reason) or CodeNeeded (staff whose
    password was right). A failure is committed before it is raised, so it counts."""
    row = _one(
        conn,
        "SELECT l.id, l.kind, s.password_hash, s.totp_secret, s.totp_confirmed_at,"
        " s.totp_last_step, s.failed_sign_ins, s.locked_until > now()"
        " FROM trust_logins AS l JOIN trust_login_secrets AS s ON s.login_id = l.id"
        " WHERE l.pmc_id = %s AND l.email = %s AND l.disabled_at IS NULL FOR UPDATE OF s",
        (pmc_id, normalize_email(email)),
    )
    if row is None:
        _verify(NO_LOGIN_HASH, password)
        raise SignInRefused()
    login_id, kind, password_hash, totp_secret, confirmed, last_step, failures, locked = row
    right = _verify(password_hash, password)
    if locked:
        raise SignInRefused()  # even with the right password, until the lock runs out
    if not right:
        _count_failure(conn, login_id, failures)
        raise SignInRefused()
    step = None
    if kind == "staff":
        if confirmed is None:
            raise Refused("finish setting up from your invite link first")
        if not code:
            raise CodeNeeded("enter the code from your authenticator app")
        step = _code_step(totp_secret, code, last_step)
        if step is None:
            _count_failure(conn, login_id, failures)
            raise CodeNeeded("that code is wrong or was already used: try the next one")
    conn.execute(
        "UPDATE trust_login_secrets SET failed_sign_ins = 0, locked_until = NULL,"
        " totp_last_step = coalesce(%s, totp_last_step),"
        " password_hash = coalesce(%s, password_hash), updated_at = now() WHERE login_id = %s",
        (
            step,
            HASHER.hash(password) if HASHER.check_needs_rehash(password_hash) else None,
            login_id,
        ),
    )
    return _new_session(conn, login_id)


def _verify(password_hash: str, password: str) -> bool:
    try:
        return HASHER.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def _count_failure(conn: psycopg.Connection[Any], login_id: UUID, failures: int) -> None:
    locks = failures + 1 >= MAX_FAILURES
    conn.execute(
        "UPDATE trust_login_secrets SET failed_sign_ins = %s,"
        " locked_until = CASE WHEN %s THEN now() + make_interval(mins => %s)"
        " ELSE locked_until END, updated_at = now() WHERE login_id = %s",
        (0 if locks else failures + 1, locks, LOCK_MINUTES, login_id),
    )
    conn.commit()


def _code_step(secret: str, code: str, last_step: int | None) -> int | None:
    """The time step whose code this is (the current one or either neighbour), if it is newer
    than the last one used."""
    totp = pyotp.TOTP(secret)
    now = int(time.time()) // totp.interval
    for step in (now - 1, now, now + 1):
        if hmac.compare_digest(totp.generate_otp(step), code.strip()) and (
            last_step is None or step > last_step
        ):
            return step
    return None


def _new_session(conn: psycopg.Connection[Any], login_id: UUID) -> Signed:
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO trust_sessions (pmc_id, login_id, token_hash, expires_at)"
        " SELECT pmc_id, id, %s, now() + make_interval(hours => %s)"
        " FROM trust_logins WHERE id = %s",
        (digest(token), SESSION_HOURS, login_id),
    )
    session = session_for(conn, token)
    assert session is not None
    return Signed(token, session)


def session_for(conn: psycopg.Connection[Any], token: str) -> Session | None:
    """The live session behind a cookie's token, if there is one."""
    row = _one(
        conn,
        "SELECT l.id, l.pmc_id, l.kind, coalesce(l.staff_id, l.owner_id, l.tenant_id), l.email"
        " FROM trust_sessions AS s JOIN trust_logins AS l ON l.id = s.login_id"
        " WHERE s.token_hash = %s AND s.ended_at IS NULL AND s.expires_at > now()"
        " AND l.disabled_at IS NULL",
        (digest(token),),
    )
    return None if row is None else Session(*row)


def sign_out(conn: psycopg.Connection[Any], token: str) -> None:
    conn.execute(
        "UPDATE trust_sessions SET ended_at = now() WHERE token_hash = %s AND ended_at IS NULL",
        (digest(token),),
    )


def disable_login(conn: psycopg.Connection[Any], pmc_id: UUID, login_id: UUID) -> None:
    """No more sign-ins, and every session ends."""
    done = conn.execute(
        "UPDATE trust_logins SET disabled_at = now()"
        " WHERE pmc_id = %s AND id = %s AND disabled_at IS NULL",
        (pmc_id, login_id),
    )
    if done.rowcount == 0:
        raise NotFound(f"no enabled login {login_id} in this PMC")
    conn.execute(
        "UPDATE trust_sessions SET ended_at = now() WHERE login_id = %s AND ended_at IS NULL",
        (login_id,),
    )
