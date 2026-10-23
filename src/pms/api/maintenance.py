"""Maintenance requests: tenants file them (tenant.py), maybe as an emergency and with photos;
staff see them, open a work order for one (urgent for an emergency, the tenant's word on entry
copied) and record what happens to it (assigned to a vendor, a visit's day and window, completed
or cancelled). Staff say who is on call for emergencies (migration 0084).

A request's status is its work order's latest step: "received" before there is one, "opened"
once there is, then "assigned", "scheduled", "completed" or "cancelled".
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from psycopg import sql
from pydantic import BaseModel, Field, StringConstraints

from pms import notices, texts
from pms.api import outbox
from pms.api.deps import Conn, Staff, refusal

router = APIRouter(prefix="/api/maintenance")
Status = Literal["received", "opened", "assigned", "scheduled", "completed", "cancelled"]
CLOSED = ("completed", "cancelled")

# Every request of a PMC with where it stands, newest first. {where} narrows it, and is only
# ever one of the fixed conditions below.
REQUESTS = """
SELECT r.id, r.lease_id, p.display_name, u.display_name, t.display_name, r.summary,
       r.entry_allowed, r.submitted_at, w.work_order_id,
       coalesce(s.step, CASE WHEN w.work_order_id IS NULL THEN 'received' ELSE 'opened' END),
       coalesce(s.taken_at, wo.opened_at, r.submitted_at),
       v.display_name, r.emergency, vi.scheduled_for, vi.visit_window,
       (SELECT count(*) FROM trust_documents AS d
        WHERE d.request_id = r.id
          AND NOT EXISTS (SELECT 1 FROM trust_document_removals AS x WHERE x.document_id = d.id))
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
    SELECT s.step, s.taken_at
    FROM trust_work_order_steps AS s
    WHERE s.work_order_id = w.work_order_id
    ORDER BY s.step IN ('completed', 'cancelled') DESC, s.taken_at DESC, s.created_at DESC
    LIMIT 1
) AS s ON true
-- Its vendor: the one last assigned, through a schedule and a close.
LEFT JOIN LATERAL (
    SELECT s.vendor_id FROM trust_work_order_steps AS s
    WHERE s.work_order_id = w.work_order_id AND s.step = 'assigned'
    ORDER BY s.taken_at DESC, s.created_at DESC
    LIMIT 1
) AS a ON true
LEFT JOIN trust_vendors AS v ON v.id = a.vendor_id
-- The visit: the day and window it was last scheduled for.
LEFT JOIN LATERAL (
    SELECT s.scheduled_for, s.visit_window FROM trust_work_order_steps AS s
    WHERE s.work_order_id = w.work_order_id AND s.step = 'scheduled'
    ORDER BY s.taken_at DESC, s.created_at DESC
    LIMIT 1
) AS vi ON true
WHERE r.pmc_id = %(pmc)s AND {where}
ORDER BY r.submitted_at DESC, r.id
"""
FOR_TENANT = (
    "r.lease_id IN (SELECT lt.lease_id FROM trust_lease_tenants AS lt"
    " WHERE lt.pmc_id = %(pmc)s"
    " AND lt.tenant_id IN (SELECT trust_tenant_records(%(pmc)s, %(person)s)))"
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
    emergency: bool
    submitted_at: datetime
    status: Status
    status_at: datetime
    # The day the vendor comes, and when in it, as last scheduled.
    visit_on: date | None
    visit_window: str | None
    # How many photos came with it.
    photos: int


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
            emergency=emergency,
            visit_on=visit_on,
            visit_window=window,
            photos=photos,
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
            emergency,
            visit_on,
            window,
            photos,
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
    step: Literal["assigned", "scheduled", "completed", "cancelled"]
    # Only an assignment names a vendor (a check on trust_work_order_steps).
    vendor_id: UUID | None = None
    # Only a schedule names the day the vendor comes, and maybe when in it ("8-10 am").
    scheduled_for: date | None = None
    visit_window: Annotated[
        str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)
    ] = None
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
    """The vendors a work order may be assigned to: those not archived (migration 0110)."""
    rows = conn.execute(
        "SELECT id, display_name FROM trust_vendors WHERE pmc_id = %s"
        " AND NOT trust_is_archived(NULL, NULL, NULL, NULL, id)"
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
    # Its details from the request: urgent for an emergency, and the tenant's word on entry.
    conn.execute(
        "INSERT INTO trust_work_order_details (pmc_id, work_order_id, priority, entry)"
        " VALUES (%s, %s, %s, %s)",
        (
            session.pmc_id,
            row[0],
            "urgent" if found.emergency else "normal",
            "allowed" if found.entry_allowed else "call first",
        ),
    )
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
    """What happened to a work order, as of now. An assignment or a schedule is emailed to the
    vendor while the server sends email and the company's settings ask for it."""
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
    if (step.step == "scheduled") != (step.scheduled_for is not None):
        raise HTTPException(422, "a schedule names the day the vendor comes, and only it does")
    if step.visit_window is not None and step.step != "scheduled":
        raise HTTPException(422, "only a schedule says when in the day the vendor comes")
    try:
        made = conn.execute(
            "INSERT INTO trust_work_order_steps"
            " (pmc_id, work_order_id, step, vendor_id, scheduled_for, visit_window, taken_at,"
            " note) VALUES (%s, %s, %s, %s, %s, %s, now(), %s) RETURNING id",
            (
                session.pmc_id,
                work_order_id,
                step.step,
                step.vendor_id,
                step.scheduled_for,
                step.visit_window,
                step.note,
            ),
        )
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(422, "no such vendor in this PMC") from None
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        # The work order is closed, this step would come before its last one, a schedule before
        # an assignment, or the vendor's insurance ran out.
        raise HTTPException(409, refusal(refused)) from None
    found = made.fetchone()
    assert found is not None
    if outbox.SENDING.get():
        notices.write_work_order(conn, session.pmc_id, found[0])
    if step.step == "scheduled":
        # The visit texted to the home's tenants who agreed (migration 20261023000204).
        texts.write_visit(conn, session.pmc_id, found[0])


# --- who is on call ---------------------------------------------------------------------------


class OnCall(BaseModel):
    staff_id: UUID
    name: str
    on_call: bool
    # Whether they have a login to be emailed at.
    emailed: bool


class SetOnCall(BaseModel):
    staff_id: UUID
    on_call: bool


ON_CALL = """
SELECT s.id, s.display_name, coalesce(o.on_call, false),
       EXISTS (SELECT 1 FROM trust_logins AS l
               JOIN trust_login_secrets AS x ON x.login_id = l.id
               WHERE l.pmc_id = s.pmc_id AND l.kind = 'staff' AND l.staff_id = s.id
                 AND l.disabled_at IS NULL)
FROM trust_staff AS s
LEFT JOIN LATERAL (
    SELECT o.on_call FROM trust_on_call_staff AS o
    WHERE o.pmc_id = s.pmc_id AND o.staff_id = s.id
    ORDER BY o.created_at DESC, o.id DESC
    LIMIT 1
) AS o ON true
WHERE s.pmc_id = %s
ORDER BY s.display_name COLLATE "C", s.id
"""


@router.get("/on-call")
def on_call(session: Staff, conn: Conn) -> list[OnCall]:
    """Every staff member, and whether they are on call for emergencies a tenant files."""
    return [
        OnCall(staff_id=i, name=n, on_call=on, emailed=emailed)
        for i, n, on, emailed in conn.execute(ON_CALL, (session.pmc_id,)).fetchall()
    ]


@router.post("/on-call")
def set_on_call(session: Staff, conn: Conn, change: SetOnCall) -> list[OnCall]:
    """Puts a staff member on call, or takes them off; the same again writes nothing."""
    now = {i: on for i, _, on, _ in conn.execute(ON_CALL, (session.pmc_id,)).fetchall()}
    if change.staff_id not in now:
        raise HTTPException(404, "no such staff member")
    if now[change.staff_id] != change.on_call:
        conn.execute(
            "INSERT INTO trust_on_call_staff (pmc_id, staff_id, on_call) VALUES (%s, %s, %s)",
            (session.pmc_id, change.staff_id, change.on_call),
        )
    return on_call(session, conn)
