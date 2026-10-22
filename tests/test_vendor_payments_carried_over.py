"""Vendor payments carried over at cutover (migration 20261013000038).

What the old system paid a vendor in a year before the cutover is kept once per vendor and year,
and the vendor payments report adds it to what the PMS paid the vendor that year, on a line of
its own under the vendor's, and counts it toward the IRS threshold (migration 0113). The golden
case under tests/golden/cases/ pins the layout.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import carry_over_vendor_payments, make_pmc
from test_recurring_bills import mark, nothing_paid, paid_bill, report

MAR_1 = date(2026, 3, 1)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


THRESHOLD_2026 = (
    3,
    "threshold",
    "a 1099 for a vendor marked for one and paid this much or more (IRS, payments from 2026)",
    None,
    Decimal("2000.00"),
)
NONE_UNMARKED = "not marked for a 1099", "0 vendors", 0, Decimal("0.00")


def second_vendor(conn, pmc, name="Vendor 2"):
    return conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
        (pmc.pmc_id, name),
    ).fetchone()[0]


def test_the_year_adds_what_the_old_system_paid_before_the_cutover(conn, app_conn, pmc):
    """Carried over counts toward the threshold: 1000.00 paid here and 1250.00 before is a
    1099 vendor's 2250.00, over 2026's 2000.00."""
    mark(conn, pmc, pmc.vendor_id, True)
    paid_bill(conn, pmc, pmc.vendor_id, "A-1", "1000.00", datetime(2026, 3, 5, tzinfo=UTC))
    carry_over_vendor_payments(app_conn, pmc.pmc_id, pmc.vendor_id, 2026, MAR_1, "1250.00")

    assert report(app_conn, pmc)[2:] == [
        THRESHOLD_2026,
        (4, "vendor", "Vendor 1", 1, Decimal("2250.00")),
        (5, "carried over", "paid in the old system before 2026-03-01", None, Decimal("1250.00")),
        (6, "total", None, 1, Decimal("2250.00")),
        (7, *NONE_UNMARKED),
    ]


def test_a_vendor_the_pms_has_not_paid_yet_is_listed(conn, pmc):
    second = second_vendor(conn, pmc)
    for vendor in (pmc.vendor_id, second):
        mark(conn, pmc, vendor, True)
    carry_over_vendor_payments(conn, pmc.pmc_id, second, 2026, MAR_1, "80.25")
    paid_bill(conn, pmc, pmc.vendor_id, "A-1", "100.00", datetime(2026, 3, 5, tzinfo=UTC))

    assert report(conn, pmc)[2:] == [
        THRESHOLD_2026,
        (4, "total", None, 0, Decimal("0.00")),
        (5, "under the threshold", "Vendor 1", 1, Decimal("100.00")),
        (6, "under the threshold", "Vendor 2", 0, Decimal("80.25")),
        (7, "carried over", "paid in the old system before 2026-03-01", None, Decimal("80.25")),
        (8, *NONE_UNMARKED),
    ]


def test_a_january_first_cutover_carries_over_the_whole_year_before(conn, pmc):
    mark(conn, pmc, pmc.vendor_id, True)
    carry_over_vendor_payments(conn, pmc.pmc_id, pmc.vendor_id, 2026, date(2027, 1, 1), "900.00")

    assert report(conn, pmc, 2026)[2:] == [
        THRESHOLD_2026,
        (4, "total", None, 0, Decimal("0.00")),
        (5, "under the threshold", "Vendor 1", 0, Decimal("900.00")),
        (6, "carried over", "paid in the old system before 2027-01-01", None, Decimal("900.00")),
        (7, *NONE_UNMARKED),
    ]
    assert report(conn, pmc, 2027)[3:] == nothing_paid(2027)[0]


def test_only_the_pmcs_own_are_counted(conn, pmc):
    other = make_pmc(conn)
    mark(conn, other, other.vendor_id, True)
    carry_over_vendor_payments(conn, other.pmc_id, other.vendor_id, 2026, MAR_1, "2500.00")

    assert report(conn, pmc)[2:] == [THRESHOLD_2026, *nothing_paid(2026)[0]]


def test_a_vendors_year_is_carried_over_once(conn, pmc):
    carry_over_vendor_payments(conn, pmc.pmc_id, pmc.vendor_id, 2026, MAR_1, "250.00")
    carry_over_vendor_payments(conn, pmc.pmc_id, pmc.vendor_id, 2025, date(2026, 1, 1), "10.00")

    with pytest.raises(psycopg.errors.UniqueViolation, match="carried_over_pkey"):
        carry_over_vendor_payments(conn, pmc.pmc_id, pmc.vendor_id, 2026, date(2026, 4, 1), "99.00")


@pytest.mark.parametrize(
    ("year", "paid_before", "amount", "why"),
    [
        (2026, MAR_1, "0", "amount_check"),
        (2026, MAR_1, "-1.00", "amount_check"),
        (2026, MAR_1, "1.005", "amount_check"),
        (1899, date(1899, 3, 1), "1.00", "year_check"),
        (9999, date(9999, 3, 1), "1.00", "year_check"),
        (2026, date(2026, 1, 1), "1.00", "carried_over_check"),  # nothing before it that year
        (2026, date(2027, 1, 2), "1.00", "carried_over_check"),  # past the year
        (2026, date(2025, 6, 1), "1.00", "carried_over_check"),
    ],
)
def test_a_carry_over_stays_in_range(conn, pmc, year, paid_before, amount, why):
    with pytest.raises(psycopg.errors.CheckViolation, match=why):
        carry_over_vendor_payments(conn, pmc.pmc_id, pmc.vendor_id, year, paid_before, amount)


def test_a_carry_over_is_for_a_vendor_of_its_pmc(conn, pmc):
    other = make_pmc(conn)

    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="trust_vendors"):
        carry_over_vendor_payments(conn, pmc.pmc_id, other.vendor_id, 2026, MAR_1, "1.00")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE trust_vendor_payments_carried_over SET amount = 1",
        "DELETE FROM trust_vendor_payments_carried_over",
    ],
)
def test_carried_over_payments_are_kept_for_good(conn, pmc, statement):
    carry_over_vendor_payments(conn, pmc.pmc_id, pmc.vendor_id, 2026, MAR_1, "250.00")

    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)


def test_the_ai_reads_them_in_the_report(conn, ai_conn, pmc):
    carry_over_vendor_payments(conn, pmc.pmc_id, pmc.vendor_id, 2026, MAR_1, "250.00")

    assert report(ai_conn, pmc) == report(conn, pmc)
