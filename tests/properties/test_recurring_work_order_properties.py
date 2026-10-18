"""Property-based tests for recurring work orders (migration 20261018000085).

Hypothesis makes a recurring work order (from some day of 2026, every 1 to 12 months, on a day of
the month 1 to 28), runs trust_open_recurring_work_orders on days some days apart, as a nightly
run on a server that was off now and then would, and may end it from any day at any point. A model
keeps the days it should have opened. After every step:
  - the work orders it opened are exactly the model's days: the first day of the month on or after
    it starts, then every so many months, through the last run, before its end as of each run;
  - each day once, opened on that day, and each run says how many it opened.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, date, datetime, timedelta

import psycopg
from helpers import make_pmc
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)


def add_months(day, months):
    year, month = divmod(day.month - 1 + months, 12)
    return date(day.year + year, month + 1, day.day)


def schedule(starts_on, every_months, day_of_month, through):
    """The model: each day it comes due through a day, ignoring any end."""
    first = date(starts_on.year, starts_on.month, day_of_month)
    if first < starts_on:
        first = add_months(first, 1)
    due, n = [], 0
    while (d := add_months(first, n * every_months)) <= through:
        due.append(d)
        n += 1
    return due


class RecurringMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn

    @initialize(
        start=st.integers(min_value=0, max_value=180),
        every_months=st.integers(min_value=1, max_value=12),
        day_of_month=st.integers(min_value=1, max_value=28),
        first_run=st.integers(min_value=-30, max_value=60),
    )
    def make(self, start, every_months, day_of_month, first_run):
        pmc = make_pmc(self.conn)
        self.pmc_id = pmc.pmc_id
        self.starts_on = date(2026, 1, 1) + timedelta(days=start)
        self.every_months, self.day_of_month = every_months, day_of_month
        self.recurring = self.conn.execute(
            "INSERT INTO trust_recurring_work_orders (pmc_id, property_id, summary,"
            " every_months, day_of_month, starts_on) VALUES (%s, %s, 'Filters', %s, %s, %s)"
            " RETURNING id",
            (pmc.pmc_id, pmc.owners[0].property_id, every_months, day_of_month, self.starts_on),
        ).fetchone()[0]
        self.today = self.starts_on + timedelta(days=first_run)
        self.ends_on = None
        self.opened: set[date] = set()

    @rule(days=st.integers(min_value=0, max_value=120))
    def run(self, days):
        self.today += timedelta(days=days)
        (count,) = self.conn.execute(
            "SELECT trust_open_recurring_work_orders(%s, %s)", (self.pmc_id, self.today)
        ).fetchone()
        due = {
            d
            for d in schedule(self.starts_on, self.every_months, self.day_of_month, self.today)
            if self.ends_on is None or d < self.ends_on
        }
        new = due - self.opened
        assert count == len(new)
        event("opened some" if new else "opened none")
        self.opened |= new

    @precondition(lambda self: self.ends_on is None)
    @rule(days=st.integers(min_value=-60, max_value=120))
    def end(self, days):
        self.ends_on = self.today + timedelta(days=days)
        self.conn.execute(
            "INSERT INTO trust_recurring_work_order_ends (recurring_work_order_id, pmc_id, ends_on)"
            " VALUES (%s, %s, %s)",
            (self.recurring, self.pmc_id, self.ends_on),
        )
        event("ended before the last run" if self.ends_on <= self.today else "ended ahead")

    @invariant()
    def opened_are_the_model(self):
        rows = self.conn.execute(
            "SELECT recurring_on, opened_at FROM trust_work_orders"
            " WHERE recurring_work_order_id = %s ORDER BY recurring_on",
            (self.recurring,),
        ).fetchall()
        assert [on for on, _ in rows] == sorted(self.opened)
        for on, at in rows:
            assert at == datetime(on.year, on.month, on.day, tzinfo=UTC)


def test_recurring_work_orders_open_on_their_days_once(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: RecurringMachine(conn))
