"""Property-based test of money held for no one yet and deposits owners hold (migrations
20261106000240 and 20261106000241; src/pms/api/unapplied.py, src/pms/api/owner_held.py).

Hypothesis charges a lease rent, records tenants' payments, receives unapplied cash, applies it
(all or part) to the lease and refunds it (all or part), records the owner's draws, and records
deposits the owner holds outside trust and what the owner returned of them, in any order, through
the API's own routes and write paths.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - in each trust bank account, the cash the bank holds (the model's: every receipt and payment
    in, every refund and draw out) is the book cash, which is the sum of every other ledger
    account in it, the unapplied cash account included: every trust bank account ties out;
  - the unapplied cash account holds what is left of the receipts, never below zero: each
    receipt less what of it was applied or refunded;
  - a deposit the owner holds is never in trust: the security-deposit account's cash and
    deposits stay as they were whatever is recorded, and recording or settling one moves no money.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
from fastapi import HTTPException
from helpers import add_tenant, add_unit, bank_sums, draw_owner, make_pmc, open_lease, staff_named
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    rule,
    run_state_machine_as_test,
)

from pms import demo
from pms.api import owner_held, rent, unapplied

DAY = timedelta(days=1)


def on_this_day() -> None:
    """Today's dates (UTC), worked out again before each test (tests/conftest.py,
    pytest_runtest_setup), so a run past midnight compares them with the database's."""
    global TODAY, START
    TODAY = datetime.now(UTC).date()
    START = TODAY - 400 * DAY


on_this_day()
CENTS = st.integers(min_value=1, max_value=200_000).map(lambda c: Decimal(c) / 100)
SHARE = st.none() | st.integers(min_value=1, max_value=100)
ZERO = Decimal(0)


def part_of(left: Decimal, percent: int | None) -> Decimal | None:
    """None (all that is left), or a percent of it, at least a cent."""
    if percent is None:
        return None
    return max((left * percent / 100).quantize(Decimal("0.01")), Decimal("0.01"))


class HeldMoneyMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        pmc = make_pmc(conn, owners=1)
        self.pmc = pmc
        prop = pmc.owners[0].property_id
        self.tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 1")
        unit = add_unit(conn, pmc.pmc_id, prop, "Unit 1")
        # A last day set, far ahead: what the owner did with a deposit is recorded at move-out.
        self.lease = open_lease(
            conn, pmc.pmc_id, unit, START, TODAY + 400 * DAY, "1500.00", [self.tenant]
        )
        self.payer = staff_named(conn, pmc.pmc_id, "Payer 1", ["disburse"])
        self.staff = demo.Builder(conn, pmc.pmc_id)
        self.rent_days: set = set()
        # The model: the operating bank's cash, the receipts (id: what is left), the deposits
        # the owner holds (id: amount, settled), and the deposit bank's books as they started.
        self.cash = ZERO
        self.receipts: dict[str, Decimal] = {}
        self.held: dict[str, bool] = {}
        self.deposit_books = self.balances_of(pmc.deposit_bank_id)

    # --- the books ---------------------------------------------------------------------------

    def balances_of(self, bank) -> dict[str, Decimal]:
        return dict(
            self.conn.execute(
                "SELECT t.ledger_account_id, a.balance FROM trust_ledger_accounts AS t"
                " JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id"
                " WHERE t.bank_account_id = %s",
                (bank,),
            ).fetchall()
        )

    def transfers(self) -> int:
        return self.conn.execute(
            "SELECT count(*) FROM pgledger_transfers AS t"
            " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.from_account_id"
            " WHERE a.pmc_id = %s",
            (self.pmc.pmc_id,),
        ).fetchone()[0]

    # --- rules -------------------------------------------------------------------------------

    @rule(back=st.integers(0, 300))
    def charge_rent(self, back):
        due = TODAY - back * DAY
        if due in self.rent_days:
            return  # rent is charged once a due date
        self.rent_days.add(due)
        self.conn.execute(
            "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount)"
            " VALUES (%s, %s, %s, 'rent', 1500.00)",
            (self.pmc.pmc_id, self.lease, due),
        )

    @rule(amount=CENTS)
    def tenant_pays(self, amount):
        with self.conn.transaction():
            rent.record_payment(self.conn, self.pmc.pmc_id, self.lease, uuid.uuid4(), amount, TODAY)
        self.cash += amount

    @rule(amount=CENTS)
    def unapplied_cash_comes_in(self, amount):
        with self.staff.as_staff(self.payer) as session:
            made = unapplied.receive(
                session,
                self.conn,
                unapplied.NewUnapplied(
                    request_id=uuid.uuid4(),
                    amount=amount,
                    received_on=TODAY,
                    payer="J. Sample",
                    method="check",
                ),
            )
        self.receipts[str(made.id)] = amount
        self.cash += amount

    @rule(place=st.integers(0, 50), share=SHARE)
    def it_is_applied_to_the_lease(self, place, share):
        if not self.receipts:
            return
        receipt = sorted(self.receipts)[place % len(self.receipts)]
        left = self.receipts[receipt]
        amount = part_of(left, share)
        try:
            with self.staff.as_staff(self.payer) as session:
                unapplied.apply(
                    session,
                    self.conn,
                    uuid.UUID(receipt),
                    unapplied.Apply(request_id=uuid.uuid4(), lease_id=self.lease, amount=amount),
                )
        except HTTPException as refused:
            # Nothing left of it: none to apply, or a cent more than is left.
            assert left == 0 and refused.status_code in (409, 422), refused.detail
            event("nothing left to apply")
            return
        self.receipts[receipt] = left - (left if amount is None else amount)

    @rule(place=st.integers(0, 50), share=SHARE)
    def it_is_refunded(self, place, share):
        if not self.receipts:
            return
        receipt = sorted(self.receipts)[place % len(self.receipts)]
        left = self.receipts[receipt]
        amount = part_of(left, share)
        try:
            with self.staff.as_staff(self.payer) as session:
                done = unapplied.refund(
                    session,
                    self.conn,
                    uuid.UUID(receipt),
                    unapplied.Refund(
                        request_id=uuid.uuid4(), paid_to="J. Sample", method="check", amount=amount
                    ),
                )
        except HTTPException as refused:
            # Nothing left of it: none to refund, or a cent more than is left.
            assert left == 0 and refused.status_code == 409, refused.detail
            event("nothing left to refund")
            return
        assert Decimal(done.amount) == (left if amount is None else amount)
        self.receipts[receipt] = left - Decimal(done.amount)
        self.cash -= Decimal(done.amount)

    @rule(share=SHARE)
    def the_owner_draws(self, share):
        owner = self.pmc.owners[0].account
        balance = rent.balances(self.conn, [owner])[owner]
        if balance <= 0:
            return
        amount = part_of(balance, share)
        try:
            with self.conn.transaction():
                paid = draw_owner(
                    self.conn, self.pmc.pmc_id, owner, str(uuid.uuid4()), amount, datetime.now(UTC)
                )
        except psycopg.errors.CheckViolation:
            event("draw refused: over what is available")
            return
        self.cash -= Decimal(paid)

    @rule(amount=CENTS, kind=st.sampled_from(["security", "pet", "key"]))
    def the_owner_holds_a_deposit(self, amount, kind):
        before = self.transfers()
        try:
            with self.staff.as_staff(self.payer) as session:
                made = owner_held.record(
                    session,
                    self.conn,
                    self.lease,
                    owner_held.NewOwnerHeld(
                        tenant_id=self.tenant, kind=kind, amount=amount, held_since=TODAY
                    ),
                )
        except HTTPException as refused:
            assert refused.status_code == 409  # one of each kind per tenant
            return
        self.held[str(made.id)] = False
        assert self.transfers() == before

    @rule(place=st.integers(0, 50), kept=st.integers(0, 100))
    def the_owner_returns_it(self, place, kept):
        waiting = sorted(h for h, done in self.held.items() if not done)
        if not waiting:
            return
        held = waiting[place % len(waiting)]
        before = self.transfers()
        amount = Decimal(
            self.conn.execute(
                "SELECT amount FROM trust_owner_held_deposits WHERE id = %s", (held,)
            ).fetchone()[0]
        )
        keeps = (amount * kept / 100).quantize(Decimal("0.01"))
        with self.staff.as_staff(self.payer) as session:
            owner_held.settle(
                session,
                self.conn,
                self.lease,
                uuid.UUID(held),
                owner_held.Outcome(returned=amount - keeps, kept=keeps, on=TODAY),
            )
        self.held[held] = True
        assert self.transfers() == before

    # --- invariants --------------------------------------------------------------------------

    @invariant()
    def bank_cash_is_book_cash_is_everything_held(self):
        books = self.balances_of(self.pmc.operating_bank_id)
        book_cash = -books[self.pmc.operating_cash]
        held = sum((b for a, b in books.items() if a != self.pmc.operating_cash), ZERO)
        assert self.cash == book_cash == held
        assert set(bank_sums(self.conn, self.pmc.pmc_id).values()) == {ZERO}

    @invariant()
    def unapplied_cash_holds_what_is_left_of_the_receipts(self):
        found = self.conn.execute(
            "SELECT a.balance FROM trust_ledger_accounts AS t"
            " JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id"
            " WHERE t.bank_account_id = %s AND t.kind = 'unapplied_cash'",
            (self.pmc.operating_bank_id,),
        ).fetchone()
        held = ZERO if found is None else found[0]
        assert held == sum(self.receipts.values(), ZERO)
        assert all(left >= 0 for left in self.receipts.values())
        for receipt, left in self.receipts.items():
            assert (
                self.conn.execute("SELECT trust_unapplied_left(%s)", (receipt,)).fetchone()[0]
                == left
            )

    @invariant()
    def a_deposit_the_owner_holds_is_never_in_trust(self):
        assert self.balances_of(self.pmc.deposit_bank_id) == self.deposit_books
        recorded = self.conn.execute(
            "SELECT h.id::text, o.held_id IS NOT NULL FROM trust_owner_held_deposits AS h"
            " LEFT JOIN trust_owner_held_deposit_outcomes AS o ON o.held_id = h.id"
            " WHERE h.lease_id = %s",
            (self.lease,),
        ).fetchall()
        assert dict(recorded) == self.held


def test_held_money_ties_out_and_owner_held_deposits_are_never_in_trust(
    database_url,
):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: HeldMoneyMachine(conn))
