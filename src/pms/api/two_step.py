"""Two-step sign-in for owners and tenants (migration 20261022000153; the founder, 2026-10-22).

An owner or a tenant turns it on for their own login from the portal: start answers the
authenticator to set up (its setup link and the key to type in), and the app's first code
confirms it; from then on every sign-in of theirs asks for a code after the password. They turn
it off with a current code, unless their company requires it. Staff keep their own authenticator,
set up from their invite: these routes refuse a staff session.

A company may require it of every owner and tenant (`trust_sign_in_settings`, the latest row
holding, naming who changed it): staff who may change who can sign in (the area "people") change
it from Settings, Logins; every staff member reads it. While it is required, an owner or a tenant
without an authenticator sets one up when they sign in (src/pms/accounts.py). Turning it on ends
nobody's session: it applies at each person's next sign-in.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from pms import accounts
from pms.api.deps import Conn, Current, Staff

router = APIRouter(prefix="/api")

# An authenticator's code: six digits, spaces around them forgiven.
Code = Annotated[str, Field(pattern=r"^\s*\d{6}\s*$")]


class TwoStepSetup(BaseModel):
    # The authenticator to set up: the link an app reads as a QR code, and the key to type in.
    totp_uri: str
    secret: str


class CodeIn(BaseModel):
    code: Code


class TwoStep(BaseModel):
    # Whether the person's sign-in asks for a code, and whether their company requires it.
    on: bool
    required: bool


class CompanyTwoStep(BaseModel):
    # Whether the company requires two-step sign-in of its owners and tenants.
    required: bool


class CompanyTwoStepSet(CompanyTwoStep):
    # When it was last changed, and by whom (None: never, or by the command line).
    changed_at: datetime | None
    changed_by: str | None


def person(session: Current) -> accounts.Session:
    if session.kind not in ("owner", "tenant"):
        raise HTTPException(
            403,
            "only an owner or a tenant turns two-step sign-in on or off here: staff always"
            " sign in with the authenticator from their invite",
        )
    return session


Person = Annotated[accounts.Session, Depends(person)]


def setup_of(enrollment: accounts.Enrollment) -> TwoStepSetup:
    return TwoStepSetup(totp_uri=enrollment.totp_uri, secret=enrollment.secret)


def state(conn: psycopg.Connection[Any], session: accounts.Session) -> TwoStep:
    return TwoStep(
        on=accounts.two_step_on(conn, session.login_id),
        required=accounts.two_step_required(conn, session.pmc_id),
    )


@router.post("/me/two-step/start")
def start(session: Person, conn: Conn) -> TwoStepSetup:
    """The authenticator to set up, shown until its first code confirms it (the same one each
    time). Nothing changes at sign-in until then."""
    return setup_of(accounts.start_two_step(conn, session))


@router.post("/me/two-step/confirm")
def confirm(body: CodeIn, session: Person, conn: Conn) -> TwoStep:
    """The app's first code: from now on each sign-in asks for a code after the password."""
    accounts.confirm_two_step(conn, session, body.code)
    return state(conn, session)


@router.post("/me/two-step/off")
def turn_off(body: CodeIn, session: Person, conn: Conn) -> TwoStep:
    """Off, with a current code, unless the company requires it. A wrong code counts as a failed
    sign-in."""
    accounts.turn_off_two_step(conn, session, body.code)
    return state(conn, session)


SETTING = (
    "SELECT s.require_two_step, s.created_at, st.display_name FROM trust_sign_in_settings AS s"
    " LEFT JOIN trust_staff AS st ON st.id = s.recorded_by"
    " WHERE s.pmc_id = %s ORDER BY s.created_at DESC, s.id DESC LIMIT 1"
)


def company_setting(conn: psycopg.Connection[Any], pmc_id: UUID) -> CompanyTwoStepSet:
    row = conn.execute(SETTING, (pmc_id,)).fetchone()
    if row is None:
        return CompanyTwoStepSet(required=False, changed_at=None, changed_by=None)
    return CompanyTwoStepSet(required=row[0], changed_at=row[1], changed_by=row[2])


@router.get("/logins/two-step")
def company(session: Staff, conn: Conn) -> CompanyTwoStepSet:
    """Whether the company requires two-step sign-in of its owners and tenants."""
    return company_setting(conn, session.pmc_id)


@router.post("/logins/two-step")
def set_company(body: CompanyTwoStep, session: Staff, conn: Conn) -> CompanyTwoStepSet:
    """Requires two-step sign-in of every owner and tenant, or stops requiring it. Saving what
    holds already writes nothing. No session ends: it applies at each one's next sign-in."""
    if company_setting(conn, session.pmc_id).required != body.required:
        conn.execute(
            "INSERT INTO trust_sign_in_settings (pmc_id, require_two_step) VALUES (%s, %s)",
            (session.pmc_id, body.required),
        )
    return company_setting(conn, session.pmc_id)
