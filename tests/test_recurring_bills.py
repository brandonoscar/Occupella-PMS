"""Recurring bills and vendor payments by year (migration 20261010000027).

The golden case under tests/golden/cases/ pins the vendor payments report's layout; these check
the rules.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_recurring_bill,
    approve_bill,
    end_recurring_bill,
    enter_bill,
    enter_recurring_bills,
    in_background,
    language_sorted_database,
    make_pmc,
    pay_bill,
    set_aside_bill,
    transfer_batch,
    wait_until_blocked,
)

JAN_10, APR_5 = date(2026, 1, 10), date(2026, 4, 5)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def bills(conn, pmc):
    """(reference, bill date, due date, amount, memo) of the PMC's bills, in reference order."""
    return conn.execute(
        "SELECT reference, bill_date, due_on, amount, memo FROM trust_bills WHERE pmc_id = %s"
        ' ORDER BY reference COLLATE "C"',
        (pmc.pmc_id,),
    ).fetchall()


def lawn(conn, pmc, starts_on=JAN_10, day=5, amount="120.00"):
    return add_recurring_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, "LAWN", amount, day, starts_on
    )


# --- recurring bills ------------------------------------------------------------------------


def test_each_months_bill_is_entered_from_the_first_day_through_the_day_asked(conn, app_conn, pmc):
    lawn(app_conn, pmc)  # the 5th of each month, from January 10: February is the first

    assert enter_recurring_bills(app_conn, pmc.pmc_id, APR_5) == 3
    assert bills(conn, pmc) == [
        (f"LAWN-2026-0{m}", date(2026, m, 5), date(2026, m, 5), Decimal("120.00"), "Landscaping")
        for m in (2, 3, 4)
    ]


def test_a_recurring_bill_starting_on_its_day_enters_that_month(conn, app_conn, pmc):
    lawn(app_conn, pmc, starts_on=date(2026, 1, 5))  # the 5th of each month, from January 5

    assert enter_recurring_bills(app_conn, pmc.pmc_id, date(2026, 1, 5)) == 1
    assert [b[0] for b in bills(conn, pmc)] == ["LAWN-2026-01"]


def test_running_again_enters_only_the_months_not_yet_entered(conn, app_conn, pmc):
    lawn(app_conn, pmc)
    enter_recurring_bills(app_conn, pmc.pmc_id, date(2026, 3, 4))

    assert enter_recurring_bills(app_conn, pmc.pmc_id, date(2026, 3, 4)) == 0  # a retry
    assert enter_recurring_bills(app_conn, pmc.pmc_id, APR_5) == 2  # March and April
    assert len(bills(conn, pmc)) == 3


def test_an_end_stops_the_bills_from_its_day(conn, app_conn, pmc):
    recurring = lawn(app_conn, pmc)
    end_recurring_bill(app_conn, pmc.pmc_id, recurring, date(2026, 3, 5))

    enter_recurring_bills(app_conn, pmc.pmc_id, date(2026, 12, 31))

    assert [b[0] for b in bills(conn, pmc)] == ["LAWN-2026-02"]


def test_a_month_already_entered_by_hand_is_left_as_it_is(conn, app_conn, pmc):
    lawn(app_conn, pmc)
    enter_bill(
        app_conn,
        pmc.pmc_id,
        pmc.vendor_id,
        pmc.owners[0].account,
        "LAWN-2026-03",
        date(2026, 3, 7),
        date(2026, 3, 20),
        "95.00",
        "Spring cleanup",
    )

    assert enter_recurring_bills(app_conn, pmc.pmc_id, APR_5) == 2
    assert (
        "LAWN-2026-03",
        date(2026, 3, 7),
        date(2026, 3, 20),
        Decimal("95.00"),
        "Spring cleanup",
    ) in bills(conn, pmc)


def test_only_the_pmcs_own_recurring_bills_are_entered(conn, app_conn, pmc):
    other = make_pmc(conn)
    lawn(app_conn, other)

    assert enter_recurring_bills(app_conn, pmc.pmc_id, APR_5) == 0
    assert bills(conn, other) == []


@pytest.mark.parametrize(
    ("mistake", "change"),
    [
        ("day 0", {"day": 0}),
        ("day 29", {"day": 29}),
        ("no amount", {"amount": "0"}),
        ("a fraction of a cent", {"amount": "10.005"}),
    ],
)
def test_a_recurring_bill_is_a_whole_day_and_an_amount_in_cents(
    conn, app_conn, pmc, mistake, change
):
    with pytest.raises(psycopg.errors.CheckViolation):
        lawn(app_conn, pmc, **change)


def test_a_recurring_bill_is_paid_from_an_owners_property_account(conn, app_conn, pmc):
    with pytest.raises(psycopg.errors.CheckViolation, match="owner's property account"):
        add_recurring_bill(
            app_conn, pmc.pmc_id, pmc.vendor_id, pmc.prepaid_rent, "LAWN", "1.00", 5, JAN_10
        )


def test_a_vendor_uses_a_prefix_once(conn, app_conn, pmc):
    lawn(app_conn, pmc)

    with pytest.raises(psycopg.errors.UniqueViolation):
        lawn(app_conn, pmc, day=20)


def test_a_recurring_bill_names_a_vendor_of_its_pmc(conn, app_conn, pmc):
    other = make_pmc(conn)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        add_recurring_bill(
            app_conn, pmc.pmc_id, other.vendor_id, pmc.owners[0].account, "X", "1.00", 5, JAN_10
        )


def test_entering_needs_a_day(conn, app_conn, pmc):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="through a day"):
        enter_recurring_bills(app_conn, pmc.pmc_id, None)


def test_recurring_bills_and_their_ends_are_records(conn, app_conn, pmc):
    end_recurring_bill(app_conn, pmc.pmc_id, lawn(app_conn, pmc), APR_5)

    for table in ("trust_recurring_bills", "trust_recurring_bill_ends"):
        with pytest.raises(psycopg.errors.Error, match="append-only|history"):
            conn.execute(f"DELETE FROM {table}")


def test_an_entered_bill_over_the_owners_limit_still_needs_approval(conn, app_conn, pmc):
    lawn(app_conn, pmc, amount="900.00")  # no agreement: a limit of 0
    enter_recurring_bills(app_conn, pmc.pmc_id, date(2026, 2, 28))
    transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "1000.00")])
    bill = conn.execute("SELECT id FROM trust_bills WHERE pmc_id = %s", (pmc.pmc_id,)).fetchone()[0]

    with pytest.raises(psycopg.errors.CheckViolation, match="approval"):
        set_aside_bill(app_conn, pmc.pmc_id, bill, datetime(2026, 2, 6, tzinfo=UTC))


@pytest.mark.timing
def test_two_runs_at_once_enter_each_month_once(conn, database_url, pmc):
    lawn(conn, pmc)

    with psycopg.connect(database_url) as first:
        assert enter_recurring_bills(first, pmc.pmc_id, APR_5) == 3  # not committed yet
        thread, pid, outcome = in_background(
            database_url, lambda worker: enter_recurring_bills(worker, pmc.pmc_id, APR_5)
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert outcome == {"result": 0}
    assert len(bills(conn, pmc)) == 3


# --- vendor payments by year ----------------------------------------------------------------


def report(conn, pmc, year=2026):
    return conn.execute(
        "SELECT line, item, vendor, payments, paid FROM trust_report_vendor_payments(%s, %s)",
        (pmc.pmc_id, year),
    ).fetchall()


def paid_bill(conn, pmc, vendor, reference, amount, paid_at):
    """A bill entered, set aside and paid at `paid_at`."""
    transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, amount)])
    bill = enter_bill(
        conn,
        pmc.pmc_id,
        vendor,
        pmc.owners[0].account,
        reference,
        date(2025, 12, 1),
        date(2025, 12, 1),
        amount,
    )
    approve_bill(conn, pmc.pmc_id, bill, date(2025, 12, 1), "Owner 1, by email")
    set_aside_bill(conn, pmc.pmc_id, bill, datetime(2025, 12, 1, tzinfo=UTC))
    return pay_bill(conn, pmc.pmc_id, bill, paid_at)


@pytest.mark.parametrize("year", [None, 1899, 9999])
def test_the_report_is_for_a_calendar_year(conn, pmc, year):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="calendar year"):
        report(conn, pmc, year)


@pytest.mark.parametrize("year", [1900, 9998])
def test_the_report_reads_every_year_from_1900_to_9998(conn, pmc, year):
    rows = report(conn, pmc, year)

    assert rows[1][1:3] == ("year", f"{year}, paid January 1 to December 31 UTC")
    assert rows[2:] == [(3, "total", None, 0, Decimal("0.00"))]


def test_the_report_refuses_an_unknown_pmc(conn):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no PMC"):
        conn.execute("SELECT * FROM trust_report_vendor_payments(%s, 2026)", (uuid.uuid4(),))


def test_each_vendor_shows_what_it_was_paid_in_the_year(conn, app_conn, pmc):
    second = conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor 2') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    conn.execute(
        "SELECT trust_open_vendor_account(%s, %s, %s)", (pmc.pmc_id, pmc.operating_bank_id, second)
    )
    paid_bill(conn, pmc, pmc.vendor_id, "A-1", "100.00", datetime(2026, 1, 1, tzinfo=UTC))
    paid_bill(conn, pmc, pmc.vendor_id, "A-2", "250.50", datetime(2026, 12, 31, 23, 59, tzinfo=UTC))
    paid_bill(conn, pmc, pmc.vendor_id, "A-3", "999.00", datetime(2025, 12, 31, 23, 59, tzinfo=UTC))
    paid_bill(conn, pmc, pmc.vendor_id, "A-4", "777.00", datetime(2027, 1, 1, tzinfo=UTC))
    paid_bill(conn, pmc, second, "B-1", "40.00", datetime(2026, 6, 1, tzinfo=UTC))

    rows = report(app_conn, pmc)

    assert [row[0] for row in rows] == list(range(1, len(rows) + 1))
    assert rows[1][1:3] == ("year", "2026, paid January 1 to December 31 UTC")
    assert rows[2:] == [
        (3, "vendor", "Vendor 1", 2, Decimal("350.50")),
        (4, "vendor", "Vendor 2", 1, Decimal("40.00")),
        (5, "total", None, 3, Decimal("390.50")),
    ]


def test_money_set_aside_but_not_paid_is_not_a_payment(conn, pmc):
    transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "100.00")])
    bill = enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        pmc.owners[0].account,
        "A-1",
        date(2026, 1, 1),
        date(2026, 1, 1),
        "100.00",
    )
    approve_bill(conn, pmc.pmc_id, bill, date(2026, 1, 1), "Owner 1, by email")
    set_aside_bill(conn, pmc.pmc_id, bill, datetime(2026, 1, 2, tzinfo=UTC))

    assert report(conn, pmc)[2:] == [(3, "total", None, 0, Decimal("0.00"))]


def test_the_report_reads_the_same_in_any_time_zone_and_for_every_role(
    conn, app_conn, ai_conn, database_url, pmc
):
    paid_bill(conn, pmc, pmc.vendor_id, "A-1", "100.00", datetime(2026, 12, 31, 23, tzinfo=UTC))

    readings = [report(app_conn, pmc), report(ai_conn, pmc)]
    for zone in ("UTC", "America/Los_Angeles", "Pacific/Auckland"):
        with psycopg.connect(database_url, autocommit=True) as session:
            session.execute(f"SET TIME ZONE '{zone}'")
            readings.append(report(session, pmc))

    assert all(reading == readings[0] for reading in readings)
    assert readings[0][-1][3:] == (1, Decimal("100.00"))


def test_vendors_list_in_the_same_order_on_any_server(database_url):
    with language_sorted_database(database_url) as conn:
        pmc = make_pmc(conn)
        conn.execute(
            "UPDATE trust_vendors SET display_name = 'vendor a' WHERE id = %s", (pmc.vendor_id,)
        )
        other = conn.execute(
            "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor b') RETURNING id",
            (pmc.pmc_id,),
        ).fetchone()[0]
        conn.execute(
            "SELECT trust_open_vendor_account(%s, %s, %s)",
            (pmc.pmc_id, pmc.operating_bank_id, other),
        )
        for vendor, reference in [(pmc.vendor_id, "A-1"), (other, "B-1")]:
            paid_bill(conn, pmc, vendor, reference, "10.00", datetime(2026, 3, 1, tzinfo=UTC))
        rows = report(conn, pmc)

    assert [r[2] for r in rows if r[1] == "vendor"] == ["Vendor b", "vendor a"]
