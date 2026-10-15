"""Property-based tests for vendor bills and the owner draws that wait on them.

Hypothesis records management agreements (a reserve and an approval limit), pays money into
owners' accounts, enters bills from three vendors (one with no account to pay it from), records
owners' approvals and declines, sets bills aside, pays them and pays owners. All in random
order, through both the owner role and trust_app. A Python model predicts every step: the
database must accept exactly what the model accepts and refuse the rest with the model's
reason.

Invariants checked after every step (ci/registry.toml maps each to this test):
  - the bills, decisions and payment steps are the model's: a vendor's reference is entered
    once; a bill takes one decision, an approval or a decline, and a decline only before it is
    set aside; a bill is set aside at most once, never once declined, and only with the owner's
    approval when it is over the limit in force on its date; it is paid at most once, after it
    was set aside and never dated before that;
  - each vendor's account holds exactly what was set aside for its bills and not yet paid, and
    every balance is the model's;
  - a draw never pays out more than the balance above the reserve and the unpaid bills (a bill
    declined holds nothing back from the day it was declined), and its request key pays once.

Recurring bills are added on random days of the month, ended and entered through random days:
each month's bill is entered once, never under a reference the vendor already has. What the
old system paid a vendor in a year before a cutover is carried over once per vendor and year.
The rule check_vendor_payments reads what each vendor was paid in a year, here and carried
over, and compares it with the model.

Bills and recurring bills are put in expense accounts at random, again and again: a bill's
latest is its account, and each month's bill a recurring bill enters is in the recurring bill's
latest account then. The rule check_the_expenses reads an owner's income statement over random
days and compares every expense line (each expense account's bills set aside in the days, those
in none, the total and net income) with the model.

The rules check_owner_balances and check_unpaid_bills read the owner balances and unpaid bills
reports as of a random day, as any role including the AI's, and compare every line with the
model. The AI's
role is refused every write it tries.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

import psycopg
import pytest
from helpers import (
    add_agreement,
    add_recurring_bill,
    approve_bill,
    carry_over_vendor_payments,
    decline_bill,
    draw_owner,
    end_recurring_bill,
    enter_bill,
    enter_recurring_bills,
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
class Recurring:
    vendor: int
    account: str
    prefix: str
    amount: Decimal
    day_of_month: int
    starts_on: date
    ends_on: date | None = None


@dataclass(frozen=True)
class Bill:
    account: str
    vendor: int
    reference: str
    bill_date: date
    amount: Decimal
    due_on: date | None = None  # None: 30 days after the bill date, as enter_a_bill sets it

    @property
    def due(self):
        return self.due_on or self.bill_date + timedelta(days=30)


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
        self.declined: dict[UUID, date] = {}  # bill -> the day its owner declined it
        self.set_aside: dict[UUID, tuple[str, datetime]] = {}  # bill -> (transfer, when)
        self.paid: dict[UUID, tuple[str, datetime]] = {}
        self.draws: dict[str, tuple[str, Decimal]] = {}
        self.recurring: dict[UUID, Recurring] = {}
        # (vendor index, year) -> (paid before, amount) the old system paid it that year
        self.carried: dict[tuple[int, int], tuple[date, Decimal]] = {}
        # Expense accounts (id, name, Schedule E line), and the latest of each bill and
        # recurring bill, by its place in that list.
        self.expense_accounts = [
            (
                owner_conn.execute(
                    "INSERT INTO trust_expense_accounts (pmc_id, name, schedule_e_line)"
                    " VALUES (%s, %s, %s) RETURNING id",
                    (self.pmc_id, name, line),
                ).fetchone()[0],
                name,
                line,
            )
            for name, line in [("Water", 17), ("Repairs", 14), ("Gutters", 14)]
        ]
        self.bill_account: dict[UUID, int] = {}
        self.recurring_account: dict[UUID, int] = {}

    # --- the model -----------------------------------------------------------------------

    def record(self, source, target, amount, event_at):
        self.transfers.append((source, target, amount, event_at))
        self.balance[source] = self.balance.get(source, Decimal(0)) - amount
        self.balance[target] = self.balance.get(target, Decimal(0)) + amount

    def agreement_on(self, account, day):
        starts = [start for (held, start) in self.agreements if held == account and start <= day]
        return self.agreements[(account, max(starts))] if starts else None

    def unpaid(self, account, day):
        """Bills dated by the end of `day` whose money was not set aside by then, and not
        declined by then."""
        return sum(
            (
                b.amount
                for i, b in self.bills.items()
                if b.account == account
                and b.bill_date <= day
                and not (i in self.set_aside and self.set_aside[i][1] < end_of(day))
                and not (i in self.declined and self.declined[i] <= day)
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

        if bill_id in self.approved or bill_id in self.declined:
            with pytest.raises(psycopg.errors.UniqueViolation):
                send()
            return
        send()
        self.approved.add(bill_id)

    @precondition(lambda self: self.bills)
    @rule(data=st.data(), role=ROLES, day=DAYS)
    def decline_a_bill(self, data, role, day):
        """The owner declines a bill, now and then one they decided or one set aside already."""
        bill_id = data.draw(st.sampled_from(list(self.bills)))
        on = BASE + timedelta(days=day)

        def send():
            decline_bill(self.conns[role], self.pmc_id, bill_id, on, "Owner, in the portal")

        if bill_id in self.approved or bill_id in self.declined:
            with pytest.raises(psycopg.errors.UniqueViolation, match="decision already"):
                send()
        elif bill_id in self.set_aside:
            with pytest.raises(psycopg.errors.CheckViolation, match="set aside already"):
                send()
        else:
            send()
            self.declined[bill_id] = on

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
        self.fund_and_set_aside(role, bill_id, day, second)

    def fund_and_set_aside(self, role, bill_id, day, second):
        bill, when = self.bills[bill_id], moment(day, second)
        short = bill.amount - self.balance.get(bill.account, Decimal(0))
        if short > 0:
            transfer_batch(self.conn, [(self.pmc.operating_cash, bill.account, short)], when)
            self.record(self.pmc.operating_cash, bill.account, short, when)
        if bill_id not in self.approved and bill_id not in self.declined:
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
        if bill_id in self.declined:
            refusal = (psycopg.errors.CheckViolation, "declined")
        elif bill.amount > (terms.limit if terms else 0) and bill_id not in self.approved:
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
        delay=st.sampled_from([-1, 0, 1, 3_600, 20 * 86_400, 366 * 86_400]),
    )
    def pay_a_bill(self, data, role, any_bill, delay):
        """Mostly a bill set aside and not yet paid, dated a second before it was set aside,
        at the same moment, or later (as late as the next year); now and then any bill."""
        ready = [i for i in self.set_aside if i not in self.paid]
        bill_id = data.draw(st.sampled_from(list(self.bills) if any_bill or not ready else ready))
        start = self.set_aside[bill_id][1] if bill_id in self.set_aside else moment(0, 0)
        self.attempt_payment(role, bill_id, start + timedelta(seconds=delay))

    @precondition(lambda self: any(i not in self.paid for i in self.bills))
    @rule(
        data=st.data(),
        role=ROLES,
        day=DAYS,
        second=TIMES,
        delay=st.sampled_from([0, 86_400, 366 * 86_400]),
    )
    def settle_a_bill(self, data, role, day, second, delay):
        """A bill not yet paid taken the whole way, as most are: funded, approved and set aside
        if it isn't, then paid, so the vendor payments report has payments to read."""
        bill_id = data.draw(st.sampled_from([i for i in self.bills if i not in self.paid]))
        if bill_id not in self.set_aside:
            self.fund_and_set_aside(role, bill_id, day, second)
        if bill_id in self.set_aside:
            self.attempt_payment(
                role, bill_id, self.set_aside[bill_id][1] + timedelta(seconds=delay)
            )

    def attempt_payment(self, role, bill_id, when):
        bill = self.bills[bill_id]

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

    @rule(role=st.sampled_from(["owner", "app", "ai"]), day=st.integers(-5, 160))
    def check_unpaid_bills(self, role, day):
        """Every bill dated by the day and not paid by its end, by vendor, due date and
        reference (byte order), with what was set aside by then and its bucket."""
        as_of = BASE + timedelta(days=day)
        rows = (
            self.conns[role]
            .execute(
                "SELECT item, vendor, due_on, reference, days_past_due, bucket, amount, set_aside"
                " FROM trust_report_unpaid_bills(%s, %s) WHERE item IN ('bill', 'total')",
                (self.pmc_id, as_of),
            )
            .fetchall()
        )
        expected = []
        for i, b in self.bills.items():
            if b.bill_date > as_of or (i in self.paid and self.paid[i][1] < end_of(as_of)):
                continue
            if i in self.declined and self.declined[i] <= as_of:
                continue
            due = b.due
            late = max((as_of - due).days, 0)
            bucket = next(
                name
                for limit, name in [(0, "current"), (30, "1-30"), (60, "31-60"), (90, "61-90")]
                + [(late, "over 90")]
                if late <= limit
            )
            held = b.amount if i in self.set_aside and self.set_aside[i][1] < end_of(as_of) else 0
            vendor = f"Vendor {b.vendor + 1}"
            expected.append(("bill", vendor, due, b.reference, late, bucket, b.amount, held))
        expected.sort(key=lambda e: (e[1].encode(), e[2], e[3].encode()))
        expected.append(
            (
                "total",
                *([None] * 5),
                sum((e[6] for e in expected), Decimal(0)),
                sum((e[7] for e in expected), Decimal(0)),
            )
        )
        assert rows == expected

    @precondition(lambda self: self.bills)
    @rule(
        data=st.data(),
        write=st.sampled_from(["enter", "approve", "decline", "set aside", "pay", "carry over"]),
    )
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
            elif write == "decline":
                decline_bill(ai, self.pmc_id, bill_id, day, "AI")
            elif write == "set aside":
                set_aside_bill(ai, self.pmc_id, bill_id, moment(0, 0))
            elif write == "carry over":
                carry_over_vendor_payments(
                    ai, self.pmc_id, self.vendors[0][0], BASE.year, BASE + timedelta(1), "1.00"
                )
            else:
                pay_bill(ai, self.pmc_id, bill_id, moment(0, 0))

    # --- invariants: checked after every step --------------------------------------------

    # --- rules: recurring bills and vendor payments ------------------------------------

    @rule(
        data=st.data(),
        role=ROLES,
        vendor=st.sampled_from([0, 1, 2]),
        prefix=st.sampled_from(["LAWN", "POOL"]),
        amount=AMOUNTS,
        day_of_month=st.integers(min_value=1, max_value=28),
        start=DAYS,
        on_its_day=st.booleans(),
    )
    def add_a_recurring_bill(
        self, data, role, vendor, prefix, amount, day_of_month, start, on_its_day
    ):
        """Starting on a random day, or now and then on its own day of the month, whose bill
        is then the first one."""
        account = data.draw(st.sampled_from(self.owner_account))
        starts_on = BASE + timedelta(days=start)
        if on_its_day:
            starts_on = starts_on.replace(day=day_of_month)

        def send():
            return add_recurring_bill(
                self.conns[role],
                self.pmc_id,
                self.vendors[vendor][0],
                account,
                prefix,
                amount,
                day_of_month,
                starts_on,
            )

        if any((r.vendor, r.prefix) == (vendor, prefix) for r in self.recurring.values()):
            with pytest.raises(psycopg.errors.UniqueViolation):
                send()
            return
        self.recurring[send()] = Recurring(vendor, account, prefix, amount, day_of_month, starts_on)

    @precondition(lambda self: self.recurring)
    @rule(data=st.data(), role=ROLES, days=st.integers(min_value=0, max_value=120))
    def end_a_recurring_bill(self, data, role, days):
        recurring_id = data.draw(st.sampled_from(list(self.recurring)))
        recurring = self.recurring[recurring_id]
        ends_on = recurring.starts_on + timedelta(days=days)

        def send():
            end_recurring_bill(self.conns[role], self.pmc_id, recurring_id, ends_on)

        if recurring.ends_on is not None:
            with pytest.raises(psycopg.errors.UniqueViolation):
                send()
            return
        send()
        self.recurring[recurring_id] = replace(recurring, ends_on=ends_on)

    @precondition(lambda self: self.recurring)
    @rule(role=ROLES, through=st.integers(min_value=0, max_value=200))
    def enter_the_recurring_bills(self, role, through):
        """Each month's bill of every recurring bill due by `through`, once: none twice, none
        under a reference a bill of the vendor already has."""
        through_on = BASE + timedelta(days=through)
        new: dict[tuple[int, str], tuple[UUID, Recurring, date]] = {}
        for recurring_id, recurring in self.recurring.items():
            month = recurring.starts_on.replace(day=1)
            while month <= through_on:
                due = month + timedelta(days=recurring.day_of_month - 1)
                reference = f"{recurring.prefix}-{due:%Y-%m}"
                key = (recurring.vendor, reference)
                if (
                    recurring.starts_on <= due <= through_on
                    and (recurring.ends_on is None or due < recurring.ends_on)
                    and not any((b.vendor, b.reference) == key for b in self.bills.values())
                ):
                    new[key] = (recurring_id, recurring, due)
                month = (month + timedelta(days=32)).replace(day=1)

        assert enter_recurring_bills(self.conns[role], self.pmc_id, through_on) == len(new)
        vendor_of = {vendor_id: i for i, (vendor_id, _) in enumerate(self.vendors)}
        stored = {
            (vendor_of[vendor_id], reference): bill_id
            for bill_id, vendor_id, reference in self.conn.execute(
                "SELECT id, vendor_id, reference FROM trust_bills WHERE pmc_id = %s",
                (self.pmc_id,),
            )
        }
        # In a fixed order: the rules draw bills by their place in self.bills, and the
        # database's order follows random ids, which Hypothesis can't replay.
        for (vendor, reference), (recurring_id, recurring, due) in sorted(new.items()):
            bill_id = stored[(vendor, reference)]
            self.bills[bill_id] = Bill(
                recurring.account, vendor, reference, due, recurring.amount, due
            )
            if recurring_id in self.recurring_account:
                self.bill_account[bill_id] = self.recurring_account[recurring_id]

    # --- expense accounts and the income statement -------------------------------------------

    @precondition(lambda self: self.bills)
    @rule(data=st.data(), role=ROLES, account=st.integers(0, 2))
    def put_a_bill_in_an_expense_account(self, data, role, account):
        """Any bill, set aside or not, paid or not: its latest account is what it is."""
        bill_id = data.draw(st.sampled_from(list(self.bills)))
        self.conns[role].execute(
            "INSERT INTO trust_bill_expense_accounts (pmc_id, bill_id, expense_account_id)"
            " VALUES (%s, %s, %s)",
            (self.pmc_id, bill_id, self.expense_accounts[account][0]),
        )
        self.bill_account[bill_id] = account

    @precondition(lambda self: self.recurring)
    @rule(data=st.data(), role=ROLES, account=st.integers(0, 2))
    def put_a_recurring_bill_in_an_expense_account(self, data, role, account):
        recurring_id = data.draw(st.sampled_from(list(self.recurring)))
        self.conns[role].execute(
            "INSERT INTO trust_recurring_bill_expense_accounts"
            " (pmc_id, recurring_bill_id, expense_account_id) VALUES (%s, %s, %s)",
            (self.pmc_id, recurring_id, self.expense_accounts[account][0]),
        )
        self.recurring_account[recurring_id] = account

    @rule(
        role=st.sampled_from(["owner", "app", "ai"]),
        owner=st.integers(0, 1),
        first=st.integers(-3, 125),
        length=st.integers(0, 130),
    )
    def check_the_expenses(self, role, owner, first, length):
        """An owner's income statement over random days: each expense account's bills set
        aside in them (by their latest account), those in none, the total and net income (no
        payment here is matched to a charge, so nothing is income)."""
        account = self.owner_account[owner]
        start_on = BASE + timedelta(days=first)
        last_on = start_on + timedelta(days=length)
        spent: dict[int | None, Decimal] = {}
        for bill_id, (_, when) in self.set_aside.items():
            bill = self.bills[bill_id]
            if bill.account == account and end_of(start_on - timedelta(days=1)) <= when < end_of(
                last_on
            ):
                kind = self.bill_account.get(bill_id)
                spent[kind] = spent.get(kind, Decimal(0)) + bill.amount
        named = sorted(
            (
                (self.expense_accounts[kind][2], self.expense_accounts[kind][1], amount)
                for kind, amount in spent.items()
                if kind is not None
            ),
        )
        total = sum(spent.values(), Decimal(0))
        expected = [
            ("expense: management fees", 11, Decimal(0)),
            ("expense: leasing fees", 8, Decimal(0)),
            *((f"expense: {name}", line, amount) for line, name, amount in named),
            *(
                [("expense: bills not in an expense account", None, spent[None])]
                if None in spent
                else []
            ),
            ("total expenses", None, total),
            ("net income", None, -total),
        ]
        rows = (
            self.conns[role]
            .execute(
                "SELECT item, schedule_e, amount FROM trust_report_income_statement(%s, %s, %s, %s)"
                " WHERE property = %s AND (item LIKE 'expense: %%' OR item IN"
                " ('total expenses', 'net income')) ORDER BY line",
                (
                    self.pmc_id,
                    self.pmc.owners[owner].owner_id,
                    start_on,
                    last_on,
                    f"Property {owner + 1}",
                ),
            )
            .fetchall()
        )
        assert rows == expected

    @rule(
        data=st.data(),
        role=ROLES,
        vendor=st.integers(min_value=0, max_value=2),
        year=st.integers(min_value=-1, max_value=1),
        day=st.integers(min_value=1, max_value=366),
        amount=AMOUNTS,
    )
    def carry_over_a_vendors_payments(self, data, role, vendor, year, day, amount):
        """What the old system paid a vendor in a year before the cutover: from the year's
        second day through the next January 1 (the whole year)."""
        year += BASE.year
        paid_before = min(date(year, 1, 1) + timedelta(days=day), date(year + 1, 1, 1))
        if (vendor, year) in self.carried:
            with pytest.raises(psycopg.errors.UniqueViolation, match="carried_over_pkey"):
                carry_over_vendor_payments(
                    self.conns[role],
                    self.pmc_id,
                    self.vendors[vendor][0],
                    year,
                    paid_before,
                    amount,
                )
        else:
            carry_over_vendor_payments(
                self.conns[role], self.pmc_id, self.vendors[vendor][0], year, paid_before, amount
            )
            self.carried[(vendor, year)] = (paid_before, amount)

    @rule(data=st.data(), role=st.sampled_from(["owner", "app", "ai"]))
    def check_vendor_payments(self, data, role):
        """What each vendor was paid in a year (UTC), by payment date, and what the old system
        paid it that year before the cutover, as the model has it: mostly a year with payments,
        now and then one either side."""
        years = sorted(
            {when.year for _, when in self.paid.values()} | {y for _, y in self.carried}
        ) or [2000]
        if not (self.paid or self.carried) or data.draw(st.integers(0, 3)) == 0:
            years = [*range(years[0] - 1, years[-1] + 2)]
        year = data.draw(st.sampled_from(years))
        rows = (
            self.conns[role]
            .execute(
                "SELECT item, vendor, payments, paid FROM trust_report_vendor_payments(%s, %s)"
                " WHERE item IN ('vendor', 'carried over', 'total')",
                (self.pmc_id, year),
            )
            .fetchall()
        )
        paid: dict[int, tuple[int, Decimal]] = {}
        for bill_id, (_, when) in self.paid.items():
            if when.astimezone(UTC).year == year:
                vendor = self.bills[bill_id].vendor
                count, total = paid.get(vendor, (0, Decimal(0)))
                paid[vendor] = (count + 1, total + self.bills[bill_id].amount)
        carried = {v: kept for (v, y), kept in self.carried.items() if y == year}
        expected = []
        for vendor in sorted(set(paid) | set(carried)):
            count, here = paid.get(vendor, (0, Decimal(0)))
            before = carried.get(vendor)
            expected.append(
                ("vendor", f"Vendor {vendor + 1}", count, here + (before[1] if before else 0))
            )
            if before:
                expected.append(
                    ("carried over", f"paid in the old system before {before[0]}", None, before[1])
                )
        assert rows == [
            *expected,
            (
                "total",
                None,
                sum(c for c, _ in paid.values()),
                sum((t for _, t in paid.values()), Decimal(0))
                + sum((a for _, a in carried.values()), Decimal(0)),
            ),
        ]

    @invariant()
    def carried_over_payments_are_the_models(self):
        stored = self.conn.execute(
            "SELECT vendor_id, year, paid_before, amount FROM trust_vendor_payments_carried_over"
            " WHERE pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        vendor_ids = [vendor_id for vendor_id, _ in self.vendors]
        assert {(vendor_ids.index(v), y): (d, a) for v, y, d, a in stored} == self.carried

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
        declines = self.conn.execute(
            "SELECT bill_id, declined_on FROM trust_bill_declines WHERE pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert dict(declines) == self.declined
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
