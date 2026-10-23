"""A unit's details (migration 0124, the founder's regroup of 10/22): its bedrooms, bathrooms (in
halves), square feet and market rent, the rent it would ask today. Staff keep them on the unit's
page (area setup); the unit's property page, the rent roll, Listings and the public vacancy page
show them, and the vacancy report has them as recorded by the end of its day.

Each save is a row of `trust_unit_details` naming who recorded it, the latest holding; the same
details again write nothing, and a save with nothing in it clears them. They decide nothing and
move no money.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from pms.api import rent
from pms.api.deps import Conn, Staff
from pms.api.setup import Money

router = APIRouter(prefix="/api")

Bedrooms = Annotated[int, Field(ge=0, le=20)]
Bathrooms = Annotated[Decimal, Field(ge=0, le=20)]  # in halves, checked below
SquareFeet = Annotated[int, Field(ge=1, le=100_000)]


class UnitDetails(BaseModel):
    """What a unit has, each None when not recorded; money and bathrooms as text."""

    bedrooms: int | None = None
    bathrooms: str | None = None
    square_feet: int | None = None
    market_rent: str | None = None


class NewUnitDetails(BaseModel):
    bedrooms: Bedrooms | None = None
    bathrooms: Bathrooms | None = None
    square_feet: SquareFeet | None = None
    market_rent: Money | None = None

    @field_validator("bathrooms")
    @classmethod
    def in_halves(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (value * 2) % 1 != 0:
            raise ValueError("bathrooms count in halves: 1, 1.5, 2")
        return value


def baths(value: Decimal) -> str:
    """Bathrooms as they read: 1, 1.5, 20 (never 1.0 or 2E+1)."""
    return str(int(value)) if value == value.to_integral_value() else format(value, ".1f")


def shown(
    bedrooms: int | None,
    bathrooms: Decimal | None,
    square_feet: int | None,
    market_rent: Decimal | None,
) -> UnitDetails:
    """A row's details as the API gives them: bathrooms as they read, money to the cent."""
    return UnitDetails(
        bedrooms=bedrooms,
        bathrooms=None if bathrooms is None else baths(bathrooms),
        square_feet=square_feet,
        market_rent=None if market_rent is None else rent.cents(market_rent),
    )


# The latest details of each unit (on u), as one row's four columns; NULLs with none.
LATEST = """
LEFT JOIN LATERAL (
    SELECT d.bedrooms, d.bathrooms, d.square_feet, d.market_rent FROM trust_unit_details AS d
    WHERE d.unit_id = u.id ORDER BY d.created_at DESC, d.id DESC LIMIT 1
) AS ud ON true
"""
COLUMNS = "ud.bedrooms, ud.bathrooms, ud.square_feet, ud.market_rent"


def details_of(conn: psycopg.Connection[Any], unit_ids: list[UUID]) -> dict[UUID, UnitDetails]:
    """The latest details of each of these units; one with none reads all None."""
    found = {
        unit: shown(beds, half_baths, size, market)
        for unit, beds, half_baths, size, market in conn.execute(
            "SELECT DISTINCT ON (unit_id) unit_id, bedrooms, bathrooms, square_feet, market_rent"
            " FROM trust_unit_details WHERE unit_id = ANY(%s)"
            " ORDER BY unit_id, created_at DESC, id DESC",
            (unit_ids,),
        ).fetchall()
    }
    return {unit: found.get(unit, UnitDetails()) for unit in unit_ids}


class Changed(BaseModel):
    changed: bool


def record_details(
    conn: psycopg.Connection[Any], pmc_id: UUID, unit_id: UUID, new: NewUnitDetails
) -> bool:
    """Records a unit's details unless they are what it has already (or, with none yet, say
    nothing); True when it did."""
    asked = shown(new.bedrooms, new.bathrooms, new.square_feet, new.market_rent)
    if asked == details_of(conn, [unit_id])[unit_id]:
        return False
    # Kept to the cent, as all money is, so reports print 1300.00, not 1300.
    market = None if new.market_rent is None else new.market_rent.quantize(rent.CENT)
    conn.execute(
        "INSERT INTO trust_unit_details"
        " (pmc_id, unit_id, bedrooms, bathrooms, square_feet, market_rent)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (pmc_id, unit_id, new.bedrooms, new.bathrooms, new.square_feet, market),
    )
    return True


@router.post("/units/{unit_id}/details")
def change_details(session: Staff, conn: Conn, unit_id: UUID, new: NewUnitDetails) -> Changed:
    """A unit's bedrooms, bathrooms, square feet and market rent as they are now, each left out
    when not known; the same again writes nothing, and nothing at all clears them."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_units WHERE pmc_id = %s AND id = %s", (session.pmc_id, unit_id)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such unit")
    return Changed(changed=record_details(conn, session.pmc_id, unit_id, new))
