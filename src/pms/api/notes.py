"""Notes (the founder's punch list, ADR 0006; migration 0066): what staff write down about a
property, unit, lease, tenant, owner or vendor, on that record's Notes tab. A note is kept for
good and names who wrote it; a correction is a new note. The AI role never reads notes. Nothing
here moves money.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
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


def notes_of(conn: psycopg.Connection[Any], pmc: UUID, about: About, about_id: UUID) -> list[Note]:
    """A record's notes, newest first, each with who wrote it."""
    rows = conn.execute(
        f"SELECT n.id, n.body, s.display_name, n.created_at FROM trust_notes AS n"
        f" LEFT JOIN trust_staff AS s ON s.id = n.recorded_by"
        f" WHERE n.pmc_id = %s AND n.{about}_id = %s ORDER BY n.created_at DESC, n.id DESC",
        (pmc, about_id),
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
            f"INSERT INTO trust_notes (pmc_id, {body.about}_id, body) VALUES (%s, %s, %s)"
            " RETURNING id",
            (session.pmc_id, body.about_id, body.body),
        ).fetchone()
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(404, f"no such {body.about}") from None
    assert row is not None
    return Created(id=row[0])
