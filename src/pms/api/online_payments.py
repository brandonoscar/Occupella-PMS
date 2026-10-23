"""Rent paid online (migration 20261022000158; the founder, 2026-10-23): a tenant pays what their
lease owes by card or bank debit through the server's payment processor
(src/pms/payments/processor.py), on the processor's own page; once the processor says the money
came, the PMS records the tenant's payment as staff would (rent.record_payment): into the owners'
accounts in trust, rent first, the receipt emailed. A failure is kept with why; a payment the lease
no longer takes (a payment hold, a closed period) is refused and kept for staff to refund at the
processor; a bank debit that came back or a dispute lost bounces it (rent.bounce). The processor
bills its fees to the company; the tenant pays the amount and no fee.

Staff turn cards and bank debits on in Company setup (area rent) and see the latest online
payments there. Paying online is offered only while the server has a processor and the company
takes a method. Nothing here moves money but the payment it records, as staff would record it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from pms import notices
from pms.api import company, outbox, rent, tenant
from pms.api.deps import Conn, Staff, Tenant, refusal
from pms.payments import processor as processors
from pms.payments.processor import Event, Method

router = APIRouter()

Status = Literal["started", "succeeded", "failed", "refused", "returned"]
Amount = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2)]


class Settings(BaseModel):
    cards: bool
    bank_debits: bool
    recorded_by: str | None
    recorded_at: datetime | None


class NewSettings(BaseModel):
    cards: bool
    bank_debits: bool


class OnlinePayment(BaseModel):
    id: UUID
    lease_id: UUID
    home: str
    tenant: str
    amount: str
    method: Method
    started_at: datetime
    status: Status
    # Why it failed or was refused, in words.
    reason: str | None


class StaffView(BaseModel):
    # The server's processor, or None when it has none and paying online isn't offered.
    processor: Literal["fake", "stripe"] | None
    settings: Settings
    payments: list[OnlinePayment]


class TenantView(BaseModel):
    # What the tenant may pay by: what the company takes, while the server has a processor.
    methods: list[Method]
    payments: list[OnlinePayment]


class Start(BaseModel):
    amount: Amount
    method: Method


class Started(BaseModel):
    payment_id: UUID
    # Where the tenant goes to pay.
    url: str


class FakeOutcome(BaseModel):
    outcome: Literal["succeeded", "failed"]


def processor_of(request: Request) -> processors.Processor | None:
    found: processors.Processor | None = request.app.state.payments
    return found


def settings_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> Settings:
    row = conn.execute(
        "SELECT o.cards, o.bank_debits, s.display_name, o.created_at"
        " FROM trust_online_payment_settings AS o"
        " LEFT JOIN trust_staff AS s ON s.id = o.recorded_by"
        " WHERE o.pmc_id = %s ORDER BY o.created_at DESC, o.id DESC LIMIT 1",
        (pmc_id,),
    ).fetchone()
    if row is None:
        return Settings(cards=False, bank_debits=False, recorded_by=None, recorded_at=None)
    cards, banks, by, at = row
    return Settings(cards=cards, bank_debits=banks, recorded_by=by, recorded_at=at)


def methods_of(settings: Settings, processor: processors.Processor | None) -> list[Method]:
    if processor is None:
        return []
    taken: list[Method] = []
    if settings.cards:
        taken.append("card")
    if settings.bank_debits:
        taken.append("bank debit")
    return taken


# Each online payment with its latest step: where, who, how much, and how it stands.
PAYMENTS = """
SELECT p.id, p.lease_id, pr.display_name || ', ' || u.display_name, t.display_name, p.amount,
       p.method, p.created_at, s.step, s.reason
FROM trust_online_payments AS p
JOIN trust_leases AS l ON l.pmc_id = p.pmc_id AND l.id = p.lease_id
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS pr ON pr.id = u.property_id
JOIN trust_tenants AS t ON t.id = p.tenant_id
CROSS JOIN LATERAL (
    SELECT step, reason FROM trust_online_payment_steps AS x
    WHERE x.payment_id = p.id ORDER BY x.id DESC LIMIT 1
) AS s
WHERE p.pmc_id = %(pmc)s AND {where}
ORDER BY p.created_at DESC, p.id
LIMIT 100
"""


def payments_of(
    conn: psycopg.Connection[Any], pmc_id: UUID, where: str = "true", **params: Any
) -> list[OnlinePayment]:
    rows = conn.execute(PAYMENTS.format(where=where), {"pmc": pmc_id, **params})  # noqa: S608
    return [
        OnlinePayment(
            id=pid,
            lease_id=lease,
            home=home,
            tenant=name,
            amount=rent.cents(amount),
            method=method,
            started_at=at,
            status=step,
            reason=reason,
        )
        for pid, lease, home, name, amount, method, at, step, reason in rows.fetchall()
    ]


@router.get("/api/rent/online-payments")
def staff_view(session: Staff, conn: Conn, request: Request) -> StaffView:
    """Whether the company takes cards and bank debits online, and its latest online payments."""
    found = processor_of(request)
    return StaffView(
        processor=None if found is None else found.name,
        settings=settings_of(conn, session.pmc_id),
        payments=payments_of(conn, session.pmc_id),
    )


@router.post("/api/rent/online-payments/settings", status_code=201)
def keep_settings(session: Staff, conn: Conn, new: NewSettings) -> Settings:
    """Whether tenants may pay by card and by bank debit online, from now on."""
    conn.execute(
        "INSERT INTO trust_online_payment_settings (pmc_id, cards, bank_debits)"
        " VALUES (%s, %s, %s)",
        (session.pmc_id, new.cards, new.bank_debits),
    )
    return settings_of(conn, session.pmc_id)


@router.get("/api/tenant/online-payments")
def tenant_view(session: Tenant, conn: Conn, request: Request) -> TenantView:
    """What the tenant may pay by online, and their online payments."""
    return TenantView(
        methods=methods_of(settings_of(conn, session.pmc_id), processor_of(request)),
        payments=payments_of(
            conn,
            session.pmc_id,
            "p.tenant_id IN (SELECT trust_tenant_records(%(pmc)s, %(person)s))",
            person=session.person_id,
        ),
    )


@router.post("/api/tenant/leases/{lease_id}/online-payments", status_code=201)
def start(session: Tenant, conn: Conn, request: Request, lease_id: UUID, new: Start) -> Started:
    """Starts a payment of what the lease owes, or part of it; the tenant pays on the
    processor's page."""
    who = tenant.still_on(conn, session, lease_id)
    found = processor_of(request)
    if new.method not in methods_of(settings_of(conn, session.pmc_id), found):
        raise HTTPException(
            409, f"{company.name_of(conn, session.pmc_id)} takes no {new.method} online"
        )
    assert found is not None
    owed = rent.owed(conn, lease_id)
    if new.amount > owed:
        raise HTTPException(422, f"pay at most what the lease owes now, ${owed:,.2f}")
    held = conn.execute(
        rent.IN_FORCE, {"pmc": session.pmc_id, "lease": lease_id, "day": rent.today()}
    )
    if held.fetchone() is not None:
        raise HTTPException(409, "this lease takes no payment online now: call the office")
    payment_id = conn.execute(
        "INSERT INTO trust_online_payments (pmc_id, lease_id, tenant_id, amount, method, processor)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (session.pmc_id, lease_id, who, new.amount, new.method, found.name),
    ).fetchone()
    assert payment_id is not None
    checkout = found.start(
        str(payment_id[0]),
        new.amount,
        new.method,
        f"Rent – {rent.lease_memo(conn, lease_id)}",
        f"{outbox.PUBLIC_URL.get()}/",
    )
    conn.execute(
        "INSERT INTO trust_online_payment_steps (pmc_id, payment_id, step, reference)"
        " VALUES (%s, %s, 'started', %s)",
        (session.pmc_id, payment_id[0], checkout.reference),
    )
    return Started(payment_id=payment_id[0], url=checkout.url)


def take(conn: psycopg.Connection[Any], processor: str, event: Event) -> bool:
    """Takes a processor's event once: records the payment that succeeded, keeps one that failed
    or that the lease refused, bounces one that came back. False when it was taken before or names
    no payment of this server's."""
    if conn.execute(
        "SELECT 1 FROM trust_online_payment_steps WHERE processor_event = %s", (event.id,)
    ).fetchone():
        return False
    if event.outcome == "returned":
        row = conn.execute(
            "SELECT p.pmc_id, p.id, s.transfer_id FROM trust_online_payment_steps AS s"
            " JOIN trust_online_payments AS p ON p.id = s.payment_id"
            " WHERE s.step = 'succeeded' AND s.reference = %s AND p.processor = %s",
            (event.processor_payment, processor),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT p.pmc_id, p.id, NULL FROM trust_online_payment_steps AS s"
            " JOIN trust_online_payments AS p ON p.id = s.payment_id"
            " WHERE s.step = 'started' AND s.reference = %s AND p.processor = %s",
            (event.reference, processor),
        ).fetchone()
    if row is None:
        return False
    pmc_id, payment_id, paid_transfer = row
    if conn.execute(
        "SELECT 1 FROM trust_online_payment_steps WHERE payment_id = %s AND step = %s",
        (payment_id, "returned" if event.outcome == "returned" else "succeeded"),
    ).fetchone() or (
        event.outcome != "returned"
        and conn.execute(
            "SELECT 1 FROM trust_online_payment_steps WHERE payment_id = %s"
            " AND step IN ('failed', 'refused')",
            (payment_id,),
        ).fetchone()
    ):
        return False
    today = datetime.now(UTC).date()
    if event.outcome == "failed":
        step(
            conn,
            pmc_id,
            payment_id,
            "failed",
            event.id,
            reason=event.reason or "it didn't go through",
        )
    elif event.outcome == "returned":
        returned = rent.bounce(conn, pmc_id, paid_transfer, today)
        step(conn, pmc_id, payment_id, "returned", event.id, transfer=returned.transfers[0])
    else:
        record(conn, pmc_id, payment_id, event, today)
    return True


def record(
    conn: psycopg.Connection[Any], pmc_id: UUID, payment_id: UUID, event: Event, today: date
) -> None:
    found = conn.execute(
        "SELECT lease_id, amount, method FROM trust_online_payments WHERE id = %s", (payment_id,)
    ).fetchone()
    assert found is not None  # a step's payment exists
    lease_id, amount, method = found
    try:
        with conn.transaction():
            recorded = rent.record_payment(
                conn,
                pmc_id,
                lease_id,
                uuid.UUID(str(payment_id)),
                Decimal(amount),
                today,
                reference=event.processor_payment,
                method=method,
            )
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        step(
            conn,
            pmc_id,
            payment_id,
            "refused",
            event.id,
            reason=f"{refusal(refused)}: refund it at the payment processor",
        )
        return
    step(
        conn,
        pmc_id,
        payment_id,
        "succeeded",
        event.id,
        transfer=recorded.transfers[0],
        reference=event.processor_payment or event.reference,
    )
    if outbox.SENDING.get():
        notices.write_payment_receipts(
            conn, pmc_id, lease_id, recorded.transfers[0], Decimal(amount), today
        )


def step(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    payment_id: UUID,
    kind: str,
    event_id: str,
    transfer: str | None = None,
    reference: str | None = None,
    reason: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO trust_online_payment_steps (pmc_id, payment_id, step, reference,"
        " processor_event, transfer_id, reason) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (pmc_id, payment_id, kind, reference, event_id, transfer, reason),
    )


@router.post("/api/payments/stripe/webhook")
async def stripe_webhook(request: Request, conn: Conn) -> dict[str, int]:
    """Stripe's events about the payments it took, signed with this endpoint's secret."""
    found = processor_of(request)
    if found is None or found.name != "stripe":
        raise HTTPException(404, "this server takes no Stripe payments")
    body = await request.body()
    try:
        events = found.events(body, {k.lower(): v for k, v in request.headers.items()})
    except processors.BadSignature as bad:
        raise HTTPException(400, str(bad)) from None
    return {"taken": sum(take(conn, "stripe", event) for event in events)}


@router.post("/api/tenant/online-payments/fake/{reference}", status_code=204)
def fake_checkout(
    session: Tenant, conn: Conn, request: Request, reference: str, said: FakeOutcome
) -> None:
    """The fake processor's checkout: the tenant who started the payment says what happened, as
    a processor's event would. Only on a server whose processor is the fake one."""
    found = processor_of(request)
    if found is None or found.name != "fake":
        raise HTTPException(404, "this server's payment processor is not the fake one")
    mine = conn.execute(
        "SELECT p.id FROM trust_online_payment_steps AS s"
        " JOIN trust_online_payments AS p ON p.id = s.payment_id"
        " WHERE s.step = 'started' AND s.reference = %s AND p.processor = 'fake' AND p.pmc_id = %s"
        " AND p.tenant_id IN (SELECT trust_tenant_records(%s, %s))",
        (reference, session.pmc_id, session.pmc_id, session.person_id),
    ).fetchone()
    if mine is None:
        raise HTTPException(404, "no such payment of yours")
    event = Event(
        id=f"fake-event-{reference}",
        outcome=said.outcome,
        reference=reference,
        processor_payment=f"fake-payment-{reference}" if said.outcome == "succeeded" else None,
        reason=None if said.outcome == "succeeded" else "you said it didn't go through",
    )
    if not take(conn, "fake", event):
        raise HTTPException(409, "this payment is settled already")
