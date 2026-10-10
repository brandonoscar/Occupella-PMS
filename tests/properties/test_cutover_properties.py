"""Property-based tests for cutover: a trust bank account opening with the balances it held in
another system.

Hypothesis opens the PMC's two trust bank accounts with random balances on random cutover dates
(now and then with a total that is a cent off, or an account of the other trust bank account),
sends openings again unchanged and changed, posts money in and out on random dates and approves
reconciliations on random periods. All in random order, through both the owner role and
trust_app; the AI's role is refused every opening. A Python model predicts every step: the
database must accept exactly what the model accepts and refuse the rest with the model's reason.

Invariants checked after every step (ci/registry.toml maps each to this test):
  - a trust bank account opens once, before any posting or approved reconciliation in it; the
    same opening again returns the original transfers, and a different one is refused;
  - its opening balances are posted from its cash at the start of the cutover date (UTC), so
    its book cash is their total, and the record of the opening is the model's;
  - no transfer touching it is dated before its cutover, whoever posts it;
  - its first reconciliation starts at the cutover, and each next one where the last ended;
  - every balance is the model's.

The rule check_the_trial_balance reads the trial balance as of a random day and compares every
line with the model's balances at the end of that day.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import make_pmc, open_with_balances, post, transfer_batch
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

AMOUNTS = st.integers(min_value=1, max_value=200_000).map(lambda cents: Decimal(cents) / 100)
BASE = date(2000, 1, 1)
DAYS = st.integers(min_value=0, max_value=90)
TIMES = st.integers(min_value=0, max_value=86_399)  # seconds into the day
ROLES = st.sampled_from(["owner", "app"])
PREPARERS = st.sampled_from(["Preparer 1", "Preparer 2"])
CENT = Decimal("0.01")


def midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=UTC)


def sides(balance):
    """A balance as the trial balance prints it: (debit, credit)."""
    return max(-balance, Decimal(0)), max(balance, Decimal(0))


@dataclass
class Opening:
    opened_on: date
    balances: dict[str, Decimal]
    entered_by: str
    transfer_ids: list[str]


@dataclass
class Bank:
    bank_id: uuid.UUID
    name: str
    cash: str
    held: list[str]  # the accounts that hold money in it
    opening: Opening | None = None
    closed_through: datetime | None = None
    posted: bool = False  # a transfer other than its opening touched it

    @property
    def opened_at(self):
        return None if self.opening is None else midnight(self.opening.opened_on)

    @property
    def next_period_start(self):
        return self.closed_through or self.opened_at


class CutoverMachine(RuleBasedStateMachine):
    def __init__(self, owner_conn, app_conn, ai_conn):
        super().__init__()
        self.conns = {"owner": owner_conn, "app": app_conn, "ai": ai_conn}
        self.conn = owner_conn
        pmc = make_pmc(owner_conn, owners=2)
        self.pmc, self.pmc_id = pmc, pmc.pmc_id
        self.banks = {
            "operating": Bank(
                pmc.operating_bank_id,
                "Synthetic operating trust account (operating)",
                pmc.operating_cash,
                [o.account for o in pmc.owners]
                + [pmc.prepaid_rent, pmc.vendor_payable, pmc.pmc_income],
            ),
            "deposit": Bank(
                pmc.deposit_bank_id,
                "Synthetic security_deposit trust account (security_deposit)",
                pmc.deposit_cash,
                [pmc.tenant_deposit],
            ),
        }
        # (source, target, amount, event_at) per transfer
        self.transfers: list[tuple[str, str, Decimal, datetime]] = []

    # --- the model -----------------------------------------------------------------------

    def record(self, source, target, amount, event_at):
        self.transfers.append((source, target, amount, event_at))

    def held_on(self, account, cutoff=None):
        return sum(
            (
                (amount if target == account else -amount)
                for source, target, amount, when in self.transfers
                if account in (source, target) and (cutoff is None or when < cutoff)
            ),
            Decimal(0),
        )

    def open(self, role, bank, opened_on, balances, book_cash, entered_by):
        return open_with_balances(
            self.conns[role],
            self.pmc_id,
            bank.bank_id,
            opened_on,
            {account: str(amount) for account, amount in balances.items()},
            book_cash,
            entered_by,
        )

    # --- rules ---------------------------------------------------------------------------

    @rule(
        data=st.data(),
        role=ROLES,
        which=st.sampled_from(["operating", "deposit"]),
        day=DAYS,
        entered_by=PREPARERS,
        mistake=st.sampled_from([None, None, None, None, "a cent off", "a stray account"]),
    )
    def open_a_trust_bank_account(self, data, role, which, day, entered_by, mistake):
        bank = self.banks[which]
        other = self.banks["deposit" if which == "operating" else "operating"]
        balances = data.draw(st.dictionaries(st.sampled_from(bank.held), AMOUNTS, min_size=1))
        book_cash = sum(balances.values(), Decimal(0))
        if mistake == "a cent off":
            book_cash += CENT
        if mistake == "a stray account":
            balances = {**balances, other.held[0]: Decimal("1.00")}
            book_cash += Decimal("1.00")
        opened_on = BASE + timedelta(days=day)

        def send():
            return self.open(role, bank, opened_on, balances, book_cash, entered_by)

        if mistake == "a stray account":
            with pytest.raises(psycopg.errors.InvalidParameterValue, match="not an account held"):
                send()
        elif mistake == "a cent off":
            with pytest.raises(psycopg.errors.CheckViolation, match="add up to"):
                send()
        elif bank.opening is not None:
            same = (opened_on, balances, entered_by) == (
                bank.opening.opened_on,
                bank.opening.balances,
                bank.opening.entered_by,
            )
            if same:
                assert send() == bank.opening.transfer_ids
            else:
                with pytest.raises(psycopg.errors.UniqueViolation, match="already opened"):
                    send()
        elif bank.posted or bank.closed_through is not None:
            with pytest.raises(psycopg.errors.CheckViolation, match="come first"):
                send()
        else:
            transfer_ids = send()
            bank.opening = Opening(opened_on, balances, entered_by, transfer_ids)
            # One transfer per account, from cash, in byte order of the account id.
            for account in sorted(balances, key=lambda a: a.encode()):
                self.record(bank.cash, account, balances[account], midnight(opened_on))

    @precondition(lambda self: any(b.opening for b in self.banks.values()))
    @rule(data=st.data(), role=ROLES, change=st.sampled_from([None, "date", "amount", "who"]))
    def send_an_opening_again(self, data, role, change):
        bank = data.draw(st.sampled_from([b for b in self.banks.values() if b.opening]))
        assert bank.opening is not None
        opened_on, balances = bank.opening.opened_on, dict(bank.opening.balances)
        entered_by = bank.opening.entered_by
        if change == "date":
            opened_on += timedelta(days=data.draw(st.sampled_from([-1, 1])))
        if change == "amount":
            first = sorted(balances)[0]
            balances[first] += CENT
        if change == "who":
            entered_by += " (again)"

        def send():
            return self.open(role, bank, opened_on, balances, sum(balances.values()), entered_by)

        if change is None:
            assert send() == bank.opening.transfer_ids
        else:
            with pytest.raises(psycopg.errors.UniqueViolation, match="already opened"):
                send()

    @rule(
        data=st.data(),
        role=ROLES,
        which=st.sampled_from(["operating", "deposit"]),
        amount=AMOUNTS,
        day=DAYS,
        second=TIMES,
        direction=st.sampled_from(["in", "out"]),
    )
    def post_money(self, data, role, which, amount, day, second, direction):
        bank = self.banks[which]
        account = data.draw(st.sampled_from(bank.held))
        if direction == "out":
            amount = min(amount, self.held_on(account))
        if amount <= 0:
            return
        source, target = (bank.cash, account) if direction == "in" else (account, bank.cash)
        when = midnight(BASE + timedelta(days=day)) + timedelta(seconds=second)

        def send():
            if role == "owner":
                transfer_batch(self.conn, [(source, target, amount)], when)
            else:
                post(
                    self.conns["app"],
                    self.pmc_id,
                    str(uuid.uuid4()),
                    [(source, target, amount)],
                    when,
                )

        if bank.closed_through is not None and when < bank.closed_through:
            with pytest.raises(psycopg.errors.CheckViolation, match="reconciled and closed"):
                send()
        elif bank.opened_at is not None and when < bank.opened_at:
            with pytest.raises(psycopg.errors.CheckViolation, match="balances carried over"):
                send()
        else:
            send()
            self.record(source, target, amount, when)
            bank.posted = True

    @rule(
        data=st.data(),
        role=ROLES,
        which=st.sampled_from(["operating", "deposit"]),
        start=st.sampled_from(["the next", "a day early", "a day late", "any"]),
        length=st.integers(min_value=1, max_value=40),
    )
    def approve_a_reconciliation(self, data, role, which, start, length):
        bank = self.banks[which]
        expected = bank.next_period_start
        if expected is None or start == "any":
            period_start = midnight(BASE + timedelta(days=data.draw(DAYS)))
        else:
            shift = {"the next": 0, "a day early": -1, "a day late": 1}[start]
            period_start = expected + timedelta(days=shift)
        period_end = period_start + timedelta(days=length)

        def send():
            self.conns[role].execute(
                "SELECT trust_approve_reconciliation(%s, %s, %s, %s, 0, 'Preparer 1',"
                " 'Approver 1')",
                (self.pmc_id, bank.bank_id, period_start, period_end),
            )

        if expected is not None and period_start != expected:
            with pytest.raises(psycopg.errors.InvalidParameterValue, match="next period"):
                send()
        else:
            send()
            bank.closed_through = period_end

    @rule(which=st.sampled_from(["operating", "deposit"]), day=DAYS)
    def the_ai_role_cannot_open_a_trust_bank_account(self, which, day):
        bank = self.banks[which]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            self.open("ai", bank, BASE + timedelta(days=day), {bank.held[0]: CENT}, CENT, "AI")

    @rule(day=st.integers(min_value=-1, max_value=91), role=st.sampled_from(["owner", "app", "ai"]))
    def check_the_trial_balance(self, day, role):
        as_of = BASE + timedelta(days=day)
        cutoff = midnight(as_of + timedelta(days=1))
        rows = (
            self.conns[role]
            .execute(
                "SELECT item, bank, debit, credit FROM trust_report_trial_balance(%s, %s)",
                (self.pmc_id, as_of),
            )
            .fetchall()
        )

        expected = []
        for bank in self.banks.values():
            for account in [bank.cash, *bank.held]:
                held = self.held_on(account, cutoff)
                if held != 0 or account == bank.cash:
                    expected.append((bank.name, *sides(held)))
        assert sorted(r[1:] for r in rows if r[0] == "account") == sorted(expected)

    # --- invariants ----------------------------------------------------------------------

    @invariant()
    def balances_are_the_models(self):
        for bank in self.banks.values():
            for account in [bank.cash, *bank.held]:
                (held,) = self.conn.execute(
                    "SELECT balance FROM pgledger_accounts WHERE id = %s", (account,)
                ).fetchone()
                assert held == self.held_on(account)

    @invariant()
    def openings_are_the_models(self):
        for bank in self.banks.values():
            record = self.conn.execute(
                "SELECT o.opened_on, o.balances, o.book_cash, o.entered_by, o.transfer_ids,"
                " b.opened_at FROM trust_bank_accounts b"
                " LEFT JOIN trust_opening_balances o ON o.bank_account_id = b.id"
                " WHERE b.id = %s",
                (bank.bank_id,),
            ).fetchone()
            if bank.opening is None:
                assert record == (None,) * 6
                continue
            opened_on, balances, book_cash, entered_by, transfer_ids, opened_at = record
            assert (opened_on, entered_by, transfer_ids, opened_at) == (
                bank.opening.opened_on,
                bank.opening.entered_by,
                bank.opening.transfer_ids,
                bank.opened_at,
            )
            assert {a: Decimal(str(v)) for a, v in balances.items()} == bank.opening.balances
            assert book_cash == sum(bank.opening.balances.values())

    @invariant()
    def nothing_is_dated_before_a_cutover(self):
        (early,) = self.conn.execute(
            "SELECT count(*) FROM pgledger_transfers tr"
            " JOIN trust_ledger_accounts t"
            "   ON t.ledger_account_id IN (tr.from_account_id, tr.to_account_id)"
            " JOIN trust_bank_accounts b ON b.id = t.bank_account_id"
            " WHERE b.pmc_id = %s AND tr.event_at < b.opened_at",
            (self.pmc_id,),
        ).fetchone()
        assert early == 0

    @invariant()
    def periods_start_at_the_cutover(self):
        for bank in self.banks.values():
            periods = self.conn.execute(
                "SELECT period_start, period_end FROM trust_reconciliations"
                " WHERE bank_account_id = %s ORDER BY period_end",
                (bank.bank_id,),
            ).fetchall()
            if periods and bank.opening is not None:
                assert periods[0][0] == bank.opened_at
            for (_, end), (start, _) in zip(periods, periods[1:], strict=False):
                assert start == end


def test_cutover_invariants_hold_under_random_openings_postings_and_reconciliations(
    database_url,
):
    with (
        psycopg.connect(database_url, autocommit=True) as owner_conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
        psycopg.connect(database_url, autocommit=True) as ai_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        ai_conn.execute("SET ROLE trust_ai_agent")
        run_state_machine_as_test(lambda: CutoverMachine(owner_conn, app_conn, ai_conn))
