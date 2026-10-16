"""Notes (the founder's punch list, ADR 0006; migration 0066): what staff write down about a
property, unit, lease, tenant, owner or vendor, on that record's Notes tab. A note is kept for
good and names who wrote it; a correction is a new note. The AI role never reads notes. Nothing
here moves money.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, get_args
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, StringConstraints

from pms.api.deps import Conn, Staff

router = APIRouter(prefix="/api")

About = Literal["property", "unit", "lease", "tenant", "owner", "vendor"]
# Any text but a NUL, which Postgres text can't hold (refused here, not by the database).
Body = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=5000, pattern=r"^[^\x00]*$"),
]


class Note(BaseModel):
    id: UUID
    body: str
    by: str | None
    at: datetime


class NewNote(BaseModel):
    about: About
    about_id: UUID
    body: Body


class Created(BaseModel):
    id: UUID


def keys(about: About, about_id: UUID) -> dict[str, UUID | None]:
    """A note's six keys, the record's id in its own and null in the others, so no SQL is built
    from the request: a key compared with null matches nothing."""
    return {kind: about_id if kind == about else None for kind in get_args(About)}


def notes_of(conn: psycopg.Connection[Any], pmc: UUID, about: About, about_id: UUID) -> list[Note]:
    """A record's notes, newest first, each with who wrote it."""
    rows = conn.execute(
        "SELECT n.id, n.body, s.display_name, n.created_at FROM trust_notes AS n"
        " LEFT JOIN trust_staff AS s ON s.id = n.recorded_by"
        " WHERE n.pmc_id = %(pmc)s AND (n.property_id = %(property)s OR n.unit_id = %(unit)s"
        "   OR n.lease_id = %(lease)s OR n.tenant_id = %(tenant)s OR n.owner_id = %(owner)s"
        "   OR n.vendor_id = %(vendor)s)"
        " ORDER BY n.created_at DESC, n.id DESC",
        {"pmc": pmc, **keys(about, about_id)},
    ).fetchall()
    return [Note(id=i, body=b, by=who, at=at) for i, b, who, at in rows]


@router.get("/notes")
def notes(
    session: Staff, conn: Conn, about: Annotated[About, Query()], about_id: UUID
) -> list[Note]:
    """A record's notes, newest first, each with who wrote it."""
    return notes_of(conn, session.pmc_id, about, about_id)


@router.post("/notes", status_code=201)
def add_note(session: Staff, conn: Conn, body: NewNote) -> Created:
    """Writes a note about one of the company's records."""
    try:
        row = conn.execute(
            "INSERT INTO trust_notes"
            " (pmc_id, property_id, unit_id, lease_id, tenant_id, owner_id, vendor_id, body)"
            " VALUES (%(pmc)s, %(property)s, %(unit)s, %(lease)s, %(tenant)s, %(owner)s,"
            "   %(vendor)s, %(body)s) RETURNING id",
            {"pmc": session.pmc_id, "body": body.body, **keys(body.about, body.about_id)},
        ).fetchone()
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(404, f"no such {body.about}") from None
    assert row is not None
    return Created(id=row[0])
