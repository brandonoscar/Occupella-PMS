"""Autopay in the portal and for staff (migration 0151, src/pms/autopay.py).

A tenant sets up autopay of a lease they are on: a fixed amount each month, or the balance due up
to an amount they set, from a bank account they save on the payments partner's page (the PMS never
sees its number). It pays on the lease's rent due day, from the next one after the account is
saved. They turn it off whenever they like; staff turn it off from the lease's page, naming who.
Setting up a new one stops the one before. The fake partner's page saves a made-up account
(development and tests only).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from pms import autopay, payments
from pms.api import online_payments as op
from pms.api import outbox, rent, tenant
from pms.api.deps import Conn, Staff, Tenant

router = APIRouter(prefix="/api/payments")

Rule = Literal["fixed", "balance"]
Amount = Annotated[Decimal, Field(gt=0, le=Decimal("100000"), decimal_places=2)]
RunStatus = Literal["started", "paid", "failed", "returned", "expired", "skipped"]


class AutopayRun(BaseModel):
    due_on: date
    amount: str | None
    tenant_fee: str | None
    # What the partner reported of its payment, or "skipped" with why.
    status: RunStatus
    skipped: str | None


class Autopay(BaseModel):
    id: UUID
    tenant: str
    rule: Rule
    amount: str
    # The bank account it pays from, once saved ("Test bank ••••0000"); None while it waits for it.
    account: str | None
    started_at: datetime
    stopped_at: datetime | None
    # Why it stopped: by the tenant, by staff, or by itself (the lease ended, ...).
    stopped: str | None
    runs: list[AutopayRun]


RUNS = """
SELECT r.due_on, p.amount, p.tenant_fee, r.skipped,
       (SELECT array_agg(e.kind::text) FROM trust_online_payment_events AS e
        WHERE e.payment_id = r.payment_id)
FROM trust_autopay_runs AS r
LEFT JOIN trust_online_payments AS p ON p.id = r.payment_id
WHERE r.autopay_id = %s
ORDER BY r.due_on DESC
LIMIT 24
"""

AUTOPAYS = """
SELECT a.id, t.display_name, a.rule, a.amount, acct.label, a.created_at, s.created_at, s.why
FROM trust_autopay AS a
JOIN trust_tenants AS t ON t.id = a.tenant_id
LEFT JOIN trust_autopay_accounts AS acct ON acct.autopay_id = a.id
LEFT JOIN trust_autopay_stops AS s ON s.autopay_id = a.id
WHERE a.pmc_id = %(pmc)s AND a.lease_id = %(lease)s
  AND (%(tenant)s::uuid IS NULL OR a.tenant_id = %(tenant)s)
ORDER BY a.created_at DESC, a.id
"""


def runs_of(conn: psycopg.Connection[Any], autopay_id: UUID) -> list[AutopayRun]:
    found = []
    for due_on, amount, fee, skipped, kinds in conn.execute(RUNS, (autopay_id,)).fetchall():
        status: RunStatus = "skipped" if skipped else op.status_of(dict.fromkeys(kinds or []))
        found.append(
            AutopayRun(
                due_on=due_on,
                amount=None if amount is None else rent.cents(amount),
                tenant_fee=None if fee is None else rent.cents(fee),
                status=status,
                skipped=skipped,
            )
        )
    return found


def autopays(
    conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID, tenant_id: UUID | None = None
) -> list[Autopay]:
    """A lease's autopays (of one of its tenants, or all), newest first."""
    rows = conn.execute(AUTOPAYS, {"pmc": pmc_id, "lease": lease_id, "tenant": tenant_id})
    return [
        Autopay(
            id=row[0],
            tenant=row[1],
            rule=row[2],
            amount=rent.cents(row[3]),
            account=row[4],
            started_at=row[5],
            stopped_at=row[6],
            stopped=row[7],
            runs=runs_of(conn, row[0]),
        )
        for row in rows.fetchall()
    ]


def submit(pool: ConnectionPool[Any], partner: payments.Partner) -> None:
    """Sends the autopay payments started and not yet accepted to the partner (after a request
    that may have started some committed: an admin's Run rent now)."""
    with pool.connection() as conn:
        conn.autocommit = True
        try:
            autopay.submit_waiting(conn, partner)
        finally:
            conn.autocommit = False


# --- the tenant's side ------------------------------------------------------------------------


class AutopayView(BaseModel):
    # Whether the tenant may set up autopay of this lease now, and if not, why.
    available: bool
    why_not: str | None
    fee_payer: op.FeePayer
    # The lease's next rent due date, from tomorrow; None once the lease has no more.
    next_due: date | None
    # Theirs, newest first: the one on (or waiting for its account), then the ones stopped.
    autopays: list[Autopay]


class SetUp(BaseModel):
    rule: Rule
    amount: Amount


class Started(BaseModel):
    id: UUID
    # The partner's page, where the tenant saves their bank account.
    url: str


def next_due(conn: psycopg.Connection[Any], lease_id: UUID, day: date) -> date | None:
    """The lease's first rent due date after the day, this month or next."""
    for month in (day, (day.replace(day=1) + timedelta(days=32)).replace(day=1)):
        row = conn.execute("SELECT trust_rent_due_on(%s, %s)", (lease_id, month)).fetchone()
        if row is not None and row[0] is not None and row[0] > day:
            return date.fromisoformat(str(row[0]))
    return None


@router.get("/leases/{lease_id}/autopay")
def autopay_view(session: Tenant, conn: Conn, request: Request, lease_id: UUID) -> AutopayView:
    mine = tenant.lease_of(conn, session, lease_id)
    chosen, _ = op.settings_of(conn, session.pmc_id)
    reason = op.why_not(conn, request, session.pmc_id, lease_id, chosen)
    return AutopayView(
        available=reason is None,
        why_not=reason,
        fee_payer=chosen.ach_fee_payer,
        next_due=next_due(conn, lease_id, rent.today()),
        autopays=autopays(conn, session.pmc_id, lease_id, mine),
    )


@router.post("/leases/{lease_id}/autopay", status_code=201)
def set_up(session: Tenant, conn: Conn, request: Request, lease_id: UUID, body: SetUp) -> Started:
    """Sets up autopay of the lease (stopping the tenant's autopay of it before, if any) and
    sends the tenant to the partner's page to save the bank account it pays from."""
    payer = tenant.still_on(conn, session, lease_id)
    chosen, _ = op.settings_of(conn, session.pmc_id)
    reason = op.why_not(conn, request, session.pmc_id, lease_id, chosen)
    partner = op.partner_of(request)
    if reason is not None or partner is None:
        raise HTTPException(409, reason or "this server takes no payments online")
    for before in autopays(conn, session.pmc_id, lease_id, payer):
        if before.stopped is None:
            autopay.stop(conn, session.pmc_id, before.id, "replaced")
    made = conn.execute(
        "INSERT INTO trust_autopay (pmc_id, lease_id, tenant_id, rule, amount, partner)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (session.pmc_id, lease_id, payer, body.rule, body.amount, partner.name),
    ).fetchone()
    assert made is not None
    try:
        page = partner.save_account(made[0], f"{outbox.PUBLIC_URL.get()}/pay/done")
    except payments.PartnerFailed:
        raise HTTPException(502, "the payments partner didn't answer: try again soon") from None
    return Started(id=made[0], url=page.url)


@router.post("/leases/{lease_id}/autopay/stop", status_code=204)
def stop_mine(session: Tenant, conn: Conn, lease_id: UUID) -> None:
    """Turns the tenant's autopay of the lease off; nothing more is started from it."""
    mine = tenant.lease_of(conn, session, lease_id)
    on = [a for a in autopays(conn, session.pmc_id, lease_id, mine) if a.stopped is None]
    if not on:
        raise HTTPException(404, "you have no autopay of this lease on")
    for each in on:
        autopay.stop(conn, session.pmc_id, each.id, "tenant")


# --- staff ------------------------------------------------------------------------------------


@router.get("/autopay")
def lease_autopays(session: Staff, conn: Conn, lease: UUID) -> list[Autopay]:
    """Every autopay of a lease's tenants, newest first."""
    rent.lease_of(conn, session.pmc_id, lease)
    return autopays(conn, session.pmc_id, lease)


@router.post("/autopay/{autopay_id}/stop", status_code=204)
def staff_stop(session: Staff, conn: Conn, autopay_id: UUID) -> None:
    """Turns a tenant's autopay off, naming who did."""
    row = conn.execute("SELECT pmc_id FROM trust_autopay WHERE id = %s", (autopay_id,)).fetchone()
    if row is None or row[0] != session.pmc_id:
        raise HTTPException(404, "no such autopay")
    if not autopay.stop(conn, session.pmc_id, autopay_id, "staff"):
        raise HTTPException(409, "this autopay is off already")


# --- the fake partner's pages -----------------------------------------------------------------


class FakeSave(BaseModel):
    tenant: str
    memo: str
    saved: bool


def fake_autopay(request: Request, conn: psycopg.Connection[Any], autopay_id: UUID) -> Any:
    partner = op.partner_of(request)
    row = conn.execute(
        "SELECT a.lease_id, t.display_name, a.partner,"
        " EXISTS (SELECT 1 FROM trust_autopay_accounts AS x WHERE x.autopay_id = a.id)"
        " FROM trust_autopay AS a JOIN trust_tenants AS t ON t.id = a.tenant_id WHERE a.id = %s",
        (autopay_id,),
    ).fetchone()
    if partner is None or partner.name != "fake" or row is None or row[2] != "fake":
        raise HTTPException(404, "no such autopay at the fake partner")
    return row


@router.get("/fake-save/{autopay_id}")
def fake_save_page(request: Request, conn: Conn, autopay_id: UUID) -> FakeSave:
    """The fake partner's page that saves a bank account (development and tests only)."""
    lease, name, _, saved = fake_autopay(request, conn, autopay_id)
    return FakeSave(tenant=name, memo=rent.lease_memo(conn, lease), saved=saved)


@router.post("/fake-save/{autopay_id}", status_code=204)
def fake_save(request: Request, conn: Conn, autopay_id: UUID) -> None:
    """The fake partner's report that the tenant saved a (made-up) bank account."""
    fake_autopay(request, conn, autopay_id)
    op.record_saved(conn, payments.fake_saved(autopay_id, datetime.now(UTC)), "fake")


class FakeQueued(BaseModel):
    id: UUID
    memo: str
    amount: str
    tenant_fee: str
    started_at: datetime


@router.get("/fake")
def fake_queue(request: Request, conn: Conn) -> list[FakeQueued]:
    """The fake partner's payments nothing was reported of yet, newest first: where a test (or a
    developer) finds an autopay's payment to say what the bank did."""
    partner = op.partner_of(request)
    if partner is None or partner.name != "fake":
        raise HTTPException(404, "no fake payments partner on this server")
    rows = conn.execute(
        "SELECT p.id, p.lease_id, p.amount, p.tenant_fee, p.created_at"
        " FROM trust_online_payments AS p WHERE p.partner = 'fake'"
        " AND NOT EXISTS (SELECT 1 FROM trust_online_payment_events AS e"
        "                 WHERE e.payment_id = p.id)"
        " ORDER BY p.created_at DESC, p.id LIMIT 50"
    ).fetchall()
    return [
        FakeQueued(
            id=row[0],
            memo=rent.lease_memo(conn, row[1]),
            amount=rent.cents(row[2]),
            tenant_fee=rent.cents(row[3]),
            started_at=row[4],
        )
        for row in rows
    ]
