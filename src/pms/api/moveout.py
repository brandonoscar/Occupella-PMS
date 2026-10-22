"""Staff's move-outs (migration 0035): end a lease, charge what the tenants owe for cleaning or
damage, and return their security deposits, keeping what the lease owes.

The return goes through trust_return_deposit, once per lease: what the lease owes is kept for
the owner's account and matched to its charges oldest first, the rest goes back to each tenant.
It pays out money, so it takes the disburse role (the database refuses anyone else: 403). The
itemized statement staff send the tenants is trust_report_deposit_return, downloaded like any
report. The PMS records what the bank did; it sends no money.

Since migration 0120 (the founder's review q040 and q039; docs/law.md) each lease shows the day
its deposit is due back by the strictest deadline found, staff keep receipts for the fees it
keeps, and each tenant's statement is a letter to their forwarding address carrying the receipts
and the move-out photos (src/pms/deposit_statement.py), downloaded or emailed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID, uuid5

import psycopg
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field, StringConstraints

from pms import deposit_statement, exports, notice_letters, notices
from pms.api import company, directory, documents, rent
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
    # The home of a lease the tenants moved from ("Birch Flats, Unit A"); None for the lease's own.
    home: str | None = None


class OwnerAccount(BaseModel):
    id: str
    owner: str
    # Their percent in the property's ownership shares in force today (migration 0094): kept
    # money goes to the owners by their shares, and staff choose no account.
    percent: str | None = None


class ReceiptFile(BaseModel):
    id: UUID  # the lease's document
    title: str


class Fee(BaseModel):
    """A fee charged at move-out (due on or after the lease's last day), or any fee of the lease
    with a receipt on file: what the deposits may keep, and its receipts."""

    id: UUID
    due_on: date
    memo: str | None
    amount: str
    receipts: list[ReceiptFile]


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
    # Each tenant's id, in the order of their names: their statement is theirs.
    tenant_ids: list[UUID]
    starts_on: date
    ends_on: date | None
    # What the lease owes now, and the deposits held for its tenants now.
    owed: str
    # What the leases its tenants moved from still owe (migration 0091): the deposits pay it
    # first, each lease's for its own owner.
    carried: str
    held: str
    open_charges: list[Charge]
    # The owner's accounts kept money may go to: staff choose when there are several and no
    # ownership shares split it.
    owner_accounts: list[OwnerAccount]
    settled: Settled | None
    # The day the deposit and its statement are due back (return_by); none before the lease has
    # a last day, or once it was returned.
    return_by: date | None = None
    fees: list[Fee] = []


# The strictest deadlines found (docs/law.md): a deposit and its itemized statement within 14
# days of the tenancy's end (Hawaii, New York), within 10 while nothing is to be kept (Montana).
RETURN_WITHIN = timedelta(days=14)
NOTHING_KEPT_WITHIN = timedelta(days=10)


def return_by(ends_on: date | None, owes: Decimal) -> date | None:
    """The day a lease's deposit is due back by the strictest deadline found, counted from its
    last day: none until it has one."""
    if ends_on is None:
        return None
    return ends_on + (RETURN_WITHIN if owes > 0 else NOTHING_KEPT_WITHIN)


# Each lease of the PMC past its last day on the day whose tenants' deposits are still held, not
# a lease its tenants moved from: what it and the leases they moved from owe, and what is held.
DUE_BACK = """
SELECT l.id, p.display_name, u.display_name, l.ends_on,
       array(SELECT t.display_name FROM trust_lease_tenants AS lt
             JOIN trust_tenants AS t ON t.id = lt.tenant_id WHERE lt.lease_id = l.id
             ORDER BY t.display_name COLLATE "C", t.id),
       trust_lease_owed(l.id) + coalesce((SELECT sum(trust_lease_owed(m.lease_id))
                                          FROM trust_lease_moved_from(l.id) AS m), 0),
       h.held
FROM trust_leases AS l
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
CROSS JOIN LATERAL (
    SELECT coalesce(sum(b.balance), 0) AS held
    FROM trust_lease_tenants AS lt
    JOIN trust_ledger_accounts AS a ON a.tenant_id = lt.tenant_id AND a.kind = 'tenant_deposit'
    JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
    WHERE lt.lease_id = l.id
) AS h
WHERE l.pmc_id = %(pmc)s AND l.ends_on < %(day)s AND h.held > 0
  AND NOT EXISTS (SELECT 1 FROM trust_deposit_returns AS r WHERE r.lease_id = l.id)
  AND NOT EXISTS (SELECT 1 FROM trust_lease_transfers AS t WHERE t.from_lease_id = l.id)
"""


class DueBack(BaseModel):
    lease_id: UUID
    property: str
    unit: str
    tenants: list[str]
    held: str
    return_by: date
    # "due in 3 days", "due today" or "4 days late".
    standing: str


def standing_of(due: date, day: date) -> str:
    left = (due - day).days
    if left == 0:
        return "due today"
    plural = "" if abs(left) == 1 else "s"
    return f"due in {left} day{plural}" if left > 0 else f"{-left} day{plural} late"


def due_back(conn: psycopg.Connection[Any], pmc_id: UUID, day: date) -> list[DueBack]:
    """Each lease whose last day has passed and whose deposits are still held, soonest due
    first, with the day they are due back by the strictest deadline found."""
    found = []
    for lease, prop, unit, ends, tenants, owes, held in conn.execute(
        DUE_BACK, {"pmc": pmc_id, "day": day}
    ).fetchall():
        due = return_by(ends, owes)
        assert due is not None
        found.append(
            DueBack(
                lease_id=lease,
                property=prop,
                unit=unit,
                tenants=tenants,
                held=cents(held),
                return_by=due,
                standing=standing_of(due, day),
            )
        )
    return sorted(found, key=lambda d: (d.return_by, d.property, d.unit, str(d.lease_id)))


# A lease's fees charged at move-out, or with a receipt on file, each with its receipts not
# removed, oldest first.
FEES = """
SELECT c.id, c.due_on, c.memo, c.amount,
       coalesce(json_agg(json_build_object('id', d.id, 'title', d.title)
                         ORDER BY r.created_at, d.id) FILTER (WHERE d.id IS NOT NULL), '[]')
FROM trust_charges AS c
LEFT JOIN trust_deduction_receipts AS r ON r.charge_id = c.id
LEFT JOIN trust_documents AS d ON d.id = r.document_id
     AND NOT EXISTS (SELECT 1 FROM trust_document_removals AS x WHERE x.document_id = d.id)
WHERE c.lease_id = %(lease)s AND c.kind = 'fee' AND c.recurring_charge_id IS NULL
  AND (c.due_on >= %(ends)s OR r.charge_id IS NOT NULL)
GROUP BY c.id
ORDER BY c.due_on, c.created_at, c.id
"""


def fees_of(conn: psycopg.Connection[Any], lease_id: UUID, ends_on: date | None) -> list[Fee]:
    return [
        Fee(
            id=i,
            due_on=due_on,
            memo=memo,
            amount=cents(amount),
            receipts=[ReceiptFile(id=UUID(r["id"]), title=r["title"]) for r in receipts],
        )
        for i, due_on, memo, amount, receipts in conn.execute(
            FEES, {"lease": lease_id, "ends": ends_on}
        ).fetchall()
    ]


LEASES = """
SELECT l.id, p.display_name, u.display_name, l.starts_on, l.ends_on, trust_lease_owed(l.id),
       array(SELECT t.display_name FROM trust_lease_tenants AS lt
             JOIN trust_tenants AS t ON t.id = lt.tenant_id WHERE lt.lease_id = l.id
             ORDER BY t.display_name COLLATE "C", t.id),
       array(SELECT t.id FROM trust_lease_tenants AS lt
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
  -- A lease its tenants moved from has no return of its own: their deposits went with them.
  AND NOT EXISTS (SELECT 1 FROM trust_lease_transfers AS t WHERE t.from_lease_id = l.id)
ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C", l.starts_on, l.id
"""

# The leases a lease's tenants moved from, oldest first, each with its home.
MOVED_FROM = """
SELECT m.lease_id, p.display_name || ', ' || u.display_name, trust_lease_owed(m.lease_id)
FROM trust_lease_moved_from(%s) WITH ORDINALITY AS m (lease_id, owner_account, n)
JOIN trust_leases AS l ON l.id = m.lease_id
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
ORDER BY m.n
"""

# A lease's open charges in the order a deposit kept pays them (migration 0093): rent first.
OPEN = """
SELECT c.id, c.due_on, c.kind, c.memo, o.open
FROM trust_open_charges(%s, %s, 'infinity') AS o
JOIN trust_charges AS c ON c.id = o.charge_id
ORDER BY trust_pay_rank(c) = 2, c.due_on, trust_pay_rank(c), c.created_at, c.id
"""

OWNERS = """
SELECT a.ledger_account_id, o.display_name,
       (SELECT s.percent FROM trust_ownership_on(a.property_id, (now() AT TIME ZONE 'UTC')::date)
        AS s WHERE s.ledger_account_id = a.ledger_account_id)
FROM trust_ledger_accounts AS a
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
        lease, prop, unit, starts, ends, owed, tenants, tenant_ids, held, prop_id, *done = row
        if held <= 0 and done[0] is None and (ends is None or ends < since):
            continue
        earlier = conn.execute(MOVED_FROM, (lease,)).fetchall()
        carried = sum((owing for _, _, owing in earlier), Decimal(0))
        found.append(
            MoveOut(
                lease_id=lease,
                property=prop,
                unit=unit,
                tenants=tenants,
                tenant_ids=tenant_ids,
                starts_on=starts,
                ends_on=ends,
                owed=cents(owed),
                carried=cents(carried),
                held=cents(held),
                open_charges=[
                    Charge(id=i, due_on=d, kind=k, memo=m, open=cents(o), home=home)
                    for each, home in [*((e, h) for e, h, _ in earlier), (lease, None)]
                    for i, d, k, m, o in conn.execute(OPEN, (session.pmc_id, each)).fetchall()
                    if o > 0
                ],
                owner_accounts=[
                    OwnerAccount(id=i, owner=o, percent=None if p is None else exports.text(p))
                    for i, o, p in conn.execute(OWNERS, (session.pmc_id, prop_id)).fetchall()
                ],
                settled=None
                if done[0] is None
                else Settled(
                    held=cents(done[0]),
                    kept=cents(done[1]),
                    returned=cents(done[2]),
                    returned_on=done[3].astimezone(UTC).date(),
                ),
                return_by=None if done[0] is not None else return_by(ends, owed + carried),
                fees=fees_of(conn, lease, ends),
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


# --- the tenants moving to another unit ------------------------------------------------------


class Transfer(BaseModel):
    # Made by the page once per move, so sending it again moves nothing twice.
    request_id: UUID
    unit_id: UUID
    # The new lease's first day; the old lease's last is the day before.
    moved_on: date
    ends_on: date | None = None
    monthly_rent: Amount
    # The day of the month the new lease's rent is due; the old lease's by default.
    rent_due_day: Annotated[int, Field(ge=1, le=28)] | None = None
    # The old lease's owner's account, when its property has more than one: what the old lease
    # still owes is paid into it.
    owner_account: str | None = None
    # The day the tenants agreed in writing that their deposits stay held for the new lease
    # (none when they hold no deposit).
    deposit_agreed_on: date | None = None


class Transferred(BaseModel):
    lease_id: UUID
    # The tenants' prepaid rent that paid what the old lease still owed, and what of the rest
    # moved to the new lease's owner's trust bank account (money staff move at the bank).
    prepaid_used: str
    prepaid_moved: str


# Each staying tenant's prepaid rent in another trust bank account than the one `bank` names.
PREPAID_ELSEWHERE = """
SELECT a.ledger_account_id, a.tenant_id, b.balance, c.ledger_account_id
FROM trust_lease_tenants AS lt
JOIN trust_ledger_accounts AS a ON a.tenant_id = lt.tenant_id AND a.kind = 'prepaid_rent'
JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
JOIN trust_ledger_accounts AS c ON c.bank_account_id = a.bank_account_id AND c.kind = 'bank_cash'
WHERE lt.lease_id = %s AND a.bank_account_id <> %s AND b.balance > 0
ORDER BY a.ledger_account_id
"""

# What a move's prepaid rent moved: the transfers under its key out of prepaid rent.
PREPAID_MOVED = """
SELECT coalesce(sum(t.amount), 0) FROM pgledger_transfers AS t
JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.from_account_id
WHERE a.pmc_id = %s AND a.kind = 'prepaid_rent' AND t.metadata ->> 'key' = %s
"""


@router.post("/{lease_id}/transfer", status_code=201)
def transfer(session: Staff, conn: Conn, lease_id: UUID, body: Transfer) -> Transferred:
    """Moves the lease's tenants to another unit, maybe at another property
    (trust_transfer_lease): the lease ends the day before and a lease opens at the unit for the
    tenants staying on it. Their deposits stay held for the new lease, as they agreed in writing.
    Their prepaid rent first pays what the old lease still owes; what is left moves with them to
    the new lease's owner's trust bank account. Whatever the old lease still owes after that
    stays owed to its owner, and the new lease's payments pay it first. Sending the same again
    moves nothing twice."""
    rent.lease_of(conn, session.pmc_id, lease_id)  # another PMC's lease is not found
    if body.ends_on is not None and body.ends_on < body.moved_on:
        raise HTTPException(422, "a lease ends on or after the day it starts")
    if body.deposit_agreed_on is not None and body.deposit_agreed_on > rent.today():
        raise HTTPException(422, "the tenants agreed on a day that has come, not a later one")
    try:
        used = rent.use_prepaid(
            conn,
            session.pmc_id,
            lease_id,
            uuid5(body.request_id, "prepaid used"),
            rent.today(),
            body.owner_account,
        ).to_owner
    except HTTPException as nothing:
        if nothing.status_code != 409:
            raise
        used = Decimal(0)  # no prepaid rent, or the old lease owed nothing
    due_day = body.rent_due_day
    if due_day is None:
        row = conn.execute(
            "SELECT trust_rent_due_day(%s, %s)", (lease_id, body.moved_on - timedelta(days=1))
        ).fetchone()
        assert row is not None
        due_day = row[0]
    try:
        row = conn.execute(
            "SELECT trust_transfer_lease(%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                session.pmc_id,
                lease_id,
                body.unit_id,
                body.moved_on,
                body.ends_on,
                body.monthly_rent,
                body.owner_account,
                body.deposit_agreed_on,
            ),
        ).fetchone()
    except psycopg.errors.InvalidParameterValue as bad:  # the unit or the owner's account
        raise HTTPException(422, refusal(bad)) from None
    except (
        psycopg.errors.CheckViolation,
        psycopg.errors.UniqueViolation,
        psycopg.errors.ExclusionViolation,  # the unit has a lease then
    ) as refused:
        raise HTTPException(409, refusal(refused)) from None
    assert row is not None
    new = row[0]
    if due_day != 1:
        conn.execute(
            "INSERT INTO trust_rent_due_days (pmc_id, lease_id, starts_on, due_day)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT (lease_id, starts_on) DO NOTHING",
            (session.pmc_id, new, body.moved_on.replace(day=1), due_day),
        )
    # Prepaid rent in another trust bank account than the new lease's owner's moves there, with
    # its cash, when the new lease's property keeps its owners' money in one.
    key = f"moved-prepaid/{body.request_id}"
    banks = conn.execute(
        "SELECT DISTINCT bank_account_id FROM trust_ledger_accounts"
        " WHERE pmc_id = %s AND kind = 'owner_property' AND property_id ="
        " (SELECT u.property_id FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id"
        "  WHERE l.id = %s)",
        (session.pmc_id, new),
    ).fetchall()
    if len(banks) == 1:
        bank = banks[0][0]
        cash = rent.cash_of(conn, bank)
        transfers = []
        for account, tenant, balance, elsewhere in conn.execute(
            PREPAID_ELSEWHERE, (new, bank)
        ).fetchall():
            here = rent.tenant_account(conn, session.pmc_id, bank, "prepaid_rent", tenant)
            transfers += [(account, here, Decimal(balance)), (cash, elsewhere, Decimal(balance))]
        if transfers:
            rent.post(
                conn,
                session.pmc_id,
                key,
                transfers,
                rent.today(),
                {"key": key, "kind": "prepaid rent moved", "lease_id": str(new)},
            )
    moved = conn.execute(PREPAID_MOVED, (session.pmc_id, key)).fetchone()
    assert moved is not None
    return Transferred(lease_id=new, prepaid_used=cents(used), prepaid_moved=cents(moved[0]))


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


# --- receipts for what is kept, and the statement ---------------------------------------------


class NewReceipt(BaseModel):
    title: documents.Title
    content_type: documents.ContentType
    content_base64: str = Field(min_length=4, max_length=7_000_000)


@router.post("/{lease_id}/fees/{charge_id}/receipts", status_code=201)
def add_receipt(
    session: Staff, conn: Conn, lease_id: UUID, charge_id: UUID, new: NewReceipt
) -> Created:
    """Keeps a receipt or invoice for a fee of the lease (a repair, cleaning), as one of the
    lease's documents linked to the fee; before or after the deposit is returned, since a repair
    may be done later. The statement names it and carries the file."""
    returned(conn, session.pmc_id, lease_id)
    fee = conn.execute(
        "SELECT 1 FROM trust_charges WHERE pmc_id = %s AND id = %s AND lease_id = %s"
        " AND kind = 'fee'",
        (session.pmc_id, charge_id, lease_id),
    ).fetchone()
    if fee is None:
        raise HTTPException(404, "no such fee on this lease")
    document = documents.keep(
        conn,
        session.pmc_id,
        documents.NewDocument(
            kind="lease_document",
            about_id=lease_id,
            title=new.title,
            content_type=new.content_type,
            content_base64=new.content_base64,
        ),
    )
    conn.execute(
        "INSERT INTO trust_deduction_receipts (pmc_id, charge_id, document_id) VALUES (%s, %s, %s)",
        (session.pmc_id, charge_id, document),
    )
    return Created(id=document)


# The fees the deposits paid, each once with what they paid of it, in the statement's order.
KEPT = """
SELECT c.id, c.due_on, c.kind, c.memo, sum(x.amount)
FROM trust_deposit_return_lines AS x
JOIN trust_charges AS c ON c.id = x.charge_id
WHERE x.lease_id = %s
GROUP BY c.id
ORDER BY min(x.line_no)
"""

# Each kept fee's receipt files not removed, oldest first.
RECEIPT_FILES = """
SELECT r.charge_id, d.title, d.content_type, f.content
FROM trust_deduction_receipts AS r
JOIN trust_documents AS d ON d.id = r.document_id
JOIN trust_document_files AS f ON f.document_id = d.id
WHERE r.charge_id = ANY(%s)
  AND NOT EXISTS (SELECT 1 FROM trust_document_removals AS x WHERE x.document_id = d.id)
ORDER BY r.created_at, d.id
"""

PHOTOS = """
SELECT d.title, d.content_type, f.content
FROM trust_documents AS d
JOIN trust_document_files AS f ON f.document_id = d.id
WHERE d.lease_id = %s AND d.kind = 'move_out_photo'
  AND NOT EXISTS (SELECT 1 FROM trust_document_removals AS x WHERE x.document_id = d.id)
ORDER BY d.created_at, d.id
"""


def statement_of(
    conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID, tenant_id: UUID
) -> deposit_statement.Statement:
    """A tenant's deposit statement: 404 when the lease isn't the PMC's or the tenant isn't on
    it, 409 before its deposit was returned."""
    if not returned(conn, pmc_id, lease_id):
        raise HTTPException(409, "the deposit hasn't been returned yet")
    named = conn.execute(
        "SELECT t.display_name FROM trust_lease_tenants AS lt"
        " JOIN trust_tenants AS t ON t.id = lt.tenant_id"
        " WHERE lt.lease_id = %s AND lt.tenant_id = %s",
        (lease_id, tenant_id),
    ).fetchone()
    if named is None:
        raise HTTPException(404, "no such tenant on this lease")
    contact = directory.contact_of(conn, "tenant_id", tenant_id)
    address: tuple[str, ...] = ()
    if contact.address_line_1:
        address = tuple(
            line
            for line in (
                contact.address_line_1,
                contact.address_line_2,
                f"{contact.city}, {contact.state} {contact.postal_code}",
            )
            if line
        )
    kept = conn.execute(KEPT, (lease_id,)).fetchall()
    files: dict[UUID, list[deposit_statement.File]] = {}
    for charge, title, content_type, content in conn.execute(
        RECEIPT_FILES, ([k[0] for k in kept],)
    ).fetchall():
        files.setdefault(charge, []).append(
            deposit_statement.File(title, content_type, bytes(content))
        )
    photos = [
        deposit_statement.File(title, content_type, bytes(content))
        for title, content_type, content in conn.execute(PHOTOS, (lease_id,)).fetchall()
    ]
    return deposit_statement.Statement(
        company=company.name_of(conn, pmc_id),
        street=company.street_of(conn, pmc_id),
        dated_on=rent.today(),
        to_name=named[0],
        to_address=address,
        lines=tuple(
            (item, detail, amount)
            for item, detail, amount in conn.execute(
                "SELECT item, detail, amount FROM trust_report_deposit_return(%s, %s)"
                " ORDER BY line",
                (pmc_id, lease_id),
            ).fetchall()
        ),
        kept=tuple(
            deposit_statement.Kept(
                label=f"{memo or kind} (due {notice_letters.us(due_on)})",
                amount=amount,
                receipts=tuple(f.title for f in files.get(charge, [])),
            )
            for charge, due_on, kind, memo, amount in kept
        ),
        files=(*(f for k in kept for f in files.get(k[0], [])), *photos),
        photos=len(photos),
    )


def statement_pdf(
    conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID, tenant_id: UUID
) -> bytes:
    return deposit_statement.render(
        statement_of(conn, pmc_id, lease_id, tenant_id), datetime.now(UTC)
    )


@router.get("/{lease_id}/statement")
def statement(session: Staff, conn: Conn, lease_id: UUID, tenant_id: UUID) -> Response:
    """A tenant's deposit statement as a PDF letter to their forwarding address, with the
    receipts and move-out photos it names: to print and mail, or keep."""
    return Response(
        statement_pdf(conn, session.pmc_id, lease_id, tenant_id),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="security-deposit-statement.pdf"'},
    )


class Emailed(BaseModel):
    name: str
    to: str


class StatementSent(BaseModel):
    emailed: list[Emailed]
    # Tenants with no email on file: nothing was written to them.
    without_email: list[str]


@router.post("/{lease_id}/statement/send", status_code=201)
def send_statement(session: Staff, conn: Conn, lease_id: UUID) -> StatementSent:
    """Emails each tenant of the lease their deposit statement, its PDF made when it is sent; it
    waits in the outbox while the server sends none. Sending again writes again."""
    if not returned(conn, session.pmc_id, lease_id):
        raise HTTPException(409, "the deposit hasn't been returned yet")
    emailed, without = notices.write_deposit_statements(conn, session.pmc_id, lease_id)
    return StatementSent(
        emailed=[Emailed(name=name, to=to) for name, to in emailed], without_email=without
    )
