"""Staff's bills and owners' money: vendor bills (entered, approved by the owner when over the
agreement's limit, set aside from the owner's account, paid), monthly recurring bills, the
PMC's management and leasing fees, owner draws and owner contributions.

Every step goes through the database's write paths (trust_set_aside_bill, trust_pay_bill,
trust_enter_recurring_bills, trust_post_management_fee, trust_post_leasing_fee,
trust_draw_owner, trust_record_owner_contribution), each once per bill, period, lease or
request key, so a retry does nothing twice. A draw or a bill payment records what the bank
paid out; the PMS sends no money itself.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints

from pms import exports
from pms.api.deps import Conn, Staff

router = APIRouter(prefix="/api/payables")

Amount = Annotated[Decimal, Field(gt=0, le=Decimal("10000000"), decimal_places=2)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Account = Annotated[str, Field(min_length=1, max_length=100)]
CENT = Decimal("0.01")


def cents(value: Decimal) -> str:
    return exports.text(Decimal(value).quantize(CENT))


def today() -> date:
    return datetime.now(UTC).date()


def at(day: date) -> datetime:
    return datetime.combine(day, time(0), UTC)


def reason(refused: psycopg.Error) -> str:
    return str(refused).splitlines()[0].removeprefix("trust: ")


def not_after_today(day: date) -> None:
    if day > today():
        raise HTTPException(422, "money is recorded on the day it moved, not a later one")


def one(conn: psycopg.Connection[Any], query: str, params: tuple[Any, ...]) -> Any:
    row = conn.execute(query, params).fetchone()
    assert row is not None
    return row[0]


def owner_account(conn: psycopg.Connection[Any], pmc_id: UUID, account: str) -> UUID:
    """The trust bank account an owner's property account of the PMC is held in; 404 if none."""
    row = conn.execute(
        "SELECT bank_account_id FROM trust_ledger_accounts WHERE pmc_id = %s"
        " AND ledger_account_id = %s AND kind = 'owner_property'",
        (pmc_id, account),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such owner account")
    return UUID(str(row[0]))


# --- bills --------------------------------------------------------------------------------------

Status = Literal["needs approval", "declined", "to set aside", "set aside", "paid"]


class Bill(BaseModel):
    id: UUID
    vendor: str
    reference: str
    account: str
    owner: str
    property: str
    bill_date: date
    due_on: date
    amount: str
    memo: str
    work_order_id: UUID | None
    approval_limit: str
    approved_by: str | None
    # Who declined it for the owner (the owner in the portal, or as staff recorded it), and why.
    declined_by: str | None = None
    decline_reason: str | None = None
    status: Status


BILLS = """
SELECT b.id, v.display_name, b.reference, b.ledger_account_id, o.display_name, p.display_name,
       b.bill_date, b.due_on, b.amount, b.memo, b.work_order_id,
       coalesce((SELECT g.approval_limit FROM trust_management_agreements AS g
                 WHERE g.ledger_account_id = b.ledger_account_id AND g.starts_on <= b.bill_date
                 ORDER BY g.starts_on DESC LIMIT 1), 0),
       ap.approved_by, dc.declined_by, dc.reason,
       EXISTS (SELECT 1 FROM trust_bill_payments AS s WHERE s.bill_id = b.id
               AND s.step = 'set_aside'),
       EXISTS (SELECT 1 FROM trust_bill_payments AS s WHERE s.bill_id = b.id AND s.step = 'paid')
FROM trust_bills AS b
JOIN trust_vendors AS v ON v.id = b.vendor_id
JOIN trust_ledger_accounts AS a ON a.ledger_account_id = b.ledger_account_id
JOIN trust_owners AS o ON o.id = a.owner_id
JOIN trust_properties AS p ON p.id = a.property_id
LEFT JOIN trust_bill_approvals AS ap ON ap.bill_id = b.id
LEFT JOIN trust_bill_declines AS dc ON dc.bill_id = b.id
WHERE b.pmc_id = %s
ORDER BY b.due_on, b.bill_date, v.display_name COLLATE "C", b.reference COLLATE "C", b.id
"""


def status_of(
    amount: Decimal, limit: Decimal, approved: bool, declined: bool, aside: bool, paid: bool
) -> Status:
    if paid:
        return "paid"
    if aside:
        return "set aside"
    if declined:
        return "declined"
    if amount > limit and not approved:
        return "needs approval"
    return "to set aside"


@router.get("/bills")
def bills(
    session: Staff, conn: Conn, show: Annotated[Literal["open", "all"], Query()] = "open"
) -> list[Bill]:
    """The PMC's vendor bills, by due date: by default those still to pay (not paid, nor
    declined by the owner)."""
    found = []
    for (
        i,
        vendor,
        reference,
        account,
        owner,
        prop,
        billed,
        due,
        amount,
        memo,
        order,
        limit,
        approved_by,
        declined_by,
        decline_reason,
        aside,
        paid,
    ) in conn.execute(BILLS, (session.pmc_id,)).fetchall():
        status = status_of(
            amount, limit, approved_by is not None, declined_by is not None, aside, paid
        )
        if show == "open" and status in ("paid", "declined"):
            continue
        found.append(
            Bill(
                id=i,
                vendor=vendor,
                reference=reference,
                account=account,
                owner=owner,
                property=prop,
                bill_date=billed,
                due_on=due,
                amount=cents(amount),
                memo=memo,
                work_order_id=order,
                approval_limit=cents(limit),
                approved_by=approved_by,
                declined_by=declined_by,
                decline_reason=decline_reason,
                status=status,
            )
        )
    return found


class NewBill(BaseModel):
    vendor_id: UUID
    account: Account
    reference: Text
    bill_date: date
    due_on: date
    amount: Amount
    memo: Text
    work_order_id: UUID | None = None


class Created(BaseModel):
    id: UUID


def vendor_of(conn: psycopg.Connection[Any], pmc_id: UUID, vendor: UUID) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM trust_vendors WHERE pmc_id = %s AND id = %s", (pmc_id, vendor)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such vendor")


@router.post("/bills", status_code=201)
def enter_bill(session: Staff, conn: Conn, new: NewBill) -> Created:
    """A vendor's bill against an owner's property account, once per vendor reference."""
    vendor_of(conn, session.pmc_id, new.vendor_id)
    owner_account(conn, session.pmc_id, new.account)
    if new.due_on < new.bill_date:
        raise HTTPException(422, "a bill is due on or after its date")
    try:
        bill = one(
            conn,
            "INSERT INTO trust_bills (pmc_id, vendor_id, ledger_account_id, reference, bill_date,"
            " due_on, amount, memo, work_order_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " RETURNING id",
            (
                session.pmc_id,
                new.vendor_id,
                new.account,
                new.reference,
                new.bill_date,
                new.due_on,
                new.amount,
                new.memo,
                new.work_order_id,
            ),
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "this vendor has a bill with that reference already") from None
    except (psycopg.errors.CheckViolation, psycopg.errors.ForeignKeyViolation) as refused:
        # a work order of another property, a cancelled one, or none of this PMC's
        raise HTTPException(409, reason(refused)) from None
    return Created(id=bill)


def bill_of(conn: psycopg.Connection[Any], pmc_id: UUID, bill: UUID) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM trust_bills WHERE pmc_id = %s AND id = %s", (pmc_id, bill)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such bill")


class Approval(BaseModel):
    approved_on: date
    # Who approved it for the owner, as staff record it (the owner, or someone they named).
    approved_by: Text


@router.post("/bills/{bill_id}/approval", status_code=201)
def approve(session: Staff, conn: Conn, bill_id: UUID, approval: Approval) -> None:
    """Records the owner's approval of a bill over the agreement's approval limit."""
    bill_of(conn, session.pmc_id, bill_id)
    try:
        with conn.transaction():  # a savepoint: a refusal leaves the request's transaction usable
            conn.execute(
                "INSERT INTO trust_bill_approvals (pmc_id, bill_id, approved_on, approved_by)"
                " VALUES (%s, %s, %s, %s)",
                (session.pmc_id, bill_id, approval.approved_on, approval.approved_by),
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, decided(conn, bill_id)) from None


def decided(conn: psycopg.Connection[Any], bill_id: UUID) -> str:
    """Why a second decision on a bill is refused."""
    approved = conn.execute(
        "SELECT 1 FROM trust_bill_approvals WHERE bill_id = %s", (bill_id,)
    ).fetchone()
    return "this bill is approved already" if approved else "the owner declined this bill"


class Decline(BaseModel):
    declined_on: date
    # Who declined it for the owner, as staff record it (the owner, or someone they named).
    declined_by: Text
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


@router.post("/bills/{bill_id}/decline", status_code=201)
def decline(session: Staff, conn: Conn, bill_id: UUID, refusal: Decline) -> None:
    """Records the owner's decline of a bill: it is never set aside or paid, and holds nothing
    back from the owner's draws from that day."""
    bill_of(conn, session.pmc_id, bill_id)
    try:
        with conn.transaction():  # a savepoint, as above
            conn.execute(
                "INSERT INTO trust_bill_declines"
                " (pmc_id, bill_id, declined_on, declined_by, reason)"
                " VALUES (%s, %s, %s, %s, %s)",
                (
                    session.pmc_id,
                    bill_id,
                    refusal.declined_on,
                    refusal.declined_by,
                    refusal.reason,
                ),
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, decided(conn, bill_id)) from None
    except psycopg.errors.CheckViolation as refused:  # set aside already
        raise HTTPException(409, reason(refused)) from None


class Day(BaseModel):
    on: date


class Moved(BaseModel):
    transfer_id: str


def vendor_account(conn: psycopg.Connection[Any], pmc_id: UUID, bill: UUID) -> None:
    """Opens the bill's vendor's account in the owner account's trust bank account, if new."""
    row = conn.execute(
        "SELECT b.vendor_id, a.bank_account_id FROM trust_bills AS b"
        " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = b.ledger_account_id"
        " WHERE b.id = %s",
        (bill,),
    ).fetchone()
    assert row is not None
    vendor, bank = row
    if not conn.execute(
        "SELECT 1 FROM trust_ledger_accounts WHERE bank_account_id = %s AND kind = 'vendor_payable'"
        " AND vendor_id = %s",
        (bank, vendor),
    ).fetchone():
        conn.execute("SELECT trust_open_vendor_account(%s, %s, %s)", (pmc_id, bank, vendor))


@router.post("/bills/{bill_id}/set-aside", status_code=201)
def set_aside(session: Staff, conn: Conn, bill_id: UUID, step: Day) -> Moved:
    """Moves the bill's amount from the owner's account to the vendor's, once."""
    not_after_today(step.on)
    bill_of(conn, session.pmc_id, bill_id)
    vendor_account(conn, session.pmc_id, bill_id)
    try:
        moved = one(
            conn, "SELECT trust_set_aside_bill(%s, %s, %s)", (session.pmc_id, bill_id, at(step.on))
        )
    except psycopg.errors.CheckViolation as refused:  # no approval, not enough money, closed
        raise HTTPException(409, reason(refused)) from None
    return Moved(transfer_id=moved)


@router.post("/bills/{bill_id}/pay", status_code=201)
def pay(session: Staff, conn: Conn, bill_id: UUID, step: Day) -> Moved:
    """Records that the trust bank account paid the vendor the bill, once."""
    not_after_today(step.on)
    bill_of(conn, session.pmc_id, bill_id)
    try:
        moved = one(
            conn, "SELECT trust_pay_bill(%s, %s, %s)", (session.pmc_id, bill_id, at(step.on))
        )
    except psycopg.errors.InvalidParameterValue as refused:  # not set aside, or before that
        raise HTTPException(409, reason(refused)) from None
    except psycopg.errors.CheckViolation as refused:  # a closed period
        raise HTTPException(409, reason(refused)) from None
    return Moved(transfer_id=moved)


# --- recurring bills ------------------------------------------------------------------------


class Recurring(BaseModel):
    id: UUID
    vendor: str
    account: str
    owner: str
    property: str
    reference_prefix: str
    amount: str
    memo: str
    day_of_month: int
    starts_on: date
    ends_on: date | None


@router.get("/recurring")
def recurring(session: Staff, conn: Conn) -> list[Recurring]:
    return [
        Recurring(
            id=i,
            vendor=vendor,
            account=account,
            owner=owner,
            property=prop,
            reference_prefix=prefix,
            amount=cents(amount),
            memo=memo,
            day_of_month=day_of_month,
            starts_on=starts,
            ends_on=ends,
        )
        for i, vendor, account, owner, prop, prefix, amount, memo, day_of_month, starts, ends in (
            conn.execute(
                "SELECT r.id, v.display_name, r.ledger_account_id, o.display_name,"
                " p.display_name, r.reference_prefix, r.amount, r.memo, r.day_of_month,"
                " r.starts_on, e.ends_on FROM trust_recurring_bills AS r"
                " JOIN trust_vendors AS v ON v.id = r.vendor_id"
                " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = r.ledger_account_id"
                " JOIN trust_owners AS o ON o.id = a.owner_id"
                " JOIN trust_properties AS p ON p.id = a.property_id"
                " LEFT JOIN trust_recurring_bill_ends AS e ON e.recurring_bill_id = r.id"
                ' WHERE r.pmc_id = %s ORDER BY v.display_name COLLATE "C",'
                ' r.reference_prefix COLLATE "C", r.id',
                (session.pmc_id,),
            ).fetchall()
        )
    ]


class NewRecurring(BaseModel):
    vendor_id: UUID
    account: Account
    reference_prefix: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    amount: Amount
    memo: Text
    day_of_month: Annotated[int, Field(ge=1, le=28)]
    starts_on: date


@router.post("/recurring", status_code=201)
def add_recurring(session: Staff, conn: Conn, new: NewRecurring) -> Created:
    """A bill that comes every month on a day (1 to 28), from a first day."""
    vendor_of(conn, session.pmc_id, new.vendor_id)
    owner_account(conn, session.pmc_id, new.account)
    try:
        return Created(
            id=one(
                conn,
                "INSERT INTO trust_recurring_bills (pmc_id, vendor_id, ledger_account_id,"
                " reference_prefix, amount, memo, day_of_month, starts_on)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    session.pmc_id,
                    new.vendor_id,
                    new.account,
                    new.reference_prefix,
                    new.amount,
                    new.memo,
                    new.day_of_month,
                    new.starts_on,
                ),
            )
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            409, "this vendor has a recurring bill with that reference already"
        ) from None


class End(BaseModel):
    ends_on: date


@router.post("/recurring/{recurring_id}/end", status_code=201)
def end_recurring(session: Staff, conn: Conn, recurring_id: UUID, end: End) -> None:
    """Stops a recurring bill: no month after the day is entered."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_recurring_bills WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, recurring_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such recurring bill")
    try:
        conn.execute(
            "INSERT INTO trust_recurring_bill_ends (pmc_id, recurring_bill_id, ends_on)"
            " VALUES (%s, %s, %s)",
            (session.pmc_id, recurring_id, end.ends_on),
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "this recurring bill has ended already") from None


class Counted(BaseModel):
    entered: int


@router.post("/recurring/enter")
def enter_recurring(session: Staff, conn: Conn, through: Day) -> Counted:
    """Enters each recurring bill's months up to the day, each month once."""
    return Counted(
        entered=one(
            conn, "SELECT trust_enter_recurring_bills(%s, %s)", (session.pmc_id, through.on)
        )
    )


# --- owners' money ----------------------------------------------------------------------------


class OwnerMoney(BaseModel):
    account: str
    owner: str
    property: str
    bank_account: str
    balance: str
    reserve: str
    unpaid_bills: str
    # What a draw may take today: the balance less the reserve and unpaid bills, at least 0.
    available: str
    has_agreement: bool


OWNERS = """
SELECT a.ledger_account_id, o.display_name, p.display_name, b.display_name, l.balance,
       coalesce(g.reserve, 0),
       -- Bills dated by today and not set aside by then (trust_unpaid_bills, which the app
       -- may not call, written out).
       coalesce((SELECT sum(x.amount) FROM trust_bills AS x
                 WHERE x.ledger_account_id = a.ledger_account_id AND x.bill_date <= %(today)s
                   AND NOT EXISTS (
                       SELECT 1 FROM trust_bill_payments AS s
                       JOIN pgledger_transfers AS t ON t.id = s.transfer_id
                       WHERE s.bill_id = x.id AND s.step = 'set_aside'
                         AND t.event_at < (%(today)s + 1)::timestamp AT TIME ZONE 'UTC')
                   AND NOT EXISTS (
                       SELECT 1 FROM trust_bill_declines AS d
                       WHERE d.bill_id = x.id AND d.declined_on <= %(today)s)), 0),
       g.id IS NOT NULL
FROM trust_ledger_accounts AS a
JOIN trust_owners AS o ON o.id = a.owner_id
JOIN trust_properties AS p ON p.id = a.property_id
JOIN trust_bank_accounts AS b ON b.id = a.bank_account_id
JOIN pgledger_accounts AS l ON l.id = a.ledger_account_id
LEFT JOIN LATERAL (
    SELECT g.id, g.reserve FROM trust_management_agreements AS g
    WHERE g.ledger_account_id = a.ledger_account_id AND g.starts_on <= %(today)s
    ORDER BY g.starts_on DESC LIMIT 1
) AS g ON true
WHERE a.pmc_id = %(pmc)s AND a.kind = 'owner_property'
ORDER BY o.display_name COLLATE "C", p.display_name COLLATE "C", a.ledger_account_id
"""


@router.get("/owners")
def owners(session: Staff, conn: Conn) -> list[OwnerMoney]:
    """Each owner's property account today: what it holds, keeps back and may pay out."""
    return [
        OwnerMoney(
            account=account,
            owner=owner,
            property=prop,
            bank_account=bank,
            balance=cents(held),
            reserve=cents(reserve),
            unpaid_bills=cents(bills_),
            available=cents(max(held - reserve - bills_, Decimal(0))),
            has_agreement=agreed,
        )
        for account, owner, prop, bank, held, reserve, bills_, agreed in conn.execute(
            OWNERS, {"pmc": session.pmc_id, "today": today()}
        ).fetchall()
    ]


class FeeRun(BaseModel):
    period_start: date
    period_end: date
    on: date
    # One owner account, or every one with an agreement in force at the period's end.
    account: Account | None = None


class Fee(BaseModel):
    account: str
    owner: str
    property: str
    fee: str | None
    refused: str | None


@router.post("/management-fees")
def management_fees(session: Staff, conn: Conn, run: FeeRun) -> list[Fee]:
    """Takes the management fee of a period from each owner's account (or one), once per
    period: its percent of the rent and recurring fees (pet rent, parking) collected in it, at
    least the minimum, plus a flat fee. An account the fee can't be taken from says why, and the
    others are taken."""
    not_after_today(run.on)
    if run.period_end <= run.period_start:
        raise HTTPException(422, "a fee period ends after it starts")
    if run.account is not None:
        owner_account(conn, session.pmc_id, run.account)
    rows = conn.execute(
        "SELECT a.ledger_account_id, o.display_name, p.display_name"
        " FROM trust_ledger_accounts AS a JOIN trust_owners AS o ON o.id = a.owner_id"
        " JOIN trust_properties AS p ON p.id = a.property_id"
        " WHERE a.pmc_id = %s AND a.kind = 'owner_property'"
        "   AND (%s::text IS NULL OR a.ledger_account_id = %s::text)"
        "   AND EXISTS (SELECT 1 FROM trust_management_agreements AS g"
        "     WHERE g.ledger_account_id = a.ledger_account_id AND g.starts_on <= %s)"
        ' ORDER BY o.display_name COLLATE "C", p.display_name COLLATE "C", a.ledger_account_id',
        (session.pmc_id, run.account, run.account, run.period_end),
    ).fetchall()
    taken = []
    for account, owner, prop in rows:
        try:
            with conn.transaction():  # a savepoint: one refusal leaves the others standing
                fee = one(
                    conn,
                    "SELECT trust_post_management_fee(%s, %s, %s, %s, %s)",
                    (session.pmc_id, account, run.period_start, run.period_end, at(run.on)),
                )
            taken.append(
                Fee(account=account, owner=owner, property=prop, fee=cents(fee), refused=None)
            )
        except (
            psycopg.errors.CheckViolation,
            psycopg.errors.ExclusionViolation,
            psycopg.errors.InvalidParameterValue,
        ) as refused:
            taken.append(
                Fee(account=account, owner=owner, property=prop, fee=None, refused=reason(refused))
            )
    return taken


class LeasingFee(BaseModel):
    lease_id: UUID
    account: Account
    on: date


class Taken(BaseModel):
    amount: str


@router.post("/leasing-fees", status_code=201)
def leasing_fee(session: Staff, conn: Conn, new: LeasingFee) -> Taken:
    """Takes the leasing fee for a lease from an owner's account of its property, once."""
    not_after_today(new.on)
    owner_account(conn, session.pmc_id, new.account)
    try:
        fee = one(
            conn,
            "SELECT trust_post_leasing_fee(%s, %s, %s, %s)",
            (session.pmc_id, new.lease_id, new.account, at(new.on)),
        )
    except psycopg.errors.InvalidParameterValue as refused:
        raise HTTPException(422, reason(refused)) from None
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        raise HTTPException(409, reason(refused)) from None
    return Taken(amount=cents(fee))


class Draw(BaseModel):
    # Made by the page once per draw, so sending it again pays nothing twice.
    request_id: UUID
    account: Account
    # Leave out to draw everything available.
    amount: Amount | None = None
    on: date


@router.post("/draws", status_code=201)
def draw(session: Staff, conn: Conn, new: Draw) -> Taken:
    """Records a payout to an owner from their property account: at most the balance less the
    reserve and unpaid bills."""
    not_after_today(new.on)
    owner_account(conn, session.pmc_id, new.account)
    try:
        drawn = one(
            conn,
            "SELECT trust_draw_owner(%s, %s, %s, %s, %s)",
            (session.pmc_id, new.account, f"draw/{new.request_id}", new.amount, at(new.on)),
        )
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        raise HTTPException(409, reason(refused)) from None
    return Taken(amount=cents(drawn))


class Contribution(BaseModel):
    request_id: UUID
    account: Account
    amount: Amount
    on: date
    memo: Text | None = None


@router.post("/contributions", status_code=201)
def contribute(session: Staff, conn: Conn, new: Contribution) -> Moved:
    """Records money an owner put into their property account (to cover a bill, say)."""
    not_after_today(new.on)
    owner_account(conn, session.pmc_id, new.account)
    try:
        moved = one(
            conn,
            "SELECT trust_record_owner_contribution(%s, %s, %s, %s, %s, %s)",
            (
                session.pmc_id,
                new.account,
                f"contribution/{new.request_id}",
                new.amount,
                at(new.on),
                new.memo,
            ),
        )
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        raise HTTPException(409, reason(refused)) from None
    return Moved(transfer_id=moved)
