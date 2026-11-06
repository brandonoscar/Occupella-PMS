"""Leasing → Renewals (ADR 0005): the leases whose last day is today or one of the 90 after it,
each with where its renewal stands (none, drafted, sent, signed, renewed), as
trust_report_lease_expirations has them, so the page, the Dashboard and the download agree.
Each names its lease when exactly one lease of that property and unit ends that day. Drafting a
renewal is the lease offers' (`offers.py`); the plan here (ADR 0010 phase 3, part L) gives the
Renew lease panel its starting terms, who signs, and the notice the property's state asks before a
rent increase (src/pms/rent_notice.py), so it can warn before sending. Nothing here moves money.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from psycopg import Connection
from pydantic import BaseModel

from pms import notices, rent_notice
from pms.api import addresses
from pms.api.deps import Conn, Staff
from pms.api.offers import Rent
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
        {"pmc": session.pmc_id, "on": on or today(conn, session.pmc_id)},
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


# --- a lease's renewal: its plan (ADR 0010 phase 3, part L) ---------------------------------------


class PlanSigner(BaseModel):
    tenant_id: UUID
    name: str
    # None: no email on file, so the renewal can't be sent to them.
    email: str | None


class RenewalNotice(BaseModel):
    state: str
    days: int
    law: str
    increase_percent: str
    # The first day the new rent may start if it is sent today.
    earliest: date
    # Said before sending when the new rent starts sooner; None when it gives the notice.
    warning: str | None


class RenewalPlan(BaseModel):
    lease_id: UUID
    property: str
    unit: str
    starts_on: date
    ends_on: date | None
    # The rent in force today, and the new rent asked (the same unless one was given).
    monthly_rent: str
    new_rent: str
    # The new term's first day: the one asked, or the day after the lease ends (month to month:
    # the first of next month).
    new_starts_on: date
    # The tenants on the lease today, who sign the renewal.
    signers: list[PlanSigner]
    # The notice the property's state asks before this increase; None without an increase or
    # without a rule recorded for its state (docs/law.md).
    notice: RenewalNotice | None


LEASE = """
SELECT l.unit_id, p.id, p.display_name, u.display_name, l.starts_on, l.ends_on,
       trust_rent_on(l.id, greatest(l.starts_on, least(%(day)s, coalesce(l.ends_on, %(day)s))))
FROM trust_leases AS l
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
WHERE l.pmc_id = %(pmc)s AND l.id = %(lease)s
"""

SIGNERS = """
SELECT t.id, t.display_name
FROM trust_lease_tenants AS lt
JOIN trust_tenants AS t ON t.id = lt.tenant_id
WHERE lt.lease_id = %(lease)s
  AND NOT EXISTS (SELECT 1 FROM trust_lease_departures AS d
                  WHERE d.lease_id = lt.lease_id AND d.tenant_id = lt.tenant_id
                    AND d.left_on <= %(day)s)
ORDER BY t.display_name COLLATE "C", t.id
"""

# The leases at the unit that share a tenant with this one (this one, and those it renewed or
# followed): how long the tenants have lived there, and the rents they were charged.
SAME_HOME = """
h.pmc_id = %(pmc)s AND h.unit_id = %(unit)s
AND EXISTS (SELECT 1 FROM trust_lease_tenants AS a
            JOIN trust_lease_tenants AS b ON b.tenant_id = a.tenant_id
            WHERE a.lease_id = h.id AND b.lease_id = %(lease)s)
"""

# The lowest rent those leases charged from `from` through `to`: each one's rent on its first day
# in the window and on each change of rent within it.
LOWEST = f"""
SELECT min(trust_rent_on(h.id, d.day))
FROM trust_leases AS h
CROSS JOIN LATERAL (
  SELECT greatest(%(from)s, h.starts_on) AS day
  UNION
  SELECT c.effective_on FROM trust_rent_changes AS c
  WHERE c.lease_id = h.id AND c.effective_on BETWEEN %(from)s AND %(to)s
) AS d
WHERE {SAME_HOME}
  AND h.starts_on <= %(to)s AND (h.ends_on IS NULL OR h.ends_on >= %(from)s)
  AND d.day <= least(%(to)s, coalesce(h.ends_on, %(to)s))
"""  # noqa: S608

LIVED_FROM = f"SELECT min(h.starts_on) FROM trust_leases AS h WHERE {SAME_HOME}"  # noqa: S608


def first_of_next_month(day: date) -> date:
    return (day.replace(day=1) + timedelta(days=32)).replace(day=1)


@dataclass(frozen=True)
class Home:
    lease_id: UUID
    unit: UUID
    property_id: UUID
    began: date
    ends_on: date | None


def notice_of(
    conn: Connection[Any],
    pmc: UUID,
    home: Home,
    now: Decimal,
    new_rent: Decimal,
    starts_on: date,
    sent_on: date,
) -> RenewalNotice | None:
    """The written notice the property's state asks before a new rent of `new_rent` (over `now`)
    starts on `starts_on`, the notice going out on `sent_on`; None without an increase or without
    a rule recorded for its state (docs/law.md, src/pms/rent_notice.py)."""
    address = addresses.address_of(conn, home.property_id)
    if address is None or address.state not in rent_notice.STATES:
        return None
    window = {
        "pmc": pmc,
        "unit": home.unit,
        "lease": home.lease_id,
        "from": starts_on - timedelta(days=365),
        "to": starts_on - timedelta(days=1),
    }
    row = conn.execute(LOWEST, window).fetchone()
    lowest = Decimal(row[0]) if row and row[0] is not None else now
    first = conn.execute(LIVED_FROM, window).fetchone()
    lived = rent_notice.whole_years(first[0] if first and first[0] else home.began, starts_on)
    term = (
        rent_notice.whole_years(home.began, home.ends_on + timedelta(days=1)) if home.ends_on else 0
    )
    rule = rent_notice.needed(address.state, now, lowest, new_rent, max(lived, term))
    if rule is None:
        return None
    return RenewalNotice(
        state=rule.state,
        days=rule.days,
        law=rule.law,
        increase_percent=str(rule.increase_percent),
        earliest=rent_notice.earliest(sent_on, rule),
        warning=rent_notice.warning(rule, sent_on, starts_on),
    )


@router.get("/{lease_id}/plan")
def renewal_plan(
    session: Staff,
    conn: Conn,
    lease_id: UUID,
    starts_on: Annotated[date | None, Query()] = None,
    monthly_rent: Annotated[Rent | None, Query()] = None,
) -> RenewalPlan:
    """What renewing a lease starts from: its rent now, the new term's first day, who signs, and
    the written notice the property's state asks before the new rent (ADR 0010 phase 3)."""
    day = today(conn, session.pmc_id)
    pmc = session.pmc_id
    found = conn.execute(LEASE, {"pmc": pmc, "lease": lease_id, "day": day}).fetchone()
    if found is None:
        raise HTTPException(404, "no such lease")
    unit, property_id, prop, unit_name, began, ends_on, now = found
    new_starts = starts_on or (ends_on + timedelta(days=1) if ends_on else first_of_next_month(day))
    new_rent = Decimal(monthly_rent) if monthly_rent is not None else Decimal(now)
    signers = [
        PlanSigner(tenant_id=t, name=name, email=notices.address(conn, pmc, "tenant", t))
        for t, name in conn.execute(SIGNERS, {"lease": lease_id, "day": day}).fetchall()
    ]
    home = Home(lease_id, unit, property_id, began, ends_on)
    notice = notice_of(conn, pmc, home, Decimal(now), new_rent, new_starts, day)
    return RenewalPlan(
        lease_id=lease_id,
        property=prop,
        unit=unit_name,
        starts_on=began,
        ends_on=ends_on,
        monthly_rent=cents(Decimal(now)),
        new_rent=cents(new_rent),
        new_starts_on=new_starts,
        signers=signers,
        notice=notice,
    )


@router.get("/{lease_id}/rent-change-notice")
def rent_change_notice(
    session: Staff,
    conn: Conn,
    lease_id: UUID,
    effective_on: Annotated[date, Query()],
    monthly_rent: Annotated[Rent, Query()],
    notice_given_on: Annotated[date, Query()],
) -> RenewalNotice | None:
    """The written notice the property's state asks before a rent change recorded on the rent page
    (docs/edge-cases.md D-065, D-066), counted from the day notice was given: the Change rent panel
    warns before it is recorded when the new rent starts sooner. A warning only, as every state
    rule here is (q142); the database still asks a full month."""
    found = conn.execute(
        LEASE, {"pmc": session.pmc_id, "lease": lease_id, "day": effective_on - timedelta(days=1)}
    ).fetchone()
    if found is None:
        raise HTTPException(404, "no such lease")
    unit, property_id, _, _, began, ends_on, now = found
    home = Home(lease_id, unit, property_id, began, ends_on)
    return notice_of(
        conn,
        session.pmc_id,
        home,
        Decimal(now),
        Decimal(monthly_rent),
        effective_on,
        notice_given_on,
    )
