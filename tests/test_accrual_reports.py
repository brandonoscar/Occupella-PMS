"""An owner's books on the accrual basis (migration 20261021000104; the founder's review q115).

trust_report_income_statement_accrual counts what was charged in the days and bills on their
dates; trust_report_owner_balance_sheet holds, at a day's end, each property account's money in
trust, what tenants (as the delinquency report counts it) and housing authorities owe it, the
bills not yet set aside, and the owner's equity. The golden cases pin the layout; these check the
rules: shares, a housing authority's part, waivers and lease ends, markups and declines.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_agreement,
    add_authority,
    add_tenant,
    add_unit,
    apply_assistance_payment,
    assist,
    charge,
    charge_rent_due,
    co_own,
    decline_bill,
    end_lease,
    enter_bill,
    make_pmc,
    open_lease,
    set_aside_bill,
    set_ownership,
    transfer_batch,
)

JAN_1 = date(2026, 1, 1)
JAN_31 = date(2026, 1, 31)


def at(month, day, hour=12):
    return datetime(2026, month, day, hour, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def lease_at(conn, pmc, rent="1000.00", n=0):
    """A month-to-month lease from January 1 at owner n's property."""
    prop = pmc.owners[n].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, f"Unit {n}")
    tenant = add_tenant(conn, pmc.pmc_id, prop, f"Tenant {n}")
    return open_lease(conn, pmc.pmc_id, unit, JAN_1, None, rent, [tenant])


def income(conn, pmc, n=0, first=JAN_1, last=JAN_31):
    """The owner's lines across their properties: item -> amount."""
    return {
        item.removeprefix("all properties: "): amount
        for _, item, _, amount in conn.execute(
            "SELECT property, item, schedule_e, amount"
            " FROM trust_report_income_statement_accrual(%s, %s, %s, %s)"
            " WHERE property IS NULL AND line > 3",
            (pmc.pmc_id, pmc.owners[n].owner_id, first, last),
        ).fetchall()
    }


def sheet(conn, pmc, n=0, day=JAN_31):
    return {
        item.removeprefix("all properties: "): amount
        for item, amount in conn.execute(
            "SELECT item, amount FROM trust_report_owner_balance_sheet(%s, %s, %s)"
            " WHERE property IS NULL AND line > 3",
            (pmc.pmc_id, pmc.owners[n].owner_id, day),
        ).fetchall()
    }


def test_a_property_owned_in_shares_counts_each_owners_share_of_what_was_charged(conn, pmc):
    theirs = co_own(conn, pmc, owner=1, prop=0)
    set_ownership(
        conn,
        pmc.pmc_id,
        pmc.owners[0].property_id,
        JAN_1,
        [(pmc.owners[0].account, Decimal("60")), (theirs, Decimal("40"))],
    )
    lease_at(conn, pmc, "1000.01")
    charge_rent_due(conn, pmc.pmc_id, JAN_1)

    assert income(conn, pmc, 0)["income: rent"] == Decimal("600.01")
    # Owner 2's share of Property 1, beside their own Property 2 with nothing charged.
    assert income(conn, pmc, 1)["income: rent"] == Decimal("400.00")
    assert sheet(conn, pmc, 0)["owed by tenants"] == Decimal("600.01")
    assert sheet(conn, pmc, 1)["owed by tenants"] == Decimal("400.00")


def test_a_housing_authoritys_part_is_rent_charged_and_owed_until_it_pays(conn, pmc):
    lease = lease_at(conn, pmc)
    assist(conn, pmc.pmc_id, lease, add_authority(conn, pmc.pmc_id), JAN_1, "700.00")
    charge_rent_due(conn, pmc.pmc_id, JAN_1)
    part = conn.execute(
        "SELECT id FROM trust_assistance_charges WHERE lease_id = %s", (lease,)
    ).fetchone()[0]

    before = sheet(conn, pmc, day=date(2026, 1, 9))
    paid = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "700.00")], at(1, 10))[
        0
    ]
    apply_assistance_payment(conn, pmc.pmc_id, part, paid, "700.00")

    assert income(conn, pmc)["income: rent"] == Decimal("1000.00")  # 300.00 + 700.00
    assert (before["owed by tenants"], before["owed by housing authorities"]) == (
        Decimal("300.00"),
        Decimal("700.00"),
    )
    after = sheet(conn, pmc)
    assert after["owed by housing authorities"] == Decimal("0.00")
    assert after["cash held in trust"] == Decimal("700.00")
    assert after["owner's equity"] == Decimal("1000.00")


def test_a_waiver_and_a_lease_ends_credit_come_off_what_they_change(conn, pmc):
    lease = lease_at(conn, pmc, "3100.00")
    charge_rent_due(conn, pmc.pmc_id, JAN_1)
    key = charge(conn, pmc.pmc_id, lease, date(2026, 1, 5), "75.00", "fee", "Key replacement")
    waiver = charge(conn, pmc.pmc_id, lease, date(2026, 1, 6), "25.00", "credit", "Waived")
    conn.execute(
        "INSERT INTO trust_charge_waivers (credit_charge_id, pmc_id, waived_charge_id, reason)"
        " VALUES (%s, %s, %s, 'Lost in the move')",
        (waiver, pmc.pmc_id, key),
    )
    end_lease(conn, pmc.pmc_id, lease, date(2026, 1, 10))  # 21 of January's 31 days credited
    charge(conn, pmc.pmc_id, lease, date(2026, 1, 8), "50.00", "credit", "Goodwill")

    lines = income(conn, pmc)

    # 3100.00 less 2100.00 for the days after the end, less the 50.00 credit given on its own.
    assert lines["income: rent"] == Decimal("950.00")
    assert lines["income: other charges"] == Decimal("50.00")
    assert lines["total income"] == Decimal("1000.00")
    assert sheet(conn, pmc)["owed by tenants"] == Decimal("1000.00")


def test_bills_count_on_their_date_with_their_markup_and_never_once_declined(conn, pmc):
    add_agreement(
        conn,
        pmc.pmc_id,
        pmc.owners[0].account,
        JAN_1,
        approval_limit="1000.00",
        bill_markup_percent="10",
    )
    transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "500.00")], at(1, 2))
    fix = enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        pmc.owners[0].account,
        "FIX-1",
        date(2026, 1, 20),
        date(2026, 2, 5),
        "100.00",
    )
    conn.execute(
        "INSERT INTO trust_bill_markups (bill_id, pmc_id) VALUES (%s, %s)", (fix, pmc.pmc_id)
    )
    paint = enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        pmc.owners[0].account,
        "PAINT-1",
        date(2026, 1, 21),
        date(2026, 2, 5),
        "300.00",
    )
    decline_bill(conn, pmc.pmc_id, paint, date(2026, 1, 25), "Owner 1, by email")

    lines, unpaid = income(conn, pmc), sheet(conn, pmc)
    early = sheet(conn, pmc, day=date(2026, 1, 22))
    set_aside_bill(conn, pmc.pmc_id, fix, at(2, 1))
    paid = sheet(conn, pmc, day=date(2026, 2, 1))

    assert lines["expense: bills not in an expense account"] == Decimal("100.00")
    assert lines["expense: management fees"] == Decimal("10.00")
    assert lines["net income"] == Decimal("-110.00")
    # Before its decline the painting is owed; once declined, never.
    assert early["bills not yet set aside"] == Decimal("-410.00")
    assert unpaid["bills not yet set aside"] == Decimal("-110.00")
    assert unpaid["owner's equity"] == Decimal("390.00")
    assert (paid["cash held in trust"], paid["bills not yet set aside"]) == (
        Decimal("390.00"),
        Decimal("0.00"),
    )


def test_another_owners_and_companys_money_is_left_out(conn, pmc):
    theirs = make_pmc(conn)
    lease_at(conn, pmc, n=1)
    lease_at(conn, theirs)
    charge_rent_due(conn, pmc.pmc_id, JAN_1)
    charge_rent_due(conn, theirs.pmc_id, JAN_1)

    assert income(conn, pmc, 0)["total income"] == Decimal("0.00")
    assert sheet(conn, pmc, 0)["owed by tenants"] == Decimal("0.00")
    assert income(conn, pmc, 1)["total income"] == Decimal("1000.00")


@pytest.mark.parametrize(
    ("query", "args"),
    [
        ("trust_report_income_statement_accrual(%s, %s, %s, %s)", (JAN_31, JAN_1)),
        ("trust_report_income_statement_accrual(%s, %s, %s, %s)", (None, JAN_1)),
        ("trust_report_owner_balance_sheet(%s, %s, %s)", (None,)),
    ],
)
def test_bad_days_are_refused(conn, pmc, query, args):
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        conn.execute(f"SELECT * FROM {query}", (pmc.pmc_id, pmc.owners[0].owner_id, *args))


@pytest.mark.parametrize(
    "query",
    [
        "trust_report_income_statement_accrual(%s, %s, '2026-01-01', '2026-01-31')",
        "trust_report_owner_balance_sheet(%s, %s, '2026-01-31')",
    ],
)
def test_an_owner_of_another_company_is_refused(conn, pmc, query):
    other = make_pmc(conn)
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no owner"):
        conn.execute(f"SELECT * FROM {query}", (pmc.pmc_id, other.owners[0].owner_id))


def test_the_ai_reads_both(conn, ai_conn, pmc):
    lease_at(conn, pmc)
    charge_rent_due(conn, pmc.pmc_id, JAN_1)

    assert income(ai_conn, pmc)["income: rent"] == Decimal("1000.00")
    assert sheet(ai_conn, pmc)["owed by tenants"] == Decimal("1000.00")
