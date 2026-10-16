"""Leasing → Renewals (ADR 0005): the leases whose last day is today or one of the 90 after it,
each with where its renewal stands (none, drafted, sent, signed, renewed), as
trust_report_lease_expirations has them, so the page, the Dashboard and the download agree.
Each names its lease when exactly one lease of that property and unit ends that day. Drafting a
renewal is the lease offers' (`offers.py`). Nothing here moves money.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from pms.api.deps import Conn, Staff
from pms.api.rent import cents, today

router = APIRouter(prefix="/api/leasing/renewals")


class Expiring(BaseModel):
    lease_id: UUID | None
    property: str
    unit: str
    tenants: str
    ends_on: date
    days_left: int
    bucket: Literal["0-30", "31-60", "61-90"]
    monthly_rent: str
    renewal: Literal["none", "drafted", "sent", "signed", "renewed"]


@router.get("")
def renewals(
    session: Staff, conn: Conn, on: Annotated[date | None, Query()] = None
) -> list[Expiring]:
    """Each lease ending in the next 90 days (from today, UTC, by default), soonest first."""
    rows = conn.execute(
        "SELECT r.property, r.unit, r.tenants, r.ends_on, r.days_left, r.bucket,"
        " r.monthly_rent, r.renewal,"
        " (SELECT CASE WHEN count(*) = 1 THEN min(l.id::text)::uuid END"
        "  FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id"
        "  JOIN trust_properties AS p ON p.id = u.property_id"
        "  WHERE l.pmc_id = %(pmc)s AND l.ends_on = r.ends_on AND u.display_name = r.unit"
        "    AND p.display_name = r.property)"
        " FROM trust_report_lease_expirations(%(pmc)s, %(on)s) AS r"
        " WHERE r.item = 'lease' ORDER BY r.line",
        {"pmc": session.pmc_id, "on": on or today()},
    ).fetchall()
    return [
        Expiring(
            lease_id=lease,
            property=prop,
            unit=unit,
            tenants=tenants,
            ends_on=ends,
            days_left=left,
            bucket=bucket,
            monthly_rent=cents(Decimal(rent)),
            renewal=renewal,
        )
        for prop, unit, tenants, ends, left, bucket, rent, renewal, lease in rows
    ]
