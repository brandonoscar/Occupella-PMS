"""Custom staff roles (migration 0057): an admin names a role and the areas of the work it may
change, and gives it to staff members. A staff member given custom roles may change only what
their roles allow (the Staff dependency checks every write, src/pms/api/deps.py); one with none
works everywhere. The database refuses a change by anyone but an admin (403).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from pms import accounts
from pms.api.deps import AREAS, Conn, Staff

router = APIRouter(prefix="/api/custom-roles")

Area = Literal[
    "rent",
    "bills",
    "owners",
    "budgets",
    "journal",
    "month-end",
    "switching",
    "setup",
    "move-out",
    "documents",
    "leasing",
    "maintenance",
    "people",
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


def areas_of(conn: psycopg.Connection[Any], session: accounts.Session) -> list[str]:
    """The areas a signed-in staff member may change, in AREAS' order."""
    row = conn.execute(
        "SELECT array(SELECT a FROM unnest(%s::text[]) WITH ORDINALITY AS u (a, n)"
        " WHERE trust_staff_may(%s, %s, a) ORDER BY n)",
        (list(AREAS), session.pmc_id, session.person_id),
    ).fetchone()
    assert row is not None
    return list(row[0])


class AreaOut(BaseModel):
    area: str
    label: str


class Holder(BaseModel):
    staff_id: UUID
    name: str


class RoleOut(BaseModel):
    id: UUID
    name: str
    areas: list[str]
    staff: list[Holder]


class Roles(BaseModel):
    areas: list[AreaOut]
    roles: list[RoleOut]


LATEST_AREAS = (
    "SELECT coalesce(array_agg(x.area ORDER BY array_position(%(areas)s::text[], x.area)), '{}')"
    " FROM (SELECT DISTINCT ON (a.area) a.area, a.granted FROM trust_custom_role_areas AS a"
    "       WHERE a.role_id = r.id ORDER BY a.area, a.id DESC) AS x WHERE x.granted"
)
LATEST_STAFF = (
    "SELECT coalesce(json_agg(json_build_array(s.id, s.display_name)"
    "         ORDER BY s.display_name COLLATE \"C\", s.id), '[]')"
    " FROM trust_staff AS s WHERE (SELECT h.granted FROM trust_staff_custom_roles AS h"
    "       WHERE h.role_id = r.id AND h.staff_id = s.id ORDER BY h.id DESC LIMIT 1)"
)


@router.get("")
def roles(session: Staff, conn: Conn) -> Roles:
    """Every custom role of the PMC: the areas it allows and who holds it."""
    rows = conn.execute(
        f"SELECT r.id, r.name, ({LATEST_AREAS}), ({LATEST_STAFF})"  # noqa: S608
        ' FROM trust_custom_roles AS r WHERE r.pmc_id = %(pmc)s ORDER BY r.name COLLATE "C", r.id',
        {"areas": list(AREAS), "pmc": session.pmc_id},
    ).fetchall()
    return Roles(
        areas=[AreaOut(area=a, label=label) for a, label in AREAS.items()],
        roles=[
            RoleOut(
                id=i,
                name=n,
                areas=list(a),
                staff=[Holder(staff_id=UUID(s), name=name) for s, name in holders],
            )
            for i, n, a, holders in rows
        ],
    )


class NewRole(BaseModel):
    name: Name
    areas: list[Area] = Field(max_length=len(AREAS))


class Created(BaseModel):
    id: UUID


@router.post("", status_code=201)
def create(session: Staff, conn: Conn, new: NewRole) -> Created:
    """A role allowing these areas. Only an admin makes one (403); a name is used once (409)."""
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO trust_custom_roles (pmc_id, name) VALUES (%s, %s) RETURNING id",
                (session.pmc_id, new.name),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "the company has a role of that name already") from None
    assert row is not None
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO trust_custom_role_areas (pmc_id, role_id, area, granted)"
            " VALUES (%s, %s, %s, true)",
            [(session.pmc_id, row[0], area) for area in dict.fromkeys(new.areas)],
        )
    return Created(id=row[0])


def role_of(conn: psycopg.Connection[Any], pmc_id: UUID, role_id: UUID) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM trust_custom_roles WHERE pmc_id = %s AND id = %s", (pmc_id, role_id)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such role")


class AreaChange(BaseModel):
    area: Area
    granted: bool


@router.post("/{role_id}/areas", status_code=204)
def change_area(session: Staff, conn: Conn, role_id: UUID, change: AreaChange) -> None:
    """Allows or stops allowing an area; asking for what already holds writes nothing."""
    role_of(conn, session.pmc_id, role_id)
    held = conn.execute(
        "SELECT granted FROM trust_custom_role_areas WHERE role_id = %s AND area = %s"
        " ORDER BY id DESC LIMIT 1",
        (role_id, change.area),
    ).fetchone()
    if (held[0] if held else False) != change.granted:
        conn.execute(
            "INSERT INTO trust_custom_role_areas (pmc_id, role_id, area, granted)"
            " VALUES (%s, %s, %s, %s)",
            (session.pmc_id, role_id, change.area, change.granted),
        )


class StaffChange(BaseModel):
    staff_id: UUID
    granted: bool


@router.post("/{role_id}/staff", status_code=204)
def change_staff(session: Staff, conn: Conn, role_id: UUID, change: StaffChange) -> None:
    """Gives a staff member the role or takes it away; asking for what already holds writes
    nothing."""
    role_of(conn, session.pmc_id, role_id)
    if (
        conn.execute(
            "SELECT 1 FROM trust_staff WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, change.staff_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such staff member in this PMC")
    held = conn.execute(
        "SELECT granted FROM trust_staff_custom_roles WHERE role_id = %s AND staff_id = %s"
        " ORDER BY id DESC LIMIT 1",
        (role_id, change.staff_id),
    ).fetchone()
    if (held[0] if held else False) != change.granted:
        conn.execute(
            "INSERT INTO trust_staff_custom_roles (pmc_id, staff_id, role_id, granted)"
            " VALUES (%s, %s, %s, %s)",
            (session.pmc_id, change.staff_id, role_id, change.granted),
        )
