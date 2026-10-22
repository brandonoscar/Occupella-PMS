"""Interest on a lease's security deposits (migration 0122; the founder's review q041): what it
comes to from the day after the last payment through a day, deposit by deposit, and paying it, as
a credit against the lease's rent or added to its deposits from the owner's account.

The rate is the property's terms (Setup, on the property's page): the PMS builds in no state's
rate or rule, and staff pay it when the law the company follows says to (docs/law.md). Paying it
goes through trust_pay_deposit_interest only, once per request id; adding it to a deposit takes
the disburse role, which the database checks.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pms import exports
from pms.api.deps import Conn, Staff, refusal
from pms.api.rent import cents, lease_of, today

router = APIRouter(prefix="/api/rent")

PaidAs = Literal["rent_credit", "deposit"]


class Deposit(BaseModel):
    tenant: str  # with "(pet deposit)" or "(key deposit)" after a deposit of that kind
    held: str
    interest: str


class Payment(BaseModel):
    from_on: date
    through_on: date
    amount: str
    paid_as: PaidAs
    paid_on: date


class OwnerAccount(BaseModel):
    id: str
    owner: str


class DepositInterest(BaseModel):
    # The property's yearly rate today, as a percent ("0" without terms), and since when.
    annual_percent: str
    rate_from: date | None
    # The next payment is for the days from this one through `through`.
    from_on: date
    through: date
    deposits: list[Deposit]
    due: str
    # A lease in effect today may take it as a rent credit; a deposit gone back takes none.
    in_effect: bool
    returned: bool
    paid: list[Payment]
    # The property's owner accounts: one is named when it has more than one and no shares.
    owner_accounts: list[OwnerAccount]


@router.get("/leases/{lease_id}/deposit-interest")
def deposit_interest(
    session: Staff, conn: Conn, lease_id: UUID, through: date | None = None
) -> DepositInterest:
    """What the lease's deposits have earned since its last interest payment, through a day
    (today unless one is named), and every payment made."""
    lease = lease_of(conn, session.pmc_id, lease_id)
    day = today()
    through = through or day
    row = conn.execute(
        "SELECT trust_deposit_interest_from(l.id), l.starts_on <= %s"
        " AND (l.ends_on IS NULL OR l.ends_on >= %s),"
        " EXISTS (SELECT 1 FROM trust_deposit_returns AS r WHERE r.lease_id = l.id),"
        " (SELECT t.starts_on FROM trust_deposit_interest_terms AS t"
        "  WHERE t.property_id = %s AND t.starts_on <= %s ORDER BY t.starts_on DESC LIMIT 1),"
        " trust_deposit_interest_rate(%s, %s)"
        " FROM trust_leases AS l WHERE l.id = %s",
        (day, day, lease.property_id, day, lease.property_id, day, lease_id),
    ).fetchone()
    assert row is not None
    start, in_effect, returned, rate_from, rate = row
    deposits = [
        Deposit(tenant=tenant, held=cents(held), interest=cents(interest))
        for tenant, held, interest in conn.execute(
            "SELECT tenant, held, interest FROM trust_deposit_interest_due(%s, %s, %s)",
            (lease_id, start, through),
        ).fetchall()
    ]
    paid = [
        Payment(
            from_on=f,
            through_on=t,
            amount=cents(amount),
            paid_as=how,
            paid_on=at.astimezone(UTC).date(),
        )
        for f, t, amount, how, at in conn.execute(
            "SELECT from_on, through_on, amount, paid_as, paid_at"
            " FROM trust_deposit_interest_payments WHERE lease_id = %s ORDER BY from_on",
            (lease_id,),
        ).fetchall()
    ]
    owners = [
        OwnerAccount(id=account, owner=name)
        for account, name in conn.execute(
            "SELECT a.ledger_account_id, o.display_name FROM trust_ledger_accounts AS a"
            " JOIN trust_owners AS o ON o.id = a.owner_id"
            " WHERE a.pmc_id = %s AND a.kind = 'owner_property' AND a.property_id = %s"
            ' ORDER BY o.display_name COLLATE "C", a.ledger_account_id',
            (session.pmc_id, lease.property_id),
        ).fetchall()
    ]
    return DepositInterest(
        annual_percent=exports.text(rate.normalize()),
        rate_from=rate_from,
        from_on=start,
        through=through,
        deposits=deposits,
        due=cents(sum((Decimal(d.interest) for d in deposits), Decimal(0))),
        in_effect=bool(in_effect),
        returned=bool(returned),
        paid=paid,
        owner_accounts=owners,
    )


class Pay(BaseModel):
    # Made by the page once per payment, so sending it again pays nothing twice.
    request_id: UUID
    through: date
    paid_as: PaidAs
    on: date  # the day it is credited or added: today or a day before
    owner_account: str | None = None


class Paid(BaseModel):
    from_on: date
    through_on: date
    amount: str
    paid_as: PaidAs


# The database's refusals, in words without its ids: each pattern's days, MM/DD/YYYY, go into
# its words in order.
DAY = r"(\d{4}-\d{2}-\d{2})"
WORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(rf"no interest is due on lease \S+ deposits from {DAY} through {DAY}"),
        "no interest is due on the deposits from {} through {}",
    ),
    (
        re.compile(r"the deposit of lease \S+ was returned already"),
        "the deposit went back already; its interest is paid before it does",
    ),
    (
        re.compile(rf"lease \S+ is not in effect on {DAY}"),
        "the lease is not in effect on {}: add its interest to the deposit",
    ),
    (
        re.compile(rf"lease \S+ deposit interest is paid through {DAY}; pay from {DAY} on"),
        "its interest is paid through {}: pay from {} on",
    ),
    (
        re.compile(r"lease \S+ tenants moved to lease"),
        "its tenants moved to another lease, and their deposits with them",
    ),
    (
        re.compile(r"request key \S+ paid other deposit interest"),
        "this request paid other interest already: start a new one",
    ),
)


def us(day: str) -> str:
    return f"{day[5:7]}/{day[8:10]}/{day[:4]}"


def words(error: psycopg.Error) -> str:
    text = refusal(error)
    for pattern, said in WORDS:
        found = pattern.search(text)
        if found is not None:
            return said.format(*(us(day) for day in found.groups()))
    return text


@router.post("/leases/{lease_id}/deposit-interest", status_code=201)
def pay_deposit_interest(session: Staff, conn: Conn, lease_id: UUID, pay: Pay) -> Paid:
    """Pays the lease's deposit interest from the day after its last payment through a day gone
    by: credited against its rent, or added to its deposits from the owner's account (whose cash
    moves with it, from the owner's trust bank account to the deposits')."""
    lease_of(conn, session.pmc_id, lease_id)
    if pay.on > today():
        raise HTTPException(422, "interest is paid on the day it is credited or added, not later")
    if pay.through > pay.on:
        raise HTTPException(422, "interest is paid for days gone by: through the day it is paid")
    try:
        amount = conn.execute(
            "SELECT trust_pay_deposit_interest(%s, %s, %s, %s, %s, %s, %s)",
            (
                session.pmc_id,
                lease_id,
                pay.through,
                pay.paid_as,
                pay.owner_account,
                f"deposit-interest/{pay.request_id}",
                datetime.combine(pay.on, time(0), UTC),
            ),
        ).fetchone()
    except psycopg.errors.InvalidParameterValue:  # not the one owner's account of the property
        raise HTTPException(
            422, "name the owner's account it comes from: the property has more than one"
        ) from None
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        raise HTTPException(409, words(refused)) from None
    assert amount is not None
    row = conn.execute(
        "SELECT from_on, through_on FROM trust_deposit_interest_payments"
        " WHERE pmc_id = %s AND request_key = %s",
        (session.pmc_id, f"deposit-interest/{pay.request_id}"),
    ).fetchone()
    assert row is not None
    return Paid(from_on=row[0], through_on=row[1], amount=cents(amount[0]), paid_as=pay.paid_as)
