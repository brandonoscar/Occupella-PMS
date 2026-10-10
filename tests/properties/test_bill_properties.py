"""Property-based tests for vendor bills and the owner draws that wait on them.

Hypothesis records management agreements (a reserve and an approval limit), pays money into
owners' accounts, enters bills from three vendors (one with no account to pay it from), records
owners' approvals, sets bills aside, pays them and pays owners. All in random order, through
both the owner role and trust_app. A Python model predicts every step: the database must accept
exactly what the model accepts and refuse the rest with the model's reason.

Invariants checked after every step (ci/registry.toml maps each to this test):
  - the bills, approvals and payment steps are the model's: a vendor's reference is entered
    once; a bill is set aside at most once, only with the owner's approval when it is over the
    limit in force on its date; it is paid at most once, after it was set aside and never dated
    before that;
  - each vendor's account holds exactly what was set aside for its bills and not yet paid, and
    every balance is the model's;
  - a draw never pays out more than the balance above the reserve and the unpaid bills, and
    its request key pays once.

The rule check_owner_balances reads the owner balances report as of a random day, as any role
including the AI's, and compares every line, unpaid bills included, with the model. The AI's
role is refused every write it tries.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

import psycopg
import pytest
from helpers import (
    add_agreement,
    approve_bill,
    draw_owner,
    enter_bill,
    make_pmc,
    pay_bill,
    set_aside_bill,
    transfer_batch,
)
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

CENT = Decimal("0.01")
AMOUNTS = st.integers(min_value=1, max_value=200_000).map(lambda cents: Decimal(cents) / 100)
BASE = date(2000, 1, 1)
DAYS = st.integers(min_value=0, max_value=120)
TIMES = st.integers(min_value=0, max_value=86_399)  # seconds into the day
ROLES = st.sampled_from(["owner", "app"])
REFERENCES = st.sampled_from(["INV-1", "INV-2", "inv-3", "INV-4"])


@dataclass(frozen=True)
class Terms:
    reserve: Decimal
    limit: Decimal


@dataclass(frozen=True)
class Bill:
    account: str
    vendor: int
    reference: str
    bill_date: date
    amount: Decimal


def to_cents(amount):
    """Round to cents, halves away from zero, as Postgres's round(numeric, 2) does."""
    return amount.quantize(CENT, ROUND_HALF_UP)


def moment(day, second):
    return datetime.combine(BASE + timedelta(days=day), time(0), tzinfo=UTC) + timedelta(
        seconds=second
    )


def end_of(day):
    return datetime.combine(day + timedelta(days=1), time(0), tzinfo=UTC)


class BillMachine(RuleBasedStateMachine):
    def __init__(self, owner_conn, app_conn, ai_conn):
        super().__init__()
        self.conns = {"owner": owner_conn, "app": app_conn, "ai": ai_conn}
        self.conn = owner_conn
        pmc = make_pmc(owner_conn, owners=2)
        self.pmc, self.pmc_id = pmc, pmc.pmc_id
        self.owner_account = [owner.account for owner in pmc.owners]
        # Vendor 1 and Vendor 2 have accounts in the operating trust account; Vendor 3 has none.
        self.vendors: list[tuple[UUID, str | None]] = [(pmc.vendor_id, pmc.vendor_payable)]
        for name, opened in [("Vendor 2", True), ("Vendor 3", False)]:
            vendor_id = owner_conn.execute(
                "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
                (self.pmc_id, name),
            ).fetchone()[0]
            payable = (
                owner_conn.execute(
                    "SELECT trust_open_vendor_account(%s, %s, %s)",
                    (self.pmc_id, pmc.operating_bank_id, vendor_id),
                ).fetchone()[0]
                if opened
                else None
            )
            self.vendors.append((vendor_id, payable))
        self.balance: dict[str, Decimal] = {}
        # (source, target, amount, event_at) per transfer the model knows of
        self.transfers: list[tuple[str, str, Decimal, datetime]] = []
        self.agreements: dict[tuple[str, date], Terms] = {}
        self.bills: dict[UUID, Bill] = {}
        self.approved: set[UUID] = set()
        self.set_aside: dict[UUID, tuple[str, datetime]] = {}  # bill -> (transfer, when)
        self.paid: dict[UUID, tuple[str, datetime]] = {}
        self.draws: dict[str, tuple[str, Decimal]] = {}

    # --- the model -----------------------------------------------------------------------

    def record(self, source, target, amount, event_at):
        self.transfers.append((source, target, amount, event_at))
        self.balance[source] = self.balance.get(source, Decimal(0)) - amount
        self.balance[target] = self.balance.get(target, Decimal(0)) + amount

    def agreement_on(self, account, day):
        starts = [start for (held, start) in self.agreements if held == account and start <= day]
        return self.agreements[(account, max(starts))] if starts else None

    def unpaid(self, account, day):
        """Bills dated by the end of `day` whose money was not set aside by then."""
        return sum(
            (
                b.amount
                for i, b in self.bills.items()
                if b.account == account
                and b.bill_date <= day
                and not (i in self.set_aside and self.set_aside[i][1] < end_of(day))
            ),
            Decimal(0),
        )

    def held_on(self, account, cutoff):
        return sum(
            (
                (amount if target == account else -amount)
                for source, target, amount, when in self.transfers
                if account in (source, target) and when < cutoff
            ),
            Decimal(0),
        )

    # --- rules ---------------------------------------------------------------------------

    @rule(
        data=st.data(),
        role=ROLES,
        start=DAYS,
        reserve=st.sampled_from(["0", "200.00"]),
        limit=st.sampled_from(["0", "100.00", "500.00"]),
    )
    def record_an_agreement(self, data, role, start, reserve, limit):
        account = data.draw(st.sampled_from(self.owner_account))
        starts_on = BASE + timedelta(days=start)

        def send():
            add_agreement(
                self.conns[role],
                self.pmc_id,
                account,
                starts_on,
                reserve=reserve,
                approval_limit=limit,
            )

        if (account, starts_on) in self.agreements:
            with pytest.raises(psycopg.errors.UniqueViolation):
                send()
        else:
            send()
            self.agreements[(account, starts_on)] = Terms(Decimal(reserve), Decimal(limit))

    @rule(data=st.data(), amount=AMOUNTS, day=DAYS, second=TIMES)
    def pay_money_in(self, data, amount, day, second):
        account = data.draw(st.sampled_from(self.owner_account))
        when = moment(day, second)
        transfer_batch(self.conn, [(self.pmc.operating_cash, account, amount)], when)
        self.record(self.pmc.operating_cash, account, amount, when)

    @rule(
        data=st.data(),
        role=ROLES,
        vendor=st.sampled_from([0, 0, 1, 1, 2]),  # Vendor 3, with no account, now and then
        reference=REFERENCES,
        day=DAYS,
        amount=st.one_of(AMOUNTS, st.sampled_from([Decimal("100.00"), Decimal("100.01")])),
    )
    def enter_a_bill(self, data, role, vendor, reference, day, amount):
        account = data.draw(st.sampled_from(self.owner_account))
        bill_date = BASE + timedelta(days=day)

        def send():
            return enter_bill(
                self.conns[role],
                self.pmc_id,
                self.vendors[vendor][0],
                account,
                reference,
                bill_date,
                bill_date + timedelta(days=30),
                amount,
            )

        if any(b.vendor == vendor and b.reference == reference for b in self.bills.values()):
            with pytest.raises(psycopg.errors.UniqueViolation):
                send()
            return
        self.bills[send()] = Bill(account, vendor, reference, bill_date, amount)

    @precondition(lambda self: self.bills)
    @rule(data=st.data(), role=ROLES, day=DAYS)
    def approve_a_bill(self, data, role, day):
        bill_id = data.draw(st.sampled_from(list(self.bills)))

        def send():
            approve_bill(
                self.conns[role], self.pmc_id, bill_id, BASE + timedelta(days=day), "Owner, email"
            )

        if bill_id in self.approved:
            with pytest.raises(psycopg.errors.UniqueViolation):
                send()
            return
        send()
        self.approved.add(bill_id)

    @precondition(lambda self: self.bills)
    @rule(data=st.data(), role=ROLES, day=DAYS, second=TIMES)
    def set_a_bill_aside(self, data, role, day, second):
        self.attempt_set_aside(role, data.draw(st.sampled_from(list(self.bills))), day, second)

    @precondition(lambda self: any(i not in self.set_aside for i in self.bills))
    @rule(data=st.data(), role=ROLES, day=DAYS, second=TIMES)
    def fund_approve_and_set_aside(self, data, role, day, second):
        """The usual path for a bill not yet set aside: the owner's account gets what it lacks
        and the owner approves the bill if it isn't approved, then it is set aside."""
        bill_id = data.draw(st.sampled_from([i for i in self.bills if i not in self.set_aside]))
        bill, when = self.bills[bill_id], moment(day, second)
        short = bill.amount - self.balance.get(bill.account, Decimal(0))
        if short > 0:
            transfer_batch(self.conn, [(self.pmc.operating_cash, bill.account, short)], when)
            self.record(self.pmc.operating_cash, bill.account, short, when)
        if bill_id not in self.approved:
            approve_bill(self.conn, self.pmc_id, bill_id, bill.bill_date, "Owner, phone")
            self.approved.add(bill_id)
        self.attempt_set_aside(role, bill_id, day, second)

    def attempt_set_aside(self, role, bill_id, day, second):
        bill, when = self.bills[bill_id], moment(day, second)
        terms = self.agreement_on(bill.account, bill.bill_date)
        payable = self.vendors[bill.vendor][1]

        def send():
            return set_aside_bill(self.conns[role], self.pmc_id, bill_id, when)

        if bill_id in self.set_aside:
            assert send() == self.set_aside[bill_id][0]  # a retry: nothing moves again
            return
        if bill.amount > (terms.limit if terms else 0) and bill_id not in self.approved:
            refusal = (psycopg.errors.CheckViolation, "approval limit")
        elif payable is None:
            refusal = (psycopg.errors.InvalidParameterValue, "open the vendor's account")
        elif bill.amount > self.balance.get(bill.account, Decimal(0)):
            refusal = (psycopg.errors.CheckViolation, "below zero")
        else:
            self.set_aside[bill_id] = (send(), when)
            self.record(bill.account, payable, bill.amount, when)
            return
        with pytest.raises(refusal[0], match=refusal[1]):
            send()

    @precondition(lambda self: self.bills)
    @rule(
        data=st.data(),
        role=ROLES,
        any_bill=st.booleans(),
        delay=st.sampled_from([-1, 0, 1, 3_600, 20 * 86_400]),
    )
    def pay_a_bill(self, data, role, any_bill, delay):
        """Mostly a bill set aside and not yet paid, dated a second before it was set aside,
        at the same moment, or later; now and then any bill."""
        ready = [i for i in self.set_aside if i not in self.paid]
        bill_id = data.draw(st.sampled_from(list(self.bills) if any_bill or not ready else ready))
        bill = self.bills[bill_id]
        start = self.set_aside[bill_id][1] if bill_id in self.set_aside else moment(0, 0)
        when = start + timedelta(seconds=delay)

        def send():
            return pay_bill(self.conns[role], self.pmc_id, bill_id, when)

        if bill_id in self.paid:
            assert send() == self.paid[bill_id][0]  # a retry
            return
        if bill_id not in self.set_aside:
            refusal = (psycopg.errors.InvalidParameterValue, "not set aside")
        elif when < self.set_aside[bill_id][1]:
            refusal = (psycopg.errors.InvalidParameterValue, "paid before that")
        else:
            self.paid[bill_id] = (send(), when)
            payable = self.vendors[bill.vendor][1]
            self.record(payable, self.pmc.operating_cash, bill.amount, when)
            return
        with pytest.raises(refusal[0], match=refusal[1]):
            send()

    @rule(
        data=st.data(),
        role=ROLES,
        key=st.sampled_from(["draw-1", "draw-2", "draw-3"]),
        choice=st.sampled_from(["all", "what is available", "a cent too much", "half"]),
        day=DAYS,
        second=TIMES,
    )
    def pay_the_owner(self, data, role, key, choice, day, second):
        account = data.draw(st.sampled_from(self.owner_account))
        when = moment(day, second)
        terms = self.agreement_on(account, when.date())
        available = (
            self.balance.get(account, Decimal(0))
            - (terms.reserve if terms else 0)
            - self.unpaid(account, when.date())
        )
        amount = {
            "all": None,
            "what is available": available,
            "a cent too much": available + CENT,
            "half": to_cents(available / 2),
        }[choice]

        def send():
            return draw_owner(self.conns[role], self.pmc_id, account, key, amount, when)

        drawn = self.draws.get(key)
        if amount is not None and amount <= 0:
            refusal = (psycopg.errors.InvalidParameterValue, "positive amount")
        elif drawn is not None:
            if drawn[0] == account and amount in (None, drawn[1]):
                assert send() == drawn[1]  # a retry: the original draw, nothing more paid
                return
            refusal = (psycopg.errors.UniqueViolation, "already paid")
        elif available <= 0 or (amount if amount is not None else available) > available:
            refusal = (psycopg.errors.CheckViolation, "available")
        else:
            paying = amount if amount is not None else available
            assert send() == paying
            self.draws[key] = (account, paying)
            self.record(account, self.pmc.operating_cash, paying, when)
            return
        with pytest.raises(refusal[0], match=refusal[1]):
            send()

    @rule(role=st.sampled_from(["owner", "app", "ai"]), day=st.integers(-5, 160))
    def check_owner_balances(self, role, day):
        as_of = BASE + timedelta(days=day)
        rows = (
            self.conns[role]
            .execute(
                "SELECT item, balance, reserve, bills, available"
                " FROM trust_report_owner_balances(%s, %s) WHERE item <> 'as of'",
                (self.pmc_id, as_of),
            )
            .fetchall()
        )
        expected = []
        for account in self.owner_account:
            terms = self.agreement_on(account, as_of)
            held = self.held_on(account, end_of(as_of))
            reserve = terms.reserve if terms else Decimal(0)
            bills = self.unpaid(account, as_of)
            expected.append(
                ("owner property", held, reserve, bills, max(held - reserve - bills, Decimal(0)))
            )
        expected.append(
            ("total", *(sum((line[i] for line in expected), Decimal(0)) for i in range(1, 5)))
        )
        assert rows[1:] == expected

    @precondition(lambda self: self.bills)
    @rule(data=st.data(), write=st.sampled_from(["enter", "approve", "set aside", "pay"]))
    def the_ai_role_cannot_write(self, data, write):
        """Whatever the AI tries, valid or not, is refused for lack of a grant, and nothing
        changes (the invariants below compare every table with the model)."""
        ai, bill_id = self.conns["ai"], data.draw(st.sampled_from(list(self.bills)))
        bill, day = self.bills[bill_id], BASE
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            if write == "enter":
                enter_bill(
                    ai,
                    self.pmc_id,
                    self.vendors[0][0],
                    bill.account,
                    "AI-1",
                    day,
                    day,
                    "1.00",
                )
            elif write == "approve":
                approve_bill(ai, self.pmc_id, bill_id, day, "AI")
            elif write == "set aside":
                set_aside_bill(ai, self.pmc_id, bill_id, moment(0, 0))
            else:
                pay_bill(ai, self.pmc_id, bill_id, moment(0, 0))

    # --- invariants: checked after every step --------------------------------------------

    @invariant()
    def bills_and_their_steps_are_the_models(self):
        stored = self.conn.execute(
            "SELECT id, ledger_account_id, vendor_id, reference, bill_date, amount"
            " FROM trust_bills WHERE pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        vendor_ids = [vendor_id for vendor_id, _ in self.vendors]
        assert {i: (a, vendor_ids.index(v), r, d, m) for i, a, v, r, d, m in stored} == {
            i: (b.account, b.vendor, b.reference, b.bill_date, b.amount)
            for i, b in self.bills.items()
        }
        approvals = self.conn.execute(
            "SELECT bill_id FROM trust_bill_approvals WHERE pmc_id = %s", (self.pmc_id,)
        ).fetchall()
        assert {i for (i,) in approvals} == self.approved
        steps = self.conn.execute(
            "SELECT p.bill_id, p.step, p.transfer_id, t.event_at FROM trust_bill_payments p"
            " JOIN pgledger_transfers t ON t.id = p.transfer_id WHERE p.pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert {i: (t, w) for i, s, t, w in steps if s == "set_aside"} == self.set_aside
        assert {i: (t, w) for i, s, t, w in steps if s == "paid"} == self.paid

    @invariant()
    def vendors_hold_what_was_set_aside_and_not_paid(self):
        for index, (_, payable) in enumerate(self.vendors):
            if payable is None:
                continue
            owed = sum(
                (
                    b.amount
                    for i, b in self.bills.items()
                    if b.vendor == index and i in self.set_aside and i not in self.paid
                ),
                Decimal(0),
            )
            (held,) = self.conn.execute(
                "SELECT balance FROM pgledger_accounts WHERE id = %s", (payable,)
            ).fetchone()
            assert held == owed
        for account, expected in self.balance.items():
            (held,) = self.conn.execute(
                "SELECT balance FROM pgledger_accounts WHERE id = %s", (account,)
            ).fetchone()
            assert held == expected

    @invariant()
    def draws_are_the_models(self):
        draws = self.conn.execute(
            "SELECT request_key, ledger_account_id, amount FROM trust_owner_draws"
            " WHERE pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert {k: (a, amount) for k, a, amount in draws} == self.draws


def test_bill_invariants_hold_under_random_bills_approvals_payments_and_draws(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as owner_conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
        psycopg.connect(database_url, autocommit=True) as ai_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        ai_conn.execute("SET ROLE trust_ai_agent")
        run_state_machine_as_test(lambda: BillMachine(owner_conn, app_conn, ai_conn))
