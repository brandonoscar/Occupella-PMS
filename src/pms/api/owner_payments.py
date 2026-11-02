"""Owners pay in online (migration 20261102000150, ADR 0009; readiness q054): an owner adds money to
one of their property accounts from the owner portal, through the server's payments partner
(pms.payments), and the PMS records what the partner reports, as it does a tenant's rent paid
online (pms.api.online_payments).

- The company's switch for online payments (Company setup) lets tenants and owners pay online.
  The partner's fee on an owner's payment is always the company's (the founder, 2026-11-02): it
  leaves trust from the company's fee account in the same trust bank account, never from the
  owner's money and never added to what the owner pays (ADR 0009 rule 1).
- An owner pays what they choose into a property account of theirs; the form starts with what
  brings the account back up to its reserve. The partner's page takes their bank account (the PMS
  never sees it). Nothing is in the books until the partner says the bank paid: then it is the
  owner's contribution (trust_record_owner_contribution, under a key made from the payment), as
  staff record one. A payment that comes back is taken back out of the owner's account through
  its cash.
- What the books can't take when it is reported waits for staff on Banking with the tenants'
  online payments, and nothing is lost: a return the owner's account can't cover yet, and a
  partner's fee the company's fee account can't cover yet (the company deposits it from its own
  account).
- Each report is recorded once (`trust_owner_online_payment_events`, unique per payment and kind)
  and each posting under a key of its own, so a partner that reports twice moves nothing twice.

Only an owner starts a payment and only the partner's report (or the fake partner's page) says it
was paid; the AI never moves money.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, StringConstraints

from pms import payments
from pms.api import online_payments as online
from pms.api import outbox, rent
from pms.api.deps import Conn, Owner, refusal

router = APIRouter(prefix="/api/payments/owner")

ZERO = Decimal(0)
AccountId = Annotated[str, StringConstraints(min_length=1, max_length=100)]


# --- what a payment is now -------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    id: UUID
    pmc_id: UUID
    account: str
    amount: Decimal
    partner: str
    partner_ref: str
    created_at: datetime


COLUMNS = "p.id, p.pmc_id, p.ledger_account_id, p.amount, p.partner, p.partner_ref, p.created_at"


def row_of(conn: psycopg.Connection[Any], payment_id: UUID) -> Row | None:
    found = conn.execute(
        f"SELECT {COLUMNS} FROM trust_owner_online_payments AS p WHERE p.id = %s",  # noqa: S608
        (payment_id,),
    ).fetchone()
    return None if found is None else Row(*found)


def events_of(
    conn: psycopg.Connection[Any], payment_id: UUID
) -> dict[str, tuple[datetime, Decimal | None]]:
    return {
        kind: (happened, fee)
        for kind, happened, fee in conn.execute(
            "SELECT kind, happened_at, partner_fee FROM trust_owner_online_payment_events"
            " WHERE payment_id = %s",
            (payment_id,),
        ).fetchall()
    }


def lock(conn: psycopg.Connection[Any], payment_id: UUID) -> None:
    """The payment's lock until commit, the one its reports' trigger takes: recording it and a
    report about it wait for each other."""
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext('trust_owner_online_payment_events'),"
        " hashtext(%s::text))",
        (payment_id,),
    )


@dataclass(frozen=True)
class Where:
    """An owner's property account: its trust bank account, owner and property."""

    bank: UUID
    owner_id: UUID
    memo: str


def where(conn: psycopg.Connection[Any], account: str) -> Where:
    found = conn.execute(
        "SELECT a.bank_account_id, o.id, o.display_name || ' / ' || p.display_name"
        " FROM trust_ledger_accounts AS a"
        " JOIN trust_owners AS o ON o.id = a.owner_id"
        " JOIN trust_properties AS p ON p.id = a.property_id"
        " WHERE a.ledger_account_id = %s",
        (account,),
    ).fetchone()
    assert found is not None  # the payment's trigger checked it is an owner's property account
    return Where(*found)


# Keys of a payment's postings: the contribution, the partner's fee out of trust, and the return.
def contribution_key(payment: UUID) -> str:
    return f"owner-payment/{payment}"


def partner_fee_key(payment: UUID) -> str:
    return f"owner-partner-fee/{payment}"


def return_key(payment: UUID) -> str:
    return f"owner-payment-back/{payment}"


def contributed(conn: psycopg.Connection[Any], row: Row) -> bool:
    """Whether the payment is the owner's contribution in the books."""
    return (
        conn.execute(
            "SELECT 1 FROM trust_owner_contributions WHERE pmc_id = %s AND request_key = %s",
            (row.pmc_id, contribution_key(row.id)),
        ).fetchone()
        is not None
    )


def open_day(conn: psycopg.Connection[Any], bank: UUID, day: date) -> date:
    """The day, or the first day month-end left open in the trust bank account when it already
    closed that one, as for a tenant's payment online."""
    found = conn.execute(
        "SELECT (closed_through AT TIME ZONE 'UTC')::date FROM trust_bank_accounts WHERE id = %s",
        (bank,),
    ).fetchone()
    return day if found is None or found[0] is None else max(day, found[0])


# --- recording what the partner reports ------------------------------------------------------


PAID_MEMO = "Paid online by bank"


def record_contribution(conn: psycopg.Connection[Any], row: Row, day: date) -> None:
    conn.execute(
        "SELECT trust_record_owner_contribution(%s, %s, %s, %s, %s, %s)",
        (
            row.pmc_id,
            row.account,
            contribution_key(row.id),
            row.amount,
            rent.at(open_day(conn, where(conn, row.account).bank, day)),
            PAID_MEMO,
        ),
    )


def record_paid(conn: psycopg.Connection[Any], row: Row, day: date) -> bool:
    """The payment as the owner's contribution; False when the books refuse it for now, and it
    waits for staff."""
    return contributed(conn, row) or online.attempt(
        conn, lambda: record_contribution(conn, row, day)
    )


def from_company(conn: psycopg.Connection[Any], row: Row, fee: Decimal, day: date) -> bool:
    """The partner's fee out of trust from the company's fee account in the owner's trust bank
    account, once that account holds enough. False while it doesn't: it waits for the company."""
    key = partner_fee_key(row.id)
    if fee <= 0 or online.posted(conn, row.pmc_id, key):
        return True
    place = where(conn, row.account)
    company = online.company_account(conn, row.pmc_id, place.bank)
    if online.held(conn, company) < fee:
        return False
    return online.attempt(
        conn,
        lambda: rent.post(
            conn,
            row.pmc_id,
            key,
            [(company, rent.cash_of(conn, place.bank), fee)],
            open_day(conn, place.bank, day),
            {
                "key": key,
                "kind": "online payment cost",
                "memo": f"Payments partner's fee on an owner's payment – {place.memo}",
            },
        ),
    )


def record_return(conn: psycopg.Connection[Any], row: Row, day: date) -> bool:
    """A paid payment that came back, taken back out of the owner's account through its cash.
    False while the books refuse it: an owner's account short of what came back."""
    key = return_key(row.id)
    if online.posted(conn, row.pmc_id, key):
        return True
    if not contributed(conn, row):
        return False
    bank = where(conn, row.account).bank
    return online.attempt(
        conn,
        lambda: rent.post(
            conn,
            row.pmc_id,
            key,
            [(row.account, rent.cash_of(conn, bank), row.amount)],
            open_day(conn, bank, day),
            {
                "key": key,
                "kind": "owner payment returned",
                "memo": "Paid online by bank; came back",
            },
        ),
    )


def settle(conn: psycopg.Connection[Any], row: Row, on: date | None = None) -> None:
    """Whatever the payment's reports call for that isn't in the books yet, as far as the books
    take it now: the contribution in, the partner's fee out, a return taken back out. The
    company's part is dated `on` when staff cover it later, else the day of the report. Safe to
    run again: each posting has its own key."""
    lock(conn, row.id)
    events = events_of(conn, row.id)
    if "paid" not in events:
        return
    paid_at, fee = events["paid"]
    paid_on = online.day_of(paid_at)
    if not record_paid(conn, row, paid_on):
        return
    from_company(conn, row, fee or ZERO, on or paid_on)
    if "returned" in events:
        record_return(conn, row, online.day_of(events["returned"][0]))


def apply(conn: psycopg.Connection[Any], event: payments.Event, partner: str) -> bool:
    """Records the partner's report about an owner's payment it took (once per payment and kind)
    and settles what it calls for. False when the payment isn't an owner's this partner took."""
    row = row_of(conn, event.payment_id)
    if row is None or row.partner != partner:
        return False
    lock(conn, row.id)
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_owner_online_payment_events (pmc_id, payment_id, kind,"
                " partner_event, partner_fee, happened_at) VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (payment_id, kind) DO NOTHING",
                (row.pmc_id, row.id, event.kind, event.event_id, event.fee, event.happened_at),
            )
    except psycopg.errors.CheckViolation:
        return True  # out of order (paid after failed, say): kept as it first came
    settle(conn, row)
    return True


# --- staff: what waits for them ---------------------------------------------------------------


def waiting(conn: psycopg.Connection[Any], pmc_id: UUID) -> list[online.Waiting]:
    """Owners' payments online the books haven't taken all of yet, as Banking lists them with the
    tenants' (online.waiting, which adds what the company deposits to cover them)."""
    found: list[online.Waiting] = []
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM trust_owner_online_payments AS p"  # noqa: S608
        " WHERE p.pmc_id = %s AND EXISTS (SELECT 1 FROM trust_owner_online_payment_events AS e"
        "  WHERE e.payment_id = p.id AND e.kind = 'paid')"
        " ORDER BY p.created_at, p.id",
        (pmc_id,),
    ).fetchall()
    for values in rows:
        row = Row(*values)
        events = events_of(conn, row.id)
        fee = events["paid"][1]
        items: list[tuple[Literal["payment", "return", "partner fee"], Decimal | None]] = []
        if not contributed(conn, row):
            items.append(("payment", None))
        else:
            if fee and not online.posted(conn, pmc_id, partner_fee_key(row.id)):
                items.append(("partner fee", fee))
            if "returned" in events and not online.posted(conn, pmc_id, return_key(row.id)):
                items.append(("return", None))
        if not items:
            continue
        place = where(conn, row.account)
        holds = rent.cents(online.held(conn, online.company_account(conn, pmc_id, place.bank)))
        found += [
            online.Waiting(
                payment_id=row.id,
                lease_id=None,
                owner_id=place.owner_id,
                memo=place.memo,
                amount=rent.cents(row.amount),
                what=what,
                company_owes=None if owes is None else rent.cents(owes),
                bank_account_id=place.bank,
                company_holds=holds,
            )
            for what, owes in items
        ]
    return found


def record(conn: psycopg.Connection[Any], row: Row) -> None:
    """Staff record what waits for an owner's payment, once the books take it: the payment (a
    refusal reaches them in words), its return, its partner's fee."""
    events = events_of(conn, row.id)
    if "paid" not in events:
        raise HTTPException(409, "the payments partner hasn't said this payment was paid")
    lock(conn, row.id)
    if not contributed(conn, row):
        try:
            with conn.transaction():
                record_contribution(conn, row, online.day_of(events["paid"][0]))
        except psycopg.Error as error:
            raise HTTPException(409, refusal(error)) from None
    settle(conn, row, on=rent.today())


# --- the fake partner's page ------------------------------------------------------------------


def fake_checkout(conn: psycopg.Connection[Any], row: Row) -> online.FakeCheckout:
    return online.FakeCheckout(
        amount=rent.cents(row.amount),
        tenant_fee="0.00",
        total=rent.cents(row.amount),
        memo=f"Owner contribution – {where(conn, row.account).memo}",
        status=online.status_of(events_of(conn, row.id)),
    )


# --- the owner's side -------------------------------------------------------------------------


class OwnerAccount(BaseModel):
    account_id: str
    property: str
    bank: str
    balance: str
    reserve: str
    # What brings the account back up to its reserve: none when it holds that much.
    short: str


class OwnerPayment(BaseModel):
    id: UUID
    started_at: datetime
    property: str
    amount: str
    status: online.Status


class OwnerPayView(BaseModel):
    # Whether the owner may pay in online now, and if not, why.
    available: bool
    why_not: str | None
    accounts: list[OwnerAccount]
    payments: list[OwnerPayment]


class OwnerPay(BaseModel):
    account_id: AccountId
    amount: online.Amount


# The owner's property accounts of properties not archived: what each holds now and the reserve
# of the agreement in force today (0 with none).
ACCOUNTS = """
SELECT a.ledger_account_id, p.display_name, b.display_name, l.balance, coalesce(g.reserve, 0)
FROM trust_ledger_accounts AS a
JOIN trust_properties AS p ON p.id = a.property_id
JOIN trust_bank_accounts AS b ON b.id = a.bank_account_id
JOIN pgledger_accounts AS l ON l.id = a.ledger_account_id
LEFT JOIN LATERAL (
    SELECT g.reserve FROM trust_management_agreements AS g
    WHERE g.ledger_account_id = a.ledger_account_id AND g.starts_on <= %(day)s
    ORDER BY g.starts_on DESC LIMIT 1
) AS g ON true
WHERE a.pmc_id = %(pmc)s AND a.owner_id = %(owner)s AND a.kind = 'owner_property'
  AND NOT trust_is_archived(p.id, NULL)
ORDER BY p.display_name COLLATE "C", a.ledger_account_id
"""


def accounts_of(conn: psycopg.Connection[Any], pmc_id: UUID, owner_id: UUID) -> list[OwnerAccount]:
    return [
        OwnerAccount(
            account_id=account,
            property=prop,
            bank=bank,
            balance=rent.cents(Decimal(held)),
            reserve=rent.cents(Decimal(reserve)),
            short=rent.cents(max(Decimal(reserve) - Decimal(held), ZERO)),
        )
        for account, prop, bank, held, reserve in conn.execute(
            ACCOUNTS, {"pmc": pmc_id, "owner": owner_id, "day": rent.today()}
        ).fetchall()
    ]


def why_not(request: Request, chosen: online.PaymentSettings) -> str | None:
    if online.partner_of(request) is None:
        return "this server takes no payments online"
    if not chosen.online:
        return "your manager doesn't take payments online"
    return None


@router.get("")
def pay_view(session: Owner, conn: Conn, request: Request) -> OwnerPayView:
    chosen, _ = online.settings_of(conn, session.pmc_id)
    reason = why_not(request, chosen)
    rows = conn.execute(
        "SELECT p.id, p.created_at, pr.display_name, p.amount"
        " FROM trust_owner_online_payments AS p"
        " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = p.ledger_account_id"
        " JOIN trust_properties AS pr ON pr.id = a.property_id"
        " WHERE p.pmc_id = %s AND a.owner_id = %s ORDER BY p.created_at DESC, p.id",
        (session.pmc_id, session.person_id),
    ).fetchall()
    return OwnerPayView(
        available=reason is None,
        why_not=reason,
        accounts=accounts_of(conn, session.pmc_id, session.person_id),
        payments=[
            OwnerPayment(
                id=payment,
                started_at=started,
                property=prop,
                amount=rent.cents(amount),
                status=online.status_of(events_of(conn, payment)),
            )
            for payment, started, prop, amount in rows
        ],
    )


@router.post("/checkout", status_code=201)
def checkout(session: Owner, conn: Conn, request: Request, body: OwnerPay) -> online.Started:
    chosen, _ = online.settings_of(conn, session.pmc_id)
    reason = why_not(request, chosen)
    partner = online.partner_of(request)
    if reason is not None or partner is None:
        raise HTTPException(409, reason or "this server takes no payments online")
    account = next(
        (
            found
            for found in accounts_of(conn, session.pmc_id, session.person_id)
            if found.account_id == body.account_id
        ),
        None,
    )
    if account is None:
        raise HTTPException(404, "no such property account of yours")
    payment = uuid4()
    done = f"{outbox.PUBLIC_URL.get()}/pay/owner-done"
    try:
        started = partner.checkout(
            payment, [(f"Owner contribution – {account.property}", body.amount)], done
        )
    except payments.PartnerFailed:
        raise HTTPException(502, "the payments partner didn't answer: try again soon") from None
    conn.execute(
        "INSERT INTO trust_owner_online_payments (id, pmc_id, ledger_account_id, amount, method,"
        " partner, partner_ref) VALUES (%s, %s, %s, %s, 'ach', %s, %s)",
        (payment, session.pmc_id, account.account_id, body.amount, partner.name, started.ref),
    )
    return online.Started(id=payment, url=started.url)
