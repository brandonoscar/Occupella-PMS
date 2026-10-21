"""A property's street address and where it is on a map (migration 0109; the open-source review's
geocoding, done last as agreed). Staff keep the address on the property's page; Find on map asks
the server's geocoder (pms.geocoding, PMS_GEOCODER) where it is, sending the property's address
alone, and the page links to OpenStreetMap at the point. Saving the same address again writes
nothing; a new address has no location until it is found.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, BeforeValidator, StringConstraints, field_validator

from pms import exports, geocoding
from pms.api.deps import Conn, Staff

router = APIRouter(prefix="/api/properties")

Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
City = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
# A state's USPS code, in either case ("ca" is CA).
State = Annotated[
    str,
    BeforeValidator(lambda value: value.strip().upper() if isinstance(value, str) else value),
    StringConstraints(pattern=r"^[A-Z]{2}$"),
]
Zip = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{5}(-[0-9]{4})?$")]

# An address (on a) on one line, as geocoding.Address.one_line writes it.
ONE_LINE = (
    "a.line_1 || coalesce(', ' || a.line_2, '') || ', ' || a.city || ', ' || a.state"
    " || ' ' || a.zip"
)


class PropertyAddress(BaseModel):
    line_1: str
    line_2: str | None
    city: str
    state: str
    zip: str
    # "100 Synthetic Way, Unit 2, Demo City, CA 00001"
    one_line: str
    recorded_by: str | None
    recorded_at: datetime


class Location(BaseModel):
    # Degrees, as text to six places.
    latitude: str
    longitude: str
    # The address as the geocoder matched it.
    matched: str
    geocoder: str
    # OpenStreetMap at the point.
    map_url: str
    found_at: datetime


def address_of(conn: psycopg.Connection[Any], property_id: UUID) -> PropertyAddress | None:
    row = conn.execute(
        f"SELECT a.line_1, a.line_2, a.city, a.state, a.zip, {ONE_LINE}, s.display_name,"  # noqa: S608
        " a.created_at FROM trust_property_address(%s) AS a"
        " LEFT JOIN trust_staff AS s ON s.id = a.recorded_by",
        (property_id,),
    ).fetchone()
    if row is None:
        return None
    line_1, line_2, city, state, zip_code, one_line, by, at = row
    return PropertyAddress(
        line_1=line_1,
        line_2=line_2,
        city=city,
        state=state,
        zip=zip_code,
        one_line=one_line,
        recorded_by=by,
        recorded_at=at,
    )


def map_url(latitude: str, longitude: str) -> str:
    return (
        f"https://www.openstreetmap.org/?mlat={latitude}&mlon={longitude}"
        f"#map=17/{latitude}/{longitude}"
    )


def location_of(conn: psycopg.Connection[Any], property_id: UUID) -> Location | None:
    row = conn.execute(
        "SELECT g.latitude, g.longitude, g.matched, g.geocoder, g.created_at"
        " FROM trust_property_location(%s) AS g",
        (property_id,),
    ).fetchone()
    if row is None:
        return None
    latitude, longitude = exports.text(row[0]), exports.text(row[1])
    return Location(
        latitude=latitude,
        longitude=longitude,
        matched=row[2],
        geocoder=row[3],
        map_url=map_url(latitude, longitude),
        found_at=row[4],
    )


def property_named(conn: psycopg.Connection[Any], pmc_id: UUID, property_id: UUID) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM trust_properties WHERE pmc_id = %s AND id = %s", (pmc_id, property_id)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such property")


class NewAddress(BaseModel):
    line_1: Line
    line_2: Line | None = None
    city: City
    state: State
    zip: Zip

    @field_validator("line_2", mode="before")
    @classmethod
    def blank_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value


@router.post("/{property_id}/address", status_code=204)
def set_address(session: Staff, conn: Conn, property_id: UUID, new: NewAddress) -> None:
    """A property's street address from now on. The same address again writes nothing."""
    property_named(conn, session.pmc_id, property_id)
    now = address_of(conn, property_id)
    if now is not None and (now.line_1, now.line_2, now.city, now.state, now.zip) == (
        new.line_1,
        new.line_2,
        new.city,
        new.state,
        new.zip,
    ):
        return
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_property_addresses"
                " (pmc_id, property_id, line_1, line_2, city, state, zip)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (session.pmc_id, property_id, new.line_1, new.line_2, new.city, new.state, new.zip),
            )
    except psycopg.errors.CheckViolation as refused:
        if refused.diag.constraint_name == "trust_property_addresses_state_check":
            raise HTTPException(422, f"{new.state} is not a state's USPS code") from None
        raise


@router.post("/{property_id}/locate", status_code=201)
def locate(request: Request, session: Staff, conn: Conn, property_id: UUID) -> Location:
    """Asks the server's geocoder where the property's address is, sending that address alone,
    and keeps what it matched."""
    property_named(conn, session.pmc_id, property_id)
    row = conn.execute(
        "SELECT a.id, a.line_1, a.line_2, a.city, a.state, a.zip"
        " FROM trust_property_address(%s) AS a",
        (property_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(409, "the property has no address yet: add its address first")
    geocoder: geocoding.Geocoder | None = request.app.state.geocoder
    if geocoder is None:
        raise HTTPException(
            503, "this server finds no addresses on a map: whoever runs it sets PMS_GEOCODER"
        )
    address = geocoding.Address(line_1=row[1], line_2=row[2], city=row[3], state=row[4], zip=row[5])
    try:
        match = geocoder.locate(address)
    except geocoding.GeocoderFailed as failed:
        raise HTTPException(502, str(failed)) from None
    if match is None:
        raise HTTPException(
            422, f"the geocoder found no match for {address.one_line()}: check the address"
        )
    conn.execute(
        "INSERT INTO trust_property_locations"
        " (pmc_id, address_id, latitude, longitude, matched, geocoder)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (session.pmc_id, row[0], match.latitude, match.longitude, match.matched, geocoder.name),
    )
    found = location_of(conn, property_id)
    assert found is not None
    return found
