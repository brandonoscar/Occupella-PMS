"""Property-based tests for owners paying in online (migration 20261102000150,
src/pms/api/owner_payments.py).

Hypothesis starts owners' payments online into their property account, has the partner report them
paid (with a random fee), failed, returned or expired (the owner left its page), in any order and
more than once, moves the owner's money out (a fee the company earns) and in (a contribution by
check), moves the company's earned fees in and out of its fee account, has staff record what
waits, and has the company cover it. A model predicts each step.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - a payment is the owner's contribution only once the partner said it was paid, at most once
    however often it is said, and never a failed one; a return takes it back out only once it was
    paid, at most once;
  - the owner's account holds exactly the contributions recorded less the returns taken back out
    (and what else moved it): the partner's fee never touches it;
  - the partner's fee leaves trust only from the company's fee account, only once that account
    holds it, and only the partner's own fee;
  - what waits for staff is exactly the model's, and every trust bank account ties out.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import psycopg
from fastapi import HTTPException
from helpers import balance, bank_sums, contribute, make_pmc, post, staff_named
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
from pms.api import owner_payments as owners

ZERO = Decimal(0)
CENTS = st.integers(min_value=1, max_value=300_000).map(lambda c: Decimal(c) / 100)
FEES = st.integers(min_value=0, max_value=500).map(lambda c: Decimal(c) / 100)


@dataclass
class Paying:
    """The model's owner payment online."""

    id: uuid.UUID
    amount: Decimal
    reported: dict  # kind -> partner fee (paid) or None
    recorded: bool = False  # the owner's contribution in the books
    fee_taken: bool = False  # the partner's fee out of trust
    back: bool = False  # a return taken back out of the owner's account


class OwnerPaymentsMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        pmc = make_pmc(conn, owners=1)
        self.pmc = pmc
        staff = staff_named(conn, pmc.pmc_id, "Staff 1")
        self.session = SimpleNamespace(pmc_id=pmc.pmc_id, person_id=staff)
        self.paying: list[Paying] = []
        # The owner's account and the company's fee account (operating trust bank account).
        self.owner = balance(conn, pmc.owners[0].account)
        self.company = balance(conn, pmc.pmc_income)

    def call(self, fn, *args):
        """A step in its own transaction, as a request is; the API's refusal, or None."""
        try:
            with self.conn.transaction():
                return fn(*args), None
        except HTTPException as refused:
            return None, refused

    # --- the model ----------------------------------------------------------------------------

    def settle(self, p: Paying):
        """What owners.settle does for one payment, as the model sees it."""
        if "paid" not in p.reported:
            return
        if not p.recorded:
            p.recorded = True
            self.owner += p.amount
        fee = p.reported["paid"]
        if not p.fee_taken and (fee == 0 or self.company >= fee):
            p.fee_taken = True
            self.company -= fee
        if "returned" in p.reported and not p.back and self.owner >= p.amount:
            p.back = True
            self.owner -= p.amount

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
            if "returned" in p.reported and not p.back:
                items.append((p.id, "return"))
        return items

    # --- rules --------------------------------------------------------------------------------

    @rule(amount=CENTS)
    def start(self, amount):
        payment = uuid.uuid4()
        self.conn.execute(
            "INSERT INTO trust_owner_online_payments (id, pmc_id, ledger_account_id, amount,"
            " method, partner, partner_ref) VALUES (%s, %s, %s, %s, 'ach', 'fake', %s)",
            (payment, self.pmc.pmc_id, self.pmc.owners[0].account, amount, f"fake_{payment.hex}"),
        )
        self.paying.append(Paying(payment, amount, {}))

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
        done, refused = self.call(owners.apply, self.conn, event_, "fake")
        assert refused is None and done is True
        # A report about it is no tenant's payment.
        assert op.row_of(self.conn, p.id) is None
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

    @precondition(lambda self: self.paying)
    @rule(which=st.integers(min_value=0))
    def staff_record(self, which):
        p = self.paying[which % len(self.paying)]
        _, refused = self.call(op.record, self.session, self.conn, p.id, op.Record())
        if "paid" not in p.reported:
            assert refused is not None and refused.status_code == 409
            return
        assert refused is None, refused
        self.settle(p)

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
    def owner_sends_a_check(self, amount):
        contribute(
            self.conn, self.pmc.pmc_id, self.pmc.owners[0].account, f"c/{uuid.uuid4()}", amount
        )
        self.owner += amount

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
                p.reported["paid"]
                for pid, what in self.waiting()
                for p in self.paying
                if p.id == pid and what == "partner fee"
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
        waiting_ids = {pid for pid, what in self.waiting() if what == "partner fee"}
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
            contributed = self.conn.execute(
                "SELECT c.amount, t.from_account_id, t.to_account_id"
                " FROM trust_owner_contributions AS c"
                " JOIN pgledger_transfers AS t ON t.id = c.transfer_id"
                " WHERE c.pmc_id = %s AND c.request_key = %s",
                (self.pmc.pmc_id, owners.contribution_key(p.id)),
            ).fetchall()
            assert bool(contributed) == p.recorded, contributed
            if contributed:
                assert contributed == [
                    (p.amount, self.pmc.operating_cash, self.pmc.owners[0].account)
                ]
            for key, expected, moved in [
                (
                    owners.partner_fee_key(p.id),
                    p.fee_taken and bool(p.reported.get("paid")),
                    (self.pmc.pmc_income, self.pmc.operating_cash, p.reported.get("paid")),
                ),
                (
                    owners.return_key(p.id),
                    p.back,
                    (self.pmc.owners[0].account, self.pmc.operating_cash, p.amount),
                ),
            ]:
                rows = self.conn.execute(
                    "SELECT t.from_account_id, t.to_account_id, t.amount"
                    " FROM pgledger_transfers AS t WHERE t.metadata ->> 'key' = %s",
                    (key,),
                ).fetchall()
                assert bool(rows) == expected, (key, rows)
                if rows:
                    # The partner's fee only from the company's account and only its own fee;
                    # a return only out of the owner's account, and only what was paid.
                    assert rows == [moved]
            if "failed" in p.reported:
                assert not p.recorded
            kinds = {
                row[0]
                for row in self.conn.execute(
                    "SELECT kind FROM trust_owner_online_payment_events WHERE payment_id = %s",
                    (p.id,),
                )
            }
            assert kinds == p.reported.keys()

    @invariant()
    def what_waits_is_the_models(self):
        shown = [(w.payment_id, w.what) for w in op.waiting(self.conn, self.pmc.pmc_id)]
        assert sorted(shown, key=str) == sorted(self.waiting(), key=str)


def test_owner_payments_online_record_what_the_partner_reports_once_and_keep_trust_whole(
    database_url,
):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: OwnerPaymentsMachine(conn))
