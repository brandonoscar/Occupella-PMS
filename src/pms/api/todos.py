"""Staff to-dos (migration 20261023000203; the founder, 2026-10-23, lane 2's third item): what a
staff member writes down to do, maybe about a property, assigned to someone on staff with a day it
is due. Staff reassign one or move its day (the latest assignment holds, naming who changed it)
and mark it done, once. The list shows the open ones by due day, theirs or everyone's; the
Dashboard shows each staff member theirs due within a week or overdue. Any staff member keeps
them: they are no custom role's area. The AI role reads none of them. Nothing here moves money.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, StringConstraints

from pms.api.deps import Conn, Staff
from pms.api.rent import today
from pms.api.rentals import Page, Paged, PerPage

router = APIRouter(prefix="/api/todos")

Title = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[^\x00\r\n]*$"
    ),
]
Notes = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000, pattern=r"^[^\x00]*$"),
]
Status = Literal["open", "done", "all"]
# How many days ahead the Dashboard looks.
SOON = 7


class Todo(BaseModel):
    id: UUID
    title: str
    notes: str | None
    property_id: UUID | None
    property: str | None
    assigned_to_id: UUID
    assigned_to: str
    due_on: date
    # Open and due before today.
    overdue: bool
    written_by: str
    written_at: datetime
    done_by: str | None
    done_at: datetime | None


TODOS = """
SELECT t.id, t.title, t.notes, t.property_id, p.display_name, a.assigned_to, s.display_name,
       a.due_on, w.display_name, t.created_at, d.display_name, c.created_at, count(*) OVER ()
FROM trust_todos AS t
CROSS JOIN LATERAL (
    SELECT x.assigned_to, x.due_on FROM trust_todo_assignments AS x
    WHERE x.todo_id = t.id ORDER BY x.created_at DESC, x.id DESC LIMIT 1
) AS a
JOIN trust_staff AS s ON s.id = a.assigned_to
JOIN trust_staff AS w ON w.id = t.recorded_by
LEFT JOIN trust_properties AS p ON p.id = t.property_id
LEFT JOIN trust_todo_completions AS c ON c.todo_id = t.id
LEFT JOIN trust_staff AS d ON d.id = c.done_by
WHERE t.pmc_id = %(pmc)s
  AND (%(status)s = 'all' OR (%(status)s = 'open') = (c.todo_id IS NULL))
  AND (%(mine)s::uuid IS NULL OR a.assigned_to = %(mine)s::uuid)
  AND (%(due_by)s::date IS NULL OR a.due_on <= %(due_by)s::date)
  AND (%(todo)s::uuid IS NULL OR t.id = %(todo)s::uuid)
ORDER BY c.todo_id IS NOT NULL, CASE WHEN c.todo_id IS NULL THEN a.due_on END,
         c.created_at DESC, t.created_at, t.id
LIMIT %(per_page)s OFFSET %(offset)s
"""


def listed(
    conn: psycopg.Connection[Any],
    pmc: UUID,
    status: Status = "open",
    mine: UUID | None = None,
    due_by: date | None = None,
    todo: UUID | None = None,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[Todo], int]:
    """To-dos: the open ones first by the day they are due, then those done, newest first."""
    rows = conn.execute(
        TODOS,
        {
            "pmc": pmc,
            "status": status,
            "mine": mine,
            "due_by": due_by,
            "todo": todo,
            "per_page": per_page,
            "offset": (page - 1) * per_page,
        },
    ).fetchall()
    on = today()
    found = [
        Todo(
            id=row[0],
            title=row[1],
            notes=row[2],
            property_id=row[3],
            property=row[4],
            assigned_to_id=row[5],
            assigned_to=row[6],
            due_on=row[7],
            overdue=row[11] is None and row[7] < on,
            written_by=row[8],
            written_at=row[9],
            done_by=row[10],
            done_at=row[11],
        )
        for row in rows
    ]
    return found, rows[0][12] if rows else 0


@router.get("")
def todos(
    session: Staff,
    conn: Conn,
    status: Annotated[Status, Query()] = "open",
    mine: Annotated[bool, Query()] = False,
    page: Page = 1,
    per_page: PerPage = 50,
) -> Paged[Todo]:
    """The company's to-dos (or the signed-in staff member's), open, done or all, a page at a
    time."""
    found, total = listed(
        conn,
        session.pmc_id,
        status,
        session.person_id if mine else None,
        page=page,
        per_page=per_page,
    )
    return Paged(items=found, total=total, page=page, per_page=per_page)


def mine_soon(conn: psycopg.Connection[Any], pmc: UUID, staff: UUID, on: date) -> list[Todo]:
    """A staff member's open to-dos due within a week or overdue, for the Dashboard."""
    return listed(conn, pmc, "open", staff, on + timedelta(days=SOON), per_page=200)[0]


def staff_named(conn: psycopg.Connection[Any], pmc: UUID, staff: UUID) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM trust_staff WHERE pmc_id = %s AND id = %s", (pmc, staff)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such staff member")


def one(conn: psycopg.Connection[Any], pmc: UUID, todo: UUID) -> Todo:
    found, _ = listed(conn, pmc, "all", todo=todo)
    if not found:
        raise HTTPException(404, "no such to-do")
    return found[0]


class NewTodo(BaseModel):
    title: Title
    notes: Notes | None = None
    property_id: UUID | None = None
    assigned_to: UUID
    due_on: date


@router.post("", status_code=201)
def add(session: Staff, conn: Conn, new: NewTodo) -> Todo:
    """A to-do, assigned to someone on staff with the day it is due, naming who wrote it."""
    staff_named(conn, session.pmc_id, new.assigned_to)
    if new.property_id is not None and (
        conn.execute(
            "SELECT 1 FROM trust_properties WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, new.property_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such property")
    row = conn.execute(
        "INSERT INTO trust_todos (pmc_id, title, notes, property_id, recorded_by)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (session.pmc_id, new.title, new.notes, new.property_id, session.person_id),
    ).fetchone()
    assert row is not None
    conn.execute(
        "INSERT INTO trust_todo_assignments (pmc_id, todo_id, assigned_to, due_on)"
        " VALUES (%s, %s, %s, %s)",
        (session.pmc_id, row[0], new.assigned_to, new.due_on),
    )
    return one(conn, session.pmc_id, row[0])


class Assignment(BaseModel):
    assigned_to: UUID
    due_on: date


@router.post("/{todo_id}/assign")
def assign(session: Staff, conn: Conn, todo_id: UUID, change: Assignment) -> Todo:
    """Reassigns an open to-do or moves the day it is due; the same again writes nothing."""
    now = one(conn, session.pmc_id, todo_id)
    staff_named(conn, session.pmc_id, change.assigned_to)
    if now.done_at is not None:
        raise HTTPException(409, "that to-do is done already")
    if (now.assigned_to_id, now.due_on) == (change.assigned_to, change.due_on):
        return now
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_todo_assignments (pmc_id, todo_id, assigned_to, due_on)"
                " VALUES (%s, %s, %s, %s)",
                (session.pmc_id, todo_id, change.assigned_to, change.due_on),
            )
    except psycopg.errors.CheckViolation:
        raise HTTPException(409, "that to-do is done already") from None
    return one(conn, session.pmc_id, todo_id)


@router.post("/{todo_id}/done")
def done(session: Staff, conn: Conn, todo_id: UUID) -> Todo:
    """Marks a to-do done, naming who did; once (again changes nothing)."""
    one(conn, session.pmc_id, todo_id)
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_todo_completions (todo_id, pmc_id, done_by) VALUES (%s, %s, %s)"
                " ON CONFLICT (todo_id) DO NOTHING",
                (todo_id, session.pmc_id, session.person_id),
            )
    except psycopg.errors.CheckViolation:
        pass  # done already, by another request at the same moment
    return one(conn, session.pmc_id, todo_id)
