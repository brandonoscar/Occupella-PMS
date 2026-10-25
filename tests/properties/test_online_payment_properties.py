"""Property-based tests for rent paid online (migration 0150, src/pms/api/online_payments.py).

Hypothesis starts online payments on a lease (the company or the tenant paying the partner's
fee), has the partner report them paid (with a random fee), failed, returned or expired (the
tenant left its page), in any order and more than once, places and releases payment holds, has
staff accept a payment past a hold, moves the company's earned fees in and out of its fee account,
and has the company cover what waits. A model predicts each step.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - a payment is in the books only once the partner said it was paid, at most once however often
    it is said, and never a failed one; a return undoes it only once it was paid;
  - the owner's account holds exactly the payments recorded less the returns undone (and the fees
    the company earned from it): the partner's fee and a tenant's fee never touch it;
  - the partner's fee leaves trust only from the company's fee account, only once that account
    holds it, and the company's account holds exactly the tenants' fees in, less the partner's fees
    and the tenants' fees that came back, plus what the company covered and earned;
  - what waits for staff is exactly the model's, and every trust bank account ties out.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import psycopg
from fastapi import HTTPException
from helpers import (
    add_unit,
    balance,
    bank_sums,
    charge,
    hold_payments,
    make_pmc,
    open_lease,
    post,
    release_hold,
    staff_named,
)
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from pms import payments
from pms.api import online_payments as op

TODAY = datetime.now(UTC).date()
ZERO = Decimal(0)
CENTS = st.integers(min_value=1, max_value=300_000).map(lambda c: Decimal(c) / 100)
FEES = st.integers(min_value=0, max_value=500).map(lambda c: Decimal(c) / 100)


@dataclass
class Paying:
    """The model's online payment."""

    id: uuid.UUID
    amount: Decimal
    tenant_fee: Decimal
    reported: dict  # kind -> partner fee (paid) or None
    recorded: bool = False  # the payment (and the tenant's fee) in the books
    fee_taken: bool = False  # the partner's fee out of trust
    bounced: bool = False
    fee_back: bool = False  # the tenant's fee back out of the company's account


class OnlinePaymentsMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        pmc = make_pmc(conn, owners=1)
        self.pmc = pmc
        staff = staff_named(conn, pmc.pmc_id, "Staff 1")
        self.session = SimpleNamespace(pmc_id=pmc.pmc_id, person_id=staff)
        unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
        tenant = conn.execute(
            "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
            (pmc.prepaid_rent,),
        ).fetchone()[0]
        self.tenant = tenant
        self.lease = open_lease(
            conn, pmc.pmc_id, unit, TODAY - timedelta(days=30), None, "1000.00", [tenant]
        )
        # Enough owed that every payment pays the owner, none of it held as prepaid rent.
        charge(conn, pmc.pmc_id, self.lease, TODAY - timedelta(days=30), Decimal("10000000.00"))
        self.paying: list[Paying] = []
        # The owner's account and the company's fee account (operating trust bank account).
        self.owner = balance(conn, pmc.owners[0].account)
        self.company = balance(conn, pmc.pmc_income)
        self.hold: uuid.UUID | None = None  # a payment hold in force today

    def call(self, fn, *args):
        """A step in its own transaction, as a request is; the API's refusal, or None."""
        try:
            with self.conn.transaction():
                return fn(*args), None
        except HTTPException as refused:
            return None, refused

    # --- the model ----------------------------------------------------------------------------

    def settle(self, p: Paying, past_hold=False):
        """What op.settle does for one payment, as the model sees it."""
        if "paid" not in p.reported:
            return
        if not p.recorded:
            if self.hold is not None and not past_hold:
                return
            p.recorded = True
            self.owner += p.amount
            self.company += p.tenant_fee
        fee = p.reported["paid"]
        if not p.fee_taken and (fee == 0 or self.company >= fee):
            p.fee_taken = True
            self.company -= fee
        if "returned" in p.reported and not p.bounced and self.owner >= p.amount:
            p.bounced = True
            self.owner -= p.amount
        if p.bounced and p.tenant_fee > 0 and not p.fee_back and self.company >= p.tenant_fee:
            p.fee_back = True
            self.company -= p.tenant_fee

    def waiting(self):
        items = []
        for p in self.paying:
            if "paid" not in p.reported:
                continue
            if not p.recorded:
                items.append((p.id, "payment"))
                continue
            if not p.fee_taken:
                items.append((p.id, "partner fee"))
            if "returned" in p.reported and not p.bounced:
                items.append((p.id, "return"))
            elif p.bounced and p.tenant_fee > 0 and not p.fee_back:
                items.append((p.id, "fee refund"))
        return items

    # --- rules --------------------------------------------------------------------------------

    @rule(amount=CENTS, tenant_pays=st.booleans())
    def start(self, amount, tenant_pays):
        fee = payments.tenant_fee(amount) if tenant_pays else ZERO
        payment = uuid.uuid4()
        self.conn.execute(
            "INSERT INTO trust_online_payments (id, pmc_id, lease_id, tenant_id, amount,"
            " tenant_fee, fee_payer, method, partner, partner_ref)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, 'ach', 'fake', %s)",
            (
                payment,
                self.pmc.pmc_id,
                self.lease,
                self.tenant,
                amount,
                fee,
                "tenant" if tenant_pays else "company",
                f"fake_{payment.hex}",
            ),
        )
        self.paying.append(Paying(payment, amount, fee, {}))

    @precondition(lambda self: self.paying)
    @rule(
        which=st.integers(min_value=0),
        kind=st.sampled_from(["paid", "failed", "returned", "expired"]),
        fee=FEES,
    )
    def report(self, which, kind, fee):
        p = self.paying[which % len(self.paying)]
        event_ = payments.Event(
            p.id,
            kind,
            f"evt_{uuid.uuid4().hex}",
            datetime.now(UTC),
            fee if kind == "paid" else None,
        )
        done, refused = self.call(op.apply, self.conn, event_, "fake")
        assert refused is None and done is True
        # The database takes a report in order (paid or failed, never both; returned only once
        # paid; expired only before either), the first of each kind; one out of order changes
        # nothing, and nothing is settled.
        allowed = (
            (kind == "paid" and "failed" not in p.reported)
            or (kind == "failed" and "paid" not in p.reported)
            or (kind == "returned" and "paid" in p.reported)
            or (kind == "expired" and not {"paid", "failed"} & p.reported.keys())
        )
        if not allowed:
            event("a report out of order")
            return
        if kind in p.reported:
            event("a report sent again")
        else:
            p.reported[kind] = fee if kind == "paid" else None
        self.settle(p)

    @rule()
    def hold_or_release(self):
        if self.hold is None:
            self.hold = hold_payments(
                self.conn, self.pmc.pmc_id, self.lease, TODAY - timedelta(days=1)
            )
        else:
            release_hold(self.conn, self.pmc.pmc_id, self.hold, TODAY)
            self.hold = None

    @precondition(lambda self: self.paying)
    @rule(which=st.integers(min_value=0), past_hold=st.booleans())
    def staff_record(self, which, past_hold):
        p = self.paying[which % len(self.paying)]
        _, refused = self.call(
            op.record, self.session, self.conn, p.id, op.Record(past_hold=past_hold)
        )
        if "paid" not in p.reported:
            assert refused is not None and refused.status_code == 409
            return
        was = p.recorded
        self.settle(p, past_hold=past_hold)
        if not p.recorded:
            assert refused is not None and refused.status_code == 409
        else:
            assert refused is None, refused
            if not was and past_hold:
                event("a payment accepted past a hold")

    @rule(amount=CENTS)
    def company_earns(self, amount):
        """The company earns a fee from the owner's money (a management fee, say)."""
        if amount > self.owner:
            return
        post(
            self.conn,
            self.pmc.pmc_id,
            f"earned/{uuid.uuid4()}",
            [(self.pmc.owners[0].account, self.pmc.pmc_income, amount)],
        )
        self.owner -= amount
        self.company += amount

    @rule(amount=CENTS)
    def company_takes_its_fees(self, amount):
        if amount > self.company:
            return
        self.conn.execute(
            "SELECT trust_withdraw_pmc_fees(%s, %s, %s, %s, now())",
            (self.pmc.pmc_id, self.pmc.operating_bank_id, f"take/{uuid.uuid4()}", amount),
        )
        self.company -= amount

    @rule(share=st.integers(min_value=1, max_value=4))
    def company_covers(self, share):
        owed = sum(
            (
                p.reported["paid"] if what == "partner fee" else p.tenant_fee
                for pid, what in self.waiting()
                for p in self.paying
                if p.id == pid and what in ("partner fee", "fee refund")
            ),
            ZERO,
        )
        needed = max(owed - self.company, ZERO)
        sent = max((needed / share).quantize(Decimal("0.01")), Decimal("0.01"))
        cover = op.Cover(
            bank_account_id=self.pmc.operating_bank_id, amount=sent, request_id=uuid.uuid4()
        )
        _, refused = self.call(op.cover, self.session, self.conn, cover)
        if sent > needed:
            # The company's money goes in only to pay what waits, never to sit in trust.
            assert refused is not None and refused.status_code == 422
            return
        assert refused is None, refused
        event("the company covered what waited")
        self.company += sent
        waiting_ids = {pid for pid, what in self.waiting() if what in ("partner fee", "fee refund")}
        for p in self.paying:
            if p.id in waiting_ids:
                self.settle(p)

    # --- invariants ---------------------------------------------------------------------------

    @invariant()
    def the_books_are_the_models(self):
        assert balance(self.conn, self.pmc.owners[0].account) == self.owner
        assert balance(self.conn, self.pmc.pmc_income) == self.company
        assert all(total == 0 for total in bank_sums(self.conn, self.pmc.pmc_id).values())

    @invariant()
    def each_payment_is_in_the_books_as_the_model_says(self):
        for p in self.paying:
            for key, expected in [
                (op.payment_key(p.id), p.recorded),
                (op.partner_fee_key(p.id), p.fee_taken and bool(p.reported.get("paid"))),
                (op.bounce_key(p.id), p.bounced),
                (op.tenant_fee_key(p.id), p.recorded and p.tenant_fee > 0),
                (op.tenant_fee_back_key(p.id), p.fee_back),
            ]:
                rows = self.conn.execute(
                    "SELECT t.from_account_id, t.to_account_id, t.amount"
                    " FROM pgledger_transfers AS t WHERE t.metadata ->> 'key' = %s",
                    (key,),
                ).fetchall()
                assert bool(rows) == expected, (key, rows)
                if key == op.partner_fee_key(p.id) and rows:
                    # Only from the company's account, and only the partner's own fee.
                    assert [(r[0], r[2]) for r in rows] == [
                        (self.pmc.pmc_income, p.reported["paid"])
                    ]
            if "failed" in p.reported:
                assert not p.recorded
            # What the tenant sees of it: the partner's last word that counts.
            kinds = {
                row[0]
                for row in self.conn.execute(
                    "SELECT kind FROM trust_online_payment_events WHERE payment_id = %s", (p.id,)
                )
            }
            assert kinds == p.reported.keys()
            assert op.status_of(dict.fromkeys(kinds)) == next(
                (k for k in ("returned", "failed", "paid", "expired") if k in p.reported),
                "started",
            )

    @invariant()
    def what_waits_is_the_models(self):
        shown = [(w.payment_id, w.what) for w in op.waiting(self.conn, self.pmc.pmc_id)]
        assert sorted(shown, key=str) == sorted(self.waiting(), key=str)


def test_online_payments_record_what_the_partner_reports_once_and_keep_trust_whole(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: OnlinePaymentsMachine(conn))
