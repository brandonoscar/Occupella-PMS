"""Announcements (the portals' second version, migration 20261103000100): what staff post for the
tenants of one property, or of every property (the water off on Tuesday, the pool closed), shown
in the tenant portal until staff take it down. Each names the staff member who posted it and,
once, who took it down (trust_announcements, trust_announcement_removals; append-only). Posting
takes the custom roles' 'email' area. An announcement is not emailed: staff write to the tenants
living at a property from its Email tenants for that.

A tenant sees the announcements not taken down of the company's every property and of each
property where they have a lease in effect or to come, newest first. The AI role reads none of
it. Nothing here moves money.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StringConstraints

from pms.api.deps import Conn, Staff, Tenant
from pms.api.rent import today

router = APIRouter(prefix="/api")

Title = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[^\x00\r\n]*$"
    ),
]
Body = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000, pattern=r"^[^\x00]*$"),
]
SHOWN = 50


class Announcement(BaseModel):
    id: UUID
    # The property whose tenants see it; None: every property's.
    property_id: UUID | None
    property: str | None
    title: str
    body: str
    at: datetime
    by: str
    # When it was taken down, and by whom; None while tenants see it.
    removed_at: datetime | None
    removed_by: str | None


class TenantAnnouncement(BaseModel):
    id: UUID
    property: str | None
    title: str
    body: str
    at: datetime


class NewAnnouncement(BaseModel):
    property_id: UUID | None = None
    title: Title
    body: Body


LIST = """
SELECT a.id, a.property_id, p.display_name, a.title, a.body, a.created_at, s.display_name,
       r.created_at, rs.display_name
FROM trust_announcements AS a
JOIN trust_staff AS s ON s.id = a.recorded_by
LEFT JOIN trust_properties AS p ON p.id = a.property_id
LEFT JOIN trust_announcement_removals AS r ON r.announcement_id = a.id
LEFT JOIN trust_staff AS rs ON rs.id = r.recorded_by
WHERE a.pmc_id = %(pmc)s AND (%(id)s::uuid IS NULL OR a.id = %(id)s)
ORDER BY a.created_at DESC, a.id
LIMIT 200
"""


def listed(conn: psycopg.Connection[Any], pmc: UUID, one: UUID | None = None) -> list[Announcement]:
    return [
        Announcement(
            id=i,
            property_id=property_id,
            property=prop,
            title=title,
            body=body,
            at=at,
            by=by,
            removed_at=removed_at,
            removed_by=removed_by,
        )
        for i, property_id, prop, title, body, at, by, removed_at, removed_by in conn.execute(
            LIST, {"pmc": pmc, "id": one}
        ).fetchall()
    ]


@router.get("/announcements")
def announcements(session: Staff, conn: Conn) -> list[Announcement]:
    """The company's announcements, newest first (200 at most), each with whether it is up."""
    return listed(conn, session.pmc_id)


@router.post("/announcements", status_code=201)
def post(session: Staff, conn: Conn, new: NewAnnouncement) -> Announcement:
    """An announcement for the tenants of one of the company's properties, or of all of them."""
    if new.property_id is not None and (
        conn.execute(
            "SELECT 1 FROM trust_properties WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, new.property_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such property")
    row = conn.execute(
        "INSERT INTO trust_announcements (pmc_id, property_id, title, body)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (session.pmc_id, new.property_id, new.title, new.body),
    ).fetchone()
    assert row is not None
    (made,) = listed(conn, session.pmc_id, row[0])
    return made


@router.post("/announcements/{announcement_id}/remove")
def remove(session: Staff, conn: Conn, announcement_id: UUID) -> Announcement:
    """Takes an announcement down: tenants no longer see it. Once."""
    if not listed(conn, session.pmc_id, announcement_id):
        raise HTTPException(404, "no such announcement")
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_announcement_removals (announcement_id, pmc_id) VALUES (%s, %s)",
                (announcement_id, session.pmc_id),
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "this announcement was taken down already") from None
    (gone,) = listed(conn, session.pmc_id, announcement_id)
    return gone


FOR_TENANT = """
SELECT a.id, p.display_name, a.title, a.body, a.created_at
FROM trust_announcements AS a
LEFT JOIN trust_properties AS p ON p.id = a.property_id
WHERE a.pmc_id = %(pmc)s
  AND NOT EXISTS (SELECT 1 FROM trust_announcement_removals AS r WHERE r.announcement_id = a.id)
  AND (a.property_id IS NULL OR EXISTS (
        SELECT 1 FROM trust_lease_tenants AS lt
        JOIN trust_leases AS l ON l.id = lt.lease_id
        JOIN trust_units AS u ON u.id = l.unit_id
        WHERE lt.tenant_id = ANY(%(records)s) AND u.property_id = a.property_id
          AND (l.ends_on IS NULL OR l.ends_on >= %(day)s)))
ORDER BY a.created_at DESC, a.id
LIMIT %(shown)s
"""


@router.get("/tenant/announcements")
def tenant_announcements(session: Tenant, conn: Conn) -> list[TenantAnnouncement]:
    """What the company announced to the tenant: to every tenant, or at a property where they
    have a lease in effect or to come; newest first."""
    records = [
        r
        for (r,) in conn.execute(
            "SELECT trust_tenant_records(%s, %s)", (session.pmc_id, session.person_id)
        ).fetchall()
    ]
    rows = conn.execute(
        FOR_TENANT,
        {"pmc": session.pmc_id, "records": records, "day": today(), "shown": SHOWN},
    ).fetchall()
    return [
        TenantAnnouncement(id=i, property=prop, title=title, body=body, at=at)
        for i, prop, title, body, at in rows
    ]
