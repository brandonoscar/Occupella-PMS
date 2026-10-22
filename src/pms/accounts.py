"""Logins, invites and sessions (migration 20261011000028; docs/adr/0002-portals-and-exports.md).

Staff invite a person (one of the PMC's staff, owners or tenants) by email. The invite is a
link carrying a random token; only its SHA-256 is stored, it expires after INVITE_DAYS, and it
is accepted once. Accepting sets the password, so a new invite is also how staff reset a
forgotten one; an owner or a tenant may ask for one by email themselves (request_reset,
docs/adr/0007-email.md). Staff also enroll an authenticator app (TOTP) before their invite is
used up, and every staff sign-in needs its code; a code works once.

Two-step sign-in for owners and tenants (migration 20261022000153): an owner or a tenant may turn
it on for their own login (start_two_step, then confirm_two_step with the app's first code), and
from then on every sign-in of theirs needs a code too; they turn it off with a code
(turn_off_two_step). A company may require it (trust_sign_in_settings, two_step_required): then an
owner or a tenant without it sets it up when they sign in, after their password and before they
get a session, with the same secret each try until a code confirms it. A secret not yet
confirmed changes nothing at sign-in. Recovery stays an admin's new invite: accepting one a
staff member made clears the authenticator, while a reset the person asked for keeps it, so an
emailed link never gets past it.

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

INVITE_DAYS = 7
SESSION_HOURS = 12
MAX_FAILURES = 10
LOCK_MINUTES = 15
MIN_PASSWORD = 12
MAX_PASSWORD = 1024
# Password resets an owner or a tenant may ask for in an hour (docs/adr/0007-email.md).
RESETS_PER_HOUR = 3
# Each kind of person's queries, written out in full: no part of a query comes from a request.
KINDS = {
    kind: {
        "person": f"SELECT display_name FROM {table} WHERE pmc_id = %s AND id = %s",  # noqa: S608
        "login": f"SELECT id, email FROM trust_logins WHERE pmc_id = %s AND {kind}_id = %s",  # noqa: S608
        "new login": f"INSERT INTO trust_logins (pmc_id, kind, {kind}_id, email)"  # noqa: S608
        f" VALUES (%s, '{kind}', %s, %s) RETURNING id",
    }
    for kind, table in (
        ("staff", "trust_staff"),
        ("owner", "trust_owners"),
        ("tenant", "trust_tenants"),
    )
}
# The shape trust_logins checks ('^[^@\s]+@[^@\s]+\.[^@\s]+$'), written so a string can match it in
# only one way: from the start (fullmatch anchors it too, but CodeQL reads the "^"), the
# domain's first character, then up to its next dot. The database's own form backtracks for a
# time that grows with the square of the length on a crafted address.
EMAIL = re.compile(r"^[^@\s]+@[^@\s][^@\s.]*\.[^@\s]+")
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
    """The password was right, and the authenticator code is missing or wrong: staff, or an
    owner or a tenant with two-step sign-in on (or setting it up)."""


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
class Reset:
    """A password reset to email: whose login, at which address, and its invite."""

    kind: str
    person_id: UUID
    email: str
    invite: Invite


@dataclass(frozen=True)
class InviteView:
    kind: str
    email: str
    name: str
    pmc_name: str
    expires_at: datetime


@dataclass(frozen=True)
class Enrollment:
    """An authenticator to set up: a staff invite's password is set, an owner or a tenant turns
    two-step sign-in on, or one whose company requires it signs in without it. Its first code
    confirms it."""

    totp_uri: str
    secret: str


@dataclass(frozen=True)
class PasswordSet:
    """An owner's or a tenant's reset, asked for by email, set the new password; their
    authenticator stays, so they sign in with the password and a code (no session yet)."""

    pmc_id: UUID


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
    queries = KINDS[kind]
    if _one(conn, queries["person"], (pmc_id, person_id)) is None:
        raise NotFound(f"no {kind} {person_id} in this PMC")
    login = _one(conn, queries["login"], (pmc_id, person_id))
    if login is None:
        clash = _one(
            conn, "SELECT 1 FROM trust_logins WHERE pmc_id = %s AND email = %s", (pmc_id, email)
        )
        if clash is not None:
            raise Conflict(f"{email} already signs in to this PMC as someone else")
        try:
            with conn.transaction():
                login_id = _one(conn, queries["new login"], (pmc_id, person_id, email))[0]
        except psycopg.errors.CheckViolation as refused:
            # A tenant's record at another property signs in as their first (migration 0105).
            raise Conflict(str(refused).splitlines()[0].removeprefix("trust: ")) from None
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


def request_reset(conn: psycopg.Connection[Any], pmc_id: UUID, email: str) -> Reset | None:
    """A new invite, to set a new password, for an owner's or a tenant's login with this email
    that can sign in and asked fewer than RESETS_PER_HOUR times in the last hour; None
    otherwise. The caller answers the same either way, so it doesn't tell who has a login. A
    staff member resets through an admin's new invite instead: an emailed link must not get past
    their authenticator. Accepting it sets the new password, so until then the old one works; an
    owner's or a tenant's two-step sign-in stays on through it (accept_invite)."""
    found = _one(
        conn,
        "SELECT kind, coalesce(owner_id, tenant_id) FROM trust_logins"
        " WHERE pmc_id = %s AND email = %s AND disabled_at IS NULL"
        " AND kind IN ('owner', 'tenant')",
        (pmc_id, normalize_email(email)),
    )
    if found is None:
        return None
    kind, person_id = found
    asked = _one(
        conn,
        "SELECT count(*) FROM trust_emails WHERE pmc_id = %s AND kind = 'password_reset'"
        " AND coalesce(owner_id, tenant_id) = %s AND created_at > now() - interval '1 hour'",
        (pmc_id, person_id),
    )[0]
    if asked >= RESETS_PER_HOUR:
        return None
    invite = create_invite(conn, pmc_id, kind, person_id, email, None)
    return Reset(kind, person_id, normalize_email(email), invite)


def _open_invite(conn: psycopg.Connection[Any], token: str, lock: bool) -> Any:
    """The invite's row if it can still be used: not accepted, not expired, login enabled; last,
    the staff member who made it (None: a reset the person asked for, or the command line's)."""
    row = _one(
        conn,
        "SELECT i.id, i.pmc_id, l.id, l.kind, l.email, i.expires_at, i.created_by"  # noqa: S608
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
    _, _, login_id, kind, email, expires_at, _ = _open_invite(conn, token, lock=False)
    name, pmc_name = names(conn, login_id)
    return InviteView(kind, email, name, pmc_name, expires_at)


def accept_invite(
    conn: psycopg.Connection[Any], token: str, password: str
) -> Signed | Enrollment | PasswordSet:
    """Set the password. Staff enroll an authenticator next (confirm_invite), and their invite
    stays open until they do. An owner or a tenant is signed in, unless:
      - the invite is a reset they asked for (no staff member made it) and their two-step sign-in
        is on: it stays on, an emailed link must not get past it, so they sign in next with the
        new password and a code (PasswordSet);
      - their company requires two-step sign-in and they have none: they set it up next
        (confirm_invite), as staff do.
    An invite a staff member made clears an owner's or a tenant's authenticator, so one who lost
    their phone gets back in: recovery stays an admin's new invite."""
    check_password(password)
    invite_id, pmc_id, login_id, kind, _, _, made_by = _open_invite(conn, token, lock=True)
    hashed = HASHER.hash(password)
    if kind == "staff" or made_by is not None:
        totp_secret = pyotp.random_base32() if kind == "staff" else None
        conn.execute(
            "INSERT INTO trust_login_secrets (login_id, pmc_id, password_hash, totp_secret)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (login_id) DO UPDATE SET password_hash = excluded.password_hash,"
            " totp_secret = excluded.totp_secret, totp_confirmed_at = NULL,"
            " totp_last_step = NULL, failed_sign_ins = 0, locked_until = NULL, updated_at = now()",
            (login_id, pmc_id, hashed, totp_secret),
        )
        if totp_secret is not None:
            return _enrollment(conn, login_id, totp_secret)
    else:
        # The person's own reset: the authenticator stays, and while it is on, so do the failed
        # sign-ins counted and any lock, which an emailed link must not clear either.
        conn.execute(
            "INSERT INTO trust_login_secrets AS s (login_id, pmc_id, password_hash)"
            " VALUES (%s, %s, %s)"
            " ON CONFLICT (login_id) DO UPDATE SET password_hash = excluded.password_hash,"
            " failed_sign_ins = CASE WHEN s.totp_confirmed_at IS NULL THEN 0"
            " ELSE s.failed_sign_ins END,"
            " locked_until = CASE WHEN s.totp_confirmed_at IS NULL THEN NULL"
            " ELSE s.locked_until END, updated_at = now()",
            (login_id, pmc_id, hashed),
        )
    secret, on = _one(
        conn,
        "SELECT totp_secret, totp_confirmed_at IS NOT NULL FROM trust_login_secrets"
        " WHERE login_id = %s",
        (login_id,),
    )
    if on:
        _use_invite(conn, invite_id, login_id)
        return PasswordSet(pmc_id)
    if two_step_required(conn, pmc_id):
        return _setup(conn, login_id, secret)
    return _finish_invite(conn, invite_id, login_id)


def confirm_invite(conn: psycopg.Connection[Any], token: str, code: str) -> Signed:
    """The first code from the authenticator app proves it is set up (staff, or an owner or a
    tenant whose company requires two-step sign-in); then the invite is used and they are signed
    in."""
    invite_id, _, login_id, kind, _, _, _ = _open_invite(conn, token, lock=True)
    secret = _one(
        conn,
        "SELECT totp_secret, totp_last_step, totp_confirmed_at FROM trust_login_secrets"
        " WHERE login_id = %s FOR UPDATE",
        (login_id,),
    )
    # An owner's or a tenant's authenticator is set up here only while it waits for its first
    # code: one already on is never a way past the password.
    if secret is None or secret[0] is None or (kind != "staff" and secret[2] is not None):
        raise Refused("set a password from this invite first")
    step = _code_step(secret[0], code, secret[1])
    if step is None:
        raise CodeNeeded(WRONG_CODE)
    conn.execute(
        "UPDATE trust_login_secrets SET totp_confirmed_at = now(), totp_last_step = %s,"
        " updated_at = now() WHERE login_id = %s",
        (step, login_id),
    )
    return _finish_invite(conn, invite_id, login_id)


def _use_invite(conn: psycopg.Connection[Any], invite_id: UUID, login_id: UUID) -> None:
    conn.execute("UPDATE trust_invites SET accepted_at = now() WHERE id = %s", (invite_id,))
    # A new password ends every session the old one opened.
    conn.execute(
        "UPDATE trust_sessions SET ended_at = now() WHERE login_id = %s AND ended_at IS NULL",
        (login_id,),
    )


def _finish_invite(conn: psycopg.Connection[Any], invite_id: UUID, login_id: UUID) -> Signed:
    _use_invite(conn, invite_id, login_id)
    return _new_session(conn, login_id)


# --- two-step sign-in for owners and tenants ------------------------------------------------

ENTER_CODE = "enter the code from your authenticator app"
WRONG_CODE = "that code is wrong or was already used: try the next one"


def two_step_required(conn: psycopg.Connection[Any], pmc_id: UUID) -> bool:
    """Whether the company requires two-step sign-in of its owners and tenants: the latest row of
    trust_sign_in_settings (none: it doesn't)."""
    row = _one(
        conn,
        "SELECT require_two_step FROM trust_sign_in_settings WHERE pmc_id = %s"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (pmc_id,),
    )
    return bool(row and row[0])


def two_step_on(conn: psycopg.Connection[Any], login_id: UUID) -> bool:
    """Whether the login signs in with a code: its authenticator is confirmed."""
    row = _one(
        conn,
        "SELECT totp_confirmed_at IS NOT NULL FROM trust_login_secrets WHERE login_id = %s",
        (login_id,),
    )
    return bool(row and row[0])


def _enrollment(conn: psycopg.Connection[Any], login_id: UUID, secret: str) -> Enrollment:
    email = _one(conn, "SELECT email FROM trust_logins WHERE id = %s", (login_id,))[0]
    uri = pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)
    return Enrollment(uri, secret)


def _setup(conn: psycopg.Connection[Any], login_id: UUID, secret: str | None) -> Enrollment:
    """An owner's or a tenant's authenticator to set up: the secret waiting for its first code,
    or a new one kept to wait, so each try shows the same one until a code confirms it."""
    if secret is None:
        secret = pyotp.random_base32()
        conn.execute(
            "UPDATE trust_login_secrets SET totp_secret = %s, updated_at = now()"
            " WHERE login_id = %s",
            (secret, login_id),
        )
    return _enrollment(conn, login_id, secret)


def _own_secret(conn: psycopg.Connection[Any], session: Session) -> Any:
    """An owner's or a tenant's own authenticator, locked for the change: its secret, when it
    was confirmed, the last step used, the failed sign-ins and whether the login is locked."""
    if session.kind not in ("owner", "tenant"):
        raise Refused("staff sign in with the authenticator they set up from their invite")
    row = _one(
        conn,
        "SELECT totp_secret, totp_confirmed_at, totp_last_step, failed_sign_ins,"
        " coalesce(locked_until > now(), false) FROM trust_login_secrets WHERE login_id = %s"
        " FOR UPDATE",
        (session.login_id,),
    )
    if row is None:
        raise NotFound("set a password from your invite first")
    return row


def start_two_step(conn: psycopg.Connection[Any], session: Session) -> Enrollment:
    """An owner or a tenant turns two-step sign-in on: the authenticator to set up. Nothing
    changes at sign-in until its first code confirms it (confirm_two_step)."""
    secret, confirmed, _, _, _ = _own_secret(conn, session)
    if confirmed is not None:
        raise Conflict("two-step sign-in is on already")
    return _setup(conn, session.login_id, secret)


def confirm_two_step(conn: psycopg.Connection[Any], session: Session, code: str) -> None:
    """The first code from the app: from now on every sign-in of this login needs a code."""
    secret, confirmed, last_step, _, _ = _own_secret(conn, session)
    if confirmed is not None:
        raise Conflict("two-step sign-in is on already")
    if secret is None:
        raise Refused("turn two-step sign-in on first")
    step = _code_step(secret, code, last_step)
    if step is None:
        raise Refused(WRONG_CODE)
    conn.execute(
        "UPDATE trust_login_secrets SET totp_confirmed_at = now(), totp_last_step = %s,"
        " updated_at = now() WHERE login_id = %s",
        (step, session.login_id),
    )


def turn_off_two_step(conn: psycopg.Connection[Any], session: Session, code: str) -> None:
    """An owner or a tenant turns two-step sign-in off with a current code, unless their company
    requires it. A wrong code counts as a failed sign-in, and none is tried while the login is
    locked, so a session left open can't be used to guess codes."""
    secret, confirmed, last_step, failures, locked = _own_secret(conn, session)
    if confirmed is None:
        raise Conflict("two-step sign-in is off already")
    if two_step_required(conn, session.pmc_id):
        raise Refused("your property manager requires two-step sign-in, so it stays on")
    if locked:
        raise Refused(f"too many wrong tries: try again in {LOCK_MINUTES} minutes")
    if _code_step(secret, code, last_step) is None:
        _count_failure(conn, session.login_id, failures)
        raise Refused(WRONG_CODE)
    conn.execute(
        "UPDATE trust_login_secrets SET totp_secret = NULL, totp_confirmed_at = NULL,"
        " totp_last_step = NULL, updated_at = now() WHERE login_id = %s",
        (session.login_id,),
    )


# --- signing in and out -------------------------------------------------------------------


def sign_in(
    conn: psycopg.Connection[Any], pmc_id: UUID, email: str, password: str, code: str | None
) -> Signed | Enrollment:
    """A new session; or, for an owner or a tenant whose company requires two-step sign-in and
    who has no authenticator yet, the one to set up (no session until a code from it comes with
    the password). Refused with SignInRefused (the same for every reason) or CodeNeeded (the
    password was right; the code is missing or wrong). A failure is committed before it is
    raised, so it counts."""
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
    if kind == "staff" and confirmed is None:
        raise Refused("finish setting up from your invite link first")
    sets_up = confirmed is None and kind != "staff" and two_step_required(conn, pmc_id)
    if sets_up and (totp_secret is None or not code):
        # Required, and not set up yet: the password was right, so the authenticator is set up
        # now, as staff's is from their invite (a secret waiting already is the one shown).
        return _setup(conn, login_id, totp_secret)
    step = None
    if confirmed is not None or sets_up:
        # Staff, an owner or a tenant who turned two-step sign-in on, or one setting it up.
        if not code:
            raise CodeNeeded(ENTER_CODE)
        step = _code_step(totp_secret, code, last_step)
        if step is None:
            _count_failure(conn, login_id, failures)
            raise CodeNeeded(WRONG_CODE)
    conn.execute(
        "UPDATE trust_login_secrets SET failed_sign_ins = 0, locked_until = NULL,"
        " totp_last_step = coalesce(%s, totp_last_step),"
        " totp_confirmed_at = CASE WHEN %s THEN now() ELSE totp_confirmed_at END,"
        " password_hash = coalesce(%s, password_hash), updated_at = now() WHERE login_id = %s",
        (
            step,
            sets_up,
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
