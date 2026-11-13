"""Property-based tests for Stripe Connect (docs/adr/0014-stripe-connect.md, src/pms/connect.py,
src/pms/api/connect.py, src/pms/api/disputes.py).

Occupella's fee: whatever fee setting the server accepts lands at or above what a payment costs
the platform under the model chosen, at every amount (and the defaults above it by the margin);
what a tenant adds for the fees covers Stripe's and Occupella's and is the least that does, never
more than a card's cap.

The books: Hypothesis starts payments through a connected account (a tenant's or an owner's, by
bank or card, the company or the tenant paying the fees), has Stripe report them paid (keeping
its fee and Occupella's), failed or left, disputed and the dispute won or lost, in any order and
more than once, moves the company's earned fees in and out of its fee account and has staff
record what waits. After every step:
  - an owner's account moves only by whole payments in and their returns out: no fee, Stripe's
    or Occupella's or a dispute's, ever leaves it;
  - the fees Stripe kept (its own and Occupella's) and a dispute's fee leave trust only from the
    company's fee account, each exactly what was kept;
  - every trust bank account ties out.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import psycopg
from fastapi import HTTPException
from helpers import acting_as, add_unit, bank_sums, charge, make_pmc, open_lease, post, staff_named
from hypothesis import event, given
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from pms import connect, payments
from pms.api import connect as connect_api
from pms.api import online_payments as op

TODAY = datetime.now(UTC).date()
ZERO = Decimal(0)
CENTS = st.integers(min_value=1, max_value=10_000_000).map(lambda c: Decimal(c) / 100)
CARD_CENTS = st.integers(min_value=1, max_value=1_000_000).map(lambda c: Decimal(c) / 100)
METHODS = st.sampled_from(connect.METHODS)
FEES = st.builds(
    connect.Fee,
    st.integers(min_value=0, max_value=9000).map(lambda p: Decimal(p) / 1_000_000),
    st.integers(min_value=0, max_value=5000).map(lambda c: Decimal(c) / 100),
    st.integers(min_value=0, max_value=5000).map(lambda c: Decimal(c) / 100),
)


# --- the fee ------------------------------------------------------------------------------------


@given(method=METHODS, amount=CENTS)
def test_the_default_fee_is_never_below_cost(method, amount):
    fee = connect.DEFAULT_FEES[method].on(amount)
    assert fee - connect.cost(connect.CHOSEN, method, amount) >= connect.MARGIN


@given(
    fee=FEES,
    method=METHODS,
    model=st.sampled_from([connect.STANDARD, connect.EXPRESS]),
    amount=CENTS,
)
def test_a_fee_below_cost_check_passes_is_never_below_cost(fee, method, model, amount):
    """below_cost checks a fee where it or the cost turns, so a fee it passes is at or above the
    cost everywhere, not only at the rents it names."""
    if connect.below_cost(fee, method, model) is None:
        assert fee.on(amount) >= connect.cost(model, method, amount)
    else:
        event("a fee below cost somewhere")


PLATFORM_FEES = st.integers(0, 5000).map(lambda c: Decimal(c) / 100)


@given(method=METHODS, amount=CENTS, platform_fee=PLATFORM_FEES)
def test_what_a_tenant_adds_covers_the_fees_and_is_the_least_that_does(
    method, amount, platform_fee
):
    added = connect.gross_up(method, amount, platform_fee)
    assert connect.stripe_fee(method, amount + added) + platform_fee <= added
    if added > 0:
        less = added - Decimal("0.01")
        assert connect.stripe_fee(method, amount + less) + platform_fee > less


@given(amount=CARD_CENTS, cap=st.sampled_from([Decimal("0.02"), Decimal("0.03")]))
def test_a_tenants_card_fee_never_passes_the_cap(amount, cap):
    added = connect.tenant_card_fee(amount, Decimal("0.50"), cap)
    assert 0 <= added <= amount * cap


# --- the books ----------------------------------------------------------------------------------


@dataclass
class Paying:
    id: uuid.UUID
    owner: bool  # an owner's payment into their account, else a tenant's of the lease
    amount: Decimal
    tenant_fee: Decimal
    kept: Decimal  # Stripe's fee and Occupella's, as Stripe reports them on paid
    reported: set


# Keys of everything a payment moves, and where each may move money from.
OWNER_IN = ("payment/", "owner-payment/", "owner-payment-reinstated/")
OWNER_OUT = ("bounce/payment/", "owner-payment-back/")


class ConnectMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        pmc = make_pmc(conn, owners=1)
        self.pmc = pmc
        admin = staff_named(conn, pmc.pmc_id, "Admin 1", roles=("admin",))
        self.session = SimpleNamespace(pmc_id=pmc.pmc_id, person_id=admin)
        self.account = f"fake_acct_{uuid.uuid4().hex[:16]}"
        with acting_as(conn, admin):
            conn.execute(
                "INSERT INTO trust_connected_accounts (pmc_id, account_id, partner, livemode)"
                " VALUES (%s, %s, 'fake_connect', false)",
                (pmc.pmc_id, self.account),
            )
        self.partner = connect.FakeConnected(self.account, conn)
        unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
        self.tenant = conn.execute(
            "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
            (pmc.prepaid_rent,),
        ).fetchone()[0]
        self.lease = open_lease(
            conn, pmc.pmc_id, unit, TODAY - timedelta(days=30), None, "1000.00", [self.tenant]
        )
        # Enough owed that every payment pays the owner, none of it held as prepaid rent.
        charge(conn, pmc.pmc_id, self.lease, TODAY - timedelta(days=30), Decimal("10000000.00"))
        self.paying: list[Paying] = []

    def call(self, fn, *args):
        """A step in its own transaction, as a request is; the API's refusal, or None."""
        try:
            with self.conn.transaction():
                return fn(*args), None
        except HTTPException as refused:
            return None, refused

    @rule(
        amount=CARD_CENTS,
        method=METHODS,
        tenant_pays=st.booleans(),
        owner=st.booleans(),
    )
    def start(self, amount, method, tenant_pays, owner):
        payment = uuid.uuid4()
        platform_fee = connect.DEFAULT_FEES[method].on(amount)
        tenant_fee = ZERO
        if owner:
            self.conn.execute(
                "INSERT INTO trust_owner_online_payments (id, pmc_id, ledger_account_id, amount,"
                " method, partner, partner_ref, platform_fee)"
                " VALUES (%s, %s, %s, %s, %s, 'fake_connect', %s, %s)",
                (
                    payment,
                    self.pmc.pmc_id,
                    self.pmc.owners[0].account,
                    amount,
                    method,
                    f"fake_connect_{payment.hex}",
                    platform_fee,
                ),
            )
            connect_api.started_on_account(self.conn, self.pmc.pmc_id, owner_payment=payment)
        else:
            if tenant_pays:
                tenant_fee = (
                    connect.gross_up("ach", amount, platform_fee)
                    if method == "ach"
                    else connect.tenant_card_fee(amount, platform_fee, connect.NETWORK_CAP)
                )
            self.conn.execute(
                "INSERT INTO trust_online_payments (id, pmc_id, lease_id, tenant_id, amount,"
                " tenant_fee, fee_payer, method, partner, partner_ref, platform_fee)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'fake_connect', %s, %s)",
                (
                    payment,
                    self.pmc.pmc_id,
                    self.lease,
                    self.tenant,
                    amount,
                    tenant_fee,
                    "tenant" if tenant_fee > 0 else "company",
                    method,
                    f"fake_connect_{payment.hex}",
                    platform_fee,
                ),
            )
            connect_api.started_on_account(self.conn, self.pmc.pmc_id, payment=payment)
        kept = connect.stripe_fee(method, amount + tenant_fee) + platform_fee
        self.paying.append(Paying(payment, owner, amount, tenant_fee, kept, set()))

    @precondition(lambda self: self.paying)
    @rule(
        which=st.integers(min_value=0),
        kind=st.sampled_from(["paid", "failed", "expired", "disputed", "won", "lost"]),
    )
    def stripe_says(self, which, kind):
        p = self.paying[which % len(self.paying)]
        now = datetime.now(UTC)
        said = f"evt_{uuid.uuid4().hex}"
        dispute_id = f"du_{p.id.hex}"
        items: list = []
        if kind == "paid":
            items = [payments.Event(p.id, "paid", said, now, p.kept)]
        elif kind in ("failed", "expired"):
            items = [payments.Event(p.id, kind, said, now)]
        elif kind == "disputed":
            items = [
                payments.Event(p.id, "returned", said, now),
                connect.Dispute(p.id, dispute_id, "opened", connect.DISPUTE_FEE, said, now),
            ]
        else:
            back = connect.DISPUTE_FEE if kind == "won" else None
            items = [connect.Dispute(p.id, dispute_id, kind, back, said, now)]
        _, refused = self.call(
            connect_api.record_items, self.conn, self.partner, self.pmc.pmc_id, items
        )
        if refused is not None:
            # A return before paid, or a close before its dispute opened: sent again later.
            assert refused.status_code == 409
            event("a report out of order, sent again later")
            return
        p.reported.add(kind)

    @precondition(lambda self: self.paying)
    @rule(which=st.integers(min_value=0))
    def staff_record(self, which):
        p = self.paying[which % len(self.paying)]
        self.call(op.record, self.session, self.conn, p.id, op.Record())

    @rule(amount=st.integers(min_value=1, max_value=50_000).map(lambda c: Decimal(c) / 100))
    def company_earns(self, amount):
        """The company's earned fees come into its fee account (a management fee, say)."""
        post(
            self.conn,
            self.pmc.pmc_id,
            f"earned/{uuid.uuid4()}",
            [(self.pmc.operating_cash, self.pmc.pmc_income, amount)],
        )

    @rule(amount=st.integers(min_value=1, max_value=50_000).map(lambda c: Decimal(c) / 100))
    def company_takes_its_fees(self, amount):
        held = op.held(self.conn, self.pmc.pmc_income)
        if amount > held:
            return
        self.conn.execute(
            "SELECT trust_withdraw_pmc_fees(%s, %s, %s, %s, now())",
            (self.pmc.pmc_id, self.pmc.operating_bank_id, f"take/{uuid.uuid4()}", amount),
        )

    # --- invariants ---------------------------------------------------------------------------

    def moves(self, account):
        """Each transfer into or out of `account`: its key, its amount, and whether it came in."""
        return [
            (key or "", Decimal(amount), to == account)
            for key, amount, to in self.conn.execute(
                # An owner's contribution names its request key on its own row.
                "SELECT coalesce(t.metadata ->> 'key', c.request_key), t.amount, t.to_account_id"
                " FROM pgledger_transfers AS t"
                " LEFT JOIN trust_owner_contributions AS c ON c.transfer_id = t.id"
                " WHERE t.from_account_id = %s OR t.to_account_id = %s",
                (account, account),
            ).fetchall()
        ]

    @invariant()
    def no_fee_ever_leaves_an_owners_account(self):
        amounts = {str(p.id): p.amount for p in self.paying}
        reinstated = {connect_api_reinstated(p.id): p.amount for p in self.paying if not p.owner}
        for key, amount, came_in in self.moves(self.pmc.owners[0].account):
            if came_in:
                assert key.startswith(OWNER_IN), key
            else:
                assert key.startswith(OWNER_OUT), key
            payment = key.rsplit("/", 1)[1]
            expected = amounts.get(payment) or reinstated.get(key)
            assert amount == expected, (key, amount)

    @invariant()
    def stripes_and_occupellas_fees_leave_only_from_the_companys_account(self):
        by_id = {str(p.id): p for p in self.paying}
        for key, amount, to in self.conn.execute(
            "SELECT metadata ->> 'key', amount, to_account_id FROM pgledger_transfers"
            " WHERE metadata ->> 'key' LIKE 'partner-fee/%%'"
            "    OR metadata ->> 'key' LIKE 'owner-partner-fee/%%'"
            "    OR metadata ->> 'key' LIKE 'dispute-fee/%%'"
        ).fetchall():
            source = self.conn.execute(
                "SELECT from_account_id FROM pgledger_transfers WHERE metadata ->> 'key' = %s",
                (key,),
            ).fetchone()[0]
            p = by_id.get(key.rsplit("/", 1)[1])
            if p is None:
                continue  # another machine's company
            assert source == self.pmc.pmc_income and to == self.pmc.operating_cash
            if key.startswith("dispute-fee/"):
                assert Decimal(amount) == connect.DISPUTE_FEE
            else:
                assert Decimal(amount) == p.kept

    @invariant()
    def every_trust_bank_account_ties_out(self):
        assert all(total == 0 for total in bank_sums(self.conn, self.pmc.pmc_id).values())


def connect_api_reinstated(payment):
    from pms.api import disputes

    return disputes.reinstated_key(payment)


def test_connect_payments_never_take_a_fee_from_an_owner_and_keep_trust_whole(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: ConnectMachine(conn))
