"""Property-based tests for bank statements, matching and reconciliation (migration 0032).

Hypothesis posts money in and out of a trust bank account's cash, imports statements day after
day whose lines clear some of those transfers (singly or as a batch) along with bank-only items,
matches lines to transfers, undoes matches, tries wrong matches and approves periods, in random
order. A model keeps what the bank and the books hold and which matches stand; the database must
agree after every step.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - at the end of the last statement's day, trust_report_bank_reconciliation's bank balance,
    book cash, deposits in transit, outstanding withdrawals and the lines the books lack are the
    model's, and the adjusted bank balance less book cash is exactly those missing lines;
  - a line or a transfer is in at most one match not undone, and every such match comes to the
    same on both sides;
  - a match or an undo that would change an approved period is refused with nothing written;
  - a period is approved only when the books lack no line, at the bank balance the statements
    give, and its trust bank account is closed through its end;
  - each approved period's three-way reconciliation adds the deposits in transit and outstanding
    withdrawals of its last day to the statement balance, lists each of them and each line the
    books lack, and its adjusted bank balance comes to the trust journal (migration 0050).

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import itertools
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_staff,
    approve_bank,
    bank_reconciliation,
    import_statement,
    make_pmc,
    match_lines,
    post,
    unmatch_lines,
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

START = date(2026, 1, 1)
CENTS = st.integers(min_value=1, max_value=200_000).map(lambda c: Decimal(c) / 100)
NUMBERS = itertools.count()


def noon(day):
    return datetime(day.year, day.month, day.day, 12, tzinfo=UTC)


@dataclass
class Moved:
    id: str
    day: date
    cash: Decimal


@dataclass
class Line:
    id: uuid.UUID
    day: date
    amount: Decimal
    clears: list[Moved] = field(default_factory=list)  # what the bank cleared, if anything


@dataclass
class Match:
    id: uuid.UUID
    lines: list[Line]
    transfers: list[Moved]


class BankMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.pmc = make_pmc(conn, owners=1)
        self.bank = self.pmc.operating_bank_id
        self.staff = add_staff(conn, self.pmc.pmc_id)
        # Model
        self.held = Decimal(0)  # the owner's balance, so payouts never go below zero
        self.moved: list[Moved] = []
        self.lines: list[Line] = []
        self.matches: list[Match] = []
        self.next_day = START  # the next statement's first day
        self.closed: date | None = None  # the first day still open, once a period is approved
        self.approved: list[tuple[uuid.UUID, date]] = []  # (reconciliation, its last day)

    # --- helpers --------------------------------------------------------------------------

    def matched(self, item):
        return any(item in m.lines or item in m.transfers for m in self.matches)

    def open_from(self):
        return self.closed or START

    def before_closing(self, match):
        return self.closed is not None and all(
            x.day < self.closed for x in [*match.lines, *match.transfers]
        )

    # --- the books ------------------------------------------------------------------------

    @rule(amount=CENTS, offset=st.integers(0, 8))
    def money_in(self, amount, offset):
        day = self.open_from() + timedelta(days=offset)
        (moved,) = post(
            self.conn,
            self.pmc.pmc_id,
            f"in-{next(NUMBERS)}",
            [(self.pmc.operating_cash, self.pmc.owners[0].account, amount)],
            noon(day),
        )
        self.held += amount
        self.moved.append(Moved(moved, day, amount))

    @precondition(lambda self: self.held > 0)
    @rule(data=st.data(), offset=st.integers(0, 8))
    def money_out(self, data, offset):
        amount = data.draw(st.integers(1, int(self.held * 100)).map(lambda c: Decimal(c) / 100))
        day = self.open_from() + timedelta(days=offset)
        (moved,) = post(
            self.conn,
            self.pmc.pmc_id,
            f"out-{next(NUMBERS)}",
            [(self.pmc.owners[0].account, self.pmc.operating_cash, amount)],
            noon(day),
        )
        self.held -= amount
        self.moved.append(Moved(moved, day, -amount))

    # --- the bank -------------------------------------------------------------------------

    @rule(
        data=st.data(),
        days=st.integers(1, 10),
        extras=st.lists(CENTS | CENTS.map(lambda a: -a), max_size=2),
    )
    def a_statement_comes(self, data, days, extras):
        first, last = self.next_day, self.next_day + timedelta(days=days - 1)
        waiting = [
            m
            for m in self.moved
            if not self.matched(m) and not any(m in line.clears for line in self.lines)
        ]
        cleared = (
            data.draw(st.lists(st.sampled_from(waiting), unique_by=lambda m: m.id, max_size=4))
            if waiting
            else []
        )
        batches = data.draw(st.integers(1, max(len(cleared), 1)))
        groups = [cleared[i::batches] for i in range(batches)]
        planned = []
        for group in groups:
            total = sum((m.cash for m in group), Decimal(0))
            if group and total != 0:
                planned.append((total, group))
        planned += [(amount, []) for amount in extras]
        rows = [
            (first + timedelta(days=data.draw(st.integers(0, days - 1))), amount, "Line", None)
            for amount, _ in planned
        ]
        _, ids = import_statement(
            self.conn,
            self.pmc.pmc_id,
            self.bank,
            self.staff,
            first,
            last,
            rows,
            opening=str(self.bank_total()),
        )
        for (day, amount, _, _), line_id, (_, group) in zip(rows, ids, planned, strict=True):
            self.lines.append(Line(line_id, day, amount, group))
        self.next_day = last + timedelta(days=1)

    def bank_total(self, through=None):
        return sum(
            (ln.amount for ln in self.lines if through is None or ln.day <= through), Decimal(0)
        )

    # --- matching -------------------------------------------------------------------------
    @precondition(lambda self: any(not ln.clears and not self.matched(ln) for ln in self.lines))
    @rule(data=st.data())
    def book_what_only_the_bank_had(self, data):
        """Staff record a line the books lack (a fee, interest) as its own transfer."""
        line = data.draw(
            st.sampled_from([ln for ln in self.lines if not ln.clears and not self.matched(ln)])
        )
        if self.held + line.amount < 0:
            return
        day = max(line.day, self.open_from())
        into, out = self.pmc.operating_cash, self.pmc.owners[0].account
        (moved,) = post(
            self.conn,
            self.pmc.pmc_id,
            f"booked-{next(NUMBERS)}",
            [(into, out, line.amount) if line.amount > 0 else (out, into, -line.amount)],
            noon(day),
        )
        self.held += line.amount
        line.clears = [Moved(moved, day, line.amount)]
        self.moved.append(line.clears[0])

    @precondition(
        lambda self: any(
            ln.clears and not self.matched(ln) and not any(self.matched(m) for m in ln.clears)
            for ln in self.lines
        )
    )
    @rule(data=st.data())
    def match_what_the_bank_cleared(self, data):
        line = data.draw(
            st.sampled_from(
                [
                    ln
                    for ln in self.lines
                    if ln.clears
                    and not self.matched(ln)
                    and not any(self.matched(m) for m in ln.clears)
                ]
            )
        )
        match = Match(uuid.uuid4(), [line], list(line.clears))
        if self.before_closing(match):
            event("match: refused, it would change an approved period")
            with pytest.raises(psycopg.errors.CheckViolation, match="closed through"):
                self.match(match)
            return
        event(f"match: {len(match.transfers)} transfer(s)")
        match.id = self.match(match)
        self.matches.append(match)

    def match(self, match):
        return match_lines(
            self.conn,
            self.pmc.pmc_id,
            self.bank,
            [ln.id for ln in match.lines],
            [m.id for m in match.transfers],
            self.staff,
        )

    @precondition(
        lambda self: (
            any(not self.matched(ln) for ln in self.lines)
            and any(not self.matched(m) for m in self.moved)
        )
    )
    @rule(data=st.data())
    def a_wrong_match_is_refused(self, data):
        line = data.draw(st.sampled_from([ln for ln in self.lines if not self.matched(ln)]))
        moved = data.draw(st.sampled_from([m for m in self.moved if not self.matched(m)]))
        if line.amount == moved.cash:
            return
        event("match: refused, the sides differ")
        with pytest.raises(psycopg.errors.CheckViolation, match="comes to the same"):
            self.match(Match(uuid.uuid4(), [line], [moved]))

    @precondition(lambda self: self.matches)
    @rule(data=st.data())
    def undo_a_match(self, data):
        match = data.draw(st.sampled_from(self.matches))
        if self.before_closing(match):
            event("undo: refused, it would change an approved period")
            with pytest.raises(psycopg.errors.CheckViolation, match="closed through"):
                unmatch_lines(self.conn, self.pmc.pmc_id, match.id, self.staff)
            return
        event("undo")
        unmatch_lines(self.conn, self.pmc.pmc_id, match.id, self.staff)
        self.matches.remove(match)

    # --- approving ------------------------------------------------------------------------

    def cleared(self, match, through):
        return all(x.day <= through for x in [*match.lines, *match.transfers])

    def missing(self, through):
        return [
            ln
            for ln in self.lines
            if ln.day <= through
            and not any(ln in m.lines and self.cleared(m, through) for m in self.matches)
        ]

    def outstanding(self, through):
        return [
            mv
            for mv in self.moved
            if mv.day <= through
            and not any(mv in m.transfers and self.cleared(m, through) for m in self.matches)
        ]

    @precondition(lambda self: self.lines or self.next_day > START)
    @rule(data=st.data())
    def approve_a_period(self, data):
        first = self.closed or START
        last = self.next_day - timedelta(days=1)
        if last < first:
            return
        through = data.draw(
            st.sampled_from([first + timedelta(days=n) for n in range((last - first).days + 1)])
        )
        if sum((ln.amount for ln in self.missing(through)), Decimal(0)) != 0:
            event("approve: refused, the books lack lines")
            with pytest.raises(psycopg.errors.CheckViolation, match="that the books lack"):
                approve_bank(self.conn, self.pmc.pmc_id, self.bank, through)
            return
        event("approve")
        reconciliation = approve_bank(self.conn, self.pmc.pmc_id, self.bank, through)
        (balance,) = self.conn.execute(
            "SELECT statement_balance FROM trust_reconciliations WHERE id = %s",
            (reconciliation,),
        ).fetchone()
        assert balance == self.bank_total(through)
        self.closed = through + timedelta(days=1)
        self.approved.append((reconciliation, through))

    # --- invariants -----------------------------------------------------------------------

    @invariant()
    def the_report_is_the_model(self):
        if self.next_day == START:
            return
        through = self.next_day - timedelta(days=1)
        report = bank_reconciliation(self.conn, self.pmc.pmc_id, self.bank, through)
        totals = {
            item: amount for line, item, _, amount in report if line <= 9 and amount is not None
        }
        listed = [(item, amount) for line, item, _, amount in report if line > 9]
        book = sum((mv.cash for mv in self.moved if mv.day <= through), Decimal(0))
        transit = [mv.cash for mv in self.outstanding(through) if mv.cash > 0]
        out = [mv.cash for mv in self.outstanding(through) if mv.cash < 0]
        lacking = [ln.amount for ln in self.missing(through)]
        assert totals["bank balance (statements)"] == self.bank_total(through)
        assert totals["trust journal (book cash)"] == book
        assert totals["deposits in transit"] == sum(transit, Decimal(0))
        assert totals["outstanding withdrawals"] == sum(out, Decimal(0))
        assert totals["adjusted bank balance"] - book == sum(lacking, Decimal(0))
        assert totals["difference: adjusted bank balance - trust journal"] == sum(
            lacking, Decimal(0)
        )
        assert sorted(a for i, a in listed if i == "deposit in transit") == sorted(transit)
        assert sorted(a for i, a in listed if i == "outstanding withdrawal") == sorted(out)
        assert sorted(a for i, a in listed if i == "bank line not in the books") == sorted(lacking)

    @invariant()
    def each_three_way_lists_what_had_not_cleared(self):
        """Each approved period's three-way reconciliation adds what had not cleared by its last
        day to the statement balance, lists each item, and comes to the trust journal less the
        lines the books lack (none in all, once approved)."""
        for reconciliation, through in self.approved:
            rows = self.conn.execute(
                "SELECT line, item, detail, amount"
                " FROM trust_report_three_way_reconciliation(%s, %s)",
                (self.pmc.pmc_id, reconciliation),
            ).fetchall()
            totals = {item: amount for line, item, _, amount in rows if line <= 14}
            listed = [(i, a) for line, i, _, a in rows if line > 14 and i != "ledger"]
            book = sum((mv.cash for mv in self.moved if mv.day <= through), Decimal(0))
            transit = [mv.cash for mv in self.outstanding(through) if mv.cash > 0]
            out = [mv.cash for mv in self.outstanding(through) if mv.cash < 0]
            lacking = [ln.amount for ln in self.missing(through)]
            assert totals["bank statement balance"] == self.bank_total(through)
            assert totals["trust journal (book cash)"] == book
            assert totals["deposits in transit"] == sum(transit, Decimal(0))
            assert totals["outstanding withdrawals"] == sum(out, Decimal(0))
            assert totals["adjusted bank balance"] == self.bank_total(through) + sum(
                transit + out, Decimal(0)
            )
            assert totals["difference: adjusted bank balance - trust journal"] == sum(
                lacking, Decimal(0)
            )
            assert sum(lacking, Decimal(0)) == 0
            assert sorted(a for i, a in listed if i == "deposit in transit") == sorted(transit)
            assert sorted(a for i, a in listed if i == "outstanding withdrawal") == sorted(out)
            assert sorted(a for i, a in listed if i == "bank line not in the books") == sorted(
                lacking
            )

    @invariant()
    def matches_stand_alone_and_balance(self):
        standing = self.conn.execute(
            "SELECT m.id FROM trust_bank_matches AS m WHERE m.pmc_id = %s AND NOT EXISTS"
            " (SELECT 1 FROM trust_bank_unmatches AS u WHERE u.match_id = m.id)",
            (self.pmc.pmc_id,),
        ).fetchall()
        assert {row[0] for row in standing} == {m.id for m in self.matches}
        assert not self.conn.execute(
            "SELECT x FROM ("
            "  SELECT ml.line_id::text AS x FROM trust_bank_match_lines AS ml"
            "  WHERE ml.pmc_id = %(p)s AND NOT EXISTS"
            "    (SELECT 1 FROM trust_bank_unmatches AS u WHERE u.match_id = ml.match_id)"
            "  UNION ALL"
            "  SELECT mt.transfer_id FROM trust_bank_match_transfers AS mt"
            "  WHERE mt.pmc_id = %(p)s AND NOT EXISTS"
            "    (SELECT 1 FROM trust_bank_unmatches AS u WHERE u.match_id = mt.match_id)"
            ") AS items GROUP BY x HAVING count(*) > 1",
            {"p": self.pmc.pmc_id},
        ).fetchall()
        for match in self.matches:
            assert sum((ln.amount for ln in match.lines), Decimal(0)) == sum(
                (mv.cash for mv in match.transfers), Decimal(0)
            )


def test_bank_matching_invariants_hold_under_random_statements_matches_and_approvals(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: BankMachine(conn))
