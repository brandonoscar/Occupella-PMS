"""The owner portal's second version (the founder's choice of 11/03, "C"): what an owner sees
of each property they hold money for (its units, who rents each and for how much, what they owe,
the units vacant and since when), the work orders at their properties, and their own contact
details, which they change themselves. Tenants' names, never their contact details. For that
owner only: the owner and the PMC come from the session, and another owner's property reads as
not found. Read only but the owner's own contact details; nothing here moves money.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pms import exports
from pms.api import addresses, directory, leaving, rentals, work_orders
from pms.api.deps import Conn, Owner
from pms.api.rent import cents
from pms.api.rent import today as rent_today

router = APIRouter(prefix="/api/owner")


class OwnedLease(BaseModel):
    tenants: list[str]
    starts_on: date
    ends_on: date | None
    monthly_rent: str
    # What the lease owes at the end of today.
    owed: str
    status: Literal["current", "future", "ended"]
    # The day its tenants said they will move out, when they gave notice.
    leaving_on: date | None


class OwnedUnit(BaseModel):
    id: UUID
    name: str
    # The lease in effect today, or else the next to start; None when vacant.
    lease: OwnedLease | None
    # Vacant: since the day after its last lease ended; None if it was never leased in the PMS.
    vacant_since: date | None


class OwnedProperty(BaseModel):
    property_id: UUID
    name: str
    address: str | None
    # What the owner's accounts at the property hold today.
    balance: str
    units: list[OwnedUnit]
    occupied: int
    vacant: int
    # What the property's leases in effect owe at the end of today.
    owed: str


def owned_properties(conn: psycopg.Connection[Any], pmc: UUID, owner: UUID) -> list[UUID]:
    return [
        p
        for (p,) in conn.execute(
            "SELECT DISTINCT property_id FROM trust_ledger_accounts"
            " WHERE pmc_id = %s AND owner_id = %s AND kind = 'owner_property'",
            (pmc, owner),
        ).fetchall()
    ]


@router.get("/properties/{property_id}")
def owned_property(session: Owner, conn: Conn, property_id: UUID) -> OwnedProperty:
    """One of the owner's properties: each unit (archived ones left out), with the lease in effect
    today or the next to start (its tenants' names, rent, what it owes and any notice to move
    out), or since when it is vacant. Another's property reads as not found."""
    if property_id not in owned_properties(conn, session.pmc_id, session.person_id):
        raise HTTPException(404, "no such property of yours")
    day = rent_today()
    found = conn.execute(
        "SELECT p.display_name, coalesce(sum(a.balance), 0)"
        " FROM trust_properties AS p"
        " JOIN trust_ledger_accounts AS t ON t.property_id = p.id AND t.owner_id = %s"
        "  AND t.kind = 'owner_property'"
        " JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id"
        " WHERE p.pmc_id = %s AND p.id = %s GROUP BY p.display_name",
        (session.person_id, session.pmc_id, property_id),
    ).fetchone()
    assert found is not None
    leases: dict[UUID, tuple[UUID, rentals.LeaseLine]] = {}
    for unit, lease in sorted(
        rentals.lease_lines(
            conn,
            session.pmc_id,
            day,
            "u.property_id = %(property)s AND (l.ends_on IS NULL OR l.ends_on >= %(day)s)",
            property=property_id,
        ),
        key=lambda pair: (pair[1].status != "current", pair[1].starts_on),
    ):
        leases.setdefault(unit, (lease.id, lease))
    ended = dict(
        conn.execute(
            "SELECT l.unit_id, max(l.ends_on) + 1 FROM trust_leases AS l"
            " JOIN trust_units AS u ON u.id = l.unit_id"
            " WHERE u.property_id = %s AND l.ends_on < %s GROUP BY l.unit_id",
            (property_id, day),
        ).fetchall()
    )
    units = []
    for u, name in conn.execute(
        "SELECT u.id, u.display_name FROM trust_units AS u"
        " WHERE u.property_id = %s AND NOT trust_is_archived(NULL, u.id)"
        ' ORDER BY u.display_name COLLATE "C", u.id',
        (property_id,),
    ).fetchall():
        line = leases.get(u)
        owned = None
        if line is not None:
            lease_id, lease = line
            notice = leaving.notice_of(conn, session.pmc_id, lease_id)
            owned = OwnedLease(
                tenants=lease.tenants,
                starts_on=lease.starts_on,
                ends_on=lease.ends_on,
                monthly_rent=lease.monthly_rent,
                owed=lease.owed,
                status=lease.status,
                leaving_on=None if notice is None else notice.leaving_on,
            )
        current = owned is not None and owned.status == "current"
        units.append(
            OwnedUnit(id=u, name=name, lease=owned, vacant_since=None if current else ended.get(u))
        )
    address = addresses.address_of(conn, property_id)
    occupied = sum(1 for unit in units if unit.lease is not None and unit.lease.status == "current")
    return OwnedProperty(
        property_id=property_id,
        name=found[0],
        address=None if address is None else address.one_line,
        balance=exports.text(found[1]),
        units=units,
        occupied=occupied,
        vacant=len(units) - occupied,
        owed=cents(
            sum(
                (
                    Decimal(unit.lease.owed)
                    for unit in units
                    if unit.lease is not None and unit.lease.status == "current"
                ),
                Decimal(0),
            )
        ),
    )


@router.get("/work-orders")
def owned_work_orders(session: Owner, conn: Conn) -> list[work_orders.WorkOrderRow]:
    """The work orders at the owner's properties, newest first (200 at most): what, where, its
    status, priority, vendor, the visit and what was billed. No tenant's contact details."""
    owned = owned_properties(conn, session.pmc_id, session.person_id)
    found = conn.execute(
        work_orders.WORK_ORDERS.format(where="w.property_id = ANY(%(owned)s)"),
        work_orders.query(pmc=session.pmc_id, owned=owned, per_page=200, offset=0),
    ).fetchall()
    return work_orders.rows_of(found)


@router.get("/contact")
def my_contact(session: Owner, conn: Conn) -> directory.Contact:
    """The owner's contact details as the company has them."""
    return directory.contact_of(conn, "owner_id", session.person_id)


@router.post("/contact", status_code=204)
def change_my_contact(session: Owner, conn: Conn, body: directory.Contact) -> None:
    """The owner's own email, phones and mailing address, as they give them (all of them, so a
    field left empty is cleared): the company's emails and statements go to this email from now
    on, and staff see on the owner's page that the owner changed them. Their sign-in email stays
    as it is."""
    directory.record_contact(
        conn, session.pmc_id, "owner_id", session.person_id, body, by_person=True
    )
