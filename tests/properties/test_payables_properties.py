"""Property-based tests for staff's payables and owners' money (src/pms/api/payables.py).

Hypothesis enters bills against an owner's property account (some dated ahead), sets them
aside and pays them, records owner contributions and draws (some sent again), takes management
fees and takes the company's fees out of trust (some sent again), in random order. A model
predicts what the owner's account holds, what its unpaid bills are, what a draw may take and
what the company's fee account holds; the database and the pages must agree after every step.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - the owners page shows as available exactly what a draw of everything takes, and a draw of
    more is refused with nothing written;
  - its unpaid bills are the database's own (trust_unpaid_bills), which a draw keeps back;
  - a bill is set aside only from money the owner holds, and paid once it was;
  - sending a draw again pays nothing twice;
  - the company takes out at most the fees its fee account holds, never an owner's money, and
    sending a withdrawal again takes nothing twice;
  - every trust bank account ties out.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import re
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
FEE = Decimal("25.00")  # each period's management fee: the agreement's minimum


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
            minimum_fee=str(FEE),
            approval_limit="10000000",
        )
        self.bank = pmc.operating_bank_id
        self.pmc_income = pmc.pmc_income
        # Model
        self.held = Decimal(0)
        self.bills: list[Bill] = []
        self.draws: list[tuple[uuid.UUID, Decimal | None, Decimal]] = []
        self.periods = 0
        self.company = Decimal(0)  # the company's fees in the trust bank account
        self.withdrawals: list[tuple[uuid.UUID, Decimal | None, Decimal]] = []

    def unpaid(self):
        return sum((b.amount for b in self.bills if not b.aside and not b.dated_ahead), Decimal(0))

    def available(self):
        return max(self.held - RESERVE - self.unpaid(), Decimal(0))

    def call(self, endpoint, *args):
        with self.conn.transaction():
            return endpoint(self.session, self.conn, *args)

    def refused(self, said, endpoint, *args):
        """Refused with 409, saying exactly what the model says, in words."""
        with pytest.raises(HTTPException, match=f"^409: {re.escape(said)}$") as raised:
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
            short = bill.amount - self.held
            said = f"not enough money: the owner's account would be ${short:,.2f} short"
            self.refused(said, payables.set_aside, bill.id, step)
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
            held = f"the ${RESERVE:,.2f} reserve"
            if self.unpaid() > 0:
                held += f" and ${self.unpaid():,.2f} of unpaid bills"
            said = (
                f"nothing is available to draw: what the account holds is kept for {held}"
                if available == 0
                else f"only ${available:,.2f} is available to draw (the balance less {held});"
                f" ${amount:,.2f} is more than that"
            )
            self.refused(said, payables.draw, new)
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

    # --- the company's fees ------------------------------------------------------------------

    @precondition(lambda self: self.periods < 25)
    @rule()
    def take_a_management_fee(self):
        # One day's period at a time, each after the agreement starts and none overlapping.
        start = TODAY - timedelta(days=29 - self.periods)
        self.periods += 1
        run = payables.FeeRun(
            period_start=start, period_end=start + timedelta(days=1), on=TODAY, account=self.account
        )
        (fee,) = self.call(payables.management_fees, run)
        if self.held < FEE:
            event("management fee: refused, not enough money")
            assert (fee.fee, fee.refused) == (
                None,
                f"not enough money: the owner's account would be ${FEE - self.held:,.2f} short",
            )
            return
        event("management fee")
        assert Decimal(fee.fee) == FEE
        self.held -= FEE
        self.company += FEE

    @rule(amount=st.none() | CENTS)
    def take_the_companys_fees_out(self, amount):
        request = uuid.uuid4()
        new = payables.FeeWithdrawal(
            request_id=request, bank_account_id=self.bank, amount=amount, on=TODAY
        )
        if self.company == 0 or (amount is not None and amount > self.company):
            event("company fees: refused, more than is there")
            said = (
                "none of the company's fees is in this trust bank account to take"
                if self.company == 0
                else f"only ${self.company:,.2f} of the company's fees is in this trust bank"
                f" account; ${amount:,.2f} is more than that"
            )
            self.refused(said, payables.take_company_fees, new)
            return
        event("company fees: everything" if amount is None else "company fees: an amount")
        taken = Decimal(self.call(payables.take_company_fees, new).amount)
        assert taken == (self.company if amount is None else amount)
        self.company -= taken
        self.withdrawals.append((request, amount, taken))

    @precondition(lambda self: self.withdrawals)
    @rule(data=st.data())
    def send_a_withdrawal_again(self, data):
        request, amount, taken = data.draw(st.sampled_from(self.withdrawals))
        new = payables.FeeWithdrawal(
            request_id=request, bank_account_id=self.bank, amount=amount, on=TODAY
        )
        assert Decimal(self.call(payables.take_company_fees, new).amount) == taken

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
    def the_companys_fees_are_the_model_and_the_database(self):
        (fees,) = [
            f for f in self.call(payables.company_fees, None) if f.bank_account_id == self.bank
        ]
        assert Decimal(fees.held) == self.company == balance(self.conn, self.pmc_income)
        assert (fees.since is None) == (self.company == 0)

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
