"""Staff's move-outs (migration 0035): end a lease, charge what the tenants owe for cleaning or
damage, and return their security deposits, keeping what the lease owes.

The return goes through trust_return_deposit, once per lease: what the lease owes is kept for
the owner's account and matched to its charges oldest first, the rest goes back to each tenant.
It pays out money, so it takes the disburse role (the database refuses anyone else: 403). The
itemized statement staff send the tenants is trust_report_deposit_return, downloaded like any
report. The PMS records what the bank did; it sends no money.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from pms import exports
from pms.api.deps import Conn, Staff, refusal

router = APIRouter(prefix="/api/move-out")

Amount = Annotated[Decimal, Field(gt=0, le=Decimal("1000000"), decimal_places=2)]
Memo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


def cents(value: Decimal) -> str:
    return exports.text(Decimal(value).quantize(Decimal("0.01")))


def returned(conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID) -> bool:
    """Whether the lease's deposit was returned; 404 when it is no lease of the PMC."""
    row = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM trust_deposit_returns AS r WHERE r.lease_id = l.id)"
        " FROM trust_leases AS l WHERE l.pmc_id = %s AND l.id = %s",
        (pmc_id, lease_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such lease")
    return bool(row[0])


# --- what each lease holds and owes ----------------------------------------------------------


class Charge(BaseModel):
    id: UUID
    due_on: date
    kind: Literal["rent", "fee"]
    memo: str | None
    open: str


class OwnerAccount(BaseModel):
    id: str
    owner: str


class Settled(BaseModel):
    held: str
    kept: str
    returned: str
    returned_on: date


class MoveOut(BaseModel):
    lease_id: UUID
    property: str
    unit: str
    tenants: list[str]
    starts_on: date
    ends_on: date | None
    # What the lease owes now, and the deposits held for its tenants now.
    owed: str
    held: str
    open_charges: list[Charge]
    # The owner's accounts kept money may go to: staff choose when there are several.
    owner_accounts: list[OwnerAccount]
    settled: Settled | None


LEASES = """
SELECT l.id, p.display_name, u.display_name, l.starts_on, l.ends_on, trust_lease_owed(l.id),
       array(SELECT t.display_name FROM trust_lease_tenants AS lt
             JOIN trust_tenants AS t ON t.id = lt.tenant_id WHERE lt.lease_id = l.id
             ORDER BY t.display_name COLLATE "C", t.id),
       coalesce((SELECT sum(b.balance) FROM trust_lease_tenants AS lt
                 JOIN trust_ledger_accounts AS a ON a.tenant_id = lt.tenant_id
                 JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
                 WHERE lt.lease_id = l.id AND a.kind = 'tenant_deposit'), 0),
       u.property_id, r.held, r.kept, r.returned, r.returned_at
FROM trust_leases AS l
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
LEFT JOIN trust_deposit_returns AS r ON r.lease_id = l.id
WHERE l.pmc_id = %s
ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C", l.starts_on, l.id
"""

OPEN = """
SELECT c.id, c.due_on, c.kind, c.memo,
       c.amount - coalesce((SELECT sum(p.amount) FROM trust_charge_payments AS p
                            WHERE p.charge_id = c.id), 0)
                + coalesce((SELECT sum(r.amount) FROM trust_payment_reversals AS r
                            WHERE r.charge_id = c.id), 0)
                - coalesce((SELECT sum(k.amount) FROM trust_charge_waivers AS w
                            JOIN trust_charges AS k ON k.id = w.credit_charge_id
                            WHERE w.waived_charge_id = c.id), 0)
FROM trust_charges AS c
WHERE c.lease_id = %s AND c.kind <> 'credit'
ORDER BY c.due_on, c.created_at, c.id
"""

OWNERS = """
SELECT a.ledger_account_id, o.display_name FROM trust_ledger_accounts AS a
JOIN trust_owners AS o ON o.id = a.owner_id
WHERE a.pmc_id = %s AND a.property_id = %s AND a.kind = 'owner_property'
ORDER BY o.display_name COLLATE "C", a.ledger_account_id
"""


# A lease with no deposit stays on the list this long after its last day.
RECENT = timedelta(days=90)


@router.get("")
def move_outs(session: Staff, conn: Conn) -> list[MoveOut]:
    """Every lease that holds a deposit for its tenants or whose deposit was returned, and every
    lease ending (its last day set) that holds none, until 90 days after its last day."""
    found = []
    since = datetime.now(UTC).date() - RECENT
    for row in conn.execute(LEASES, (session.pmc_id,)).fetchall():
        lease, prop, unit, starts, ends, owed, tenants, held, prop_id, *done = row
        if held <= 0 and done[0] is None and (ends is None or ends < since):
            continue
        found.append(
            MoveOut(
                lease_id=lease,
                property=prop,
                unit=unit,
                tenants=tenants,
                starts_on=starts,
                ends_on=ends,
                owed=cents(owed),
                held=cents(held),
                open_charges=[
                    Charge(id=i, due_on=d, kind=k, memo=m, open=cents(o))
                    for i, d, k, m, o in conn.execute(OPEN, (lease,)).fetchall()
                    if o > 0
                ],
                owner_accounts=[
                    OwnerAccount(id=i, owner=o)
                    for i, o in conn.execute(OWNERS, (session.pmc_id, prop_id)).fetchall()
                ],
                settled=None
                if done[0] is None
                else Settled(
                    held=cents(done[0]),
                    kept=cents(done[1]),
                    returned=cents(done[2]),
                    returned_on=done[3].astimezone(UTC).date(),
                ),
            )
        )
    return found


# --- ending the lease and charging what is owed ---------------------------------------------


class End(BaseModel):
    ends_on: date
    # Charge the lease-break fee (due on the new last day) as a lease with an end date ends
    # before it.
    break_fee: bool = False


class BreakFee(BaseModel):
    # Whether ending on the day ends the lease before its end date (a month-to-month lease
    # never breaks).
    early: bool
    # The fee under the property's terms in force on the lease's first day, at the rent in force
    # on the new last day; None when the terms charge none.
    fee: str | None
    flat_fee: str | None
    rent_months: str | None


BREAK_FEE = """
SELECT l.ends_on, b.flat_fee, b.rent_months, trust_rent_on(l.id, %(day)s)
FROM trust_leases AS l
JOIN trust_units AS u ON u.id = l.unit_id
LEFT JOIN LATERAL (
    SELECT f.flat_fee, f.rent_months FROM trust_lease_break_fees AS f
    WHERE f.property_id = u.property_id AND f.starts_on <= l.starts_on
    ORDER BY f.starts_on DESC LIMIT 1
) AS b ON true
WHERE l.pmc_id = %(pmc)s AND l.id = %(lease)s
"""


def break_fee_of(
    conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID, ends_on: date
) -> BreakFee:
    row = conn.execute(BREAK_FEE, {"pmc": pmc_id, "lease": lease_id, "day": ends_on}).fetchone()
    if row is None:
        raise HTTPException(404, "no such lease")
    last, flat, months, rent = row
    fee = None
    if flat is not None:
        amount = flat + (months * rent).quantize(Decimal("0.01"))
        fee = cents(amount) if amount > 0 else None
    return BreakFee(
        early=last is not None and ends_on < last,
        fee=fee,
        flat_fee=None if flat is None else cents(flat),
        rent_months=None if months is None else exports.text(months),
    )


@router.get("/{lease_id}/break-fee")
def break_fee(session: Staff, conn: Conn, lease_id: UUID, ends_on: date) -> BreakFee:
    """The lease-break fee ending the lease on a day would charge, if staff charge it."""
    return break_fee_of(conn, session.pmc_id, lease_id, ends_on)


@router.post("/{lease_id}/end", status_code=204)
def end(session: Staff, conn: Conn, lease_id: UUID, body: End) -> None:
    """Sets the lease's last day (before its deposit is returned), and charges the lease-break
    fee when staff ask and the lease ends before its end date."""
    if returned(conn, session.pmc_id, lease_id):
        raise HTTPException(409, "the deposit was returned; the lease's days stay as they were")
    fee = None
    if body.break_fee:
        found = break_fee_of(conn, session.pmc_id, lease_id, body.ends_on)
        if not found.early:
            raise HTTPException(
                422, "a lease-break fee is charged only as a lease ends before its end date"
            )
        if found.fee is None:
            raise HTTPException(422, "this lease's property charges no lease-break fee")
        fee = found.fee
    try:
        conn.execute("SELECT trust_end_lease(%s, %s, %s)", (session.pmc_id, lease_id, body.ends_on))
    except psycopg.errors.CheckViolation as refused:  # before it starts
        raise HTTPException(422, refusal(refused)) from None
    if fee is not None:
        conn.execute(
            "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)"
            " VALUES (%s, %s, %s, 'fee', %s, 'Lease-break fee')",
            (session.pmc_id, lease_id, body.ends_on, Decimal(fee)),
        )


# --- a tenant leaving a lease others stay on ------------------------------------------------


class Departure(BaseModel):
    tenant_id: UUID
    left_on: date
    # A tenant staying on the lease who takes over the leaving tenant's deposit; None keeps it
    # under the leaving tenant's name until the lease's deposit is returned at move-out.
    deposit_to: UUID | None = None


class Departed(BaseModel):
    # What of the leaving tenant's deposit moved to deposit_to.
    moved: str


@router.post("/{lease_id}/departures", status_code=201)
def depart(session: Staff, conn: Conn, lease_id: UUID, body: Departure) -> Departed:
    """Takes one tenant off a lease others stay on, from a day (trust_remove_lease_tenant). Their
    deposit moves to a tenant who stays, or stays under their name until move-out; it stays in
    trust either way. Sending the same again moves nothing more."""
    if returned(conn, session.pmc_id, lease_id):
        raise HTTPException(409, "the deposit was returned; the lease's tenants stay as they were")
    try:
        row = conn.execute(
            "SELECT trust_remove_lease_tenant(%s, %s, %s, %s, %s)",
            (session.pmc_id, lease_id, body.tenant_id, body.left_on, body.deposit_to),
        ).fetchone()
    except psycopg.errors.InvalidParameterValue:
        raise HTTPException(
            422, "name a tenant on this lease, and for their deposit one who is staying"
        ) from None
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        raise HTTPException(409, refusal(refused)) from None
    assert row is not None
    return Departed(moved=cents(row[0]))


class Deduction(BaseModel):
    amount: Amount
    due_on: date
    memo: Memo


class Created(BaseModel):
    id: UUID


@router.post("/{lease_id}/deductions", status_code=201)
def deduct(session: Staff, conn: Conn, lease_id: UUID, new: Deduction) -> Created:
    """A fee the tenants owe at move-out (cleaning, damage): the deposit pays it when it is
    returned."""
    if returned(conn, session.pmc_id, lease_id):
        raise HTTPException(409, "the deposit was returned already")
    row = conn.execute(
        "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)"
        " VALUES (%s, %s, %s, 'fee', %s, %s) RETURNING id",
        (session.pmc_id, lease_id, new.due_on, new.amount, new.memo),
    ).fetchone()
    assert row is not None
    return Created(id=row[0])


# --- returning the deposit ---------------------------------------------------------------------


class Return(BaseModel):
    # Made by the page once per return, so sending it again returns nothing twice.
    request_id: UUID
    on: date
    # The owner's account kept money goes to, when the property has more than one.
    owner_account: str | None = None


class Returned(BaseModel):
    returned: str


@router.post("/{lease_id}/return", status_code=201)
def return_deposit(session: Staff, conn: Conn, lease_id: UUID, body: Return) -> Returned:
    """Keeps what the lease owes from its tenants' deposits and returns the rest, recorded on
    the day the bank paid it."""
    returned(conn, session.pmc_id, lease_id)
    if body.on > datetime.now(UTC).date():
        raise HTTPException(422, "money is recorded on the day it moved, not a later one")
    try:
        back = conn.execute(
            "SELECT trust_return_deposit(%s, %s, %s, %s, %s)",
            (
                session.pmc_id,
                lease_id,
                body.owner_account,
                f"deposit-return/{body.request_id}",
                datetime.combine(body.on, time(0), UTC),
            ),
        ).fetchone()
    except psycopg.errors.InvalidParameterValue as bad:  # not the property's owner account
        raise HTTPException(422, refusal(bad)) from None
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        raise HTTPException(409, refusal(refused)) from None
    assert back is not None
    return Returned(returned=cents(back[0]))
