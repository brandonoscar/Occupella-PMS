"""Vacancy listings (product item 8): a PMC's public page of open units with their
application links. Not syndication: nothing is sent anywhere.

Staff see on Leasing, Listings each unit that comes free within 90 days (or has an open link):
when, at what rent, and whether it is listed (the founder's punch list, ADR 0006). They list an
open application link (with a description if they like), or open a link and list it at once
(leasing.py), and take a listing off again. The public page (/listings?pmc=<PMC id>) asks
GET /api/listings/<PMC id>, which needs no login and shows only what a listing is for: the
property, unit, rent and description. An applicant applies through a listing as through its
link (leasing.py), until it is removed or the link closed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StringConstraints

from pms import exports
from pms.api import scope
from pms.api.deps import Conn, Staff
from pms.api.rentals import Page, Paged, PerPage

router = APIRouter()
# How far ahead Listings looks for a unit coming free.
AHEAD = timedelta(days=90)

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


class Vacancy(BaseModel):
    unit_id: UUID
    property_id: UUID
    property: str
    unit: str
    # The first day from today that no lease holds it, if within 90 days.
    available_on: date | None
    # The rent its latest lease charges or charged last, to ask again; None if never leased.
    last_rent: str | None
    # Its open application link (a listed one first, else the newest): the rent it asks and
    # the applications that came through it.
    link_id: UUID | None
    link_rent: str | None
    applications: int
    # The link's listing on the public page, while it is there.
    listing_id: UUID | None
    description: str | None


VACANCIES = f"""
WITH units AS (
    SELECT u.id, u.property_id, u.display_name,
           (SELECT min(c.day) FROM (
                SELECT %(day)s::date AS day
                UNION SELECT l.ends_on + 1 FROM trust_leases AS l
                WHERE l.unit_id = u.id AND l.ends_on >= %(day)s
            ) AS c
            WHERE NOT EXISTS (
                SELECT 1 FROM trust_leases AS l
                WHERE l.unit_id = u.id AND l.starts_on <= c.day
                  AND (l.ends_on IS NULL OR l.ends_on >= c.day)
            )) AS free_on
    FROM trust_units AS u
    WHERE u.pmc_id = %(pmc)s AND NOT trust_is_archived(NULL, u.id)
)
SELECT u.id, p.id, p.display_name, u.display_name,
       CASE WHEN u.free_on <= %(until)s THEN u.free_on END,
       (SELECT trust_rent_on(l.id, greatest(l.starts_on, least(l.ends_on, %(day)s)))
        FROM trust_leases AS l WHERE l.unit_id = u.id
        ORDER BY l.starts_on DESC, l.id LIMIT 1),
       k.id, k.monthly_rent,
       (SELECT count(*) FROM trust_applications AS a WHERE a.link_id = k.id),
       k.listing_id, k.description, count(*) OVER ()
FROM units AS u
JOIN trust_properties AS p ON p.id = u.property_id
LEFT JOIN LATERAL (
    SELECT l.id, l.monthly_rent, s.id AS listing_id, s.description
    FROM trust_application_links AS l
    LEFT JOIN trust_listings AS s ON s.link_id = l.id AND NOT EXISTS (
        SELECT 1 FROM trust_listing_removals AS r WHERE r.listing_id = s.id)
    WHERE l.unit_id = u.id AND NOT EXISTS (
        SELECT 1 FROM trust_application_link_closings AS c WHERE c.link_id = l.id)
    ORDER BY s.id IS NULL, l.opened_at DESC, l.id
    LIMIT 1
) AS k ON true
WHERE (u.free_on <= %(until)s OR k.id IS NOT NULL) AND {scope.within("p.id")}
ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C", u.id
LIMIT %(limit)s OFFSET %(offset)s
"""


@router.get("/api/leasing/vacancies")
def staff_vacancies(
    session: Staff,
    conn: Conn,
    group: scope.Group = None,
    property_id: scope.Property = None,
    page: Page = 1,
    per_page: PerPage = 50,
) -> Paged[Vacancy]:
    """The PMC's units that come free within 90 days or have an open application link, by
    property and unit, a page at a time: when each is free, its last rent, its open link and
    whether that is listed. Maybe of a property group or property."""
    day = datetime.now(UTC).date()
    rows = conn.execute(
        VACANCIES,
        {
            "pmc": session.pmc_id,
            "day": day,
            "until": day + AHEAD,
            "limit": per_page,
            "offset": (page - 1) * per_page,
            **scope.params(group, property_id),
        },
    ).fetchall()
    return Paged(
        items=[
            Vacancy(
                unit_id=unit_id,
                property_id=prop_id,
                property=prop,
                unit=unit,
                available_on=free_on,
                last_rent=None if last is None else exports.text(last),
                link_id=link,
                link_rent=None if link_rent is None else exports.text(link_rent),
                applications=applications,
                listing_id=listing,
                description=description,
            )
            for (
                unit_id,
                prop_id,
                prop,
                unit,
                free_on,
                last,
                link,
                link_rent,
                applications,
                listing,
                description,
                _,
            ) in rows
        ],
        total=rows[0][-1] if rows else 0,
        page=page,
        per_page=per_page,
    )


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
