"""Maintenance requests: tenants file them (tenant.py); staff see them, open a work order for
one and record what happens to it (assigned to a vendor, completed or cancelled).

A request's status is its work order's latest step: "received" before there is one, "opened"
once there is, then "assigned", "completed" or "cancelled".
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from psycopg import sql
from pydantic import BaseModel, Field

from pms.api.deps import Conn, Staff, refusal

router = APIRouter(prefix="/api/maintenance")
Status = Literal["received", "opened", "assigned", "completed", "cancelled"]
CLOSED = ("completed", "cancelled")

# Every request of a PMC with where it stands, newest first. {where} narrows it, and is only
# ever one of the fixed conditions below.
REQUESTS = """
SELECT r.id, r.lease_id, p.display_name, u.display_name, t.display_name, r.summary,
       r.entry_allowed, r.submitted_at, w.work_order_id,
       coalesce(s.step, CASE WHEN w.work_order_id IS NULL THEN 'received' ELSE 'opened' END),
       coalesce(s.taken_at, wo.opened_at, r.submitted_at),
       v.display_name
FROM trust_maintenance_requests AS r
JOIN trust_leases AS l ON l.id = r.lease_id
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
JOIN trust_tenants AS t ON t.id = r.tenant_id
LEFT JOIN trust_maintenance_request_work_orders AS w ON w.request_id = r.id
LEFT JOIN trust_work_orders AS wo ON wo.id = w.work_order_id
LEFT JOIN LATERAL (
    -- A closing step is the last one (a trigger refuses any after it), even when an earlier
    -- step of the same transaction shares its time.
    SELECT s.step, s.taken_at, s.vendor_id
    FROM trust_work_order_steps AS s
    WHERE s.work_order_id = w.work_order_id
    ORDER BY s.step IN ('completed', 'cancelled') DESC, s.taken_at DESC, s.created_at DESC
    LIMIT 1
) AS s ON true
LEFT JOIN trust_vendors AS v ON v.id = s.vendor_id
WHERE r.pmc_id = %(pmc)s AND {where}
ORDER BY r.submitted_at DESC, r.id
"""
FOR_TENANT = (
    "r.lease_id IN (SELECT lt.lease_id FROM trust_lease_tenants AS lt"
    " WHERE lt.pmc_id = %(pmc)s AND lt.tenant_id = %(person)s)"
)
ONE = "r.id = %(request)s"
EVERY = "true"


class Request(BaseModel):
    """A request as a tenant on its lease sees it."""

    id: UUID
    lease_id: UUID
    property: str
    unit: str
    filed_by: str
    summary: str
    entry_allowed: bool
    submitted_at: datetime
    status: Status
    status_at: datetime


class StaffRequest(Request):
    """A request as staff see it: also its work order and the vendor it went to."""

    work_order_id: UUID | None
    vendor: str | None


def requests(conn: psycopg.Connection[Any], where: str, **params: object) -> list[StaffRequest]:
    """The PMC's requests that match one of FOR_TENANT, ONE or EVERY."""
    if where not in (FOR_TENANT, ONE, EVERY):
        raise ValueError("requests are narrowed by one of the fixed conditions only")
    query = sql.SQL(REQUESTS).format(where=sql.SQL(where))
    return [
        StaffRequest(
            id=i,
            lease_id=lease,
            property=prop,
            unit=unit,
            filed_by=tenant,
            summary=summary,
            entry_allowed=entry,
            submitted_at=submitted,
            work_order_id=order,
            status=status,
            status_at=status_at,
            vendor=vendor,
        )
        for (
            i,
            lease,
            prop,
            unit,
            tenant,
            summary,
            entry,
            submitted,
            order,
            status,
            status_at,
            vendor,
        ) in conn.execute(query, params).fetchall()
    ]


def for_tenant(request: StaffRequest) -> Request:
    """What a tenant sees of a request: not its work order or the vendor it went to."""
    return Request(**request.model_dump(include=set(Request.model_fields)))


def one(conn: psycopg.Connection[Any], pmc_id: UUID, request_id: UUID) -> StaffRequest:
    found = requests(conn, ONE, pmc=pmc_id, request=request_id)
    if not found:
        raise HTTPException(404, "no such maintenance request")
    return found[0]


# --- staff ------------------------------------------------------------------------------------


class Vendor(BaseModel):
    id: UUID
    name: str


class Step(BaseModel):
    step: Literal["assigned", "completed", "cancelled"]
    # Only an assignment names a vendor (a check on trust_work_order_steps).
    vendor_id: UUID | None = None
    note: str | None = Field(default=None, max_length=2000)


@router.get("/requests")
def staff_requests(
    session: Staff,
    conn: Conn,
    show: Annotated[Literal["open", "all"], Query()] = "open",
) -> list[StaffRequest]:
    """The PMC's requests: by default those not yet completed or cancelled."""
    found = requests(conn, EVERY, pmc=session.pmc_id)
    return found if show == "all" else [r for r in found if r.status not in CLOSED]


@router.get("/vendors")
def vendors(session: Staff, conn: Conn) -> list[Vendor]:
    rows = conn.execute(
        "SELECT id, display_name FROM trust_vendors WHERE pmc_id = %s"
        ' ORDER BY display_name COLLATE "C", id',
        (session.pmc_id,),
    ).fetchall()
    return [Vendor(id=i, name=n) for i, n in rows]


@router.post("/requests/{request_id}/work-order", status_code=201)
def open_work_order(session: Staff, conn: Conn, request_id: UUID) -> StaffRequest:
    """A work order for the request, at its lease's unit, opened now."""
    found = one(conn, session.pmc_id, request_id)
    if found.work_order_id is not None:
        raise HTTPException(409, "this request already has a work order")
    row = conn.execute(
        "INSERT INTO trust_work_orders (pmc_id, property_id, unit_id, summary, opened_at)"
        " SELECT r.pmc_id, u.property_id, u.id, r.summary, now()"
        " FROM trust_maintenance_requests AS r"
        " JOIN trust_leases AS l ON l.id = r.lease_id"
        " JOIN trust_units AS u ON u.id = l.unit_id"
        " WHERE r.pmc_id = %s AND r.id = %s RETURNING id",
        (session.pmc_id, request_id),
    ).fetchone()
    assert row is not None  # the request was just read in this transaction
    try:
        conn.execute(
            "INSERT INTO trust_maintenance_request_work_orders"
            " (pmc_id, request_id, work_order_id) VALUES (%s, %s, %s)",
            (session.pmc_id, request_id, row[0]),
        )
    except psycopg.errors.UniqueViolation:
        # Another staff member opened one at the same moment; theirs stands.
        raise HTTPException(409, "this request already has a work order") from None
    return one(conn, session.pmc_id, request_id)


@router.post("/work-orders/{work_order_id}/steps", status_code=201)
def record_step(session: Staff, conn: Conn, work_order_id: UUID, step: Step) -> None:
    """What happened to a work order, as of now."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_work_orders WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, work_order_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such work order")
    if (step.step == "assigned") != (step.vendor_id is not None):
        raise HTTPException(422, "an assignment names a vendor, and only an assignment does")
    try:
        conn.execute(
            "INSERT INTO trust_work_order_steps"
            " (pmc_id, work_order_id, step, vendor_id, taken_at, note)"
            " VALUES (%s, %s, %s, %s, now(), %s)",
            (session.pmc_id, work_order_id, step.step, step.vendor_id, step.note),
        )
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(422, "no such vendor in this PMC") from None
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        # The work order is closed, or this step would come before its last one.
        raise HTTPException(409, refusal(refused)) from None
