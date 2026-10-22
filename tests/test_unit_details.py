"""A unit's details (migration 20261022000124): its bedrooms, bathrooms (in halves), square feet and
market rent, row after row naming who recorded it; the vacancy report has each vacant unit's
latest as recorded by the end of its day (UTC). Every name is synthetic."""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import acting_as, add_staff, add_unit, make_pmc


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def unit(conn, pmc):
    return add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "1A")


def add_details(conn, pmc_id, unit, beds=2, baths=1, size=850, rent="1200.00", at=None):
    conn.execute(
        "INSERT INTO trust_unit_details"
        " (pmc_id, unit_id, bedrooms, bathrooms, square_feet, market_rent, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, coalesce(%s, clock_timestamp()))",
        (pmc_id, unit, beds, baths, size, rent, at),
    )


def test_details_are_kept_row_after_row_naming_who_recorded_them(conn, app_conn, pmc, unit):
    staff = add_staff(conn, pmc.pmc_id)
    with acting_as(app_conn, staff):
        add_details(app_conn, pmc.pmc_id, unit)
        add_details(app_conn, pmc.pmc_id, unit, baths=Decimal("1.5"), rent="1250.00")
        add_details(app_conn, pmc.pmc_id, unit, None, None, None, None)

    assert conn.execute(
        "SELECT bedrooms, bathrooms, square_feet, market_rent, recorded_by"
        " FROM trust_unit_details WHERE unit_id = %s ORDER BY created_at",
        (unit,),
    ).fetchall() == [
        (2, Decimal(1), 850, Decimal("1200.00"), staff),
        (2, Decimal("1.5"), 850, Decimal("1250.00"), staff),
        (None, None, None, None, staff),
    ]


@pytest.mark.parametrize(
    ("beds", "baths", "size", "rent"),
    [
        (-1, 1, 850, "1200.00"),
        (21, 1, 850, "1200.00"),
        (2, Decimal("1.25"), 850, "1200.00"),
        (2, -1, 850, "1200.00"),
        (2, Decimal("20.5"), 850, "1200.00"),
        (2, 1, 0, "1200.00"),
        (2, 1, 100_001, "1200.00"),
        (2, 1, 850, "-1.00"),
        (2, 1, 850, "1200.001"),
        (2, 1, 850, "10000000.01"),
    ],
)
def test_details_are_in_range(conn, pmc, unit, beds, baths, size, rent):
    with pytest.raises(psycopg.errors.CheckViolation):
        add_details(conn, pmc.pmc_id, unit, beds, baths, size, rent)


def test_details_are_of_a_unit_of_their_pmc(conn, pmc, unit):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        add_details(conn, make_pmc(conn).pmc_id, unit)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE trust_unit_details SET bedrooms = 3",
        "DELETE FROM trust_unit_details",
        "TRUNCATE trust_unit_details",
    ],
)
def test_details_are_kept_for_good(conn, pmc, unit, statement):
    add_details(conn, pmc.pmc_id, unit)
    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)


def test_the_ai_reads_details_and_writes_none(conn, ai_conn, pmc, unit):
    add_details(conn, pmc.pmc_id, unit)
    assert ai_conn.execute(
        "SELECT bedrooms, market_rent FROM trust_unit_details WHERE unit_id = %s", (unit,)
    ).fetchall() == [(2, Decimal("1200.00"))]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        add_details(ai_conn, pmc.pmc_id, unit)


def test_the_vacancy_report_has_each_vacant_units_details_by_the_end_of_its_day(
    conn, app_conn, ai_conn, pmc, unit
):
    add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "1B")
    add_details(conn, pmc.pmc_id, unit, rent="1200.00", at=datetime(2026, 1, 1, tzinfo=UTC))
    add_details(
        conn, pmc.pmc_id, unit, rent="1300.00", at=datetime(2026, 1, 31, 23, 59, 59, tzinfo=UTC)
    )
    add_details(conn, pmc.pmc_id, unit, rent="1400.00", at=datetime(2026, 2, 1, tzinfo=UTC))

    def vacant(connection, day):
        return connection.execute(
            "SELECT unit, bedrooms, bathrooms, square_feet, market_rent"
            " FROM trust_report_vacancy(%s, %s) WHERE item = 'vacant' ORDER BY line",
            (pmc.pmc_id, day),
        ).fetchall()

    assert vacant(app_conn, date(2025, 12, 31)) == [
        ("1A", None, None, None, None),
        ("1B", None, None, None, None),
    ]
    assert vacant(app_conn, date(2026, 1, 30))[0] == ("1A", 2, Decimal(1), 850, Decimal("1200.00"))
    january = date(2026, 1, 31)
    assert vacant(app_conn, january) == [
        ("1A", 2, Decimal(1), 850, Decimal("1300.00")),
        ("1B", None, None, None, None),
    ]
    assert vacant(ai_conn, date(2026, 2, 1))[0] == ("1A", 2, Decimal(1), 850, Decimal("1400.00"))
