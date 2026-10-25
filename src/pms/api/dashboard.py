"""The staff Dashboard's figures (/; ADR 0010 phase 3, part H): only numbers that change, each
beside what it was a month before. Occupancy, rent collected against rent due this month,
what is past due and how many tenants owe it, leases ending in 60 days, vacant units and how
long on average, the next draw day and what it would pay, and trust bank accounts with a month
ended and not reconciled. No fixed counts (properties, tenants). Each figure is counted by the
report that counts it everywhere else (delinquency, vacancy, lease expirations), so the Dashboard
never disagrees with Reports. What needs someone stays the Today answer (src/pms/api/today.py).

Read only.
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Query
from pydantic import BaseModel

from pms.api import payables, payouts, reconcile, rent
from pms.api.deps import Conn, Staff
from pms.api.today import months_ended

router = APIRouter(prefix="/api/dashboard")
CENT = Decimal("0.01")


def money(value: Decimal) -> str:
    return f"{Decimal(value).quantize(CENT)}"


def month_before(day: date) -> date:
    """The same day of the month before, or that month's last day when it has no such day."""
    year, month = (day.year - 1, 12) if day.month == 1 else (day.year, day.month - 1)
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def cutoff(day: date) -> datetime:
    """The end of the day, UTC: what happened before it counts as of the day."""
    return datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC)


class Occupancy(BaseModel):
    # Units with a lease in effect, of the units the company manages (not archived, added to the
    # PMS by the day), as a percent to one place; None when it had no unit.
    percent: str | None
    then_percent: str | None


class Collected(BaseModel):
    # Rent due from the month's first day through the day, and what of it was paid by the day
    # (tenants' and housing authorities' payments, less what bounced).
    collected: str
    due: str
    then_collected: str
    then_due: str


class PastDue(BaseModel):
    # The delinquency report's total, and the tenants on the leases that owe it.
    total: str
    tenants: int
    then_total: str
    then_tenants: int


class Ending(BaseModel):
    # Leases whose last day is within the next 60 days (the lease expirations report's).
    within_60: int
    then_within_60: int


class Vacant(BaseModel):
    # Units managed with no lease in effect, and how many days on average they have been vacant
    # (the vacancy report's rules: since the day after the last lease ended, or a unit never
    # leased since it was added).
    units: int
    average_days: int | None
    then_units: int
    then_average_days: int | None


class NextDraw(BaseModel):
    # The next draw day on or after the day, and what a draw of every owner's account would pay
    # now; None without a draw day. The last prepared draws' total, to compare.
    on: date | None
    total: str | None
    last_total: str | None


class Unreconciled(BaseModel):
    # Trust bank accounts with a month ended and not reconciled.
    accounts: int
    then_accounts: int


class Dashboard(BaseModel):
    on: date
    # The day a month before, each figure's "then".
    month_ago: date
    occupancy: Occupancy
    rent: Collected
    past_due: PastDue
    leases_ending: Ending
    vacant: Vacant
    next_draw: NextDraw
    unreconciled: Unreconciled


# The units the company manages on a day (not archived, added to the PMS by then), each occupied
# when a lease is in effect, else vacant since the day after its last lease ended or, never
# leased, since the day it was added: the vacancy report's rules, without the units archived
# (no longer managed) or not added yet, which it lists too.
UNITS = """
SELECT count(*),
       count(*) FILTER (WHERE NOT occupied),
       round(avg(%(day)s::date - since) FILTER (WHERE NOT occupied))
FROM (
  SELECT EXISTS (SELECT 1 FROM trust_leases AS l
                 WHERE l.unit_id = u.id AND l.starts_on <= %(day)s
                   AND (l.ends_on IS NULL OR l.ends_on >= %(day)s)) AS occupied,
         coalesce((SELECT max(l.ends_on) + 1 FROM trust_leases AS l
                   WHERE l.unit_id = u.id AND l.ends_on < %(day)s),
                  (u.created_at AT TIME ZONE 'UTC')::date) AS since
  FROM trust_units AS u
  WHERE u.pmc_id = %(pmc)s AND NOT trust_is_archived(NULL, u.id)
    AND (u.created_at AT TIME ZONE 'UTC')::date <= %(day)s
) AS units
"""


def units_on(conn: psycopg.Connection[Any], pmc: UUID, day: date) -> tuple[int, int, int | None]:
    """(units managed, vacant, average days vacant) on the day."""
    total, vacant, average = conn.execute(UNITS, {"pmc": pmc, "day": day}).fetchone() or (
        0,
        0,
        None,
    )
    return int(total), int(vacant), None if average is None else int(average)


def percent(part: int, whole: int) -> str | None:
    if whole == 0:
        return None
    return f"{(Decimal(part) * 100 / whole).quantize(Decimal('0.1'))}"


# Rent due from a month's first day through a day, and what of it was paid by then.
COLLECTED = """
WITH due AS (
    SELECT c.id, c.amount FROM trust_charges AS c
    WHERE c.pmc_id = %(pmc)s AND c.kind = 'rent' AND c.due_on BETWEEN %(start)s AND %(day)s
),
authority_due AS (
    SELECT c.id, c.amount FROM trust_assistance_charges AS c
    WHERE c.pmc_id = %(pmc)s AND c.kind = 'charge' AND c.source = 'rent'
      AND c.due_on BETWEEN %(start)s AND %(day)s
)
SELECT
    (SELECT coalesce(sum(amount), 0) FROM due)
  + (SELECT coalesce(sum(amount), 0) FROM authority_due),
    (SELECT coalesce(sum(p.amount), 0) FROM trust_charge_payments AS p
     JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
     WHERE p.charge_id IN (SELECT id FROM due) AND tr.event_at < %(cutoff)s)
  - (SELECT coalesce(sum(r.amount), 0) FROM trust_payment_reversals AS r
     JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
     WHERE r.charge_id IN (SELECT id FROM due) AND tr.event_at < %(cutoff)s)
  + (SELECT coalesce(sum(p.amount), 0) FROM trust_assistance_payments AS p
     JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
     WHERE p.assistance_charge_id IN (SELECT id FROM authority_due) AND tr.event_at < %(cutoff)s)
  - (SELECT coalesce(sum(r.amount), 0) FROM trust_assistance_payment_reversals AS r
     JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
     WHERE r.assistance_charge_id IN (SELECT id FROM authority_due) AND tr.event_at < %(cutoff)s)
"""


def collected(conn: psycopg.Connection[Any], pmc: UUID, day: date) -> tuple[Decimal, Decimal]:
    """(collected, due) of the day's month through the day."""
    due, paid = conn.execute(
        COLLECTED, {"pmc": pmc, "start": day.replace(day=1), "day": day, "cutoff": cutoff(day)}
    ).fetchone() or (Decimal(0), Decimal(0))
    return Decimal(paid), Decimal(due)


def past_due(conn: psycopg.Connection[Any], pmc: UUID, day: date) -> tuple[Decimal, int]:
    rows = conn.execute(
        "SELECT item, tenants, total FROM trust_report_delinquency(%s, %s)"
        " WHERE item IN ('lease', 'total')",
        (pmc, day),
    ).fetchall()
    total = sum((Decimal(t) for item, _, t in rows if item == "total"), Decimal(0))
    tenants = sum(len(names.split(", ")) for item, names, _ in rows if item == "lease" and names)
    return total, tenants


def ending_within_60(conn: psycopg.Connection[Any], pmc: UUID, day: date) -> int:
    row = conn.execute(
        "SELECT count(*) FROM trust_report_lease_expirations(%s, %s)"
        " WHERE item = 'lease' AND days_left <= 60",
        (pmc, day),
    ).fetchone()
    return int(row[0]) if row else 0


# Where an account's open months started as approvals and statements stood at a moment: the day
# after its last period approved by then, else its cutover, else its first statement imported by
# then (as the month-end page finds it now).
OPEN_FROM = """
SELECT (coalesce(
    (SELECT max(r.period_end) FROM trust_reconciliations AS r
     WHERE r.bank_account_id = b.id AND r.approved_at < %(cutoff)s),
    b.opened_at,
    (SELECT min(s.first_day)::timestamp AT TIME ZONE 'UTC' FROM trust_bank_statements AS s
     WHERE s.bank_account_id = b.id AND s.imported_at < %(cutoff)s)
  ) AT TIME ZONE 'UTC')::date
FROM trust_bank_accounts AS b WHERE b.id = %(bank)s
"""


def unreconciled(
    session: Any, conn: psycopg.Connection[Any], day: date, then: date
) -> tuple[int, int]:
    """Accounts with a month ended and not reconciled on the day, and as approvals and statements
    stood a month before."""
    now_count = then_count = 0
    for bank in reconcile.books(session, conn):
        start = bank.closed_through + timedelta(days=1) if bank.closed_through else bank.next_start
        if start and months_ended(start, day):
            now_count += 1
        row = conn.execute(OPEN_FROM, {"bank": bank.id, "cutoff": cutoff(then)}).fetchone()
        if row and row[0] and months_ended(row[0], then):
            then_count += 1
    return now_count, then_count


def next_draw(conn: psycopg.Connection[Any], pmc: UUID, day: date) -> NextDraw:
    draw_day = payouts.draw_day_of(conn, pmc).draw_day
    last = conn.execute(
        "SELECT coalesce(sum(l.amount), 0) FROM trust_draw_run_lines AS l WHERE l.run_id = ("
        "  SELECT r.id FROM trust_draw_runs AS r WHERE r.pmc_id = %s"
        "  ORDER BY r.month DESC, r.created_at DESC LIMIT 1)",
        (pmc,),
    ).fetchone()
    has_run = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM trust_draw_runs WHERE pmc_id = %s)", (pmc,)
    ).fetchone()
    last_total = money(last[0]) if last and has_run and has_run[0] else None
    if draw_day is None:
        return NextDraw(on=None, total=None, last_total=last_total)
    on = day.replace(day=draw_day)
    if on < day:
        on = (day.replace(day=1) + timedelta(days=32)).replace(day=draw_day)
    total = sum(
        (Decimal(m.available) for m in payables.owner_money(conn, pmc) if Decimal(m.available) > 0),
        Decimal(0),
    )
    return NextDraw(on=on, total=money(total), last_total=last_total)


@router.get("")
def dashboard(session: Staff, conn: Conn, on: Annotated[date | None, Query()] = None) -> Dashboard:
    """The Dashboard's figures on the day (today, UTC, by default), each beside its value the same
    day a month before."""
    day = on or rent.today()
    then = month_before(day)
    pmc = session.pmc_id
    units, vacant_now, average_now = units_on(conn, pmc, day)
    units_then, vacant_then, average_then = units_on(conn, pmc, then)
    paid_now, due_now = collected(conn, pmc, day)
    paid_then, due_then = collected(conn, pmc, then)
    owed_now, tenants_now = past_due(conn, pmc, day)
    owed_then, tenants_then = past_due(conn, pmc, then)
    accounts_now, accounts_then = unreconciled(session, conn, day, then)
    return Dashboard(
        on=day,
        month_ago=then,
        occupancy=Occupancy(
            percent=percent(units - vacant_now, units),
            then_percent=percent(units_then - vacant_then, units_then),
        ),
        rent=Collected(
            collected=money(paid_now),
            due=money(due_now),
            then_collected=money(paid_then),
            then_due=money(due_then),
        ),
        past_due=PastDue(
            total=money(owed_now),
            tenants=tenants_now,
            then_total=money(owed_then),
            then_tenants=tenants_then,
        ),
        leases_ending=Ending(
            within_60=ending_within_60(conn, pmc, day),
            then_within_60=ending_within_60(conn, pmc, then),
        ),
        vacant=Vacant(
            units=vacant_now,
            average_days=average_now,
            then_units=vacant_then,
            then_average_days=average_then,
        ),
        next_draw=next_draw(conn, pmc, day),
        unreconciled=Unreconciled(accounts=accounts_now, then_accounts=accounts_then),
    )
