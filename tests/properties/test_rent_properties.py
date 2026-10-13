"""Property-based tests for staff's rent work (src/pms/api/rent.py).

Hypothesis charges a lease rent, fees and credits, records tenant payments (and sends some
again), uses prepaid rent, sets the property's NSF fee and bounces payments, in random order. A
model predicts what the lease owes, what the owner's account and the tenant's prepaid rent hold,
and which steps are refused; the database must agree after every step.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - a payment pays the owner's account no more than the lease owes, and the rest is the tenant's
    prepaid rent; prepaid rent pays no more than is owed either;
  - what each payment paid the owner is matched to charges in full, and no charge is paid past
    its amount;
  - sending a payment again records nothing new;
  - a bounce takes back exactly what the payment moved, or is refused with nothing written;
  - a bounce charges the lease the property's NSF fee in force that day, once, and nothing when
    the terms charge none; every NSF fee is its terms' fee, due the day the payment came back;
  - every trust bank account ties out.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from fastapi import HTTPException
from helpers import add_nsf_fee_policy, add_unit, balance, bank_sums, charge, make_pmc, open_lease
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from pms.api import rent

TODAY = datetime.now(UTC).date()
START = TODAY - timedelta(days=400)
CENTS = st.integers(min_value=1, max_value=300_000).map(lambda c: Decimal(c) / 100)


@dataclass
class Paid:
    request_id: uuid.UUID
    amount: Decimal
    transfers: list[str]
    to_owner: Decimal
    to_prepaid: Decimal
    bounced: bool = False


class RentMachine(RuleBasedStateMachine):
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
        self.property = pmc.owners[0].property_id
        self.rents = 0
        # Model
        self.owed = Decimal(0)
        self.in_owner = Decimal(0)
        self.in_prepaid = Decimal(0)
        self.payments: list[Paid] = []
        self.nsf_terms: dict[date, Decimal] = {}  # NSF fee from each day
        self.nsf_fees: list[tuple[Decimal, date]] = []  # each fee charged, and its terms' day

    # --- what the lease owes ----------------------------------------------------------------

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

    @rule(
        fee=st.integers(min_value=0, max_value=10_000).map(lambda c: Decimal(c) / 100),
        days=st.integers(min_value=-2, max_value=3),
    )
    def set_nsf_fee(self, fee, days):
        starts_on = TODAY - timedelta(days=days)
        if starts_on in self.nsf_terms:
            event("NSF fee: refused, terms start that day already")
            with pytest.raises(psycopg.errors.UniqueViolation), self.conn.transaction():
                add_nsf_fee_policy(self.conn, self.pmc_id, self.property, starts_on, fee)
            return
        add_nsf_fee_policy(self.conn, self.pmc_id, self.property, starts_on, fee)
        self.nsf_terms[starts_on] = fee

    def nsf_terms_on(self, day):
        """The model's NSF fee in force on a day, and the day those terms start: the terms
        starting latest on or before it."""
        started = [d for d in self.nsf_terms if d <= day]
        return (self.nsf_terms[max(started)], max(started)) if started else (Decimal(0), None)

    # --- money ----------------------------------------------------------------------------

    @rule(amount=CENTS)
    def pay(self, amount):
        request = uuid.uuid4()
        with self.conn.transaction():
            recorded = rent.record_payment(
                self.conn, self.pmc_id, self.lease, request, amount, TODAY
            )
        to_owner = min(amount, max(self.owed, Decimal(0)))
        event(
            "payment: "
            + ("to the owner" if to_owner else "")
            + (" and " if to_owner and amount > to_owner else "")
            + ("to prepaid rent" if amount > to_owner else "")
        )
        assert (recorded.to_owner, recorded.to_prepaid) == (to_owner, amount - to_owner)
        self.owed -= to_owner
        self.in_owner += to_owner
        self.in_prepaid += amount - to_owner
        self.payments.append(Paid(request, amount, recorded.transfers, to_owner, amount - to_owner))

    @precondition(lambda self: self.payments)
    @rule(data=st.data())
    def send_a_payment_again(self, data):
        paid = data.draw(st.sampled_from(self.payments))
        with self.conn.transaction():
            again = rent.record_payment(
                self.conn, self.pmc_id, self.lease, paid.request_id, paid.amount, TODAY
            )
        assert again.transfers == paid.transfers

    @rule()
    def use_prepaid(self):
        use = min(self.in_prepaid, max(self.owed, Decimal(0)))
        if use <= 0:
            event("prepaid rent: nothing to use")
            refused = pytest.raises(HTTPException, match="no prepaid rent")
            with refused, self.conn.transaction():
                rent.use_prepaid(self.conn, self.pmc_id, self.lease, uuid.uuid4(), TODAY)
            return
        event("prepaid rent: used")
        with self.conn.transaction():
            used = rent.use_prepaid(self.conn, self.pmc_id, self.lease, uuid.uuid4(), TODAY)
        assert used.to_owner == use
        self.owed -= use
        self.in_owner += use
        self.in_prepaid -= use

    @precondition(lambda self: any(not p.bounced for p in self.payments))
    @rule(data=st.data())
    def bounce(self, data):
        paid = data.draw(st.sampled_from([p for p in self.payments if not p.bounced]))
        if self.in_owner < paid.to_owner or self.in_prepaid < paid.to_prepaid:
            event("bounce: refused, the money was used")
            refused = pytest.raises(psycopg.errors.CheckViolation, match="below zero")
            with refused, self.conn.transaction():
                rent.bounce(self.conn, self.pmc_id, paid.transfers[0], TODAY)
            return
        event("bounce: taken back")
        with self.conn.transaction():
            returned = rent.bounce(self.conn, self.pmc_id, paid.transfers[0], TODAY)
        assert len(returned.transfers) == len(paid.transfers)
        fee, terms = self.nsf_terms_on(TODAY)
        event("bounce: NSF fee charged" if fee else "bounce: no NSF fee")
        assert returned.nsf_fee == (fee or None)
        paid.bounced = True
        self.owed += paid.to_owner + fee
        if fee:
            self.nsf_fees.append((fee, terms))
        self.in_owner -= paid.to_owner
        self.in_prepaid -= paid.to_prepaid

    # --- invariants -----------------------------------------------------------------------

    @invariant()
    def the_books_are_the_models(self):
        assert rent.owed(self.conn, self.lease) == self.owed
        assert balance(self.conn, self.owner) == self.in_owner
        assert balance(self.conn, self.prepaid) == self.in_prepaid
        assert set(bank_sums(self.conn, self.pmc_id).values()) == {Decimal(0)}

    @invariant()
    def every_nsf_fee_is_its_terms_fee_due_the_day_it_came_back(self):
        fees = self.conn.execute(
            "SELECT f.fee, p.fee, p.property_id, p.starts_on, c.lease_id, c.kind, c.amount,"
            " c.due_on FROM trust_nsf_fees AS f"
            " JOIN trust_nsf_fee_policies AS p ON p.id = f.policy_id"
            " JOIN trust_charges AS c ON c.id = f.fee_charge_id WHERE f.pmc_id = %s"
            " ORDER BY f.created_at, c.created_at",
            (self.pmc_id,),
        ).fetchall()
        assert sorted((fee, starts_on) for fee, _, _, starts_on, *_ in fees) == sorted(
            self.nsf_fees
        )
        for fee, terms, prop, _, lease, kind, amount, due_on in fees:
            assert (prop, lease, kind, due_on) == (self.property, self.lease, "fee", TODAY)
            assert fee == terms == amount > 0

    @invariant()
    def payments_are_matched_in_full_and_no_charge_is_overpaid(self):
        for paid in self.payments:
            if paid.to_owner > 0:
                (matched,) = self.conn.execute(
                    "SELECT coalesce(sum(amount), 0) FROM trust_charge_payments"
                    " WHERE transfer_id = %s",
                    (paid.transfers[0],),
                ).fetchone()
                assert matched == paid.to_owner
        assert all(left > 0 for _, left in rent.open_charges(self.conn, self.pmc_id, self.lease))
        assert not self.conn.execute(
            "SELECT 1 FROM trust_charges AS c WHERE c.lease_id = %s AND c.kind <> 'credit'"
            " AND c.amount < coalesce((SELECT sum(p.amount) FROM trust_charge_payments AS p"
            "   WHERE p.charge_id = c.id), 0)"
            " - coalesce((SELECT sum(r.amount) FROM trust_payment_reversals AS r"
            "   WHERE r.charge_id = c.id), 0)",
            (self.lease,),
        ).fetchall()


def test_rent_invariants_hold_under_random_charges_payments_and_bounces(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: RentMachine(conn))
