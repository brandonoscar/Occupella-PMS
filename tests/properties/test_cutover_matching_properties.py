"""Property-based tests for carrying a trust bank account over from another system (migration
0033): opening balances, the checks and deposits the bank had not cleared, the sign-off, the
opening match, and later bank lines clearing those items, with transfers, undos and approvals in
random order. A model keeps what the bank and the books hold; the database must agree after
every step.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - at the end of the last statement's day, the reconciliation's book cash, bank balance,
    deposits in transit and outstanding withdrawals are the model's: transfers no cleared match
    holds, and items the opening match carries that no cleared match clears;
  - the adjusted bank balance less book cash is exactly the bank lines the books lack, so a
    migrated account approves its first period once every line is matched;
  - an item clears only after the opening match, at most once at a time, and the opening match
    is never undone while an item it carries is cleared.

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
    approve_bank,
    bank_reconciliation,
    carry_over,
    import_statement,
    make_pmc,
    match_lines,
    match_opening,
    open_with_balances,
    post,
    sign_off,
    staff_named,
    unmatch_lines,
)
from hypothesis import assume, event
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

CUTOVER = date(2026, 1, 1)
CENTS = st.integers(min_value=1, max_value=200_000).map(lambda c: Decimal(c) / 100)
SIGNED = CENTS | CENTS.map(lambda a: -a)
NUMBERS = itertools.count()


def noon(day):
    return datetime(day.year, day.month, day.day, 12, tzinfo=UTC)


@dataclass
class Item:
    id: uuid.UUID
    amount: Decimal


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
    item: Item | None = None  # the item carried over it clears
    moved: Moved | None = None  # or the transfer


@dataclass
class Match:
    id: uuid.UUID
    lines: list[Line]
    transfers: list[Moved] = field(default_factory=list)
    carried: list[Item] = field(default_factory=list)
    cleared: list[Item] = field(default_factory=list)


class CutoverMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.pmc = make_pmc(conn, owners=1)
        self.bank = self.pmc.operating_bank_id
        # Signing off a cutover and approving take the approve role.
        self.staff = staff_named(conn, self.pmc.pmc_id, "Staff 1", ("admin", "approve"))
        self.moved: list[Moved] = []
        self.items: list[Item] = []
        self.lines: list[Line] = []
        self.matches: list[Match] = []
        self.opening: Match | None = None
        self.held = Decimal(0)
        self.next_day = CUTOVER
        self.closed: date | None = None

    @initialize(book=CENTS, amounts=st.lists(SIGNED, max_size=4))
    def cut_over(self, book, amounts):
        assume(book - sum(amounts, Decimal(0)) != 0)
        transfers = open_with_balances(
            self.conn,
            self.pmc.pmc_id,
            self.bank,
            CUTOVER,
            {self.pmc.owners[0].account: str(book)},
            str(book),
        )
        self.opening_transfers = [Moved(t, CUTOVER, book) for t in transfers]
        self.moved += self.opening_transfers
        self.held = book
        for amount in amounts:
            item = carry_over(self.conn, self.pmc.pmc_id, self.bank, self.staff, CUTOVER, amount)
            self.items.append(Item(item, amount))
        sign_off(self.conn, self.pmc.pmc_id, self.bank, self.staff)
        _, (forward,) = import_statement(
            self.conn,
            self.pmc.pmc_id,
            self.bank,
            self.staff,
            CUTOVER,
            CUTOVER,
            [(CUTOVER, str(book - sum(amounts, Decimal(0))), "Balance brought forward", None)],
        )
        self.forward = Line(forward, CUTOVER, book - sum(amounts, Decimal(0)))
        self.lines.append(self.forward)
        self.next_day = CUTOVER + timedelta(days=1)

    # --- helpers --------------------------------------------------------------------------

    def matched(self, thing):
        return any(
            thing in m.lines or thing in m.transfers or thing in m.cleared for m in self.matches
        )

    def open_from(self):
        return self.closed or CUTOVER

    def last_day(self):
        return self.next_day - timedelta(days=1)

    # --- the books and the bank -----------------------------------------------------------

    @rule(amount=CENTS, offset=st.integers(0, 6))
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

    @rule(data=st.data(), days=st.integers(1, 6), extra=st.none() | SIGNED)
    def a_statement_comes(self, data, days, extra):
        first, last = self.next_day, self.next_day + timedelta(days=days - 1)
        pending = [
            m
            for m in self.moved
            if m not in self.opening_transfers
            and not self.matched(m)
            and not any(ln.moved is m for ln in self.lines)
        ]
        uncleared = [i for i in self.items if not any(ln.item is i for ln in self.lines)]
        planned = (
            [
                (m.cash, None, m)
                for m in data.draw(
                    st.lists(st.sampled_from(pending), unique_by=lambda m: m.id, max_size=2)
                )
            ]
            if pending
            else []
        )
        planned += (
            [
                (i.amount, i, None)
                for i in data.draw(
                    st.lists(st.sampled_from(uncleared), unique_by=lambda i: i.id, max_size=2)
                )
            ]
            if uncleared
            else []
        )
        if extra is not None:
            planned.append((extra, None, None))
        rows = [
            (first + timedelta(days=data.draw(st.integers(0, days - 1))), amount, "Line", None)
            for amount, _, _ in planned
        ]
        opening = sum((ln.amount for ln in self.lines), Decimal(0))
        _, ids = import_statement(
            self.conn,
            self.pmc.pmc_id,
            self.bank,
            self.staff,
            first,
            last,
            rows,
            opening=str(opening),
        )
        for (day, amount, _, _), line_id, (_, item, moved) in zip(rows, ids, planned, strict=True):
            self.lines.append(Line(line_id, day, amount, item, moved))
        self.next_day = last + timedelta(days=1)

    # --- matching -------------------------------------------------------------------------

    @precondition(lambda self: self.opening is None)
    @rule()
    def match_the_opening(self):
        match = Match(
            uuid.uuid4(), [self.forward], list(self.opening_transfers), carried=list(self.items)
        )
        if self.closed is not None:
            event("opening: refused, the period is closed")
            with pytest.raises(psycopg.errors.CheckViolation, match="closed through"):
                match_opening(self.conn, self.pmc.pmc_id, self.bank, self.forward.id, self.staff)
            return
        event("opening matched")
        match.id = match_opening(self.conn, self.pmc.pmc_id, self.bank, self.forward.id, self.staff)
        self.opening = match
        self.matches.append(match)

    @precondition(
        lambda self: any(
            ln.item and not self.matched(ln) and not self.matched(ln.item) for ln in self.lines
        )
    )
    @rule(data=st.data())
    def clear_an_item(self, data):
        line = data.draw(
            st.sampled_from(
                [
                    ln
                    for ln in self.lines
                    if ln.item and not self.matched(ln) and not self.matched(ln.item)
                ]
            )
        )
        if self.opening is None:
            event("item: refused before the opening match")
            with pytest.raises(psycopg.errors.CheckViolation, match="clears once the opening"):
                match_lines(
                    self.conn, self.pmc.pmc_id, self.bank, [line.id], [], self.staff, [line.item.id]
                )
            return
        if self.closed is not None and line.day < self.closed:
            event("item: refused, the period is closed")
            with pytest.raises(psycopg.errors.CheckViolation, match="closed through"):
                match_lines(
                    self.conn, self.pmc.pmc_id, self.bank, [line.id], [], self.staff, [line.item.id]
                )
            return
        event("item cleared")
        made = match_lines(
            self.conn, self.pmc.pmc_id, self.bank, [line.id], [], self.staff, [line.item.id]
        )
        self.matches.append(Match(made, [line], cleared=[line.item]))

    @precondition(
        lambda self: any(
            ln.moved and not self.matched(ln) and not self.matched(ln.moved) for ln in self.lines
        )
    )
    @rule(data=st.data())
    def match_a_transfer(self, data):
        line = data.draw(
            st.sampled_from(
                [
                    ln
                    for ln in self.lines
                    if ln.moved and not self.matched(ln) and not self.matched(ln.moved)
                ]
            )
        )
        if self.closed is not None and line.day < self.closed and line.moved.day < self.closed:
            return
        made = match_lines(
            self.conn, self.pmc.pmc_id, self.bank, [line.id], [line.moved.id], self.staff
        )
        self.matches.append(Match(made, [line], [line.moved]))

    @precondition(lambda self: self.matches)
    @rule(data=st.data())
    def undo_a_match(self, data):
        match = data.draw(st.sampled_from(self.matches))
        days = [x.day for x in [*match.lines, *match.transfers]] + [
            CUTOVER for _ in [*match.carried, *match.cleared]
        ]
        if self.closed is not None and all(d < self.closed for d in days):
            event("undo: refused, the period is closed")
            with pytest.raises(psycopg.errors.CheckViolation, match="closed through"):
                unmatch_lines(self.conn, self.pmc.pmc_id, match.id, self.staff)
            return
        if match is self.opening and any(self.matched(i) for i in match.carried):
            event("undo: refused, an item it carries is cleared")
            with pytest.raises(psycopg.errors.CheckViolation, match="later match clears"):
                unmatch_lines(self.conn, self.pmc.pmc_id, match.id, self.staff)
            return
        event("undo")
        unmatch_lines(self.conn, self.pmc.pmc_id, match.id, self.staff)
        self.matches.remove(match)
        if match is self.opening:
            self.opening = None

    # --- approving ------------------------------------------------------------------------

    def cleared(self, match, through):
        return all(x.day <= through for x in [*match.lines, *match.transfers])

    def outstanding(self, through):
        moved = [
            m.cash
            for m in self.moved
            if m.day <= through
            and not any(m in x.transfers and self.cleared(x, through) for x in self.matches)
        ]
        items = [
            i.amount
            for i in self.items
            if any(i in x.carried and self.cleared(x, through) for x in self.matches)
            and not any(i in x.cleared and self.cleared(x, through) for x in self.matches)
        ]
        return moved + items

    def missing(self, through):
        return [
            ln.amount
            for ln in self.lines
            if ln.day <= through
            and not any(ln in x.lines and self.cleared(x, through) for x in self.matches)
        ]

    @rule()
    def approve_the_last_day(self):
        through = self.last_day()
        if self.closed is not None and through < self.closed:
            return
        if sum(self.missing(through), Decimal(0)) != 0:
            event("approve: refused, the books lack lines")
            with pytest.raises(psycopg.errors.CheckViolation, match="that the books lack"):
                approve_bank(self.conn, self.pmc.pmc_id, self.bank, through)
            return
        event("approve")
        approve_bank(self.conn, self.pmc.pmc_id, self.bank, through)
        self.closed = through + timedelta(days=1)

    # --- invariants -----------------------------------------------------------------------

    @invariant()
    def the_report_is_the_model(self):
        if not self.lines:
            return
        through = self.last_day()
        report = bank_reconciliation(self.conn, self.pmc.pmc_id, self.bank, through)
        totals = {i: a for line, i, _, a in report if line <= 9 and a is not None}
        uncleared = self.outstanding(through)
        book = sum((m.cash for m in self.moved if m.day <= through), Decimal(0))
        assert totals["trust journal (book cash)"] == book
        assert totals["bank balance (statements)"] == sum(
            (ln.amount for ln in self.lines if ln.day <= through), Decimal(0)
        )
        assert totals["deposits in transit"] == sum((a for a in uncleared if a > 0), Decimal(0))
        assert totals["outstanding withdrawals"] == sum((a for a in uncleared if a < 0), Decimal(0))
        assert totals["adjusted bank balance"] - book == sum(self.missing(through), Decimal(0))


def test_cutover_invariants_hold_under_random_items_statements_matches_and_approvals(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: CutoverMachine(conn))
