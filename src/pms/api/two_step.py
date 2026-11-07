"""Two-step sign-in for owners and tenants (the founder's scope of 2026-10-22): theirs to turn on
from their Profile, with an authenticator app, as staff sign in with one from their invite.

Starting gives an authenticator's setup link; its first code turns it on, and every other session
of the login ends. From then on signing in asks for its code (accounts.sign_in), and turning it
off takes one. A reset they ask for keeps it; a new invite from staff turns it off
(accounts.accept_invite). Nothing here moves money; the AI role never reads a login's secrets.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from pms import accounts
from pms.api.deps import COOKIE, Conn, Portal

router = APIRouter(prefix="/api/me/two-step")

# Six digits, as an authenticator app shows them (the sign-in's own Code).
Code = Annotated[str, Field(pattern=r"^\s*\d{6}\s*$")]


class TwoStep(BaseModel):
    on: bool


class Started(BaseModel):
    # otpauth://totp/...: the portal shows it as a QR code and its key to type in.
    uri: str


class WithCode(BaseModel):
    code: Code


class Nothing(BaseModel):
    pass


@router.get("")
def two_step(session: Portal, conn: Conn) -> TwoStep:
    return TwoStep(on=accounts.two_step_on(conn, session))


@router.post("/start")
def start(body: Nothing, session: Portal, conn: Conn) -> Started:
    return Started(uri=accounts.start_two_step(conn, session))


@router.post("/confirm")
def confirm(body: WithCode, request: Request, session: Portal, conn: Conn) -> TwoStep:
    accounts.confirm_two_step(conn, session, body.code, request.cookies.get(COOKIE, ""))
    return TwoStep(on=True)


@router.post("/off")
def off(body: WithCode, session: Portal, conn: Conn) -> TwoStep:
    accounts.stop_two_step(conn, session, body.code)
    return TwoStep(on=False)
