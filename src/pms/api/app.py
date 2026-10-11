"""The API: health, signing in and out, invites, and staff downloads of reports and the ledger.

Every request runs in one transaction on a pooled connection acting as `trust_app` (or the
role in PMS_DB_ROLE). A staff member's request names them to the audit log
(`trust.staff_id`), so every write they cause records who did it.

What a browser may do:
  - the session is a cookie: HttpOnly, SameSite=Strict, and Secure unless PMS_COOKIE_SECURE
    is "false" (plain http on a laptop);
  - a request that changes something must send JSON, and if it carries an Origin header that
    origin must be PMS_PUBLIC_URL's. With SameSite=Strict that keeps other sites from acting
    for a signed-in person (CSRF);
  - API answers are never cached.

Staff see their whole PMC. Owners and tenants get their own endpoints in the portal PRs; here
they can sign in and read who they are, and nothing else.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from psycopg import sql
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from pms import accounts, exports

COOKIE = "pms_session"
HEALTH_TIMEOUT = 2.0  # seconds to wait for a database connection before answering 503
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MEDIA = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


@dataclass(frozen=True)
class Settings:
    database_url: str
    public_url: str = "http://localhost:8000"
    cookie_secure: bool = True
    db_role: str = "trust_app"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.environ["DATABASE_URL"],
            public_url=os.environ.get("PMS_PUBLIC_URL", cls.public_url),
            cookie_secure=os.environ.get("PMS_COOKIE_SECURE", "true").lower() != "false",
            db_role=os.environ.get("PMS_DB_ROLE", cls.db_role),
        )

    @property
    def origin(self) -> str:
        parts = urlsplit(self.public_url)
        return f"{parts.scheme}://{parts.netloc}"


# --- request and answer shapes ------------------------------------------------------------

Email = Annotated[str, Field(min_length=3, max_length=320)]
Password = Annotated[str, Field(min_length=1, max_length=accounts.MAX_PASSWORD)]
Token = Annotated[str, Field(min_length=20, max_length=100)]
Code = Annotated[str, Field(pattern=r"^\s*\d{6}\s*$")]


class SignIn(BaseModel):
    pmc_id: UUID
    email: Email
    password: Password
    code: Code | None = None


class NewInvite(BaseModel):
    kind: Literal["staff", "owner", "tenant"]
    person_id: UUID
    email: Email


class InviteToken(BaseModel):
    token: Token


class AcceptInvite(InviteToken):
    password: Password


class ConfirmInvite(InviteToken):
    code: Code


class Me(BaseModel):
    kind: str
    email: str
    name: str
    person_id: UUID
    pmc_id: UUID
    pmc_name: str


class InviteLink(BaseModel):
    login_id: UUID
    url: str
    expires_at: datetime


class InviteDetails(BaseModel):
    kind: str
    email: str
    name: str
    pmc_name: str
    expires_at: datetime


class Enroll(BaseModel):
    totp_uri: str


# --- who is asking ------------------------------------------------------------------------


def connection(request: Request) -> Iterator[psycopg.Connection[Any]]:
    # Committed when the request succeeds, rolled back when it raises.
    pool: ConnectionPool[Any] = request.app.state.pool
    with pool.connection() as conn:
        yield conn


# scope="function": the commit happens before the answer is sent, so a client never acts on a
# write that isn't committed yet.
Conn = Annotated[psycopg.Connection[Any], Depends(connection, scope="function")]


def current(request: Request, conn: Conn) -> accounts.Session:
    token = request.cookies.get(COOKIE)
    session = accounts.session_for(conn, token) if token else None
    if session is None:
        raise HTTPException(401, "sign in first")
    if session.kind == "staff":
        # The audit log records this staff member for every write the request causes.
        conn.execute("SELECT set_config('trust.staff_id', %s, true)", (str(session.person_id),))
    return session


Current = Annotated[accounts.Session, Depends(current)]


def staff(session: Current) -> accounts.Session:
    if session.kind != "staff":
        raise HTTPException(403, "only the PMC's staff may do this")
    return session


Staff = Annotated[accounts.Session, Depends(staff)]


# --- the app ------------------------------------------------------------------------------


def create_app(settings: Settings) -> FastAPI:
    def configure(conn: psycopg.Connection[Any]) -> None:
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(settings.db_role)))
        conn.commit()

    pool = ConnectionPool(
        settings.database_url, min_size=1, max_size=10, configure=configure, open=False
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool.open(wait=False)  # the app starts even if the database isn't up yet
        try:
            yield
        finally:
            pool.close()

    app = FastAPI(title="Occupella PMS", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.pool = pool

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and origin != settings.origin:
                return JSONResponse({"detail": "this request came from another site"}, 403)
            if request.headers.get("content-type", "").split(";")[0].strip() != (
                "application/json"
            ):
                return JSONResponse({"detail": "send JSON"}, 415)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(accounts.Refused)
    async def refused(request: Request, error: accounts.Refused) -> JSONResponse:
        status = {
            accounts.NotFound: 404,
            accounts.Conflict: 409,
            accounts.SignInRefused: 401,
            accounts.CodeNeeded: 401,
        }.get(type(error), 422)
        return JSONResponse({"detail": str(error)}, status)

    def signed_in(response: Response, signed: accounts.Signed, conn: Conn) -> Me:
        response.set_cookie(
            COOKIE,
            signed.token,
            max_age=accounts.SESSION_HOURS * 3600,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            path="/",
        )
        return me_for(conn, signed.session)

    def me_for(conn: psycopg.Connection[Any], session: accounts.Session) -> Me:
        name, pmc_name = accounts.names(conn, session.login_id)
        return Me(
            kind=session.kind,
            email=session.email,
            name=name,
            person_id=session.person_id,
            pmc_id=session.pmc_id,
            pmc_name=pmc_name,
        )

    @app.get("/health")
    def health() -> JSONResponse:
        try:
            with pool.connection(timeout=HEALTH_TIMEOUT) as conn:
                conn.execute("SELECT 1")
        except Exception:  # any failure to reach the database is "unavailable"
            return JSONResponse({"status": "database unavailable"}, 503)
        return JSONResponse({"status": "ok"})

    @app.post("/api/sign-in")
    def sign_in(body: SignIn, response: Response, conn: Conn) -> Me:
        signed = accounts.sign_in(conn, body.pmc_id, body.email, body.password, body.code)
        return signed_in(response, signed, conn)

    @app.post("/api/sign-out", status_code=204)
    def sign_out(request: Request, response: Response, conn: Conn) -> None:
        token = request.cookies.get(COOKIE)
        if token:
            accounts.sign_out(conn, token)
        response.delete_cookie(COOKIE, path="/")

    @app.get("/api/me")
    def me(session: Current, conn: Conn) -> Me:
        return me_for(conn, session)

    @app.post("/api/invites", status_code=201)
    def invite(body: NewInvite, session: Staff, conn: Conn) -> InviteLink:
        made = accounts.create_invite(
            conn, session.pmc_id, body.kind, body.person_id, body.email, session.person_id
        )
        # The token rides in the fragment, so it never reaches a server log or a referrer.
        url = f"{settings.public_url.rstrip('/')}/invite#{made.token}"
        return InviteLink(login_id=made.login_id, url=url, expires_at=made.expires_at)

    @app.post("/api/invites/view")
    def view_invite(body: InviteToken, conn: Conn) -> InviteDetails:
        found = accounts.view_invite(conn, body.token)
        return InviteDetails(**found.__dict__)

    @app.post("/api/invites/accept", response_model=None)
    def accept_invite(body: AcceptInvite, response: Response, conn: Conn) -> Me | Enroll:
        done = accounts.accept_invite(conn, body.token, body.password)
        if isinstance(done, accounts.Enrollment):
            return Enroll(totp_uri=done.totp_uri)
        return signed_in(response, done, conn)

    @app.post("/api/invites/confirm")
    def confirm_invite(body: ConfirmInvite, response: Response, conn: Conn) -> Me:
        return signed_in(response, accounts.confirm_invite(conn, body.token, body.code), conn)

    @app.post("/api/logins/{login_id}/disable", status_code=204)
    def disable(login_id: UUID, session: Staff, conn: Conn) -> None:
        if login_id == session.login_id:
            raise HTTPException(422, "you can't disable your own login")
        accounts.disable_login(conn, session.pmc_id, login_id)

    @app.get("/api/reports/{name}")
    def report(
        name: str,
        session: Staff,
        conn: Conn,
        fmt: Annotated[Literal["csv", "xlsx", "pdf"], Query(alias="format")] = "csv",
        args: Annotated[list[str] | None, Query(alias="arg")] = None,
    ) -> Response:
        # Every report takes the PMC first (tests/api pins it): it always comes from the
        # session, never from the request.
        try:
            table = exports.run_report(conn, name, [session.pmc_id, *(args or [])])
        except exports.UnknownReport as unknown:
            raise HTTPException(404, str(unknown)) from None
        except exports.ExportError as refused:
            raise HTTPException(422, str(refused)) from None
        except (psycopg.errors.DataError, psycopg.errors.InvalidParameterValue) as bad:
            raise HTTPException(422, str(bad).splitlines()[0]) from None
        return download(exports.export(table, fmt), name.removeprefix("trust_report_"), fmt)

    @app.get("/api/ledger.zip")
    def ledger(session: Staff, conn: Conn) -> Response:
        return download(exports.ledger_zip(conn, session.pmc_id), "ledger", "zip")

    return app


def download(data: bytes, name: str, ext: str) -> Response:
    disposition = f'attachment; filename="{name}.{ext}"'
    return Response(
        data,
        media_type=MEDIA.get(ext, "application/zip"),
        headers={"Content-Disposition": disposition},
    )
