"""Property-based tests for owner draws prepared on the draw day and the ACH files the PMS writes
(migration 20261024000153, ADR 0010 phase 1b).

Hypothesis gives two owners money and reserves, enters bills against them, changes the company's
draw day, and runs the nightly step on days some days apart. A model keeps each owner's balance,
reserve and bills not yet paid. After every step:
  - a month has at most one prepared run, made on the first night on or after its draw day;
  - each line is what the account had available that night (its balance less its reserve and its
    bills dated by then), only those above zero, and how its owner is paid;
  - preparing moves no money: every balance is the model's and the trust bank account ties out.

And for any credits: an ACH file's records are all 94 characters in blocks of ten, its batch and
file controls count the entries, sum their credits and hash their banks as NACHA asks, and its
trace numbers run from 1.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
from helpers import add_agreement, balance, bank_sums, contribute, enter_bill, make_pmc
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
    run_state_machine_as_test,
)

from pms.payouts import nacha

MONEY = st.integers(min_value=1, max_value=50_000).map(lambda c: Decimal(c) / 100)


class DrawMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn

    @initialize(reserves=st.lists(st.integers(0, 20_000), min_size=2, max_size=2))
    def a_company_with_two_owners(self, reserves):
        pmc = make_pmc(self.conn)
        self.pmc = pmc
        self.reserve = {}
        for owner, cents in zip(pmc.owners, reserves, strict=True):
            self.reserve[owner.account] = Decimal(cents) / 100
            add_agreement(
                self.conn,
                pmc.pmc_id,
                owner.account,
                date(2026, 1, 1),
                reserve=self.reserve[owner.account],
                approval_limit="100000",
            )
        self.today = date(2026, 1, 1)
        self.balances = {owner.account: Decimal("0.00") for owner in pmc.owners}
        self.bills: list[tuple[str, date, Decimal]] = []  # (account, bill date, amount)
        self.draw_day: int | None = None
        self.prepared: dict[date, date] = {}  # month: the day its run was prepared
        self.n = 0

    def at(self):
        return datetime(self.today.year, self.today.month, self.today.day, tzinfo=UTC)

    @rule(owner=st.integers(0, 1), amount=MONEY)
    def an_owner_puts_money_in(self, owner, amount):
        account = self.pmc.owners[owner].account
        self.n += 1
        contribute(self.conn, self.pmc.pmc_id, account, f"in-{self.n}", amount, self.at())
        self.balances[account] += amount

    @rule(owner=st.integers(0, 1), amount=MONEY, ahead=st.integers(0, 40))
    def a_bill_comes_in(self, owner, amount, ahead):
        account = self.pmc.owners[owner].account
        self.n += 1
        dated = self.today + timedelta(days=ahead)
        enter_bill(
            self.conn,
            self.pmc.pmc_id,
            self.pmc.vendor_id,
            account,
            f"B-{self.n}",
            dated,
            dated + timedelta(days=30),
            amount,
        )
        self.bills.append((account, dated, amount))

    @rule(day=st.one_of(st.none(), st.integers(1, 28)))
    def staff_set_the_draw_day(self, day):
        self.conn.execute(
            "INSERT INTO trust_owner_draw_settings (pmc_id, draw_day) VALUES (%s, %s)",
            (self.pmc.pmc_id, day),
        )
        self.draw_day = day

    @rule(days=st.integers(1, 20))
    def a_night_passes(self, days):
        self.today += timedelta(days=days)
        run = self.conn.execute(
            "SELECT trust_prepare_owner_draws(%s, %s)", (self.pmc.pmc_id, self.today)
        ).fetchone()[0]
        month = self.today.replace(day=1)
        expected = (
            self.draw_day is not None
            and self.today.day >= self.draw_day
            and month not in self.prepared
        )
        assert (run is not None) == expected
        if run is None:
            return
        self.prepared[month] = self.today
        want = {}
        for account, held in self.balances.items():
            bills = sum(
                (a for acc, dated, a in self.bills if acc == account and dated <= self.today),
                Decimal(0),
            )
            available = held - self.reserve[account] - bills
            if available > 0:
                want[account] = (available, "check")
        got = {
            account: (amount, method)
            for account, amount, method in self.conn.execute(
                "SELECT ledger_account_id, amount, method FROM trust_draw_run_lines"
                " WHERE run_id = %s",
                (run,),
            ).fetchall()
        }
        assert got == want

    @invariant()
    def nothing_moves_and_one_run_a_month(self):
        if not hasattr(self, "pmc"):
            return
        for account, held in self.balances.items():
            assert balance(self.conn, account) == held
        assert all(total == 0 for total in bank_sums(self.conn, self.pmc.pmc_id).values())
        assert (
            dict(
                self.conn.execute(
                    "SELECT month, prepared_on FROM trust_draw_runs WHERE pmc_id = %s",
                    (self.pmc.pmc_id,),
                ).fetchall()
            )
            == self.prepared
        )
        assert (
            self.conn.execute(
                "SELECT count(*) FROM trust_owner_draws WHERE pmc_id = %s", (self.pmc.pmc_id,)
            ).fetchone()[0]
            == 0
        )


def test_draws_prepared_on_the_draw_day_are_what_each_owner_has_available(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: DrawMachine(conn))


ROUTINGS = (
    st.integers(0, 999_999)
    .map(lambda n: f"00{n:06d}")
    .map(
        lambda p: (
            p
            + str(
                next(
                    c
                    for c in range(10)
                    if (
                        3 * (int(p[0]) + int(p[3]) + int(p[6]))
                        + 7 * (int(p[1]) + int(p[4]) + int(p[7]))
                        + int(p[2])
                        + int(p[5])
                        + c
                    )
                    % 10
                    == 0
                )
            )
        )
    )
)
ENTRIES = st.lists(
    st.builds(
        nacha.Entry,
        routing_number=ROUTINGS,
        account_number=st.from_regex(r"[0-9]{4,17}", fullmatch=True),
        savings=st.booleans(),
        amount=st.integers(1, 99_999_999).map(lambda c: Decimal(c) / 100),
        name=st.text(min_size=1, max_size=40),
        reference=st.text(max_size=20),
    ),
    min_size=1,
    max_size=25,
)


@given(batches=st.lists(ENTRIES, min_size=1, max_size=3))
def test_an_ach_file_adds_up_as_nacha_asks(batches):
    origin = nacha.Origin(
        "001234561", "Synthetic Bank", "1000000000", "Golden", "1000000000", "Golden"
    )
    text = nacha.write(
        origin,
        [nacha.Batch("PPD", "OWNER DRAW", date(2026, 10, 26), entries) for entries in batches],
        datetime(2026, 10, 24, 9, 30),
    )
    lines = text.splitlines()
    assert len(lines) % 10 == 0 and all(len(line) == 94 for line in lines)
    entries = [line for line in lines if line.startswith("6")]
    every = [e for batch in batches for e in batch]
    assert len(entries) == len(every)
    assert [int(e[87:]) for e in entries] == list(range(1, len(every) + 1))
    credit = sum(int(e.amount * 100) for e in every)
    entry_hash = sum(int(e.routing_number[:8]) for e in every) % 10**10
    total = next(line for line in lines if line.startswith("9") and line != "9" * 94)
    assert int(total[1:7]) == len(batches) and int(total[13:21]) == len(every)
    assert int(total[21:31]) == entry_hash and int(total[43:55]) == credit
    assert int(total[7:13]) == -(-(2 + 2 * len(batches) + len(every)) // 10)
    controls = [line for line in lines if line.startswith("8")]
    for control, batch in zip(controls, batches, strict=True):
        assert int(control[4:10]) == len(batch)
        assert int(control[32:44]) == sum(int(e.amount * 100) for e in batch)
