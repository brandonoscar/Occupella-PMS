"""Property-based tests for shared bills and markups (migration 20261018000083).

Hypothesis gives two owners agreements with some markup percent (maybe none), puts money in,
enters bills with or without the markup, shares invoices between the two owners in random
shares, sets bills aside, pays them, declines some and draws everything an owner may take. A
model keeps each owner's balance, each bill's state and the company's fees. After every step:
  - each owner's balance, the vendor's account and the company's fee account are the model's: a
    bill is set aside exactly when its owner holds the bill and its markup, and the markup moves
    to the company with it, never on its own;
  - a markup is the agreement's percent of its bill to the cent, and only where it sets one;
  - what draws hold back for each owner (trust_unpaid_bills) is every bill not set aside or
    declined, with its markup, and a draw of everything leaves exactly that;
  - a shared invoice's parts come to it, each in its own owner's account;
  - the trust bank account ties out.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

import psycopg
import pytest
from helpers import (
    add_agreement,
    balance,
    bank_sums,
    contribute,
    decline_bill,
    draw_owner,
    enter_bill,
    make_pmc,
    pay_bill,
    set_aside_bill,
)
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

MONEY = st.integers(min_value=1, max_value=60_000).map(lambda c: Decimal(c) / 100)
# A markup percent of up to 25.0, to a tenth, or none.
PERCENT = st.one_of(
    st.just(Decimal(0)), st.integers(min_value=1, max_value=250).map(lambda t: Decimal(t) / 10)
)
DAY = date(2026, 10, 1)
CENT = Decimal("0.01")


def at(day):
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


@dataclass
class Bill:
    owner: int
    amount: Decimal
    markup: Decimal  # 0: none
    state: str = "unpaid"  # unpaid, set aside, paid, declined


class MarkupMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn

    @initialize(first=PERCENT, second=PERCENT)
    def a_company_with_two_owners(self, first, second):
        self.pmc = make_pmc(self.conn)
        self.percents = [first, second]
        for owner, percent in zip(self.pmc.owners, self.percents, strict=True):
            add_agreement(
                self.conn,
                self.pmc.pmc_id,
                owner.account,
                date(2026, 1, 1),
                approval_limit="1000000",
                bill_markup_percent=str(percent),
            )
        # Model
        self.balances = [Decimal("0.00"), Decimal("0.00")]
        self.bills: dict = {}
        self.fees = Decimal("0.00")
        self.n = 0

    def account(self, owner):
        return self.pmc.owners[owner].account

    def markup_of(self, owner, amount):
        return (amount * self.percents[owner] / 100).quantize(CENT, rounding=ROUND_HALF_UP)

    def bill_in(self, owner, amount, reference, marked):
        bill = enter_bill(
            self.conn,
            self.pmc.pmc_id,
            self.pmc.vendor_id,
            self.account(owner),
            reference,
            DAY,
            DAY,
            amount,
        )
        markup = Decimal(0)
        if marked:
            expected = self.markup_of(owner, amount)
            insert = (
                "INSERT INTO trust_bill_markups (bill_id, pmc_id) VALUES (%s, %s) RETURNING amount",
                (bill, self.pmc.pmc_id),
            )
            if expected > 0:
                (markup,) = self.conn.execute(*insert).fetchone()
                assert markup == expected
                event("markup added")
            else:
                # In a savepoint: a shared invoice's parts go on in the same transaction.
                with pytest.raises(psycopg.errors.CheckViolation), self.conn.transaction():
                    self.conn.execute(*insert)
                event("markup refused: the agreement sets none, or it comes to nothing")
        self.bills[bill] = Bill(owner, amount, markup)
        return bill

    @rule(owner=st.integers(0, 1), amount=MONEY)
    def an_owner_puts_money_in(self, owner, amount):
        self.n += 1
        contribute(self.conn, self.pmc.pmc_id, self.account(owner), f"in-{self.n}", amount, at(DAY))
        self.balances[owner] += amount

    @rule(owner=st.integers(0, 1), amount=MONEY, marked=st.booleans())
    def staff_enter_a_bill(self, owner, amount, marked):
        self.n += 1
        self.bill_in(owner, amount, f"B-{self.n}", marked)

    @rule(first=MONEY, second=MONEY, marked=st.booleans())
    def staff_share_an_invoice(self, first, second, marked):
        self.n += 1
        reference = f"S-{self.n}"
        with self.conn.transaction():
            shared = self.conn.execute(
                "INSERT INTO trust_shared_bills (pmc_id, vendor_id, reference, amount)"
                " VALUES (%s, %s, %s, %s) RETURNING id",
                (self.pmc.pmc_id, self.pmc.vendor_id, reference, first + second),
            ).fetchone()[0]
            for part, (owner, amount) in enumerate(((0, first), (1, second)), 1):
                bill = self.bill_in(owner, amount, f"{reference}/{part}", marked)
                self.conn.execute(
                    "INSERT INTO trust_shared_bill_parts (shared_bill_id, part, pmc_id, bill_id)"
                    " VALUES (%s, %s, %s, %s)",
                    (shared, part, self.pmc.pmc_id, bill),
                )
        event("invoice shared")

    @precondition(lambda self: any(b.state == "unpaid" for b in self.bills.values()))
    @rule(data=st.data())
    def staff_set_a_bill_aside(self, data):
        bill = data.draw(st.sampled_from([k for k, b in self.bills.items() if b.state == "unpaid"]))
        b = self.bills[bill]
        if b.amount + b.markup > self.balances[b.owner]:
            with pytest.raises(psycopg.errors.CheckViolation):
                set_aside_bill(self.conn, self.pmc.pmc_id, bill, at(DAY))
            event("owner short of the bill and its markup: nothing set aside")
            return
        set_aside_bill(self.conn, self.pmc.pmc_id, bill, at(DAY))
        b.state = "set aside"
        self.balances[b.owner] -= b.amount + b.markup
        self.fees += b.markup
        event("set aside with a markup" if b.markup else "set aside")

    @precondition(lambda self: any(b.state == "set aside" for b in self.bills.values()))
    @rule(data=st.data())
    def staff_pay_a_bill(self, data):
        bill = data.draw(
            st.sampled_from([k for k, b in self.bills.items() if b.state == "set aside"])
        )
        pay_bill(self.conn, self.pmc.pmc_id, bill, at(DAY))
        self.bills[bill].state = "paid"

    @precondition(lambda self: any(b.state == "unpaid" for b in self.bills.values()))
    @rule(data=st.data())
    def the_owner_declines_a_bill(self, data):
        bill = data.draw(st.sampled_from([k for k, b in self.bills.items() if b.state == "unpaid"]))
        decline_bill(self.conn, self.pmc.pmc_id, bill, DAY, "Owner")
        self.bills[bill].state = "declined"

    @rule(owner=st.integers(0, 1))
    def the_owner_draws_everything(self, owner):
        available = self.balances[owner] - self.held(owner)
        self.n += 1
        if available <= 0:
            with pytest.raises(psycopg.errors.CheckViolation):
                draw_owner(
                    self.conn, self.pmc.pmc_id, self.account(owner), f"d-{self.n}", None, at(DAY)
                )
            event("nothing to draw")
            return
        drawn = draw_owner(
            self.conn, self.pmc.pmc_id, self.account(owner), f"d-{self.n}", None, at(DAY)
        )
        assert drawn == available
        self.balances[owner] -= drawn
        event("drew what bills and markups leave")

    def held(self, owner):
        return sum(
            (
                b.amount + b.markup
                for b in self.bills.values()
                if b.owner == owner and b.state == "unpaid"
            ),
            Decimal(0),
        )

    @invariant()
    def the_books_are_the_model(self):
        for owner in (0, 1):
            assert balance(self.conn, self.account(owner)) == self.balances[owner] >= 0
            (held,) = self.conn.execute(
                "SELECT trust_unpaid_bills(%s, %s)", (self.account(owner), DAY)
            ).fetchone()
            assert held == self.held(owner)
        assert balance(self.conn, self.pmc.pmc_income) == self.fees
        assert balance(self.conn, self.pmc.vendor_payable) == sum(
            (b.amount for b in self.bills.values() if b.state == "set aside"), Decimal(0)
        )
        assert all(total == 0 for total in bank_sums(self.conn, self.pmc.pmc_id).values())
        # Each markup taken is its bill's, once.
        assert self.conn.execute(
            "SELECT coalesce(sum(m.amount), 0) FROM trust_bill_markup_fees AS f"
            " JOIN trust_bill_markups AS m ON m.bill_id = f.bill_id WHERE f.pmc_id = %s",
            (self.pmc.pmc_id,),
        ).fetchone() == (self.fees,)


def test_markups_and_shared_bills_follow_the_rules_and_tie_out(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: MarkupMachine(conn))
