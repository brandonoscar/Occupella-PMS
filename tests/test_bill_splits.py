"""A bill split across expense accounts (migration 0069, ADR 0006): a bill's latest row in
trust_bill_expense_accounts is its account, and when lines name that row the bill is split by
them. A row's lines split their own bill, each account once, and come to the bill's amount at
commit. Append-only and audited; the income statement counts each line in its account (the
golden case split_bills_2026 works one through by hand)."""

import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from helpers import enter_bill, make_pmc

TODAY = datetime.now(UTC).date()


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def account(conn, pmc, name, line=14):
    return conn.execute(
        "INSERT INTO trust_expense_accounts (pmc_id, name, schedule_e_line) VALUES (%s, %s, %s)"
        " RETURNING id",
        (pmc.pmc_id, name, line),
    ).fetchone()[0]


def bill(conn, pmc, amount="300.00", reference="FIX-1"):
    return enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, reference, TODAY, TODAY, amount
    )


def row(conn, pmc, bill_id, expense):
    return conn.execute(
        "INSERT INTO trust_bill_expense_accounts (pmc_id, bill_id, expense_account_id)"
        " VALUES (%s, %s, %s) RETURNING id",
        (pmc.pmc_id, bill_id, expense),
    ).fetchone()[0]


def line(conn, pmc, bill_id, categorized, expense, amount):
    conn.execute(
        "INSERT INTO trust_bill_splits"
        " (pmc_id, bill_id, categorized_id, expense_account_id, amount)"
        " VALUES (%s, %s, %s, %s, %s)",
        (pmc.pmc_id, bill_id, categorized, expense, amount),
    )


def test_lines_that_come_to_the_bill_split_it(conn, pmc):
    repairs, supplies = account(conn, pmc, "Repairs"), account(conn, pmc, "Supplies", 15)
    fix = bill(conn, pmc)

    with conn.transaction():
        head = row(conn, pmc, fix, repairs)
        line(conn, pmc, fix, head, repairs, "200.00")
        line(conn, pmc, fix, head, supplies, "100.00")

    assert conn.execute(
        "SELECT expense_account_id, amount FROM trust_bill_splits WHERE categorized_id = %s"
        " ORDER BY id",
        (head,),
    ).fetchall() == [(repairs, 200), (supplies, 100)]


@pytest.mark.parametrize("amounts", [["200.00", "50.00"], ["200.00", "150.00"]])
def test_lines_that_dont_come_to_the_bill_are_refused_at_commit(conn, pmc, amounts):
    repairs, supplies = account(conn, pmc, "Repairs"), account(conn, pmc, "Supplies", 15)
    fix = bill(conn, pmc)

    with (
        pytest.raises(psycopg.errors.CheckViolation, match="lines come to .*, not its amount"),
        conn.transaction(),
    ):
        head = row(conn, pmc, fix, repairs)
        line(conn, pmc, fix, head, repairs, amounts[0])
        line(conn, pmc, fix, head, supplies, amounts[1])

    assert conn.execute(
        "SELECT count(*) FROM trust_bill_splits WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_a_line_splits_only_its_own_bills_row_each_account_once(conn, pmc):
    repairs, supplies = account(conn, pmc, "Repairs"), account(conn, pmc, "Supplies", 15)
    fix, other = bill(conn, pmc), bill(conn, pmc, reference="FIX-2")
    theirs = row(conn, pmc, other, repairs)

    with (
        pytest.raises(psycopg.errors.CheckViolation, match="split a row of that bill"),
        conn.transaction(),
    ):
        line(conn, pmc, fix, theirs, repairs, "300.00")
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        head = row(conn, pmc, fix, repairs)
        line(conn, pmc, fix, head, repairs, "150.00")
        line(conn, pmc, fix, head, repairs, "150.00")
    # Lines added to a row already split would come to more than the bill.
    with conn.transaction():
        head = row(conn, pmc, fix, repairs)
        line(conn, pmc, fix, head, repairs, "200.00")
        line(conn, pmc, fix, head, supplies, "100.00")
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        line(conn, pmc, fix, head, account(conn, pmc, "Water", 17), "1.00")


def test_a_line_is_whole_cents_of_an_account_of_the_bills_company(conn, pmc):
    fix = bill(conn, pmc)
    head = row(conn, pmc, fix, account(conn, pmc, "Repairs"))
    theirs = account(conn, make_pmc(conn), "Repairs")

    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        line(conn, pmc, fix, head, account(conn, pmc, "Supplies", 15), "0.001")
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        line(conn, pmc, fix, head, theirs, "300.00")
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        line(conn, pmc, uuid.uuid4(), head, theirs, "300.00")


def test_lines_are_never_rewritten(conn, pmc):
    repairs, supplies = account(conn, pmc, "Repairs"), account(conn, pmc, "Supplies", 15)
    fix = bill(conn, pmc)
    with conn.transaction():
        head = row(conn, pmc, fix, repairs)
        line(conn, pmc, fix, head, repairs, "200.00")
        line(conn, pmc, fix, head, supplies, "100.00")

    for change in ("UPDATE trust_bill_splits SET amount = 1", "DELETE FROM trust_bill_splits"):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(change)
