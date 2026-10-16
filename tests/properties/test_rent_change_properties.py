"""Property-based tests for a lease's rent changing from the first of a month (migration
20261015000061).

Hypothesis opens a lease (starting on some day of January 2026, month to month or ending later),
records rent changes from the first of random months with notice given by the first of the month
before, and runs the rent month by month, in random order. A model keeps the rent in force each
month. After every step:
  - each month's rent charge is that month's rent in force, for the days the lease is in effect;
  - a change is refused exactly when it starts no later than the lease, after its end, in a month
    charged already, or in a month with a change already; a lease with an end date names the
    term that allows it;
  - the rent in force (trust_rent_on) and the rent roll agree with the model.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date
from decimal import Decimal

import psycopg
import pytest
from helpers import add_unit, charge_rent_due, make_pmc, open_lease
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
RENTS = st.integers(min_value=50_000, max_value=300_000).map(lambda c: Decimal(c) / 100)


def first(month):
    """The first of the month `month` months after January 2026 (0 is January)."""
    year, m = divmod(month, 12)
    return date(2026 + year, m + 1, 1)


class RentChangeMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn

    @initialize(
        start_day=st.integers(min_value=1, max_value=28), ends=st.none() | st.integers(3, 10)
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
        # Ends on the last day of a month, so every month it is in effect is whole but the first.
        self.ends = None if ends is None else date.fromordinal(first(ends).toordinal() - 1)
        self.lease = open_lease(self.conn, self.pmc, unit, self.starts, self.ends, BASE, [tenant])
        # Model
        self.changes: dict[int, Decimal] = {}  # month: rent from its first day
        self.charged = -1  # the last month the rent run charged
        event("month to month" if ends is None else "with an end date")

    def in_force(self, month):
        earlier = [m for m in self.changes if m <= month]
        return self.changes[max(earlier)] if earlier else BASE

    def last_month(self):
        return 11 if self.ends is None else self.ends.month - 1 + (self.ends.year - 2026) * 12

    @rule(month=st.integers(min_value=0, max_value=12), rent=RENTS)
    def record_a_change(self, month, rent):
        effective = first(month)
        allowed = (
            effective > self.starts
            and (self.ends is None or effective <= self.ends)
            and month > self.charged
            and month not in self.changes
        )
        sql = (
            "INSERT INTO trust_rent_changes (pmc_id, lease_id, effective_on, monthly_rent,"
            " notice_given_on, served_by, lease_terms) VALUES (%s, %s, %s, %s, %s, 'mail', %s)"
        )
        args = (
            self.pmc,
            self.lease,
            effective,
            rent,
            first(month - 1),
            None if self.ends is None else "Clause 4",
        )
        if allowed:
            self.conn.execute(sql, args)
            self.changes[month] = rent
            event("change recorded")
        else:
            with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation)):
                self.conn.execute(sql, args)
            event("change refused")

    @precondition(lambda self: self.charged < self.last_month())
    @rule()
    def run_the_next_months_rent(self):
        self.charged += 1
        due = first(self.charged)
        charge_rent_due(self.conn, self.pmc, due)
        (amount,) = self.conn.execute(
            "SELECT amount FROM trust_charges WHERE lease_id = %s AND kind = 'rent'"
            " AND date_trunc('month', due_on) = %s",
            (self.lease, due),
        ).fetchone()
        (expected,) = self.conn.execute(
            "SELECT amount FROM trust_rent_for_month(%s, %s, %s, %s)",
            (self.starts, self.ends, self.in_force(self.charged), max(due, self.starts)),
        ).fetchone()
        assert amount == expected
        event(
            "rent charged at a changed rent"
            if self.in_force(self.charged) != BASE
            else "rent charged"
        )

    @invariant()
    def the_rent_in_force_is_the_model(self):
        for month in range(0, 13):
            (rent,) = self.conn.execute(
                "SELECT trust_rent_on(%s, %s)", (self.lease, first(month))
            ).fetchone()
            assert rent == self.in_force(month)
        # The rent roll in mid-February shows February's rent while the lease is in effect.
        if self.ends is None or self.ends >= date(2026, 2, 15):
            assert self.conn.execute(
                "SELECT monthly_rent FROM trust_report_rent_roll(%s, %s) WHERE item = 'unit'",
                (self.pmc, date(2026, 2, 15)),
            ).fetchall() == [(self.in_force(1),)]


def test_the_rent_in_force_is_charged_each_month_and_changes_follow_the_rules(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: RentChangeMachine(conn))
