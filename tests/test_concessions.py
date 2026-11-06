"""Concessions (migration 20261106000242): a credit staff mark as a concession, given once or every
month, is a discount on rent the accrual income statement counts on a line of its own, "income:
concessions", a contra to rent (code 4010 in the chart), never folded into rent or the monthly
charges; only a credit is one. It moves no money: rent first still takes it off the lease's rent."""

from datetime import date
from decimal import Decimal

import psycopg
import pytest
from helpers import add_unit, charge, charge_recurring_due, make_pmc, open_lease

JAN_1 = date(2026, 1, 1)
FEB_1 = date(2026, 2, 1)


def one(conn, query, params=()):
    return conn.execute(query, params).fetchone()[0]


@pytest.fixture
def home(conn):
    pmc = make_pmc(conn, owners=1)
    tenant = one(
        conn,
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    )
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "1000.00", [tenant])
    return pmc, lease


def concession(conn, pmc, lease, amount, kind="credit", on=JAN_1):
    return one(
        conn,
        "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo, concession)"
        " VALUES (%s, %s, %s, %s, %s, 'First month 10%% off', true) RETURNING id",
        (pmc.pmc_id, lease, on, kind, Decimal(amount)),
    )


def monthly_concession(conn, pmc, lease, amount, kind="credit"):
    return one(
        conn,
        "INSERT INTO trust_recurring_charges (pmc_id, lease_id, kind, amount, memo, starts_on,"
        " concession) VALUES (%s, %s, %s, %s, 'Military discount', %s, true) RETURNING id",
        (pmc.pmc_id, lease, kind, Decimal(amount), JAN_1),
    )


def accrual(conn, pmc, through=date(2026, 2, 28)):
    rows = conn.execute(
        "SELECT property, item, schedule_e, amount"
        " FROM trust_report_income_statement_accrual(%s, %s, %s, %s) ORDER BY line",
        (pmc.pmc_id, pmc.owners[0].owner_id, JAN_1, through),
    ).fetchall()
    return {item: amount for prop, item, _, amount in rows if prop is not None}, rows


def test_only_a_credit_is_a_concession(conn, home):
    pmc, lease = home

    with pytest.raises(psycopg.errors.CheckViolation, match="concession"):
        concession(conn, pmc, lease, "100.00", kind="fee")
    with pytest.raises(psycopg.errors.CheckViolation, match="concession"):
        monthly_concession(conn, pmc, lease, "50.00", kind="fee")
    assert (
        one(
            conn,
            "SELECT concession FROM trust_charges WHERE id = %s",
            (charge(conn, pmc.pmc_id, lease, JAN_1, "1000.00"),),
        )
        is False
    )


def test_the_accrual_statement_counts_concessions_on_their_own_line_against_rent(conn, home):
    pmc, lease = home
    for due in (JAN_1, FEB_1):
        charge(conn, pmc.pmc_id, lease, due, "1000.00")
    concession(conn, pmc, lease, "100.00")
    monthly_concession(conn, pmc, lease, "50.00")
    for due in (JAN_1, FEB_1):
        charge_recurring_due(conn, pmc.pmc_id, due)
    # A credit not marked a concession still comes off rent, as before.
    charge(conn, pmc.pmc_id, lease, FEB_1, "25.00", kind="credit", memo="Goodwill")

    found, rows = accrual(conn, pmc)

    assert found["income: rent"] == Decimal("1975.00")
    assert found["income: concessions"] == Decimal("-200.00")
    assert found["income: recurring charges"] == Decimal("0.00")
    assert found["total income"] == Decimal("1775.00")
    assert found["Schedule E line 3: Rents received"] == Decimal("1775.00")
    items = [item for prop, item, _, _ in rows if prop is not None]
    # Right after rent, a contra to it, on Schedule E's line 3.
    assert items[items.index("income: rent") + 1] == "income: concessions"
    assert [s for _, item, s, _ in rows if item == "income: concessions"] == [3]
    assert [a for _, item, _, a in rows if item == "all properties: income: concessions"] == [
        Decimal("-200.00")
    ]


def test_without_a_concession_the_statement_has_no_such_line(conn, home):
    pmc, lease = home
    charge(conn, pmc.pmc_id, lease, JAN_1, "1000.00")

    found, _ = accrual(conn, pmc)

    assert "income: concessions" not in found
    assert found["income: rent"] == Decimal("1000.00")


def test_a_concession_comes_off_rent_first_as_any_credit(conn, home):
    pmc, lease = home
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, "1000.00")
    concession(conn, pmc, lease, "100.00")

    assert one(
        conn,
        "SELECT o.open FROM trust_open_charges(%s, %s, 'infinity') AS o WHERE o.charge_id = %s",
        (pmc.pmc_id, lease, rent),
    ) == Decimal("900.00")


def statement(conn, pmc, start=JAN_1, end=date(2026, 3, 1)):
    return conn.execute(
        "SELECT property, item, detail, amount, balance"
        " FROM trust_report_owner_statement(%s, %s, %s::timestamptz, %s::timestamptz)"
        " ORDER BY line",
        (pmc.pmc_id, pmc.owners[0].owner_id, f"{start} 00:00+00", f"{end} 00:00+00"),
    ).fetchall()


def test_the_owner_statement_says_the_concessions_given_in_the_period_on_a_memo_line(conn, home):
    pmc, lease = home
    for due in (JAN_1, FEB_1):
        charge(conn, pmc.pmc_id, lease, due, "1000.00")
    concession(conn, pmc, lease, "100.00")
    monthly_concession(conn, pmc, lease, "50.00")
    for due in (JAN_1, FEB_1):
        charge_recurring_due(conn, pmc.pmc_id, due)
    charge(conn, pmc.pmc_id, lease, FEB_1, "25.00", kind="credit", memo="Goodwill")

    rows = statement(conn, pmc)
    february = statement(conn, pmc, start=FEB_1)

    memos = [
        (item, detail, amount, balance)
        for _, item, detail, amount, balance in rows
        if item.startswith("memo")
    ]
    assert memos == [
        (
            "memo: concessions",
            "given in the period, credits off rent (not money moved)",
            Decimal("200.00"),
            None,
        ),
    ]
    # After the property's closing balance, which it leaves as it is.
    items = [item for _, item, *_ in rows]
    assert items.index("memo: concessions") == items.index("closing balance") + 1
    assert [a for _, item, _, a, _ in february if item == "memo: concessions"] == [Decimal("50.00")]
    assert "memo: concessions" not in [
        item for _, item, *_ in statement(conn, pmc, start=date(2026, 3, 1), end=date(2026, 4, 1))
    ]


def test_no_new_kind_of_fee_is_named_concessions(conn, home):
    pmc, _ = home

    with pytest.raises(psycopg.errors.CheckViolation, match="not_concessions"):
        conn.execute(
            "INSERT INTO trust_charge_types (pmc_id, name) VALUES (%s, 'Concessions')",
            (pmc.pmc_id,),
        )
