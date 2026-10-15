"""Budgets (migration 20261015000055): a month's budget of an owner's property account on one
of the income statement's lines, its latest row counting, and trust_report_budget_vs_actual,
which lays an owner's budget beside their income statement. Its golden case
(tests/golden/cases/trust_report_budget_vs_actual/year_2026.sql) pins the layout."""

import uuid
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from helpers import make_pmc


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def add_budget(conn, pmc_id, account, month, line, amount, expense_account_id=None):
    conn.execute(
        "INSERT INTO trust_budgets (pmc_id, ledger_account_id, month, line, expense_account_id,"
        " amount) VALUES (%s, %s, %s, %s, %s, %s)",
        (pmc_id, account, month, line, expense_account_id, Decimal(amount)),
    )


def budgeted(conn, pmc, start, through, item="income: rent"):
    owner = pmc.owners[0]
    (row,) = [
        r
        for r in conn.execute(
            "SELECT property, item, budget FROM trust_report_budget_vs_actual(%s, %s, %s, %s)",
            (pmc.pmc_id, owner.owner_id, start, through),
        ).fetchall()
        if r[0] == "Property 1" and r[1] == item
    ]
    return row[2]


def test_a_budget_is_of_an_owners_property_account_of_its_own_pmc(conn, pmc):
    other = make_pmc(conn)
    for account in (pmc.operating_cash, pmc.tenant_deposit, other.owners[0].account):
        with pytest.raises(psycopg.errors.CheckViolation, match="not an owner's property account"):
            add_budget(conn, pmc.pmc_id, account, date(2026, 1, 1), "rent", "1.00")


def test_a_budget_is_a_months_whole_cents_on_a_known_line(conn, pmc):
    account = pmc.owners[0].account
    for month, line, amount, match in [
        (date(2026, 1, 2), "rent", "1.00", "month_check"),
        (date(2026, 1, 1), "parking", "1.00", "line_check"),
        (date(2026, 1, 1), "rent", "-1.00", "amount_check"),
        (date(2026, 1, 1), "rent", "1.001", "amount_check"),
        (date(2026, 1, 1), "expense account", "1.00", "trust_budgets_check"),
    ]:
        with pytest.raises(psycopg.errors.CheckViolation, match=match):
            add_budget(conn, pmc.pmc_id, account, month, line, amount)
    other = make_pmc(conn)
    theirs = conn.execute(
        "INSERT INTO trust_expense_accounts (pmc_id, name, schedule_e_line)"
        " VALUES (%s, 'Repairs', 14) RETURNING id",
        (other.pmc_id,),
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        add_budget(conn, pmc.pmc_id, account, date(2026, 1, 1), "expense account", "1.00", theirs)


def test_budgets_are_append_only(conn, pmc):
    add_budget(conn, pmc.pmc_id, pmc.owners[0].account, date(2026, 1, 1), "rent", "1.00")
    for query in ("UPDATE trust_budgets SET amount = 2", "DELETE FROM trust_budgets"):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(query)


def test_a_lines_latest_row_is_its_budget(conn, pmc):
    account = pmc.owners[0].account
    add_budget(conn, pmc.pmc_id, account, date(2026, 1, 1), "rent", "1400.00")
    add_budget(conn, pmc.pmc_id, account, date(2026, 1, 1), "rent", "1500.00")
    assert budgeted(conn, pmc, date(2026, 1, 1), date(2026, 1, 31)) == Decimal("1500.00")
    add_budget(conn, pmc.pmc_id, account, date(2026, 1, 1), "rent", "0.00")
    assert budgeted(conn, pmc, date(2026, 1, 1), date(2026, 1, 31)) == Decimal("0.00")


def test_the_report_counts_the_months_that_start_within_its_days(conn, pmc):
    account = pmc.owners[0].account
    for month in (1, 2, 3):
        add_budget(conn, pmc.pmc_id, account, date(2026, month, 1), "rent", "100.00")

    assert budgeted(conn, pmc, date(2026, 1, 1), date(2026, 3, 31)) == Decimal("300.00")
    assert budgeted(conn, pmc, date(2026, 1, 1), date(2026, 3, 1)) == Decimal("300.00")
    assert budgeted(conn, pmc, date(2026, 1, 2), date(2026, 2, 28)) == Decimal("100.00")
    assert budgeted(conn, pmc, date(2026, 1, 1), date(2026, 2, 28)) == Decimal("200.00")


def test_the_report_is_the_owners_and_says_what_is_wrong(conn, ai_conn, pmc):
    add_budget(conn, pmc.pmc_id, pmc.owners[1].account, date(2026, 1, 1), "rent", "999.00")
    rows = ai_conn.execute(
        "SELECT property, item, budget FROM trust_report_budget_vs_actual(%s, %s, %s, %s)",
        (pmc.pmc_id, pmc.owners[0].owner_id, date(2026, 1, 1), date(2026, 12, 31)),
    ).fetchall()
    assert ("Property 2", "income: rent", Decimal("999.00")) not in rows
    assert ("Property 1", "income: rent", Decimal("0.00")) in rows

    with pytest.raises(psycopg.errors.InvalidParameterValue):
        conn.execute(
            "SELECT * FROM trust_report_budget_vs_actual(%s, %s, %s, %s)",
            (pmc.pmc_id, uuid.uuid4(), date(2026, 1, 1), date(2026, 12, 31)),
        ).fetchall()
