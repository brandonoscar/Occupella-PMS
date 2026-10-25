"""Rent paid online (migration 0150, ADR 0009): a tenant pays from the portal through the server's
payments partner (pms.payments), and the PMS records what the partner reports.

- A company turns online payments on in Company setup and chooses who pays the partner's fee on a
  bank payment (the founder, 2026-10-23): the company, from its fees held in trust, or the tenant,
  a fee shown before they pay (`trust_payment_settings`).
- A tenant on a lease pays what they choose from the lease's page; the partner's page takes their
  bank account (the PMS never sees it). Nothing is recorded in the books until the partner says the
  bank paid: then the payment is recorded as staff record one (rent.record_payment, its request id
  the online payment's), the tenant's fee goes to the company's fee account in the same trust bank
  account, and the partner's fee, which the partner kept out of what it paid into trust, leaves
  trust from that account. A payment that comes back is recorded as a bounce (rent.bounce, with the
  property's NSF fee) and the tenant's fee goes back out of the company's account.
- What can't be recorded when it is reported waits for staff, and nothing is lost: a payment
  that came in while the lease was on a payment hold (staff accept it past the hold), a return
  the owner's account can't cover yet, and a partner's fee (or a tenant's fee that came back)
  the company's fee account can't cover yet: the company deposits it from its own account.
  Banking lists them.
- Each report is recorded once (`trust_online_payment_events`, unique per payment and kind) and each
  posting under a key of its own, so a partner that reports twice moves nothing twice.

The money itself never passes through the PMS, and the AI never moves it: only a tenant starts a
payment, and only the partner's report (or the fake partner's page) says it was paid.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from pms import payments
from pms.api import outbox, rent, tenant
from pms.api.deps import Conn, Staff, Tenant, refusal

router = APIRouter(prefix="/api/payments")

ZERO = Decimal(0)
Amount = Annotated[Decimal, Field(gt=0, le=Decimal("100000"), decimal_places=2)]
FeePayer = Literal["company", "tenant"]
Status = Literal["started", "paid", "failed", "returned", "expired"]


def partner_of(request: Request) -> payments.Partner | None:
    found: payments.Partner | None = request.app.state.payments
    return found


# --- a company's choices ---------------------------------------------------------------------


class PaymentSettings(BaseModel):
    online: bool
    ach_fee_payer: FeePayer


class SettingsView(PaymentSettings):
    # The server's payments partner: None when this server takes no payments online.
    partner: str | None
    changed_at: datetime | None


def settings_of(
    conn: psycopg.Connection[Any], pmc_id: UUID
) -> tuple[PaymentSettings, datetime | None]:
    """The company's latest choices, or the defaults (off; the company pays the fee)."""
    row = conn.execute(
        "SELECT online, ach_fee_payer, created_at FROM trust_payment_settings"
        " WHERE pmc_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
        (pmc_id,),
    ).fetchone()
    if row is None:
        return PaymentSettings(online=False, ach_fee_payer="company"), None
    return PaymentSettings(online=row[0], ach_fee_payer=row[1]), row[2]


@router.get("/settings")
def get_settings(session: Staff, conn: Conn, request: Request) -> SettingsView:
    chosen, changed = settings_of(conn, session.pmc_id)
    partner = partner_of(request)
    return SettingsView(
        **chosen.model_dump(), partner=None if partner is None else partner.name, changed_at=changed
    )


@router.post("/settings", status_code=204)
def change_settings(session: Staff, conn: Conn, body: PaymentSettings) -> None:
    current, _ = settings_of(conn, session.pmc_id)
    if current == body:
        return  # the same choices again write nothing
    conn.execute(
        "INSERT INTO trust_payment_settings (pmc_id, online, ach_fee_payer) VALUES (%s, %s, %s)",
        (session.pmc_id, body.online, body.ach_fee_payer),
    )


# --- what a payment is now -------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    id: UUID
    pmc_id: UUID
    lease_id: UUID
    tenant_id: UUID
    amount: Decimal
    tenant_fee: Decimal
    fee_payer: str
    partner: str
    partner_ref: str
    created_at: datetime


COLUMNS = (
    "p.id, p.pmc_id, p.lease_id, p.tenant_id, p.amount, p.tenant_fee, p.fee_payer, p.partner,"
    " p.partner_ref, p.created_at"
)


def row_of(conn: psycopg.Connection[Any], payment_id: UUID) -> Row | None:
    found = conn.execute(
        f"SELECT {COLUMNS} FROM trust_online_payments AS p WHERE p.id = %s",  # noqa: S608
        (payment_id,),
    ).fetchone()
    return None if found is None else Row(*found)


def events_of(
    conn: psycopg.Connection[Any], payment_id: UUID
) -> dict[str, tuple[datetime, Decimal | None]]:
    return {
        kind: (happened, fee)
        for kind, happened, fee in conn.execute(
            "SELECT kind, happened_at, partner_fee FROM trust_online_payment_events"
            " WHERE payment_id = %s",
            (payment_id,),
        ).fetchall()
    }


def status_of(events: dict[str, Any]) -> Status:
    for kind in ("returned", "failed", "paid", "expired"):
        if kind in events:
            return kind
    return "started"


# The transfers posted under a key, first to last (each carries its key in its metadata).
POSTED = """
SELECT t.id FROM pgledger_transfers AS t
JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id
WHERE a.pmc_id = %s AND t.metadata ->> 'key' = %s
ORDER BY t.created_at, t.id
"""


def posted(conn: psycopg.Connection[Any], pmc_id: UUID, key: str) -> bool:
    """Whether a posting under `key` is in the books."""
    return conn.execute(POSTED, (pmc_id, key)).fetchone() is not None


# Keys of a payment's postings: the payment (rent.record_payment's), the tenant's fee into the
# company's account and back out, the partner's fee out of trust, and the bounce (rent.bounce's).
def payment_key(payment: UUID) -> str:
    return f"payment/{payment}"


def tenant_fee_key(payment: UUID) -> str:
    return f"online-fee/{payment}"


def tenant_fee_back_key(payment: UUID) -> str:
    return f"online-fee-back/{payment}"


def partner_fee_key(payment: UUID) -> str:
    return f"partner-fee/{payment}"


def bounce_key(payment: UUID) -> str:
    return f"bounce/{payment_key(payment)}"


# --- the tenant's side ------------------------------------------------------------------------


class OnlinePayment(BaseModel):
    id: UUID
    started_at: datetime
    amount: str
    tenant_fee: str
    status: Status


class PayView(BaseModel):
    # Whether the tenant may pay this lease online now, and if not, why.
    available: bool
    why_not: str | None
    owed: str
    fee_payer: FeePayer
    payments: list[OnlinePayment]


class FeeQuote(BaseModel):
    amount: str
    tenant_fee: str
    total: str


class Pay(BaseModel):
    amount: Amount


class Started(BaseModel):
    id: UUID
    # Where the tenant pays: the partner's page.
    url: str


def why_not(
    conn: psycopg.Connection[Any],
    request: Request,
    pmc_id: UUID,
    lease_id: UUID,
    chosen: PaymentSettings,
) -> str | None:
    """Why this lease takes no payment online now, or None when it does."""
    if partner_of(request) is None:
        return "this server takes no payments online"
    if not chosen.online:
        return "your manager doesn't take payments online"
    day = rent.today()
    if conn.execute(rent.IN_FORCE, {"pmc": pmc_id, "lease": lease_id, "day": day}).fetchone():
        return "this lease takes no payment online now: ask your manager"
    return None


def online_payments(conn: psycopg.Connection[Any], lease_id: UUID) -> list[OnlinePayment]:
    rows = conn.execute(
        "SELECT p.id, p.created_at, p.amount, p.tenant_fee FROM trust_online_payments AS p"
        " WHERE p.lease_id = %s ORDER BY p.created_at DESC, p.id",
        (lease_id,),
    ).fetchall()
    return [
        OnlinePayment(
            id=payment,
            started_at=started,
            amount=rent.cents(amount),
            tenant_fee=rent.cents(fee),
            status=status_of(events_of(conn, payment)),
        )
        for payment, started, amount, fee in rows
    ]


@router.get("/leases/{lease_id}")
def pay_view(session: Tenant, conn: Conn, request: Request, lease_id: UUID) -> PayView:
    tenant.lease_of(conn, session, lease_id)
    chosen, _ = settings_of(conn, session.pmc_id)
    return PayView(
        available=(reason := why_not(conn, request, session.pmc_id, lease_id, chosen)) is None,
        why_not=reason,
        owed=rent.cents(max(rent.owed(conn, lease_id), ZERO)),
        fee_payer=chosen.ach_fee_payer,
        payments=online_payments(conn, lease_id),
    )


def fee_for(chosen: PaymentSettings, amount: Decimal) -> Decimal:
    return payments.tenant_fee(amount) if chosen.ach_fee_payer == "tenant" else ZERO


@router.get("/leases/{lease_id}/fee")
def fee_quote(
    session: Tenant,
    conn: Conn,
    lease_id: UUID,
    amount: Annotated[Decimal, Query(gt=0, le=Decimal("100000"), decimal_places=2)],
) -> FeeQuote:
    tenant.lease_of(conn, session, lease_id)
    chosen, _ = settings_of(conn, session.pmc_id)
    fee = fee_for(chosen, amount)
    return FeeQuote(
        amount=rent.cents(amount), tenant_fee=rent.cents(fee), total=rent.cents(amount + fee)
    )


@router.post("/leases/{lease_id}/checkout", status_code=201)
def checkout(session: Tenant, conn: Conn, request: Request, lease_id: UUID, body: Pay) -> Started:
    payer = tenant.still_on(conn, session, lease_id)
    chosen, _ = settings_of(conn, session.pmc_id)
    reason = why_not(conn, request, session.pmc_id, lease_id, chosen)
    partner = partner_of(request)
    if reason is not None or partner is None:
        raise HTTPException(409, reason or "this server takes no payments online")
    fee = fee_for(chosen, body.amount)
    payment = uuid4()
    memo = rent.lease_memo(conn, lease_id)
    lines = [(f"Rent – {memo}", body.amount)]
    if fee > 0:
        lines.append(("Processing fee", fee))
    done = f"{outbox.PUBLIC_URL.get()}/pay/done"
    try:
        started = partner.checkout(payment, lines, done)
    except payments.PartnerFailed:
        raise HTTPException(502, "the payments partner didn't answer: try again soon") from None
    conn.execute(
        "INSERT INTO trust_online_payments (id, pmc_id, lease_id, tenant_id, amount, tenant_fee,"
        " fee_payer, method, partner, partner_ref)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, 'ach', %s, %s)",
        (
            payment,
            session.pmc_id,
            lease_id,
            payer,
            body.amount,
            fee,
            chosen.ach_fee_payer,
            partner.name,
            started.ref,
        ),
    )
    return Started(id=payment, url=started.url)


# --- what the partner reports -----------------------------------------------------------------


def day_of(moment: datetime) -> date:
    return moment.astimezone(UTC).date()


def bank_of(conn: psycopg.Connection[Any], row: Row, day: date) -> UUID:
    """The trust bank account the lease's rent goes to: the property's owners' (rent.owners_of)."""
    lease = rent.lease_of(conn, row.pmc_id, row.lease_id)
    return rent.owners_of(conn, row.pmc_id, lease.property_id, day, None).bank


def open_day(conn: psycopg.Connection[Any], row: Row, day: date) -> date:
    """The day, or the first day month-end left open in the lease's trust bank account when it
    already closed that one: what the bank did then and the books take only now (a payment that
    waited for a hold, a return that waited for an owner's money) goes into the next open period,
    as a late correction does."""
    found = conn.execute(
        "SELECT (closed_through AT TIME ZONE 'UTC')::date FROM trust_bank_accounts WHERE id = %s",
        (bank_of(conn, row, day),),
    ).fetchone()
    return day if found is None or found[0] is None else max(day, found[0])


def company_account(conn: psycopg.Connection[Any], pmc_id: UUID, bank: UUID) -> str:
    found = conn.execute(
        "SELECT ledger_account_id FROM trust_ledger_accounts"
        " WHERE pmc_id = %s AND bank_account_id = %s AND kind = 'pmc_income'",
        (pmc_id, bank),
    ).fetchone()
    if found is None:
        raise HTTPException(409, "the trust bank account has no fee account of the company")
    return str(found[0])


def held(conn: psycopg.Connection[Any], account: str) -> Decimal:
    found = conn.execute(
        "SELECT balance FROM pgledger_accounts WHERE id = %s", (account,)
    ).fetchone()
    return ZERO if found is None else Decimal(found[0])


# How the books refuse what they can't take now: the database's refusals, and the rent work's.
REFUSED = (
    psycopg.errors.CheckViolation,
    psycopg.errors.InvalidParameterValue,
    psycopg.errors.RaiseException,
    HTTPException,
)


def attempt(conn: psycopg.Connection[Any], step: Any) -> bool:
    """Runs a step of recording in a savepoint: True when it went into the books, False when the
    books refused it for now (it waits for staff), as a refusal from the database or the API."""
    try:
        with conn.transaction():
            step()
        return True
    except REFUSED:
        return False


def record_payment(
    conn: psycopg.Connection[Any], row: Row, day: date, past_hold_by: str | None = None
) -> None:
    """The payment in the books as staff record one, and the tenant's fee into the company's
    account in the same trust bank account. A lease on a payment hold that day takes it only past
    the hold, whatever it would pay: the books refuse one matched to its charges, and one that
    would all be prepaid rent waits too, since no one chose to take it. A day month-end already
    closed is recorded on the first day it left open."""
    day = open_day(conn, row, day)
    if (
        past_hold_by is None
        and conn.execute(
            rent.IN_FORCE, {"pmc": row.pmc_id, "lease": row.lease_id, "day": day}
        ).fetchone()
    ):
        raise HTTPException(409, "the lease is on a payment hold: staff accept it past the hold")
    rent.record_payment(
        conn,
        row.pmc_id,
        row.lease_id,
        row.id,
        row.amount,
        day,
        reference=row.partner_ref,
        method="online",
        past_hold_by=past_hold_by,
    )
    if row.tenant_fee > 0:
        bank = bank_of(conn, row, day)
        rent.post(
            conn,
            row.pmc_id,
            tenant_fee_key(row.id),
            [(rent.cash_of(conn, bank), company_account(conn, row.pmc_id, bank), row.tenant_fee)],
            day,
            {
                "key": tenant_fee_key(row.id),
                "kind": "online payment fee",
                "memo": "Processing fee paid by the tenant – "
                + rent.lease_memo(conn, row.lease_id),
            },
        )


def record_paid(conn: psycopg.Connection[Any], row: Row, day: date) -> bool:
    """record_payment, both postings or neither; False when the books refuse it for now (a
    payment hold, say), and it waits for staff."""
    return posted(conn, row.pmc_id, payment_key(row.id)) or attempt(
        conn, lambda: record_payment(conn, row, day)
    )


def from_company(
    conn: psycopg.Connection[Any], row: Row, key: str, amount: Decimal, day: date, memo: str
) -> bool:
    """`amount` out of trust from the company's fee account in the lease's trust bank account,
    under `key`, once that account holds enough: the partner's fee it kept, or a tenant's fee
    that came back. False while it doesn't: it waits for the company to cover it."""
    if amount <= 0 or posted(conn, row.pmc_id, key):
        return True
    bank = bank_of(conn, row, day)
    company = company_account(conn, row.pmc_id, bank)
    if held(conn, company) < amount:
        return False
    return attempt(
        conn,
        lambda: rent.post(
            conn,
            row.pmc_id,
            key,
            [(company, rent.cash_of(conn, bank), amount)],
            day,
            {
                "key": key,
                "kind": "online payment cost",
                "memo": f"{memo} – {rent.lease_memo(conn, row.lease_id)}",
            },
        ),
    )


def record_return(conn: psycopg.Connection[Any], row: Row, day: date) -> bool:
    """A paid payment that came back, undone as a bounce (with the property's NSF fee). False
    while the books refuse it: an owner's account short of what came back."""
    if posted(conn, row.pmc_id, bounce_key(row.id)):
        return True
    if not posted(conn, row.pmc_id, payment_key(row.id)):
        return False

    def step() -> None:
        first = conn.execute(POSTED, (row.pmc_id, payment_key(row.id))).fetchone()
        assert first is not None
        rent.bounce(conn, row.pmc_id, first[0], open_day(conn, row, day))

    return attempt(conn, step)


def settle(conn: psycopg.Connection[Any], row: Row, on: date | None = None) -> None:
    """Whatever the payment's reports call for that isn't in the books yet, as far as the books
    take it now: the payment and the tenant's fee in, the partner's fee out, a return undone and
    the tenant's fee back out. The company's part is dated `on` when staff cover it later, else
    the day of the report. Safe to run again: each posting has its own key."""
    events = events_of(conn, row.id)
    if "paid" not in events:
        return
    paid_at, fee = events["paid"]
    paid_on = day_of(paid_at)
    if not record_paid(conn, row, paid_on):
        return
    from_company(
        conn, row, partner_fee_key(row.id), fee or ZERO, on or paid_on, "Payments partner's fee"
    )
    if "returned" in events:
        returned_on = day_of(events["returned"][0])
        if record_return(conn, row, returned_on) and posted(
            conn, row.pmc_id, tenant_fee_key(row.id)
        ):
            from_company(
                conn,
                row,
                tenant_fee_back_key(row.id),
                row.tenant_fee,
                on or returned_on,
                "Processing fee that came back",
            )


def apply(conn: psycopg.Connection[Any], event: payments.Event, partner: str) -> bool:
    """Records the partner's report about a payment it took (once per payment and kind) and
    settles what it calls for. False when the payment isn't one this partner took for the PMS."""
    row = row_of(conn, event.payment_id)
    if row is None or row.partner != partner:
        return False
    rent.lock_lease(conn, row.lease_id)
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_online_payment_events (pmc_id, payment_id, kind, partner_event,"
                " partner_fee, happened_at) VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (payment_id, kind) DO NOTHING",
                (row.pmc_id, row.id, event.kind, event.event_id, event.fee, event.happened_at),
            )
    except psycopg.errors.CheckViolation:
        return True  # out of order (paid after failed, say): kept as it first came
    settle(conn, row)
    return True


def record_saved(conn: psycopg.Connection[Any], saved: payments.Saved, partner: str) -> bool:
    """Records the bank account the partner saved for an autopay (migration 0151), once. False
    when the autopay isn't one this partner was asked to save an account for."""
    row = conn.execute(
        "SELECT pmc_id, partner FROM trust_autopay WHERE id = %s", (saved.autopay_id,)
    ).fetchone()
    if row is None or row[1] != partner:
        return False
    conn.execute(
        "INSERT INTO trust_autopay_accounts (pmc_id, autopay_id, partner_customer, partner_method,"
        " partner_mandate, label, partner_event, saved_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (autopay_id) DO NOTHING",
        (
            row[0],
            saved.autopay_id,
            saved.account.customer,
            saved.account.method,
            saved.account.mandate,
            saved.label,
            saved.event_id,
            saved.happened_at,
        ),
    )
    return True


class Received(BaseModel):
    recorded: bool


async def raw_body(request: Request) -> bytes:
    """The request's body as sent: the partner signs these bytes."""
    return await request.body()


@router.post("/webhook/{name}")
def webhook(
    request: Request, conn: Conn, name: str, body: Annotated[bytes, Depends(raw_body)]
) -> Received:
    """The partner's report of what the bank did (Stripe's webhook). It carries the partner's
    signature, not a session; a report about a payment the PMS didn't start is acknowledged and
    ignored, so the partner stops sending it."""
    partner = partner_of(request)
    if partner is None or partner.name != name or name == "fake":
        raise HTTPException(404, "no such payments partner on this server")
    try:
        event = partner.event(body, request.headers)
    except payments.NotTheirs as wrong:
        raise HTTPException(400, str(wrong)) from None
    except payments.PartnerFailed:
        raise HTTPException(502, "the payments partner didn't answer: send it again") from None
    if event is None:
        return Received(recorded=False)
    if isinstance(event, payments.Saved):
        return Received(recorded=record_saved(conn, event, partner.name))
    return Received(recorded=apply(conn, event, partner.name))


# --- the fake partner's page ------------------------------------------------------------------


class FakeCheckout(BaseModel):
    amount: str
    tenant_fee: str
    total: str
    memo: str
    status: Status


class FakeOutcome(BaseModel):
    outcome: Literal["paid", "failed", "returned", "expired"]


def fake_row(request: Request, conn: psycopg.Connection[Any], payment_id: UUID) -> Row:
    partner = partner_of(request)
    row = row_of(conn, payment_id)
    if partner is None or partner.name != "fake" or row is None or row.partner != "fake":
        raise HTTPException(404, "no such payment at the fake partner")
    return row


@router.get("/fake/{payment_id}")
def fake_checkout(request: Request, conn: Conn, payment_id: UUID) -> FakeCheckout:
    """The fake partner's page (development and tests only): what the tenant is paying."""
    row = fake_row(request, conn, payment_id)
    return FakeCheckout(
        amount=rent.cents(row.amount),
        tenant_fee=rent.cents(row.tenant_fee),
        total=rent.cents(row.amount + row.tenant_fee),
        memo=rent.lease_memo(conn, row.lease_id),
        status=status_of(events_of(conn, row.id)),
    )


@router.post("/fake/{payment_id}")
def fake_report(request: Request, conn: Conn, payment_id: UUID, body: FakeOutcome) -> Received:
    """The fake partner's report, from its page's buttons: the bank paid it, it failed, it came
    back, or the tenant left without paying. A paid payment's fee is what Stripe charges for a bank
    payment."""
    row = fake_row(request, conn, payment_id)
    fee = payments.ach_fee(row.amount + row.tenant_fee) if body.outcome == "paid" else None
    event = payments.Event(row.id, body.outcome, f"fake_{uuid4().hex}", datetime.now(UTC), fee)
    return Received(recorded=apply(conn, event, "fake"))


# --- staff: what waits for them ---------------------------------------------------------------


class Waiting(BaseModel):
    payment_id: UUID
    lease_id: UUID
    memo: str
    amount: str
    # What waits: the payment itself (a payment hold, or the books refused it), its return (an
    # owner's account short), or what the company's fee account can't cover yet: the partner's
    # fee, or a tenant's fee that came back.
    what: Literal["payment", "return", "partner fee", "fee refund"]
    # What the company covers for it, if anything.
    company_owes: str | None
    bank_account_id: UUID
    company_holds: str
    # What the company deposits from its own account to cover all that waits in this trust bank
    # account: what it owes there less what its fee account holds.
    cover_needed: str = "0.00"


def waiting(conn: psycopg.Connection[Any], pmc_id: UUID) -> list[Waiting]:
    found: list[Waiting] = []
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM trust_online_payments AS p"  # noqa: S608
        " WHERE p.pmc_id = %s AND EXISTS (SELECT 1 FROM trust_online_payment_events AS e"
        "  WHERE e.payment_id = p.id AND e.kind = 'paid')"
        " ORDER BY p.created_at, p.id",
        (pmc_id,),
    ).fetchall()
    for values in rows:
        row = Row(*values)
        events = events_of(conn, row.id)
        paid_at, fee = events["paid"]
        items: list[
            tuple[Literal["payment", "return", "partner fee", "fee refund"], Decimal | None]
        ]
        if not posted(conn, pmc_id, payment_key(row.id)):
            items = [("payment", None)]
        else:
            items = []
            if fee and not posted(conn, pmc_id, partner_fee_key(row.id)):
                items.append(("partner fee", fee))
            if "returned" in events and not posted(conn, pmc_id, bounce_key(row.id)):
                items.append(("return", None))
            elif (
                "returned" in events
                and posted(conn, pmc_id, tenant_fee_key(row.id))
                and not posted(conn, pmc_id, tenant_fee_back_key(row.id))
            ):
                items.append(("fee refund", row.tenant_fee))
        if not items:
            continue
        bank = bank_of(conn, row, day_of(paid_at))
        holds = rent.cents(held(conn, company_account(conn, pmc_id, bank)))
        memo = rent.lease_memo(conn, row.lease_id)
        found += [
            Waiting(
                payment_id=row.id,
                lease_id=row.lease_id,
                memo=memo,
                amount=rent.cents(row.amount),
                what=what,
                company_owes=None if owes is None else rent.cents(owes),
                bank_account_id=bank,
                company_holds=holds,
            )
            for what, owes in items
        ]
    for bank in {item.bank_account_id for item in found}:
        here = [item for item in found if item.bank_account_id == bank]
        owed = sum((Decimal(i.company_owes) for i in here if i.company_owes is not None), ZERO)
        needed = rent.cents(max(owed - Decimal(here[0].company_holds), ZERO))
        for item in here:
            item.cover_needed = needed
    return found


@router.get("/waiting")
def waiting_list(session: Staff, conn: Conn) -> list[Waiting]:
    return waiting(conn, session.pmc_id)


class Record(BaseModel):
    # Accept a payment that came in while the lease was on a payment hold.
    past_hold: bool = False


@router.post("/{payment_id}/record", status_code=204)
def record(session: Staff, conn: Conn, payment_id: UUID, body: Record) -> None:
    """Staff record what waits for a payment, once the books take it: the payment (past a hold if
    they accept it), its return, its partner's fee."""
    row = row_of(conn, payment_id)
    if row is None or row.pmc_id != session.pmc_id:
        raise HTTPException(404, "no such online payment")
    events = events_of(conn, row.id)
    if "paid" not in events:
        raise HTTPException(409, "the payments partner hasn't said this payment was paid")
    rent.lock_lease(conn, row.lease_id)
    day = day_of(events["paid"][0])
    if not posted(conn, row.pmc_id, payment_key(row.id)):
        past_hold_by = str(session.person_id) if body.past_hold else None
        try:
            with conn.transaction():
                record_payment(conn, row, day, past_hold_by)
        except psycopg.Error as error:
            raise HTTPException(409, refusal(error)) from None
    settle(conn, row, on=rent.today())


class Cover(BaseModel):
    bank_account_id: UUID
    amount: Amount
    request_id: UUID


class Covered(BaseModel):
    # What still waits for the company in that trust bank account after the deposit.
    still_waiting: int


@router.post("/partner-fees/cover")
def cover(session: Staff, conn: Conn, body: Cover) -> Covered:
    """The company's own money, deposited into a trust bank account to pay what its fee account
    there can't (the partner's fees, tenants' fees that came back): into that account, then every
    payment waiting on it there settled, oldest first. Never more than they need, so the company's
    money doesn't sit in trust; once per request id."""
    company = company_account(conn, session.pmc_id, body.bank_account_id)
    key = f"cover/{body.request_id}"

    def owed_here() -> list[Waiting]:
        return [
            item
            for item in waiting(conn, session.pmc_id)
            if item.bank_account_id == body.bank_account_id and item.company_owes is not None
        ]

    if not posted(conn, session.pmc_id, key):
        needed = max(
            sum((Decimal(str(item.company_owes)) for item in owed_here()), ZERO)
            - held(conn, company),
            ZERO,
        )
        if body.amount > needed:
            raise HTTPException(
                422, f"what waits here needs {rent.cents(needed)} from the company, no more"
            )
    done = rent.post(
        conn,
        session.pmc_id,
        key,
        [(rent.cash_of(conn, body.bank_account_id), company, body.amount)],
        rent.today(),
        {
            "key": key,
            "kind": "company cover",
            "memo": "The company's deposit to pay online payments' costs",
        },
    )
    if done is None:
        raise HTTPException(409, "this request id was used for something else")
    for payment in dict.fromkeys(item.payment_id for item in owed_here()):
        row = row_of(conn, payment)
        assert row is not None
        rent.lock_lease(conn, row.lease_id)
        settle(conn, row, on=rent.today())
    return Covered(still_waiting=len(owed_here()))
