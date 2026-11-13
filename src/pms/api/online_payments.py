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
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from pms import days, notices, payments
from pms.api import outbox, rent, tenant
from pms.api.company import how_to_pay
from pms.api.deps import Conn, Staff, Tenant, refusal

router = APIRouter(prefix="/api/payments")

ZERO = Decimal(0)
# How long a payment started online may go with no word from the partner before staff see it
# waiting (docs/edge-cases.md P-013) and a balance autopay stops counting it as on its way
# (src/pms/autopay.py, P-067, P-068, H-145): a bank payment reports how it went in a few business
# days and a page left unpaid expires in one (NOT VERIFIED against Stripe from here).
NO_WORD_DAYS = 14
Amount = Annotated[Decimal, Field(gt=0, le=Decimal("100000"), decimal_places=2)]
FeePayer = Literal["company", "tenant"]
Status = Literal["started", "paid", "failed", "returned", "expired"]


def resolver_of(request: Request) -> payments.Resolver:
    found: payments.Resolver = request.app.state.payments
    return found


def partner_of(
    request: Request, conn: psycopg.Connection[Any], pmc_id: UUID
) -> payments.Partner | None:
    """The partner a company's payments go through: its own Stripe account once an admin entered
    its keys (src/pms/api/payment_keys.py), else the server's."""
    return resolver_of(request).for_pmc(conn, pmc_id)


def server_partner(request: Request) -> payments.Partner | None:
    """The server's own partner (PMS_PAYMENTS): the fake partner's pages and the server's webhook
    are its."""
    return resolver_of(request).server


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
    partner = partner_of(request, conn, session.pmc_id)
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


# Why a tenant or owner can't pay online now: the company (or its server) takes no payment
# online, or the lease is on a payment hold. The portal says it in its own words.
WhyNot = Literal["off", "paused"]
OFF = "Online payments aren't available."
PARTNER_DOWN = "We couldn't reach our payments partner. Try again in a few minutes."
PAUSED = "Online payments are paused for this lease. Contact your property manager."
SAID: dict[str, str] = {"off": OFF, "paused": PAUSED}


class PayView(BaseModel):
    # Whether the tenant may pay this lease online now, and if not, why.
    available: bool
    why_not: WhyNot | None
    # The company's own words on how to pay it, shown when the tenant can't pay online.
    how_to_pay: str | None
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
) -> WhyNot | None:
    """Why this lease takes no payment online now, or None when it does."""
    if partner_of(request, conn, pmc_id) is None or not chosen.online:
        return "off"
    day = rent.today(conn, pmc_id)
    if conn.execute(rent.IN_FORCE, {"pmc": pmc_id, "lease": lease_id, "day": day}).fetchone():
        return "paused"
    if not placed(conn, pmc_id, lease_id, day):
        return "off"
    return None


def placed(conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID, day: date) -> bool:
    """Whether the books know whose the lease's payment is on the day: false at a property with
    several owner accounts and no ownership shares, where staff choose (docs/edge-cases.md P-040,
    P-162), so neither Pay online nor autopay starts one."""
    try:
        rent.owners_of(conn, pmc_id, rent.lease_of(conn, pmc_id, lease_id).property_id, day, None)
    except HTTPException:
        return False
    return True


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
        how_to_pay=how_to_pay(conn, session.pmc_id),
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
    partner = partner_of(request, conn, session.pmc_id)
    if reason is not None or partner is None:
        raise HTTPException(409, SAID[reason] if reason else OFF)
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
        raise HTTPException(502, PARTNER_DOWN) from None
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


def day_of(conn: psycopg.Connection[Any], pmc_id: UUID, moment: datetime) -> date:
    """The company's day at a moment the partner reported (src/pms/days.py): rent paid at 9 pm
    Eastern on October 31 is October's."""
    return days.day_at(conn, pmc_id, moment)


class OwnerChoice(BaseModel):
    account_id: str
    owner: str


def owner_choices(conn: psycopg.Connection[Any], row: Row, day: date) -> list[OwnerChoice]:
    """The owner accounts staff choose among for the payment: those of the lease's property when
    it has more than one and no ownership shares on the day, else none (docs/edge-cases.md
    P-040: a payment started before a second owner account came)."""
    lease = rent.lease_of(conn, row.pmc_id, row.lease_id)
    if conn.execute(rent.OWNERSHIP, (lease.property_id, day)).fetchone():
        return []
    found = [
        OwnerChoice(account_id=account, owner=owner)
        for account, owner in conn.execute(
            "SELECT a.ledger_account_id, o.display_name FROM trust_ledger_accounts AS a"
            " JOIN trust_owners AS o ON o.id = a.owner_id"
            " WHERE a.pmc_id = %s AND a.kind = 'owner_property' AND a.property_id = %s"
            ' ORDER BY o.display_name COLLATE "C", a.ledger_account_id',
            (row.pmc_id, lease.property_id),
        )
    ]
    return found if len(found) > 1 else []


# The trust bank account a payment already in the books went into (its postings carry their key,
# as POSTED reads them).
PAID_INTO = """
SELECT a.bank_account_id FROM pgledger_transfers AS t
JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id
WHERE a.pmc_id = %s AND t.metadata ->> 'key' = %s
ORDER BY t.created_at, t.id
LIMIT 1
"""


def bank_of(conn: psycopg.Connection[Any], row: Row, day: date, account: str | None = None) -> UUID:
    """The trust bank account the lease's rent goes to: the owner's account staff chose, or the
    one the payment went into once in the books, or the property's owners' (rent.owners_of), or,
    while staff are still to choose its owner's account, the one all of them are in."""
    if account is not None:
        chosen = conn.execute(
            "SELECT bank_account_id FROM trust_ledger_accounts"
            " WHERE pmc_id = %s AND ledger_account_id = %s AND kind = 'owner_property'",
            (row.pmc_id, account),
        ).fetchone()
        if chosen is None:
            raise HTTPException(404, "no such owner account for this lease's property")
        return UUID(str(chosen[0]))
    paid_into = conn.execute(PAID_INTO, (row.pmc_id, payment_key(row.id))).fetchone()
    if paid_into is not None:
        return UUID(str(paid_into[0]))
    lease = rent.lease_of(conn, row.pmc_id, row.lease_id)
    try:
        return rent.owners_of(conn, row.pmc_id, lease.property_id, day, None).bank
    except HTTPException:
        banks = conn.execute(
            "SELECT DISTINCT bank_account_id FROM trust_ledger_accounts"
            " WHERE pmc_id = %s AND kind = 'owner_property' AND property_id = %s",
            (row.pmc_id, lease.property_id),
        ).fetchall()
        if len(banks) != 1:
            raise
        found: UUID = banks[0][0]
        return found


def open_day(
    conn: psycopg.Connection[Any], row: Row, day: date, account: str | None = None
) -> date:
    """The day, or the first day month-end left open in the lease's trust bank account when it
    already closed that one: what the bank did then and the books take only now (a payment that
    waited for a hold, a return that waited for an owner's money) goes into the next open period,
    as a late correction does."""
    found = conn.execute(
        "SELECT (closed_through AT TIME ZONE 'UTC')::date FROM trust_bank_accounts WHERE id = %s",
        (bank_of(conn, row, day, account),),
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
    conn: psycopg.Connection[Any],
    row: Row,
    day: date,
    past_hold_by: str | None = None,
    account: str | None = None,
) -> None:
    """The payment in the books as staff record one, and the tenant's fee into the company's
    account in the same trust bank account. A lease on a payment hold that day takes it only past
    the hold, whatever it would pay: the books refuse one matched to its charges, and one that
    would all be prepaid rent waits too, since no one chose to take it. A day month-end already
    closed is recorded on the first day it left open."""
    day = open_day(conn, row, day, account)
    if (
        past_hold_by is None
        and conn.execute(
            rent.IN_FORCE, {"pmc": row.pmc_id, "lease": row.lease_id, "day": day}
        ).fetchone()
    ):
        raise HTTPException(409, "the lease is on a payment hold: staff accept it past the hold")
    recorded = rent.record_payment(
        conn,
        row.pmc_id,
        row.lease_id,
        row.id,
        row.amount,
        day,
        reference=row.partner_ref,
        method="online",
        past_hold_by=past_hold_by,
        account=account,
    )
    # Every payment's receipt, online and autopay ones too (ADR 0010), once per payment.
    if outbox.SENDING.get():
        notices.write_payment_receipts(
            conn, row.pmc_id, row.lease_id, recorded.transfers[0], row.amount, day
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
    paid_on = day_of(conn, row.pmc_id, paid_at)
    if not record_paid(conn, row, paid_on):
        return
    from_company(
        conn, row, partner_fee_key(row.id), fee or ZERO, on or paid_on, "Payments partner's fee"
    )
    if "returned" in events:
        returned_on = day_of(conn, row.pmc_id, events["returned"][0])
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
    from pms.api import disputes  # a card dispute through Connect, after its return (ADR 0014)

    disputes.settle_tenant(conn, row, on)


# A return reported before its payment's paid report: refused for now (409), so the partner sends
# it again once paid is in (Stripe retries a webhook for days), never acknowledged and lost, which
# left the books holding money the bank took back (docs/edge-cases.md C-087, C-088, P-024).
SEND_AGAIN = (
    "this payment isn't paid in the books yet, so it can't come back: send this again later"
)


def refuse_too_soon(
    conn: psycopg.Connection[Any], events: str, payment_id: UUID, kind: str
) -> None:
    """Raise 409 for a return out of order that a paid report may still come before: neither
    paid nor failed is recorded. A return after a failure is acknowledged, as no paid will come."""
    if kind != "returned":
        return
    reached = conn.execute(
        f"SELECT EXISTS (SELECT 1 FROM {events} WHERE payment_id = %s"  # noqa: S608
        " AND kind IN ('paid', 'failed'))",
        (payment_id,),
    ).fetchone()
    if reached is not None and not reached[0]:
        raise HTTPException(409, SEND_AGAIN)


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
        refuse_too_soon(conn, "trust_online_payment_events", row.id, event.kind)
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


def read(
    partner: payments.Partner, body: bytes, headers: Any
) -> payments.Event | payments.Saved | None:
    """A partner's signed report, read; refused when its signature isn't the partner's."""
    try:
        return partner.event(body, headers)
    except payments.NotTheirs as wrong:
        raise HTTPException(400, str(wrong)) from None
    except payments.PartnerFailed:
        raise HTTPException(502, "the payments partner didn't answer: send it again") from None


def receive(
    conn: psycopg.Connection[Any],
    partner: payments.Partner,
    event: payments.Event | payments.Saved | None,
    pmc_id: UUID | None = None,
) -> Received:
    """Records a partner's report; with `pmc_id` (a company's own webhook), only a report about
    that company's payment or autopay."""
    if event is None:
        return Received(recorded=False)
    if pmc_id is not None and company_of(conn, event) != pmc_id:
        return Received(recorded=False)  # another company's, or none of ours: acknowledged
    if isinstance(event, payments.Saved):
        return Received(recorded=record_saved(conn, event, partner.name))
    from pms.api import owner_payments  # it reads this module's helpers; owners pay in online too

    return Received(
        recorded=apply(conn, event, partner.name) or owner_payments.apply(conn, event, partner.name)
    )


def company_of(
    conn: psycopg.Connection[Any], event: payments.Event | payments.Saved
) -> UUID | None:
    """The company whose payment (a tenant's or an owner's) or autopay a report is about."""
    from pms.api import owner_payments  # it reads this module's helpers; owners pay in online too

    if isinstance(event, payments.Saved):
        found = conn.execute(
            "SELECT pmc_id FROM trust_autopay WHERE id = %s", (event.autopay_id,)
        ).fetchone()
        return None if found is None else found[0]
    row = row_of(conn, event.payment_id) or owner_payments.row_of(conn, event.payment_id)
    return None if row is None else row.pmc_id


@router.post("/webhook/{name}")
def webhook(
    request: Request, conn: Conn, name: str, body: Annotated[bytes, Depends(raw_body)]
) -> Received:
    """The server's partner's report of what the bank did (Stripe's webhook, under the server's
    keys). It carries the partner's signature, not a session; a report about a payment the PMS
    didn't start is acknowledged and ignored, so the partner stops sending it."""
    partner = server_partner(request)
    if partner is None or partner.name != name or name == "fake":
        raise HTTPException(404, "no such payments partner on this server")
    return receive(conn, partner, read(partner, body, request.headers))


@router.post("/webhook/{name}/{pmc_id}")
def company_webhook(
    request: Request,
    conn: Conn,
    name: str,
    pmc_id: UUID,
    body: Annotated[bytes, Depends(raw_body)],
) -> Received:
    """A company's own Stripe account's report (its webhook, signed with its own secret): it
    records only that company's payments. A report signed with a secret the company used before
    (keys since changed or taken away) still lands: a bank payment reports for days."""
    keys = [p for p in resolver_of(request).every_own(conn, pmc_id) if p.name == name]
    if not keys:
        raise HTTPException(404, "no such payments partner for this company")
    for partner in keys:
        try:
            event = partner.event(body, request.headers)
        except payments.NotTheirs:
            continue
        except payments.PartnerFailed:
            raise HTTPException(502, "the payments partner didn't answer: send it again") from None
        return receive(conn, partner, event, pmc_id)
    raise HTTPException(400, "the report's signature isn't this company's")


# --- the fake partner's page ------------------------------------------------------------------


class FakeCheckout(BaseModel):
    amount: str
    tenant_fee: str
    total: str
    memo: str
    status: Status
    # Where the payer goes back to, as the partner's success page: a tenant's or an owner's.
    done: str = "/pay/done"


class FakeOutcome(BaseModel):
    outcome: Literal["paid", "failed", "returned", "expired"]


def fake_row(request: Request, conn: psycopg.Connection[Any], payment_id: UUID) -> Any:
    """A tenant's or an owner's payment at the fake partner (owner_payments.Row for an owner's)."""
    from pms.api import owner_payments  # it reads this module's helpers; owners pay in online too

    partner = server_partner(request)
    row = row_of(conn, payment_id) or owner_payments.row_of(conn, payment_id)
    if partner is None or partner.name != "fake" or row is None or row.partner != "fake":
        raise HTTPException(404, "no such payment at the fake partner")
    return row


@router.get("/fake/{payment_id}")
def fake_checkout(request: Request, conn: Conn, payment_id: UUID) -> FakeCheckout:
    """The fake partner's page (development and tests only): what the tenant or owner is paying."""
    from pms.api import owner_payments  # it reads this module's helpers; owners pay in online too

    row = fake_row(request, conn, payment_id)
    if isinstance(row, owner_payments.Row):
        return owner_payments.fake_checkout(conn, row)
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
    from pms.api import owner_payments  # it reads this module's helpers; owners pay in online too

    row = fake_row(request, conn, payment_id)
    owners = isinstance(row, owner_payments.Row)
    total = row.amount if owners else row.amount + row.tenant_fee
    fee = payments.ach_fee(total) if body.outcome == "paid" else None
    event = payments.Event(row.id, body.outcome, f"fake_{uuid4().hex}", datetime.now(UTC), fee)
    if owners:
        return Received(recorded=owner_payments.apply(conn, event, "fake"))
    return Received(recorded=apply(conn, event, "fake"))


# --- staff: what waits for them ---------------------------------------------------------------


class Waiting(BaseModel):
    payment_id: UUID
    # A tenant's payment's lease, or the owner of an owner's payment (owner_payments).
    lease_id: UUID | None
    owner_id: UUID | None = None
    memo: str
    amount: str
    # What waits: the payment itself (a payment hold, or the books refused it), its return (an
    # owner's account short), what the company's fee account can't cover yet (the partner's fee,
    # or a tenant's fee that came back), or a payment the partner has said nothing of for
    # NO_WORD_DAYS (P-013): staff look it up there; its report, when it comes, brings it in.
    # Through Connect (src/pms/api/disputes.py): a card dispute's fee the company's fee account
    # can't cover yet, or a dispute won whose payment the books refuse for now.
    what: Literal[
        "payment", "return", "partner fee", "fee refund", "no word", "dispute fee", "dispute won"
    ]
    # The day a payment with no word was started (the company's day).
    started_on: date | None = None
    # What the company covers for it, if anything.
    company_owes: str | None
    bank_account_id: UUID
    company_holds: str
    # What the company deposits from its own account to cover all that waits in this trust bank
    # account: what it owes there less what its fee account holds.
    cover_needed: str = "0.00"
    # The owner accounts staff choose among to record the payment (P-040), or none.
    choose_account: list[OwnerChoice] = []


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
        paid_on = day_of(conn, pmc_id, paid_at)
        choices = owner_choices(conn, row, paid_on) if items == [("payment", None)] else []
        # Waiting for staff to choose whose it is: listed in the first choice's trust bank account
        # (its owners' accounts may sit in more than one, P-163).
        bank = bank_of(conn, row, paid_on, choices[0].account_id if choices else None)
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
                choose_account=choices,
            )
            for what, owes in items
        ]
    found += no_word(conn, pmc_id)
    from pms.api import owner_payments  # it reads this module's helpers; owners pay in online too

    found += owner_payments.waiting(conn, pmc_id)
    from pms.api import disputes  # card disputes through Connect (ADR 0014)

    found += disputes.waiting(conn, pmc_id)
    for bank in {item.bank_account_id for item in found}:
        here = [item for item in found if item.bank_account_id == bank]
        owed = sum((Decimal(i.company_owes) for i in here if i.company_owes is not None), ZERO)
        needed = rent.cents(max(owed - Decimal(here[0].company_holds), ZERO))
        for item in here:
            item.cover_needed = needed
    return found


# A tenant's payment started online that the partner has said nothing of for NO_WORD_DAYS, and that
# isn't in the books (docs/edge-cases.md P-013).
NO_WORD = (
    f"SELECT {COLUMNS} FROM trust_online_payments AS p"  # noqa: S608 (constant text)
    """
WHERE p.pmc_id = %s AND p.created_at < %s
  AND NOT EXISTS (SELECT 1 FROM trust_online_payment_events AS e WHERE e.payment_id = p.id)
  AND NOT EXISTS (SELECT 1 FROM pgledger_transfers AS t
                  WHERE t.metadata ->> 'key' = 'payment/' || p.id::text)
ORDER BY p.created_at, p.id
"""
)


def no_word(conn: psycopg.Connection[Any], pmc_id: UUID) -> list[Waiting]:
    """The payments started online that the partner has said nothing of for NO_WORD_DAYS: its
    webhook broken, or the PMS down past its retries. Staff look each up at the partner; nothing
    here records one."""
    since = datetime.now(UTC) - timedelta(days=NO_WORD_DAYS)
    found = []
    for values in conn.execute(NO_WORD, (pmc_id, since)).fetchall():
        row = Row(*values)
        started_on = day_of(conn, pmc_id, row.created_at)
        bank = bank_of(conn, row, started_on)
        found.append(
            Waiting(
                payment_id=row.id,
                lease_id=row.lease_id,
                memo=rent.lease_memo(conn, row.lease_id),
                amount=rent.cents(row.amount),
                what="no word",
                company_owes=None,
                bank_account_id=bank,
                company_holds=rent.cents(held(conn, company_account(conn, pmc_id, bank))),
                started_on=started_on,
            )
        )
    return found


@router.get("/waiting")
def waiting_list(session: Staff, conn: Conn) -> list[Waiting]:
    return waiting(conn, session.pmc_id)


class Record(BaseModel):
    # Accept a payment that came in while the lease was on a payment hold.
    past_hold: bool = False
    # The owner's account it goes to, when the lease's property has several and no shares.
    account: Annotated[str | None, Field(max_length=40)] = None


@router.post("/{payment_id}/record", status_code=204)
def record(session: Staff, conn: Conn, payment_id: UUID, body: Record) -> None:
    """Staff record what waits for a payment, once the books take it: the payment (past a hold if
    they accept it), its return, its partner's fee."""
    from pms.api import owner_payments  # it reads this module's helpers; owners pay in online too

    row = row_of(conn, payment_id)
    if row is None:
        owners = owner_payments.row_of(conn, payment_id)
        if owners is None or owners.pmc_id != session.pmc_id:
            raise HTTPException(404, "no such online payment")
        owner_payments.record(conn, owners)
        return
    if row.pmc_id != session.pmc_id:
        raise HTTPException(404, "no such online payment")
    events = events_of(conn, row.id)
    if "paid" not in events:
        raise HTTPException(409, "the payments partner hasn't said this payment was paid")
    rent.lock_lease(conn, row.lease_id)
    day = day_of(conn, row.pmc_id, events["paid"][0])
    if not posted(conn, row.pmc_id, payment_key(row.id)):
        past_hold_by = str(session.person_id) if body.past_hold else None
        try:
            with conn.transaction():
                record_payment(conn, row, day, past_hold_by, body.account)
        except psycopg.Error as error:
            raise HTTPException(409, refusal(error)) from None
    settle(conn, row, on=rent.today(conn, session.pmc_id))


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
    money doesn't sit in trust; once per request id. Two at once (two staff, each page with its
    own request id) wait for each other on the trust bank account, so the second reads what the
    first left waiting (docs/edge-cases.md C-093)."""
    company = company_account(conn, session.pmc_id, body.bank_account_id)
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('company cover in ' || %s::uuid, 0))",
        (body.bank_account_id,),
    )
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
        rent.today(conn, session.pmc_id),
        {
            "key": key,
            "kind": "company cover",
            "memo": "The company's deposit to pay online payments' costs",
        },
    )
    if done is None:
        raise HTTPException(409, "this request id was used for something else")
    from pms.api import owner_payments  # it reads this module's helpers; owners pay in online too

    for payment in dict.fromkeys(item.payment_id for item in owed_here()):
        row = row_of(conn, payment)
        if row is None:
            owners = owner_payments.row_of(conn, payment)
            assert owners is not None
            owner_payments.settle(conn, owners, on=rent.today(conn, session.pmc_id))
            continue
        rent.lock_lease(conn, row.lease_id)
        settle(conn, row, on=rent.today(conn, session.pmc_id))
    return Covered(still_waiting=len(owed_here()))
