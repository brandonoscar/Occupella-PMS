"""Who is on a lease besides its tenants (migration 0080; the founder's review, q011 and q012):
others living there, their pets and vehicles, and the lease's guarantors. Staff add them on the
lease's Tenants tab and take one off from its row; nothing is deleted (a removal is a row of its
own) and none of it moves money. An assistance animal is not a pet under the Fair Housing Act: no
pet rent, fee or deposit is charged for it (docs/law.md). The AI role reads none of it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from psycopg import sql
from pydantic import BaseModel, Field, StringConstraints

from pms import exports
from pms.api.deps import Conn, Staff
from pms.api.directory import Email, Phone

router = APIRouter(prefix="/api/leases")


Long = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Medium = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Brief = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
Plate = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=15)]
Weight = Annotated[Decimal, Field(gt=0, le=500, decimal_places=1)]


class Occupant(BaseModel):
    id: UUID
    name: str
    relationship: str | None


class Pet(BaseModel):
    id: UUID
    name: str
    kind: str
    breed: str | None
    weight_lbs: str | None
    assistance_animal: bool


class Vehicle(BaseModel):
    id: UUID
    make: str
    model: str | None
    color: str | None
    plate: str
    plate_state: str | None


class Guarantor(BaseModel):
    id: UUID
    name: str
    email: str | None
    phone: str | None
    # Whether they signed the lease offer this lease was made of, as its guarantor.
    signed: bool


class Household(BaseModel):
    occupants: list[Occupant]
    pets: list[Pet]
    vehicles: list[Vehicle]
    guarantors: list[Guarantor]


class Created(BaseModel):
    id: UUID


def lease_of(conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID) -> None:
    """404 unless the PMC has this lease."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_leases WHERE pmc_id = %s AND id = %s", (pmc_id, lease_id)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such lease")


# Each kind's table and the column a removal names it by.
KINDS = {
    "occupants": ("trust_lease_occupants", "occupant_id"),
    "pets": ("trust_lease_pets", "pet_id"),
    "vehicles": ("trust_lease_vehicles", "vehicle_id"),
    "guarantors": ("trust_lease_guarantors", "guarantor_id"),
}
Kind = Literal["occupants", "pets", "vehicles", "guarantors"]


def current(kind: Kind, *columns: str) -> sql.Composed:
    """The lease's rows of a kind not taken off, in the order they were added."""
    table, named_by = KINDS[kind]
    return sql.SQL(
        "SELECT x.id, {columns} FROM {table} AS x"
        " WHERE x.pmc_id = %s AND x.lease_id = %s AND NOT EXISTS ("
        "   SELECT 1 FROM trust_lease_household_removals AS r WHERE r.{named_by} = x.id)"
        " ORDER BY x.created_at, x.id"
    ).format(
        columns=sql.SQL(", ").join(sql.Identifier("x", c) for c in columns),
        table=sql.Identifier(table),
        named_by=sql.Identifier(named_by),
    )


@router.get("/{lease_id}/household")
def household(session: Staff, conn: Conn, lease_id: UUID) -> Household:
    """The lease's other occupants, pets, vehicles and guarantors, as they are now."""
    lease_of(conn, session.pmc_id, lease_id)
    key = (session.pmc_id, lease_id)
    return Household(
        occupants=[
            Occupant(id=i, name=n, relationship=r)
            for i, n, r in conn.execute(
                current("occupants", "display_name", "relationship"), key
            ).fetchall()
        ],
        pets=[
            Pet(
                id=i,
                name=n,
                kind=k,
                breed=b,
                weight_lbs=None if w is None else exports.text(w),
                assistance_animal=a,
            )
            for i, n, k, b, w, a in conn.execute(
                current("pets", "display_name", "kind", "breed", "weight_lbs", "assistance_animal"),
                key,
            ).fetchall()
        ],
        vehicles=[
            Vehicle(id=i, make=mk, model=md, color=c, plate=p, plate_state=s)
            for i, mk, md, c, p, s in conn.execute(
                current("vehicles", "make", "model", "color", "plate", "plate_state"), key
            ).fetchall()
        ],
        guarantors=[
            Guarantor(id=i, name=n, email=e, phone=p, signed=o is not None)
            for i, n, e, p, o in conn.execute(
                current("guarantors", "display_name", "email", "phone", "offer_id"), key
            ).fetchall()
        ],
    )


def added(conn: psycopg.Connection[Any], statement: str, params: tuple[Any, ...]) -> Created:
    row = conn.execute(statement, params).fetchone()
    assert row is not None
    return Created(id=row[0])


class NewOccupant(BaseModel):
    name: Long
    relationship: Medium | None = None


@router.post("/{lease_id}/occupants", status_code=201)
def add_occupant(session: Staff, conn: Conn, lease_id: UUID, new: NewOccupant) -> Created:
    """Someone living there who isn't a tenant on the lease."""
    lease_of(conn, session.pmc_id, lease_id)
    return added(
        conn,
        "INSERT INTO trust_lease_occupants (pmc_id, lease_id, display_name, relationship)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (session.pmc_id, lease_id, new.name, new.relationship),
    )


class NewPet(BaseModel):
    name: Medium
    kind: Short
    breed: Medium | None = None
    weight_lbs: Weight | None = None
    # A service or support animal: not a pet, and charged no pet rent, fee or deposit.
    assistance_animal: bool = False


@router.post("/{lease_id}/pets", status_code=201)
def add_pet(session: Staff, conn: Conn, lease_id: UUID, new: NewPet) -> Created:
    """A pet, or an assistance animal, living there."""
    lease_of(conn, session.pmc_id, lease_id)
    return added(
        conn,
        "INSERT INTO trust_lease_pets"
        " (pmc_id, lease_id, display_name, kind, breed, weight_lbs, assistance_animal)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            session.pmc_id,
            lease_id,
            new.name,
            new.kind,
            new.breed,
            new.weight_lbs,
            new.assistance_animal,
        ),
    )


class NewVehicle(BaseModel):
    make: Short
    model: Short | None = None
    color: Brief | None = None
    plate: Plate
    plate_state: Brief | None = None


@router.post("/{lease_id}/vehicles", status_code=201)
def add_vehicle(session: Staff, conn: Conn, lease_id: UUID, new: NewVehicle) -> Created:
    """A vehicle kept there."""
    lease_of(conn, session.pmc_id, lease_id)
    return added(
        conn,
        "INSERT INTO trust_lease_vehicles"
        " (pmc_id, lease_id, make, model, color, plate, plate_state)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (session.pmc_id, lease_id, new.make, new.model, new.color, new.plate, new.plate_state),
    )


class NewGuarantor(BaseModel):
    name: Long
    email: Email | None = None
    phone: Phone | None = None


@router.post("/{lease_id}/guarantors", status_code=201)
def add_guarantor(session: Staff, conn: Conn, lease_id: UUID, new: NewGuarantor) -> Created:
    """Someone who guarantees the lease without living there."""
    lease_of(conn, session.pmc_id, lease_id)
    return added(
        conn,
        "INSERT INTO trust_lease_guarantors (pmc_id, lease_id, display_name, email, phone)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (
            session.pmc_id,
            lease_id,
            new.name,
            None if new.email is None else new.email.lower(),
            new.phone,
        ),
    )


@router.post("/{lease_id}/{kind}/{item_id}/remove", status_code=204)
def remove(session: Staff, conn: Conn, lease_id: UUID, kind: Kind, item_id: UUID) -> None:
    """Takes an occupant, pet, vehicle or guarantor off the lease, once."""
    table, named_by = KINDS[kind]
    row = conn.execute(
        sql.SQL(
            "SELECT EXISTS (SELECT 1 FROM trust_lease_household_removals AS r"
            "               WHERE r.{named_by} = x.id)"
            " FROM {table} AS x WHERE x.pmc_id = %s AND x.lease_id = %s AND x.id = %s"
        ).format(named_by=sql.Identifier(named_by), table=sql.Identifier(table)),
        (session.pmc_id, lease_id, item_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"no such {kind.removesuffix('s')} on this lease")
    if row[0]:
        raise HTTPException(409, "taken off this lease already")
    conn.execute(
        sql.SQL("INSERT INTO trust_lease_household_removals (pmc_id, {}) VALUES (%s, %s)").format(
            sql.Identifier(named_by)
        ),
        (session.pmc_id, item_id),
    )
