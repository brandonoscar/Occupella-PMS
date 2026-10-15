"""Migration 20261015000054's reports at their edges: a balance sheet of the trust, cash flow,
vacancy, work orders and the tenant, owner and vendor directories. Their golden cases (one
scenario, tests/golden/cases/trust_report_<name>/march_2026.sql) pin the whole layout; these
pin where a day starts and ends, what an earlier or later record does, the refusals, and that
each runs for the app and the AI on its own PMC only."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_document,
    add_unit,
    add_work_order,
    approve_bill,
    enter_bill,
    make_pmc,
    open_lease,
    open_with_balances,
    pay_bill,
    set_aside_bill,
    step_work_order,
    transfer_batch,
)

DAY = date(2026, 3, 31)
ONE = timedelta(days=1)
REPORTS = {
    "trust_report_balance_sheet": (DAY,),
    "trust_report_cash_flow": (date(2026, 3, 1), DAY),
    "trust_report_vacancy": (DAY,),
    "trust_report_work_orders": (DAY,),
    "trust_report_tenant_directory": (DAY,),
    "trust_report_owner_directory": (DAY,),
    "trust_report_vendor_directory": (DAY,),
}


def at(day, hour=12, minute=0, second=0):
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=UTC)


def report(conn, name, pmc_id, *days):
    marks = ", ".join(["%s"] * (len(days) + 1))
    return conn.execute(f"SELECT * FROM {name}({marks})", (pmc_id, *days)).fetchall()


def rows_where(conn, name, pmc_id, days, column, value):
    """The report's rows whose column (by position) is the value."""
    return [row for row in report(conn, name, pmc_id, *days) if row[column] == value]


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.mark.parametrize("name", REPORTS)
def test_each_report_needs_its_days_and_a_known_pmc(conn, pmc, name):
    days = REPORTS[name]
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no PMC"):
        report(conn, name, uuid.uuid4(), *days)
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="day"):
        report(conn, name, pmc.pmc_id, *(None for _ in days))


def test_cash_flow_runs_from_a_day_through_one_no_earlier(conn, pmc):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no earlier"):
        report(conn, "trust_report_cash_flow", pmc.pmc_id, DAY, DAY - ONE)
    # One day is a period of its own.
    assert report(conn, "trust_report_cash_flow", pmc.pmc_id, DAY, DAY)[1][3] == (
        "2026-03-31 to 2026-03-31 UTC"
    )


@pytest.mark.parametrize("name", REPORTS)
def test_each_report_runs_for_the_app_and_the_ai_on_its_own_pmc_only(conn, app_conn, ai_conn, name):
    ours, theirs = make_pmc(conn), make_pmc(conn)
    for pmc, tag in ((ours, "Ours"), (theirs, "Theirs")):
        prop = pmc.owners[0].property_id
        unit = add_unit(conn, pmc.pmc_id, prop, f"{tag} unit")
        add_work_order(conn, pmc.pmc_id, prop, unit, f"{tag} repair", at(DAY))
        transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "10.00")], at(DAY))
    seen = {
        role: report(c, name, ours.pmc_id, *REPORTS[name])
        for role, c in (("app", app_conn), ("ai", ai_conn))
    }

    names = {
        tag: conn.execute(
            "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone()[0]
        for tag, pmc in (("ours", ours), ("theirs", theirs))
    }
    assert seen["app"] == seen["ai"]
    assert names["ours"] in seen["app"][0]  # the first line names the company
    text = repr(seen["app"])
    assert "Theirs" not in text and names["theirs"] not in text


def test_the_balance_sheet_counts_through_the_end_of_its_day(conn, pmc):
    owner = pmc.owners[0].account
    transfer_batch(conn, [(pmc.operating_cash, owner, "10.00")], at(DAY, 23, 59, 59))
    transfer_batch(conn, [(pmc.operating_cash, owner, "5.00")], at(DAY + ONE, 0))

    def cash(day):
        (row,) = [
            r
            for r in report(conn, "trust_report_balance_sheet", pmc.pmc_id, day)
            if r[1] == "assets" and r[2].startswith("Synthetic operating")
        ]
        return row[4]

    assert (cash(DAY - ONE), cash(DAY), cash(DAY + ONE)) == (
        Decimal("0"),
        Decimal("10.00"),
        Decimal("15.00"),
    )


def test_cash_flow_counts_its_first_day_and_not_the_day_after(conn, pmc):
    owner = pmc.owners[0].account
    first, last = date(2026, 3, 1), DAY
    transfer_batch(conn, [(pmc.operating_cash, owner, "1.00")], at(first - ONE, 23, 59, 59))
    transfer_batch(conn, [(pmc.operating_cash, owner, "20.00")], at(first, 0))
    transfer_batch(conn, [(owner, pmc.operating_cash, "3.00")], at(last, 23, 59, 59))
    transfer_batch(conn, [(pmc.operating_cash, owner, "400.00")], at(last + ONE, 0))

    lines = {
        row[3]: row[4]
        for row in report(conn, "trust_report_cash_flow", pmc.pmc_id, first, last)
        if row[2] and row[2].startswith("Synthetic operating")
    }

    assert lines == {
        "opening cash, by the books": Decimal("1.00"),
        "in: received for owners": Decimal("20.00"),
        "out: paid back from owners' accounts (returned payments, other)": Decimal("3.00"),
        "total in": Decimal("20.00"),
        "total out": Decimal("3.00"),
        "closing cash, by the books": Decimal("18.00"),
    }


def test_cash_flow_names_opening_balances_draws_bills_and_fees(conn, pmc):
    cutover = date(2026, 3, 1)
    owner = pmc.owners[0].account
    open_with_balances(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        cutover,
        {owner: "1000.00", pmc.pmc_income: "40.00"},
        "1040.00",
    )
    conn.execute(
        "INSERT INTO trust_management_agreements (pmc_id, ledger_account_id, starts_on,"
        " approval_limit) VALUES (%s, %s, %s, 1000)",
        (pmc.pmc_id, owner, cutover),
    )
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, owner, "B-1", date(2026, 3, 2), date(2026, 3, 9), "60.00"
    )
    set_aside_bill(conn, pmc.pmc_id, bill, at(date(2026, 3, 3)))
    pay_bill(conn, pmc.pmc_id, bill, at(date(2026, 3, 4)))
    conn.execute(
        "SELECT trust_draw_owner(%s, %s, 'draw-1', 100.00, %s)",
        (pmc.pmc_id, owner, at(date(2026, 3, 5))),
    )
    transfer_batch(conn, [(pmc.pmc_income, pmc.operating_cash, "40.00")], at(date(2026, 3, 6)))

    lines = {
        row[3]: row[4]
        for row in report(conn, "trust_report_cash_flow", pmc.pmc_id, cutover, DAY)
        if row[2] and row[2].startswith("Synthetic operating")
    }

    assert lines == {
        "opening cash, by the books": Decimal("0"),
        "in: opening balances carried over": Decimal("1040.00"),
        "out: bills paid": Decimal("60.00"),
        "out: owner draws": Decimal("100.00"),
        "out: fees paid to the company": Decimal("40.00"),
        "total in": Decimal("1040.00"),
        "total out": Decimal("200.00"),
        "closing cash, by the books": Decimal("840.00"),
    }


def test_a_lease_ending_on_the_day_keeps_its_unit_occupied_that_day(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    open_lease(conn, pmc.pmc_id, unit, date(2025, 4, 1), DAY, "900.00", [tenant])

    assert rows_where(conn, "trust_report_vacancy", pmc.pmc_id, (DAY,), 1, "vacant") == []
    (vacant,) = rows_where(conn, "trust_report_vacancy", pmc.pmc_id, (DAY + ONE,), 1, "vacant")
    assert vacant[3:] == ("Unit 1", DAY + ONE, 1, Decimal("900.00"), None)
    assert report(conn, "trust_report_vacancy", pmc.pmc_id, DAY + ONE)[-1][2] == (
        "1 units: 0 occupied, 1 vacant (0.0% occupied)"
    )


def test_a_company_with_no_units_says_so(conn, pmc):
    assert report(conn, "trust_report_vacancy", pmc.pmc_id, DAY)[-1][1:3] == ("total", "no units")


def test_a_work_orders_status_and_vendor_are_as_of_the_day(conn, pmc):
    prop = pmc.owners[0].property_id
    order = add_work_order(conn, pmc.pmc_id, prop, None, "Fence", at(DAY - 3 * ONE))
    step_work_order(conn, pmc.pmc_id, order, "assigned", at(DAY - ONE), pmc.vendor_id)
    step_work_order(conn, pmc.pmc_id, order, "completed", at(DAY + ONE))

    def orders(day):
        return [
            r
            for r in report(conn, "trust_report_work_orders", pmc.pmc_id, day)
            if r[1] in ("open", "closed")
        ]

    def row(day):
        (found,) = orders(day)
        return found[1], found[6], found[7], found[8]

    assert row(DAY - 2 * ONE) == ("open", "open", None, 1)
    assert row(DAY) == ("open", "assigned", "Vendor 1", 3)
    assert row(DAY + ONE) == ("closed", "completed", "Vendor 1", 4)
    # Not yet opened: not on the report.
    assert orders(DAY - 4 * ONE) == []


def test_a_tenants_last_lease_is_their_latest_and_their_next_is_the_soonest(conn, pmc):
    prop = pmc.owners[0].property_id
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    first, second = (
        add_unit(conn, pmc.pmc_id, prop, "Unit 1"),
        add_unit(conn, pmc.pmc_id, prop, "Unit 2"),
    )
    open_lease(conn, pmc.pmc_id, first, date(2024, 1, 1), date(2024, 12, 31), "800.00", [tenant])
    open_lease(conn, pmc.pmc_id, second, date(2025, 1, 1), date(2025, 12, 31), "850.00", [tenant])
    open_lease(conn, pmc.pmc_id, first, date(2026, 6, 1), None, "900.00", [tenant])
    open_lease(conn, pmc.pmc_id, second, date(2026, 9, 1), None, "950.00", [tenant])

    def lease(day):
        (row,) = rows_where(
            conn, "trust_report_tenant_directory", pmc.pmc_id, (day,), 2, "Tenant 1"
        )
        return row[1], row[4], row[7]

    assert lease(date(2026, 3, 31)) == ("next lease", "Unit 1", Decimal("900.00"))
    assert lease(date(2025, 6, 1)) == ("current lease", "Unit 2", Decimal("850.00"))
    assert lease(date(2026, 1, 15)) == ("next lease", "Unit 1", Decimal("900.00"))
    assert lease(date(2023, 12, 31)) == ("next lease", "Unit 1", Decimal("800.00"))


def test_a_vendor_paid_last_year_shows_when_but_not_in_this_years_total(conn, pmc):
    owner = pmc.owners[0].account
    transfer_batch(conn, [(pmc.operating_cash, owner, "500.00")], at(date(2025, 12, 1)))
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, owner, "B-1", date(2025, 12, 1), date(2025, 12, 9), "75.00"
    )
    approve_bill(conn, pmc.pmc_id, bill, date(2025, 12, 1), "Owner 1")
    set_aside_bill(conn, pmc.pmc_id, bill, at(date(2025, 12, 2)))
    pay_bill(conn, pmc.pmc_id, bill, at(date(2025, 12, 31), 23, 59, 59))

    def vendor(day):
        (row,) = rows_where(conn, "trust_report_vendor_directory", pmc.pmc_id, (day,), 1, "vendor")
        return row[3:]

    assert vendor(date(2025, 12, 31)) == (
        "no",
        0,
        0,
        Decimal("0.00"),
        Decimal("75.00"),
        date(2025, 12, 31),
    )
    assert vendor(date(2026, 1, 1)) == (
        "no",
        0,
        0,
        Decimal("0.00"),
        Decimal("0.00"),
        date(2025, 12, 31),
    )
    # Before it was paid, it was unpaid.
    assert vendor(date(2025, 12, 30)) == ("no", 0, 1, Decimal("75.00"), Decimal("0.00"), None)


def test_a_w9_counts_from_the_day_it_was_kept_until_the_day_it_was_removed(conn, pmc):
    document = add_document(conn, pmc.pmc_id, "w9", pmc.vendor_id)
    kept = conn.execute(
        "SELECT created_at FROM trust_documents WHERE id = %s", (document,)
    ).fetchone()[0]
    today = kept.astimezone(UTC).date()

    def on_file(day):
        (row,) = rows_where(conn, "trust_report_vendor_directory", pmc.pmc_id, (day,), 1, "vendor")
        return row[3]

    assert (on_file(today - ONE), on_file(today)) == ("no", "yes")
    # Removed the next day: still on file through the end of this one.
    conn.execute(
        "INSERT INTO trust_document_removals (document_id, pmc_id, created_at) VALUES (%s, %s, %s)",
        (document, pmc.pmc_id, at(today + ONE, 0)),
    )
    assert (on_file(today), on_file(today + ONE)) == ("yes", "no")
