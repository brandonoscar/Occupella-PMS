"""Work orders as a property manager works them (ADR 0005): every work order of the company a
page at a time (open, closed or all), one work order with its steps, the bills that name it and
the tenant's request it answers, and a work order opened by staff for a property or unit.

Where a work order stands is its latest step, as trust_report_work_orders reads it: "open"
before any, then "assigned", "completed" or "cancelled" (a closing step is the last; a trigger
refuses any after it). Recording a step is `maintenance.py`'s. Nothing
here moves money.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, StringConstraints

from pms.api.deps import Conn, Staff
from pms.api.rent import cents
from pms.api.rentals import Page, Paged, PerPage, Ref, Search, like

router = APIRouter(prefix="/api/work-orders")

Status = Literal["open", "assigned", "completed", "cancelled"]

# Each work order of the PMC with its latest step (its vendor, the last one assigned) and what
# bills naming it come to. {where} is only ever one of the fixed conditions below.
WORK_ORDERS = """
SELECT w.id, p.id, p.display_name, u.id, u.display_name, w.summary, w.opened_at,
       coalesce(s.step, 'open'), v.display_name,
       (SELECT coalesce(sum(b.amount), 0) FROM trust_bills AS b WHERE b.work_order_id = w.id),
       count(*) OVER ()
FROM trust_work_orders AS w
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
WHERE w.pmc_id = %(pmc)s AND {where}
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
)


class WorkOrderRow(BaseModel):
    id: UUID
    property: Ref
    unit: Ref | None
    summary: str
    opened_at: datetime
    status: Status
    vendor: str | None
    billed: str


def rows_of(found: list[tuple]) -> list[WorkOrderRow]:  # type: ignore[type-arg]
    return [
        WorkOrderRow(
            id=i,
            property=Ref(id=p, name=pn),
            unit=None if u is None else Ref(id=u, name=un),
            summary=summary,
            opened_at=opened,
            status=status,
            vendor=vendor,
            billed=cents(Decimal(billed)),
        )
        for i, p, pn, u, un, summary, opened, status, vendor, billed, _ in found
    ]


@router.get("")
def work_orders(
    session: Staff,
    conn: Conn,
    show: Annotated[Literal["open", "closed", "all"], Query()] = "open",
    q: Search = None,
    page: Page = 1,
    per_page: PerPage = 50,
) -> Paged[WorkOrderRow]:
    """The company's work orders a page at a time, newest first: those still open (by default),
    the closed ones or all, found by what they are for, property, unit or vendor."""
    found = conn.execute(
        WORK_ORDERS.format(where=SHOWN[show] + FOUND),
        {
            "pmc": session.pmc_id,
            "like": like(q),
            "per_page": per_page,
            "offset": (page - 1) * per_page,
        },
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
            {"pmc": pmc, "vendor": vendor, "per_page": 500, "offset": 0},
        ).fetchall()
    )


class StepOut(BaseModel):
    step: Literal["assigned", "completed", "cancelled"]
    vendor: str | None
    taken_at: datetime
    note: str | None


class BillLine(BaseModel):
    id: UUID
    vendor: str
    reference: str
    amount: str
    paid: bool


class RequestOut(BaseModel):
    id: UUID
    tenant: str
    summary: str
    submitted_at: datetime


REQUEST = ("id", "tenant", "summary", "submitted_at")


class WorkOrder(WorkOrderRow):
    steps: list[StepOut]
    bills: list[BillLine]
    request: RequestOut | None


@router.get("/{work_order_id}")
def work_order(session: Staff, conn: Conn, work_order_id: UUID) -> WorkOrder:
    """A work order: where it stands, every step in order, the bills that name it and the
    tenant's request it answers, if one."""
    found = conn.execute(
        WORK_ORDERS.format(where="w.id = %(id)s"),
        {"pmc": session.pmc_id, "id": work_order_id, "per_page": 1, "offset": 0},
    ).fetchall()
    if not found:
        raise HTTPException(404, "no such work order")
    (row,) = rows_of(found)
    steps = [
        StepOut(step=step, vendor=vendor, taken_at=at, note=note)
        for step, vendor, at, note in conn.execute(
            "SELECT s.step, v.display_name, s.taken_at, s.note FROM trust_work_order_steps AS s"
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
        "SELECT r.id, t.display_name, r.summary, r.submitted_at"
        " FROM trust_maintenance_request_work_orders AS m"
        " JOIN trust_maintenance_requests AS r ON r.id = m.request_id"
        " JOIN trust_tenants AS t ON t.id = r.tenant_id WHERE m.work_order_id = %s",
        (work_order_id,),
    ).fetchone()
    return WorkOrder(
        **row.model_dump(),
        steps=steps,
        bills=bills,
        request=None if request is None else RequestOut(**dict(zip(REQUEST, request, strict=True))),
    )


Summary = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class NewWorkOrder(BaseModel):
    property_id: UUID
    unit_id: UUID | None = None
    summary: Summary


class Opened(BaseModel):
    id: UUID


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
    return Opened(id=row[0])
