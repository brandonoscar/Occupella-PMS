"""Property-based tests for journal entries (migration 20261015000053).

Hypothesis has three staff members of one PMC (a bookkeeper with no role and two managers who
hold approve) propose entries of random lines between its ledger accounts, in both trust bank
accounts, and post and decline them in random order, as the app (trust_app) naming each one. A
model predicts what each step does; the database must agree, refusing what it refuses with the
reason it gives.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - every balance is the model's: only posted entries moved money, each once;
  - a posted entry was posted by a staff member who held approve and did not propose it, and
    its transfers are its lines, in order, dated its day;
  - a declined or undecided entry moved nothing;
  - no account but a bank's cash is below zero, and each trust bank account ties out.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
from helpers import (
    decline_entry,
    make_pmc,
    post_entry,
    propose_entry,
    staff_named,
    transfer_batch,
)
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    invariant,
    rule,
)
from hypothesis.stateful import (
    run_state_machine_as_test as run,
)

STAFF = ("Bookkeeper 1", "Manager 1", "Manager 2")
APPROVERS = {1, 2}
WHO = st.integers(min_value=0, max_value=len(STAFF) - 1)
DAY = date(2026, 3, 1)
CENTS = Decimal("0.01")


@dataclass
class Entry:
    id: object
    by: int
    lines: list[tuple[int, int, Decimal]]
    decision: str | None = None
    transfers: list[str] | None = None


class JournalMachine(RuleBasedStateMachine):
    entries = Bundle("entries")

    def __init__(self, conn, app_conn):
        super().__init__()
        self.conn = conn
        self.app = app_conn
        pmc = make_pmc(conn)
        self.pmc = pmc.pmc_id
        self.staff = [
            staff_named(conn, self.pmc, name, ["approve"] if n in APPROVERS else [])
            for n, name in enumerate(STAFF)
        ]
        # The accounts an entry moves money between, and the trust bank account each is in.
        self.accounts = [
            pmc.operating_cash,
            pmc.owners[0].account,
            pmc.owners[1].account,
            pmc.pmc_income,
            pmc.prepaid_rent,
            pmc.deposit_cash,
            pmc.tenant_deposit,
        ]
        self.bank = ["operating"] * 5 + ["deposit"] * 2
        self.cash = {0, 5}
        # 1000.00 held in each account.
        transfer_batch(
            conn,
            [
                (self.accounts[5 if n > 4 else 0], a, "1000.00")
                for n, a in enumerate(self.accounts)
                if n not in self.cash
            ],
            datetime(2026, 2, 1, tzinfo=UTC),
        )
        # Model
        self.balances = [
            Decimal(b) for b in ("-4000", "1000", "1000", "1000", "1000", "-1000", "1000")
        ]
        self.all: list[Entry] = []

    @staticmethod
    def refused(error, work):
        """The first line of the refusal `work` must meet (autocommit: nothing was written)."""
        try:
            work()
        except error as refused:
            return str(refused).splitlines()[0]
        raise AssertionError(f"expected {error.__name__}")

    @rule(
        target=entries,
        by=WHO,
        lines=st.lists(
            st.tuples(
                st.integers(0, 6), st.integers(0, 6), st.integers(min_value=1, max_value=150000)
            ).filter(lambda line: line[0] != line[1]),
            min_size=1,
            max_size=3,
        ),
    )
    def propose(self, by, lines):
        moved = [(f, t, Decimal(cents) * CENTS) for f, t, cents in lines]
        entry_id = propose_entry(
            self.app,
            self.pmc,
            self.staff[by],
            DAY,
            "Adjusting entry",
            [(self.accounts[f], self.accounts[t], str(amount)) for f, t, amount in moved],
        )
        entry = Entry(entry_id, by, moved)
        self.all.append(entry)
        event("entry proposed")
        return entry

    def outcome(self, lines):
        """What posting these lines does: the balances after, or why it is refused."""
        after = list(self.balances)
        for f, t, amount in lines:
            after[f] -= amount
            after[t] += amount
            if any(after[n] < 0 for n in range(len(after)) if n not in self.cash):
                return "below zero", None
        for bank in ("operating", "deposit"):
            if sum(b for n, b in enumerate(after) if self.bank[n] == bank) != 0:
                return "does not tie out", after
        return None, after

    @rule(entry=entries, by=WHO)
    def post(self, entry, by):
        def work():
            return post_entry(self.app, self.pmc, entry.id, self.staff[by])

        if entry.decision == "declined":
            event("post refused: declined")
            assert "was declined" in self.refused(psycopg.errors.CheckViolation, work)
            return
        if entry.decision == "posted":
            event("post again: the same transfers")
            assert work() == entry.transfers
            return
        if by == entry.by:
            event("post refused: the proposer")
            assert "who proposed" in self.refused(psycopg.errors.InsufficientPrivilege, work)
            return
        refusal, after = self.outcome(entry.lines)
        if refusal == "below zero":
            event("post refused: below zero")
            assert "below zero" in self.refused(psycopg.errors.CheckViolation, work)
        elif by not in APPROVERS:
            event("post refused: may not approve")
            assert "may not post a journal entry" in self.refused(
                psycopg.errors.InsufficientPrivilege, work
            )
        elif refusal == "does not tie out":
            event("post refused: does not tie out")
            assert "does not tie out" in self.refused(psycopg.errors.CheckViolation, work)
        else:
            event("entry posted")
            entry.transfers = work()
            entry.decision = "posted"
            assert after is not None
            self.balances = after

    @rule(entry=entries, by=WHO)
    def decline(self, entry, by):
        def work():
            decline_entry(self.app, self.pmc, entry.id, self.staff[by])

        if entry.decision is not None:
            event("decline refused: decided already")
            assert "decided already" in self.refused(psycopg.errors.UniqueViolation, work)
        else:
            event("entry declined")
            work()
            entry.decision = "declined"

    @invariant()
    def balances_are_the_models(self):
        assert [
            self.conn.execute(
                "SELECT balance FROM pgledger_accounts WHERE id = %s", (a,)
            ).fetchone()[0]
            for a in self.accounts
        ] == self.balances

    @invariant()
    def only_posted_entries_moved_money_by_another_approver(self):
        for entry in self.all:
            row = self.conn.execute(
                "SELECT d.decision, d.decided_by, d.transfer_ids, e.proposed_by"
                " FROM trust_journal_entries AS e"
                " LEFT JOIN trust_journal_entry_decisions AS d ON d.entry_id = e.id"
                " WHERE e.id = %s",
                (entry.id,),
            ).fetchone()
            assert row[0] == entry.decision
            assert row[3] == self.staff[entry.by]
            moved = self.conn.execute(
                "SELECT t.from_account_id, t.to_account_id, t.amount, t.event_at"
                " FROM pgledger_transfers AS t"
                " LEFT JOIN trust_journal_entry_decisions AS d ON d.entry_id = %s"
                " LEFT JOIN unnest(d.transfer_ids) WITH ORDINALITY AS k (id, n) ON k.id = t.id"
                " WHERE t.metadata ->> 'journal_entry_id' = %s ORDER BY k.n",
                (entry.id, str(entry.id)),
            ).fetchall()
            if entry.decision != "posted":
                assert moved == []
                continue
            assert row[1] != row[3]
            assert row[1] in [self.staff[n] for n in APPROVERS]
            assert row[2] == entry.transfers
            assert moved == [
                (self.accounts[f], self.accounts[t], amount, datetime(2026, 3, 1, tzinfo=UTC))
                for f, t, amount in entry.lines
            ]


def test_journal_entries_move_money_only_when_another_approver_posts_them(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        run(lambda: JournalMachine(conn, app_conn))
