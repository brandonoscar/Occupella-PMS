"""Work orders as a property manager works them (ADR 0005, ADR 0006): every work order of the
company a page at a time (open, closed or all, or of one status, vendor, property group or
property), numbered in the order they were opened; one work order with its details, steps, the
bills that name it, the tenant's request it answers and the contact details of the tenants of its
unit; a work order opened by staff for a property or unit; and its details changed.

Where a work order stands is its latest step, as trust_report_work_orders reads it: "open"
before any, then "assigned", "scheduled", "completed" or "cancelled" (a closing step is the last;
a trigger refuses any after it). Its details (priority, what is wrong, the day it is due and
whether the vendor may let themselves in) are the latest row of trust_work_order_details
(migration 0072). Recording a step is `maintenance.py`'s. Nothing here moves money.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, StringConstraints

from pms.api import scope
from pms.api.deps import Conn, Staff
from pms.api.directory import Contact, contacts_of
from pms.api.rent import cents, today
from pms.api.rentals import Page, Paged, PerPage, Ref, Search, like

router = APIRouter(prefix="/api/work-orders")

Status = Literal["open", "assigned", "scheduled", "completed", "cancelled"]
Priority = Literal["low", "normal", "high", "urgent"]
# May the vendor let themselves in: yes, call the tenant first, or no.
Entry = Literal["allowed", "call first", "not allowed"]

# Each work order of the PMC, numbered in the order they were opened, with its latest details,
# its latest step (its vendor, the last one assigned; the day it is scheduled for), when it last
# changed and what bills naming it come to. {where} is only ever one of the fixed conditions below.
WORK_ORDERS = """
WITH numbered AS (
    SELECT w.*, row_number() OVER (ORDER BY w.created_at, w.id) AS number
    FROM trust_work_orders AS w WHERE w.pmc_id = %(pmc)s
)
SELECT w.id, p.id, p.display_name, u.id, u.display_name, w.summary, w.opened_at,
       coalesce(s.step, 'open'), v.display_name,
       (SELECT coalesce(sum(b.amount), 0) FROM trust_bills AS b WHERE b.work_order_id = w.id),
       w.number, coalesce(d.priority, 'normal'), d.due_on, d.description, d.entry, a.vendor_id,
       vi.scheduled_for,
       greatest(w.opened_at, d.created_at,
                (SELECT max(s.taken_at) FROM trust_work_order_steps AS s
                 WHERE s.work_order_id = w.id)),
       vi.visit_window,
       (SELECT max(s.taken_at) FROM trust_work_order_steps AS s
        WHERE s.work_order_id = w.id AND s.step IN ('completed', 'cancelled')),
       count(*) OVER ()
FROM numbered AS w
JOIN trust_properties AS p ON p.id = w.property_id
LEFT JOIN trust_units AS u ON u.id = w.unit_id
LEFT JOIN LATERAL (
    SELECT s.step FROM trust_work_order_steps AS s WHERE s.work_order_id = w.id
    ORDER BY s.step IN ('completed', 'cancelled') DESC, s.taken_at DESC, s.created_at DESC
    LIMIT 1
) AS s ON true
LEFT JOIN LATERAL (
    SELECT s.vendor_id FROM trust_work_order_steps AS s
    WHERE s.work_order_id = w.id AND s.step = 'assigned'
    ORDER BY s.taken_at DESC, s.created_at DESC LIMIT 1
) AS a ON true
LEFT JOIN trust_vendors AS v ON v.id = a.vendor_id
-- The visit: the day and window it was last scheduled for.
LEFT JOIN LATERAL (
    SELECT s.scheduled_for, s.visit_window FROM trust_work_order_steps AS s
    WHERE s.work_order_id = w.id AND s.step = 'scheduled'
    ORDER BY s.taken_at DESC, s.created_at DESC LIMIT 1
) AS vi ON true
LEFT JOIN LATERAL (
    SELECT d.priority, d.due_on, d.description, d.entry, d.created_at
    FROM trust_work_order_details AS d WHERE d.work_order_id = w.id
    ORDER BY d.created_at DESC, d.id DESC LIMIT 1
) AS d ON true
WHERE {where}
ORDER BY w.opened_at DESC, w.id
LIMIT %(per_page)s OFFSET %(offset)s
"""
SHOWN = {
    "open": "coalesce(s.step, 'open') NOT IN ('completed', 'cancelled')",
    "closed": "coalesce(s.step, 'open') IN ('completed', 'cancelled')",
    "all": "true",
}
FOUND = (
    " AND (%(like)s::text IS NULL OR w.summary ILIKE %(like)s OR p.display_name ILIKE %(like)s"
    " OR u.display_name ILIKE %(like)s OR v.display_name ILIKE %(like)s)"
    " AND (%(status)s::text IS NULL OR coalesce(s.step, 'open') = %(status)s)"
    " AND (%(vendor)s::uuid IS NULL OR a.vendor_id = %(vendor)s)"
    f" AND {scope.within('p.id')}"
)


class WorkOrderRow(BaseModel):
    id: UUID
    # Its number in the company, in the order work orders were opened.
    number: int
    property: Ref
    unit: Ref | None
    summary: str
    opened_at: datetime
    status: Status
    vendor_id: UUID | None
    vendor: str | None
    billed: str
    priority: Priority
    due_on: date | None
    # Due before today and not closed.
    overdue: bool
    scheduled_for: date | None
    # When in that day the vendor comes ("8-10 am"), if said.
    visit_window: str | None
    updated_at: datetime
    # Days from the day it was opened to the day it was closed, or today (UTC).
    days_open: int


def rows_of(found: list[tuple]) -> list[WorkOrderRow]:  # type: ignore[type-arg]
    day = today()
    return [
        WorkOrderRow(
            id=i,
            number=number,
            property=Ref(id=p, name=pn),
            unit=None if u is None else Ref(id=u, name=un),
            summary=summary,
            opened_at=opened,
            status=status,
            vendor_id=vendor_id,
            vendor=vendor,
            billed=cents(Decimal(billed)),
            priority=priority,
            due_on=due,
            overdue=due is not None and due < day and status not in ("completed", "cancelled"),
            scheduled_for=scheduled,
            visit_window=window,
            updated_at=updated,
            days_open=(
                (closed or datetime.now(UTC)).astimezone(UTC).date() - opened.astimezone(UTC).date()
            ).days,
        )
        for (
            i,
            p,
            pn,
            u,
            un,
            summary,
            opened,
            status,
            vendor,
            billed,
            number,
            priority,
            due,
            _description,
            _entry,
            vendor_id,
            scheduled,
            updated,
            window,
            closed,
            _,
        ) in found
    ]


def query(**asked: Any) -> dict[str, Any]:
    """The parameters WORK_ORDERS takes, with the filters not given left open."""
    return {
        "like": None,
        "status": None,
        "vendor": None,
        **scope.params(None, None),
        **asked,
    }


@router.get("")
def work_orders(
    session: Staff,
    conn: Conn,
    show: Annotated[Literal["open", "closed", "all"], Query()] = "open",
    status: Annotated[Status | None, Query()] = None,
    vendor: Annotated[UUID | None, Query()] = None,
    group: scope.Group = None,
    property_id: scope.Property = None,
    q: Search = None,
    page: Page = 1,
    per_page: PerPage = 50,
) -> Paged[WorkOrderRow]:
    """The company's work orders a page at a time, newest first: those still open (by default),
    the closed ones or all; maybe of one status or vendor (the one last assigned), of a property
    group or property, or found by what they are for, property, unit or vendor."""
    found = conn.execute(
        WORK_ORDERS.format(where=SHOWN[show] + FOUND),
        query(
            pmc=session.pmc_id,
            like=like(q),
            status=status,
            vendor=vendor,
            per_page=per_page,
            offset=(page - 1) * per_page,
            **scope.params(group, property_id),
        ),
    ).fetchall()
    return Paged(
        items=rows_of(found), total=found[0][-1] if found else 0, page=page, per_page=per_page
    )


def assigned_to(conn: psycopg.Connection[Any], pmc: UUID, vendor: UUID) -> list[WorkOrderRow]:
    """The PMC's work orders whose latest assignment is to the vendor, newest first (at most
    500)."""
    return rows_of(
        conn.execute(
            WORK_ORDERS.format(where="a.vendor_id = %(vendor)s"),
            query(pmc=pmc, vendor=vendor, per_page=500, offset=0),
        ).fetchall()
    )


class StepOut(BaseModel):
    step: Literal["assigned", "scheduled", "completed", "cancelled"]
    vendor: str | None
    taken_at: datetime
    scheduled_for: date | None
    visit_window: str | None
    note: str | None


class BillLine(BaseModel):
    id: UUID
    vendor: str
    reference: str
    amount: str
    paid: bool


class Photo(BaseModel):
    id: UUID
    title: str


class RequestOut(BaseModel):
    id: UUID
    tenant: str
    summary: str
    submitted_at: datetime
    emergency: bool
    entry_allowed: bool
    # The photos the tenant filed with it (documents about it, not removed).
    photos: list[Photo]
    # The staff members on call who were emailed of it, an emergency, by name.
    emailed: list[str]


REQUEST = ("id", "tenant", "summary", "submitted_at", "emergency", "entry_allowed")


class TenantContact(BaseModel):
    name: str
    contact: Contact


class WorkOrder(WorkOrderRow):
    description: str | None
    entry: Entry | None
    steps: list[StepOut]
    bills: list[BillLine]
    request: RequestOut | None
    # The tenants of its unit's lease in effect today, with their contact details.
    tenants: list[TenantContact]


@router.get("/{work_order_id}")
def work_order(session: Staff, conn: Conn, work_order_id: UUID) -> WorkOrder:
    """A work order: where it stands, every step in order, the bills that name it and the
    tenant's request it answers, if one."""
    found = conn.execute(
        WORK_ORDERS.format(where="w.id = %(id)s"),
        query(pmc=session.pmc_id, id=work_order_id, per_page=1, offset=0),
    ).fetchall()
    if not found:
        raise HTTPException(404, "no such work order")
    (row,) = rows_of(found)
    description, entry = found[0][13], found[0][14]
    steps = [
        StepOut(
            step=step,
            vendor=vendor,
            taken_at=at,
            scheduled_for=scheduled,
            visit_window=window,
            note=note,
        )
        for step, vendor, at, scheduled, window, note in conn.execute(
            "SELECT s.step, v.display_name, s.taken_at, s.scheduled_for, s.visit_window, s.note"
            " FROM trust_work_order_steps AS s"
            " LEFT JOIN trust_vendors AS v ON v.id = s.vendor_id WHERE s.work_order_id = %s"
            " ORDER BY s.taken_at, s.step IN ('completed', 'cancelled'), s.created_at",
            (work_order_id,),
        ).fetchall()
    ]
    bills = [
        BillLine(id=i, vendor=vendor, reference=reference, amount=cents(amount), paid=paid)
        for i, vendor, reference, amount, paid in conn.execute(
            "SELECT b.id, v.display_name, b.reference, b.amount,"
            " EXISTS (SELECT 1 FROM trust_bill_payments AS x WHERE x.bill_id = b.id"
            "         AND x.step = 'paid')"
            " FROM trust_bills AS b JOIN trust_vendors AS v ON v.id = b.vendor_id"
            " WHERE b.work_order_id = %s ORDER BY b.bill_date, b.created_at, b.id",
            (work_order_id,),
        ).fetchall()
    ]
    request = conn.execute(
        "SELECT r.id, t.display_name, r.summary, r.submitted_at, r.emergency, r.entry_allowed"
        " FROM trust_maintenance_request_work_orders AS m"
        " JOIN trust_maintenance_requests AS r ON r.id = m.request_id"
        " JOIN trust_tenants AS t ON t.id = r.tenant_id WHERE m.work_order_id = %s",
        (work_order_id,),
    ).fetchone()
    known = contacts_of(conn, session.pmc_id, "tenant_id")
    tenants = [
        TenantContact(name=name, contact=known.get(tenant, Contact()))
        for tenant, name in conn.execute(
            "SELECT t.id, t.display_name FROM trust_work_orders AS w"
            " JOIN trust_leases AS l ON l.unit_id = w.unit_id AND l.starts_on <= %s"
            "   AND (l.ends_on IS NULL OR l.ends_on >= %s)"
            " JOIN trust_lease_tenants AS lt ON lt.lease_id = l.id"
            " JOIN trust_tenants AS t ON t.id = lt.tenant_id"
            ' WHERE w.id = %s ORDER BY t.display_name COLLATE "C", t.id',
            (today(), today(), work_order_id),
        ).fetchall()
    ]
    return WorkOrder(
        **row.model_dump(),
        description=description,
        entry=entry,
        steps=steps,
        bills=bills,
        request=None if request is None else request_out(conn, request),
        tenants=tenants,
    )


def request_out(conn: psycopg.Connection[Any], request: tuple) -> RequestOut:  # type: ignore[type-arg]
    """The tenant's request a work order answers, with the photos filed with it."""
    photos = [
        Photo(id=i, title=title)
        for i, title in conn.execute(
            "SELECT d.id, d.title FROM trust_documents AS d WHERE d.request_id = %s"
            " AND NOT EXISTS (SELECT 1 FROM trust_document_removals AS x"
            "                 WHERE x.document_id = d.id)"
            ' ORDER BY d.created_at, d.title COLLATE "C", d.id',
            (request[0],),
        ).fetchall()
    ]
    emailed = [
        name
        for (name,) in conn.execute(
            "SELECT s.display_name FROM trust_emails AS e"
            " JOIN trust_staff AS s ON s.id = e.staff_id"
            " WHERE e.request_id = %s AND e.kind = 'emergency_request'"
            ' ORDER BY s.display_name COLLATE "C", s.id',
            (request[0],),
        ).fetchall()
    ]
    return RequestOut(**dict(zip(REQUEST, request, strict=True)), photos=photos, emailed=emailed)


Summary = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)
]


class Details(BaseModel):
    priority: Priority = "normal"
    description: Description | None = None
    due_on: date | None = None
    entry: Entry | None = None


class NewWorkOrder(Details):
    property_id: UUID
    unit_id: UUID | None = None
    summary: Summary


class Opened(BaseModel):
    id: UUID


def record_details(
    conn: psycopg.Connection[Any], pmc_id: UUID, work_order_id: UUID, details: Details
) -> bool:
    """Records a work order's details unless they are what it has already; True when it did."""
    latest = conn.execute(
        "SELECT priority, description, due_on, entry FROM trust_work_order_details"
        " WHERE work_order_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
        (work_order_id,),
    ).fetchone()
    asked = (details.priority, details.description, details.due_on, details.entry)
    if (latest is None and asked == ("normal", None, None, None)) or (
        latest is not None and tuple(latest) == asked
    ):
        return False
    conn.execute(
        "INSERT INTO trust_work_order_details"
        " (pmc_id, work_order_id, priority, description, due_on, entry)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (pmc_id, work_order_id, *asked),
    )
    return True


@router.post("", status_code=201)
def open_work_order(session: Staff, conn: Conn, new: NewWorkOrder) -> Opened:
    """A work order staff open themselves, for a property or one of its units, opened now."""
    try:
        row = conn.execute(
            "INSERT INTO trust_work_orders (pmc_id, property_id, unit_id, summary, opened_at)"
            " SELECT %s, p.id, %s, %s, now() FROM trust_properties AS p"
            " WHERE p.pmc_id = %s AND p.id = %s RETURNING id",
            (session.pmc_id, new.unit_id, new.summary, session.pmc_id, new.property_id),
        ).fetchone()
    except (psycopg.errors.ForeignKeyViolation, psycopg.errors.CheckViolation):
        # Another PMC's unit, or another property's (trust_check_work_order).
        raise HTTPException(422, "that unit is not one of the property's") from None
    if row is None:
        raise HTTPException(404, "no such property")
    record_details(conn, session.pmc_id, row[0], new)
    return Opened(id=row[0])


class Changed(BaseModel):
    changed: bool


@router.post("/{work_order_id}/details")
def change_details(session: Staff, conn: Conn, work_order_id: UUID, details: Details) -> Changed:
    """A work order's priority, what is wrong, the day it is due and whether the vendor may let
    themselves in, as they are now; the same again writes nothing."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_work_orders WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, work_order_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such work order")
    return Changed(changed=record_details(conn, session.pmc_id, work_order_id, details))
