"""Property groups (the founder's punch list, 2026-10-16; migration 0065): a PMC's named sets of
its properties, which staff narrow the list pages to (`pms.api.scope`). Staff make a group,
rename it and choose its properties at Settings, Property groups. A group is never deleted; a
property joins or leaves one by a row of `trust_property_group_members`, which names who did it.
Nothing here moves money.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from pms.api.deps import Conn, Staff, refusal
from pms.api.rentals import Ref

router = APIRouter(prefix="/api")

GroupName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Properties = Annotated[list[UUID], Field(max_length=5000)]


class Group(BaseModel):
    id: UUID
    name: str
    properties: list[Ref]


class Groups(BaseModel):
    groups: list[Group]
    # Every property not archived, for the picker and for choosing a group's properties.
    properties: list[Ref]


GROUPS = """
SELECT g.id, g.display_name,
       coalesce(array_agg(p.id ORDER BY p.display_name COLLATE "C", p.id)
                FILTER (WHERE p.id IS NOT NULL), '{}'),
       coalesce(array_agg(p.display_name ORDER BY p.display_name COLLATE "C", p.id)
                FILTER (WHERE p.id IS NOT NULL), '{}')
FROM trust_property_groups AS g
LEFT JOIN trust_properties AS p ON p.id IN (SELECT trust_group_properties(g.id))
WHERE g.pmc_id = %s
GROUP BY g.id
ORDER BY g.display_name COLLATE "C", g.id
"""


@router.get("/property-groups")
def groups(session: Staff, conn: Conn) -> Groups:
    """The company's property groups, each with its properties, and every property not
    archived."""
    return Groups(
        groups=[
            Group(id=i, name=n, properties=[Ref(id=p, name=m) for p, m in zip(ps, ms, strict=True)])
            for i, n, ps, ms in conn.execute(GROUPS, (session.pmc_id,)).fetchall()
        ],
        properties=[
            Ref(id=i, name=n)
            for i, n in conn.execute(
                "SELECT p.id, p.display_name FROM trust_properties AS p"
                " WHERE p.pmc_id = %s AND NOT trust_is_archived(p.id, NULL)"
                ' ORDER BY p.display_name COLLATE "C", p.id',
                (session.pmc_id,),
            ).fetchall()
        ],
    )


class NewGroup(BaseModel):
    name: GroupName
    property_ids: Properties = []


class Created(BaseModel):
    id: UUID


class Rename(BaseModel):
    name: GroupName


class Members(BaseModel):
    property_ids: Properties


def set_members(conn: psycopg.Connection[Any], pmc: UUID, group: UUID, wanted: list[UUID]) -> None:
    """Makes the group's properties exactly `wanted`: a row for each that joins or leaves."""
    unknown = conn.execute(
        "SELECT count(*) FROM unnest(%s::uuid[]) AS w(id) WHERE NOT EXISTS"
        " (SELECT 1 FROM trust_properties AS p WHERE p.pmc_id = %s AND p.id = w.id)",
        (wanted, pmc),
    ).fetchone()
    if unknown is not None and unknown[0]:
        raise HTTPException(404, "no such property")
    now = {row[0] for row in conn.execute("SELECT trust_group_properties(%s)", (group,)).fetchall()}
    changes = [(p, True) for p in dict.fromkeys(wanted) if p not in now]
    changes += [(p, False) for p in sorted(now - set(wanted))]
    try:
        for property_id, member in changes:
            conn.execute(
                "INSERT INTO trust_property_group_members (pmc_id, group_id, property_id, member)"
                " VALUES (%s, %s, %s, %s)",
                (pmc, group, property_id, member),
            )
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, refusal(refused)) from None


def group_of(conn: psycopg.Connection[Any], pmc: UUID, group: UUID) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM trust_property_groups WHERE pmc_id = %s AND id = %s", (pmc, group)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such property group")


@router.post("/property-groups", status_code=201)
def create_group(session: Staff, conn: Conn, body: NewGroup) -> Created:
    """Makes a property group with its properties."""
    try:
        row = conn.execute(
            "INSERT INTO trust_property_groups (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
            (session.pmc_id, body.name),
        ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, f"a property group is called {body.name} already") from None
    assert row is not None
    set_members(conn, session.pmc_id, row[0], body.property_ids)
    return Created(id=row[0])


@router.post("/property-groups/{group_id}/name", status_code=204)
def rename_group(session: Staff, conn: Conn, group_id: UUID, body: Rename) -> None:
    """Renames a property group; the audit log keeps the name it had."""
    group_of(conn, session.pmc_id, group_id)
    try:
        conn.execute(
            "UPDATE trust_property_groups SET display_name = %s WHERE pmc_id = %s AND id = %s",
            (body.name, session.pmc_id, group_id),
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, f"a property group is called {body.name} already") from None


@router.post("/property-groups/{group_id}/properties", status_code=204)
def choose_properties(session: Staff, conn: Conn, group_id: UUID, body: Members) -> None:
    """Makes a group's properties exactly these: those added join it, those left out leave it."""
    group_of(conn, session.pmc_id, group_id)
    set_members(conn, session.pmc_id, group_id, body.property_ids)
