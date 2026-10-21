"""Property-based tests for owners' fees (migration 20261018000082).

Hypothesis gives two owners money, takes fees from them under a few request keys (some sent
again, some reused for another fee), turns taking the management fees each month on and off from
some day of the month, and runs the nightly step on days some days apart. A model keeps each
owner's balance and the fees taken. After every step:
  - each owner's balance and the company's fee account are the model's: a fee is taken exactly
    when its key is new and the owner holds it, a key sent again with the same fee takes nothing
    more, and one reused for another fee is refused;
  - each month's management fee is taken at most once per account, only while the company has it
    on and its day has come, and exactly when the owner holds it;
  - the trust bank account ties out and no owner goes below zero.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import add_agreement, balance, bank_sums, contribute, make_pmc
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

MONEY = st.integers(min_value=1, max_value=50_000).map(lambda c: Decimal(c) / 100)
KEYS = st.sampled_from(["k1", "k2", "k3", "k4"])
KINDS = st.sampled_from(["inspection", "lease_up", "other"])


def month_start(day):
    return day.replace(day=1)


def last_month(day):
    return (month_start(day) - timedelta(days=1)).replace(day=1)


class OwnerFeeMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn

    @initialize(minimum=st.integers(min_value=1_000, max_value=20_000))
    def a_company_with_two_owners(self, minimum):
        pmc = make_pmc(self.conn)
        self.pmc = pmc
        self.minimum = Decimal(minimum) / 100
        for owner in pmc.owners:
            add_agreement(self.conn, pmc.pmc_id, owner.account, date(2026, 1, 1), "0", self.minimum)
        # Model
        self.today = date(2026, 1, 31)
        self.balances = {owner.account: Decimal("0.00") for owner in pmc.owners}
        self.keys: dict[str, tuple] = {}  # request key: (account, kind, amount, memo)
        self.fees = Decimal("0.00")  # in the company's fee account
        self.months: set[tuple[str, date]] = set()  # (account, month) fees taken
        self.automatic, self.day = False, 1
        self.n = 0

    def at(self):
        return datetime(self.today.year, self.today.month, self.today.day, tzinfo=UTC)

    @rule(owner=st.integers(0, 1), amount=MONEY)
    def an_owner_puts_money_in(self, owner, amount):
        account = self.pmc.owners[owner].account
        self.n += 1
        contribute(self.conn, self.pmc.pmc_id, account, f"in-{self.n}", amount, self.at())
        self.balances[account] += amount

    @rule(owner=st.integers(0, 1), key=KEYS, kind=KINDS, amount=MONEY, memo=st.sampled_from("ab"))
    def staff_take_a_fee(self, owner, key, kind, amount, memo):
        account = self.pmc.owners[owner].account
        fee = (account, kind, amount, memo)
        call = (
            "SELECT trust_take_owner_fee(%s, %s, %s, %s, %s, %s, %s)",
            (self.pmc.pmc_id, account, kind, amount, memo, key, self.at()),
        )
        if key in self.keys and self.keys[key] == fee:
            self.conn.execute(*call)
            event("fee sent again")
        elif key in self.keys:
            with pytest.raises(psycopg.errors.UniqueViolation):
                self.conn.execute(*call)
            event("key reused for another fee: refused")
        elif amount > self.balances[account]:
            with pytest.raises(psycopg.errors.CheckViolation):
                self.conn.execute(*call)
            event("more than the owner holds: refused")
        else:
            self.conn.execute(*call)
            self.keys[key] = fee
            self.balances[account] -= amount
            self.fees += amount
            event("fee taken")

    @rule(automatic=st.booleans(), day=st.integers(1, 28))
    def the_company_sets_its_fees(self, automatic, day):
        self.conn.execute(
            "INSERT INTO trust_management_fee_settings (pmc_id, automatic, day_of_month)"
            " VALUES (%s, %s, %s)",
            (self.pmc.pmc_id, automatic, day),
        )
        self.automatic, self.day = automatic, day

    @precondition(lambda self: self.today < date(2026, 7, 1))
    @rule(days=st.integers(min_value=1, max_value=20))
    def a_night_some_days_later(self, days):
        self.today += timedelta(days=days)
        (taken,) = self.conn.execute(
            "SELECT trust_take_monthly_management_fees(%s, %s)", (self.pmc.pmc_id, self.today)
        ).fetchone()
        expected = 0
        if self.automatic and self.today.day >= self.day:
            month = last_month(self.today)
            for account in sorted(self.balances):
                if (account, month) in self.months or self.balances[account] < self.minimum:
                    continue
                self.months.add((account, month))
                self.balances[account] -= self.minimum
                self.fees += self.minimum
                expected += 1
        assert taken == expected
        event(f"{expected} management fees taken")

    @invariant()
    def the_books_are_the_model(self):
        for account, held in self.balances.items():
            assert balance(self.conn, account) == held >= 0
        assert balance(self.conn, self.pmc.pmc_income) == self.fees
        assert all(total == 0 for total in bank_sums(self.conn, self.pmc.pmc_id).values())
        assert (
            set(
                self.conn.execute(
                    "SELECT ledger_account_id, period_start FROM trust_management_fees"
                    " WHERE pmc_id = %s",
                    (self.pmc.pmc_id,),
                ).fetchall()
            )
            == self.months
        )


def test_owner_fees_follow_the_rules_and_the_books_tie_out(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: OwnerFeeMachine(conn))
