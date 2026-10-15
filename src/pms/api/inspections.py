"""Move-in and move-out inspections (migration 0058): staff record a lease's inspection, room
by room, each room in a condition (good, fair, poor, damaged, missing) maybe with a note. The
latest of each kind counts; trust_report_inspection lays the two side by side and staff
download it through /api/reports. An inspection moves no money: what a damaged room costs is a
charge, kept back from the deposit at move-out.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints, model_validator

from pms.api.deps import Conn, Staff

router = APIRouter(prefix="/api/inspections")

Kind = Literal["move-in", "move-out"]
Condition = Literal["good", "fair", "poor", "damaged", "missing"]
Room = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ItemOut(BaseModel):
    room: str
    condition: str
    note: str | None


class InspectionOut(BaseModel):
    id: UUID
    kind: str
    inspected_on: date
    notes: str | None
    recorded_by: str | None
    recorded_at: datetime
    items: list[ItemOut]


def lease_of(conn: Conn, pmc_id: UUID, lease_id: UUID) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM trust_leases WHERE pmc_id = %s AND id = %s", (pmc_id, lease_id)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such lease")


@router.get("")
def inspections(
    session: Staff, conn: Conn, lease_id: Annotated[UUID, Query()]
) -> list[InspectionOut]:
    """A lease's inspections, the latest first."""
    lease_of(conn, session.pmc_id, lease_id)
    rows = conn.execute(
        "SELECT i.id, i.kind, i.inspected_on, i.notes, s.display_name, i.created_at,"
        "  (SELECT coalesce(json_agg(json_build_array(x.room, x.condition, x.note)"
        "     ORDER BY x.line), '[]') FROM trust_inspection_items AS x"
        "   WHERE x.inspection_id = i.id)"
        " FROM trust_inspections AS i LEFT JOIN trust_staff AS s ON s.id = i.recorded_by"
        " WHERE i.lease_id = %s ORDER BY i.inspected_on DESC, i.created_at DESC, i.id",
        (lease_id,),
    ).fetchall()
    return [
        InspectionOut(
            id=i,
            kind=kind,
            inspected_on=on,
            notes=notes,
            recorded_by=by,
            recorded_at=at,
            items=[ItemOut(room=r, condition=c, note=n) for r, c, n in items],
        )
        for i, kind, on, notes, by, at, items in rows
    ]


class NewItem(BaseModel):
    room: Room
    condition: Condition
    note: Note | None = None


class NewInspection(BaseModel):
    lease_id: UUID
    kind: Kind
    inspected_on: date
    notes: Notes | None = None
    items: list[NewItem] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def names_each_room_once(self) -> NewInspection:
        rooms = [item.room.lower() for item in self.items]
        if len(set(rooms)) != len(rooms):
            raise ValueError("an inspection names each room once")
        return self


class Created(BaseModel):
    id: UUID


@router.post("", status_code=201)
def record(session: Staff, conn: Conn, new: NewInspection) -> Created:
    """Records an inspection of a lease and its rooms, in the order given."""
    lease_of(conn, session.pmc_id, new.lease_id)
    row = conn.execute(
        "INSERT INTO trust_inspections (pmc_id, lease_id, kind, inspected_on, notes)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (session.pmc_id, new.lease_id, new.kind, new.inspected_on, new.notes),
    ).fetchone()
    assert row is not None
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO trust_inspection_items"
            " (inspection_id, pmc_id, line, room, condition, note)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (row[0], session.pmc_id, n, item.room, item.condition, item.note)
                for n, item in enumerate(new.items, start=1)
            ],
        )
    return Created(id=row[0])
