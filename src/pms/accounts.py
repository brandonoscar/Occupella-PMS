"""Logins, invites and sessions (migration 20261011000028; docs/adr/0002-portals-and-exports.md).

Staff invite a person (one of the PMC's staff, owners or tenants) by email. The invite is a
link carrying a random token; only its SHA-256 is stored, it expires after INVITE_DAYS, and it
is accepted once. Accepting sets the password, so a new invite is also how staff reset a
forgotten one; an owner or a tenant may ask for one by email themselves (request_reset,
docs/adr/0007-email.md). Staff also enroll an authenticator app (TOTP) before their invite is
used up, and every staff sign-in needs its code; a code works once.

An owner or a tenant may turn two-step sign-in on from their Profile (the founder's scope of
2026-10-22): an authenticator app of their own, proved by its first code; from then on signing in
asks for its code, and turning it off takes one. A reset they ask for keeps it and asks for its
code before it sets the password, so an emailed link alone never gets past it; any other invite
(staff's, or the operator's) turns it off, which is how a manager vouches for someone who lost
their phone.

Passwords are hashed with argon2id. A sign-in that fails for any reason (no such login, wrong
password, disabled, locked) gets the same answer, after the same work, so it doesn't tell an
attacker which emails have logins. Ten failures in a row lock the login for LOCK_MINUTES.

A session is a random token in an HttpOnly cookie; only its SHA-256 is stored. It lasts
SESSION_HOURS, or until it is left alone for IDLE_MINUTES (trust_session_activity, moved forward at
most every TOUCH_MINUTES while it is used), and signing out, a new password or disabling the login
ends it.

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

from pms import phones

INVITE_DAYS = 7
SESSION_HOURS = 12
IDLE_MINUTES = 60
TOUCH_MINUTES = 5
MAX_FAILURES = 10
LOCK_MINUTES = 15
MIN_PASSWORD = 12
MAX_PASSWORD = 1024
# Password resets an owner or a tenant may ask for in an hour (docs/adr/0007-email.md).
RESETS_PER_HOUR = 3
WRONG_CODE = "that code is wrong or was already used: try the next one"
ASK_CODE = "enter the code from your authenticator app"
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
    """Staff: the password was right, and the authenticator code is missing or wrong."""


class WrongCode(Refused):
    """An owner's or a tenant's code, at their Profile, that is wrong or was used already."""


class CompanyNotActive(Refused):
    """The company is suspended or closed (`python -m pms.admin status`, migration
    20261105000100): nobody signs in to it, whoever they are."""


# What a person signing in to a company that isn't active is told.
NOT_ACTIVE = {
    "suspended": "this company's account is suspended: ask whoever runs this server",
    "closed": "this company's account is closed",
}


def company_status(conn: psycopg.Connection[Any], pmc_id: UUID) -> str:
    """The company's status: active, suspended or closed (trust_pmc_status)."""
    return str(_one(conn, "SELECT trust_pmc_status(%s)", (pmc_id,))[0])


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
    # A tenant or an owner gives their phone at sign-up and says whether the company may text
    # them, beside these words (src/pms/phones.py); staff are asked neither.
    text_consent: str | None = None
    # A reset an owner or a tenant asked for while their two-step sign-in is on: setting the
    # password takes the code from their authenticator app too.
    two_step: bool = False


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
    asked_for: bool = False,
) -> Invite:
    """An invite for a person of the PMC, making their login if they have none yet. One the
    person asked for themselves (a reset: request_reset) keeps their two-step sign-in."""
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
        "INSERT INTO trust_invites"
        " (pmc_id, login_id, token_hash, created_by, asked_for, expires_at)"
        " VALUES (%s, %s, %s, %s, %s, now() + make_interval(days => %s)) RETURNING expires_at",
        (pmc_id, login_id, digest(token), created_by, asked_for, INVITE_DAYS),
    )[0]
    return Invite(login_id, token, expires_at)


def request_reset(conn: psycopg.Connection[Any], pmc_id: UUID, email: str) -> Reset | None:
    """A new invite, to set a new password, for an owner's or a tenant's login with this email
    that can sign in and asked fewer than RESETS_PER_HOUR times in the last hour; None
    otherwise. The caller answers the same either way, so it doesn't tell who has a login. A
    staff member resets through an admin's new invite instead: an emailed link must not get past
    their authenticator. Accepting it sets the new password, so until then the old one works.
    A company that isn't active sends none."""
    if company_status(conn, pmc_id) != "active":
        return None
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
    invite = create_invite(conn, pmc_id, kind, person_id, email, None, asked_for=True)
    return Reset(kind, person_id, normalize_email(email), invite)


def _open_invite(conn: psycopg.Connection[Any], token: str, lock: bool) -> Any:
    """The invite's row if it can still be used: not accepted, not expired, login enabled, and
    the login's newest (a newer invite or password reset retires it: docs/edge-cases.md U-013)."""
    row = _one(
        conn,
        "SELECT i.id, i.pmc_id, l.id, l.kind, l.email, i.expires_at, i.asked_for"  # noqa: S608
        " FROM trust_invites AS i JOIN trust_logins AS l ON l.id = i.login_id"
        " WHERE i.token_hash = %s AND i.accepted_at IS NULL AND i.expires_at > now()"
        " AND l.disabled_at IS NULL"
        " AND NOT EXISTS (SELECT 1 FROM trust_invites AS n WHERE n.login_id = i.login_id"
        "   AND n.created_at > i.created_at)" + (" FOR UPDATE OF i" if lock else ""),
        (digest(token),),
    )
    if row is None:
        raise NotFound("this invite link can't be used: ask for a new one")
    # A company that isn't active takes no one in, as it signs no one in: an invite made before it
    # was suspended set the password, then failed making the session (the AI pen test of 11/06).
    status = company_status(conn, row[1])
    if status != "active":
        raise CompanyNotActive(NOT_ACTIVE[status])
    return row


def open_invite(conn: psycopg.Connection[Any], token: str) -> Any:
    """The invite's row, locked, if it can still be used (_open_invite): its id, PMC, login,
    kind, email, expiry and whether the person asked for it. For a host app's sign-in
    (pms.external), which uses an owner's or a tenant's invite up without a password."""
    return _open_invite(conn, token, lock=True)


def finish_invite(conn: psycopg.Connection[Any], invite_id: UUID, login_id: UUID) -> Signed:
    """The invite used up, the login's other sessions ended, and a new one (pms.external)."""
    return _finish_invite(conn, invite_id, login_id)


def new_session(conn: psycopg.Connection[Any], login_id: UUID) -> Signed:
    """A new session for the login (pms.external: the host signed the person in)."""
    return _new_session(conn, login_id)


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
    _, _, login_id, kind, email, expires_at, asked_for = _open_invite(conn, token, lock=False)
    name, pmc_name = names(conn, login_id)
    wording = None if kind == "staff" else phones.text_consent(pmc_name)
    keeps = _keeps_two_step(conn, login_id, kind, asked_for, lock=False) is not None
    return InviteView(kind, email, name, pmc_name, expires_at, wording, keeps)


def _keeps_two_step(
    conn: psycopg.Connection[Any], login_id: UUID, kind: str, asked_for: bool, lock: bool
) -> Any:
    """For a reset an owner or a tenant asked for themselves while their two-step sign-in is on:
    its secret, last step used, failures and lock. None otherwise."""
    if kind == "staff" or not asked_for:
        return None
    return _one(
        conn,
        "SELECT totp_secret, totp_last_step, failed_sign_ins, locked_until > now()"  # noqa: S608
        " FROM trust_login_secrets WHERE login_id = %s AND totp_confirmed_at IS NOT NULL"
        + (" FOR UPDATE" if lock else ""),
        (login_id,),
    )


def accept_invite(
    conn: psycopg.Connection[Any], token: str, password: str, code: str | None = None
) -> Signed | Enrollment:
    """Set the password. An owner or a tenant is signed in; staff enroll an authenticator
    next (confirm_invite), and their invite stays open until they do. A reset an owner or a
    tenant asked for keeps their two-step sign-in, and takes its code before anything changes;
    any other invite turns it off."""
    check_password(password)
    invite_id, pmc_id, login_id, kind, _, _, asked_for = _open_invite(conn, token, lock=True)
    kept = _keeps_two_step(conn, login_id, kind, asked_for, lock=True)
    if kept is not None:
        secret, last_step, failures, locked = kept
        if locked:
            raise CodeNeeded(f"too many wrong codes: try again in {LOCK_MINUTES} minutes")
        if not code:
            raise CodeNeeded(ASK_CODE)
        step = _code_step(secret, code, last_step)
        if step is None:
            _count_failure(conn, login_id, failures)
            raise CodeNeeded(WRONG_CODE)
        conn.execute(
            "UPDATE trust_login_secrets SET password_hash = %s, totp_last_step = %s,"
            " failed_sign_ins = 0, locked_until = NULL, updated_at = now() WHERE login_id = %s",
            (HASHER.hash(password), step, login_id),
        )
        return _finish_invite(conn, invite_id, login_id)
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
    invite_id, _, login_id, kind, _, _, _ = _open_invite(conn, token, lock=True)
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
        raise CodeNeeded(WRONG_CODE)
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
    """A new session, or SignInRefused (the same for every reason) or CodeNeeded (staff, or an
    owner or a tenant with two-step sign-in on, whose password was right). A failure is
    committed before it is raised, so it counts. A company that isn't active refuses everyone
    the same way, before any login is looked at."""
    status = company_status(conn, pmc_id)
    if status != "active":
        raise CompanyNotActive(NOT_ACTIVE[status])
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
    if kind == "staff" and confirmed is None:
        raise Refused("finish setting up from your invite link first")
    # Staff always; an owner or a tenant once they turned two-step sign-in on.
    if confirmed is not None:
        if not code:
            raise CodeNeeded(ASK_CODE)
        step = _code_step(totp_secret, code, last_step)
        if step is None:
            _count_failure(conn, login_id, failures)
            raise CodeNeeded(WRONG_CODE)
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


# --- two-step sign-in for owners and tenants ----------------------------------------------


def _portal_secrets(conn: psycopg.Connection[Any], session: Session, lock: bool = True) -> Any:
    """An owner's or a tenant's secret, when it was confirmed, its last step, failures and lock;
    locked for a change."""
    if session.kind not in ("owner", "tenant"):
        raise Refused("staff sign in with the authenticator from their invite")
    return _one(
        conn,
        "SELECT totp_secret, totp_confirmed_at, totp_last_step, failed_sign_ins,"  # noqa: S608
        " locked_until > now() FROM trust_login_secrets WHERE login_id = %s"
        + (" FOR UPDATE" if lock else ""),
        (session.login_id,),
    )


def two_step_on(conn: psycopg.Connection[Any], session: Session) -> bool:
    row = _portal_secrets(conn, session, lock=False)
    return row is not None and row[1] is not None


def start_two_step(conn: psycopg.Connection[Any], session: Session) -> str:
    """A new authenticator's setup link (otpauth://), not on until its first code proves it
    (confirm_two_step). Starting again replaces one not yet proved."""
    row = _portal_secrets(conn, session)
    if row is None:
        raise Refused("set a password from your invite first")
    if row[1] is not None:
        raise Conflict(
            "two-step sign-in is on already: turn it off first to set up another authenticator"
        )
    secret = pyotp.random_base32()
    conn.execute(
        "UPDATE trust_login_secrets SET totp_secret = %s, totp_last_step = NULL,"
        " updated_at = now() WHERE login_id = %s",
        (secret, session.login_id),
    )
    return pyotp.TOTP(secret).provisioning_uri(name=session.email, issuer_name=ISSUER)


def confirm_two_step(
    conn: psycopg.Connection[Any], session: Session, code: str, token: str
) -> None:
    """The new authenticator's first code turns two-step sign-in on. Every other session of
    the login ends: each was opened without a code."""
    row = _portal_secrets(conn, session)
    if row is not None and row[1] is not None:
        raise Conflict("two-step sign-in is on already")
    if row is None or row[0] is None:
        raise Conflict("set up two-step sign-in first: start it from your profile")
    step = _code_step(row[0], code, row[2])
    if step is None:
        raise WrongCode(WRONG_CODE)
    conn.execute(
        "UPDATE trust_login_secrets SET totp_confirmed_at = now(), totp_last_step = %s,"
        " updated_at = now() WHERE login_id = %s",
        (step, session.login_id),
    )
    conn.execute(
        "UPDATE trust_sessions SET ended_at = now()"
        " WHERE login_id = %s AND ended_at IS NULL AND token_hash <> %s",
        (session.login_id, digest(token)),
    )


def stop_two_step(conn: psycopg.Connection[Any], session: Session, code: str) -> None:
    """Turning two-step sign-in off takes a code from the app, so a session left open alone
    can't. A wrong one counts as a failed sign-in (MAX_FAILURES lock the login)."""
    row = _portal_secrets(conn, session)
    if row is None or row[1] is None:
        raise Conflict("two-step sign-in is off already")
    secret, _, last_step, failures, locked = row
    if locked:
        raise WrongCode(f"too many wrong codes: try again in {LOCK_MINUTES} minutes")
    if _code_step(secret, code, last_step) is None:
        _count_failure(conn, session.login_id, failures)
        raise WrongCode(WRONG_CODE)
    conn.execute(
        "UPDATE trust_login_secrets SET totp_secret = NULL, totp_confirmed_at = NULL,"
        " totp_last_step = NULL, updated_at = now() WHERE login_id = %s",
        (session.login_id,),
    )


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
    """The live session behind a cookie's token, if there is one: not ended, not past its hours,
    used within IDLE_MINUTES, its login enabled and its company active. Using it moves its last
    use forward, at most every TOUCH_MINUTES."""
    row = _one(
        conn,
        "SELECT l.id, l.pmc_id, l.kind, coalesce(l.staff_id, l.owner_id, l.tenant_id), l.email,"
        " s.id"
        " FROM trust_sessions AS s JOIN trust_logins AS l ON l.id = s.login_id"
        " LEFT JOIN trust_session_activity AS a ON a.session_id = s.id"
        " WHERE s.token_hash = %s AND s.ended_at IS NULL AND s.expires_at > now()"
        " AND coalesce(a.last_seen_at, s.created_at) > now() - make_interval(mins => %s)"
        " AND l.disabled_at IS NULL AND trust_pmc_status(l.pmc_id) = 'active'",
        (digest(token), IDLE_MINUTES),
    )
    if row is None:
        return None
    conn.execute(
        "INSERT INTO trust_session_activity (session_id, pmc_id) VALUES (%s, %s)"
        " ON CONFLICT (session_id) DO UPDATE SET last_seen_at = now()"
        " WHERE trust_session_activity.last_seen_at < now() - make_interval(mins => %s)",
        (row[5], row[1], TOUCH_MINUTES),
    )
    return Session(*row[:5])


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
