"""Utility billing, RUBS and submeters (migration 0096, the founder's review q021; docs/law.md):
a property's plan for each utility (by unit, by square feet, by occupants or by submeter, less
the owner's share), its units' square feet, which utilities each lease is billed for, and its
utility bills billed back to the leases (trust_bill_back_utility), each billed lease's share a
fee charged to it. A preview shows the shares before anything is charged.

What the tenants are charged never comes to more than the bill less the owner's share: a vacant
unit's days, a lease not billed for the utility, the common areas and each part of a cent stay
with the owner. The PMS charges the leases; it sends nothing to the utility.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, StringConstraints

from pms.api import rent
from pms.api.deps import Conn, Staff, refusal

router = APIRouter(prefix="/api/utilities")

Utility = Literal["water", "sewer", "trash", "gas", "electric", "other"]
Method = Literal["units", "square_feet", "occupants", "submeter"]
Percent = Annotated[Decimal, Field(ge=0, le=100, decimal_places=2)]
Reading = Annotated[Decimal, Field(ge=0, le=Decimal("1000000000"), decimal_places=4)]
Master = Annotated[Decimal, Field(gt=0, le=Decimal("1000000000"), decimal_places=4)]
Memo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
ZERO = Decimal(0)


class Created(BaseModel):
    id: UUID


def property_named(conn: psycopg.Connection[Any], pmc_id: UUID, property_id: UUID) -> str:
    row = conn.execute(
        "SELECT display_name FROM trust_properties WHERE pmc_id = %s AND id = %s",
        (pmc_id, property_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such property")
    return str(row[0])


# --- a property's utility billing ----------------------------------------------------------------


class Plan(BaseModel):
    id: UUID
    utility: Utility
    starts_on: date
    # 'none': the property stopped billing the utility back from that day.
    method: Method | Literal["none"]
    owner_percent: str
    recorded_by: str | None


class UnitArea(BaseModel):
    unit_id: UUID
    unit: str
    square_feet: int | None


class BilledLease(BaseModel):
    lease_id: UUID
    # "Morgan Demo, Birch Flats Unit A"
    home: str
    starts_on: date
    ends_on: date | None
    # The utilities it is billed for today (or from its first day, one to come).
    billed: list[Utility]


class Share(BaseModel):
    lease_id: UUID
    home: str
    billed: bool
    weight: str
    usage: str | None
    amount: str


class UtilityBill(BaseModel):
    id: UUID
    utility: Utility
    period_start: date
    period_end: date
    amount: str
    method: Method
    owner_percent: str
    master_usage: str | None
    due_on: date
    memo: str | None
    # What the leases were charged, and what stayed with the owner.
    charged: str
    owner_keeps: str
    shares: list[Share]


class PropertyUtilities(BaseModel):
    property: str
    plans: list[Plan]
    units: list[UnitArea]
    leases: list[BilledLease]
    bills: list[UtilityBill]


PLANS = """
SELECT p.id, p.utility, p.starts_on, p.method, p.owner_percent, s.display_name
FROM trust_utility_plans AS p
LEFT JOIN trust_staff AS s ON s.id = p.recorded_by
WHERE p.pmc_id = %s AND p.property_id = %s
ORDER BY p.utility COLLATE "C", p.starts_on DESC, p.created_at DESC
"""

UNITS = """
SELECT u.id, u.display_name, trust_unit_area(u.id)
FROM trust_units AS u
WHERE u.pmc_id = %s AND u.property_id = %s AND NOT trust_is_archived(NULL, u.id)
ORDER BY u.display_name COLLATE "C", u.id
"""

LEASES = """
SELECT l.id, l.starts_on, l.ends_on,
       ARRAY(SELECT x.utility FROM unnest(
                 ARRAY['water', 'sewer', 'trash', 'gas', 'electric', 'other']) AS x (utility)
             WHERE trust_lease_billed(l.id, x.utility, greatest(%(day)s, l.starts_on)))
FROM trust_leases AS l
JOIN trust_units AS u ON u.id = l.unit_id
WHERE l.pmc_id = %(pmc)s AND u.property_id = %(property)s
  AND (l.ends_on IS NULL OR l.ends_on >= %(day)s)
ORDER BY u.display_name COLLATE "C", l.starts_on, l.id
"""

BILLS = """
SELECT b.id, b.utility, b.period_start, b.period_end, b.amount, b.method, b.owner_percent,
       b.master_usage, b.due_on, b.memo
FROM trust_utility_bills AS b
WHERE b.pmc_id = %s AND b.property_id = %s
ORDER BY b.period_start DESC, b.utility COLLATE "C", b.id
"""

SHARES = """
SELECT s.lease_id, s.billed, s.weight, s.usage, s.amount
FROM trust_utility_shares AS s
WHERE s.utility_bill_id = %s
"""


def text(value: Decimal | None) -> str | None:
    """A weight or reading as text, without trailing zeros: never a float."""
    if value is None:
        return None
    normal = Decimal(value).normalize()
    return format(normal, "f")


def shares_of(conn: psycopg.Connection[Any], rows: list[tuple[Any, ...]]) -> list[Share]:
    shares = [
        Share(
            lease_id=lease,
            home=rent.lease_memo(conn, lease),
            billed=billed,
            weight=text(weight) or "0",
            usage=text(usage),
            amount=rent.cents(amount),
        )
        for lease, billed, weight, usage, amount in rows
    ]
    return sorted(shares, key=lambda share: (share.home, str(share.lease_id)))


@router.get("/properties/{property_id}")
def property_utilities(session: Staff, conn: Conn, property_id: UUID) -> PropertyUtilities:
    """A property's utility billing: its plans, its units' square feet, its leases and the
    utilities each is billed for, and its utility bills with each lease's share."""
    name = property_named(conn, session.pmc_id, property_id)
    plans = [
        Plan(
            id=i,
            utility=utility,
            starts_on=starts,
            method=method,
            owner_percent=text(percent) or "0",
            recorded_by=by,
        )
        for i, utility, starts, method, percent, by in conn.execute(
            PLANS, (session.pmc_id, property_id)
        ).fetchall()
    ]
    units = [
        UnitArea(unit_id=i, unit=unit, square_feet=area)
        for i, unit, area in conn.execute(UNITS, (session.pmc_id, property_id)).fetchall()
    ]
    leases = [
        BilledLease(
            lease_id=i,
            home=rent.lease_memo(conn, i),
            starts_on=starts,
            ends_on=ends,
            billed=billed,
        )
        for i, starts, ends, billed in conn.execute(
            LEASES, {"pmc": session.pmc_id, "property": property_id, "day": rent.today()}
        ).fetchall()
    ]
    bills = []
    for i, utility, start, end, amount, method, percent, master, due, memo in conn.execute(
        BILLS, (session.pmc_id, property_id)
    ).fetchall():
        shares = shares_of(conn, conn.execute(SHARES, (i,)).fetchall())
        charged = sum((Decimal(share.amount) for share in shares), ZERO)
        bills.append(
            UtilityBill(
                id=i,
                utility=utility,
                period_start=start,
                period_end=end,
                amount=rent.cents(amount),
                method=method,
                owner_percent=text(percent) or "0",
                master_usage=text(master),
                due_on=due,
                memo=memo,
                charged=rent.cents(charged),
                owner_keeps=rent.cents(Decimal(amount) - charged),
                shares=shares,
            )
        )
    return PropertyUtilities(property=name, plans=plans, units=units, leases=leases, bills=bills)


class NewPlan(BaseModel):
    utility: Utility
    starts_on: date
    method: Method | Literal["none"]
    # The common areas' share, which the owner keeps.
    owner_percent: Percent = ZERO


@router.post("/properties/{property_id}/plans", status_code=201)
def set_plan(session: Staff, conn: Conn, property_id: UUID, new: NewPlan) -> Created:
    """How the property bills a utility back from a day; a later one takes over from its day,
    and 'none' stops it."""
    property_named(conn, session.pmc_id, property_id)
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO trust_utility_plans (pmc_id, property_id, utility, starts_on, method,"
                " owner_percent) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    session.pmc_id,
                    property_id,
                    new.utility,
                    new.starts_on,
                    new.method,
                    new.owner_percent,
                ),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            409, f"the property has a {new.utility} plan from that day already"
        ) from None
    assert row is not None
    return Created(id=row[0])


class NewArea(BaseModel):
    square_feet: Annotated[int, Field(gt=0, le=1000000)]


@router.post("/units/{unit_id}/area", status_code=201)
def set_area(session: Staff, conn: Conn, unit_id: UUID, new: NewArea) -> Created:
    """A unit's square feet, for a utility billed by square feet."""
    if not conn.execute(
        "SELECT 1 FROM trust_units WHERE pmc_id = %s AND id = %s", (session.pmc_id, unit_id)
    ).fetchone():
        raise HTTPException(404, "no such unit")
    row = conn.execute(
        "INSERT INTO trust_unit_areas (pmc_id, unit_id, square_feet) VALUES (%s, %s, %s)"
        " RETURNING id",
        (session.pmc_id, unit_id, new.square_feet),
    ).fetchone()
    assert row is not None
    return Created(id=row[0])


class NewBilled(BaseModel):
    utility: Utility
    billed: bool
    starts_on: date


@router.post("/leases/{lease_id}/billed", status_code=201)
def set_billed(session: Staff, conn: Conn, lease_id: UUID, new: NewBilled) -> Created:
    """Whether a lease is billed for a utility from a day: as the lease says."""
    rent.lease_of(conn, session.pmc_id, lease_id)
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO trust_lease_utilities (pmc_id, lease_id, utility, starts_on, billed)"
                " VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (session.pmc_id, lease_id, new.utility, new.starts_on, new.billed),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            409, f"the lease's {new.utility} billing changes on that day already"
        ) from None
    assert row is not None
    return Created(id=row[0])


# --- a bill -----------------------------------------------------------------------------------


class MeterReading(BaseModel):
    lease_id: UUID
    usage: Reading


class BillAsked(BaseModel):
    property_id: UUID
    utility: Utility
    period_start: date
    period_end: date
    amount: rent.Amount
    # By submeter: the master meter's reading for the period, and each lease's unit's.
    master_usage: Master | None = None
    usage: Annotated[list[MeterReading], Field(max_length=1000)] = []


class Preview(BaseModel):
    method: Method
    owner_percent: str
    charged: str
    owner_keeps: str
    shares: list[Share]


ALLOCATION = """
SELECT a.lease_id, a.billed, a.weight, a.usage, a.amount
FROM trust_utility_allocation(%s, %s, %s, %s, %s, %s, %s, %s) AS a
"""


def asked_usage(conn: psycopg.Connection[Any], pmc_id: UUID, asked: BillAsked) -> Jsonb:
    """The readings by lease, each a lease of the property in effect in the period, once."""
    leases = [reading.lease_id for reading in asked.usage]
    if len(set(leases)) != len(leases):
        raise HTTPException(422, "each lease has one reading")
    known = {
        row[0]
        for row in conn.execute(
            "SELECT w.lease_id FROM trust_utility_weights(%s, %s, %s, %s, %s, 'units', NULL) AS w",
            (pmc_id, asked.property_id, asked.utility, asked.period_start, asked.period_end),
        ).fetchall()
    }
    if any(lease not in known for lease in leases):
        raise HTTPException(
            422, "a reading names a lease that isn't the property's in the bill's period"
        )
    return Jsonb({str(r.lease_id): str(r.usage) for r in asked.usage})


def allocation(conn: psycopg.Connection[Any], pmc_id: UUID, asked: BillAsked) -> Preview:
    property_named(conn, pmc_id, asked.property_id)
    if asked.period_end < asked.period_start:
        raise HTTPException(422, "the bill's period ends on or after its first day")
    usage = asked_usage(conn, pmc_id, asked)
    try:
        with conn.transaction():
            rows = conn.execute(
                ALLOCATION,
                (
                    pmc_id,
                    asked.property_id,
                    asked.utility,
                    asked.period_start,
                    asked.period_end,
                    asked.amount,
                    asked.master_usage,
                    usage,
                ),
            ).fetchall()
    except (psycopg.errors.CheckViolation, psycopg.errors.InvalidParameterValue) as refused:
        raise HTTPException(409, refusal(refused)) from None
    plan = conn.execute(
        "SELECT method, owner_percent FROM trust_utility_plan_on(%s, %s, %s)",
        (asked.property_id, asked.utility, asked.period_start),
    ).fetchone()
    assert plan is not None
    shares = shares_of(conn, rows)
    charged = sum((Decimal(share.amount) for share in shares), ZERO)
    return Preview(
        method=plan[0],
        owner_percent=text(plan[1]) or "0",
        charged=rent.cents(charged),
        owner_keeps=rent.cents(asked.amount - charged),
        shares=shares,
    )


@router.post("/preview")
def preview(session: Staff, conn: Conn, asked: BillAsked) -> Preview:
    """How a utility bill would be shared among the property's leases, before anything is
    charged: each lease's weight and share, and what the owner keeps."""
    return allocation(conn, session.pmc_id, asked)


class NewUtilityBill(BillAsked):
    due_on: date
    memo: Memo | None = None
    # The vendor's bill for it, when it was entered as one (Bills).
    bill_id: UUID | None = None


class BilledBack(Preview):
    id: UUID


@router.post("/bills", status_code=201)
def bill_back(session: Staff, conn: Conn, new: NewUtilityBill) -> BilledBack:
    """Bills a utility bill back to the property's leases, once: each billed lease's share a fee
    charged to it, due on the day given. No two of the property's bills for a utility cover the
    same day."""
    shared = allocation(conn, session.pmc_id, new)
    if (
        new.bill_id is not None
        and not conn.execute(
            "SELECT 1 FROM trust_bills AS b"
            " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = b.ledger_account_id"
            " WHERE b.pmc_id = %s AND b.id = %s AND a.property_id = %s",
            (session.pmc_id, new.bill_id, new.property_id),
        ).fetchone()
    ):
        raise HTTPException(422, "the vendor's bill isn't one of this property's")
    if (
        new.bill_id is not None
        and conn.execute(
            "SELECT 1 FROM trust_utility_bills WHERE bill_id = %s", (new.bill_id,)
        ).fetchone()
    ):
        raise HTTPException(409, "that vendor's bill is billed back already")
    try:
        with conn.transaction():
            row = conn.execute(
                "SELECT trust_bill_back_utility(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    session.pmc_id,
                    new.property_id,
                    new.utility,
                    new.period_start,
                    new.period_end,
                    new.amount,
                    new.due_on,
                    new.memo,
                    new.master_usage,
                    asked_usage(conn, session.pmc_id, new),
                    new.bill_id,
                ),
            ).fetchone()
    except psycopg.errors.UniqueViolation as refused:
        raise HTTPException(409, refusal(refused)) from None
    assert row is not None
    return BilledBack(id=row[0], **shared.model_dump())
