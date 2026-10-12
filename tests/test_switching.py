"""Carrying a trust bank account over from another system (migration 20261012000033): the
checks and deposits the bank had not cleared at the cutover, the staff sign-off that freezes
them, and the opening match that lets a migrated account reconcile from its first period."""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_staff,
    approve,
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
    unmatch_lines,
)

CUTOVER = date(2026, 1, 1)
JAN_31, FEB_1, FEB_28 = date(2026, 1, 31), date(2026, 2, 1), date(2026, 2, 28)


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def refused(match, error=psycopg.errors.CheckViolation):
    return pytest.raises(error, match=match)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def staff(conn, pmc):
    return add_staff(conn, pmc.pmc_id)


@pytest.fixture
def opened(conn, pmc):
    """The operating account cut over on Jan 1 holding 1000.00 for owner 1: its transfers."""
    return open_with_balances(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        CUTOVER,
        {pmc.owners[0].account: "1000.00"},
        "1000.00",
    )


def carried(conn, pmc, staff, *amounts):
    return [
        carry_over(
            conn,
            pmc.pmc_id,
            pmc.operating_bank_id,
            staff,
            date(2025, 12, 30),
            amount,
            f"Item {n}",
            str(100 + n),
        )
        for n, amount in enumerate(amounts)
    ]


def january(conn, pmc, staff, lines):
    return import_statement(conn, pmc.pmc_id, pmc.operating_bank_id, staff, CUTOVER, JAN_31, lines)


def totals(conn, pmc, through):
    report = bank_reconciliation(conn, pmc.pmc_id, pmc.operating_bank_id, through)
    return {item: amount for line, item, _, amount in report if line <= 9 and amount is not None}


# --- items and the sign-off ------------------------------------------------------------------


def test_items_and_the_sign_off_follow_the_opening(conn, pmc, staff):
    with refused("has no opening balances to carry over to"):
        carried(conn, pmc, staff, "-50.00")
    with refused("has no opening balances to carry over to"):
        sign_off(conn, pmc.pmc_id, pmc.operating_bank_id, staff)


def test_an_item_was_issued_by_the_cutover_and_none_comes_after_the_sign_off(
    conn, pmc, staff, opened
):
    carry_over(conn, pmc.pmc_id, pmc.operating_bank_id, staff, CUTOVER, "-50.00")
    with refused("issued by the cutover, 2026-01-01, not 2026-01-02"):
        carry_over(conn, pmc.pmc_id, pmc.operating_bank_id, staff, date(2026, 1, 2), "-50.00")
    sign_off(conn, pmc.pmc_id, pmc.operating_bank_id, staff)

    with refused("signed off already"):
        carried(conn, pmc, staff, "-10.00")
    with refused("signed off already"):
        sign_off(conn, pmc.pmc_id, pmc.operating_bank_id, staff)


@pytest.mark.parametrize(
    "amount, description, reference, why",
    [
        ("0.00", "Nothing", None, "amount_check"),
        ("1.001", "Odd", None, "amount_check"),
        ("1000000000.00", "Too much", None, "amount_check"),
        ("1.00", " ", None, "description_check"),
        ("1.00", "x" * 201, None, "description_check"),
        ("1.00", "Check", " ", "reference_check"),
        ("1.00", "Check", "r" * 101, "reference_check"),
    ],
)
def test_an_item_holds_only_sound_values(
    conn, pmc, staff, opened, amount, description, reference, why
):
    with refused(why):
        carry_over(
            conn, pmc.pmc_id, pmc.operating_bank_id, staff, CUTOVER, amount, description, reference
        )


def test_items_and_sign_offs_are_their_own_pmcs(conn, pmc, staff, opened):
    other = make_pmc(conn)
    theirs = add_staff(conn, other.pmc_id)
    with refused("foreign key", psycopg.errors.ForeignKeyViolation):
        carry_over(conn, pmc.pmc_id, pmc.operating_bank_id, theirs, CUTOVER, "-5.00")
    with refused("foreign key", psycopg.errors.ForeignKeyViolation):
        carry_over(conn, other.pmc_id, pmc.operating_bank_id, theirs, CUTOVER, "-5.00")
    with refused("foreign key", psycopg.errors.ForeignKeyViolation):
        sign_off(conn, pmc.pmc_id, pmc.operating_bank_id, theirs)


def test_items_and_sign_offs_are_append_only(conn, pmc, staff, opened):
    carried(conn, pmc, staff, "-5.00")
    sign_off(conn, pmc.pmc_id, pmc.operating_bank_id, staff)
    for table in (
        "trust_cutover_items",
        "trust_cutover_signoffs",
        "trust_bank_match_cutover_items",
    ):
        for change in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with refused("append-only", psycopg.errors.RestrictViolation):
                conn.execute(change)


def test_a_statement_starts_no_earlier_than_the_cutover(conn, pmc, staff, opened):
    with refused("opened on 2026-01-01; a statement starts no earlier"):
        import_statement(
            conn, pmc.pmc_id, pmc.operating_bank_id, staff, date(2025, 12, 31), JAN_31, []
        )


# --- the opening match -----------------------------------------------------------------------


def test_the_opening_match_needs_a_signed_off_cutover(conn, pmc, staff, opened):
    _, (forward,) = january(
        conn, pmc, staff, [(CUTOVER, "1000.00", "Balance brought forward", None)]
    )

    with refused("has no signed-off cutover to match"):
        match_opening(conn, pmc.pmc_id, pmc.operating_bank_id, forward, staff)
    other = make_pmc(conn)
    with refused("no trust bank account", psycopg.errors.InvalidParameterValue):
        match_opening(conn, other.pmc_id, pmc.operating_bank_id, forward, staff)


def test_the_balance_brought_forward_is_book_cash_less_the_items_not_cleared(
    conn, pmc, staff, opened
):
    carried(conn, pmc, staff, "-200.00", "75.00")
    sign_off(conn, pmc.pmc_id, pmc.operating_bank_id, staff)
    _, (wrong, right) = january(
        conn,
        pmc,
        staff,
        [(CUTOVER, "1000.00", "Balance brought forward", None), (CUTOVER, "125.00", "Other", None)],
    )

    with refused(
        "the balance brought forward is 1000.00; the opening balances \\(1000.00\\) less "
        "the items not yet cleared \\(-125.00\\) come to 1125.00"
    ):
        match_opening(conn, pmc.pmc_id, pmc.operating_bank_id, wrong, staff)
    _, (theirs,) = import_statement(
        conn,
        pmc.pmc_id,
        pmc.deposit_bank_id,
        staff,
        CUTOVER,
        JAN_31,
        [(CUTOVER, "1125.00", "Elsewhere", None)],
    )
    with refused("not on a statement of trust bank account", psycopg.errors.InvalidParameterValue):
        match_opening(conn, pmc.pmc_id, pmc.operating_bank_id, theirs, staff)
    assert right


def test_an_opening_is_matched_once(conn, pmc, staff, opened):
    sign_off(conn, pmc.pmc_id, pmc.operating_bank_id, staff)
    _, (forward, again) = january(
        conn,
        pmc,
        staff,
        [(CUTOVER, "1000.00", "Balance brought forward", None), (CUTOVER, "1000.00", "Copy", None)],
    )
    first = match_opening(conn, pmc.pmc_id, pmc.operating_bank_id, forward, staff)

    for line in (forward, again):
        with refused("the line or the opening is matched already"):
            match_opening(conn, pmc.pmc_id, pmc.operating_bank_id, line, staff)
    unmatch_lines(conn, pmc.pmc_id, first, staff)
    match_opening(conn, pmc.pmc_id, pmc.operating_bank_id, again, staff)


# --- clearing what was carried over ----------------------------------------------------------


def test_a_migrated_account_reconciles_from_its_first_period(conn, pmc, staff, opened):
    check, deposit = carried(conn, pmc, staff, "-200.00", "75.00")
    sign_off(conn, pmc.pmc_id, pmc.operating_bank_id, staff)
    rent = post(
        conn,
        pmc.pmc_id,
        "rent-jan",
        [(pmc.operating_cash, pmc.owners[0].account, Decimal("500.00"))],
        at(date(2026, 1, 5)),
    )
    _, (forward, deposited, cleared, paid) = january(
        conn,
        pmc,
        staff,
        [
            (CUTOVER, "1125.00", "Balance brought forward", None),
            (date(2026, 1, 3), "75.00", "Deposit", None),
            (date(2026, 1, 9), "-200.00", "Check 100", "100"),
            (date(2026, 1, 6), "500.00", "Deposit", None),
        ],
    )

    with refused("clears once the opening is matched"):
        match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [cleared], [], staff, [check])
    opening = match_opening(conn, pmc.pmc_id, pmc.operating_bank_id, forward, staff)
    at_cutover = totals(conn, pmc, CUTOVER)
    match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [deposited], [], staff, [deposit])
    clearing = match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [cleared], [], staff, [check])
    match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [paid], rent, staff)

    assert at_cutover == {
        "bank balance (statements)": Decimal("1125.00"),
        "deposits in transit": Decimal("75.00"),
        "outstanding withdrawals": Decimal("-200.00"),
        "adjusted bank balance": Decimal("1000.00"),
        "trust journal (book cash)": Decimal("1000.00"),
        "difference: adjusted bank balance - trust journal": Decimal("0.00"),
    }
    assert totals(conn, pmc, JAN_31)["difference: adjusted bank balance - trust journal"] == 0
    with refused("a later match clears an item this opening carries"):
        unmatch_lines(conn, pmc.pmc_id, opening, staff)
    reconciliation = approve_bank(conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31)
    assert conn.execute(
        "SELECT period_start, statement_balance, book_balance FROM trust_reconciliations"
        " WHERE id = %s",
        (reconciliation,),
    ).fetchone() == (at(CUTOVER, 0), Decimal("1500.00"), Decimal("1500.00"))
    with refused("closed through"):
        unmatch_lines(conn, pmc.pmc_id, clearing, staff)


def test_items_not_cleared_show_as_outstanding_from_before_the_cutover(conn, pmc, staff, opened):
    carried(conn, pmc, staff, "-200.00")
    sign_off(conn, pmc.pmc_id, pmc.operating_bank_id, staff)
    _, (forward,) = january(
        conn, pmc, staff, [(CUTOVER, "1200.00", "Balance brought forward", None)]
    )
    report = bank_reconciliation(conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31)
    assert [(i, d, a) for line, i, d, a in report if line > 9] == [
        ("deposit in transit", "2026-01-01 Opening balance", Decimal("1000.00")),
        ("bank line not in the books", "2026-01-01 Balance brought forward", Decimal("1200.00")),
    ]

    match_opening(conn, pmc.pmc_id, pmc.operating_bank_id, forward, staff)

    report = bank_reconciliation(conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31)
    assert [(i, d, a) for line, i, d, a in report if line > 9] == [
        (
            "outstanding withdrawal",
            "2025-12-30 Item 0 (100), from before the cutover",
            Decimal("-200.00"),
        ),
    ]
    assert totals(conn, pmc, JAN_31)["difference: adjusted bank balance - trust journal"] == 0


def test_an_item_clears_once_and_only_in_its_own_account(conn, pmc, staff, opened):
    (check,) = carried(conn, pmc, staff, "-200.00")
    sign_off(conn, pmc.pmc_id, pmc.operating_bank_id, staff)
    _, (forward, once, twice) = january(
        conn,
        pmc,
        staff,
        [
            (CUTOVER, "1200.00", "Balance brought forward", None),
            (date(2026, 1, 9), "-200.00", "Check 100", None),
            (date(2026, 1, 10), "-200.00", "Check 100 again", None),
        ],
    )
    match_opening(conn, pmc.pmc_id, pmc.operating_bank_id, forward, staff)
    match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [once], [], staff, [check])

    with refused("matched already"):
        match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [twice], [], staff, [check])
    with refused("names each line, transfer and item once", psycopg.errors.InvalidParameterValue):
        match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [twice], [], staff, [check, check])
    with refused(
        "was not carried over into trust bank account", psycopg.errors.InvalidParameterValue
    ):
        match_lines(conn, pmc.pmc_id, pmc.deposit_bank_id, [], [], staff, [check])


def test_an_item_counts_as_dated_at_the_cutover_for_a_closed_period(conn, pmc, staff, opened):
    (check,) = carried(conn, pmc, staff, "-200.00")
    sign_off(conn, pmc.pmc_id, pmc.operating_bank_id, staff)
    _, (forward, late) = january(
        conn,
        pmc,
        staff,
        [
            (CUTOVER, "1200.00", "Balance brought forward", None),
            (date(2026, 1, 9), "-200.00", "Check 100", None),
        ],
    )
    match_opening(conn, pmc.pmc_id, pmc.operating_bank_id, forward, staff)
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, at(CUTOVER, 0), at(FEB_1, 0), "1000.00")

    with refused("closed through"):
        match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [late], [], staff, [check])
    _, (feb,) = import_statement(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        staff,
        FEB_1,
        FEB_28,
        [(FEB_1, "-200.00", "Check 100", None)],
        opening="1000.00",
    )
    match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [feb], [], staff, [check])


def test_an_opening_match_in_a_closed_period_is_refused(conn, pmc, staff, opened):
    sign_off(conn, pmc.pmc_id, pmc.operating_bank_id, staff)
    _, (forward,) = january(
        conn, pmc, staff, [(CUTOVER, "1000.00", "Balance brought forward", None)]
    )
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, at(CUTOVER, 0), at(FEB_1, 0), "1000.00")

    with refused("closed through"):
        match_opening(conn, pmc.pmc_id, pmc.operating_bank_id, forward, staff)


def test_matches_with_items_are_written_only_by_their_functions(conn, app_conn, pmc, staff, opened):
    with refused("permission denied", psycopg.errors.InsufficientPrivilege):
        app_conn.execute("INSERT INTO trust_bank_match_cutover_items DEFAULT VALUES")


def tie_out(conn, pmc_id, bank_id):
    return conn.execute(
        "SELECT item, detail, amount FROM trust_report_cutover(%s, %s)", (pmc_id, bank_id)
    ).fetchall()


def test_the_tie_out_shows_the_opening_the_items_and_the_sign_off(conn, pmc, staff, opened):
    carried(conn, pmc, staff, "-200.00")
    before = tie_out(conn, pmc.pmc_id, pmc.operating_bank_id)
    sign_off(conn, pmc.pmc_id, pmc.operating_bank_id, staff)
    after = tie_out(conn, pmc.pmc_id, pmc.operating_bank_id)

    assert before[4:] == [
        ("old system's book cash", None, Decimal("1000.00")),
        ("opening balance", "owner_property: Owner 1 / Property 1", Decimal("1000.00")),
        ("opening balances, total", None, Decimal("1000.00")),
        ("difference: total - book cash", None, Decimal("0.00")),
        ("outstanding check", "2025-12-30 Item 0 (100)", Decimal("-200.00")),
        (
            "bank balance to expect at the cutover",
            "book cash less the items the bank had not cleared",
            Decimal("1200.00"),
        ),
        ("signed off by", "not signed off yet", None),
    ]
    assert after[-1][1].startswith("Staff 1 at ")
    with refused("has no opening balances", psycopg.errors.InvalidParameterValue):
        tie_out(conn, pmc.pmc_id, pmc.deposit_bank_id)
    with refused("has no opening balances", psycopg.errors.InvalidParameterValue):
        tie_out(conn, make_pmc(conn).pmc_id, pmc.operating_bank_id)
