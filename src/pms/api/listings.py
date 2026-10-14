"""Vacancy listings (product item 8): a PMC's public page of open units with their
application links. Not syndication: nothing is sent anywhere.

Staff list an open application link (with a description if they like) and take a listing off
again. The public page (/listings?pmc=<PMC id>) asks GET /api/listings/<PMC id>, which needs no
login and shows only what a listing is for: the property, unit, rent and description. An
applicant applies through a listing as through its link (leasing.py), until it is removed or
the link closed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StringConstraints

from pms import exports
from pms.api.deps import Conn, Staff

router = APIRouter()

Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]


class NewListing(BaseModel):
    description: Description | None = None


class Listed(BaseModel):
    id: UUID


class PublicListing(BaseModel):
    id: UUID
    property: str
    unit: str
    monthly_rent: str
    description: str | None
    listed_at: datetime


class Vacancies(BaseModel):
    pmc: str
    listings: list[PublicListing]


@router.post("/api/leasing/links/{link_id}/listing", status_code=201)
def list_link(session: Staff, conn: Conn, link_id: UUID, new: NewListing) -> Listed:
    """Puts an open application link on the PMC's public page."""
    found = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM trust_application_link_closings AS c"
        "   WHERE c.link_id = l.id)"
        " FROM trust_application_links AS l WHERE l.pmc_id = %s AND l.id = %s",
        (session.pmc_id, link_id),
    ).fetchone()
    if found is None:
        raise HTTPException(404, "no such application link")
    if found[0]:
        raise HTTPException(409, "this link is closed")
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO trust_listings (pmc_id, link_id, description, listed_by)"
                " VALUES (%s, %s, %s, %s) RETURNING id",
                (session.pmc_id, link_id, new.description, session.person_id),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            409, "this link was listed already; open a new link to list the unit again"
        ) from None
    assert row is not None
    return Listed(id=row[0])


@router.post("/api/leasing/listings/{listing_id}/remove", status_code=204)
def remove_listing(session: Staff, conn: Conn, listing_id: UUID) -> None:
    """Takes a listing off the PMC's public page; its link stays open."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_listings WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, listing_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such listing")
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_listing_removals (pmc_id, listing_id, removed_by)"
                " VALUES (%s, %s, %s)",
                (session.pmc_id, listing_id, session.person_id),
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "this listing is removed already") from None


@router.get("/api/listings/{pmc_id}")
def vacancies(conn: Conn, pmc_id: UUID) -> Vacancies:
    """A PMC's public page: each listing not removed whose link is open. No login."""
    pmc = conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc_id,)
    ).fetchone()
    if pmc is None:
        raise HTTPException(404, "no such PMC")
    rows = conn.execute(
        "SELECT s.id, p.display_name, u.display_name, l.monthly_rent, s.description, s.listed_at"
        " FROM trust_listings AS s"
        " JOIN trust_application_links AS l ON l.id = s.link_id"
        " JOIN trust_units AS u ON u.id = l.unit_id"
        " JOIN trust_properties AS p ON p.id = u.property_id"
        " WHERE s.pmc_id = %s"
        "   AND NOT EXISTS (SELECT 1 FROM trust_listing_removals AS r WHERE r.listing_id = s.id)"
        "   AND NOT EXISTS ("
        "     SELECT 1 FROM trust_application_link_closings AS c WHERE c.link_id = l.id)"
        ' ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C", s.id',
        (pmc_id,),
    ).fetchall()
    return Vacancies(
        pmc=pmc[0],
        listings=[
            PublicListing(
                id=i,
                property=prop,
                unit=unit,
                monthly_rent=exports.text(rent),
                description=text,
                listed_at=at,
            )
            for i, prop, unit, rent, text, at in rows
        ],
    )
