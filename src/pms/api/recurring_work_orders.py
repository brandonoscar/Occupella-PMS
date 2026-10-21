"""Recurring work orders (migration 0085; the founder's review, q073): a job that comes back every
few months at a property or unit (filters, gutters, a yearly inspection), from a day, on a day of
the month, maybe with its vendor. The nightly run opens each one due (src/pms/nightly.py); adding
one opens any already due through today. An end stops it from a day, once.

Opening goes through trust_open_recurring_work_orders: once per recurring work order and day, with
its priority, and its vendor assigned unless their insurance ran out. Nothing here moves money.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from pms.api.deps import Conn, Staff
from pms.api.rent import today
from pms.api.work_orders import Priority, every

router = APIRouter(prefix="/api/work-orders/recurring")

Summary = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]

RECURRING = """
SELECT r.id, p.id, p.display_name, u.id, u.display_name, r.summary, r.every_months,
       r.day_of_month, r.starts_on, r.priority, v.display_name, e.ends_on,
       (SELECT max(w.recurring_on) FROM trust_work_orders AS w
        WHERE w.recurring_work_order_id = r.id)
FROM trust_recurring_work_orders AS r
JOIN trust_properties AS p ON p.id = r.property_id
LEFT JOIN trust_units AS u ON u.id = r.unit_id
LEFT JOIN trust_vendors AS v ON v.id = r.vendor_id
LEFT JOIN trust_recurring_work_order_ends AS e ON e.recurring_work_order_id = r.id
WHERE r.pmc_id = %s
ORDER BY p.display_name COLLATE "C", r.summary COLLATE "C", r.id
"""


class Recurring(BaseModel):
    id: UUID
    property_id: UUID
    property: str
    unit_id: UUID | None
    unit: str | None
    summary: str
    every_months: int
    # "every 3 months on the 15th"
    schedule: str
    day_of_month: int
    starts_on: date
    priority: Priority
    vendor: str | None
    ends_on: date | None
    # The day of the last work order it opened, if any.
    last_opened_on: date | None


class NewRecurring(BaseModel):
    property_id: UUID
    unit_id: UUID | None = None
    summary: Summary
    every_months: int = Field(ge=1, le=12)
    day_of_month: int = Field(ge=1, le=28)
    starts_on: date
    priority: Priority = "normal"
    vendor_id: UUID | None = None


class Added(BaseModel):
    id: UUID
    # Work orders it opened at once, already due through today.
    opened: int


class End(BaseModel):
    ends_on: date


def ordinal(day: int) -> str:
    """1st, 2nd, 3rd, 4th... 21st, 22nd, 23rd, 28th."""
    suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def listed(conn: psycopg.Connection[Any], pmc_id: UUID) -> list[Recurring]:
    return [
        Recurring(
            id=i,
            property_id=p,
            property=pn,
            unit_id=u,
            unit=un,
            summary=summary,
            every_months=months,
            schedule=f"{every(months)} on the {ordinal(day)}",
            day_of_month=day,
            starts_on=starts,
            priority=priority,
            vendor=vendor,
            ends_on=ends,
            last_opened_on=last,
        )
        for (
            i,
            p,
            pn,
            u,
            un,
            summary,
            months,
            day,
            starts,
            priority,
            vendor,
            ends,
            last,
        ) in conn.execute(RECURRING, (pmc_id,)).fetchall()
    ]


@router.get("")
def recurring_work_orders(session: Staff, conn: Conn) -> list[Recurring]:
    """The company's recurring work orders, by property, with when each last opened one."""
    return listed(conn, session.pmc_id)


@router.post("", status_code=201)
def add_recurring(session: Staff, conn: Conn, new: NewRecurring) -> Added:
    """A job that comes back; any work order of it already due through today opens now."""
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO trust_recurring_work_orders (pmc_id, property_id, unit_id, summary,"
                " every_months, day_of_month, starts_on, priority, vendor_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    session.pmc_id,
                    new.property_id,
                    new.unit_id,
                    new.summary,
                    new.every_months,
                    new.day_of_month,
                    new.starts_on,
                    new.priority,
                    new.vendor_id,
                ),
            ).fetchone()
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(404, "no such property, unit or vendor in this company") from None
    except psycopg.errors.CheckViolation:
        # A unit of another property (trust_check_work_order).
        raise HTTPException(422, "that unit is not one of the property's") from None
    assert row is not None
    opened = conn.execute(
        "SELECT trust_open_recurring_work_orders(%s, %s)", (session.pmc_id, today())
    ).fetchone()
    assert opened is not None
    return Added(id=row[0], opened=opened[0])


@router.post("/{recurring_id}/end", status_code=204)
def end_recurring(session: Staff, conn: Conn, recurring_id: UUID, end: End) -> None:
    """Stops it from a day: no work order of it opens that day or after. Once."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_recurring_work_orders WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, recurring_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such recurring work order")
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_recurring_work_order_ends"
                " (recurring_work_order_id, pmc_id, ends_on) VALUES (%s, %s, %s)",
                (recurring_id, session.pmc_id, end.ends_on),
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "this recurring work order has ended already") from None
