"""Property-based test of who keeps a fee (migration 20261106000220; src/pms/api/rent.py).

Hypothesis charges a lease rent, fees of no type, of a type the owner keeps and of a type the
company keeps, changes who keeps each type on random steps, records payments of random amounts
(some choosing open charges to pay first), uses prepaid rent and bounces payments.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - each match pays the company's fee account exactly when its charge's type was the company's
    as it was charged, and the owner's account otherwise; a fee charged before a change keeps
    the payee it was charged under;
  - what the company's fee account holds is what payments paid of fees it keeps, less what
    bounced; the owner's account holds the rest of what paid the lease's charges;
  - the owner's management fee base counts none of the company's fees;
  - what the lease owes is the model's, and every trust bank account ties out.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
from fastapi import HTTPException
from helpers import add_tenant, add_unit, bank_sums, make_pmc, open_lease
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    rule,
    run_state_machine_as_test,
)

from pms.api import rent

DAY = timedelta(days=1)


def on_this_day() -> None:
    """Today's dates (UTC), worked out again before each test (tests/conftest.py,
    pytest_runtest_setup), so a run past midnight compares them with the database's."""
    global TODAY, START
    TODAY = datetime.now(UTC).date()
    START = TODAY - 400 * DAY


on_this_day()
CENTS = st.integers(min_value=1, max_value=200_000).map(lambda c: Decimal(c) / 100)
KINDS = st.sampled_from(["rent", "untyped", "owner type", "company type"])
PICKS = st.lists(st.tuples(st.integers(0, 9), st.integers(1, 100)), min_size=1, max_size=3)
ZERO = Decimal(0)


def cents(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"))


class FeePayeeMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        pmc = make_pmc(conn)
        self.pmc = pmc
        self.pmc_id = pmc.pmc_id
        prop = pmc.owners[0].property_id
        tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 1")
        unit = add_unit(conn, pmc.pmc_id, prop, "Unit 1")
        self.lease = open_lease(conn, pmc.pmc_id, unit, START, None, "1500.00", [tenant])
        self.prepaid = rent.tenant_account(
            conn, pmc.pmc_id, pmc.operating_bank_id, "prepaid_rent", tenant
        )
        self.types = {
            name: conn.execute(
                "INSERT INTO trust_charge_types (pmc_id, name) VALUES (%s, %s) RETURNING id",
                (pmc.pmc_id, name),
            ).fetchone()[0]
            for name in ("owner type", "company type")
        }
        self.payee = {"owner type": "owner", "company type": "owner"}
        self.set_payee("company type", "company")
        self.kept: dict[uuid.UUID, str] = {}  # each charge's payee as it was charged
        self.charged = ZERO
        self.rent_days: set = set()
        self.payments: list[str] = []  # each payment's first transfer, not bounced yet

    def set_payee(self, name, payee):
        self.conn.execute(
            "INSERT INTO trust_charge_type_payees (pmc_id, charge_type_id, payee)"
            " VALUES (%s, %s, %s)",
            (self.pmc_id, self.types[name], payee),
        )
        self.payee[name] = payee

    @rule(amount=CENTS, back=st.integers(0, 300), kind=KINDS)
    def charge_it(self, amount, back, kind):
        due = TODAY - back * DAY
        if kind == "rent":
            if due in self.rent_days:
                return  # rent is charged once a due date
            self.rent_days.add(due)
        charge = self.conn.execute(
            "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo,"
            " charge_type_id) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                self.pmc_id,
                self.lease,
                due,
                "rent" if kind == "rent" else "fee",
                amount,
                kind,
                self.types.get(kind),
            ),
        ).fetchone()[0]
        self.kept[charge] = self.payee.get(kind, "owner")
        self.charged += amount

    @rule(name=st.sampled_from(["owner type", "company type"]))
    def change_who_keeps_it(self, name):
        self.set_payee(name, "owner" if self.payee[name] == "company" else "company")

    @rule(amount=CENTS, picks=st.none() | PICKS)
    def pay(self, amount, picks):
        before = rent.open_charges(self.conn, self.pmc_id, self.lease)
        chosen: dict[uuid.UUID, Decimal] = {}
        for place, percent in picks or []:
            if not before:
                break
            charge_id, left = before[place % len(before)]
            part = max(cents(left * percent / 100), Decimal("0.01"))
            if charge_id in chosen or sum(chosen.values()) + part > amount:
                continue
            chosen[charge_id] = part
        with self.conn.transaction():
            recorded = rent.record_payment(
                self.conn,
                self.pmc_id,
                self.lease,
                uuid.uuid4(),
                amount,
                TODAY,
                chosen=list(chosen.items()) or None,
            )
        event(f"company's part: {'some' if recorded.to_company > 0 else 'none'}")
        self.payments.append(recorded.transfers[0])

    @rule()
    def use_prepaid(self):
        try:
            with self.conn.transaction():
                rent.use_prepaid(self.conn, self.pmc_id, self.lease, uuid.uuid4(), TODAY)
        except HTTPException as refused:
            assert refused.status_code == 409  # nothing to use, or nothing owed

    @rule(place=st.integers(0, 50))
    def bounce(self, place):
        if not self.payments:
            return
        payment = self.payments[place % len(self.payments)]
        try:
            with self.conn.transaction():
                rent.bounce(self.conn, self.pmc_id, payment, TODAY)
        except psycopg.errors.CheckViolation as refused:
            # Its prepaid rent was used since: the tenant's account can't go below zero.
            assert "below zero" in str(refused) or "would be" in str(refused), refused
            return
        self.payments.remove(payment)

    @invariant()
    def each_fee_goes_to_whoever_kept_it_as_it_was_charged(self):
        rows = self.conn.execute(
            "SELECT cp.charge_id, a.kind FROM trust_charge_payments AS cp"
            " JOIN trust_charges AS c ON c.id = cp.charge_id"
            " JOIN pgledger_transfers AS t ON t.id = cp.transfer_id"
            " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id"
            " WHERE c.lease_id = %s",
            (self.lease,),
        ).fetchall()
        for charge, kind in rows:
            assert kind == ("pmc_income" if self.kept[charge] == "company" else "owner_property")

    @invariant()
    def the_accounts_hold_what_paid_each_ones_fees(self):
        net = dict(
            self.conn.execute(
                "SELECT c.id, coalesce((SELECT sum(p.amount) FROM trust_charge_payments AS p"
                "                       WHERE p.charge_id = c.id), 0)"
                " - coalesce((SELECT sum(r.amount) FROM trust_payment_reversals AS r"
                "             WHERE r.charge_id = c.id), 0)"
                " FROM trust_charges AS c WHERE c.lease_id = %s",
                (self.lease,),
            ).fetchall()
        )
        to_company = sum((v for c, v in net.items() if self.kept[c] == "company"), ZERO)
        to_owner = sum((v for c, v in net.items() if self.kept[c] == "owner"), ZERO)
        held = rent.balances(self.conn, [self.pmc.pmc_income, self.pmc.owners[0].account])
        assert held[self.pmc.pmc_income] == to_company
        assert held[self.pmc.owners[0].account] == to_owner
        # The management fee's base counts rent paid into the owner's account, never the
        # company's fees.
        (base,) = self.conn.execute(
            "SELECT trust_management_fee_collected(%s, '-infinity', 'infinity')",
            (self.pmc.owners[0].account,),
        ).fetchone()
        rent_paid = sum(
            (
                v
                for c, v in net.items()
                if self.conn.execute(
                    "SELECT kind = 'rent' FROM trust_charges WHERE id = %s", (c,)
                ).fetchone()[0]
            ),
            ZERO,
        )
        assert base == rent_paid

    @invariant()
    def the_books_tie_out(self):
        paid = (
            self.conn.execute(
                "SELECT coalesce(sum(p.amount), 0) FROM trust_charge_payments AS p"
                " JOIN trust_charges AS c ON c.id = p.charge_id WHERE c.lease_id = %s",
                (self.lease,),
            ).fetchone()[0]
            - self.conn.execute(
                "SELECT coalesce(sum(r.amount), 0) FROM trust_payment_reversals AS r"
                " JOIN trust_charges AS c ON c.id = r.charge_id WHERE c.lease_id = %s",
                (self.lease,),
            ).fetchone()[0]
        )
        assert rent.owed(self.conn, self.lease) == self.charged - paid
        assert set(bank_sums(self.conn, self.pmc_id).values()) == {Decimal(0)}


def test_a_fee_goes_to_whoever_keeps_its_type_as_it_was_charged_and_the_books_tie_out(
    database_url,
):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: FeePayeeMachine(conn))
