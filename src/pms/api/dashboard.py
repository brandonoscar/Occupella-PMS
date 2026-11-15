"""The staff Dashboard's figures (/; ADR 0010 phase 3, part H): only numbers that change, each
beside what it was a month before. Occupancy, rent collected against rent due this month,
what is past due and how many tenants owe it, leases ending in 60 days, vacant units and how
long on average, the next draw day and what it would pay, and trust bank accounts with a month
ended and not reconciled. No fixed counts (properties, tenants). Each figure is counted by the
report that counts it everywhere else (delinquency, vacancy, lease expirations), so the Dashboard
never disagrees with Reports: what is past due is the delinquency report's own arithmetic for
both days in one pass (issue #231), held to the report by
tests/properties/test_dashboard_past_due_properties.py. What needs someone stays the Today answer
(src/pms/api/today.py).

Read only.
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from pms.api import payables, payouts, reconcile, rent
from pms.api.deps import Conn, Staff
from pms.api.today import months_ended

router = APIRouter(prefix="/api/dashboard")
CENT = Decimal("0.01")
# The days the Dashboard counts: each figure reaches a month back and the next draw day a month
# on, which Python's dates can't do at their ends (0001-01-01 answered with a server error, found
# by the nightly API fuzz).
FIRST_DAY, LAST_DAY = date(1900, 1, 1), date(9999, 1, 1)


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
# (no longer managed) or not added yet, which it lists too. The day a unit was added is the
# company's (its time zone), as `day` is: a unit added at 8 pm in New York counts that evening.
# Each day asked in one pass: whether a unit is archived and the day it was added are read once
# (issue #231), not once a day, and the day added is trust_pmc_day's, the company's time zone
# read once rather than once a unit (that cost 38 ms on 1,000 units).
UNITS = """
WITH days AS (SELECT DISTINCT d AS day FROM unnest(%(days)s::date[]) AS d),
managed AS MATERIALIZED (
  SELECT u.id, (u.created_at AT TIME ZONE z.zone)::date AS added
  FROM trust_units AS u
  CROSS JOIN (SELECT trust_pmc_time_zone(%(pmc)s) AS zone) AS z
  WHERE u.pmc_id = %(pmc)s AND NOT trust_is_archived(NULL, u.id)
)
SELECT d.day,
       count(units.id),
       count(units.id) FILTER (WHERE NOT units.occupied),
       round(avg(d.day - units.since) FILTER (WHERE NOT units.occupied))
FROM days AS d
LEFT JOIN LATERAL (
  SELECT u.id,
         EXISTS (SELECT 1 FROM trust_leases AS l
                 WHERE l.unit_id = u.id AND l.starts_on <= d.day
                   AND (l.ends_on IS NULL OR l.ends_on >= d.day)) AS occupied,
         coalesce((SELECT max(l.ends_on) + 1 FROM trust_leases AS l
                   WHERE l.unit_id = u.id AND l.ends_on < d.day),
                  u.added) AS since
  FROM managed AS u
  WHERE u.added <= d.day
) AS units ON true
GROUP BY d.day
"""


def units_on(
    conn: psycopg.Connection[Any], pmc: UUID, days: list[date]
) -> dict[date, tuple[int, int, int | None]]:
    """(units managed, vacant, average days vacant) on each day."""
    return {
        day: (int(total), int(vacant), None if average is None else int(average))
        for day, total, vacant, average in conn.execute(UNITS, {"pmc": pmc, "days": days})
    }


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


# What is past due as of the end of each day asked, and how many tenants are on the leases that
# owe it: trust_report_delinquency's total and the tenants of its lease lines, for several days
# in one pass (issue #231: the report run once a day cost the Dashboard ~145 ms each on 1,000
# units). It is the report's own arithmetic, step by step, with each day's what-was-paid
# (payments net of bounces by the day's end), what-was-waived and offsets laid beside one
# another; what doesn't change with the day (a charge's place in the order money pays it, rent
# first; whether a credit is a waiver or a lease end's credit on a fee the company keeps) is
# worked out once. tests/properties/test_dashboard_past_due_properties.py holds it to the report
# for random books and days.
PAST_DUE = """
WITH days AS (
    SELECT DISTINCT d AS as_of, (d + 1)::timestamp AT TIME ZONE 'UTC' AS cutoff
    FROM unnest(%(days)s::date[]) AS d
),
owing AS MATERIALIZED (
    -- The leases that may owe on a day asked: each with a charge due by the last of them that
    -- its payments, net of bounces, don't cover, or that was paid after the end of a day asked
    -- on or after its due day. Every other lease's charges due by a day asked were paid in full
    -- by its end, net of every bounce (payments and bounces are positive amounts), and credits
    -- and waivers only take more off: it owes nothing on any of them, whatever the rest says.
    SELECT DISTINCT c.lease_id
    FROM trust_charges AS c
    LEFT JOIN (
        SELECT cp.charge_id, sum(cp.amount) AS amount, max(tr.event_at) AS last_at
        FROM trust_charge_payments AS cp
        JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
        WHERE cp.pmc_id = %(pmc)s
        GROUP BY cp.charge_id
    ) AS paid ON paid.charge_id = c.id
    LEFT JOIN (
        SELECT r.charge_id, sum(r.amount) AS amount
        FROM trust_payment_reversals AS r WHERE r.pmc_id = %(pmc)s
        GROUP BY r.charge_id
    ) AS back ON back.charge_id = c.id
    WHERE c.pmc_id = %(pmc)s AND c.kind <> 'credit'
      AND c.due_on <= (SELECT max(d.as_of) FROM days AS d)
      AND (c.amount > coalesce(paid.amount, 0) - coalesce(back.amount, 0)
           OR EXISTS (SELECT 1 FROM days AS d
                      WHERE c.due_on <= d.as_of AND paid.last_at >= d.cutoff))
),
charges AS MATERIALIZED (
    -- Every charge of those leases, credits too.
    SELECT c.id, c.lease_id, c.kind, c.amount, c.due_on, c.created_at,
           CASE
               WHEN c.kind = 'rent' THEN 0
               WHEN c.kind = 'fee' AND (
                   c.recurring_charge_id IS NOT NULL
                   OR EXISTS (
                       SELECT 1 FROM trust_lease_end_adjustments AS x
                       JOIN trust_charges AS o ON o.id = x.charge_id
                       WHERE x.adjustment_charge_id = c.id
                         AND (o.kind = 'rent' OR o.recurring_charge_id IS NOT NULL)
                   )
               ) THEN 1
               ELSE 2
           END AS pay_rank
    FROM trust_charges AS c
    WHERE c.pmc_id = %(pmc)s AND c.lease_id IN (SELECT o.lease_id FROM owing AS o)
),
company_kept AS (
    -- A lease end's credits for fees the company keeps (as trust_charge_payee has it).
    SELECT a.charge_id, a.adjustment_charge_id
    FROM trust_lease_end_adjustments AS a
    JOIN trust_charges AS src ON src.id = a.charge_id
    LEFT JOIN trust_recurring_charges AS rc ON rc.id = src.recurring_charge_id
    WHERE a.pmc_id = %(pmc)s
      AND coalesce((
          SELECT py.payee FROM trust_charge_type_payees AS py
          WHERE py.charge_type_id = coalesce(src.charge_type_id, rc.charge_type_id)
            AND py.created_at <= src.created_at
          ORDER BY py.id DESC
          LIMIT 1
      ), 'owner') = 'company'
),
credits_taking AS (
    -- Credits that come off the charge they name: waivers (migration 0079), and a lease end's
    -- credits for fees the company keeps (migration 20261107000100).
    SELECT w.waived_charge_id AS id, w.credit_charge_id AS credit_id
    FROM trust_charge_waivers AS w
    WHERE w.pmc_id = %(pmc)s
    UNION ALL
    SELECT k.charge_id, k.adjustment_charge_id
    FROM company_kept AS k
    JOIN trust_charges AS credit ON credit.id = k.adjustment_charge_id
    WHERE credit.kind = 'credit'
),
paid AS (
    -- What of each charge was paid by each day's end, net of bounces by then: each charge's
    -- payments and bounces read by its own key (the charges left are few).
    SELECT c.id AS charge_id, d.as_of, sum(m.amount) AS amount
    FROM charges AS c
    CROSS JOIN LATERAL (
        SELECT cp.transfer_id, cp.amount
        FROM trust_charge_payments AS cp WHERE cp.charge_id = c.id
        UNION ALL
        SELECT r.reversal_id, -r.amount
        FROM trust_payment_reversals AS r WHERE r.charge_id = c.id
    ) AS m
    JOIN pgledger_transfers AS tr ON tr.id = m.transfer_id
    JOIN days AS d ON tr.event_at < d.cutoff
    GROUP BY c.id, d.as_of
),
waived AS (
    -- What those credits due by each day take off the charge each names.
    SELECT t.id, d.as_of, sum(k.amount) AS amount
    FROM credits_taking AS t
    JOIN trust_charges AS k ON k.id = t.credit_id
    JOIN days AS d ON k.due_on <= d.as_of
    GROUP BY t.id, d.as_of
),
settled AS (
    SELECT c.id, c.lease_id, c.due_on, c.pay_rank, c.created_at, d.as_of,
           c.amount - coalesce(p.amount, 0)
           - least(greatest(c.amount - coalesce(p.amount, 0), 0), coalesce(w.amount, 0))
               AS left_owing,
           coalesce(w.amount, 0)
           - least(greatest(c.amount - coalesce(p.amount, 0), 0), coalesce(w.amount, 0))
               AS spare,
           CASE WHEN c.due_on > d.as_of THEN coalesce(p.amount, 0) ELSE 0 END AS paid_ahead
    FROM charges AS c
    CROSS JOIN days AS d
    LEFT JOIN paid AS p ON p.charge_id = c.id AND p.as_of = d.as_of
    LEFT JOIN waived AS w ON w.id = c.id AND w.as_of = d.as_of
    WHERE c.kind <> 'credit'
),
open_charges AS (
    SELECT s.as_of, s.lease_id, s.left_owing,
           sum(s.left_owing) OVER (
               PARTITION BY s.as_of, s.lease_id
               ORDER BY s.pay_rank = 2, s.due_on, s.pay_rank, s.created_at, s.id
               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
           ) AS owing_before
    FROM settled AS s
    WHERE s.due_on <= s.as_of
),
offsets AS (
    -- Per lease and day: credits due by the day that take off no charge of their own, what a
    -- waiver could not take off its charge, and payments by then toward charges not yet due.
    SELECT o.lease_id, o.as_of, sum(o.amount) AS amount
    FROM (
        SELECT c.lease_id, d.as_of, c.amount
        FROM charges AS c
        JOIN days AS d ON c.due_on <= d.as_of
        WHERE c.kind = 'credit'
          AND NOT EXISTS (SELECT 1 FROM credits_taking AS t WHERE t.credit_id = c.id)
        UNION ALL
        SELECT s.lease_id, s.as_of, s.spare + s.paid_ahead FROM settled AS s
    ) AS o
    GROUP BY o.lease_id, o.as_of
),
by_lease AS (
    SELECT o.as_of, o.lease_id,
           sum(o.left_owing - least(
               o.left_owing,
               greatest(coalesce(f.amount, 0) - coalesce(o.owing_before, 0), 0)
           )) AS owed
    FROM open_charges AS o
    LEFT JOIN offsets AS f ON f.lease_id = o.lease_id AND f.as_of = o.as_of
    GROUP BY o.as_of, o.lease_id
    HAVING sum(o.left_owing - least(
        o.left_owing,
        greatest(coalesce(f.amount, 0) - coalesce(o.owing_before, 0), 0)
    )) > 0
)
SELECT d.as_of, round(coalesce(sum(b.owed), 0), 2),
       coalesce(sum((SELECT count(*) FROM trust_lease_tenants AS lt
                     WHERE lt.lease_id = b.lease_id)), 0)
FROM days AS d
LEFT JOIN by_lease AS b ON b.as_of = d.as_of
GROUP BY d.as_of
"""


def past_due_on(
    conn: psycopg.Connection[Any], pmc: UUID, days: list[date]
) -> dict[date, tuple[Decimal, int]]:
    """Each day's past due total (the delinquency report's) and the tenants on the leases that
    owe it, in one query."""
    return {
        as_of: (Decimal(total), int(tenants))
        for as_of, total, tenants in conn.execute(PAST_DUE, {"pmc": pmc, "days": days})
    }


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
    day = on or rent.today(conn, session.pmc_id)
    if not FIRST_DAY <= day <= LAST_DAY:
        raise HTTPException(422, "the Dashboard shows a day from 01/01/1900 to 01/01/9999")
    then = month_before(day)
    pmc = session.pmc_id
    managed = units_on(conn, pmc, [day, then])
    units, vacant_now, average_now = managed[day]
    units_then, vacant_then, average_then = managed[then]
    paid_now, due_now = collected(conn, pmc, day)
    paid_then, due_then = collected(conn, pmc, then)
    owing = past_due_on(conn, pmc, [day, then])
    owed_now, tenants_now = owing[day]
    owed_then, tenants_then = owing[then]
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
