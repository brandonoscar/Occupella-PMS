"""The API: health, signing in and out, invites, and staff downloads of reports and the ledger.

Every request runs in one transaction on a pooled connection acting as `trust_app` (or the
role in PMS_DB_ROLE). A staff member's request names them to the audit log
(`trust.staff_id`), so every write they cause records who did it.

What a browser may do:
  - the session is a cookie: HttpOnly, SameSite=Strict, and Secure unless PMS_COOKIE_SECURE
    is "false" (plain http on a laptop);
  - a request that changes something must send JSON, a browser must not say it came from
    another site (Sec-Fetch-Site), and if it carries an Origin header that origin must be
    PMS_PUBLIC_URL's. With SameSite=Strict that keeps other sites from acting for a signed-in
    person (CSRF);
  - API answers are never cached;
  - served over https, the browser is told to use nothing else (HSTS); no page opens a window
    that keeps a handle on it, and none may use the camera, microphone, location or payments.

Staff see their whole PMC. Owners and tenants get their own endpoints in the portal PRs; here
they can sign in and read who they are, and nothing else. What a staff member may do beyond that
is their roles' (migration 0034, src/pms/api/staff.py): the database refuses the rest, and the
refusal is answered 403.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg import sql
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from pms import accounts, email, exports, geocoding, nightly
from pms.api import (
    addresses,
    assistance,
    bank_deposits,
    banking,
    budgets,
    calendar,
    company,
    custom_roles,
    dashboard,
    directory,
    documents,
    emails,
    general_ledger,
    groups,
    history,
    household,
    inspections,
    journal,
    leads,
    leasing,
    listings,
    maintenance,
    moveout,
    notes,
    notice_templates,
    offers,
    outbox,
    owner,
    payables,
    payment_files,
    people,
    quickbooks,
    receipts,
    reconcile,
    recurring_work_orders,
    renewals,
    rent,
    rentals,
    reports,
    scope,
    screening_criteria,
    setup,
    signers,
    signing,
    staff,
    state_rules,
    switching,
    tenant,
    today,
    utilities,
    vendor_links,
    vendors,
    work_orders,
    year_close,
)
from pms.api.deps import COOKIE, Conn, Current, Staff, download, refusal
from pms.leasing import screening

HEALTH_TIMEOUT = 2.0  # seconds to wait for a database connection before answering 503
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    public_url: str = "http://localhost:8000"
    cookie_secure: bool = True
    db_role: str = "trust_app"
    # The built portal (frontend/dist), served at / when set.
    static_dir: Path | None = None
    # The tenant screening partner (pms.leasing.screening): "fake", or empty for none.
    screening_partner: str = ""
    # The email sender (pms.email): "fake", "smtp" (its settings from the environment), or
    # empty for none.
    email_sender: str = ""
    # The geocoder that finds a property on a map (pms.geocoding): "census", "fake", or empty
    # for none.
    geocoder: str = ""
    # The nightly run (pms.nightly): rent, late fees and recurring bills post by themselves at
    # start and every day at 06:00 UTC. On unless PMS_NIGHTLY is "off".
    nightly: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.environ["DATABASE_URL"],
            public_url=os.environ.get("PMS_PUBLIC_URL", cls.public_url),
            cookie_secure=os.environ.get("PMS_COOKIE_SECURE", "true").lower() != "false",
            db_role=os.environ.get("PMS_DB_ROLE", cls.db_role),
            static_dir=Path(os.environ["PMS_STATIC_DIR"])
            if "PMS_STATIC_DIR" in os.environ
            else None,
            screening_partner=os.environ.get("PMS_SCREENING_PARTNER", ""),
            email_sender=os.environ.get("PMS_EMAIL_SENDER", ""),
            geocoder=os.environ.get("PMS_GEOCODER", ""),
            nightly=os.environ.get("PMS_NIGHTLY", "on").lower() != "off",
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


class ResetRequest(BaseModel):
    pmc_id: UUID
    email: Email


class ResetAvailable(BaseModel):
    # Whether this server sends email, so a forgotten password can be reset from sign-in.
    available: bool


class ResetAsked(BaseModel):
    detail: str


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
    # A staff member's roles (staff.ROLES); none for an owner or a tenant.
    roles: list[str]
    # The areas a staff member may change (deps.AREAS): every one unless their custom roles
    # say otherwise; none for an owner or a tenant.
    areas: list[str]
    # Whether this server sends email (PMS_EMAIL_SENDER); without it, emails wait in the outbox.
    email_on: bool


class InviteLink(BaseModel):
    login_id: UUID
    url: str
    expires_at: datetime
    # Whether the invite was also emailed (the server sends email).
    emailed: bool


class InviteDetails(BaseModel):
    kind: str
    email: str
    name: str
    pmc_name: str
    expires_at: datetime


class Enroll(BaseModel):
    totp_uri: str


# --- the app ------------------------------------------------------------------------------


def create_app(settings: Settings) -> FastAPI:
    def configure(conn: psycopg.Connection[Any]) -> None:
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(settings.db_role)))
        # The planner's JIT compiling costs more than it saves on the app's queries: on a
        # 1,000-unit company it made the trial balance take 1.2 s instead of 0.18 s, and the
        # general ledger 2.1 s instead of 0.5 s (tools/speed.py).
        conn.execute("SET jit = off")
        conn.commit()

    pool = ConnectionPool(
        settings.database_url, min_size=1, max_size=10, configure=configure, open=False
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool.open(wait=False)  # the app starts even if the database isn't up yet
        runs = None
        if settings.nightly:
            runs = nightly.Scheduler(
                settings.database_url, settings.db_role, settings.public_url, app.state.email
            )
            runs.start()
        try:
            yield
        finally:
            if runs is not None:
                runs.stop()
            pool.close()

    app = FastAPI(title="Occupella PMS", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.pool = pool
    app.state.screening = screening.partner_named(settings.screening_partner)
    app.state.geocoder = geocoding.geocoder_named(settings.geocoder)
    # The SMTP settings come straight from the environment, so a password is never part of
    # Settings (which a log or an error could print).
    app.state.email = email.sender_named(settings.email_sender, os.environ)

    https = urlsplit(settings.public_url).scheme == "https"

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method not in SAFE_METHODS:
            # A browser says where a request comes from (OWASP's CSRF cheat sheet): another
            # site, or another origin of this one, is refused whatever its Origin says. Origin
            # stays the check for a browser that doesn't say.
            fetched_from = request.headers.get("sec-fetch-site")
            origin = request.headers.get("origin")
            if fetched_from in CROSS_SITE or (origin is not None and origin != settings.origin):
                return JSONResponse({"detail": "this request came from another site"}, 403)
            if request.headers.get("content-type", "").split(";")[0].strip() != (
                "application/json"
            ):
                return JSONResponse({"detail": "send JSON"}, 415)
        outbox.PUBLIC_URL.set(settings.public_url)
        outbox.SENDING.set(request.app.state.email is not None)
        response = await call_next(request)
        sender = request.app.state.email
        if request.method not in SAFE_METHODS and response.status_code < 400 and sender:
            # After the answer is sent, so after the request's transaction committed: what it
            # wrote to the outbox exists, and goes out.
            delivery = BackgroundTasks()
            delivery.add_task(outbox.deliver, pool, sender)
            response.background = delivery
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = PERMISSIONS_POLICY
        if https:
            response.headers["Strict-Transport-Security"] = STRICT_TRANSPORT
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

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, error: RequestValidationError) -> Response:
        # FastAPI's own answer, in ASCII JSON: what it can't encode as UTF-8 (a lone surrogate,
        # echoed back as the input refused) is escaped instead of failing as a server error.
        body = json.dumps({"detail": jsonable_encoder(error.errors())}, ensure_ascii=True)
        return Response(body, status_code=422, media_type="application/json")

    @app.exception_handler(psycopg.DataError)
    async def unstorable(request: Request, error: psycopg.DataError) -> JSONResponse:
        # Text with a NUL character, which Postgres can't store and psycopg refuses before it is
        # sent: refused as bad input (the transaction is rolled back). Any other data error is
        # still a server error, to be found and fixed.
        if "NUL (0x00)" not in str(error):
            raise error
        return JSONResponse({"detail": "text can't contain a NUL character"}, 422)

    @app.exception_handler(psycopg.errors.InsufficientPrivilege)
    async def not_allowed(request: Request, error: psycopg.Error) -> JSONResponse:
        # A staff member without the role a write takes (the transaction is rolled back).
        reason = refusal(error)
        return JSONResponse({"detail": reason}, 403)

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
            roles=staff.roles_of(conn, session) if session.kind == "staff" else [],
            areas=custom_roles.areas_of(conn, session) if session.kind == "staff" else [],
            email_on=app.state.email is not None,
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
        # Emailed too when the server sends email (ADR 0007); the link is never kept in the
        # outbox, so the page still shows it once, in case the email doesn't arrive.
        emailed = app.state.email is not None
        if emailed:
            email.write_invite(
                conn,
                session.pmc_id,
                body.kind,
                body.person_id,
                accounts.normalize_email(body.email),
                url,
                made.expires_at,
            )
        return InviteLink(
            login_id=made.login_id, url=url, expires_at=made.expires_at, emailed=emailed
        )

    @app.get("/api/password-reset")
    def reset_available() -> ResetAvailable:
        return ResetAvailable(available=app.state.email is not None)

    @app.post("/api/password-reset", status_code=202)
    def ask_reset(body: ResetRequest, conn: Conn) -> ResetAsked:
        """Emails an owner or a tenant a link to set a new password (ADR 0007). The answer is the
        same whether or not the email has a login, so it tells no one who does."""
        if app.state.email is not None:
            reset = accounts.request_reset(conn, body.pmc_id, body.email)
            if reset is not None:
                email.write_password_reset(
                    conn,
                    body.pmc_id,
                    reset.kind,
                    reset.person_id,
                    reset.email,
                    f"{settings.public_url.rstrip('/')}/invite#{reset.invite.token}",
                    reset.invite.expires_at,
                )
        return ResetAsked(
            detail="If that email signs in here as an owner or a tenant, a link to set a new"
            " password is on its way."
        )

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

    @app.get("/api/reports/{name}", response_model=None)
    def report(
        name: str,
        session: Staff,
        conn: Conn,
        fmt: Annotated[Literal["csv", "xlsx", "pdf", "json"], Query(alias="format")] = "csv",
        args: Annotated[list[str] | None, Query(alias="arg")] = None,
        group: scope.Group = None,
        property_id: scope.Property = None,
        owner: Annotated[UUID | None, Query(alias="owner")] = None,
    ) -> Response | reports.Preview:
        # Every report takes the PMC first (tests/api pins it): it always comes from the
        # session, never from the request. "json" is the preview Reports shows on screen
        # (src/pms/api/reports.py); a group, a property or an owner's properties narrow it and
        # every format alike.
        try:
            table = reports.narrowed(
                conn,
                session.pmc_id,
                exports.run_report(conn, name, [session.pmc_id, *(args or [])]),
                group,
                property_id,
                owner,
            )
        except exports.UnknownReport as unknown:
            raise HTTPException(404, str(unknown)) from None
        except exports.ExportError as refused:
            raise HTTPException(422, str(refused)) from None
        except (psycopg.errors.DataError, psycopg.errors.InvalidParameterValue) as bad:
            raise HTTPException(422, str(bad).splitlines()[0]) from None
        if fmt == "json":
            return reports.preview(table)
        return download(exports.export(table, fmt), name.removeprefix("trust_report_"), fmt)

    @app.get("/api/ledger.zip")
    def ledger(session: Staff, conn: Conn) -> Response:
        return download(exports.ledger_zip(conn, session.pmc_id), "ledger", "zip")

    @app.get("/api/records.zip")
    def records(session: Staff, conn: Conn) -> Response:
        """Everything of the company (q132): every record, its files and its ledger. It holds
        everyone's contact details and documents, so only an admin downloads it."""
        row = conn.execute(
            "SELECT trust_staff_holds(%s, %s, 'admin')", (session.pmc_id, session.person_id)
        ).fetchone()
        if row is None or not row[0]:
            raise HTTPException(403, "only an admin downloads everything")
        return download(exports.records_zip(conn, session.pmc_id), "records", "zip")

    app.include_router(owner.router)
    app.include_router(tenant.router)
    app.include_router(maintenance.router)
    app.include_router(screening_criteria.router)
    app.include_router(leads.router)
    app.include_router(leasing.router)
    app.include_router(offers.router)
    app.include_router(listings.router)
    app.include_router(signing.router)
    app.include_router(calendar.router)
    app.include_router(quickbooks.router)
    calendar.hide_feed_tokens(logging.getLogger("uvicorn.access"))
    app.include_router(people.router)
    app.include_router(setup.router)
    app.include_router(payment_files.router)
    app.include_router(rent.router)
    app.include_router(assistance.router)
    app.include_router(addresses.router)
    app.include_router(utilities.router)
    app.include_router(receipts.router)
    app.include_router(rentals.router)
    app.include_router(groups.router)
    app.include_router(directory.router)
    app.include_router(general_ledger.router)
    app.include_router(vendor_links.staff_router)
    app.include_router(vendor_links.router)
    # Before work_orders: its /{work_order_id} would take "recurring" for an id.
    app.include_router(recurring_work_orders.router)
    app.include_router(work_orders.router)
    app.include_router(renewals.router)
    app.include_router(payables.router)
    app.include_router(reconcile.router)
    app.include_router(year_close.router)
    app.include_router(banking.router)
    app.include_router(bank_deposits.router)
    app.include_router(journal.router)
    app.include_router(budgets.router)
    app.include_router(custom_roles.router)
    app.include_router(vendors.router)
    app.include_router(inspections.router)
    app.include_router(switching.router)
    app.include_router(history.router)
    app.include_router(staff.router)
    app.include_router(signers.router)
    app.include_router(state_rules.router)
    app.include_router(moveout.router)
    app.include_router(household.router)
    app.include_router(documents.router)
    app.include_router(company.router)
    app.include_router(notes.router)
    app.include_router(notice_templates.router)
    app.include_router(emails.router)
    app.include_router(today.router)
    app.include_router(dashboard.router)
    if settings.static_dir is not None:
        serve_portal(app, settings.static_dir)
    return app


# What a browser's Sec-Fetch-Site says of a request from another site, or from another origin
# of this site (a sibling subdomain).
CROSS_SITE = frozenset({"cross-site", "same-site"})
# Two years, this host and its subdomains (OWASP's HTTP headers cheat sheet), sent only when
# the public URL is https; not preloaded, which is the operator's choice.
STRICT_TRANSPORT = "max-age=63072000; includeSubDomains"
# Nothing in the portal uses these.
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=()"


# The portal's own pages: a built React app (frontend/dist). Its scripts and styles are files
# of its own, so the policy allows nothing inline and nothing from another origin, but for the
# two fixed <style> elements React Aria (its dialogs and menus) writes: each page carries a fresh
# nonce for them in <meta property="csp-nonce">, and a style element is allowed only with it.
def content_security_policy(nonce: str) -> str:
    return (
        f"default-src 'self'; img-src 'self' data:; style-src 'self' 'nonce-{nonce}';"
        " script-src 'self'; font-src 'self'; connect-src 'self'; frame-ancestors 'none';"
        " base-uri 'none'; form-action 'self'"
    )


def with_nonce(html: str, nonce: str) -> str:
    """The page with the nonce React Aria reads, first thing in its head."""
    return html.replace("<head>", f'<head><meta property="csp-nonce" nonce="{nonce}">', 1)


def serve_portal(app: FastAPI, static_dir: Path) -> None:
    index = static_dir / "index.html"
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def page(path: str) -> Response:
        if path.startswith("api/") or path == "api":
            raise HTTPException(404, "no such endpoint")
        nonce = secrets.token_urlsafe(16)
        return Response(
            with_nonce(index.read_text(encoding="utf-8"), nonce),
            media_type="text/html",
            headers={
                "Content-Security-Policy": content_security_policy(nonce),
                "Cache-Control": "no-cache",
                "X-Frame-Options": "DENY",
            },
        )
