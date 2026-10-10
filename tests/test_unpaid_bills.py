"""trust_report_unpaid_bills: as of the end of a day (UTC), every bill dated by then and not paid
by then, by vendor, with what was set aside for it and how far past due it is (migration
20261010000018).

The golden case under tests/golden/cases/ pins the full layout; these check the rules behind it.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_agreement,
    enter_bill,
    language_sorted_database,
    make_pmc,
    pay_bill,
    set_aside_bill,
    transfer_batch,
)

APR_30 = date(2026, 4, 30)


def report(conn, pmc, as_of):
    return conn.execute(
        "SELECT line, item, vendor, reference, property, bill_date, due_on, days_past_due,"
        " bucket, amount, set_aside FROM trust_report_unpaid_bills(%s, %s)",
        (pmc.pmc_id, as_of),
    ).fetchall()


def bills(rows):
    """(vendor, reference, days past due, bucket, amount, set aside) per bill line."""
    return [(r[2], r[3], r[7], r[8], r[9], r[10]) for r in rows if r[1] == "bill"]


@pytest.fixture
def pmc(conn):
    pmc = make_pmc(conn)
    owner = pmc.owners[0].account
    add_agreement(conn, pmc.pmc_id, owner, date(2025, 1, 1), approval_limit="10000")
    transfer_batch(conn, [(pmc.operating_cash, owner, "5000.00")], datetime(2025, 1, 2, tzinfo=UTC))
    return pmc


def bill(conn, pmc, reference, due_on, amount="100.00", bill_date=None, vendor=None):
    return enter_bill(
        conn,
        pmc.pmc_id,
        vendor or pmc.vendor_id,
        pmc.owners[0].account,
        reference,
        bill_date or min(due_on, APR_30),
        due_on,
        amount,
    )


@pytest.mark.parametrize("mistake", ["no date", "unknown PMC"])
def test_a_missing_date_or_an_unknown_pmc_is_refused(conn, pmc, mistake):
    pmc_id, as_of = (pmc.pmc_id, None) if mistake == "no date" else (uuid.uuid4(), APR_30)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="as of a day|no PMC"):
        conn.execute("SELECT * FROM trust_report_unpaid_bills(%s, %s)", (pmc_id, as_of))


@pytest.mark.parametrize(
    ("late", "bucket"),
    [
        (-1, "current"),
        (0, "current"),
        (1, "1-30"),
        (30, "1-30"),
        (31, "31-60"),
        (60, "31-60"),
        (61, "61-90"),
        (90, "61-90"),
        (91, "over 90"),
    ],
)
def test_each_bill_falls_in_the_bucket_of_its_days_past_due(conn, pmc, late, bucket):
    bill(conn, pmc, "INV-1", APR_30 - timedelta(days=late))

    assert bills(report(conn, pmc, APR_30)) == [
        ("Vendor 1", "INV-1", max(late, 0), bucket, Decimal("100.00"), Decimal("0.00"))
    ]


def test_a_bill_shows_from_its_date_until_it_is_paid(conn, pmc):
    first = bill(conn, pmc, "INV-1", APR_30, bill_date=APR_30)
    bill(conn, pmc, "INV-2", date(2026, 5, 31), bill_date=date(2026, 5, 1))  # not dated yet
    set_aside_bill(conn, pmc.pmc_id, first, datetime(2026, 4, 30, 23, 59, 59, tzinfo=UTC))
    pay_bill(conn, pmc.pmc_id, first, datetime(2026, 5, 1, 0, 0, tzinfo=UTC))

    # April 30: set aside at its last second, paid at May's first moment.
    assert bills(report(conn, pmc, APR_30)) == [
        ("Vendor 1", "INV-1", 0, "current", Decimal("100.00"), Decimal("100.00"))
    ]
    assert bills(report(conn, pmc, date(2026, 4, 29))) == []  # not dated yet either
    assert [b[1] for b in bills(report(conn, pmc, date(2026, 5, 1)))] == ["INV-2"]


def test_what_was_set_aside_after_the_day_is_not_shown_set_aside(conn, pmc):
    later = bill(conn, pmc, "INV-1", APR_30)
    set_aside_bill(conn, pmc.pmc_id, later, datetime(2026, 5, 1, 0, 0, tzinfo=UTC))

    assert bills(report(conn, pmc, APR_30))[0][5] == Decimal("0.00")


def test_each_vendor_has_a_total_and_the_report_a_grand_total(conn, pmc):
    vendor_2 = conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor 2') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    conn.execute(
        "SELECT trust_open_vendor_account(%s, %s, %s)",
        (pmc.pmc_id, pmc.operating_bank_id, vendor_2),
    )
    held = bill(conn, pmc, "INV-1", APR_30, "40.00")
    bill(conn, pmc, "INV-2", APR_30, "60.00")
    bill(conn, pmc, "B-1", APR_30, "25.00", vendor=vendor_2)
    set_aside_bill(conn, pmc.pmc_id, held, datetime(2026, 4, 1, tzinfo=UTC))

    rows = report(conn, pmc, APR_30)

    assert [row[0] for row in rows] == list(range(1, len(rows) + 1))
    assert [(r[1], r[2], r[9], r[10]) for r in rows if r[1] != "bill"][2:] == [
        ("vendor total", "Vendor 1", Decimal("100.00"), Decimal("40.00")),
        ("vendor total", "Vendor 2", Decimal("25.00"), Decimal("0.00")),
        ("total", None, Decimal("125.00"), Decimal("40.00")),
    ]


def test_a_pmc_with_no_unpaid_bills_shows_a_zero_total(conn, pmc):
    rows = report(conn, pmc, APR_30)

    assert [r[1] for r in rows] == ["PMC", "as of", "total"]
    assert rows[2][9:] == (Decimal("0.00"), Decimal("0.00"))


def test_the_report_reads_the_same_in_any_time_zone_and_for_every_role(
    conn, app_conn, ai_conn, database_url, pmc
):
    paid = bill(conn, pmc, "INV-1", APR_30)
    set_aside_bill(conn, pmc.pmc_id, paid, datetime(2026, 4, 30, 23, 0, tzinfo=UTC))
    pay_bill(conn, pmc.pmc_id, paid, datetime(2026, 4, 30, 23, 30, tzinfo=UTC))  # May in Auckland

    readings = [report(app_conn, pmc, APR_30), report(ai_conn, pmc, APR_30)]
    for zone in ("UTC", "America/Los_Angeles", "Pacific/Auckland"):
        with psycopg.connect(database_url, autocommit=True) as session:
            session.execute(f"SET TIME ZONE '{zone}'")
            readings.append(report(session, pmc, APR_30))

    assert all(reading == readings[0] for reading in readings)
    assert bills(readings[0]) == []


def test_vendors_and_references_list_in_the_same_order_on_any_server(database_url):
    with language_sorted_database(database_url) as conn:
        pmc = make_pmc(conn)
        vendor_b = conn.execute(
            "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor b') RETURNING id",
            (pmc.pmc_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE trust_vendors SET display_name = 'vendor a' WHERE id = %s", (pmc.vendor_id,)
        )
        for reference, vendor in [
            ("inv-1", pmc.vendor_id),
            ("INV-2", pmc.vendor_id),
            ("X", vendor_b),
        ]:
            bill(conn, pmc, reference, APR_30, vendor=vendor)
        rows = report(conn, pmc, APR_30)

    assert [(b[0], b[1]) for b in bills(rows)] == [
        ("Vendor b", "X"),
        ("vendor a", "INV-2"),
        ("vendor a", "inv-1"),
    ]
