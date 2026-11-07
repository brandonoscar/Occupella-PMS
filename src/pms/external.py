"""Signing in through a host app (docs/adr/0013-sign-in-through-a-host.md; AgenticHelixis
docs/plans/one-login-one-ui.md, phase 4).

An app that mounts the PMS, such as Occupella, signs people in itself and hands the PMS a
Verifier through its Settings (`external`). Its frontend sends the PMS the token of its own
sign-in (`POST /api/external-sign-in`); the verifier says who that is (a Person), and the PMS
makes its own session for them, as a password sign-in does. What the host vouches for:

- who the person is: its own id of them (`subject`), never reused for another person, and an
  email address it has checked is theirs;
- the companies they are staff of in the host, and in which they are an admin;
- that a staff member passed the host's second step (an authenticator) in this sign-in.

What the PMS still checks, as for any sign-in: a staff member's company is one the host says
they are staff of, today; the login isn't disabled; the company is active; an owner or a tenant
joins only through the invite staff sent them, at the invite's own email. The host's admins are
the books' admins (the founder, 11/07): each staff sign-in gives or takes the admin role as the
host says (trust_staff_roles.host_issuer), never leaving a company without one.

A first staff sign-in of a company whose books aren't open yet opens them when the host says the
person is its admin: the company (its id is the host's), with its starting chart and kinds of fee,
as `python -m pms.admin setup` gives one. Its staff join on their first sign-in, as a staff
record of their own, or the staff login that already has their email.

A person is linked to one login per company (trust_external_identities); a login linked this way
needs no password, and has none unless it accepts a password invite.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import psycopg

from pms import accounts
from pms.api import setup as books

log = logging.getLogger("pms.external")

# A host's name for itself, as trust_external_identities.issuer takes it.
ISSUER = re.compile(r"[a-z][a-z0-9_-]{0,31}")
MAX_SUBJECT = 255
SECOND_STEP = (
    "staff sign in with two-step sign-in: turn it on where you sign in, then sign in again"
)
NOT_OPEN = "this company's books aren't open yet: an admin of the company opens them by signing in"
SEVERAL = "you sign in to more than one company here: say which one"
COMPANY = "My company"


@dataclass(frozen=True)
class Membership:
    """A person is staff of a company in the host: whether they are its admin, and its name."""

    admin: bool
    company_name: str


@dataclass(frozen=True)
class Person:
    """Who the host signed in. `email` is one the host has checked is theirs."""

    subject: str
    email: str
    name: str
    staff_of: Mapping[UUID, Membership]
    second_step: bool


class Verifier(Protocol):
    """The host's check of its own sign-in's token: the person, or None when it isn't one."""

    @property
    def issuer(self) -> str: ...

    def verify(self, token: str) -> Person | None: ...


class SecondStepNeeded(accounts.Refused):
    """A staff member signed in to the host without its second step."""


class BooksNotOpen(accounts.Refused):
    """A staff member of a company whose books no admin has opened yet."""


def refuse_issuer(issuer: str) -> None:
    """Raise unless `issuer` is a name trust_external_identities takes."""
    if not ISSUER.fullmatch(issuer):
        raise ValueError(
            f"a host app's issuer is 1 to 32 lower-case letters, digits, - or _, starting with a"
            f" letter: {issuer!r} isn't"
        )


def sign_in(
    conn: psycopg.Connection[Any],
    issuer: str,
    person: Person,
    pmc_id: UUID | None,
    invite: str | None,
) -> accounts.Signed:
    """A session for the person the host signed in: through an owner's or a tenant's invite, the
    login they are linked to, or, for the host's staff, the staff login they join as."""
    email = accounts.normalize_email(person.email)
    if not 1 <= len(person.subject) <= MAX_SUBJECT or not accounts.EMAIL.fullmatch(email):
        # The host's own mistake: never a session, and the same answer as any refusal.
        log.warning("the host's verifier named a person with no usable id or email")
        raise accounts.SignInRefused()
    # One sign-in of a person at a time, so two of their first sign-ins make one login.
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"external sign-in {issuer} {person.subject}",),
    )
    if invite is not None:
        return _through_invite(conn, issuer, person, email, invite)
    pmc_id = _company(conn, issuer, person, pmc_id)
    linked = conn.execute(
        "SELECT l.id, l.kind, l.staff_id, l.disabled_at IS NOT NULL"
        " FROM trust_external_identities AS e"
        " JOIN trust_logins AS l ON l.pmc_id = e.pmc_id AND l.id = e.login_id"
        " WHERE e.pmc_id = %s AND e.issuer = %s AND e.subject = %s",
        (pmc_id, issuer, person.subject),
    ).fetchone()
    if linked is not None:
        login_id, kind, staff_id, disabled = linked
        if disabled:
            raise accounts.SignInRefused()
        _active(conn, pmc_id)
        if kind == "staff":
            membership = _staff_of(person, pmc_id)
            _sync_admin(conn, issuer, pmc_id, staff_id, membership.admin)
        return accounts.new_session(conn, login_id)
    membership = _staff_of(person, pmc_id)
    if _books_open(conn, pmc_id, membership):
        _active(conn, pmc_id)
    login_id, staff_id = _staff_login(conn, issuer, person, email, pmc_id)
    conn.execute(
        "INSERT INTO trust_external_identities (pmc_id, login_id, issuer, subject)"
        " VALUES (%s, %s, %s, %s)",
        (pmc_id, login_id, issuer, person.subject),
    )
    _sync_admin(conn, issuer, pmc_id, staff_id, membership.admin)
    return accounts.new_session(conn, login_id)


def _company(
    conn: psycopg.Connection[Any], issuer: str, person: Person, pmc_id: UUID | None
) -> UUID:
    """The company signed in to: the one named (a company they are neither linked in nor staff of
    is refused after, as any refusal is), else the only one they are linked in or staff of."""
    if pmc_id is not None:
        return pmc_id
    linked = {
        row[0]
        for row in conn.execute(
            "SELECT pmc_id FROM trust_external_identities WHERE issuer = %s AND subject = %s",
            (issuer, person.subject),
        ).fetchall()
    }
    found = linked | set(person.staff_of)
    if not found:
        raise accounts.SignInRefused()
    if len(found) > 1:
        raise accounts.Conflict(SEVERAL)
    return found.pop()


def _staff_of(person: Person, pmc_id: UUID) -> Membership:
    """The person's membership of the company as the host says it today: a staff sign-in takes
    one, and the host's second step."""
    membership = person.staff_of.get(pmc_id)
    if membership is None:
        raise accounts.SignInRefused()
    if not person.second_step:
        raise SecondStepNeeded(SECOND_STEP)
    return membership


def _active(conn: psycopg.Connection[Any], pmc_id: UUID) -> None:
    status = accounts.company_status(conn, pmc_id)
    if status != "active":
        raise accounts.CompanyNotActive(accounts.NOT_ACTIVE[status])


def _books_open(conn: psycopg.Connection[Any], pmc_id: UUID, membership: Membership) -> bool:
    """Whether the company's books were open already. Its admin's first sign-in opens them."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"open books {pmc_id}",))
    if conn.execute("SELECT 1 FROM trust_pmcs WHERE pmc_id = %s", (pmc_id,)).fetchone():
        return True
    if not membership.admin:
        raise BooksNotOpen(NOT_OPEN)
    opened = _value(
        conn,
        "INSERT INTO trust_pmcs (pmc_id, display_name) VALUES (%s, %s) RETURNING pmc_id",
        (pmc_id, membership.company_name.strip() or COMPANY),
    )
    books.add_starting_chart(conn, pmc_id)
    books.add_starting_charge_types(conn, pmc_id)
    # Fixed words and the id the database stored: nothing the request or the host's person said
    # (a name or an id with a line break would forge a log line).
    log.info("opened the books of company %s at its admin's first sign-in", opened)
    return False


def _staff_login(
    conn: psycopg.Connection[Any], issuer: str, person: Person, email: str, pmc_id: UUID
) -> tuple[UUID, UUID]:
    """The staff login the person joins as: the one with their email, not linked to anyone of the
    host yet, else a new staff member's."""
    found = conn.execute(
        "SELECT l.id, l.kind, l.staff_id, l.disabled_at IS NOT NULL,"
        " EXISTS (SELECT 1 FROM trust_external_identities AS e"
        "         WHERE e.login_id = l.id AND e.issuer = %s)"
        " FROM trust_logins AS l WHERE l.pmc_id = %s AND l.email = %s",
        (issuer, pmc_id, email),
    ).fetchone()
    if found is not None:
        login_id, kind, staff_id, disabled, linked = found
        if kind != "staff":
            raise accounts.Conflict(f"{email} signs in to this company as an owner or a tenant")
        if disabled or linked:
            raise accounts.SignInRefused()
        return login_id, staff_id
    staff_id = _value(
        conn,
        "INSERT INTO trust_staff (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
        (pmc_id, person.name.strip() or email),
    )
    login_id = _value(conn, accounts.KINDS["staff"]["new login"], (pmc_id, staff_id, email))
    return login_id, staff_id


def _sync_admin(
    conn: psycopg.Connection[Any], issuer: str, pmc_id: UUID, staff_id: UUID, admin: bool
) -> None:
    """Give or take the admin role as the host says. Taking it from the company's last admin is
    left undone, and logged: the company keeps an admin (migration 20261105000600)."""
    holds = _value(conn, "SELECT trust_staff_holds(%s, %s, 'admin')", (pmc_id, staff_id))
    if holds == admin:
        return
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_staff_roles (pmc_id, staff_id, role, granted, host_issuer)"
                " VALUES (%s, %s, 'admin', %s, %s)",
                (pmc_id, staff_id, admin, issuer),
            )
    except psycopg.errors.CheckViolation:
        # Fixed words and the staff member's id, which the database made: nothing the request or
        # the host's person said.
        log.warning(
            "the host says staff member %s is no admin, but they are their company's last: the"
            " books keep them an admin until another is",
            staff_id,
        )


def _value(conn: psycopg.Connection[Any], query: str, params: tuple[Any, ...]) -> Any:
    """The first column of the query's one row."""
    row = conn.execute(query, params).fetchone()
    assert row is not None
    return row[0]


def _through_invite(
    conn: psycopg.Connection[Any], issuer: str, person: Person, email: str, token: str
) -> accounts.Signed:
    """An owner or a tenant joins through the invite staff sent them, at its own email: their
    login is linked to the person, and the invite is used up."""
    invite_id, pmc_id, login_id, kind, invited, _, _ = accounts.open_invite(conn, token)
    if kind == "staff":
        raise accounts.Refused("a staff invite sets a password: open it on this server")
    if invited != email:
        raise accounts.SignInRefused()
    links = conn.execute(
        "SELECT login_id, subject FROM trust_external_identities"
        " WHERE pmc_id = %s AND issuer = %s AND (subject = %s OR login_id = %s)",
        (pmc_id, issuer, person.subject, login_id),
    ).fetchall()
    if any(row[0] != login_id or row[1] != person.subject for row in links):
        raise accounts.Conflict(
            "your account signs in to this company as someone else already: ask your property"
            " manager"
        )
    if not links:
        conn.execute(
            "INSERT INTO trust_external_identities (pmc_id, login_id, issuer, subject)"
            " VALUES (%s, %s, %s, %s)",
            (pmc_id, login_id, issuer, person.subject),
        )
    return accounts.finish_invite(conn, invite_id, login_id)
