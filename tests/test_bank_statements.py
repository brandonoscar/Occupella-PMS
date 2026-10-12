"""Bank statements, matching their lines to the books, and approving a reconciliation from them.

Migration 20261012000032. A trust bank account's statements follow one another from 0, each
with all its lines; a line or a transfer is in at most one match not undone, and a match comes
to the same on both sides. trust_report_bank_reconciliation shows a day's end as a
reconciliation sees it, and trust_approve_bank_reconciliation approves only when no line is
missing from the books.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_staff,
    approve,
    approve_bank,
    bank_reconciliation,
    import_statement,
    in_background,
    make_pmc,
    match_lines,
    post,
    unmatch_lines,
    wait_until_blocked,
)

JAN_1, JAN_31 = date(2026, 1, 1), date(2026, 1, 31)
FEB_1, FEB_28 = date(2026, 2, 1), date(2026, 2, 28)
TODAY = datetime.now(UTC).date()


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def staff(conn, pmc):
    return add_staff(conn, pmc.pmc_id)


def rent(conn, pmc, amount, day, n=0, memo="Rent"):
    """A tenant's payment into owner n's account through the operating account's cash."""
    (moved,) = post(
        conn,
        pmc.pmc_id,
        f"rent-{day}-{amount}-{n}-{memo}",
        [(pmc.operating_cash, pmc.owners[n].account, Decimal(amount))],
        at(day),
        {"memo": memo},
    )
    return moved


def payout(conn, pmc, amount, day, n=0, memo="Owner draw"):
    """Money out of owner n's account through the operating account's cash."""
    (moved,) = post(
        conn,
        pmc.pmc_id,
        f"out-{day}-{amount}-{n}-{memo}",
        [(pmc.owners[n].account, pmc.operating_cash, Decimal(amount))],
        at(day),
        {"memo": memo},
    )
    return moved


def january(conn, pmc, staff, lines, **more):
    return import_statement(
        conn, pmc.pmc_id, pmc.operating_bank_id, staff, JAN_1, JAN_31, lines, **more
    )


def refused(match, error=psycopg.errors.CheckViolation):
    return pytest.raises(error, match=match)


def totals(conn, pmc, through):
    report = bank_reconciliation(conn, pmc.pmc_id, pmc.operating_bank_id, through)
    return {item: amount for line, item, _, amount in report if line <= 9 and amount is not None}


def items(conn, pmc, through):
    report = bank_reconciliation(conn, pmc.pmc_id, pmc.operating_bank_id, through)
    return [(item, detail, amount) for line, item, detail, amount in report if line > 9]


# --- statements ----------------------------------------------------------------------------


def test_statements_follow_one_another_from_zero(conn, pmc, staff):
    first, lines = january(
        conn,
        pmc,
        staff,
        [(JAN_1, "500.00", "Balance brought forward", None), (JAN_31, "-20.00", "Check 7", "7")],
    )
    second, _ = import_statement(
        conn, pmc.pmc_id, pmc.operating_bank_id, staff, FEB_1, FEB_28, [], opening="480.00"
    )

    stored = conn.execute(
        "SELECT first_day, last_day, opening_balance, closing_balance, line_count"
        " FROM trust_bank_statements WHERE pmc_id = %s ORDER BY first_day",
        (pmc.pmc_id,),
    ).fetchall()
    assert stored == [
        (JAN_1, JAN_31, Decimal("0.00"), Decimal("480.00"), 2),
        (FEB_1, FEB_28, Decimal("480.00"), Decimal("480.00"), 0),
    ]
    assert len(lines) == 2 and first != second


@pytest.mark.parametrize(
    "days, opening, why",
    [
        ((date(2026, 2, 2), FEB_28), "480.00", "starts on 2026-02-01"),  # a gap
        ((JAN_31, FEB_28), "480.00", "starts on 2026-02-01"),  # an overlap
        ((FEB_1, FEB_28), "480.01", "opens at 480.00"),
    ],
)
def test_the_next_statement_starts_the_next_day_at_the_last_closing(
    conn, pmc, staff, days, opening, why
):
    january(conn, pmc, staff, [(JAN_1, "480.00", "Deposit", None)])
    with refused(why):
        import_statement(conn, pmc.pmc_id, pmc.operating_bank_id, staff, *days, [], opening=opening)


def test_the_first_statement_opens_at_zero(conn, pmc, staff):
    with refused("opens at 0, not 100.00"):
        january(conn, pmc, staff, [(JAN_1, "5.00", "Deposit", None)], opening="100.00")


def test_a_statement_has_all_its_lines_and_they_come_to_its_closing(conn, pmc, staff):
    with refused("has 1 lines coming to 5.00; it says 1 lines from 0 to 6.00"), conn.transaction():
        statement = conn.execute(
            "INSERT INTO trust_bank_statements (pmc_id, bank_account_id, first_day, last_day,"
            " opening_balance, closing_balance, line_count, source, file_sha256, imported_by)"
            " VALUES (%s, %s, %s, %s, 0, 6.00, 1, 'csv', %s, %s) RETURNING id",
            (pmc.pmc_id, pmc.operating_bank_id, JAN_1, JAN_31, "a" * 64, staff),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO trust_bank_statement_lines (pmc_id, statement_id, line_no, posted_on,"
            " amount, description) VALUES (%s, %s, 1, %s, 5.00, 'Deposit')",
            (pmc.pmc_id, statement, JAN_1),
        )
    with refused("has 0 lines coming to 0; it says 2 lines from 0 to 0"), conn.transaction():
        conn.execute(
            "INSERT INTO trust_bank_statements (pmc_id, bank_account_id, first_day, last_day,"
            " opening_balance, closing_balance, line_count, source, file_sha256, imported_by)"
            " VALUES (%s, %s, %s, %s, 0, 0, 2, 'csv', %s, %s)",
            (pmc.pmc_id, pmc.operating_bank_id, JAN_1, JAN_31, "b" * 64, staff),
        )


def test_no_line_joins_a_statement_once_it_is_in(conn, pmc, staff):
    statement, _ = january(conn, pmc, staff, [(JAN_1, "5.00", "Deposit", None)])
    for line_no, why in ((2, "has 1 lines, not line 2"), (1, "duplicate key")):
        error = psycopg.errors.UniqueViolation if line_no == 1 else psycopg.errors.CheckViolation
        with refused(why, error):
            conn.execute(
                "INSERT INTO trust_bank_statement_lines (pmc_id, statement_id, line_no,"
                " posted_on, amount, description) VALUES (%s, %s, %s, %s, 1.00, 'Late')",
                (pmc.pmc_id, statement, line_no, JAN_1),
            )


def test_a_line_is_posted_on_a_day_its_statement_covers(conn, pmc, staff):
    with refused("covers 2026-01-01 to 2026-01-31, not 2026-02-01"):
        january(conn, pmc, staff, [(FEB_1, "5.00", "Deposit", None)])


def test_a_statement_covers_days_that_have_come(conn, pmc, staff):
    with refused("covers days that have come"):
        import_statement(
            conn, pmc.pmc_id, pmc.operating_bank_id, staff, TODAY, TODAY + timedelta(days=1), []
        )
    import_statement(conn, pmc.pmc_id, pmc.operating_bank_id, staff, TODAY, TODAY, [])


def test_a_file_is_imported_once(conn, pmc, staff):
    january(conn, pmc, staff, [], file_sha256="c" * 64)
    with refused("duplicate key", psycopg.errors.UniqueViolation):
        import_statement(
            conn, pmc.pmc_id, pmc.operating_bank_id, staff, FEB_1, FEB_28, [], file_sha256="c" * 64
        )


@pytest.mark.parametrize(
    "amount, description, reference",
    [
        ("0.00", "Nothing", None),
        ("1.001", "A tenth of a cent", None),
        ("1.00", "x" * 501, None),
        ("1.00", "Blank reference", " "),
        ("1000000000.00", "Too much", None),
    ],
)
def test_a_line_is_a_real_amount_with_a_short_description(
    conn, pmc, staff, amount, description, reference
):
    with refused("violates check constraint"):
        january(conn, pmc, staff, [(JAN_1, amount, description, reference)])


def test_statements_and_lines_are_append_only(conn, pmc, staff):
    january(conn, pmc, staff, [(JAN_1, "5.00", "Deposit", None)])
    for table in ("trust_bank_statements", "trust_bank_statement_lines"):
        for change in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with refused("append-only", psycopg.errors.RestrictViolation):
                conn.execute(change)


# --- matching ------------------------------------------------------------------------------


def test_a_line_matches_the_transfers_that_moved_its_cash(conn, pmc, staff):
    one = rent(conn, pmc, "1500.00", date(2026, 1, 5))
    two = rent(conn, pmc, "700.00", date(2026, 1, 5), n=1)
    out = payout(conn, pmc, "300.00", date(2026, 1, 20))
    _, (deposit, check) = january(
        conn,
        pmc,
        staff,
        [
            (date(2026, 1, 6), "2200.00", "Deposit", None),
            (date(2026, 1, 22), "-300.00", "Check 1", "1"),
        ],
    )

    batch = match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [deposit], [one, two], staff)
    single = match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [check], [out], staff)

    held = conn.execute(
        "SELECT m.id, count(DISTINCT l.line_id), count(DISTINCT t.transfer_id)"
        " FROM trust_bank_matches AS m"
        " JOIN trust_bank_match_lines AS l ON l.match_id = m.id"
        " JOIN trust_bank_match_transfers AS t ON t.match_id = m.id"
        " WHERE m.pmc_id = %s GROUP BY m.id",
        (pmc.pmc_id,),
    ).fetchall()
    assert sorted(held) == sorted([(batch, 1, 2), (single, 1, 1)])


def test_a_payment_and_its_bounce_the_bank_never_saw_match_with_no_line(conn, pmc, staff):
    paid = rent(conn, pmc, "900.00", date(2026, 1, 5))
    back = payout(conn, pmc, "900.00", date(2026, 1, 7), memo="Bounced")

    match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [], [paid, back], staff)

    empty = "a match holds at least one line, transfer or cutover item"
    with refused(empty, psycopg.errors.InvalidParameterValue):
        match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [], [], staff)


def test_a_match_comes_to_the_same_on_both_sides(conn, pmc, staff):
    paid = rent(conn, pmc, "1500.00", date(2026, 1, 5))
    _, (line,) = january(conn, pmc, staff, [(date(2026, 1, 6), "1500.01", "Deposit", None)])

    with refused("the lines come to 1500.01 and the transfers and items to 1500.00"):
        match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [line], [paid], staff)


def test_a_match_names_only_this_accounts_lines_and_cash_once_each(conn, pmc, staff):
    paid = rent(conn, pmc, "100.00", date(2026, 1, 5))
    (fee,) = post(  # moves no cash: owner to the PMC's fee account
        conn,
        pmc.pmc_id,
        "fee-1",
        [(pmc.owners[0].account, pmc.pmc_income, Decimal("10.00"))],
        at(date(2026, 1, 6)),
    )
    _, (line,) = january(conn, pmc, staff, [(date(2026, 1, 6), "100.00", "Deposit", None)])
    _, (theirs,) = import_statement(
        conn,
        pmc.pmc_id,
        pmc.deposit_bank_id,
        staff,
        JAN_1,
        JAN_31,
        [(date(2026, 1, 6), "100.00", "Deposit", None)],
    )
    bank = pmc.operating_bank_id
    cases = [
        ([line, line], [paid], "names each line, transfer and item once"),
        ([line], [paid, paid], "names each line, transfer and item once"),
        ([theirs], [paid], "not on a statement of trust bank account"),
        ([line], [fee], f"transfer {fee} moved no cash"),
    ]
    for lines, transfers, why in cases:
        with refused(why, psycopg.errors.InvalidParameterValue):
            match_lines(conn, pmc.pmc_id, bank, lines, transfers, staff)
    other = make_pmc(conn)
    with refused("no trust bank account", psycopg.errors.InvalidParameterValue):
        match_lines(conn, other.pmc_id, bank, [line], [paid], staff)


def test_a_line_or_transfer_is_in_one_match_until_it_is_undone(conn, pmc, staff):
    paid = rent(conn, pmc, "100.00", date(2026, 1, 5))
    again = rent(conn, pmc, "100.00", date(2026, 1, 9), memo="Second")
    _, (line, other) = january(
        conn,
        pmc,
        staff,
        [
            (date(2026, 1, 6), "100.00", "Deposit", None),
            (date(2026, 1, 10), "100.00", "Deposit", None),
        ],
    )
    first = match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [line], [paid], staff)

    for lines, transfers in (([line], [again]), ([other], [paid])):
        with refused("matched already"):
            match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, lines, transfers, staff)
    unmatch_lines(conn, pmc.pmc_id, first, staff)
    unmatch_lines(conn, pmc.pmc_id, first, staff)  # safe to retry
    match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [line], [again], staff)
    match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [other], [paid], staff)

    assert conn.execute(
        "SELECT count(*) FROM trust_bank_unmatches WHERE match_id = %s", (first,)
    ).fetchone() == (1,)
    with refused("no bank match", psycopg.errors.InvalidParameterValue):
        unmatch_lines(conn, make_pmc(conn).pmc_id, first, staff)


def test_cash_moved_between_two_trust_accounts_matches_once_in_each(conn, pmc, staff):
    (held,) = post(
        conn,
        pmc.pmc_id,
        "deposit-1",
        [(pmc.deposit_cash, pmc.tenant_deposit, Decimal("50.00"))],
        at(date(2026, 1, 3)),
    )
    # The tenant's deposit becomes prepaid rent: held money and its cash change bank together.
    moved, cash = post(
        conn,
        pmc.pmc_id,
        "move-1",
        [
            (pmc.tenant_deposit, pmc.prepaid_rent, Decimal("50.00")),
            (pmc.operating_cash, pmc.deposit_cash, Decimal("50.00")),
        ],
        at(date(2026, 1, 5)),
    )
    _, (into,) = january(conn, pmc, staff, [(date(2026, 1, 6), "50.00", "Transfer in", None)])
    _, (deposited, out) = import_statement(
        conn,
        pmc.pmc_id,
        pmc.deposit_bank_id,
        staff,
        JAN_1,
        JAN_31,
        [
            (date(2026, 1, 3), "50.00", "Deposit", None),
            (date(2026, 1, 6), "-50.00", "Transfer out", None),
        ],
    )

    match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [into], [cash], staff)
    match_lines(conn, pmc.pmc_id, pmc.deposit_bank_id, [deposited], [held], staff)
    match_lines(conn, pmc.pmc_id, pmc.deposit_bank_id, [out], [cash], staff)
    with refused(f"transfer {moved} moved no cash", psycopg.errors.InvalidParameterValue):
        match_lines(conn, pmc.pmc_id, pmc.deposit_bank_id, [], [moved], staff)


def test_matches_never_change_a_period_already_approved(conn, pmc, staff):
    early = rent(conn, pmc, "100.00", date(2026, 1, 5))
    late = rent(conn, pmc, "40.00", date(2026, 1, 30))
    _, (line, other) = january(
        conn,
        pmc,
        staff,
        [
            (date(2026, 1, 6), "100.00", "Deposit", None),
            (date(2026, 1, 20), "40.00", "Deposit", None),
        ],
    )
    kept = match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [line], [early], staff)
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, at(JAN_1, 0), at(FEB_1, 0), "140.00")
    feb = rent(conn, pmc, "40.00", FEB_1)

    with refused("closed through"):
        unmatch_lines(conn, pmc.pmc_id, kept, staff)
    with refused("closed through"):
        match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [other], [late], staff)
    # A match reaching past the closing changes nothing closed.
    match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [other], [feb], staff)


def test_matches_are_written_only_by_their_functions(conn, app_conn, pmc, staff):
    for table in (
        "trust_bank_matches",
        "trust_bank_match_lines",
        "trust_bank_match_transfers",
        "trust_bank_unmatches",
    ):
        with refused("permission denied", psycopg.errors.InsufficientPrivilege):
            app_conn.execute(f"INSERT INTO {table} DEFAULT VALUES")
        for change in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with refused("append-only", psycopg.errors.RestrictViolation):
                conn.execute(change)


# --- the reconciliation at a day's end -------------------------------------------------------


def test_a_days_end_shows_what_the_bank_had_not_cleared_and_the_lines_the_books_lack(
    conn, pmc, staff
):
    cleared = rent(conn, pmc, "1500.00", date(2026, 1, 5))
    in_transit = rent(conn, pmc, "700.00", date(2026, 1, 31), memo="Rent, last day")
    check = payout(conn, pmc, "300.00", date(2026, 1, 20), memo="Check 12")
    _, (deposit, fee, later) = january(
        conn,
        pmc,
        staff,
        [
            (date(2026, 1, 6), "1500.00", "Deposit", "D1"),
            (date(2026, 1, 25), "-5.00", "Service fee", None),
            (date(2026, 1, 31), "-300.00", "Check 12", "12"),
        ],
    )
    match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [deposit], [cleared], staff)
    match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [later], [check], staff)

    assert totals(conn, pmc, date(2026, 1, 30)) == {
        "bank balance (statements)": Decimal("1495.00"),
        "deposits in transit": Decimal("0"),
        "outstanding withdrawals": Decimal("-300.00"),
        "adjusted bank balance": Decimal("1195.00"),
        "trust journal (book cash)": Decimal("1200.00"),
        "difference: adjusted bank balance - trust journal": Decimal("-5.00"),
    }
    assert items(conn, pmc, date(2026, 1, 30)) == [
        ("outstanding withdrawal", "2026-01-20 Check 12", Decimal("-300.00")),
        ("bank line not in the books", "2026-01-25 Service fee", Decimal("-5.00")),
    ]
    assert totals(conn, pmc, JAN_31)["deposits in transit"] == Decimal("700.00")
    assert items(conn, pmc, JAN_31) == [
        ("deposit in transit", "2026-01-31 Rent, last day", Decimal("700.00")),
        ("bank line not in the books", "2026-01-25 Service fee", Decimal("-5.00")),
    ]
    assert totals(conn, pmc, JAN_31)[
        "difference: adjusted bank balance - trust journal"
    ] == Decimal("-5.00")
    head = bank_reconciliation(conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31)[:3]
    assert [(item, detail) for _, item, detail, _ in head][1:] == [
        ("trust bank account", "Synthetic operating trust account (operating)"),
        ("at the end of", "2026-01-31 (UTC)"),
    ]
    assert in_transit


def test_a_day_needs_a_statement_covering_it_and_the_pmcs_own_account(conn, pmc, staff):
    january(conn, pmc, staff, [])
    with refused("no statement of trust bank account", psycopg.errors.InvalidParameterValue):
        bank_reconciliation(conn, pmc.pmc_id, pmc.operating_bank_id, FEB_1)
    with refused("no trust bank account", psycopg.errors.InvalidParameterValue):
        bank_reconciliation(conn, make_pmc(conn).pmc_id, pmc.operating_bank_id, JAN_31)


def test_an_undone_match_leaves_its_items_open_again(conn, pmc, staff):
    paid = rent(conn, pmc, "100.00", date(2026, 1, 5))
    _, (line,) = january(conn, pmc, staff, [(date(2026, 1, 6), "100.00", "Deposit", None)])
    match = match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [line], [paid], staff)
    unmatch_lines(conn, pmc.pmc_id, match, staff)

    assert [item for item, _, _ in items(conn, pmc, JAN_31)] == [
        "deposit in transit",
        "bank line not in the books",
    ]


# --- approval ------------------------------------------------------------------------------


def test_a_period_is_approved_from_its_statements_when_the_books_lack_no_line(conn, pmc, staff):
    paid = rent(conn, pmc, "1500.00", date(2026, 1, 5))
    rent(conn, pmc, "700.00", date(2026, 1, 31))  # in transit at the end of January
    _, (line,) = january(conn, pmc, staff, [(date(2026, 1, 6), "1500.00", "Deposit", None)])
    match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [line], [paid], staff)

    first = approve_bank(conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31)
    import_statement(
        conn, pmc.pmc_id, pmc.operating_bank_id, staff, FEB_1, FEB_28, [], opening="1500.00"
    )
    second = approve_bank(conn, pmc.pmc_id, pmc.operating_bank_id, date(2026, 2, 10))

    approved = conn.execute(
        "SELECT id, period_start, period_end, statement_balance, book_balance, prepared_by"
        " FROM trust_reconciliations WHERE pmc_id = %s ORDER BY period_end",
        (pmc.pmc_id,),
    ).fetchall()
    assert approved == [
        (first, at(JAN_1, 0), at(FEB_1, 0), Decimal("1500.00"), Decimal("2200.00"), "Preparer 1"),
        (
            second,
            at(FEB_1, 0),
            at(date(2026, 2, 11), 0),
            Decimal("1500.00"),
            Decimal("2200.00"),
            "Preparer 1",
        ),
    ]


def test_a_period_whose_lines_the_books_lack_is_not_approved(conn, pmc, staff):
    january(conn, pmc, staff, [(date(2026, 1, 25), "-5.00", "Service fee", None)])

    with refused("come to -5.00 that the books lack by the end of 2026-01-31"):
        approve_bank(conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31)
    assert conn.execute(
        "SELECT closed_through FROM trust_bank_accounts WHERE id = %s", (pmc.operating_bank_id,)
    ).fetchone() == (None,)


def test_a_statement_never_starts_in_a_closed_period(conn, pmc, staff):
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, at(JAN_1, 0), at(FEB_1, 0))

    with refused("closed through"):
        january(conn, pmc, staff, [])
    import_statement(conn, pmc.pmc_id, pmc.operating_bank_id, staff, FEB_1, FEB_28, [])


# --- what a statement and a line may hold ----------------------------------------------------


def raw_statement(conn, pmc, staff, **more):
    """A bare statement row (no lines), so each column's own check can be tried."""
    row = {
        "pmc_id": pmc.pmc_id,
        "bank_account_id": pmc.operating_bank_id,
        "first_day": JAN_1,
        "last_day": JAN_31,
        "opening_balance": Decimal(0),
        "closing_balance": Decimal(0),
        "line_count": 0,
        "source": "csv",
        "file_sha256": "d" * 64,
        "imported_by": staff,
    } | more
    with conn.transaction():
        conn.execute(
            f"INSERT INTO trust_bank_statements ({', '.join(row)})"
            f" VALUES ({', '.join(['%s'] * len(row))})",
            list(row.values()),
        )


@pytest.mark.parametrize(
    "more, why",
    [
        ({"closing_balance": Decimal("0.001")}, "trust_bank_statements_closing_balance_check"),
        ({"line_count": -1}, "trust_bank_statements_line_count_check"),
        ({"line_count": 20001}, "trust_bank_statements_line_count_check"),
        ({"source": "pdf"}, "trust_bank_statements_source_check"),
        ({"file_sha256": "D" * 64}, "trust_bank_statements_file_sha256_check"),
        ({"file_sha256": "d" * 63}, "trust_bank_statements_file_sha256_check"),
        ({"last_day": date(2025, 12, 31)}, "trust_bank_statements_check"),
    ],
)
def test_a_statement_row_holds_only_sound_values(conn, pmc, staff, more, why):
    with refused(why):
        raw_statement(conn, pmc, staff, **more)


def test_a_statement_is_of_its_own_pmcs_account_by_its_own_staff(conn, pmc, staff):
    other = make_pmc(conn)
    theirs = add_staff(conn, other.pmc_id)
    with refused("foreign key", psycopg.errors.ForeignKeyViolation):
        raw_statement(conn, pmc, staff, bank_account_id=other.operating_bank_id)
    with refused("foreign key", psycopg.errors.ForeignKeyViolation):
        raw_statement(conn, pmc, theirs)


def test_a_line_belongs_to_its_own_pmcs_statement(conn, pmc, staff):
    other = make_pmc(conn)
    with refused("foreign key", psycopg.errors.ForeignKeyViolation), conn.transaction():
        (statement,) = conn.execute(
            "INSERT INTO trust_bank_statements (pmc_id, bank_account_id, first_day, last_day,"
            " opening_balance, closing_balance, line_count, source, file_sha256, imported_by)"
            " VALUES (%s, %s, %s, %s, 0, 5.00, 1, 'csv', %s, %s) RETURNING id",
            (pmc.pmc_id, pmc.operating_bank_id, JAN_1, JAN_31, "f" * 64, staff),
        ).fetchone()
        conn.execute(
            "INSERT INTO trust_bank_statement_lines (pmc_id, statement_id, line_no, posted_on,"
            " amount, description) VALUES (%s, %s, 1, %s, 5.00, 'Deposit')",
            (other.pmc_id, statement, JAN_1),
        )


@pytest.mark.parametrize(
    "lines, why",
    [
        # Each line in cents, even when the lines together come to whole cents.
        ([(JAN_1, "1.001", "Odd", None), (JAN_1, "-0.001", "Odd", None)], "amount_check"),
        ([(JAN_1, "1.00", "Long reference", "r" * 256)], "bank_reference_check"),
    ],
)
def test_a_lines_amount_and_reference_are_checked_on_their_own(conn, pmc, staff, lines, why):
    with refused(why):
        january(conn, pmc, staff, lines)


def test_a_line_is_numbered_from_one(conn, pmc, staff):
    with refused("trust_bank_statement_lines_line_no_check"), conn.transaction():
        conn.execute(
            "INSERT INTO trust_bank_statements (pmc_id, bank_account_id, first_day, last_day,"
            " opening_balance, closing_balance, line_count, source, file_sha256, imported_by)"
            " VALUES (%s, %s, %s, %s, 0, 5.00, 1, 'csv', %s, %s) RETURNING id",
            (pmc.pmc_id, pmc.operating_bank_id, JAN_1, JAN_31, "e" * 64, staff),
        )
        statement = conn.execute(
            "SELECT id FROM trust_bank_statements WHERE file_sha256 = %s", ("e" * 64,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO trust_bank_statement_lines (pmc_id, statement_id, line_no, posted_on,"
            " amount, description) VALUES (%s, %s, 0, %s, 5.00, 'Deposit')",
            (pmc.pmc_id, statement, JAN_1),
        )


def test_a_line_on_the_closing_day_reaches_past_the_closing(conn, pmc, staff):
    early = rent(conn, pmc, "40.00", date(2026, 1, 30))
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, at(JAN_1, 0), at(FEB_1, 0), "40.00")
    _, (line,) = import_statement(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        staff,
        FEB_1,
        FEB_28,
        [(FEB_1, "40.00", "Deposit", None)],
    )

    match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [line], [early], staff)


def test_a_transfer_dated_at_the_closing_is_in_the_open_period(conn, pmc, staff):
    _, (line,) = january(conn, pmc, staff, [(date(2026, 1, 20), "40.00", "Deposit", None)])
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, at(JAN_1, 0), at(FEB_1, 0), "40.00")
    (booked,) = post(
        conn,
        pmc.pmc_id,
        "rent-at-closing",
        [(pmc.operating_cash, pmc.owners[0].account, Decimal("40.00"))],
        at(FEB_1, 0),
    )

    match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [line], [booked], staff)


@pytest.mark.timing
def test_statements_of_one_account_wait_for_each_other(conn, database_url, pmc, staff):
    with psycopg.connect(database_url) as first:
        first.execute(
            "INSERT INTO trust_bank_statements (pmc_id, bank_account_id, first_day, last_day,"
            " opening_balance, closing_balance, line_count, source, file_sha256, imported_by)"
            " VALUES (%s, %s, %s, %s, 0, 0, 0, 'csv', %s, %s)",
            (pmc.pmc_id, pmc.operating_bank_id, JAN_1, JAN_31, "1" * 64, staff),
        )  # not committed yet
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: import_statement(
                worker, pmc.pmc_id, pmc.operating_bank_id, staff, date(2026, 1, 15), FEB_28, []
            ),
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert "starts on 2026-02-01" in str(outcome["error"])
