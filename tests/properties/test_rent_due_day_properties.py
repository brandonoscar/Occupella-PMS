"""Property-based tests for a lease's rent due day (migration 20261018000081).

Hypothesis opens a lease (starting on some day of January 2026, month to month or ending on some
day of a later month), picks due days from the first of random months, and runs the nightly rent
run on days some days apart, as a server that was off now and then would. A model keeps the due
day in force each month. After every step:
  - each month's rent is charged once, by the first run of that month on or after its due date
    (any run of the month for a lease due on the first, as before), due on the lease's due day kept
    within the days the lease is in effect that month, for those days;
  - no rent is charged before its due date, and a month with no run on or after it is not charged;
  - a due day is refused exactly when it starts before the lease's first month, after its end, in
    or before a month charged already, or in a month with one already;
  - trust_rent_due_on agrees with the model in every month.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import add_unit, make_pmc, open_lease
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

BASE = Decimal("1000.00")
LAST_MONTH = 7  # August 2026


def first(month):
    """The first of the month `month` months after January 2026 (0 is January)."""
    year, m = divmod(month, 12)
    return date(2026 + year, m + 1, 1)


def month_of(day):
    return (day.year - 2026) * 12 + day.month - 1


def last_day(month):
    return first(month + 1) - timedelta(days=1)


class DueDayMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn

    @initialize(
        start_day=st.integers(min_value=1, max_value=28),
        ends=st.none() | st.tuples(st.integers(1, 6), st.integers(1, 31)),
    )
    def open_a_lease(self, start_day, ends):
        pmc = make_pmc(self.conn)
        self.pmc = pmc.pmc_id
        tenant = self.conn.execute(
            "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
            (pmc.tenant_deposit,),
        ).fetchone()[0]
        unit = add_unit(self.conn, self.pmc, pmc.owners[0].property_id)
        self.starts = date(2026, 1, start_day)
        self.ends = None
        if ends is not None:
            month, day = ends
            self.ends = min(first(month) + timedelta(days=day - 1), last_day(month))
        self.lease = open_lease(self.conn, self.pmc, unit, self.starts, self.ends, BASE, [tenant])
        # Model
        self.due_days: dict[int, int] = {}  # month: due day from its first
        self.charged: dict[int, date] = {}  # month: the due date its rent was charged on
        self.today = date(2025, 12, 31)  # the last day the run ran
        event("month to month" if ends is None else "with an end date")

    def due_day(self, month):
        earlier = [m for m in self.due_days if m <= month]
        return self.due_days[max(earlier)] if earlier else 1

    def due_on(self, month):
        """The lease's rent due date in a month, or None when it isn't in effect then."""
        if self.starts > last_day(month) or (self.ends is not None and self.ends < first(month)):
            return None
        on = max(first(month) + timedelta(days=self.due_day(month) - 1), self.starts)
        return min(on, self.ends or last_day(month))

    @rule(month=st.integers(min_value=-1, max_value=LAST_MONTH), day=st.integers(1, 28))
    def pick_a_due_day(self, month, day):
        allowed = (
            month >= 0
            and (self.ends is None or first(month) <= self.ends)
            and all(charged < month for charged in self.charged)
            and month not in self.due_days
        )
        sql = (
            "INSERT INTO trust_rent_due_days (pmc_id, lease_id, starts_on, due_day)"
            " VALUES (%s, %s, %s, %s)"
        )
        if allowed:
            self.conn.execute(sql, (self.pmc, self.lease, first(month), day))
            self.due_days[month] = day
            event("due day picked")
        else:
            with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation)):
                self.conn.execute(sql, (self.pmc, self.lease, first(month), day))
            event("due day refused")

    @precondition(lambda self: month_of(self.today) <= LAST_MONTH)
    @rule(days=st.integers(min_value=1, max_value=20))
    def run_rent_some_days_later(self, days):
        self.today += timedelta(days=days)
        self.conn.execute(
            "SELECT trust_run_rent(%s, %s, %s)", (self.pmc, self.today.replace(day=1), self.today)
        )
        month = month_of(self.today)
        due = self.due_on(month)
        if (
            due is not None
            and month not in self.charged
            and (self.due_day(month) == 1 or due <= self.today)
        ):
            self.charged[month] = due
            event(
                "charged on the first"
                if self.due_day(month) == 1
                else "charged on its due day"
                if due.day == self.due_day(month)
                else "charged on a due date kept within the lease"
            )

    @invariant()
    def rent_is_charged_once_on_its_due_date_for_its_days(self):
        rows = self.conn.execute(
            "SELECT due_on, amount FROM trust_charges WHERE lease_id = %s AND kind = 'rent'"
            " ORDER BY due_on",
            (self.lease,),
        ).fetchall()
        assert [due_on for due_on, _ in rows] == sorted(self.charged.values())
        for due_on, amount in rows:
            assert self.charged[month_of(due_on)] == due_on
            (expected,) = self.conn.execute(
                "SELECT amount FROM trust_rent_for_month(%s, %s, %s, %s)",
                (self.starts, self.ends, BASE, due_on),
            ).fetchone()
            assert amount == expected

    @invariant()
    def the_due_date_is_the_model(self):
        for month in range(0, LAST_MONTH + 1):
            assert self.conn.execute(
                "SELECT trust_rent_due_on(%s, %s)", (self.lease, first(month))
            ).fetchone() == (self.due_on(month),)


def test_rent_is_charged_on_its_due_day_once_a_month(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: DueDayMachine(conn))
