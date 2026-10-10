"""trust_report_owner_balances: per owner's property, what it holds at the end of a day (UTC),
the reserve its agreement keeps back that day, and what is available to pay the owner
(migration 20261010000016).

The golden case under tests/golden/cases/ pins the full layout; these check the rules behind it.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import add_agreement, language_sorted_database, make_pmc, transfer_batch

JAN_1, JAN_31, FEB_1 = date(2026, 1, 1), date(2026, 1, 31), date(2026, 2, 1)


def report(conn, pmc, as_of):
    return conn.execute(
        "SELECT line, item, owner, property, detail, balance, reserve, available"
        " FROM trust_report_owner_balances(%s, %s)",
        (pmc.pmc_id, as_of),
    ).fetchall()


def lines(rows):
    """(owner, property, detail, balance, reserve, available) per owner's property."""
    return [tuple(row[2:]) for row in rows if row[1] == "owner property"]


def total(rows):
    (row,) = [row for row in rows if row[1] == "total"]
    return tuple(row[5:])


@pytest.mark.parametrize("mistake", ["no date", "unknown PMC"])
def test_a_missing_date_or_an_unknown_pmc_is_refused(conn, mistake):
    pmc = make_pmc(conn)
    pmc_id, as_of = (pmc.pmc_id, None) if mistake == "no date" else (uuid.uuid4(), JAN_31)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="as of a day|no PMC"):
        conn.execute("SELECT * FROM trust_report_owner_balances(%s, %s)", (pmc_id, as_of))


def test_each_property_shows_its_balance_reserve_and_what_is_available(conn):
    pmc = make_pmc(conn)
    first, second = pmc.owners
    add_agreement(conn, pmc.pmc_id, first.account, JAN_1, "8", "50", "10", reserve="200")
    add_agreement(conn, pmc.pmc_id, second.account, JAN_1, reserve="500")
    jan_3 = datetime(2026, 1, 3, tzinfo=UTC)
    transfer_batch(conn, [(pmc.operating_cash, first.account, "1500.00")], jan_3)
    transfer_batch(conn, [(pmc.operating_cash, second.account, "300.00")], jan_3)

    rows = report(conn, pmc, JAN_31)

    assert [row[0] for row in rows] == list(range(1, len(rows) + 1))
    assert lines(rows) == [
        (
            "Owner 1",
            "Property 1",
            "8% of collected rent, minimum 50.00, flat 10.00",
            Decimal("1500.00"),
            Decimal("200.00"),
            Decimal("1300.00"),
        ),
        # Less than the reserve: nothing available, not a negative amount.
        (
            "Owner 2",
            "Property 2",
            "0% of collected rent, minimum 0.00, flat 0.00",
            Decimal("300.00"),
            Decimal("500.00"),
            Decimal("0.00"),
        ),
    ]
    assert total(rows) == (Decimal("1800.00"), Decimal("700.00"), Decimal("1300.00"))


def test_balances_are_as_of_the_end_of_the_day_and_the_reserve_in_force_that_day(conn):
    pmc = make_pmc(conn)
    owner = pmc.owners[0].account
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, reserve="100")
    add_agreement(conn, pmc.pmc_id, owner, FEB_1, reserve="400")
    transfer_batch(
        conn, [(pmc.operating_cash, owner, "1000.00")], datetime(2026, 1, 31, 23, 59, tzinfo=UTC)
    )
    transfer_batch(
        conn, [(pmc.operating_cash, owner, "50.00")], datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
    )

    assert lines(report(conn, pmc, date(2025, 12, 31)))[0][3:] == (
        Decimal("0.00"),
        Decimal("0.00"),
        Decimal("0.00"),
    )
    assert lines(report(conn, pmc, JAN_31))[0][3:] == (
        Decimal("1000.00"),
        Decimal("100.00"),
        Decimal("900.00"),
    )
    assert lines(report(conn, pmc, FEB_1))[0][3:] == (
        Decimal("1050.00"),
        Decimal("400.00"),
        Decimal("650.00"),
    )


def test_a_property_without_an_agreement_keeps_no_reserve(conn):
    pmc = make_pmc(conn)
    transfer_batch(
        conn,
        [(pmc.operating_cash, pmc.owners[0].account, "75.00")],
        datetime(2026, 1, 3, tzinfo=UTC),
    )

    assert lines(report(conn, pmc, JAN_31))[0] == (
        "Owner 1",
        "Property 1",
        "no management agreement",
        Decimal("75.00"),
        Decimal("0.00"),
        Decimal("75.00"),
    )


def test_the_report_reads_the_same_in_any_time_zone_and_for_every_role(
    conn, app_conn, ai_conn, database_url
):
    pmc = make_pmc(conn)
    transfer_batch(
        conn,
        [(pmc.operating_cash, pmc.owners[0].account, "75.00")],
        datetime(2026, 1, 31, 23, 30, tzinfo=UTC),  # Feb 1 already in Auckland
    )

    readings = [report(app_conn, pmc, JAN_31), report(ai_conn, pmc, JAN_31)]
    for zone in ("UTC", "America/Los_Angeles", "Pacific/Auckland"):
        with psycopg.connect(database_url, autocommit=True) as session:
            session.execute(f"SET TIME ZONE '{zone}'")
            readings.append(report(session, pmc, JAN_31))

    assert all(reading == readings[0] for reading in readings)
    assert lines(readings[0])[0][3] == Decimal("75.00")


def test_owners_and_properties_list_in_the_same_order_on_any_server(database_url):
    with language_sorted_database(database_url) as conn:
        pmc = make_pmc(conn)
        for owner, name in zip(pmc.owners, ["owner a", "Owner b"], strict=True):
            conn.execute(
                "UPDATE trust_owners SET display_name = %s WHERE id = %s", (name, owner.owner_id)
            )
        rows = report(conn, pmc, JAN_31)

    assert [row[0] for row in lines(rows)] == ["Owner b", "owner a"]
