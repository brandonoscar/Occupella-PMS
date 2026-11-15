"""Property-based tests for the company covering an owner's shortfall when a payment bounces
(migration 20261115000100, docs/edge-cases.md M-060, GitHub issue #232).

Hypothesis charges a lease rent, records tenants' payments, pays the owner out (draws), takes fees
from the owner, bounces random payments with or without the company's cover, and takes random
repayments of what the owner owes back. A model keeps the owner's balance and what is owed. After
every step:
  - a bounce that would leave the owner's account short is refused without a cover, moving
    nothing, and with one records exactly the shortfall advanced; a bounce with nothing short
    advances nothing;
  - every advance equals the shortfall of its payment at its bounce, and the account held exactly
    zero after that bounce;
  - a repayment never exceeds what is owed (nor what the account holds): one asking more is
    refused and moves nothing;
  - no owner's account (nor any account but cash) is ever below zero, and every trust bank
    account ties out.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
from fastapi import HTTPException
from helpers import (
    acting_as,
    add_staff,
    add_unit,
    balance,
    bank_sums,
    charge,
    give_roles,
    make_pmc,
    open_lease,
)
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
    run_state_machine_as_test,
)

from pms.api import rent

DAY = timedelta(days=1)
ZERO = Decimal("0.00")
MONEY = st.integers(min_value=1, max_value=300_000).map(lambda c: Decimal(c) / 100)
PART = st.integers(min_value=1, max_value=100)  # a percent of what there is


def cents(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"))


class CompanyCoverMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn

    @initialize()
    def a_lease_and_a_manager(self):
        self.today = datetime.now(UTC).date()
        self.pmc = make_pmc(self.conn)
        pmc_id = self.pmc.pmc_id
        prop = self.pmc.owners[0].property_id
        tenant = self.conn.execute(
            "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
            (self.pmc.prepaid_rent,),
        ).fetchone()[0]
        unit = add_unit(self.conn, pmc_id, prop, "Unit P")
        self.lease = open_lease(
            self.conn, pmc_id, unit, self.today - 400 * DAY, None, "1500.00", [tenant]
        )
        self.owner = self.pmc.owners[0].account
        self.staff = add_staff(self.conn, pmc_id, "Manager P")
        give_roles(self.conn, pmc_id, self.staff, ["disburse"])
        self.rent_days: set = set()
        # Model
        self.balance = ZERO
        self.owed = ZERO
        self.payments: dict[str, Decimal] = {}  # not bounced: what each brought the owner
        self.n = 0

    def key(self) -> str:
        self.n += 1
        return f"p-{self.n}"

    @rule(amount=MONEY, back=st.integers(0, 300))
    def charge_rent(self, amount, back):
        due = self.today - back * DAY
        if due in self.rent_days:
            return
        self.rent_days.add(due)
        charge(self.conn, self.pmc.pmc_id, self.lease, due, amount)

    @rule(amount=MONEY)
    def the_tenant_pays(self, amount):
        with acting_as(self.conn, self.staff), self.conn.transaction():
            paid = rent.record_payment(
                self.conn, self.pmc.pmc_id, self.lease, uuid.uuid4(), amount, self.today
            )
        brought = sum(
            (
                Decimal(a)
                for (a,) in self.conn.execute(
                    "SELECT amount FROM pgledger_transfers WHERE id = ANY(%s)"
                    " AND to_account_id = %s",
                    (paid.transfers, self.owner),
                ).fetchall()
            ),
            ZERO,
        )
        self.payments[paid.transfers[0]] = brought
        self.balance += brought

    @rule(amount=MONEY)
    def the_owner_sends_money_in(self, amount):
        self.conn.execute(
            "SELECT trust_record_owner_contribution(%s, %s, %s, %s, now(), NULL)",
            (self.pmc.pmc_id, self.owner, self.key(), amount),
        )
        self.balance += amount

    @rule(part=PART)
    def the_owner_is_paid(self, part):
        amount = cents(self.balance * part / 100)
        if amount <= 0:
            return
        with acting_as(self.conn, self.staff):
            self.conn.execute(
                "SELECT trust_draw_owner(%s, %s, %s, %s, now())",
                (self.pmc.pmc_id, self.owner, self.key(), amount),
            )
        self.balance -= amount

    @rule(part=PART)
    def a_fee_is_taken(self, part):
        amount = cents(self.balance * part / 100)
        if amount <= 0:
            return
        with acting_as(self.conn, self.staff):
            self.conn.execute(
                "SELECT trust_take_owner_fee(%s, %s, 'other', %s, 'Fee', %s, now())",
                (self.pmc.pmc_id, self.owner, amount, self.key()),
            )
        self.balance -= amount

    @rule(which=st.integers(0, 50), cover=st.booleans())
    def a_payment_bounces(self, which, cover):
        if not self.payments:
            return
        payment = sorted(self.payments)[which % len(self.payments)]
        brought = self.payments[payment]
        short = max(brought - self.balance, ZERO)
        before = self.advanced()
        try:
            with acting_as(self.conn, self.staff), self.conn.transaction():
                returned = rent.bounce(
                    self.conn,
                    self.pmc.pmc_id,
                    payment,
                    self.today,
                    uuid.uuid4() if cover else None,
                )
        except HTTPException as refused:
            event("refused: the owner short, no cover")
            assert short > 0 and not cover
            assert refused.status_code == 409
            assert refused.detail == rent.short_words(short)
            assert self.advanced() == before
            return
        event("covered" if short > 0 else "nothing short")
        assert short == 0 or cover
        assert returned.covered == short
        assert self.advanced() == before + short
        del self.payments[payment]
        self.balance += short - brought
        self.owed += short

    @rule(part=st.integers(1, 150), extra=st.booleans())
    def the_owner_repays(self, part, extra):
        amount = cents(self.owed * part / 100) + (Decimal("0.01") if extra else ZERO)
        if amount <= 0:
            return
        try:
            with acting_as(self.conn, self.staff), self.conn.transaction():
                self.conn.execute(
                    "SELECT trust_repay_company_advance(%s, %s, %s, %s, now())",
                    (self.pmc.pmc_id, self.owner, amount, self.key()),
                )
        except psycopg.errors.CheckViolation:
            event("repayment refused")
            assert amount > self.owed or amount > self.balance
            return
        event("repaid")
        assert amount <= self.owed and amount <= self.balance
        self.owed -= amount
        self.balance -= amount

    def advanced(self) -> Decimal:
        found = self.conn.execute(
            "SELECT coalesce(sum(amount), 0) FROM trust_company_advances WHERE pmc_id = %s",
            (self.pmc.pmc_id,),
        ).fetchone()[0]
        return Decimal(found)

    @invariant()
    def the_books_are_the_models(self):
        assert balance(self.conn, self.owner) == self.balance
        owed = self.conn.execute("SELECT trust_company_advance_owed(%s)", (self.owner,)).fetchone()[
            0
        ]
        assert owed == self.owed >= 0

    @invariant()
    def every_advance_was_its_bounces_shortfall(self):
        # The bounce of each advance's payment took the account to exactly zero at its lowest.
        lows = self.conn.execute(
            "SELECT v.amount, (SELECT min(e.account_current_balance) FROM pgledger_transfers AS t"
            "  JOIN pgledger_entries AS e ON e.transfer_id = t.id"
            "  WHERE t.metadata ->> 'key' = 'bounce/' || v.payment_key"
            "    AND e.account_id = v.ledger_account_id)"
            " FROM trust_company_advances AS v WHERE v.pmc_id = %s",
            (self.pmc.pmc_id,),
        ).fetchall()
        assert all(amount > 0 and low == 0 for amount, low in lows)
        repaid = self.conn.execute(
            "SELECT coalesce(sum(amount), 0) FROM trust_company_advance_repayments"
            " WHERE pmc_id = %s",
            (self.pmc.pmc_id,),
        ).fetchone()[0]
        assert repaid <= self.advanced()

    @invariant()
    def nothing_but_cash_is_below_zero_and_every_bank_ties_out(self):
        below = self.conn.execute(
            "SELECT count(*) FROM trust_ledger_accounts AS t"
            " JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id"
            " WHERE t.pmc_id = %s AND t.kind <> 'bank_cash' AND a.balance < 0",
            (self.pmc.pmc_id,),
        ).fetchone()[0]
        assert below == 0
        assert set(bank_sums(self.conn, self.pmc.pmc_id).values()) <= {Decimal(0)}


def test_a_bounce_the_owner_cant_take_is_refused_or_covered_by_exactly_the_shortfall(conn):
    run_state_machine_as_test(lambda: CompanyCoverMachine(conn))
