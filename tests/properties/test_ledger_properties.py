"""Property-based tests: Hypothesis throws random sequences of postings at the ledger and checks
the money invariants after every single step.

Each example is a fresh synthetic PMC with two trust bank accounts, three owners and every
account kind. A Python model tracks what each balance should be, so every posting is predicted
before it is sent: the database must accept exactly the postings the model accepts, refuse the
rest, and leave nothing behind when it refuses. Postings go through both the owner role and
trust_app, the role the application uses.

Invariants checked after every step (ci/registry.toml maps each to this test):
  - debits equal credits: each transfer has one -amount and one +amount entry;
  - no held account (anything but bank_cash) is ever below zero, and balances match the model;
  - per PMC, book cash equals the sum of every balance held for someone;
  - history is append-only: rows once written never change, and a correction is a new,
    reversing transfer;
  - every entry carries the running balance and the account's version, chained from zero.

Run more examples locally with HYPOTHESIS_PROFILE=nightly; a failure prints the shortest
sequence of steps that breaks an invariant.
"""

from decimal import Decimal

import psycopg
import pytest
from helpers import make_pmc, running_balance_breaks, transfer, transfer_batch
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

CENT = Decimal("0.01")
AMOUNTS = st.integers(min_value=1, max_value=1_000_000).map(lambda cents: Decimal(cents) / 100)

ROWS = """
SELECT 'transfer', t.id, t.from_account_id || '>' || t.to_account_id, t.amount, NULL::numeric
FROM pgledger_transfers t
WHERE t.from_account_id = ANY(%(ids)s) OR t.to_account_id = ANY(%(ids)s)
UNION ALL
SELECT 'entry', e.id, e.account_id || '@' || e.transfer_id, e.amount, e.account_current_balance
FROM pgledger_entries e
WHERE e.account_id = ANY(%(ids)s)
"""

UNBALANCED = """
SELECT t.id
FROM pgledger_transfers t
LEFT JOIN pgledger_entries e ON e.transfer_id = t.id
WHERE t.from_account_id = ANY(%(ids)s) OR t.to_account_id = ANY(%(ids)s)
GROUP BY t.id, t.from_account_id, t.to_account_id, t.amount
HAVING count(e.id) <> 2
    OR coalesce(sum(e.amount), 1) <> 0
    OR count(*) FILTER (WHERE e.account_id = t.from_account_id AND e.amount = -t.amount) <> 1
    OR count(*) FILTER (WHERE e.account_id = t.to_account_id AND e.amount = t.amount) <> 1
"""

BALANCES = """
SELECT a.id, a.balance, coalesce(sum(e.amount), 0)
FROM pgledger_accounts a
LEFT JOIN pgledger_entries e ON e.account_id = a.id
WHERE a.id = ANY(%(ids)s)
GROUP BY a.id, a.balance
"""


class LedgerMachine(RuleBasedStateMachine):
    def __init__(self, owner_conn, app_conn):
        super().__init__()
        self.conns = {"owner": owner_conn, "app": app_conn}
        self.conn = owner_conn
        pmc = make_pmc(owner_conn, owners=3)
        self.kinds = {
            pmc.operating_cash: "bank_cash",
            pmc.deposit_cash: "bank_cash",
            pmc.pmc_income: "pmc_income",
            pmc.tenant_deposit: "tenant_deposit",
            **{owner.account: "owner_property" for owner in pmc.owners},
        }
        self.accounts = sorted(self.kinds)
        self.ids = {"ids": self.accounts}
        self.model = dict.fromkeys(self.accounts, Decimal(0))
        self.posted: list[tuple[str, tuple[str, str, Decimal]]] = []
        self.seen_rows: dict[tuple[str, str], tuple] = {}
        self.other_pmc_owner = None

    # --- helpers -------------------------------------------------------------------------

    def amount_for(self, data, source):
        """Mostly random amounts, plus the exact balance and one cent more, where overdrafts
        and empty-to-zero edge cases live."""
        held = self.model[source]
        choices = [AMOUNTS]
        if held > 0:
            choices += [st.just(held), st.just(held + CENT)]
        return data.draw(st.one_of(*choices))

    def draw_request(self, data):
        source = data.draw(st.sampled_from(self.accounts))
        target = data.draw(st.sampled_from([a for a in self.accounts if a != source]))
        return (source, target, self.amount_for(data, source))

    def snapshot(self):
        return sorted(self.conn.execute(ROWS, self.ids).fetchall()), sorted(
            self.conn.execute(BALANCES, self.ids).fetchall()
        )

    def attempt(self, conn, requests):
        """Post a batch the model predicts; all of it lands, or none of it."""
        expected = dict(self.model)
        allowed = True
        for source, target, amount in requests:
            expected[source] -= amount
            expected[target] += amount
            if self.kinds[source] != "bank_cash" and expected[source] < 0:
                allowed = False
                break

        if allowed:
            ids = transfer_batch(conn, requests)
            assert len(ids) == len(requests)
            self.model = expected
            self.posted.extend(zip(ids, requests, strict=True))
        else:
            before = self.snapshot()
            with pytest.raises(psycopg.errors.CheckViolation, match="below zero"):
                transfer_batch(conn, requests)
            assert self.snapshot() == before

    # --- rules: what the test may do next -------------------------------------------------

    @rule(data=st.data(), role=st.sampled_from(["owner", "app"]))
    def post(self, data, role):
        self.attempt(self.conns[role], [self.draw_request(data)])

    @rule(data=st.data(), size=st.integers(min_value=2, max_value=4))
    def post_batch(self, data, size):
        self.attempt(self.conns["app"], [self.draw_request(data) for _ in range(size)])

    @precondition(lambda self: self.posted)
    @rule(data=st.data())
    def correct_with_a_reversal(self, data):
        _, (source, target, amount) = data.draw(st.sampled_from(self.posted))
        self.attempt(self.conns["app"], [(target, source, amount)])

    @rule(amount=AMOUNTS)
    def try_to_cross_pmcs(self, amount):
        if self.other_pmc_owner is None:
            self.other_pmc_owner = make_pmc(self.conn, owners=1).owners[0].account
        source = next(a for a in self.accounts if self.kinds[a] == "bank_cash")
        before = self.snapshot()
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="crosses PMCs"):
            transfer(self.conn, source, self.other_pmc_owner, amount)
        assert self.snapshot() == before

    @precondition(lambda self: self.posted)
    @rule(
        data=st.data(),
        table=st.sampled_from(["pgledger_transfers", "pgledger_entries"]),
        statement=st.sampled_from(["UPDATE", "DELETE"]),
    )
    def try_to_rewrite_history(self, data, table, statement):
        transfer_id = data.draw(st.sampled_from(self.posted))[0]
        column = "id" if table == "pgledger_transfers" else "transfer_id"
        query = (
            f"UPDATE {table} SET amount = amount + 1 WHERE {column} = %s"
            if statement == "UPDATE"
            else f"DELETE FROM {table} WHERE {column} = %s"
        )
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            self.conn.execute(query, (transfer_id,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            self.conns["app"].execute(query, (transfer_id,))

    # --- invariants: checked after every step ---------------------------------------------

    @invariant()
    def debits_equal_credits(self):
        assert self.conn.execute(UNBALANCED, self.ids).fetchall() == []

    @invariant()
    def held_accounts_never_go_below_zero(self):
        rows = self.conn.execute(BALANCES, self.ids).fetchall()
        balances = {account: amount for account, amount, _ in rows}
        assert balances == self.model
        for account, amount in balances.items():
            if self.kinds[account] != "bank_cash":
                assert amount >= 0, (self.kinds[account], account, amount)

    @invariant()
    def book_cash_equals_held_balances(self):
        rows = self.conn.execute(BALANCES, self.ids).fetchall()
        cash = sum(amount for account, amount, _ in rows if self.kinds[account] == "bank_cash")
        held = sum(amount for account, amount, _ in rows if self.kinds[account] != "bank_cash")
        assert -cash == held

    @invariant()
    def history_is_append_only(self):
        rows = self.conn.execute(ROWS, self.ids).fetchall()
        current = {(row[0], row[1]): row for row in rows}
        for key, row in self.seen_rows.items():
            assert current.get(key) == row, f"{key} changed or vanished"
        self.seen_rows = current
        for account, amount, entries_total in self.conn.execute(BALANCES, self.ids).fetchall():
            assert amount == entries_total, account

    @invariant()
    def entries_carry_the_running_balance(self):
        assert running_balance_breaks(self.conn, self.accounts) == []


def test_ledger_invariants_hold_under_random_postings(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as owner_conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        run_state_machine_as_test(lambda: LedgerMachine(owner_conn, app_conn))
