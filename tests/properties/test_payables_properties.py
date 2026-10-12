"""Property-based tests for staff's payables and owners' money (src/pms/api/payables.py).

Hypothesis enters bills against an owner's property account (some dated ahead), sets them
aside and pays them, records owner contributions and draws (some sent again), in random order.
A model predicts what the owner's account holds, what its unpaid bills are and what a draw may
take; the database and the owners page must agree after every step.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - the owners page shows as available exactly what a draw of everything takes, and a draw of
    more is refused with nothing written;
  - its unpaid bills are the database's own (trust_unpaid_bills), which a draw keeps back;
  - a bill is set aside only from money the owner holds, and paid once it was;
  - sending a draw again pays nothing twice;
  - every trust bank account ties out.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from fastapi import HTTPException
from helpers import add_agreement, balance, bank_sums, make_pmc
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from pms.accounts import Session
from pms.api import payables

TODAY = datetime.now(UTC).date()
CENTS = st.integers(min_value=1, max_value=300_000).map(lambda c: Decimal(c) / 100)
RESERVE = Decimal("100.00")


@dataclass
class Bill:
    id: uuid.UUID
    amount: Decimal
    dated_ahead: bool
    aside: bool = False
    paid: bool = False


class PayablesMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        pmc = make_pmc(conn)
        self.session = Session(uuid.uuid4(), pmc.pmc_id, "staff", uuid.uuid4(), "s@example.com")
        self.account = pmc.owners[0].account
        self.vendor = pmc.vendor_id
        add_agreement(
            conn,
            pmc.pmc_id,
            self.account,
            TODAY - timedelta(days=30),
            reserve=str(RESERVE),
            approval_limit="10000000",
        )
        # Model
        self.held = Decimal(0)
        self.bills: list[Bill] = []
        self.draws: list[tuple[uuid.UUID, Decimal | None, Decimal]] = []

    def unpaid(self):
        return sum((b.amount for b in self.bills if not b.aside and not b.dated_ahead), Decimal(0))

    def available(self):
        return max(self.held - RESERVE - self.unpaid(), Decimal(0))

    def call(self, endpoint, *args):
        with self.conn.transaction():
            return endpoint(self.session, self.conn, *args)

    def refused(self, match, endpoint, *args):
        with pytest.raises(HTTPException, match=match) as raised:
            self.call(endpoint, *args)
        assert raised.value.status_code == 409

    # --- money in, bills -------------------------------------------------------------------

    @rule(amount=CENTS)
    def contribute(self, amount):
        new = payables.Contribution(
            request_id=uuid.uuid4(), account=self.account, amount=amount, on=TODAY
        )
        self.call(payables.contribute, new)
        self.held += amount

    @rule(amount=CENTS, ahead=st.booleans())
    def enter_a_bill(self, amount, ahead):
        dated = TODAY + timedelta(days=3) if ahead else TODAY - timedelta(days=1)
        new = payables.NewBill(
            vendor_id=self.vendor,
            account=self.account,
            reference=f"INV-{len(self.bills)}",
            bill_date=dated,
            due_on=dated + timedelta(days=10),
            amount=amount,
            memo="Repair",
        )
        created = self.call(payables.enter_bill, new)
        self.bills.append(Bill(created.id, amount, ahead))

    @precondition(lambda self: any(not b.aside and not b.dated_ahead for b in self.bills))
    @rule(data=st.data())
    def set_aside(self, data):
        bill = data.draw(
            st.sampled_from([b for b in self.bills if not b.aside and not b.dated_ahead])
        )
        step = payables.Day(on=TODAY)
        if bill.amount > self.held:
            event("set aside: refused, not enough money")
            self.refused("below zero", payables.set_aside, bill.id, step)
            return
        event("set aside")
        self.call(payables.set_aside, bill.id, step)
        bill.aside = True
        self.held -= bill.amount

    @precondition(lambda self: any(b.aside and not b.paid for b in self.bills))
    @rule(data=st.data())
    def pay(self, data):
        bill = data.draw(st.sampled_from([b for b in self.bills if b.aside and not b.paid]))
        self.call(payables.pay, bill.id, payables.Day(on=TODAY))
        bill.paid = True

    # --- draws ------------------------------------------------------------------------------

    @rule(amount=st.none() | CENTS)
    def draw(self, amount):
        request = uuid.uuid4()
        new = payables.Draw(request_id=request, account=self.account, amount=amount, on=TODAY)
        available = self.available()
        if available == 0 or (amount is not None and amount > available):
            event("draw: refused, more than is available")
            self.refused("available to draw", payables.draw, new)
            return
        event("draw: everything available" if amount is None else "draw: an amount")
        taken = Decimal(self.call(payables.draw, new).amount)
        assert taken == (available if amount is None else amount)
        self.held -= taken
        self.draws.append((request, amount, taken))

    @precondition(lambda self: self.draws)
    @rule(data=st.data())
    def send_a_draw_again(self, data):
        request, amount, taken = data.draw(st.sampled_from(self.draws))
        new = payables.Draw(request_id=request, account=self.account, amount=amount, on=TODAY)
        assert Decimal(self.call(payables.draw, new).amount) == taken

    # --- invariants -------------------------------------------------------------------------

    @invariant()
    def the_owners_page_is_the_model_and_the_database(self):
        (row,) = [r for r in self.call(payables.owners) if r.account == self.account]
        (unpaid,) = self.conn.execute(
            "SELECT trust_unpaid_bills(%s, %s)", (self.account, TODAY)
        ).fetchone()
        assert Decimal(row.balance) == self.held == balance(self.conn, self.account)
        assert Decimal(row.unpaid_bills) == self.unpaid() == unpaid
        assert Decimal(row.available) == self.available()
        assert Decimal(row.reserve) == RESERVE
        assert set(bank_sums(self.conn, self.session.pmc_id).values()) == {Decimal(0)}

    @invariant()
    def bill_statuses_follow_their_steps(self):
        listed = {b.id: b.status for b in self.call(payables.bills, "all")}
        for bill in self.bills:
            assert listed[bill.id] == (
                "paid" if bill.paid else "set aside" if bill.aside else "to set aside"
            )


def test_payables_invariants_hold_under_random_bills_contributions_and_draws(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: PayablesMachine(conn))
