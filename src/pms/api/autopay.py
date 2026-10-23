"""Autopay (migration 20261022000159; the founder, 2026-10-23): a tenant still on a lease saves a
card or a bank account with the payment processor for it, and from then on, once a month's rent
is due (the lease's due day), what the lease owes is charged to it by itself: an online payment
(src/pms/api/online_payments.py) the nightly run or an admin's Run rent now starts, once per
autopay and due date, recorded in trust once the processor says the money came. A lease owing
nothing, on a payment hold or ended is charged nothing. A lease has one autopay at a time; the
tenant stops it in the portal, staff from Company setup's Online payments (area rent), with why.

The processor keeps the card or account; the PMS keeps only the processor's reference to it and
how it is shown ("Card ••••4242"). Nothing here moves money but the payments it records.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, StringConstraints

from pms.api import company, online_payments, outbox, rent, tenant
from pms.api.deps import Conn, Staff, Tenant
from pms.payments import processor as processors
from pms.payments.processor import Event, Method

router = APIRouter()
log = logging.getLogger(__name__)

Status = Literal["started", "on", "failed", "stopped"]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class Autopay(BaseModel):
    id: UUID
    lease_id: UUID
    home: str
    tenant: str
    method: Method
    since: datetime
    # started: waiting for the card or account to be saved; on: charging it each due day.
    status: Status
    # How the saved card or account is shown: "Card ••••4242".
    label: str | None
    reason: str | None


class TenantAutopay(BaseModel):
    methods: list[Method]
    autopay: list[Autopay]


class StartAutopay(BaseModel):
    method: Method


class AutopayStarted(BaseModel):
    autopay_id: UUID
    url: str


class Stopping(BaseModel):
    reason: Reason


class FakeSave(BaseModel):
    outcome: Literal["saved", "not saved"]


# Each autopay with how it stands: its latest step, and what it saved.
AUTOPAY = """
SELECT a.id, a.lease_id, pr.display_name || ', ' || u.display_name, t.display_name, a.method,
       a.created_at, s.step, saved.label, s.reason
FROM trust_autopay AS a
JOIN trust_leases AS l ON l.pmc_id = a.pmc_id AND l.id = a.lease_id
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS pr ON pr.id = u.property_id
JOIN trust_tenants AS t ON t.id = a.tenant_id
CROSS JOIN LATERAL (
    SELECT step, reason FROM trust_autopay_steps AS x
    WHERE x.autopay_id = a.id ORDER BY x.id DESC LIMIT 1
) AS s
LEFT JOIN trust_autopay_steps AS saved ON saved.autopay_id = a.id AND saved.step = 'saved'
WHERE a.pmc_id = %(pmc)s AND {where}
ORDER BY a.created_at DESC, a.id
LIMIT 200
"""
STATUS: dict[str, Status] = {
    "started": "started",
    "saved": "on",
    "failed": "failed",
    "stopped": "stopped",
}


def autopay_of(
    conn: psycopg.Connection[Any], pmc_id: UUID, where: str = "true", **params: Any
) -> list[Autopay]:
    rows = conn.execute(AUTOPAY.format(where=where), {"pmc": pmc_id, **params})  # noqa: S608
    return [
        Autopay(
            id=aid,
            lease_id=lease,
            home=home,
            tenant=name,
            method=method,
            since=at,
            status=STATUS[step],
            label=label,
            reason=reason,
        )
        for aid, lease, home, name, method, at, step, label, reason in rows.fetchall()
    ]


def on_autopay(conn: psycopg.Connection[Any], lease_id: UUID) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM trust_autopay AS a WHERE a.lease_id = %s"
            " AND EXISTS (SELECT 1 FROM trust_autopay_steps AS s"
            "             WHERE s.autopay_id = a.id AND s.step = 'saved')"
            " AND NOT EXISTS (SELECT 1 FROM trust_autopay_steps AS s"
            "                 WHERE s.autopay_id = a.id AND s.step = 'stopped')",
            (lease_id,),
        ).fetchone()
        is not None
    )


@router.get("/api/tenant/autopay")
def tenant_autopay(session: Tenant, conn: Conn, request: Request) -> TenantAutopay:
    """What the tenant may set autopay up with, and their autopay."""
    found = online_payments.processor_of(request)
    return TenantAutopay(
        methods=online_payments.methods_of(
            online_payments.settings_of(conn, session.pmc_id), found
        ),
        autopay=autopay_of(
            conn,
            session.pmc_id,
            "a.tenant_id IN (SELECT trust_tenant_records(%(pmc)s, %(person)s))",
            person=session.person_id,
        ),
    )


@router.post("/api/tenant/leases/{lease_id}/autopay", status_code=201)
def start_autopay(
    session: Tenant, conn: Conn, request: Request, lease_id: UUID, new: StartAutopay
) -> AutopayStarted:
    """Starts autopay: the tenant saves their card or account on the processor's page."""
    who = tenant.still_on(conn, session, lease_id)
    found = online_payments.processor_of(request)
    taken = online_payments.methods_of(online_payments.settings_of(conn, session.pmc_id), found)
    if new.method not in taken:
        name = company.name_of(conn, session.pmc_id)
        raise HTTPException(409, f"{name} takes no {new.method} online")
    assert found is not None
    if on_autopay(conn, lease_id):
        raise HTTPException(409, "this lease is on autopay already: stop it first")
    row = conn.execute(
        "INSERT INTO trust_autopay (pmc_id, lease_id, tenant_id, method, processor)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (session.pmc_id, lease_id, who, new.method, found.name),
    ).fetchone()
    assert row is not None
    checkout = found.save(str(row[0]), new.method, f"{outbox.PUBLIC_URL.get()}/")
    conn.execute(
        "INSERT INTO trust_autopay_steps (pmc_id, autopay_id, step, reference)"
        " VALUES (%s, %s, 'started', %s)",
        (session.pmc_id, row[0], checkout.reference),
    )
    return AutopayStarted(autopay_id=row[0], url=checkout.url)


def stop(conn: psycopg.Connection[Any], pmc_id: UUID, autopay_id: UUID, reason: str) -> None:
    if conn.execute(
        "SELECT 1 FROM trust_autopay_steps WHERE autopay_id = %s AND step IN ('stopped', 'failed')",
        (autopay_id,),
    ).fetchone():
        raise HTTPException(409, "this autopay is off already")
    conn.execute(
        "INSERT INTO trust_autopay_steps (pmc_id, autopay_id, step, reason)"
        " VALUES (%s, %s, 'stopped', %s)",
        (pmc_id, autopay_id, reason),
    )


@router.post("/api/tenant/autopay/{autopay_id}/stop", status_code=204)
def tenant_stop(session: Tenant, conn: Conn, autopay_id: UUID) -> None:
    """The tenant stops their autopay: nothing more is charged to it."""
    mine = conn.execute(
        "SELECT 1 FROM trust_autopay WHERE pmc_id = %s AND id = %s"
        " AND tenant_id IN (SELECT trust_tenant_records(%s, %s))",
        (session.pmc_id, autopay_id, session.pmc_id, session.person_id),
    ).fetchone()
    if mine is None:
        raise HTTPException(404, "no such autopay of yours")
    stop(conn, session.pmc_id, autopay_id, "the tenant stopped it")


@router.get("/api/rent/autopay")
def staff_autopay(session: Staff, conn: Conn) -> list[Autopay]:
    """Every lease's autopay, newest first, with how each stands."""
    return autopay_of(conn, session.pmc_id)


@router.post("/api/rent/autopay/{autopay_id}/stop", status_code=204)
def staff_stop(session: Staff, conn: Conn, autopay_id: UUID, stopping: Stopping) -> None:
    """Staff stop a tenant's autopay, with why: nothing more is charged to it."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_autopay WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, autopay_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such autopay")
    stop(conn, session.pmc_id, autopay_id, stopping.reason)


def take_saved(conn: psycopg.Connection[Any], processor: str, event: Event) -> bool:
    """Takes a processor's word that a card or account was saved, or not, once."""
    if conn.execute(
        "SELECT 1 FROM trust_autopay_steps WHERE processor_event = %s", (event.id,)
    ).fetchone():
        return False
    row = conn.execute(
        "SELECT a.pmc_id, a.id FROM trust_autopay_steps AS s"
        " JOIN trust_autopay AS a ON a.id = s.autopay_id"
        " WHERE s.step = 'started' AND s.reference = %s AND a.processor = %s",
        (event.reference, processor),
    ).fetchone()
    if (
        row is None
        or conn.execute(
            "SELECT 1 FROM trust_autopay_steps WHERE autopay_id = %s"
            " AND step IN ('saved', 'failed', 'stopped')",
            (row[1],),
        ).fetchone()
    ):
        return False
    pmc_id, autopay_id = row
    if event.outcome == "saved":
        try:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO trust_autopay_steps (pmc_id, autopay_id, step, reference, label,"
                    " processor_event) VALUES (%s, %s, 'saved', %s, %s, %s)",
                    (pmc_id, autopay_id, event.processor_payment, event.label, event.id),
                )
            return True
        except psycopg.errors.CheckViolation:  # another autopay of the lease was saved first
            reason = "this lease is on autopay already"
    else:
        reason = event.reason or "nothing was saved"
    conn.execute(
        "INSERT INTO trust_autopay_steps (pmc_id, autopay_id, step, reason, processor_event)"
        " VALUES (%s, %s, 'failed', %s, %s)",
        (pmc_id, autopay_id, reason, event.id),
    )
    return True


@router.post("/api/tenant/autopay/fake/{reference}", status_code=204)
def fake_save(
    session: Tenant, conn: Conn, request: Request, reference: str, said: FakeSave
) -> None:
    """The fake processor's page for saving a card or account: the tenant says what happened."""
    found = online_payments.processor_of(request)
    if found is None or found.name != "fake":
        raise HTTPException(404, "this server's payment processor is not the fake one")
    mine = conn.execute(
        "SELECT a.method FROM trust_autopay_steps AS s"
        " JOIN trust_autopay AS a ON a.id = s.autopay_id"
        " WHERE s.step = 'started' AND s.reference = %s AND a.processor = 'fake'"
        " AND a.pmc_id = %s AND a.tenant_id IN (SELECT trust_tenant_records(%s, %s))",
        (reference, session.pmc_id, session.pmc_id, session.person_id),
    ).fetchone()
    if mine is None:
        raise HTTPException(404, "no such autopay of yours")
    label = "Card ••••4242" if mine[0] == "card" else "Bank account ••••6789"
    event = Event(
        id=f"fake-event-{reference}",
        outcome=said.outcome,
        reference=reference,
        processor_payment=f"fake_saved_{reference}" if said.outcome == "saved" else None,
        reason=None if said.outcome == "saved" else "you said it wasn't saved",
        label=label if said.outcome == "saved" else None,
    )
    if not take_saved(conn, "fake", event):
        raise HTTPException(409, "this autopay is settled already")


# Each autopay of the PMC that is on, through this processor, whose lease is in effect on the day
# and whose tenant is still on it: what it saved, and the lease's due date this month.
DUE = """
SELECT a.id, a.lease_id, a.tenant_id, a.method, saved.reference,
       trust_rent_due_on(a.lease_id, %(day)s)
FROM trust_autopay AS a
JOIN trust_autopay_steps AS saved ON saved.autopay_id = a.id AND saved.step = 'saved'
JOIN trust_leases AS l ON l.id = a.lease_id
WHERE a.pmc_id = %(pmc)s AND a.processor = %(processor)s
  AND NOT EXISTS (SELECT 1 FROM trust_autopay_steps AS s
                  WHERE s.autopay_id = a.id AND s.step = 'stopped')
  AND l.starts_on <= %(day)s AND (l.ends_on IS NULL OR %(day)s <= l.ends_on)
  AND NOT EXISTS (SELECT 1 FROM trust_lease_departures AS d
                  WHERE d.lease_id = a.lease_id AND d.tenant_id = a.tenant_id
                    AND d.left_on <= %(day)s)
ORDER BY a.created_at, a.id
"""


def charge_due(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    day: date,
    found: processors.Processor | None,
) -> int:
    """Starts the payment of what each lease on autopay owes once its rent is due this month, once
    per autopay and due date; a lease owing nothing or on a payment hold is left. How many
    started. A processor that fails to take a charge is logged and tried again the next run."""
    if found is None:
        return 0
    started = 0
    rows = conn.execute(DUE, {"pmc": pmc_id, "day": day, "processor": found.name}).fetchall()
    for autopay_id, lease_id, tenant_id, method, saved, due_on in rows:
        if due_on is None or day < due_on:
            continue
        if conn.execute(
            "SELECT 1 FROM trust_autopay_charges WHERE autopay_id = %s AND due_on = %s",
            (autopay_id, due_on),
        ).fetchone():
            continue
        owed = rent.owed(conn, lease_id)
        held = conn.execute(rent.IN_FORCE, {"pmc": pmc_id, "lease": lease_id, "day": day})
        if owed <= 0 or held.fetchone() is not None:
            continue
        try:
            with conn.transaction():
                immediate = start_charge(
                    conn,
                    found,
                    pmc_id,
                    autopay_id,
                    lease_id,
                    tenant_id,
                    method,
                    saved,
                    due_on,
                    owed,
                )
        except (OSError, ValueError, KeyError):
            log.exception("autopay %s couldn't be charged; the next run tries again", autopay_id)
            continue
        started += 1
        if immediate is not None:
            with conn.transaction():
                online_payments.take(conn, found.name, immediate)
    return started


def start_charge(
    conn: psycopg.Connection[Any],
    found: processors.Processor,
    pmc_id: UUID,
    autopay_id: UUID,
    lease_id: UUID,
    tenant_id: UUID,
    method: Method,
    saved: str,
    due_on: date,
    owed: Decimal,
) -> Event | None:
    row = conn.execute(
        "INSERT INTO trust_online_payments (pmc_id, lease_id, tenant_id, amount, method, processor)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (pmc_id, lease_id, tenant_id, owed, method, found.name),
    ).fetchone()
    assert row is not None
    conn.execute(
        "INSERT INTO trust_autopay_charges (pmc_id, autopay_id, due_on, payment_id)"
        " VALUES (%s, %s, %s, %s)",
        (pmc_id, autopay_id, due_on, row[0]),
    )
    reference, immediate = found.charge(
        saved,
        owed,
        method,
        f"{autopay_id}-{due_on.isoformat()}",
        f"Rent – {rent.lease_memo(conn, lease_id)} (autopay)",
    )
    conn.execute(
        "INSERT INTO trust_online_payment_steps (pmc_id, payment_id, step, reference)"
        " VALUES (%s, %s, 'started', %s)",
        (pmc_id, row[0], reference),
    )
    return immediate
