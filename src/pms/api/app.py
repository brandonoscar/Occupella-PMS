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

import contextlib
import json
import logging
import os
import secrets
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from psycopg import sql
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from pms import (
    accounts,
    days,
    deployment,
    email,
    exports,
    geocoding,
    nightly,
    payments,
    phones,
    schema,
    webhooks,
)
from pms.api import (
    addresses,
    ai_keys,
    announcements,
    applicant_details,
    assistance,
    autopay,
    bank_deposits,
    banking,
    budgets,
    calendar,
    chart,
    company,
    custom_roles,
    dashboard,
    deposit_interest,
    deps,
    directory,
    documents,
    drafts,
    emails,
    events,
    general_ledger,
    go_live,
    groups,
    history,
    household,
    inspections,
    journal,
    leads,
    leasing,
    listings,
    maintenance,
    mcp,
    messages,
    moveout,
    notes,
    notice_templates,
    offers,
    online_payments,
    outbox,
    owner,
    owner_held,
    owner_payments,
    owner_portal,
    payables,
    payment_files,
    payment_keys,
    payouts,
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
    switching,
    tax_credit,
    tax_ids,
    tenant,
    throttle,
    today,
    two_step,
    unapplied,
    unit_details,
    utilities,
    vendor_links,
    vendors,
    work_orders,
    year_close,
)
from pms.api.deps import (
    COOKIE,
    CROSS_SITE,
    DISK_FULL,
    Conn,
    Current,
    Staff,
    download,
    refusal,
)
from pms.leasing import screening

HEALTH_TIMEOUT = 2.0  # seconds to wait for a database connection before answering 503
log = logging.getLogger("pms.api")
# Each origin a change was refused from is logged once per process, at most this many.
LOGGED_ORIGINS = 50
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class NearestProxy:
    """With PMS_TRUSTED_PROXIES "*" the only one that connects is the HTTPS proxy on the server
    itself (compose.selfhost.yaml), and the address it adds to X-Forwarded-For is the last: nginx
    as docs/install.md sets it ($proxy_add_x_forwarded_for) appends it to whatever the visitor
    sent. uvicorn, trusting everyone, would take the first, the visitor's own words, so a visitor
    could dodge the sign-in limit with a new address each try and write any address into a
    signature's evidence. This keeps the last alone."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            named = [value for name, value in scope["headers"] if name == b"x-forwarded-for"]
            if named:
                last = b",".join(named).split(b",")[-1].strip()
                headers = [(n, v) for n, v in scope["headers"] if n != b"x-forwarded-for"]
                scope = {**scope, "headers": [*headers, (b"x-forwarded-for", last)]}
        await self.app(scope, receive, send)


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
    # The payments partner tenants pay online through (pms.payments): "stripe" (its keys from
    # the environment), "fake", or empty for none.
    payments: str = ""
    # The nightly run (pms.nightly): rent, late fees and recurring bills post by themselves at
    # start and every day at 06:00 UTC. On unless PMS_NIGHTLY is "off".
    nightly: bool = False
    # Webhooks (pms.webhooks): each event goes to the company's webhooks every 30 seconds. On
    # unless PMS_WEBHOOKS is "off"; a server without PMS_DATA_KEY keeps and sends none.
    webhooks: bool = False
    # "allow" lets a server at an https address run fake partners: a public demo of a synthetic
    # company, and nothing else (pms.deployment). Empty refuses them there.
    fake_partners: str = ""
    # The HTTPS proxy in front of the app (docs/install.md, step 4), whose X-Forwarded-For names
    # the visitor: its address, a comma list of them, or "*" (compose.selfhost.yaml, where only
    # the server itself reaches the app). Then a signature's evidence keeps the signer's address,
    # not the proxy's. Empty trusts no proxy: each address is the connection's.
    trusted_proxies: str = ""

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """The settings in `environ`, the process's own environment unless another is given: an
        app the PMS is mounted in hands it the books' settings alone (AgenticHelixis
        docs/decisions/ADR-one-product.md, phase 3), so a DATABASE_URL of its own never leaks in."""
        env = os.environ if environ is None else environ
        return cls(
            database_url=env["DATABASE_URL"],
            public_url=env.get("PMS_PUBLIC_URL", cls.public_url),
            cookie_secure=env.get("PMS_COOKIE_SECURE", "true").lower() != "false",
            db_role=env.get("PMS_DB_ROLE", cls.db_role),
            static_dir=Path(env["PMS_STATIC_DIR"]) if "PMS_STATIC_DIR" in env else None,
            screening_partner=env.get("PMS_SCREENING_PARTNER", ""),
            email_sender=env.get("PMS_EMAIL_SENDER", ""),
            geocoder=env.get("PMS_GEOCODER", ""),
            payments=env.get("PMS_PAYMENTS", ""),
            nightly=env.get("PMS_NIGHTLY", "on").lower() != "off",
            webhooks=env.get("PMS_WEBHOOKS", "on").lower() != "off",
            fake_partners=env.get("PMS_FAKE_PARTNERS", ""),
            trusted_proxies=env.get("PMS_TRUSTED_PROXIES", "").strip(),
        )

    @property
    def origin(self) -> str:
        """PMS_PUBLIC_URL's origin as a browser sends it in Origin: the scheme and host in lower
        case, without the scheme's default port (docs/edge-cases.md H-161, H-162)."""
        parts = urlsplit(self.public_url.strip())
        scheme = parts.scheme.lower()
        host = parts.hostname or ""
        if ":" in host:  # an IPv6 address
            host = f"[{host}]"
        port = parts.port
        default = {"https": 443, "http": 80}.get(scheme)
        return f"{scheme}://{host}" + (f":{port}" if port is not None and port != default else "")


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


class ServerCompany(BaseModel):
    # The company this server signs in to, when it holds exactly one (a company's own server, or
    # its own stack when hosted), so its sign-in page needs no ?pmc= link. Both empty on a server
    # with more companies, which names none of them.
    pmc_id: UUID | None
    name: str | None


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
    # A tenant's or an owner's phone, as typed, and whether the company may text them (item 14
    # of the founder's review of 11/03); staff give neither.
    phone: Annotated[str, Field(max_length=40)] | None = None
    texts_ok: bool = False
    # A reset an owner or a tenant asked for while their two-step sign-in is on: its code.
    code: Code | None = None


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
    # The company's time zone and its day now (src/pms/days.py): the portal's "today".
    time_zone: str
    today: date


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
    # The words beside "OK to text me", for a tenant or an owner; none for staff.
    text_consent: str | None = None
    # Setting the password takes the code from their authenticator app too (a reset an owner or
    # a tenant asked for while their two-step sign-in is on).
    two_step: bool = False


class Enroll(BaseModel):
    totp_uri: str


# --- the app ------------------------------------------------------------------------------


def create_app(settings: Settings) -> FastAPI:
    # An address no browser opens it at refuses every change (pms.deployment).
    deployment.refuse_public_url(settings.public_url)
    # A server at an https address is one real people use: it refuses the test partners.
    deployment.refuse_fakes(
        settings.public_url,
        {
            "PMS_PAYMENTS": settings.payments,
            "PMS_SCREENING_PARTNER": settings.screening_partner,
            "PMS_GEOCODER": settings.geocoder,
            "PMS_EMAIL_SENDER": settings.email_sender,
        },
        settings.fake_partners,
    )

    def configure(conn: psycopg.Connection[Any]) -> None:
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(settings.db_role)))
        # The planner's JIT compiling costs more than it saves on the app's queries: on a
        # 1,000-unit company it made the trial balance take 1.2 s instead of 0.18 s, and the
        # general ledger 2.1 s instead of 0.5 s (tools/speed.py).
        conn.execute("SET jit = off")
        # A database a newer PMS migrated, or not migrated yet, gets no request (H-041): the
        # connection is refused, and the pool says why in the log.
        schema.check(conn)
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
                settings.database_url,
                settings.db_role,
                settings.public_url,
                app.state.email,
                payments=app.state.payments,
            )
            runs.start()
        else:
            # Nothing posts by itself then (docs/edge-cases.md H-022).
            log.warning(
                "the nightly run is off (PMS_NIGHTLY=off): rent, recurring charges, late fees,"
                " recurring bills and draws post only when `python -m pms.nightly` runs, once a"
                " day, or an admin presses Run rent now"
            )
        courier = None
        if settings.webhooks:
            courier = webhooks.Courier(settings.database_url, settings.db_role)
            courier.start()
        try:
            yield
        finally:
            if courier is not None:
                courier.stop()
            if runs is not None:
                runs.stop()
            pool.close()

    app = FastAPI(title="Occupella PMS", lifespan=lifespan, docs_url=None, redoc_url=None)
    if settings.trusted_proxies:
        # uvicorn's own: from a trusted proxy only, the visitor's address (and the scheme) come
        # from X-Forwarded-For (and -Proto); from anyone else the headers are ignored.
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.trusted_proxies)
        if settings.trusted_proxies == "*":
            # Before it: uvicorn takes the first address when it trusts everyone, which the
            # visitor may write themselves (docs/edge-cases.md U-033).
            app.add_middleware(NearestProxy)
    app.state.settings = settings
    app.state.pool = pool
    app.state.screening = screening.partner_named(settings.screening_partner)
    app.state.geocoder = geocoding.geocoder_named(settings.geocoder)
    # Like the SMTP settings, the server's partner's keys come straight from the environment; a
    # company's own keys, entered in Settings, are sealed in the database (pms.payments.Resolver).
    app.state.payments = payments.Resolver(payments.partner_named(settings.payments, os.environ))
    # The SMTP settings come straight from the environment, so a password is never part of
    # Settings (which a log or an error could print).
    app.state.email = email.sender_named(settings.email_sender, os.environ)
    # What one address may try (pms.api.throttle): failed sign-ins and password resets.
    app.state.sign_in_failures = throttle.Limiter(
        throttle.SIGN_IN_FAILURES, throttle.SIGN_IN_WINDOW
    )
    app.state.resets_asked = throttle.Limiter(throttle.RESETS, throttle.RESET_WINDOW)

    https = urlsplit(settings.public_url).scheme == "https"
    refused_from: set[str] = set()
    # Whether this process has said, once, that it sees a proxy it doesn't trust (H-067).
    untrusted_proxy_said: list[bool] = []

    def log_refused(origin: str) -> None:
        # A server whose PMS_PUBLIC_URL isn't the address people use refuses every change
        # (docs/edge-cases.md H-059); its log names both origins, each once, so another site's
        # requests can't flood it.
        if origin in refused_from or len(refused_from) >= LOGGED_ORIGINS:
            return
        refused_from.add(origin)
        log.warning(
            "refused a change from %r: PMS_PUBLIC_URL's origin is %r, so a browser at another"
            " address can't sign in or change anything",
            origin,
            settings.origin,
        )

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Behind a proxy it doesn't trust, every visitor has the proxy's address, so the sign-in
        # limit counts them all as one and signatures keep the proxy's (docs/edge-cases.md H-067):
        # the PMS can't tell which proxy is right, so it says what it sees, once a process.
        if (
            not settings.trusted_proxies
            and not untrusted_proxy_said
            and "x-forwarded-for" in request.headers
        ):
            untrusted_proxy_said.append(True)
            log.warning(UNTRUSTED_PROXY)
        if request.method not in SAFE_METHODS:
            # A browser says where a request comes from (OWASP's CSRF cheat sheet): another
            # site, or another origin of this one, is refused whatever its Origin says. Origin
            # stays the check for a browser that doesn't say.
            fetched_from = request.headers.get("sec-fetch-site")
            origin = request.headers.get("origin")
            if fetched_from in CROSS_SITE or (origin is not None and origin != settings.origin):
                if origin is not None and origin != settings.origin:
                    log_refused(origin)
                return JSONResponse({"detail": "this request came from another site"}, 403)
            if request.headers.get("content-type", "").split(";")[0].strip() != (
                "application/json"
            ):
                return JSONResponse({"detail": "send JSON"}, 415)
        outbox.PUBLIC_URL.set(settings.public_url)
        outbox.SENDING.set(request.app.state.email is not None)
        outbox.PARTNER.set(request.app.state.payments)
        response = await call_next(request)
        sender = request.app.state.email
        partner = request.app.state.payments
        if (
            request.method not in SAFE_METHODS
            and response.status_code < 400
            and (sender or partner)
        ):
            # After the answer is sent, so after the request's transaction committed: the autopay
            # payments it started go to the payments partner, and what it wrote to the outbox
            # exists, and goes out.
            after = BackgroundTasks()
            if partner:
                after.add_task(autopay.submit, pool, partner)
            if sender:
                after.add_task(outbox.deliver, pool, sender)
            response.background = after
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        # No other site loads an answer of the PMS into its page (Spectre's side channel reads
        # what a page loads); every page of the portal loads only the PMS's own.
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
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
            accounts.CompanyNotActive: 403,
        }.get(type(error), 422)
        return JSONResponse({"detail": str(error)}, status)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, error: RequestValidationError) -> Response:
        # FastAPI's own answer, in ASCII JSON (what it can't encode as UTF-8, a lone surrogate, is
        # escaped instead of failing as a server error), without the input refused: echoed back,
        # a body just under MAX_BODY made one unauthenticated request cost the server seconds and
        # megabytes (the AI pen test of 11/06). Where and why is what the portal reads.
        said = [
            {key: value for key, value in found.items() if key in ("loc", "msg", "type", "ctx")}
            for found in error.errors()
        ]
        body = json.dumps({"detail": jsonable_encoder(said)}, ensure_ascii=True)
        return Response(body, status_code=422, media_type="application/json")

    @app.exception_handler(psycopg.DataError)
    async def unstorable(request: Request, error: psycopg.DataError) -> JSONResponse:
        # Text with a NUL character, which Postgres can't store and psycopg refuses before it is
        # sent: refused as bad input (the transaction is rolled back). Any other data error is
        # still a server error, to be found and fixed.
        if "NUL (0x00)" not in str(error):
            raise error
        return JSONResponse({"detail": "text can't contain a NUL character"}, 422)

    @app.exception_handler(psycopg.errors.DiskFull)
    async def disk_full(request: Request, error: psycopg.Error) -> JSONResponse:
        # The transaction is rolled back; whoever runs the server frees space (docs/support.md).
        log.error("the database's disk is full: %s", error.diag.message_primary)
        return JSONResponse({"detail": DISK_FULL}, 503, headers={"Retry-After": "60"})

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
            time_zone=days.time_zone(conn, session.pmc_id),
            today=days.today(conn, session.pmc_id),
        )

    @app.get("/health")
    def health() -> JSONResponse:
        try:
            with pool.connection(timeout=HEALTH_TIMEOUT) as conn:
                conn.execute("SELECT 1")
        except Exception:  # any failure to reach the database is "unavailable"
            return JSONResponse({"status": "database unavailable"}, 503)
        return JSONResponse({"status": "ok"})

    @app.get("/api/sign-in")
    def server_company(conn: Conn) -> ServerCompany:
        rows = conn.execute(
            "SELECT pmc_id, display_name FROM trust_pmcs ORDER BY pmc_id LIMIT 2"
        ).fetchall()
        if len(rows) != 1:
            return ServerCompany(pmc_id=None, name=None)
        return ServerCompany(pmc_id=rows[0][0], name=rows[0][1])

    @app.post("/api/sign-in")
    def sign_in(body: SignIn, request: Request, response: Response, conn: Conn) -> Me:
        address = throttle.address_of(request)
        throttle.refuse_if_waiting(app.state.sign_in_failures, address, "failed sign-ins")
        try:
            signed = accounts.sign_in(conn, body.pmc_id, body.email, body.password, body.code)
        except accounts.SignInRefused:
            app.state.sign_in_failures.hit(address)
            raise
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
    def ask_reset(body: ResetRequest, request: Request, conn: Conn) -> ResetAsked:
        """Emails an owner or a tenant a link to set a new password (ADR 0007). The answer is the
        same whether or not the email has a login, so it tells no one who does. An address asks
        at most throttle.RESETS times an hour."""
        address = throttle.address_of(request)
        throttle.refuse_if_waiting(app.state.resets_asked, address, "password resets")
        app.state.resets_asked.hit(address)
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
        invited = accounts.view_invite(conn, body.token)
        phone = None
        if invited.text_consent is not None:
            if not (body.phone or "").strip():
                raise HTTPException(422, "Enter your phone number.")
            try:
                phone = phones.e164(body.phone or "")
            except phones.BadPhone as bad:
                raise HTTPException(422, str(bad)) from None
        done = accounts.accept_invite(conn, body.token, body.password, body.code)
        if isinstance(done, accounts.Enrollment):
            return Enroll(totp_uri=done.totp_uri)
        if phone is not None and invited.text_consent is not None:
            who = done.session
            phones.record_signup(
                conn,
                who.pmc_id,
                who.login_id,
                who.kind,
                who.person_id,
                phone,
                body.texts_ok,
                invited.text_consent,
            )
        return signed_in(response, done, conn)

    @app.post("/api/invites/confirm")
    def confirm_invite(body: ConfirmInvite, response: Response, conn: Conn) -> Me:
        return signed_in(response, accounts.confirm_invite(conn, body.token, body.code), conn)

    @app.post("/api/logins/{login_id}/disable", status_code=204)
    def disable(login_id: UUID, session: Staff, conn: Conn) -> None:
        if login_id == session.login_id:
            raise HTTPException(422, "you can't disable your own login")
        # A staff login takes admin (the database checks it); an owner's or a tenant's, the area
        # their invite takes, "who can sign in" (docs/edge-cases.md U-009).
        kind = conn.execute(
            "SELECT kind FROM trust_logins WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, login_id),
        ).fetchone()
        if kind is not None and kind[0] != "staff" and not deps.may(conn, session, "people"):
            raise HTTPException(
                403, f"your roles don't let you change {deps.AREAS['people']}: an admin can add it"
            )
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

    @app.get("/api/ledger.zip", response_class=Response, responses=ZIP)
    def ledger(session: Staff, conn: Conn) -> Response:
        return download(exports.ledger_zip(conn, session.pmc_id), "ledger", "zip")

    @app.get("/api/records.zip", response_class=Response, responses=ZIP)
    def records(session: Staff, conn: Conn) -> Response:
        """Everything of the company (q132): every record, its files and its ledger. It holds
        everyone's contact details and documents, so only an admin downloads it."""
        row = conn.execute(
            "SELECT trust_staff_holds(%s, %s, 'admin')", (session.pmc_id, session.person_id)
        ).fetchone()
        if row is None or not row[0]:
            raise HTTPException(403, "only an admin downloads everything")
        # Written to a file as its rows come and sent from it a block at a time, never whole in
        # memory: a company may hold gigabytes of files (docs/edge-cases.md H-097, F-126).
        with contextlib.ExitStack() as stack:
            spool = stack.enter_context(tempfile.TemporaryFile())
            exports.write_records_zip(conn, session.pmc_id, spool)
            spool.seek(0)
            sent = stack.pop_all()  # the file stays open until it is sent

        def blocks() -> Iterator[bytes]:
            with sent:
                while block := spool.read(1024 * 1024):
                    yield block

        return StreamingResponse(
            blocks(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="records.zip"'},
        )

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
    app.include_router(chart.router)
    app.include_router(unapplied.router)
    app.include_router(owner_held.router)
    app.include_router(go_live.router)
    app.include_router(payment_files.router)
    app.include_router(rent.router)
    # Before online_payments, whose paths it shares a prefix with.
    app.include_router(payment_keys.router)
    app.include_router(online_payments.router)
    app.include_router(owner_payments.router)
    app.include_router(owner_portal.router)
    app.include_router(messages.router)
    app.include_router(announcements.router)
    app.include_router(autopay.router)
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
    app.include_router(payouts.router)
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
    app.include_router(moveout.router)
    app.include_router(deposit_interest.router)
    app.include_router(tax_credit.router)
    app.include_router(unit_details.router)
    app.include_router(household.router)
    app.include_router(documents.router)
    app.include_router(company.router)
    app.include_router(notes.router)
    app.include_router(notice_templates.router)
    app.include_router(emails.router)
    app.include_router(today.router)
    app.include_router(dashboard.router)
    app.include_router(tax_ids.router)
    app.include_router(applicant_details.router)
    app.include_router(two_step.router)
    # Software with an API key (ADR 0011): its own header, never a session's cookie.
    app.include_router(ai_keys.router)
    app.include_router(drafts.router)
    app.include_router(events.router)
    app.include_router(mcp.router)
    if settings.static_dir is not None:
        serve_portal(app, settings.static_dir)
    # Outermost: no request reads more than MAX_BODY bytes into the process (H-072).
    app.add_middleware(BodyLimit)
    return app


# The most a request may send, in bytes: the largest thing the PMS takes is a 5 MB file as base64
# in JSON, about 7 MB (docs/edge-cases.md H-072: with no limit, anyone could make the one process
# read hundreds of megabytes into memory, signed in or not).
MAX_BODY = 10 * 1024 * 1024
TOO_BIG = "this request is larger than the PMS takes (10 MB)"


class BodyLimit:
    """Refuses a request whose body is larger than MAX_BODY (413): at once when it says its
    length, else counted as it streams in, its reading stopped there."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY):
            await JSONResponse({"detail": TOO_BIG}, 413)(scope, receive, send)
            return
        read = 0
        too_big = False
        started = False

        async def counted() -> Message:
            nonlocal read, too_big
            message = await receive()
            if message["type"] == "http.request":
                read += len(message.get("body", b""))
                if read > MAX_BODY:
                    too_big = True
                    # The app reads no more: to it, the client went away.
                    return {"type": "http.disconnect"}
            return message

        async def watched(message: Message) -> None:
            nonlocal started
            if too_big:
                return  # the app's answer to a body it never got whole: the 413 goes instead
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted, watched)
        except ClientDisconnect:
            if not too_big:
                raise
        if too_big and not started:
            await JSONResponse({"detail": TOO_BIG}, 413)(scope, receive, send)


UNTRUSTED_PROXY = (
    "requests name a forwarded address (X-Forwarded-For) but PMS_TRUSTED_PROXIES is empty:"
    " every visitor has the proxy's address, so the sign-in limit counts them all as one; set"
    " PMS_TRUSTED_PROXIES to the proxy's address (docs/install.md)"
)
# Two years, this host and its subdomains (OWASP's HTTP headers cheat sheet), sent only when
# the public URL is https; not preloaded, which is the operator's choice.
STRICT_TRANSPORT = "max-age=63072000; includeSubDomains"
# Nothing in the portal uses these.
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=()"
# A zip download, as the API's schema states it (what OWASP ZAP checks an answer against).
ZIP: dict[int | str, dict[str, Any]] = {
    200: {"description": "A zip file", "content": {"application/zip": {}}}
}


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
