"""The PMC's staff and their roles (migration 0034): admin (changes roles; invites and disables
staff logins), approve (approves a month-end, signs off a cutover) and disburse (pays bills and
owner draws). Every staff member sees who holds what; an admin adds staff members and changes
roles. The database keeps the rules: only an admin changes a role, a change changes something,
and a PMC keeps one admin.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StringConstraints

from pms import accounts
from pms.api.deps import Conn, Staff, refusal

router = APIRouter(prefix="/api/staff")

ROLES = ("admin", "approve", "disburse")
Role = Literal["admin", "approve", "disburse"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

HOLDS = (
    "SELECT array(SELECT r FROM unnest(%(roles)s::text[]) WITH ORDINALITY AS u (r, n)"
    " WHERE trust_staff_holds(%(pmc)s, {staff}, r) ORDER BY n)"
)


def roles_of(conn: psycopg.Connection[Any], session: accounts.Session) -> list[str]:
    """The roles a signed-in staff member holds, as the database counts them."""
    row = conn.execute(
        HOLDS.format(staff="%(staff)s"),
        {"roles": list(ROLES), "pmc": session.pmc_id, "staff": session.person_id},
    ).fetchone()
    assert row is not None
    return list(row[0])


class Member(BaseModel):
    staff_id: UUID
    name: str
    roles: list[Role]


@router.get("")
def members(session: Staff, conn: Conn) -> list[Member]:
    rows = conn.execute(
        "SELECT s.id, s.display_name, (" + HOLDS.format(staff="s.id") + ")"
        ' FROM trust_staff AS s WHERE s.pmc_id = %(pmc)s ORDER BY s.display_name COLLATE "C", s.id',
        {"roles": list(ROLES), "pmc": session.pmc_id},
    ).fetchall()
    return [Member(staff_id=i, name=n, roles=r) for i, n, r in rows]


class NewMember(BaseModel):
    name: Name


class Created(BaseModel):
    id: UUID


@router.post("", status_code=201)
def add(session: Staff, conn: Conn, new: NewMember) -> Created:
    """A staff member with no role yet; an admin invites them and gives them roles."""
    if "admin" not in roles_of(conn, session):
        raise HTTPException(403, "only an admin adds a staff member")
    row = conn.execute(
        "INSERT INTO trust_staff (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
        (session.pmc_id, new.name),
    ).fetchone()
    assert row is not None
    return Created(id=row[0])


class RoleChange(BaseModel):
    role: Role
    granted: bool


@router.post("/{staff_id}/roles", status_code=204)
def change(session: Staff, conn: Conn, staff_id: UUID, body: RoleChange) -> None:
    """Grants or takes away a role. Only an admin does (403); a change changes something and a
    PMC keeps an admin (409)."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_staff WHERE pmc_id = %s AND id = %s", (session.pmc_id, staff_id)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such staff member in this PMC")
    try:
        conn.execute(
            "INSERT INTO trust_staff_roles (pmc_id, staff_id, role, granted)"
            " VALUES (%s, %s, %s, %s)",
            (session.pmc_id, staff_id, body.role, body.granted),
        )
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, refusal(refused)) from None
