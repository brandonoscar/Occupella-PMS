"""Property-based tests for prepaid rent used by itself (migration 0117, rent.use_prepaid_by_itself;
the founder's review q031).

Hypothesis charges a lease rent, fees and credits, records payments (what is not owed is held as
the tenant's prepaid rent), turns the company's setting on and off, places payment holds and runs
the use on one of the last few days, in random order. A model predicts what each run uses.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - a run uses nothing while the company has it off;
  - with it on, a run on a day uses the tenant's prepaid rent for what the lease owes, no more
    than either, once that day: a second run that day uses nothing, whatever was charged since;
  - a lease on a payment hold that day keeps its prepaid rent;
  - what the lease owes, the owner's account and the prepaid rent are the model's, and every
    trust bank account ties out.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
from helpers import add_unit, balance, bank_sums, charge, hold_payments, make_pmc, open_lease
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

from pms.api import rent

TODAY = datetime.now(UTC).date()
START = TODAY - timedelta(days=400)
CENTS = st.integers(min_value=1, max_value=300_000).map(lambda c: Decimal(c) / 100)
DAYS_BACK = st.integers(min_value=0, max_value=3)


class PrepaidMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        pmc = make_pmc(conn)
        self.pmc_id = pmc.pmc_id
        self.owner = pmc.owners[0].account
        self.prepaid = pmc.prepaid_rent
        tenant = conn.execute(
            "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
            (pmc.prepaid_rent,),
        ).fetchone()[0]
        unit = add_unit(conn, self.pmc_id, pmc.owners[0].property_id)
        self.lease = open_lease(conn, self.pmc_id, unit, START, None, "1000.00", [tenant])
        self.rents = 0
        # Model
        self.on = False
        self.holds: list = []  # each hold's first day
        self.used_on: set = set()  # the days a run used prepaid rent
        self.owed = Decimal(0)
        self.in_owner = Decimal(0)
        self.in_prepaid = Decimal(0)

    @initialize(on=st.booleans(), ahead=CENTS)
    def a_tenant_paid_ahead(self, on, ahead):
        """The company may start with it on, and the tenant with some prepaid rent."""
        if on:
            self.turn(True)
        self.pay(ahead)

    @rule(amount=CENTS)
    def charge_rent(self, amount):
        charge(self.conn, self.pmc_id, self.lease, START + timedelta(days=self.rents), amount)
        self.rents += 1
        self.owed += amount

    @rule(amount=CENTS)
    def charge_a_fee(self, amount):
        charge(self.conn, self.pmc_id, self.lease, TODAY, amount, "fee")
        self.owed += amount

    @rule(amount=CENTS)
    def credit(self, amount):
        charge(self.conn, self.pmc_id, self.lease, TODAY, amount, "credit")
        self.owed -= amount

    @precondition(lambda self: not self.holds)  # a hold refuses payments; tested elsewhere
    @rule(amount=CENTS)
    def pay(self, amount):
        with self.conn.transaction():
            rent.record_payment(self.conn, self.pmc_id, self.lease, uuid.uuid4(), amount, TODAY)
        to_owner = min(amount, max(self.owed, Decimal(0)))
        self.owed -= to_owner
        self.in_owner += to_owner
        self.in_prepaid += amount - to_owner

    @rule(on=st.booleans())
    def turn(self, on):
        self.conn.execute(
            "INSERT INTO trust_prepaid_rent_settings (pmc_id, automatic) VALUES (%s, %s)",
            (self.pmc_id, on),
        )
        self.on = on

    @rule(back=DAYS_BACK)
    def hold(self, back):
        hold_payments(self.conn, self.pmc_id, self.lease, TODAY - timedelta(days=back))
        self.holds.append(TODAY - timedelta(days=back))

    @rule(back=DAYS_BACK)
    def run(self, back):
        day = TODAY - timedelta(days=back)
        use = min(self.in_prepaid, max(self.owed, Decimal(0)))
        if not self.on:
            why = "off"
        elif use <= 0:
            why = "nothing to use"
        elif any(first <= day for first in self.holds):
            why = "on hold"
        elif day in self.used_on:
            why = "used that day already"
        else:
            why = None
        event(f"run: {why or 'used'}")

        used = rent.use_prepaid_by_itself(self.conn, self.pmc_id, day)

        assert used == (0 if why else 1)
        if why is None:
            self.used_on.add(day)
            self.owed -= use
            self.in_owner += use
            self.in_prepaid -= use

    @invariant()
    def the_books_are_the_models(self):
        assert rent.owed(self.conn, self.lease) == self.owed
        assert balance(self.conn, self.owner) == self.in_owner
        assert balance(self.conn, self.prepaid) == self.in_prepaid
        assert set(bank_sums(self.conn, self.pmc_id).values()) == {Decimal(0)}


def test_prepaid_rent_used_by_itself_follows_the_setting_holds_and_days(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: PrepaidMachine(conn))
