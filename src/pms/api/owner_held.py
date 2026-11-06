"""Deposits the owner holds (migration 20261106000241, the founder's chart of accounts pass, part
2): some owners keep a lease's security, pet or key deposit themselves, outside trust. Staff
record it from the lease page (each tenant's, by kind, its amount and the day it was held from)
and, at move-out, what the owner returned and kept of it. No money moves and no ledger account
holds it: the lease page, Move-outs, the tenant ledger and the owner's statement show it beside
what trust holds, on a memo line (code 2150 in the chart).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from pms import days
from pms.api.deps import Conn, Staff, refusal

router = APIRouter()

Kind = Literal["security", "pet", "key"]
Amount = Annotated[Decimal, Field(gt=0, le=Decimal("1000000"), decimal_places=2)]
Part = Annotated[Decimal, Field(ge=0, le=Decimal("1000000"), decimal_places=2)]
Memo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


def cents(value: Decimal) -> str:
    return f"{Decimal(value):.2f}"


def dollars(value: Decimal) -> str:
    return f"${Decimal(value):,.2f}"


class Settled(BaseModel):
    returned: str
    kept: str
    on: date
    memo: str | None


class OwnerHeld(BaseModel):
    id: UUID
    tenant_id: UUID
    tenant: str
    kind: Kind
    amount: str
    held_since: date
    memo: str | None
    # What the owner returned and kept of it at move-out; None until staff record it.
    settled: Settled | None


HELD = """
SELECT h.lease_id, h.id, h.tenant_id, t.display_name, h.deposit_kind, h.amount, h.held_since,
       h.memo, o.returned, o.kept, o.on_day, o.memo
FROM trust_owner_held_deposits AS h
JOIN trust_tenants AS t ON t.id = h.tenant_id
LEFT JOIN trust_owner_held_deposit_outcomes AS o ON o.held_id = h.id
WHERE h.pmc_id = %(pmc)s AND (%(lease)s::uuid IS NULL OR h.lease_id = %(lease)s)
  AND (%(held)s::uuid IS NULL OR h.id = %(held)s)
ORDER BY h.lease_id, t.display_name COLLATE "C",
         CASE h.deposit_kind WHEN 'security' THEN 0 WHEN 'key' THEN 1 ELSE 2 END, h.id
"""


def held_by_owner(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    lease_id: UUID | None = None,
    held_id: UUID | None = None,
) -> dict[UUID, list[OwnerHeld]]:
    """Each lease's deposits the owner holds (of the PMC, or one lease), each tenant's security
    deposit first, then key, then pet, with what the owner did with it at move-out."""
    found: dict[UUID, list[OwnerHeld]] = {}
    rows = conn.execute(HELD, {"pmc": pmc_id, "lease": lease_id, "held": held_id}).fetchall()
    for lease, i, tenant, name, kind, amount, since, memo, back, kept, on, said in rows:
        found.setdefault(lease, []).append(
            OwnerHeld(
                id=i,
                tenant_id=tenant,
                tenant=name,
                kind=kind,
                amount=cents(amount),
                held_since=since,
                memo=memo,
                settled=None
                if on is None
                else Settled(returned=cents(back), kept=cents(kept), on=on, memo=said),
            )
        )
    return found


class NewOwnerHeld(BaseModel):
    tenant_id: UUID
    kind: Kind = "security"
    amount: Amount
    held_since: date
    memo: Memo | None = None


@router.post("/api/rent/leases/{lease_id}/owner-held-deposits", status_code=201)
def record(session: Staff, conn: Conn, lease_id: UUID, new: NewOwnerHeld) -> OwnerHeld:
    """Records that the owner holds a tenant's deposit of the lease, outside trust: once per
    tenant and kind, from a day not after today. No money moves."""
    if new.held_since > days.today(conn, session.pmc_id):
        raise HTTPException(422, "a deposit the owner holds is recorded from a day not after today")
    if not conn.execute(
        "SELECT 1 FROM trust_leases WHERE pmc_id = %s AND id = %s", (session.pmc_id, lease_id)
    ).fetchone():
        raise HTTPException(404, "no such lease")
    if not conn.execute(
        "SELECT 1 FROM trust_lease_tenants WHERE pmc_id = %s AND lease_id = %s AND tenant_id = %s",
        (session.pmc_id, lease_id, new.tenant_id),
    ).fetchone():
        raise HTTPException(404, "no such tenant on this lease")
    try:
        with conn.transaction():
            made = conn.execute(
                "INSERT INTO trust_owner_held_deposits (pmc_id, lease_id, tenant_id, deposit_kind,"
                " amount, held_since, memo) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    session.pmc_id,
                    lease_id,
                    new.tenant_id,
                    new.kind,
                    new.amount,
                    new.held_since,
                    new.memo,
                ),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            409, f"the owner's {new.kind} deposit for this tenant is recorded already"
        ) from None
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, refusal(refused)) from None
    assert made is not None
    return held_by_owner(conn, session.pmc_id, lease_id, made[0])[lease_id][0]


class Outcome(BaseModel):
    # What the owner gave back to the tenant and what they kept: together what they held.
    returned: Part
    kept: Part
    on: date
    memo: Memo | None = None


@router.post("/api/move-out/{lease_id}/owner-held-deposits/{held_id}/settle", status_code=201)
def settle(
    session: Staff, conn: Conn, lease_id: UUID, held_id: UUID, outcome: Outcome
) -> OwnerHeld:
    """Records at move-out what the owner returned and kept of a deposit they hold, once, the
    lease's last day set. The owner pays it; nothing moves in trust."""
    found = held_by_owner(conn, session.pmc_id, lease_id, held_id).get(lease_id)
    if not found:
        raise HTTPException(404, "no such deposit the owner holds on this lease")
    held = found[0]
    if held.settled is not None:
        raise HTTPException(409, "what the owner did with this deposit is recorded already")
    if outcome.on > days.today(conn, session.pmc_id):
        raise HTTPException(422, "what the owner did is recorded on a day not after today")
    if outcome.returned + outcome.kept != Decimal(held.amount):
        raise HTTPException(
            422,
            f"what the owner returned and kept come to the {dollars(Decimal(held.amount))} they"
            " held",
        )
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_owner_held_deposit_outcomes (held_id, pmc_id, returned, kept,"
                " on_day, memo) VALUES (%s, %s, %s, %s, %s, %s)",
                (held_id, session.pmc_id, outcome.returned, outcome.kept, outcome.on, outcome.memo),
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            409, "what the owner did with this deposit is recorded already"
        ) from None
    except psycopg.errors.CheckViolation as refused:  # the lease's last day not set, say
        raise HTTPException(409, refusal(refused)) from None
    return held_by_owner(conn, session.pmc_id, lease_id, held_id)[lease_id][0]
