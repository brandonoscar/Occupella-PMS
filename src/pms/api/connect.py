"""Stripe Connect through the API (docs/adr/0014-stripe-connect.md, src/pms/connect.py).

- Company setup: an admin connects the company's own Stripe account (Connect Stripe: an account
  made for it if it has none, then Stripe's hosted onboarding, which returns to Company setup),
  sees whether it takes charges and pays out and what Stripe still needs, and disconnects it.
  Staff choose who pays the card fee, and see where a state's rule restricts it.
- A tenant pays a lease by bank or card, and sets up autopay by either, through the connected
  account (a direct charge with Occupella's fee as its application fee); an owner adds money by
  either. Who pays the fees, and how much of them a tenant is asked for, is `lease_terms`.
- The platform's one Connect webhook: each report's connected account names the company, and a
  report about an account no company connected is refused (404, so Stripe sends it again); one
  about a payment started on another account is acknowledged and never booked.
- The fake platform's pages (development and CI): onboarding finished or left wanting, a bank or
  card payment paid, failed, left, disputed and the dispute won or lost, a card or bank account
  saved for autopay.

A payment through Connect is recorded as any payment online (src/pms/api/online_payments.py,
src/pms/api/owner_payments.py), its dispute by src/pms/api/disputes.py. The AI never reads a
connected account; only an admin changes one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, StringConstraints

from pms import connect, payments
from pms.api import online_payments as op
from pms.api import outbox, owner_payments, rent, tenant
from pms.api.deps import Conn, Owner, Staff, Tenant
from pms.api.payment_keys import is_admin

router = APIRouter(prefix="/api/payments/connect")

ZERO = Decimal(0)
Method = Literal["ach", "card"]
FeePayer = Literal["company", "tenant"]
NOT_OFFERED = "This server doesn't offer Stripe Connect."
ADMINS_ONLY = "Only an admin can connect or disconnect the company's Stripe account."
CARD_TOO_MUCH = f"A card payment is ${connect.CARD_MAX:,.2f} at most: pay more by bank."
OWN_KEYS = "This company takes payments through its own Stripe keys: pay by bank on Pay online."


def platform_of(request: Request) -> connect.Platform | None:
    return connect.platform_of(op.resolver_of(request))


def fees() -> dict[connect.Method, connect.Fee]:
    """Occupella's fee on each method, as the server's environment sets it (checked at start)."""
    return connect.fees_from(os.environ)


def connected_partner(
    request: Request, conn: psycopg.Connection[Any], pmc_id: UUID
) -> connect.Connected | None:
    """The company's connected account, bound to this request, when its payments go through it."""
    partner = op.partner_of(request, conn, pmc_id)
    if isinstance(partner, connect.StripeConnected | connect.FakeConnected):
        return partner
    return None


# --- who pays what ----------------------------------------------------------------------------


def card_fee_payer(conn: psycopg.Connection[Any], pmc_id: UUID) -> tuple[FeePayer, datetime | None]:
    """Who the company chose to pay the card fee (the company, until it chooses), and when."""
    row = conn.execute(
        "SELECT card_fee_payer, created_at FROM trust_card_fee_settings WHERE pmc_id = %s"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (pmc_id,),
    ).fetchone()
    return ("company", None) if row is None else (row[0], row[1])


def state_of(conn: psycopg.Connection[Any], lease_id: UUID) -> str | None:
    """The state of the lease's property, as its address says; None without one."""
    row = conn.execute(
        "SELECT a.state FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id,"
        " LATERAL trust_property_address(u.property_id) AS a WHERE l.id = %s",
        (lease_id,),
    ).fetchone()
    return None if row is None else str(row[0])


@dataclass(frozen=True)
class Terms:
    method: Method
    fee_payer: FeePayer
    tenant_fee: Decimal
    platform_fee: Decimal
    # The tenant pays a card fee: the payment page takes no debit or prepaid card.
    credit_only: bool
    # Why the company pays though it chose the tenant (a state's ban, autopay), in words.
    why_company: str | None


def lease_terms(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    lease_id: UUID,
    method: Method,
    amount: Decimal,
    recurring: bool = False,
) -> Terms:
    """A tenant's payment's fees through Connect: Occupella's fee on the amount, and who pays it
    with Stripe's. A bank payment's by the company's choice (trust_payment_settings), the tenant
    adding exactly what covers both; a card payment's by its card fee choice, where the property's
    state allows a surcharge and not on autopay, the tenant adding no more than the cap."""
    platform_fee = fees()[method].on(amount)
    chosen, _ = op.settings_of(conn, pmc_id)
    if method == "ach":
        if chosen.ach_fee_payer == "tenant":
            added = connect.gross_up("ach", amount, platform_fee)
            return Terms("ach", "tenant", added, platform_fee, False, None)
        return Terms("ach", "company", ZERO, platform_fee, False, None)
    payer, _ = card_fee_payer(conn, pmc_id)
    card = connect.card_fee_terms(payer, state_of(conn, lease_id), recurring)
    added = connect.tenant_card_fee(amount, platform_fee, card.cap) if card.tenant_pays else ZERO
    if added > 0:
        return Terms("card", "tenant", added, platform_fee, True, None)
    return Terms("card", "company", ZERO, platform_fee, False, card.why_company)


def on_its_account(conn: psycopg.Connection[Any], pmc_id: UUID, autopay_id: UUID) -> bool:
    """Whether an autopay through Connect was saved at the company's connected account now: one
    saved at an account since disconnected or replaced can't be charged there."""
    now = connect.connection_of(conn, pmc_id)
    row = conn.execute(
        "SELECT account_id FROM trust_connected_payments WHERE autopay_id = %s", (autopay_id,)
    ).fetchone()
    return now is not None and row is not None and row[0] == now.account


def autopay_terms(
    conn: psycopg.Connection[Any], pmc_id: UUID, autopay_id: UUID, lease_id: UUID, amount: Decimal
) -> Terms:
    """An autopay's payment through Connect: by the method it saved, its card fee the company's."""
    row = conn.execute("SELECT method FROM trust_autopay WHERE id = %s", (autopay_id,)).fetchone()
    method: Method = "card" if row is not None and row[0] == "card" else "ach"
    return lease_terms(conn, pmc_id, lease_id, method, amount, recurring=True)


def started_on_account(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    payment: UUID | None = None,
    owner_payment: UUID | None = None,
    autopay_id: UUID | None = None,
    of_autopay: UUID | None = None,
) -> None:
    """Records which connected account a payment, an owner's payment or an autopay was started on:
    the company's now, or for an autopay's payment (`of_autopay`), the account it was saved at."""
    if of_autopay is not None:
        row = conn.execute(
            "SELECT account_id FROM trust_connected_payments WHERE autopay_id = %s", (of_autopay,)
        ).fetchone()
        assert row is not None
        account = str(row[0])
    else:
        found = connect.connection_of(conn, pmc_id)
        assert found is not None
        account = found.account
    conn.execute(
        "INSERT INTO trust_connected_payments (pmc_id, account_id, online_payment_id,"
        " owner_payment_id, autopay_id) VALUES (%s, %s, %s, %s, %s)",
        (pmc_id, account, payment, owner_payment, autopay_id),
    )


# --- Company setup ----------------------------------------------------------------------------


class FeeLine(BaseModel):
    method: Method
    # Occupella's fee on a payment by this method, in words: "$0.50".
    words: str


class StateRule(BaseModel):
    state: str
    # What the state's rule says of a fee for paying by card or online.
    says: str
    # Whether a tenant's card fee is banned there (the company pays it), capped, or neither.
    card: Literal["banned", "capped", "allowed"]


class ConnectView(BaseModel):
    # Whether this server offers Stripe Connect, through which platform (the fake one in
    # development and CI), and whether in live mode.
    offered: bool
    platform: str | None
    live: bool
    connected: bool
    # The last four characters of the connected account's id: all that shows of it.
    account_ends: str | None
    charges_enabled: bool
    payouts_enabled: bool
    details_submitted: bool
    requirements_due: list[str]
    # Whether the company's payments go through it now.
    using: bool
    connected_at: datetime | None
    connected_by: str | None
    may_change: bool
    fees: list[FeeLine]
    card_fee_payer: FeePayer
    card_fee_changed_at: datetime | None
    # The rules of the states the company's properties are in, as docs/law.md records them.
    rules: list[StateRule]
    card_max: str


LATEST_ROW = (
    "SELECT c.created_at, s.display_name FROM trust_connected_accounts AS c"
    " JOIN trust_staff AS s ON s.id = c.recorded_by"
    " WHERE c.pmc_id = %s ORDER BY c.created_at DESC, c.id DESC LIMIT 1"
)
STATES = (
    "SELECT DISTINCT a.state FROM trust_properties AS p,"
    " LATERAL trust_property_address(p.id) AS a WHERE p.pmc_id = %s"
)


def rules_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> list[StateRule]:
    found = []
    for (state,) in sorted(conn.execute(STATES, (pmc_id,)).fetchall()):
        said = [
            text
            for text in (
                connect.SURCHARGE_BANNED.get(state),
                connect.SURCHARGE_CAPPED.get(state, (None, None))[1],
                connect.FREE_WAY_TO_PAY.get(state),
            )
            if text
        ]
        if not said:
            continue
        card: Literal["banned", "capped", "allowed"] = (
            "banned"
            if state in connect.SURCHARGE_BANNED
            else "capped"
            if state in connect.SURCHARGE_CAPPED
            else "allowed"
        )
        found.append(StateRule(state=state, says="; ".join(said), card=card))
    return found


def view(conn: psycopg.Connection[Any], request: Request, session: Any) -> ConnectView:
    platform = platform_of(request)
    found = connect.connection_of(conn, session.pmc_id)
    latest = conn.execute(LATEST_ROW, (session.pmc_id,)).fetchone()
    resolver = op.resolver_of(request)
    using = (
        isinstance(resolver, connect.ConnectResolver)
        and resolver.connected(conn, session.pmc_id) is not None
    )
    payer, changed = card_fee_payer(conn, session.pmc_id)
    return ConnectView(
        offered=platform is not None,
        platform=None if platform is None else platform.name,
        live=platform is not None and platform.live,
        connected=found is not None,
        account_ends=None if found is None else found.account[-4:],
        charges_enabled=found is not None and found.charges_enabled,
        payouts_enabled=found is not None and found.payouts_enabled,
        details_submitted=found is not None and found.details_submitted,
        requirements_due=[] if found is None else list(found.requirements_due),
        using=using,
        connected_at=None if latest is None else latest[0],
        connected_by=None if latest is None else latest[1],
        may_change=is_admin(conn, session),
        fees=[FeeLine(method=m, words=fee.words()) for m, fee in fees().items()],
        card_fee_payer=payer,
        card_fee_changed_at=changed,
        rules=rules_of(conn, session.pmc_id),
        card_max=rent.cents(connect.CARD_MAX),
    )


def refuse_unless_admin(conn: psycopg.Connection[Any], session: Any) -> None:
    if not is_admin(conn, session):
        raise HTTPException(403, ADMINS_ONLY)


def offered(request: Request) -> connect.Platform:
    platform = platform_of(request)
    if platform is None:
        raise HTTPException(404, NOT_OFFERED)
    return platform


@router.get("")
def get_connect(session: Staff, conn: Conn, request: Request) -> ConnectView:
    return view(conn, request, session)


class Link(BaseModel):
    # Stripe's onboarding page, where the admin gives Stripe what it asks; it returns to Company
    # setup.
    url: str


@router.post("/start")
def start(session: Staff, conn: Conn, request: Request) -> Link:
    """Connects the company's Stripe account: an account made for it on the platform if it has
    none here (or only one made in the other mode, or at another platform), then a link to
    Stripe's onboarding for it, to finish or to change what Stripe holds."""
    refuse_unless_admin(conn, session)
    platform = offered(request)
    # Two admins at once make one account, not two.
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('connect ' || %s::uuid, 0))",
        (session.pmc_id,),
    )
    found = connect.connection_of(conn, session.pmc_id)
    if found is None or found.partner != platform.name or found.livemode != platform.live:
        made = conn.execute(
            "SELECT count(*) FROM trust_connected_accounts WHERE pmc_id = %s", (session.pmc_id,)
        ).fetchone()
        assert made is not None
        try:
            account = platform.create_account(session.pmc_id, int(made[0]))
        except payments.PartnerFailed:
            raise HTTPException(502, op.PARTNER_DOWN) from None
        try:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO trust_connected_accounts (pmc_id, account_id, partner, livemode)"
                    " VALUES (%s, %s, %s, %s)",
                    (session.pmc_id, account, platform.name, platform.live),
                )
        except psycopg.errors.CheckViolation:
            raise HTTPException(409, "That Stripe account is another company's.") from None
    else:
        account = found.account
    base = outbox.PUBLIC_URL.get()
    try:
        url = platform.onboarding_link(
            account, f"{base}/settings/setup?connect=again", f"{base}/settings/setup?connect=back"
        )
    except payments.PartnerFailed:
        raise HTTPException(502, op.PARTNER_DOWN) from None
    return Link(url=url)


def record_state(conn: psycopg.Connection[Any], pmc_id: UUID, state: connect.AccountState) -> bool:
    """What Stripe said of the company's account, once per report."""
    made = conn.execute(
        "INSERT INTO trust_connected_account_states (pmc_id, account_id, charges_enabled,"
        " payouts_enabled, details_submitted, requirements_due, partner_event, reported_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (partner_event) DO NOTHING"
        " RETURNING id",
        (
            pmc_id,
            state.account,
            state.charges_enabled,
            state.payouts_enabled,
            state.details_submitted,
            list(state.requirements_due),
            state.event_id,
            state.reported_at,
        ),
    ).fetchone()
    return made is not None


@router.post("/refresh")
def refresh(session: Staff, conn: Conn, request: Request) -> ConnectView:
    """Asks Stripe now what the company's account is (back from onboarding, Stripe's report may
    not have come yet). The fake platform says it on its own page, so nothing is asked of it."""
    platform = offered(request)
    found = connect.connection_of(conn, session.pmc_id)
    if found is None:
        raise HTTPException(404, "The company hasn't connected a Stripe account.")
    if isinstance(platform, connect.StripePlatform) and found.partner == platform.name:
        try:
            record_state(conn, session.pmc_id, platform.state(found.account))
        except payments.PartnerFailed:
            raise HTTPException(502, op.PARTNER_DOWN) from None
    return view(conn, request, session)


@router.post("/disconnect", status_code=204)
def disconnect(session: Staff, conn: Conn, request: Request) -> Response:
    """Disconnects the company's Stripe account: its payments go back to its own keys or the
    server's partner, if any. Reports of payments started through it still land (the account
    stays the company's); the autopays saved at it stop, each tenant told to set it up again."""
    refuse_unless_admin(conn, session)
    found = connect.connection_of(conn, session.pmc_id)
    if found is None:
        raise HTTPException(404, "The company hasn't connected a Stripe account.")
    conn.execute(
        "INSERT INTO trust_connected_accounts (pmc_id, account_id, partner, livemode)"
        " VALUES (%s, NULL, %s, %s)",
        (session.pmc_id, found.partner, found.livemode),
    )
    stop_autopays_at(conn, session.pmc_id, found.account)
    return Response(status_code=204)


# The company's autopays saved at an account, still on.
SAVED_AT = """
SELECT a.id FROM trust_autopay AS a
JOIN trust_connected_payments AS c ON c.autopay_id = a.id
WHERE a.pmc_id = %s AND c.account_id = %s
  AND NOT EXISTS (SELECT 1 FROM trust_autopay_stops AS s WHERE s.autopay_id = a.id)
ORDER BY a.created_at, a.id
"""


def stop_autopays_at(conn: psycopg.Connection[Any], pmc_id: UUID, account: str) -> int:
    """Stops each autopay saved at an account the company no longer takes payments through (it
    can't be charged at another), and tells its tenant to set it up again; how many stopped."""
    from pms import autopay  # the autopay's own rules; it reads this module's terms

    stopped = 0
    day = rent.today(conn, pmc_id)
    for (autopay_id,) in conn.execute(SAVED_AT, (pmc_id, account)).fetchall():
        if not autopay.stop(conn, pmc_id, autopay_id, "account changed"):
            continue
        stopped += 1
        saved = conn.execute(
            "SELECT 1 FROM trust_autopay_accounts WHERE autopay_id = %s", (autopay_id,)
        ).fetchone()
        if saved is not None and outbox.SENDING.get():
            autopay.write_email(
                conn,
                pmc_id,
                autopay.saved(conn, pmc_id, autopay_id, day),
                "autopay_stopped",
                outbox.PUBLIC_URL.get(),
                why=autopay.STOPPED_BY_ITSELF["account changed"],
            )
    return stopped


class CardFees(BaseModel):
    card_fee_payer: FeePayer


@router.post("/card-fees", status_code=204)
def change_card_fees(session: Staff, conn: Conn, body: CardFees) -> Response:
    """Who pays the card fee: the company, or the tenant where the property's state and the card
    networks allow it (lease_terms). The same again writes nothing."""
    current, _ = card_fee_payer(conn, session.pmc_id)
    if current != body.card_fee_payer:
        conn.execute(
            "INSERT INTO trust_card_fee_settings (pmc_id, card_fee_payer) VALUES (%s, %s)",
            (session.pmc_id, body.card_fee_payer),
        )
    return Response(status_code=204)


# --- the tenant's side ------------------------------------------------------------------------


class MethodTerms(BaseModel):
    method: Method
    fee_payer: FeePayer
    # Why the company pays a card fee the company chose the tenant to pay, in words.
    why_company: str | None
    # For the amount asked about: what the tenant adds, and the whole.
    tenant_fee: str | None
    total: str | None


class LeaseMethods(BaseModel):
    # Whether the lease's payments go through Connect now: then the tenant picks bank or card.
    connect: bool
    card_max: str
    methods: list[MethodTerms]
    # Autopay's terms by each method (a card fee on autopay is the company's).
    autopay: list[MethodTerms]


Amount = Annotated[Decimal, Field(gt=0, le=Decimal("100000"), decimal_places=2)]


def method_terms(terms: Terms, amount: Decimal | None) -> MethodTerms:
    return MethodTerms(
        method=terms.method,
        fee_payer=terms.fee_payer,
        why_company=terms.why_company,
        tenant_fee=None if amount is None else rent.cents(terms.tenant_fee),
        total=None if amount is None else rent.cents(amount + terms.tenant_fee),
    )


@router.get("/leases/{lease_id}")
def lease_methods(
    session: Tenant,
    conn: Conn,
    request: Request,
    lease_id: UUID,
    amount: Annotated[Decimal | None, Query(gt=0, le=Decimal("100000"), decimal_places=2)] = None,
) -> LeaseMethods:
    """How the tenant may pay the lease through Connect, and for `amount`, what each way costs
    them, shown before they pay."""
    tenant.lease_of(conn, session, lease_id)
    if connected_partner(request, conn, session.pmc_id) is None:
        return LeaseMethods(
            connect=False, card_max=rent.cents(connect.CARD_MAX), methods=[], autopay=[]
        )
    priced = amount if amount is not None else Decimal("100.00")
    return LeaseMethods(
        connect=True,
        card_max=rent.cents(connect.CARD_MAX),
        methods=[
            method_terms(lease_terms(conn, session.pmc_id, lease_id, m, priced), amount)
            for m in connect.METHODS
        ],
        autopay=[
            method_terms(lease_terms(conn, session.pmc_id, lease_id, m, priced, True), None)
            for m in connect.METHODS
        ],
    )


class ConnectPay(BaseModel):
    amount: Amount
    method: Method


def ready(
    request: Request, conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID
) -> connect.Connected:
    """The connected account a tenant's payment of the lease starts on, or why not (409)."""
    chosen, _ = op.settings_of(conn, pmc_id)
    reason = op.why_not(conn, request, pmc_id, lease_id, chosen)
    if reason is not None:
        raise HTTPException(409, op.SAID[reason])
    partner = connected_partner(request, conn, pmc_id)
    if partner is None:
        raise HTTPException(409, OWN_KEYS)
    return partner


def done_url(path: str) -> str:
    return f"{outbox.PUBLIC_URL.get()}{path}"


@router.post("/leases/{lease_id}/checkout", status_code=201)
def checkout(
    session: Tenant, conn: Conn, request: Request, lease_id: UUID, body: ConnectPay
) -> op.Started:
    """Starts a tenant's payment of the lease by bank or card on the company's connected account,
    its fees as lease_terms says, the fee shown on Stripe's page as its own line."""
    payer = tenant.still_on(conn, session, lease_id)
    partner = ready(request, conn, session.pmc_id, lease_id)
    if body.method == "card" and body.amount > connect.CARD_MAX:
        raise HTTPException(422, CARD_TOO_MUCH)
    terms = lease_terms(conn, session.pmc_id, lease_id, body.method, body.amount)
    payment = uuid4()
    memo = rent.lease_memo(conn, lease_id)
    lines = [(f"Rent – {memo}", body.amount)]
    if terms.tenant_fee > 0:
        lines.append(("Card fee" if body.method == "card" else "Processing fee", terms.tenant_fee))
    try:
        started = partner.connect_checkout(
            payment,
            lines,
            done_url("/pay/done"),
            body.method,
            terms.platform_fee,
            terms.credit_only,
        )
    except payments.PartnerFailed:
        raise HTTPException(502, op.PARTNER_DOWN) from None
    conn.execute(
        "INSERT INTO trust_online_payments (id, pmc_id, lease_id, tenant_id, amount, tenant_fee,"
        " fee_payer, method, partner, partner_ref, platform_fee)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            payment,
            session.pmc_id,
            lease_id,
            payer,
            body.amount,
            terms.tenant_fee,
            terms.fee_payer,
            body.method,
            partner.name,
            started.ref,
            terms.platform_fee,
        ),
    )
    started_on_account(conn, session.pmc_id, payment=payment)
    return op.Started(id=payment, url=started.url)


class ConnectAutopay(BaseModel):
    rule: Literal["fixed", "balance"]
    amount: Amount
    method: Method


class AutopayStarted(BaseModel):
    id: UUID
    url: str


@router.post("/leases/{lease_id}/autopay", status_code=201)
def set_up_autopay(
    session: Tenant, conn: Conn, request: Request, lease_id: UUID, body: ConnectAutopay
) -> AutopayStarted:
    """Sets up autopay of the lease through Connect, by bank or card (stopping the tenant's
    autopay of it before), and sends the tenant to Stripe's page to save what it pays from."""
    from pms import autopay  # the autopay's own rules; it reads this module's terms

    payer = tenant.still_on(conn, session, lease_id)
    partner = ready(request, conn, session.pmc_id, lease_id)
    if body.method == "card" and body.amount > connect.CARD_MAX:
        raise HTTPException(422, CARD_TOO_MUCH)
    from pms.api import autopay as autopay_api  # its list of the tenant's autopays

    for before in autopay_api.autopays(conn, session.pmc_id, lease_id, payer):
        if before.stopped is None:
            autopay.stop(conn, session.pmc_id, before.id, "replaced")
    made = conn.execute(
        "INSERT INTO trust_autopay (pmc_id, lease_id, tenant_id, rule, amount, partner, method)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (session.pmc_id, lease_id, payer, body.rule, body.amount, partner.name, body.method),
    ).fetchone()
    assert made is not None
    started_on_account(conn, session.pmc_id, autopay_id=made[0])
    try:
        page = partner.connect_save(made[0], done_url("/pay/done"), body.method)
    except payments.PartnerFailed:
        raise HTTPException(502, op.PARTNER_DOWN) from None
    return AutopayStarted(id=made[0], url=page.url)


# --- the owner's side -------------------------------------------------------------------------


class OwnerMethods(BaseModel):
    # Whether the owner's payments go through Connect now: then they pick bank or card, with no
    # fee either way (an owner's is always the company's).
    connect: bool
    card_max: str


@router.get("/owner")
def owner_methods(session: Owner, conn: Conn, request: Request) -> OwnerMethods:
    return OwnerMethods(
        connect=connected_partner(request, conn, session.pmc_id) is not None,
        card_max=rent.cents(connect.CARD_MAX),
    )


class OwnerConnectPay(BaseModel):
    account_id: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    amount: Amount
    method: Method


@router.post("/owner/checkout", status_code=201)
def owner_checkout(
    session: Owner, conn: Conn, request: Request, body: OwnerConnectPay
) -> op.Started:
    """Starts an owner's payment into one of their property accounts by bank or card, through the
    company's connected account: Occupella's fee and Stripe's are the company's, never added to
    what the owner pays and never taken from their money."""
    chosen, _ = op.settings_of(conn, session.pmc_id)
    if owner_payments.why_not(request, conn, session.pmc_id, chosen) is not None:
        raise HTTPException(409, op.OFF)
    partner = connected_partner(request, conn, session.pmc_id)
    if partner is None:
        raise HTTPException(409, OWN_KEYS)
    if body.method == "card" and body.amount > connect.CARD_MAX:
        raise HTTPException(422, CARD_TOO_MUCH)
    account = next(
        (
            found
            for found in owner_payments.accounts_of(conn, session.pmc_id, session.person_id)
            if found.account_id == body.account_id
        ),
        None,
    )
    if account is None:
        raise HTTPException(404, "no such property account of yours")
    payment = uuid4()
    platform_fee = fees()[body.method].on(body.amount)
    try:
        started = partner.connect_checkout(
            payment,
            [(f"Owner contribution – {account.property}", body.amount)],
            done_url("/pay/owner-done"),
            body.method,
            platform_fee,
            False,
        )
    except payments.PartnerFailed:
        raise HTTPException(502, op.PARTNER_DOWN) from None
    conn.execute(
        "INSERT INTO trust_owner_online_payments (id, pmc_id, ledger_account_id, amount, method,"
        " partner, partner_ref, platform_fee) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            payment,
            session.pmc_id,
            account.account_id,
            body.amount,
            body.method,
            partner.name,
            started.ref,
            platform_fee,
        ),
    )
    started_on_account(conn, session.pmc_id, owner_payment=payment)
    return op.Started(id=payment, url=started.url)


# --- what Stripe reports ----------------------------------------------------------------------


def company_of_account(conn: psycopg.Connection[Any], account: str) -> UUID | None:
    """The company that connected an account (an account is one company's), even since
    disconnected: payments started through it still report in."""
    row = conn.execute(
        "SELECT pmc_id FROM trust_connected_accounts WHERE account_id = %s LIMIT 1", (account,)
    ).fetchone()
    return None if row is None else UUID(str(row[0]))


def started_on(conn: psycopg.Connection[Any], pmc_id: UUID, account: str, about: UUID) -> bool:
    """Whether a payment, an owner's payment or an autopay of the company was started on the
    account: a report from another account never lands."""
    return (
        conn.execute(
            "SELECT 1 FROM trust_connected_payments WHERE pmc_id = %s AND account_id = %s"
            " AND %s IN (online_payment_id, owner_payment_id, autopay_id)",
            (pmc_id, account, about),
        ).fetchone()
        is not None
    )


def record_items(
    conn: psycopg.Connection[Any],
    partner: connect.Connected,
    pmc_id: UUID,
    items: list[connect.Item],
) -> bool:
    """Records what a connected account's report says, each part about the company's own: its
    state, an autopay's saved bank account or card, a payment's report, a dispute's."""
    from pms.api import disputes  # it reads online_payments too

    recorded = False
    for item in items:
        if isinstance(item, connect.AccountState):
            if item.account == partner.account:
                recorded = record_state(conn, pmc_id, item) or recorded
        elif isinstance(item, payments.Saved):
            if started_on(conn, pmc_id, partner.account, item.autopay_id):
                recorded = op.record_saved(conn, item, partner.name) or recorded
        elif isinstance(item, payments.Event):
            if started_on(conn, pmc_id, partner.account, item.payment_id):
                recorded = op.receive(conn, partner, item, pmc_id).recorded or recorded
        elif started_on(conn, pmc_id, partner.account, item.payment_id):
            recorded = disputes.record(conn, item) or recorded
    return recorded


@router.post("/webhook")
def webhook(
    request: Request, conn: Conn, body: Annotated[bytes, Depends(op.raw_body)]
) -> op.Received:
    """The platform's Connect webhook: Stripe's signed report about a connected account. Its
    account names the company; one no company connected is refused (404), so Stripe sends it
    again; a report from the other mode is acknowledged and never booked."""
    platform = platform_of(request)
    if not isinstance(platform, connect.StripePlatform):
        raise HTTPException(404, NOT_OFFERED)
    try:
        report = platform.report(body, request.headers)
    except payments.NotTheirs as wrong:
        raise HTTPException(400, str(wrong)) from None
    if report is None or report.account is None:
        return op.Received(recorded=False)
    pmc_id = company_of_account(conn, report.account)
    if pmc_id is None:
        raise HTTPException(404, "no company connected this Stripe account")
    partner = platform.bound(report.account, conn)
    try:
        items = partner.read(report)
    except payments.PartnerFailed:
        raise HTTPException(502, "the payments partner didn't answer: send it again") from None
    return op.Received(recorded=record_items(conn, partner, pmc_id, items))


# --- the fake platform's pages ----------------------------------------------------------------


def fake(request: Request) -> connect.FakePlatform:
    platform = platform_of(request)
    if not isinstance(platform, connect.FakePlatform):
        raise HTTPException(404, "no fake Connect platform on this server")
    return platform


class FakeAccount(BaseModel):
    company: str
    charges_enabled: bool


def fake_account(conn: psycopg.Connection[Any], account: str) -> tuple[UUID, str]:
    row = conn.execute(
        "SELECT c.pmc_id, p.display_name FROM trust_connected_accounts AS c"
        " JOIN trust_pmcs AS p ON p.pmc_id = c.pmc_id"
        " WHERE c.account_id = %s AND c.partner = 'fake_connect' LIMIT 1",
        (account,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such account at the fake platform")
    return UUID(str(row[0])), str(row[1])


FakeAccountId = Annotated[str, StringConstraints(pattern=r"^fake_acct_[A-Za-z0-9]{6,64}$")]


@router.get("/fake/accounts/{account}")
def fake_onboarding(request: Request, conn: Conn, account: FakeAccountId) -> FakeAccount:
    """The fake platform's onboarding page (development and tests only): whose account it is."""
    fake(request)
    pmc_id, company = fake_account(conn, account)
    found = conn.execute(connect.LATEST_STATE, (pmc_id, account)).fetchone()
    return FakeAccount(company=company, charges_enabled=found is not None and found[0])


class FakeOnboarded(BaseModel):
    # Finished: the account takes charges and pays out. Wanting: Stripe still needs something.
    outcome: Literal["finished", "wanting"]


@router.post("/fake/accounts/{account}", status_code=204)
def fake_onboarded(
    request: Request, conn: Conn, account: FakeAccountId, body: FakeOnboarded
) -> Response:
    """The fake platform's report of what the account is once onboarding ends, as Stripe's
    account.updated says it."""
    fake(request)
    pmc_id, _ = fake_account(conn, account)
    done = body.outcome == "finished"
    record_state(
        conn,
        pmc_id,
        connect.AccountState(
            account,
            done,
            done,
            True,
            () if done else ("external_account",),
            f"fake_{uuid4().hex}",
            datetime.now(UTC),
        ),
    )
    return Response(status_code=204)


class FakePayment(BaseModel):
    method: Method
    amount: str
    tenant_fee: str
    total: str
    # Occupella's fee on it, kept out of what reaches trust with Stripe's.
    platform_fee: str
    memo: str
    status: op.Status
    # The dispute on it, if any: opened, then won or lost.
    dispute: Literal["opened", "won", "lost"] | None
    done: str


Row = op.Row | owner_payments.Row


def fake_row(conn: psycopg.Connection[Any], payment_id: UUID) -> tuple[Row, str, Decimal]:
    """A payment at the fake platform, with its method and Occupella's fee."""
    row: Row | None = op.row_of(conn, payment_id) or owner_payments.row_of(conn, payment_id)
    if row is None or row.partner != "fake_connect":
        raise HTTPException(404, "no such payment at the fake platform")
    table = "trust_online_payments" if isinstance(row, op.Row) else "trust_owner_online_payments"
    found = conn.execute(
        f"SELECT method, platform_fee FROM {table} WHERE id = %s",  # noqa: S608
        (payment_id,),
    ).fetchone()
    assert found is not None
    return row, str(found[0]), Decimal(found[1])


def fake_status(conn: psycopg.Connection[Any], row: Row) -> tuple[op.Status, str | None]:
    if isinstance(row, op.Row):
        events, column = op.events_of(conn, row.id), "online_payment_id"
    else:
        events, column = owner_payments.events_of(conn, row.id), "owner_payment_id"
    from pms.api import disputes  # it reads online_payments too

    said = disputes.found(conn, column, row.id)  # type: ignore[arg-type]
    dispute = next((k for k in ("lost", "won", "opened") if k in said), None)
    return op.status_of(events), dispute


@router.get("/fake/payments/{payment_id}")
def fake_payment(request: Request, conn: Conn, payment_id: UUID) -> FakePayment:
    """The fake platform's payment page (development and tests only)."""
    fake(request)
    row, method, platform_fee = fake_row(conn, payment_id)
    status, dispute = fake_status(conn, row)
    if isinstance(row, op.Row):
        fee, memo, back = row.tenant_fee, rent.lease_memo(conn, row.lease_id), "/pay/done"
    else:
        fee, memo, back = ZERO, owner_payments.where(conn, row.account).memo, "/pay/owner-done"
    return FakePayment(
        method="card" if method == "card" else "ach",
        amount=rent.cents(row.amount),
        tenant_fee=rent.cents(fee),
        total=rent.cents(row.amount + fee),
        platform_fee=rent.cents(platform_fee),
        memo=memo,
        status=status,
        dispute=dispute,  # type: ignore[arg-type]
        done=back,
    )


class FakeOutcome(BaseModel):
    outcome: Literal["paid", "failed", "expired", "disputed", "won", "lost"]


@router.post("/fake/payments/{payment_id}")
def fake_report(request: Request, conn: Conn, payment_id: UUID, body: FakeOutcome) -> op.Received:
    """The fake platform's report, from its page's buttons: the bank or the card paid it (Stripe's
    fee and Occupella's kept out of it), it failed, the payer left, or a dispute opened (the
    payment came back and Stripe took its $15.00 fee), was won (both came back) or lost."""
    fake(request)
    row, method, platform_fee = fake_row(conn, payment_id)
    pmc_id = row.pmc_id
    account_row = conn.execute(
        "SELECT account_id FROM trust_connected_payments"
        " WHERE online_payment_id = %s OR owner_payment_id = %s",
        (payment_id, payment_id),
    ).fetchone()
    assert account_row is not None
    partner = connect.FakeConnected(str(account_row[0]), conn)
    now = datetime.now(UTC)
    said = f"fake_{uuid4().hex}"
    items: list[connect.Item]
    if body.outcome == "paid":
        total = row.amount + (row.tenant_fee if isinstance(row, op.Row) else ZERO)
        kept = connect.stripe_fee("card" if method == "card" else "ach", total) + platform_fee
        items = [payments.Event(row.id, "paid", said, now, kept)]
    elif body.outcome in ("failed", "expired"):
        items = [payments.Event(row.id, body.outcome, said, now)]
    elif body.outcome == "disputed":
        items = [
            payments.Event(row.id, "returned", said, now),
            connect.Dispute(
                row.id, f"fake_du_{row.id.hex}", "opened", connect.DISPUTE_FEE, said, now
            ),
        ]
    else:
        fee_back = connect.DISPUTE_FEE if body.outcome == "won" else None
        kind: connect.DisputeKind = "won" if body.outcome == "won" else "lost"
        items = [connect.Dispute(row.id, f"fake_du_{row.id.hex}", kind, fee_back, said, now)]
    return op.Received(recorded=record_items(conn, partner, pmc_id, items))


class FakeSave(BaseModel):
    tenant: str
    memo: str
    method: Method
    saved: bool


def fake_autopay(conn: psycopg.Connection[Any], autopay_id: UUID) -> Any:
    row = conn.execute(
        "SELECT a.lease_id, t.display_name, a.method, a.pmc_id,"
        " EXISTS (SELECT 1 FROM trust_autopay_accounts AS x WHERE x.autopay_id = a.id),"
        " c.account_id"
        " FROM trust_autopay AS a JOIN trust_tenants AS t ON t.id = a.tenant_id"
        " JOIN trust_connected_payments AS c ON c.autopay_id = a.id"
        " WHERE a.id = %s AND a.partner = 'fake_connect'",
        (autopay_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such autopay at the fake platform")
    return row


@router.get("/fake/autopay/{autopay_id}")
def fake_save_page(request: Request, conn: Conn, autopay_id: UUID) -> FakeSave:
    """The fake platform's page that saves a bank account or card for autopay."""
    fake(request)
    lease, name, method, _, saved, _ = fake_autopay(conn, autopay_id)
    return FakeSave(tenant=name, memo=rent.lease_memo(conn, lease), method=method, saved=saved)


@router.post("/fake/autopay/{autopay_id}", status_code=204)
def fake_save(request: Request, conn: Conn, autopay_id: UUID) -> Response:
    """The fake platform's report that the tenant saved a (made-up) bank account or card."""
    fake(request)
    _, _, method, pmc_id, _, account = fake_autopay(conn, autopay_id)
    saved = connect.fake_saved(autopay_id, "card" if method == "card" else "ach", datetime.now(UTC))
    record_items(conn, connect.FakeConnected(str(account), conn), pmc_id, [saved])
    return Response(status_code=204)
