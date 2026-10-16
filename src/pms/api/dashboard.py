"""The staff Dashboard's figures (/): what tenants owe, open work orders, leases ending, vacant
units and applications waiting, each as its own report counts it (ADR 0005). What needs
someone today stays the Today answer (src/pms/api/today.py); the Dashboard shows both.

Read only, and counted on the server from the reports themselves, so a company with thousands
of units sends a handful of numbers.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from pms import exports
from pms.api import rent
from pms.api.deps import Conn, Staff

router = APIRouter(prefix="/api/dashboard")


class Ending(BaseModel):
    """Leases whose last day is within the days given (as the lease expirations report groups
    them): 0 to 30, 31 to 60 and 61 to 90."""

    within_30: int
    within_60: int
    within_90: int


class Dashboard(BaseModel):
    on: date
    # Everything due by the day and not paid, over every lease (the delinquency report's total),
    # and how many leases owe it.
    outstanding: str
    leases_owing: int
    # Work orders not completed or cancelled on the day.
    open_work_orders: int
    leases_ending: Ending
    # Units with no lease in effect on the day.
    vacant_units: int
    # Applications not decided yet.
    applications_waiting: int


@router.get("")
def dashboard(session: Staff, conn: Conn, on: Annotated[date | None, Query()] = None) -> Dashboard:
    """The Dashboard's figures on the day (today, UTC, by default)."""
    day = on or rent.today()
    pmc = session.pmc_id
    total, owing = conn.execute(
        "SELECT coalesce(sum(total) FILTER (WHERE item = 'total'), 0),"
        " count(*) FILTER (WHERE item = 'lease')"
        " FROM trust_report_delinquency(%s, %s)",
        (pmc, day),
    ).fetchone() or (Decimal(0), 0)
    (work_orders,) = conn.execute(
        "SELECT count(*) FROM trust_report_work_orders(%s, %s)"
        " WHERE status IS NOT NULL AND status NOT IN ('completed', 'cancelled')",
        (pmc, day),
    ).fetchone() or (0,)
    buckets = dict(
        conn.execute(
            "SELECT bucket, count(*) FROM trust_report_lease_expirations(%s, %s)"
            " WHERE item = 'lease' GROUP BY bucket",
            (pmc, day),
        ).fetchall()
    )
    (vacant,) = conn.execute(
        "SELECT count(*) FROM trust_report_vacancy(%s, %s) WHERE item = 'vacant'", (pmc, day)
    ).fetchone() or (0,)
    (waiting,) = conn.execute(
        "SELECT count(*) FROM trust_applications AS a WHERE a.pmc_id = %s"
        " AND NOT EXISTS (SELECT 1 FROM trust_application_decisions AS d"
        "                 WHERE d.application_id = a.id)",
        (pmc,),
    ).fetchone() or (0,)
    return Dashboard(
        on=day,
        outstanding=exports.text(Decimal(total).quantize(Decimal("0.01"))),
        leases_owing=owing,
        open_work_orders=work_orders,
        leases_ending=Ending(
            within_30=buckets.get("0-30", 0),
            within_60=buckets.get("31-60", 0),
            within_90=buckets.get("61-90", 0),
        ),
        vacant_units=vacant,
        applications_waiting=waiting,
    )
