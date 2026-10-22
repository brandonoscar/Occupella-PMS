"""Tax credit (LIHTC) compliance (migration 0123; the founder's review q143, docs/law.md): a
property's area income limits at 50% of median from a day, a unit's designation from a day (the
percent of median its household's income may reach, its bedrooms, the utility allowance), and
where each tax credit unit stands: its household's certification, the income test and whether the
rent with the allowance is under the most it may be.

The company enters the limits each year from its area's published table; the PMS looks nothing up
and files nothing with the state agency or the IRS. A household's certification is recorded as a
housing-assistance lease's is (src/pms/api/assistance.py), and its income is never printed here.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pms import exports
from pms.api import rent
from pms.api.deps import Conn, Staff
from pms.api.setup import Created, Money, found, insert, refusals

router = APIRouter(prefix="/api/tax-credit")

Limit = Annotated[Decimal, Field(gt=0, lt=Decimal("10000000"), decimal_places=2)]
IncomePercent = Literal[20, 30, 40, 50, 60, 70, 80]


def money(value: Decimal | None) -> str | None:
    return None if value is None else exports.text(value)


class Limits(BaseModel):
    starts_on: date
    limits: list[str]  # households of one to eight, at 50% of median


class NewLimits(BaseModel):
    property_id: UUID
    starts_on: date
    limits: list[Limit] = Field(min_length=8, max_length=8)


@router.post("/limits", status_code=201)
def set_limits(session: Staff, conn: Conn, new: NewLimits) -> Created:
    """A property's area income limits at 50% of median for households of one to eight, as its
    area's table gives them, from a day; each other percent is in proportion."""
    found(conn, "property", session.pmc_id, new.property_id)
    with refusals("this property's income limits change on that day already"):
        return Created(
            id=insert(
                conn,
                "INSERT INTO trust_tax_credit_limits (pmc_id, property_id, starts_on, limits)"
                " VALUES (%s, %s, %s, %s::numeric[]) RETURNING id",
                (session.pmc_id, new.property_id, new.starts_on, new.limits),
            )
        )


@router.get("/properties/{property_id}")
def property_limits(session: Staff, conn: Conn, property_id: UUID) -> list[Limits]:
    """A property's income limits, the latest from a day first."""
    found(conn, "property", session.pmc_id, property_id)
    return [
        Limits(starts_on=starts, limits=[exports.text(v) for v in values])
        for starts, values in conn.execute(
            "SELECT starts_on, limits FROM trust_tax_credit_limits"
            " WHERE pmc_id = %s AND property_id = %s ORDER BY starts_on DESC",
            (session.pmc_id, property_id),
        ).fetchall()
    ]


class Designation(BaseModel):
    starts_on: date
    # None: not a tax credit unit from that day.
    income_percent: IncomePercent | None
    bedrooms: int | None
    utility_allowance: str | None


class NewDesignation(BaseModel):
    unit_id: UUID
    starts_on: date
    income_percent: IncomePercent | None
    bedrooms: Annotated[int, Field(ge=0, le=6)] | None = None
    utility_allowance: Money | None = None


@router.post("/units", status_code=201)
def designate(session: Staff, conn: Conn, new: NewDesignation) -> Created:
    """A unit's tax credit designation from a day: the percent of median its household's income
    may reach, its bedrooms and the utility allowance; with no percent, it is not a tax credit
    unit from that day."""
    found(conn, "unit", session.pmc_id, new.unit_id)
    if (new.income_percent is None) != (new.bedrooms is None) or (new.income_percent is None) != (
        new.utility_allowance is None
    ):
        raise HTTPException(
            422,
            "a tax credit unit names its percent of median, bedrooms and utility allowance;"
            " ending one names none",
        )
    with refusals("this unit's designation changes on that day already"):
        return Created(
            id=insert(
                conn,
                "INSERT INTO trust_tax_credit_units"
                " (pmc_id, unit_id, starts_on, income_percent, bedrooms, utility_allowance)"
                " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    session.pmc_id,
                    new.unit_id,
                    new.starts_on,
                    new.income_percent,
                    new.bedrooms,
                    new.utility_allowance,
                ),
            )
        )


class UnitTaxCredit(BaseModel):
    # In force today (None: not a tax credit unit), with the most its gross rent may be.
    in_force: Designation | None
    max_gross_rent: str | None
    # Every designation, the latest from a day first.
    designations: list[Designation]


@router.get("/units/{unit_id}")
def unit_tax_credit(session: Staff, conn: Conn, unit_id: UUID) -> UnitTaxCredit:
    found(conn, "unit", session.pmc_id, unit_id)
    rows = conn.execute(
        "SELECT starts_on, income_percent, bedrooms, utility_allowance"
        " FROM trust_tax_credit_units WHERE pmc_id = %s AND unit_id = %s ORDER BY starts_on DESC",
        (session.pmc_id, unit_id),
    ).fetchall()
    designations = [
        Designation(starts_on=s, income_percent=p, bedrooms=b, utility_allowance=money(a))
        for s, p, b, a in rows
    ]
    day = rent.today()
    current = next((d for d in designations if d.starts_on <= day), None)
    if current is None or current.income_percent is None:
        return UnitTaxCredit(in_force=None, max_gross_rent=None, designations=designations)
    row = conn.execute(
        "SELECT trust_tax_credit_rent_limit(u.property_id, %s, %s, %s)"
        " FROM trust_units AS u WHERE u.id = %s",
        (day, current.income_percent, current.bedrooms, unit_id),
    ).fetchone()
    assert row is not None
    return UnitTaxCredit(in_force=current, max_gross_rent=money(row[0]), designations=designations)


class LeaseTaxCredit(BaseModel):
    """Where a tax credit unit's lease stands today, as the compliance report says it."""

    income_percent: int
    bedrooms: int
    certification: str
    income_test: str
    max_gross_rent: str | None
    gross_rent: str | None
    rent_test: str


@router.get("/leases/{lease_id}")
def lease_tax_credit(session: Staff, conn: Conn, lease_id: UUID) -> LeaseTaxCredit | None:
    """Where the lease stands as its unit's tax credit compliance today; nothing for a lease
    whose unit is not a tax credit unit today or which is not in effect today."""
    rent.lease_of(conn, session.pmc_id, lease_id)
    row = conn.execute(
        "SELECT r.income_percent, r.bedrooms, r.certification, r.income_test,"
        " r.max_gross_rent, r.gross_rent, r.rent_test"
        " FROM trust_leases AS l"
        " JOIN trust_report_tax_credit_compliance(%s, %s) AS r ON r.unit_id = l.unit_id"
        " WHERE l.id = %s AND r.item = 'unit' AND r.tenants IS NOT NULL"
        " AND l.starts_on <= %s AND (l.ends_on IS NULL OR l.ends_on >= %s)",
        (session.pmc_id, rent.today(), lease_id, rent.today(), rent.today()),
    ).fetchone()
    if row is None:
        return None
    percent, bedrooms, standing, income_test, most, gross, rent_test = row
    return LeaseTaxCredit(
        income_percent=percent,
        bedrooms=bedrooms,
        certification=standing,
        income_test=income_test,
        max_gross_rent=money(most),
        gross_rent=money(gross),
        rent_test=rent_test,
    )
