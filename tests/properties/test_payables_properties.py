"""Property-based tests for staff's payables and owners' money (src/pms/api/payables.py).

Hypothesis enters bills against an owner's property account (some dated ahead), pays them
whole or in parts (some sent again), voids them, records owner contributions and draws (some
sent again), takes management fees and takes the company's fees out of trust (some sent again),
in random order. A model predicts what the owner's account holds, what each bill holds of it
(ADR 0010), what its unpaid bills are, what a draw may take and what the company's fee account
holds; the database and the pages must agree after every step.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - the owners page shows as available exactly what a draw of everything takes, and a draw of
    more is refused with nothing written;
  - its unpaid bills are the database's own (trust_unpaid_bills), which a draw keeps back;
  - each bill's status, what it holds, what it is short and what the owner must send are the
    model's: the balance laid over the bills not paid yet in the order they are due; holds never
    come to more than the balance, a void bill holds nothing, and a draw never pays held money
    (every hold is the same after it);
  - a bill's first payment takes it whole from the owner, each pays at most what is left, a
    short bill pays nothing, and sent again a payment records nothing new;
  - a void is refused once money was taken for the bill, and a void bill is never paid;
  - Pay bills pays what is left of every bill it names, or nothing at all when one of them is
    short (the refusal naming that bill and what the owner must send); sent again it pays
    nothing twice, and each payment keeps how it was sent;
  - sending a draw again pays nothing twice;
  - Pay owners pays every account it names what it asks, or nothing at all when one of them
    can't pay (the refusal naming that owner and property); sent again it pays nothing twice,
    and each payout keeps how it was sent;
  - the management fees page says what Collect fees would take, moving nothing, and then that
    it was taken;
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
from helpers import add_agreement, add_staff, balance, bank_sums, give_roles, make_pmc
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
    index: int
    amount: Decimal
    dated_ahead: bool
    taken: bool = False  # its money left the owner's account (its first payment)
    paid: Decimal = Decimal(0)
    void: bool = False

    @property
    def reference(self):
        return f"INV-{self.index}"

    @property
    def left(self):
        return self.amount - self.paid

    @property
    def open(self):
        return not self.void and self.paid < self.amount


class PayablesMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        # Nobody named until this PMC's staff member is: the last example named another PMC's.
        conn.execute("SELECT set_config('trust.staff_id', '', false)")
        pmc = make_pmc(conn)
        # A staff member who may pay, named on every write as the API names whoever is signed in
        # (a void names who voided it).
        staff = add_staff(conn, pmc.pmc_id)
        give_roles(conn, pmc.pmc_id, staff, ["admin", "approve", "disburse"])
        conn.execute("SELECT set_config('trust.staff_id', %s, false)", (str(staff),))
        self.session = Session(uuid.uuid4(), pmc.pmc_id, "staff", staff, "s@example.com")
        self.account = pmc.owners[0].account
        # A second owner's account that never holds anything: Pay owners can't pay from it.
        self.empty = pmc.owners[1].account
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
        self.payouts: list[tuple[payables.PayOwners, Decimal]] = []

    def waiting(self):
        """The bills dated by today whose money is still the owner's: open and not yet taken."""
        return [b for b in self.bills if b.open and not b.taken and not b.dated_ahead]

    def unpaid(self):
        return sum((b.amount for b in self.waiting()), Decimal(0))

    def holds(self):
        """What each waiting bill holds of the owner's balance, what it is short and what the
        owner must send for it and those before it: the balance laid over them in the order they
        are due (all due the same day here, so by reference)."""
        laid = {}
        held = max(self.held, Decimal(0))
        through = Decimal(0)
        for bill in sorted(self.waiting(), key=lambda b: b.reference):
            through += bill.amount
            holds = min(bill.amount, max(held - (through - bill.amount), Decimal(0)))
            laid[bill.id] = (holds, bill.amount - holds, max(through - held, Decimal(0)))
        return laid

    def status(self, bill):
        if bill.void:
            return "void"
        if bill.paid >= bill.amount:
            return "paid"
        if bill.paid > 0:
            return "partly paid"
        if bill.taken:
            return "ready to pay"
        if bill.dated_ahead:
            return "entered"
        return "short" if self.holds()[bill.id][1] > 0 else "ready to pay"

    def database_holds(self):
        return {
            bill: (held, short, needed)
            for bill, held, short, needed in self.conn.execute(
                "SELECT bill_id, held, short, needed FROM trust_bill_holds(%s)",
                (self.session.pmc_id,),
            ).fetchall()
        }

    def short_said(self, bill):
        _, short, needed = self.holds()[bill.id]
        return (
            f"Owner 1's balance is ${short:,.2f} short of Vendor 1 {bill.reference}: they need to"
            f" send ${needed:,.2f} first"
        )

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
        self.bills.append(Bill(created.id, len(self.bills), amount, ahead))

    def take(self, bill, amount):
        """The model of a payment: the first takes the whole bill from the owner."""
        if not bill.taken:
            self.held -= bill.amount
            bill.taken = True
        bill.paid += amount

    @precondition(lambda self: any(b.open and not b.dated_ahead for b in self.bills))
    @rule(data=st.data(), part=st.none() | CENTS)
    def pay(self, data, part):
        """A payment of what is left of a bill, or a part of it, sent twice."""
        bill = data.draw(st.sampled_from([b for b in self.bills if b.open and not b.dated_ahead]))
        step = payables.Paying(on=TODAY, amount=part, request_id=uuid.uuid4())
        if self.status(bill) == "short":
            event("pay: refused, short")
            self.refused(self.short_said(bill), payables.pay, bill.id, step)
            return
        amount = bill.left if part is None else part
        if amount > bill.left:
            event("pay: refused, more than is left")
            said = (
                f"${bill.left:,.2f} is left to pay of Vendor 1 {bill.reference};"
                f" ${amount:,.2f} is more than that"
            )
            self.refused(said, payables.pay, bill.id, step)
            return
        event("pay: all that is left" if amount == bill.left else "pay: a part")
        moved = self.call(payables.pay, bill.id, step)
        assert self.call(payables.pay, bill.id, step) == moved  # sent again: nothing new
        self.take(bill, amount)

    @precondition(lambda self: self.bills)
    @rule(data=st.data())
    def void(self, data):
        bill = data.draw(st.sampled_from(self.bills))
        why = payables.Void(reason="Entered twice")
        if bill.void:
            self.refused(f"bill {bill.reference} is void already", payables.void, bill.id, why)
        elif bill.taken:
            event("void: refused, money taken")
            said = (
                f"money was taken from the owner's account for bill {bill.reference} already;"
                " it can't be voided"
            )
            self.refused(said, payables.void, bill.id, why)
        else:
            event("void")
            self.call(payables.void, bill.id, why)
            bill.void = True

    @precondition(lambda self: any(b.open and not b.dated_ahead for b in self.bills))
    @rule(data=st.data(), method=st.sampled_from(["check", "EFT"]))
    def pay_bills(self, data, method):
        """Pay bills: what is left of each bill chosen, all of them or none."""
        chosen = data.draw(
            st.lists(
                st.sampled_from([b for b in self.bills if b.open and not b.dated_ahead]),
                min_size=1,
                max_size=3,
                unique_by=lambda b: b.id,
            )
        )
        run = payables.PayBills(on=TODAY, method=method, bill_ids=[b.id for b in chosen])
        short = [b for b in chosen if self.status(b) == "short"]
        if short:
            # Paying a ready bill never makes another short, so the first short one is refused.
            event("pay bills: refused, one is short")
            said = self.short_said(short[0])
            self.refused(said, payables.pay_bills, run)
            return
        event(f"pay bills: {len(chosen)} paid")
        paid = self.call(payables.pay_bills, run)
        assert self.call(payables.pay_bills, run) == paid  # sent again: nothing twice
        assert [(p.bill_id, Decimal(p.amount)) for p in paid] == [(b.id, b.left) for b in chosen]
        methods = self.conn.execute(
            "SELECT method FROM trust_payout_methods WHERE transfer_id = ANY(%s)",
            ([p.transfer_id for p in paid],),
        ).fetchall()
        assert methods == [(method,)] * len(chosen)
        for bill in chosen:
            self.take(bill, bill.left)

    # --- draws ------------------------------------------------------------------------------

    def paying(self, request, amount):
        """Pay owners, this owner's account alone: everything available when amount is None."""
        return payables.PayOwners(
            request_id=request,
            on=TODAY,
            method="check",
            payouts=[payables.Payout(account=self.account, amount=amount)],
        )

    @rule(amount=st.none() | CENTS)
    def draw(self, amount):
        request = uuid.uuid4()
        new = self.paying(request, amount)
        available = self.available()
        if available == 0 or (amount is not None and amount > available):
            event("draw: refused, more than is available")
            held = f"the ${RESERVE:,.2f} reserve"
            if self.unpaid() > 0:
                held += f" and ${self.unpaid():,.2f} of unpaid bills"
            said = "Owner 1, Property 1: " + (
                f"nothing is available to draw: what the account holds is kept for {held}"
                if available == 0
                else f"only ${available:,.2f} is available to draw (the balance less {held});"
                f" ${amount:,.2f} is more than that"
            )
            self.refused(said, payables.pay_owners, new)
            return
        event("draw: everything available" if amount is None else "draw: an amount")
        before = self.database_holds()
        (paid,) = self.call(payables.pay_owners, new)
        # A draw never pays held money: every bill holds what it held.
        assert self.database_holds() == before
        taken = Decimal(paid.amount)
        assert taken == (available if amount is None else amount)
        self.held -= taken
        self.draws.append((request, amount, taken))

    @precondition(lambda self: self.draws)
    @rule(data=st.data())
    def send_a_draw_again(self, data):
        request, amount, taken = data.draw(st.sampled_from(self.draws))
        (paid,) = self.call(payables.pay_owners, self.paying(request, amount))
        assert Decimal(paid.amount) == taken

    @rule(amount=CENTS, method=st.sampled_from(["check", "EFT"]), with_empty=st.booleans())
    def pay_owners(self, amount, method, with_empty):
        payouts = [payables.Payout(account=self.account, amount=amount)]
        if with_empty:
            payouts.append(payables.Payout(account=self.empty, amount=Decimal("0.01")))
        run = payables.PayOwners(request_id=uuid.uuid4(), on=TODAY, method=method, payouts=payouts)
        if amount > self.available() or with_empty:
            event("pay owners: refused, all of it")
            with pytest.raises(HTTPException) as raised:
                self.call(payables.pay_owners, run)
            assert raised.value.status_code == 409
            assert re.match(r"^[^:]+, [^:]+: (only|nothing)", raised.value.detail)
            return
        event("pay owners")
        (paid,) = self.call(payables.pay_owners, run)
        assert Decimal(paid.amount) == amount
        self.held -= amount
        self.payouts.append((run, amount))
        assert self.conn.execute(
            "SELECT method FROM trust_payout_methods WHERE transfer_id = %s", (paid.transfer_id,)
        ).fetchone() == (method,)

    @precondition(lambda self: self.payouts)
    @rule(data=st.data(), method=st.sampled_from(["check", "EFT"]))
    def send_pay_owners_again(self, data, method):
        run, amount = data.draw(st.sampled_from(self.payouts))
        (paid,) = self.call(payables.pay_owners, run.model_copy(update={"method": method}))
        assert Decimal(paid.amount) == amount
        # How it was sent stays as first recorded.
        assert self.conn.execute(
            "SELECT method FROM trust_payout_methods WHERE transfer_id = %s", (paid.transfer_id,)
        ).fetchone() == (run.method,)

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
        # What the page says Collect fees would take, which moves nothing.
        (due,) = [
            d
            for d in self.call(payables.management_fees_due, run.period_start, run.period_end)
            if d.account == self.account
        ]
        assert balance(self.conn, self.account) == self.held
        (fee,) = self.call(payables.management_fees, run)
        assert (due.taken, due.fee, due.refused) == (False, fee.fee, fee.refused)
        (after,) = [
            d
            for d in self.call(payables.management_fees_due, run.period_start, run.period_end)
            if d.account == self.account
        ]
        assert after.taken == (fee.fee is not None)
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

    def teardown(self):
        self.conn.execute("SELECT set_config('trust.staff_id', '', false)")

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
    def bills_hold_what_the_model_holds(self):
        with self.conn.transaction():
            listed = {b.id: b for b in payables.find_bills(self.conn, self.session.pmc_id)[0]}
        holds = self.holds()
        assert self.database_holds() == holds
        for bill in self.bills:
            shown = listed[bill.id]
            assert shown.status == self.status(bill)
            assert (Decimal(shown.paid), Decimal(shown.left)) == (
                bill.paid,
                Decimal(0) if bill.void else bill.left,
            )
            held, short, needed = holds.get(bill.id, (None, None, None))
            if bill.taken and bill.open:
                held = bill.left  # its money left the owner's account: what is left is held
            assert shown.held == (None if held is None else f"{held:.2f}")
            assert shown.short == (f"{short:.2f}" if short else None)
            assert shown.needed == (f"{needed:.2f}" if needed else None)
        # Holds never come to more than the balance, and a void bill holds nothing.
        assert sum((h for h, _, _ in holds.values()), Decimal(0)) <= max(self.held, Decimal(0))
        assert not any(b.void and b.id in holds for b in self.bills)


def test_payables_invariants_hold_under_random_bills_contributions_and_draws(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: PayablesMachine(conn))
