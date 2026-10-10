"""Property-based tests: Hypothesis throws random sequences of postings at the ledger and checks
the money invariants after every single step.

Each example is a fresh synthetic PMC with two trust bank accounts, three owners and every
account kind. A Python model tracks what each balance should be, so every posting is predicted
before it is sent: the database must accept exactly the postings the model accepts, refuse the
rest, and leave nothing behind when it refuses. Postings go through both the owner role and
trust_app, the role the application uses, which posts only with an idempotency key. A synthetic
clock dates every posting; it moves forward, reconciliations close periods behind it, and some
postings are dated back into them.

Invariants checked after every step (ci/registry.toml maps each to this test):
  - debits equal credits: each transfer has one -amount and one +amount entry;
  - no held account (anything but bank_cash) is ever below zero, and balances match the model;
  - per trust bank account, book cash equals the sum of every balance held for someone in it
    (owners, prepaid rent, security deposits, vendors, the PMC's fees): held money moves
    between trust bank accounts only together with its cash;
  - history is append-only: rows once written never change, and a correction is a new,
    reversing transfer;
  - every entry carries the running balance and the account's version, chained from zero;
  - an idempotency key posts once: retrying it returns the original transfers and writes
    nothing, and reusing it for a different posting is refused;
  - an approved reconciliation is locked: the record never changes, nothing dated inside its
    period is posted after it (so its book balance still holds), and periods follow one
    another with no gap or overlap;
  - every security deposit sits in the security-deposit trust account, which holds nothing
    else, so its cash is exactly the deposits held; no account opens in the wrong kind;
  - the three-way reconciliation report of every approved period agrees with the model: its
    trust journal is the book balance approved, its ledgers add up to that and list in byte
    order, and its only difference is the statement's;
  - an owner statement for any period agrees with the model: the opening balance, each posting
    in date order with the balance after it, the closing balance and the totals;
  - the trial balance and the security deposit register as of any day agree with the model:
    each bank's book cash against the balances it holds, the totals, and each deposit held;
  - the general ledger over any period agrees with the model: each account's opening balance,
    its entries in date order with the balance after each, its closing balance, and debits
    equal to credits;
  - the AI's role (trust_ai_agent) reads the same statement, and every posting it tries is
    refused with nothing written: the AI never moves money.

Run more examples locally with HYPOTHESIS_PROFILE=nightly; a failure prints the shortest
sequence of steps that breaks an invariant.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import approve, make_pmc, post, running_balance_breaks, transfer, transfer_batch
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
# A small pool, so keys come back often within one example.
KEYS = st.sampled_from([f"key-{n}" for n in range(4)])
# The synthetic clock starts here and moves at most 41 days a step, so even at 100 steps it
# stays years before the real now(): a reconciliation may only close a period that has ended.
CLOCK_START = datetime(2000, 1, 1, tzinfo=UTC)
FIRST_PERIOD_START = datetime(1999, 12, 1, tzinfo=UTC)
BACK = st.timedeltas(min_value=timedelta(microseconds=1), max_value=timedelta(days=90))
CLOSED = "reconciled and closed"

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

BOOKS_CHANGED_SINCE_APPROVAL = """
SELECT * FROM (
    SELECT r.id, r.book_balance, (
        SELECT coalesce(-sum(e.amount), 0)
        FROM trust_ledger_accounts t
        JOIN pgledger_entries e ON e.account_id = t.ledger_account_id
        JOIN pgledger_transfers tr ON tr.id = e.transfer_id
        WHERE t.bank_account_id = r.bank_account_id AND t.kind = 'bank_cash'
          AND tr.event_at < r.period_end
    ) AS books_now
    FROM trust_reconciliations r
    WHERE r.pmc_id = %s
) checked
WHERE book_balance <> books_now
"""


def sides(balance):
    """(debit, credit) of a trial balance line: money held is a credit, a shortfall a debit."""
    return (max(-balance, Decimal(0)), max(balance, Decimal(0)))


class LedgerMachine(RuleBasedStateMachine):
    def __init__(self, owner_conn, app_conn, ai_conn):
        super().__init__()
        self.conns = {"owner": owner_conn, "app": app_conn, "ai": ai_conn}
        self.conn = owner_conn
        pmc = make_pmc(owner_conn, owners=3)
        # "Owner 1", "owner 2", "Owner 3": byte order and language rules sort these differently,
        # so the reports' ordering is checked on any server that sorts by language rules.
        owner_conn.execute(
            "UPDATE trust_owners SET display_name = 'owner 2' WHERE id = %s",
            (pmc.owners[1].owner_id,),
        )
        self.pmc = pmc
        self.pmc_id = pmc.pmc_id
        self.tenant_id = owner_conn.execute(
            "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
            (pmc.tenant_deposit,),
        ).fetchone()[0]
        self.kinds = {
            pmc.operating_cash: "bank_cash",
            pmc.deposit_cash: "bank_cash",
            pmc.pmc_income: "pmc_income",
            pmc.tenant_deposit: "tenant_deposit",
            pmc.prepaid_rent: "prepaid_rent",
            pmc.vendor_payable: "vendor_payable",
            **{owner.account: "owner_property" for owner in pmc.owners},
        }
        self.accounts = sorted(self.kinds)
        # Each account's trust bank account, named by that account's bank_cash.
        self.bank = {account: pmc.cash_for(account) for account in self.accounts}
        self.bank_id = {
            pmc.operating_cash: pmc.operating_bank_id,
            pmc.deposit_cash: pmc.deposit_bank_id,
        }
        self.ids = {"ids": self.accounts}
        self.model = dict.fromkeys(self.accounts, Decimal(0))
        # (transfer id, request, event_at) of every transfer that landed.
        self.posted: list[tuple[str, tuple[str, str, Decimal], datetime]] = []
        self.seen_rows: dict[tuple[str, str], tuple] = {}
        self.other_pmc_owner = None
        # key -> (requests, event_at, transfer ids)
        self.keyed: dict[str, tuple[list, datetime, list[str]]] = {}
        self.fresh_keys = 0
        self.tried_without_a_key = False
        self.now = CLOCK_START
        self.closed: dict[str, datetime | None] = dict.fromkeys(self.bank_id)
        # (bank_cash, period_start, period_end, book balance) of every approved reconciliation.
        self.reconciled: list[tuple[str, datetime, datetime, Decimal]] = []

    # --- helpers -------------------------------------------------------------------------

    def amount_for(self, data, source):
        """Mostly random amounts, plus the exact balance and one cent more, where overdrafts
        and empty-to-zero edge cases live."""
        held = self.model[source]
        choices = [AMOUNTS]
        if held > 0:
            choices += [st.just(held), st.just(held + CENT)]
        return data.draw(st.one_of(*choices))

    def draw_request(self, data, bank=None):
        """Mostly inside one trust bank account; one in five crosses to the other, which the
        tie-out check refuses unless the cash moves in the same batch. With `bank`, the money
        comes from an account in that trust bank account."""
        sources = [a for a in self.accounts if bank is None or self.bank[a] == bank]
        source = data.draw(st.sampled_from(sources))
        cross = data.draw(st.integers(min_value=0, max_value=4)) == 0
        targets = [
            a for a in self.accounts if a != source and (self.bank[a] != self.bank[source]) == cross
        ]
        target = data.draw(st.sampled_from(targets))
        return (source, target, self.amount_for(data, source))

    def snapshot(self):
        return sorted(self.conn.execute(ROWS, self.ids).fetchall()), sorted(
            self.conn.execute(BALANCES, self.ids).fetchall()
        )

    def is_closed(self, account, when):
        closed_through = self.closed[self.bank[account]]
        return closed_through is not None and when < closed_through

    def attempt(self, role, requests, key=None, when=None):
        """Post a batch the model predicts, dated `when` (the clock by default); all of it
        lands, or none of it. The app always posts through trust_post_transfers with a key (a
        new one unless given), and a posting that lands files its key; the owner posts through
        pgledger directly when no key is given."""
        when = self.now if when is None else when
        if role == "app" and key is None:
            self.fresh_keys += 1
            key = f"fresh-{self.fresh_keys}"
        expected = dict(self.model)
        refusal = None
        # pgledger works through a batch in order: each transfer's balances are checked first,
        # then its row is inserted, which is when a closed period refuses it.
        for source, target, amount in requests:
            expected[source] -= amount
            expected[target] += amount
            if self.kinds[source] != "bank_cash" and expected[source] < 0:
                refusal = "below zero"
                break
            if self.is_closed(source, when) or self.is_closed(target, when):
                refusal = CLOSED
                break
        # Checked at commit: every trust bank account's balances still sum to zero.
        ties_out = all(
            sum(expected[a] for a in self.accounts if self.bank[a] == bank) == 0
            for bank in set(self.bank.values())
        )
        if refusal is None and not ties_out:
            refusal = "does not tie out"

        def send():
            if key is None:
                return transfer_batch(self.conns[role], requests, event_at=when)
            return post(self.conns[role], self.pmc_id, key, requests, event_at=when)

        if refusal is None:
            ids = send()
            assert len(ids) == len(requests)
            self.model = expected
            self.posted.extend((i, r, when) for i, r in zip(ids, requests, strict=True))
            if key is not None:
                self.keyed[key] = (requests, when, ids)
        else:
            before = self.snapshot()
            with pytest.raises(psycopg.errors.CheckViolation, match=refusal):
                send()
            assert self.snapshot() == before

    def book_balance(self, cash, period_end):
        """The cash the model says a trust bank account held at period_end."""
        book = Decimal(0)
        for _, (source, target, amount), when in self.posted:
            if when >= period_end:
                continue
            if source == cash:  # cash arrives: bank_cash pays out to an account held for someone
                book += amount
            elif target == cash:  # cash leaves
                book -= amount
        return book

    # --- rules: what the test may do next -------------------------------------------------

    @rule(data=st.data(), role=st.sampled_from(["owner", "app"]))
    def post(self, data, role):
        self.attempt(role, [self.draw_request(data)])

    @rule(data=st.data(), size=st.integers(min_value=2, max_value=4))
    def post_batch(self, data, size):
        self.attempt("app", [self.draw_request(data) for _ in range(size)])

    @rule(data=st.data())
    def move_between_bank_accounts(self, data):
        """Held money changes trust bank account with its cash, in one batch: a deposit kept for
        damages goes to the owner, and the cash goes from the deposit account to operating."""
        source = data.draw(
            st.sampled_from([a for a in self.accounts if self.kinds[a] != "bank_cash"])
        )
        target = data.draw(
            st.sampled_from(
                [
                    a
                    for a in self.accounts
                    if self.bank[a] != self.bank[source] and self.kinds[a] != "bank_cash"
                ]
            )
        )
        amount = self.amount_for(data, source)
        cash_move = (self.bank[target], self.bank[source], amount)
        self.attempt("app", [(source, target, amount), cash_move])

    @rule(data=st.data(), key=KEYS, retry=st.booleans())
    def post_with_key(self, data, key, retry):
        if key not in self.keyed:
            size = data.draw(st.integers(min_value=1, max_value=3))
            self.attempt("app", [self.draw_request(data) for _ in range(size)], key)
            return
        requests, when, ids = self.keyed[key]
        before = self.snapshot()
        if retry:
            # A retry after a timeout: same key, same posting. The original comes back, even
            # once a reconciliation has closed the period it is dated in.
            assert post(self.conns["app"], self.pmc_id, key, requests, event_at=when) == ids
        else:
            # The same key for a different posting is refused.
            source, target, amount = requests[0]
            changed = [(source, target, amount + CENT), *requests[1:]]
            with pytest.raises(psycopg.errors.UniqueViolation, match="different posting"):
                post(self.conns["app"], self.pmc_id, key, changed, event_at=when)
        assert self.snapshot() == before

    @precondition(lambda self: self.posted)
    @rule(data=st.data())
    def correct_with_a_reversal(self, data):
        # Dated today, so a correction to a closed period lands in the open one.
        _, (source, target, amount), _ = data.draw(st.sampled_from(self.posted))
        self.attempt("app", [(target, source, amount)])

    @rule(data=st.data(), role=st.sampled_from(["owner", "app"]), back=BACK)
    def post_backdated(self, data, role, back):
        """A posting dated before today: refused if that date is closed for either side."""
        self.attempt(role, [self.draw_request(data)], when=self.now - back)

    @precondition(lambda self: any(self.closed.values()))
    @rule(data=st.data(), role=st.sampled_from(["owner", "app"]), back=BACK)
    def try_to_post_into_a_closed_period(self, data, role, back):
        cash = data.draw(st.sampled_from([c for c, end in self.closed.items() if end]))
        self.attempt(role, [self.draw_request(data, bank=cash)], when=self.closed[cash] - back)

    @precondition(lambda self: not self.tried_without_a_key)  # once is enough per example
    @rule()
    def app_cannot_post_without_a_key(self):
        # pgledger's posting functions take no key: only the owner role may call them.
        self.tried_without_a_key = True
        source = next(a for a in self.accounts if self.kinds[a] == "bank_cash")
        target = next(a for a in self.accounts if self.bank[a] == source and a != source)
        before = self.snapshot()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            transfer_batch(self.conns["app"], [(source, target, CENT)], event_at=self.now)
        assert self.snapshot() == before

    @rule(days=st.integers(min_value=1, max_value=40), micros=st.integers(0, 86_399_999_999))
    def advance_clock(self, days, micros):
        self.now += timedelta(days=days, microseconds=micros)

    def next_period_start(self, cash):
        return self.closed[cash] or FIRST_PERIOD_START

    @precondition(lambda self: any(self.next_period_start(c) < self.now for c in self.closed))
    @rule(data=st.data(), role=st.sampled_from(["owner", "app"]), statement=AMOUNTS)
    def approve_reconciliation(self, data, role, statement):
        cash = data.draw(
            st.sampled_from(sorted(c for c in self.closed if self.next_period_start(c) < self.now))
        )
        start = self.next_period_start(cash)
        # Any end after the start, up to today: a period is approved once it has ended.
        end = start + data.draw(
            st.timedeltas(min_value=timedelta(microseconds=1), max_value=self.now - start)
        )
        approved = approve(self.conns[role], self.pmc_id, self.bank_id[cash], start, end, statement)
        book = self.book_balance(cash, end)
        row = self.conn.execute(
            "SELECT bank_account_id, period_start, period_end, statement_balance, book_balance"
            " FROM trust_reconciliations WHERE id = %s",
            (approved,),
        ).fetchone()
        assert row == (self.bank_id[cash], start, end, statement, book)
        self.closed[cash] = end
        self.reconciled.append((cash, start, end, book))

    @precondition(lambda self: any(self.closed.values()))
    @rule(data=st.data(), shift=BACK, later=st.booleans())
    def try_to_approve_out_of_sequence(self, data, shift, later):
        """A period that overlaps the last approved one, or leaves a gap after it."""
        cash = data.draw(st.sampled_from([c for c, end in self.closed.items() if end]))
        start = self.closed[cash] + shift if later else self.closed[cash] - shift
        before = self.reconciliations()
        with pytest.raises(psycopg.errors.InvalidParameterValue, match="next period"):
            approve(self.conn, self.pmc_id, self.bank_id[cash], start, start + shift)
        assert self.reconciliations() == before

    @precondition(lambda self: self.reconciled)
    @rule(
        statement=st.sampled_from(
            [
                "UPDATE trust_reconciliations SET period_end = period_start + interval '1 day'",
                "UPDATE trust_reconciliations SET book_balance = book_balance + 1",
                "DELETE FROM trust_reconciliations",
            ]
        )
    )
    def try_to_change_a_reconciliation(self, statement):
        where = f"{statement} WHERE pmc_id = %s"
        before = self.reconciliations()
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            self.conn.execute(where, (self.pmc_id,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            self.conns["app"].execute(where, (self.pmc_id,))
        reopen = "UPDATE trust_bank_accounts SET closed_through = NULL WHERE pmc_id = %s"
        with pytest.raises(psycopg.errors.RestrictViolation, match="newest approved"):
            self.conn.execute(reopen, (self.pmc_id,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            self.conns["app"].execute(reopen, (self.pmc_id,))
        assert self.reconciliations() == before

    def reconciliations(self):
        return self.conn.execute(
            "SELECT r.*, b.closed_through FROM trust_reconciliations r"
            " JOIN trust_bank_accounts b ON b.id = r.bank_account_id"
            " WHERE r.pmc_id = %s ORDER BY r.id",
            (self.pmc_id,),
        ).fetchall()

    @rule(
        data=st.data(),
        role=st.sampled_from(["owner", "app", "ai"]),
        back=st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=120)),
        length=st.timedeltas(min_value=timedelta(microseconds=1), max_value=timedelta(days=150)),
    )
    def check_an_owner_statement(self, data, role, back, length):
        owner = data.draw(st.sampled_from(self.pmc.owners))
        start = self.now - back
        end = start + length
        rows = (
            self.conns[role]
            .execute(
                "SELECT posted_on, item, amount, balance"
                " FROM trust_report_owner_statement(%s, %s, %s, %s)",
                (self.pmc_id, owner.owner_id, start, end),
            )
            .fetchall()
        )

        def change(source, target, amount):
            return amount if target == owner.account else -amount if source == owner.account else 0

        mine = [(when, change(*request)) for _, request, when in self.posted]
        mine = [(when, amount) for when, amount in mine if amount]
        opening = sum((a for when, a in mine if when < start), Decimal(0))
        # Date order; a stable sort keeps posting order for the same instant.
        inside = sorted(((w, a) for w, a in mine if start <= w < end), key=lambda p: p[0])
        expected = [(None, "opening balance", None, opening)]
        running = opening
        for when, amount in inside:
            running += amount
            expected.append((when.strftime("%Y-%m-%d"), "posting", amount, running))
        expected.append((None, "closing balance", None, running))
        money_in = sum((a for _, a in inside if a > 0), Decimal(0))
        money_out = sum((a for _, a in inside if a < 0), Decimal(0))
        expected += [
            (None, "all properties: opening balance", None, opening),
            (None, "all properties: money in", money_in, None),
            (None, "all properties: money out", money_out, None),
            (None, "all properties: closing balance", None, running),
        ]
        assert rows[3:] == expected

    @precondition(lambda self: self.posted)
    @rule(
        data=st.data(),
        role=st.sampled_from(["owner", "app", "ai"]),
        before=st.integers(min_value=0, max_value=30),
        after=st.integers(min_value=0, max_value=30),
    )
    def check_the_general_ledger(self, data, role, before, after):
        """Over a period of whole days (UTC) around a day something was posted, each account's
        opening balance, its entries in date order with the balance after each, and its closing
        balance, as the model has them; the period's debits equal its credits."""
        posted_on = data.draw(st.sampled_from([when for _, _, when in self.posted])).date()
        first = posted_on - timedelta(days=before)
        last = posted_on + timedelta(days=after)
        start = datetime.combine(first, datetime.min.time(), tzinfo=UTC)
        end = datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
        rows = (
            self.conns[role]
            .execute(
                "SELECT item, bank, account, debit, credit, balance"
                " FROM trust_report_general_ledger(%s, %s, %s) WHERE item NOT IN ('PMC', 'period')",
                (self.pmc_id, first, last),
            )
            .fetchall()
        )
        names = {
            self.pmc.operating_cash: "Synthetic operating trust account (operating)",
            self.pmc.deposit_cash: "Synthetic security_deposit trust account (security_deposit)",
        }

        # The model's blocks: per account, its lines on its own side (cash as money the bank
        # holds, every other account as money held for someone).
        expected: dict[str, list[tuple]] = {name: [] for name in names.values()}
        in_period = sorted(  # stable: one instant keeps the order of posting
            (p for p in self.posted if start <= p[2] < end), key=lambda p: p[2]
        )
        total = Decimal(0)
        for account in self.accounts:
            sign = -1 if self.kinds[account] == "bank_cash" else 1
            balance = sign * sum(
                (
                    (amount if target == account else -amount)
                    for _, (source, target, amount), when in self.posted
                    if account in (source, target) and when < start
                ),
                Decimal(0),
            )
            block = [("opening balance", None, None, balance)]
            debits = credits = Decimal(0)
            for _, (source, target, amount), _when in in_period:
                if account not in (source, target):
                    continue
                dr, cr = (amount, Decimal(0)) if source == account else (Decimal(0), amount)
                balance += sign * (cr - dr)
                debits, credits = debits + dr, credits + cr
                block.append(("entry", dr, cr, balance))
            if len(block) == 1 and block[0][3] == 0 and self.kinds[account] != "bank_cash":
                continue
            block.append(("closing balance", debits, credits, balance))
            expected[names[self.bank[account]]].append(tuple(block))
            total += debits

        blocks: dict[tuple[str, str], list[tuple]] = {}
        for item, bank, account, debit, credit, balance in rows[:-1]:
            blocks.setdefault((bank, account), []).append((item, debit, credit, balance))
        for name, mine in expected.items():
            got = [tuple(lines) for (bank, _), lines in blocks.items() if bank == name]
            assert sorted(got) == sorted(mine)
        assert rows[-1] == ("total", None, None, total, total, None)

    @rule(
        role=st.sampled_from(["owner", "app", "ai"]),
        back=st.timedeltas(min_value=timedelta(days=-2), max_value=timedelta(days=120)),
    )
    def check_the_trial_balance_and_deposit_register(self, role, back):
        """As of a day, each trust bank account's book cash against the balances it holds, as
        the model has them, and the deposit register against the deposit account's."""
        as_of = (self.now - back).date()
        cutoff = datetime.combine(as_of + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
        held = dict.fromkeys(self.accounts, Decimal(0))
        for _, (source, target, amount), when in self.posted:
            if when < cutoff:
                held[source] -= amount
                held[target] += amount
        rows = (
            self.conns[role]
            .execute(
                "SELECT item, bank, account, debit, credit"
                " FROM trust_report_trial_balance(%s, %s) WHERE item <> 'as of'",
                (self.pmc_id, as_of),
            )
            .fetchall()
        )
        # Banks list by name: "Synthetic operating ..." before "Synthetic security_deposit ...".
        for cash, name in [
            (self.pmc.operating_cash, "Synthetic operating trust account (operating)"),
            (self.pmc.deposit_cash, "Synthetic security_deposit trust account (security_deposit)"),
        ]:
            mine = [r for r in rows if r[1] == name]
            # A balance sits on the side its sign puts it. Cash the bank holds is a debit, and
            # so is an account overdrawn as of the day (a posting backdated past the one that
            # funded it).
            others = sorted(
                sides(held[a])
                for a in self.accounts
                if self.bank[a] == cash and a != cash and held[a] != 0
            )
            assert mine[0][2:] == ("book cash", *sides(held[cash]))
            assert sorted(r[3:] for r in mine if r[0] == "account" and r[2] != "book cash") == (
                others
            )
            lines = [sides(held[cash]), *others]
            assert mine[-1][3:] == tuple(sum(side, Decimal(0)) for side in zip(*lines, strict=True))
            assert mine[-1][3] == mine[-1][4]  # the trust bank account ties out
        assert rows[-1][:3] == ("total", None, None)
        assert (
            rows[-1][3]
            == rows[-1][4]
            == sum((r[3] for r in rows if r[0] == "bank total"), Decimal(0))
        )

        register = (
            self.conns[role]
            .execute(
                "SELECT item, held FROM trust_report_security_deposits(%s, %s)"
                " WHERE item NOT IN ('PMC', 'as of')",
                (self.pmc_id, as_of),
            )
            .fetchall()
        )
        deposit = held[self.pmc.tenant_deposit]
        assert register == [
            *([("deposit", deposit)] if deposit else []),
            ("deposits held", deposit),
            ("book cash", -held[self.pmc.deposit_cash]),
            ("total held", deposit),
        ]

    @rule(data=st.data(), path=st.sampled_from(["with a key", "through pgledger"]), back=BACK)
    def the_ai_role_cannot_post(self, data, path, back):
        """Whatever the AI tries to post, valid or not, dated now or back in a closed period, is
        refused for lack of a grant before anything is checked or written."""
        requests = [self.draw_request(data)]
        when = self.now - back
        before = self.snapshot()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            if path == "with a key":
                post(self.conns["ai"], self.pmc_id, "ai-key", requests, event_at=when)
            else:
                transfer_batch(self.conns["ai"], requests, event_at=when)
        assert self.snapshot() == before

    @rule(amount=AMOUNTS)
    def try_to_cross_pmcs(self, amount):
        if self.other_pmc_owner is None:
            self.other_pmc_owner = make_pmc(self.conn, owners=1).owners[0].account
        source = next(a for a in self.accounts if self.kinds[a] == "bank_cash")
        before = self.snapshot()
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="crosses PMCs"):
            transfer(self.conn, source, self.other_pmc_owner, amount)
        assert self.snapshot() == before

    @rule(
        kind=st.sampled_from(
            ["tenant_deposit", "owner_property", "pmc_income", "prepaid_rent", "vendor_payable"]
        ),
        role=st.sampled_from(["owner", "app"]),
    )
    def try_to_open_an_account_in_the_wrong_trust_account(self, kind, role):
        """Deposits only in the deposit account; nothing else held in trust in it."""
        pmc, owner = self.pmc, self.pmc.owners[0]
        wrong_bank = pmc.operating_bank_id if kind == "tenant_deposit" else pmc.deposit_bank_id
        if kind == "vendor_payable":
            query = "SELECT trust_open_vendor_account(%s, %s, %s)"
            params: tuple = (self.pmc_id, wrong_bank, pmc.vendor_id)
        else:
            owner_id, property_id = (
                (owner.owner_id, owner.property_id) if kind == "owner_property" else (None, None)
            )
            tenant_id = self.tenant_id if kind in ("tenant_deposit", "prepaid_rent") else None
            query = "SELECT trust_open_ledger_account(%s, %s, %s, %s, %s, %s)"
            params = (self.pmc_id, wrong_bank, kind, owner_id, property_id, tenant_id)
        opened = (
            "SELECT (SELECT count(*) FROM pgledger_accounts),"
            " (SELECT count(*) FROM trust_ledger_accounts)"
        )
        before = self.conn.execute(opened).fetchone()
        with pytest.raises(psycopg.errors.CheckViolation, match="accounts belong in the"):
            self.conns[role].execute(query, params)
        assert self.conn.execute(opened).fetchone() == before

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
    def three_way_reconciliation_reports_agree(self):
        approvals = self.conn.execute(
            "SELECT id, bank_account_id, period_end, statement_balance"
            " FROM trust_reconciliations WHERE pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        cash_of = {bank_id: cash for cash, bank_id in self.bank_id.items()}
        for reconciliation, bank_id, period_end, statement in approvals:
            rows = self.conn.execute(
                "SELECT item, detail, amount FROM trust_report_three_way_reconciliation(%s, %s)",
                (self.pmc_id, reconciliation),
            ).fetchall()
            figures = {item: amount for item, _, amount in rows if item != "ledger"}
            ledgers = [amount for item, _, amount in rows if item == "ledger"]
            # Ledger lines list in byte order, the same on every server.
            holders = [detail for item, detail, _ in rows if item == "ledger"]
            assert holders == sorted(holders, key=str.encode)
            # Each batch is one transaction with one date, and ties out on its own, so at any
            # instant the books and the ledgers agree.
            book = self.book_balance(cash_of[bank_id], period_end)
            assert figures["trust journal (book cash)"] == book
            assert figures["beneficiary ledgers"] == book == sum(ledgers)
            assert figures["difference: trust journal - beneficiary ledgers"] == 0
            assert figures["difference: bank statement - trust journal"] == statement - book

    @invariant()
    def deposits_sit_only_in_the_deposit_account(self):
        rows = self.conn.execute(
            "SELECT b.kind, t.kind, sum(a.balance)"
            " FROM trust_ledger_accounts t"
            " JOIN trust_bank_accounts b ON b.id = t.bank_account_id"
            " JOIN pgledger_accounts a ON a.id = t.ledger_account_id"
            " WHERE t.pmc_id = %s GROUP BY b.kind, t.kind",
            (self.pmc_id,),
        ).fetchall()
        held = {(bank_kind, kind): amount for bank_kind, kind, amount in rows}
        assert {kind for bank_kind, kind in held if bank_kind == "security_deposit"} == {
            "bank_cash",
            "tenant_deposit",
        }
        assert ("operating", "tenant_deposit") not in held
        # The deposit account's cash is every deposit held, and nothing but deposits.
        deposits = sum(amount for (_, kind), amount in held.items() if kind == "tenant_deposit")
        assert -held[("security_deposit", "bank_cash")] == deposits

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
    def each_trust_bank_account_ties_out(self):
        rows = self.conn.execute(BALANCES, self.ids).fetchall()
        for bank in set(self.bank.values()):
            in_bank = [
                (account, amount) for account, amount, _ in rows if self.bank[account] == bank
            ]
            cash = sum(amount for account, amount in in_bank if self.kinds[account] == "bank_cash")
            held = sum(amount for account, amount in in_bank if self.kinds[account] != "bank_cash")
            assert -cash == held, bank

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

    @invariant()
    def each_key_posted_once(self):
        rows = self.conn.execute(
            "SELECT idempotency_key, transfer_ids FROM trust_idempotency_keys WHERE pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert dict(rows) == {key: ids for key, (_, _, ids) in self.keyed.items()}

    @invariant()
    def approved_periods_stay_closed(self):
        rows = self.conn.execute(
            "SELECT b.id, b.closed_through FROM trust_bank_accounts b WHERE b.pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert dict(rows) == {self.bank_id[cash]: end for cash, end in self.closed.items()}

        approved = self.conn.execute(
            "SELECT bank_account_id, period_start, period_end, book_balance"
            " FROM trust_reconciliations WHERE pmc_id = %s ORDER BY period_end, bank_account_id",
            (self.pmc_id,),
        ).fetchall()
        expected = [(self.bank_id[c], s, e, b) for c, s, e, b in self.reconciled]
        assert approved == sorted(expected, key=lambda r: (r[2], r[0]))
        # Nothing dated inside an approved period landed after its approval, so the books
        # still show, at each period's end, the cash they showed when it was approved.
        assert self.conn.execute(BOOKS_CHANGED_SINCE_APPROVAL, (self.pmc_id,)).fetchall() == []


def test_ledger_invariants_hold_under_random_postings(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as owner_conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
        psycopg.connect(database_url, autocommit=True) as ai_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        ai_conn.execute("SET ROLE trust_ai_agent")
        run_state_machine_as_test(lambda: LedgerMachine(owner_conn, app_conn, ai_conn))
