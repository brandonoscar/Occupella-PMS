"""Property-based tests for a company's day (migration 20261104000900, src/pms/days.py; the
founder's review of 11/03, item 13: rent paid at 9 pm Eastern on October 31 is October's).

Hypothesis picks a company's time zone and a moment its payments partner says a tenant's payment
was paid, within a day of a month's end as often as anywhere, posts the payment to the owner's
account on the day the app dates it (online_payments.day_of), and reads the owner's statement of
the month before, of that month and of the month after.

Invariants (ci/registry.toml maps them to this test):
  - the company's day is the date in its time zone at that moment (Postgres and Python's time
    zone database agree), never after UTC's day and at most one before it;
  - the payment is on the owner's statement of exactly one month, the month of the company's
    day, dated that day.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg
from helpers import make_pmc, post
from hypothesis import event, given
from hypothesis import strategies as st

from pms import days
from pms.api import ledger_sources, payables
from pms.api import online_payments as op


def first_of(year: int, month: int) -> date:
    return date(year + (month - 1) // 12, (month - 1) % 12 + 1, 1)


# A month's end in 2026 or 2027, give or take a day: where UTC's day and a company's part.
NEAR_A_MONTHS_END = st.builds(
    lambda months, minutes: (
        datetime.combine(first_of(2026, months + 1), datetime.min.time(), UTC)
        + timedelta(minutes=minutes)
    ),
    st.integers(min_value=1, max_value=24),
    st.integers(min_value=-24 * 60, max_value=24 * 60),
)
ANYWHERE = st.datetimes(
    min_value=datetime(2026, 1, 2), max_value=datetime(2027, 12, 30), timezones=st.just(UTC)
)


def statement_total(conn, pmc, start: date, end: date) -> tuple[Decimal, list[str]]:
    """What the owner's statement over these days shows coming in, and the days it shows."""
    rows = conn.execute(
        "SELECT posted_on, amount FROM trust_report_owner_statement(%s, %s, %s, %s)"
        " WHERE posted_on IS NOT NULL AND amount IS NOT NULL",
        (pmc.pmc_id, pmc.owners[0].owner_id, *ledger_sources.period(start, end)),
    ).fetchall()
    return sum((amount for _, amount in rows), Decimal(0)), [day for day, _ in rows]


@given(
    zone=st.sampled_from(sorted(days.ZONES)),
    at=st.one_of(NEAR_A_MONTHS_END, ANYWHERE),
    cents=st.integers(min_value=1, max_value=10**7),
)
def check(conn, zone, at, cents):
    pmc = make_pmc(conn, owners=1, time_zone=zone)
    local = at.astimezone(ZoneInfo(zone)).date()

    day = op.day_of(conn, pmc.pmc_id, at)
    assert day == local
    assert at.date() - timedelta(days=1) <= day <= at.date()
    event("the company's day is the day before UTC's" if day < at.date() else "the same day")

    amount = Decimal(cents) / 100
    post(
        conn,
        pmc.pmc_id,
        f"paid-{uuid.uuid4()}",
        [(pmc.operating_cash, pmc.owners[0].account, amount)],
        payables.at(day),
        {"memo": "Rent paid online"},
    )
    month = day.replace(day=1)
    before = first_of(month.year, month.month - 1)
    after = first_of(month.year, month.month + 1)
    later = first_of(month.year, month.month + 2)
    shown = {
        start: statement_total(conn, pmc, start, end - timedelta(days=1))
        for start, end in ((before, month), (month, after), (after, later))
    }
    assert shown[month][0] == amount
    assert shown[before][0] == shown[after][0] == 0
    assert set(shown[month][1]) <= {day.isoformat(), day.strftime("%m/%d/%Y")}


def test_a_payment_is_on_the_statement_of_the_companys_own_day(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
