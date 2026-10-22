"""Expense accounts and an owner's income statement (migration 20261015000052).

A PMC's expense accounts each sit on a line of Schedule E; a bill's latest row in
trust_bill_expense_accounts is its account, and a recurring bill's account goes on each month's
bill it enters. trust_report_income_statement sums an owner's property accounts over days (UTC)
on a cash basis: what was collected by what it paid, the fees, the bills set aside by expense
account, net income, and the same by Schedule E line. The golden case pins the layout; these
check the rules.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_agreement,
    add_late_fee_policy,
    add_nsf_fee_policy,
    add_recurring_bill,
    add_recurring_charge,
    add_unit,
    apply_payment,
    approve_bill,
    assess_late_fees,
    charge,
    charge_nsf_fee,
    charge_recurring_due,
    contribute,
    draw_owner,
    end_lease,
    enter_bill,
    enter_recurring_bills,
    make_pmc,
    open_account,
    open_lease,
    post,
    post_leasing_fee,
    post_management_fee,
    reverse_payment,
    set_aside_bill,
    transfer_batch,
)

JAN_1 = date(2026, 1, 1)
YEAR = (date(2026, 1, 1), date(2026, 12, 31))


def at(month, day, year=2026, hour=12):
    return datetime(year, month, day, hour, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def lease(conn, pmc):
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "1500.00", [tenant])


def expense_account(conn, pmc, name, line):
    return conn.execute(
        "INSERT INTO trust_expense_accounts (pmc_id, name, schedule_e_line) VALUES (%s, %s, %s)"
        " RETURNING id",
        (pmc.pmc_id, name, line),
    ).fetchone()[0]


def put_in(conn, pmc, bill, account):
    conn.execute(
        "INSERT INTO trust_bill_expense_accounts (pmc_id, bill_id, expense_account_id)"
        " VALUES (%s, %s, %s)",
        (pmc.pmc_id, bill, account),
    )


def statement(conn, pmc, days=YEAR, owner=None):
    return conn.execute(
        "SELECT line, property, item, schedule_e, amount"
        " FROM trust_report_income_statement(%s, %s, %s, %s)",
        (pmc.pmc_id, owner or pmc.owners[0].owner_id, *days),
    ).fetchall()


def lines(conn, pmc, days=YEAR, prop="Property 1"):
    """One property's lines, by item."""
    return {item: amount for _, p, item, _, amount in statement(conn, pmc, days) if p == prop}


def paid(conn, pmc, charged, amount, when, account=None):
    (transfer,) = transfer_batch(
        conn, [(pmc.operating_cash, account or pmc.owners[0].account, amount)], when
    )
    apply_payment(conn, pmc.pmc_id, charged, transfer, amount)
    return transfer


def bill(conn, pmc, reference, amount, when, account=None):
    """A bill against Owner 1's account (or `account`), approved and set aside at `when`."""
    entered = enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        account or pmc.owners[0].account,
        reference,
        when.date(),
        when.date(),
        amount,
    )
    approve_bill(conn, pmc.pmc_id, entered, when.date(), "Owner 1, by email")
    set_aside_bill(conn, pmc.pmc_id, entered, when)
    return entered


def test_what_was_collected_is_income_by_what_it_paid(conn, app_conn, pmc, lease):
    property_id = pmc.owners[0].property_id
    add_late_fee_policy(conn, pmc.pmc_id, property_id, JAN_1, 5, flat_fee="50.00")
    add_nsf_fee_policy(conn, pmc.pmc_id, property_id, JAN_1, "35.00")
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, "1500.00", charged_at=at(1, 1, hour=6))
    add_recurring_charge(conn, pmc.pmc_id, lease, "40.00", JAN_1, "Parking")
    charge_recurring_due(conn, pmc.pmc_id, JAN_1)
    assess_late_fees(conn, pmc.pmc_id, date(2026, 1, 7))
    other = charge(conn, pmc.pmc_id, lease, date(2026, 1, 9), "25.00", "fee", "Key")
    charge(conn, pmc.pmc_id, lease, date(2026, 1, 9), "100.00", "credit", "Goodwill")
    # A payment that came back, and the NSF fee it was charged.
    meta = {"kind": "tenant payment", "lease_id": str(lease), "reference": None, "key": "p1"}
    (sent,) = post(
        conn,
        pmc.pmc_id,
        "p1",
        [(pmc.operating_cash, pmc.owners[0].account, "10.00")],
        at(1, 9),
        meta,
    )
    apply_payment(conn, pmc.pmc_id, other, sent, "10.00")
    meta = {**meta, "kind": "bounced payment", "key": "bounce/p1"}
    (back,) = post(
        conn,
        pmc.pmc_id,
        "bounce/p1",
        [(pmc.owners[0].account, pmc.operating_cash, "10.00")],
        at(1, 10),
        meta,
    )
    reverse_payment(conn, pmc.pmc_id, other, sent, back, "10.00")
    nsf = charge_nsf_fee(app_conn, pmc.pmc_id, "bounce/p1")
    parking, late = (
        conn.execute(
            "SELECT id FROM trust_charges WHERE lease_id = %s AND memo LIKE %s",
            (lease, pattern),
        ).fetchone()[0]
        for pattern in ("Parking%", "Late fee%")
    )
    for charged, amount in [
        (rent, "1500.00"),
        (parking, "40.00"),
        (late, "50.00"),
        (nsf, "35.00"),
        (other, "25.00"),
    ]:
        paid(conn, pmc, charged, amount, at(1, 12))

    found = lines(app_conn, pmc)

    assert [
        found[f"income: {part}"]
        for part in ("rent", "recurring charges", "late fees", "NSF fees", "other charges")
    ] == [
        Decimal("1500.00"),
        Decimal("40.00"),
        Decimal("50.00"),
        Decimal("35.00"),
        Decimal("25.00"),  # 10.00 paid and bounced, then 25.00: a credit is never income
    ]
    assert found["total income"] == Decimal("1650.00")
    assert found["Schedule E line 3: Rents received"] == Decimal("1650.00")


def test_expenses_are_the_fees_and_each_expense_accounts_bills(conn, app_conn, pmc, lease):
    account = pmc.owners[0].account
    add_agreement(conn, pmc.pmc_id, account, JAN_1, "10", leasing_fee_percent="50")
    paid(conn, pmc, charge(conn, pmc.pmc_id, lease, JAN_1, "1500.00"), "1500.00", at(1, 2))
    post_leasing_fee(conn, pmc.pmc_id, lease, account, at(1, 3))  # 750.00
    post_management_fee(conn, pmc.pmc_id, account, JAN_1, date(2026, 2, 1), at(1, 31))  # 150.00
    repairs = expense_account(conn, pmc, "Repairs", 14)
    insurance = expense_account(conn, pmc, "Insurance", 9)
    put_in(conn, pmc, bill(conn, pmc, "R-1", "120.00", at(2, 1)), repairs)
    put_in(conn, pmc, bill(conn, pmc, "R-2", "80.00", at(2, 2)), repairs)
    put_in(conn, pmc, bill(conn, pmc, "I-1", "200.00", at(2, 3)), insurance)
    bill(conn, pmc, "X-1", "15.00", at(2, 4))  # in no expense account
    # Draws and contributions move the owner's money, but are not income or expenses.
    contribute(conn, pmc.pmc_id, account, "c1", "500.00", at(3, 1))
    draw_owner(conn, pmc.pmc_id, account, "d1", "100.00", at(3, 2))

    found = lines(app_conn, pmc)

    assert {item: amount for item, amount in found.items() if item.startswith("expense")} == {
        "expense: management fees": Decimal("150.00"),
        "expense: leasing fees": Decimal("750.00"),
        "expense: Insurance": Decimal("200.00"),
        "expense: Repairs": Decimal("200.00"),
        "expense: bills not in an expense account": Decimal("15.00"),
    }
    assert (found["total expenses"], found["net income"]) == (
        Decimal("1315.00"),
        Decimal("185.00"),
    )
    assert {item: amount for item, amount in found.items() if item.startswith("Schedule E")} == {
        "Schedule E line 3: Rents received": Decimal("1500.00"),
        "Schedule E line 8: Commissions": Decimal("750.00"),
        "Schedule E line 9: Insurance": Decimal("200.00"),
        "Schedule E line 11: Management fees": Decimal("150.00"),
        "Schedule E line 14: Repairs": Decimal("200.00"),
        "Schedule E: on no line, bills not in an expense account": Decimal("15.00"),
    }


def test_a_bills_latest_expense_account_is_its_account(conn, pmc):
    water = expense_account(conn, pmc, "Water", 17)
    repairs = expense_account(conn, pmc, "Repairs", 14)
    contribute(conn, pmc.pmc_id, pmc.owners[0].account, "c1", "500.00", at(1, 1))
    entered = bill(conn, pmc, "W-1", "60.00", at(1, 5))
    put_in(conn, pmc, entered, repairs)
    put_in(conn, pmc, entered, water)  # it was the water bill

    found = lines(conn, pmc)

    assert found["expense: Water"] == Decimal("60.00")
    assert "expense: Repairs" not in found
    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute("UPDATE trust_bill_expense_accounts SET expense_account_id = %s", (repairs,))


def test_a_bill_is_put_only_in_an_expense_account_of_its_own_pmc(conn, pmc):
    other = make_pmc(conn)
    theirs = expense_account(conn, other, "Repairs", 14)
    entered = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, "B-1", JAN_1, JAN_1, "10.00"
    )

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        put_in(conn, pmc, entered, theirs)


@pytest.mark.parametrize("line", [3, 4, 20, None])
def test_an_expense_account_is_on_an_expense_line_of_schedule_e(conn, pmc, line):
    with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation)):
        expense_account(conn, pmc, "Odd", line)


def test_two_expense_accounts_of_a_pmc_take_two_names(conn, pmc):
    expense_account(conn, pmc, "Repairs", 14)
    expense_account(conn, make_pmc(conn), "Repairs", 14)  # another PMC's may share it
    with pytest.raises(psycopg.errors.UniqueViolation):
        expense_account(conn, pmc, "Repairs", 7)


def test_each_month_a_recurring_bill_enters_is_in_its_latest_expense_account(conn, app_conn, pmc):
    landscaping = expense_account(conn, pmc, "Landscaping", 7)
    cleaning = expense_account(conn, pmc, "Cleaning", 7)
    recurring = add_recurring_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, "LAWN", "30.00", 1, JAN_1
    )
    enter_recurring_bills(app_conn, pmc.pmc_id, date(2026, 1, 31))  # January: in none yet
    for account in (cleaning, landscaping):
        conn.execute(
            "INSERT INTO trust_recurring_bill_expense_accounts"
            " (pmc_id, recurring_bill_id, expense_account_id) VALUES (%s, %s, %s)",
            (pmc.pmc_id, recurring, account),
        )

    assert enter_recurring_bills(app_conn, pmc.pmc_id, date(2026, 3, 31)) == 2
    assert enter_recurring_bills(app_conn, pmc.pmc_id, date(2026, 3, 31)) == 0  # a retry

    accounts = conn.execute(
        "SELECT b.reference, (SELECT e.name FROM trust_bill_expense_accounts AS x"
        "  JOIN trust_expense_accounts AS e ON e.id = x.expense_account_id"
        "  WHERE x.bill_id = b.id ORDER BY x.id DESC LIMIT 1),"
        " (SELECT count(*) FROM trust_bill_expense_accounts AS x WHERE x.bill_id = b.id)"
        " FROM trust_bills AS b WHERE b.pmc_id = %s ORDER BY b.reference",
        (pmc.pmc_id,),
    ).fetchall()
    assert accounts == [
        ("LAWN-2026-01", None, 0),
        ("LAWN-2026-02", "Landscaping", 1),
        ("LAWN-2026-03", "Landscaping", 1),
    ]


def test_the_days_run_in_utc_through_the_last_one_whatever_the_sessions_time_zone(
    conn, database_url, pmc, lease
):
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, "1500.00")
    paid(conn, pmc, rent, "100.00", datetime(2026, 3, 31, 23, 59, 59, tzinfo=UTC))
    paid(conn, pmc, rent, "40.00", datetime(2026, 4, 1, tzinfo=UTC))
    paid(conn, pmc, rent, "7.00", datetime(2026, 2, 28, 23, 59, 59, tzinfo=UTC))

    readings = []
    for zone in ("UTC", "America/Los_Angeles", "Pacific/Auckland"):
        with psycopg.connect(database_url, autocommit=True) as session:
            session.execute(f"SET TIME ZONE '{zone}'")
            readings.append(
                lines(session, pmc, (date(2026, 3, 1), date(2026, 3, 31)))["total income"]
            )

    assert readings == [Decimal("100.00")] * 3


def test_a_bounce_comes_off_the_days_it_bounced_in(conn, pmc, lease):
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, "1500.00")
    payment = paid(conn, pmc, rent, "1500.00", at(1, 30))
    (back,) = transfer_batch(
        conn, [(pmc.owners[0].account, pmc.operating_cash, "1500.00")], at(2, 2)
    )
    reverse_payment(conn, pmc.pmc_id, rent, payment, back, "1500.00")

    january = (date(2026, 1, 1), date(2026, 1, 31))
    february = (date(2026, 2, 1), date(2026, 2, 28))
    assert lines(conn, pmc, january)["income: rent"] == Decimal("1500.00")
    assert lines(conn, pmc, february)["income: rent"] == Decimal("-1500.00")


def test_a_lease_ends_adjustment_is_what_it_adjusts(conn, pmc, lease):
    conn.execute("SELECT trust_charge_rent_due(%s, %s)", (pmc.pmc_id, JAN_1))
    rent = conn.execute(
        "SELECT id FROM trust_charges WHERE lease_id = %s AND kind = 'rent'", (lease,)
    ).fetchone()[0]
    end_lease(conn, pmc.pmc_id, lease, date(2026, 1, 16))  # a credit for 15 days
    end_lease(conn, pmc.pmc_id, lease, date(2026, 1, 21))  # moved out later: a fee for 5 back
    days_back = conn.execute(
        "SELECT a.adjustment_charge_id, c.amount FROM trust_lease_end_adjustments AS a"
        " JOIN trust_charges AS c ON c.id = a.adjustment_charge_id"
        " WHERE a.charge_id = %s AND c.kind = 'fee'",
        (rent,),
    ).fetchone()
    assert days_back is not None
    paid(conn, pmc, days_back[0], days_back[1], at(1, 25))

    found = lines(conn, pmc)

    assert (found["income: rent"], found["income: other charges"]) == (days_back[1], Decimal(0))


def test_only_the_owners_own_properties_every_one_and_the_totals_across_them(conn, pmc, lease):
    second = conn.execute(
        "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, 'Property 0')"
        " RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    other_account = open_account(
        conn, pmc.pmc_id, pmc.operating_bank_id, "owner_property", pmc.owners[0].owner_id, second
    )
    paid(conn, pmc, charge(conn, pmc.pmc_id, lease, JAN_1, "1500.00"), "1500.00", at(1, 2))
    contribute(conn, pmc.pmc_id, other_account, "c1", "100.00", at(1, 3))
    bill(conn, pmc, "B-0", "60.00", at(1, 4), account=other_account)
    contribute(conn, pmc.pmc_id, pmc.owners[1].account, "c2", "100.00", at(1, 3))
    bill(conn, pmc, "B-2", "99.00", at(1, 4), account=pmc.owners[1].account)

    rows = statement(conn, pmc)

    assert [r[0] for r in rows] == list(range(1, len(rows) + 1))
    assert [r[1:3] for r in rows[:3]] == [
        (rows[0][1], "PMC"),
        ("Owner 1", "owner"),
        ("2026-01-01 to 2026-12-31 UTC, cash basis", "period"),
    ]
    assert [p for p in dict.fromkeys(r[1] for r in rows[3:])] == ["Property 0", "Property 1", None]
    total = {item: amount for _, p, item, _, amount in rows if p is None and item.startswith("all")}
    assert total["all properties: total income"] == Decimal("1500.00")
    assert total["all properties: expense: bills not in an expense account"] == Decimal("60.00")
    assert total["all properties: net income"] == Decimal("1440.00")
    assert all("99.00" not in str(r[4]) for r in rows)  # Owner 2's bill is not Owner 1's


@pytest.mark.parametrize(
    "days",
    [(date(2026, 2, 1), date(2026, 1, 31)), (None, date(2026, 1, 31)), (JAN_1, None)],
)
def test_the_days_run_from_one_through_one_no_earlier(conn, pmc, days):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="income statement runs"):
        statement(conn, pmc, days)


def test_only_an_owner_of_the_pmc(conn, pmc):
    stranger = make_pmc(conn).owners[0].owner_id
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no owner"):
        statement(conn, pmc, owner=stranger)


def test_the_ai_reads_it_and_writes_none_of_it(conn, ai_conn, pmc):
    repairs = expense_account(conn, pmc, "Repairs", 14)

    assert lines(ai_conn, pmc)["total income"] == Decimal("0.00")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ai_conn.execute(
            "INSERT INTO trust_expense_accounts (pmc_id, name, schedule_e_line)"
            " VALUES (%s, 'Mine', 14)",
            (pmc.pmc_id,),
        )
    entered = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, "B-1", JAN_1, JAN_1, "10.00"
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        put_in(ai_conn, pmc, entered, repairs)
