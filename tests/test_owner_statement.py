"""trust_report_owner_statement: per property an opening balance, each posting in date order with
the balance after it, and a closing balance; then the owner's totals (migration 20261009000010).

The golden case under tests/golden/cases/ pins the full layout; these check the rules behind it.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import make_pmc, transfer_batch

JAN = datetime(2026, 1, 1, tzinfo=UTC)
FEB = datetime(2026, 2, 1, tzinfo=UTC)
RENT = Decimal("1500.00")


def statement(conn, pmc, owner_id, start=JAN, end=FEB):
    return conn.execute(
        "SELECT line, property, posted_on, item, detail, amount, balance"
        " FROM trust_report_owner_statement(%s, %s, %s, %s)",
        (pmc.pmc_id, owner_id, start, end),
    ).fetchall()


def items(rows):
    return [(item, amount, balance) for _, _, _, item, _, amount, balance in rows]


def totals(rows):
    return {
        item: amount if balance is None else balance
        for _, _, _, item, _, amount, balance in rows
        if item.startswith("all properties")
    }


@pytest.mark.parametrize(("start", "end"), [(FEB, JAN), (JAN, JAN), (None, FEB), (JAN, None)])
def test_a_period_must_end_after_it_starts(conn, start, end):
    pmc = make_pmc(conn)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="must end after it starts"):
        statement(conn, pmc, pmc.owners[0].owner_id, start, end)


@pytest.mark.parametrize("owner", ["unknown", "another PMC's"])
def test_an_owner_outside_the_pmc_is_refused_not_shown_as_holding_nothing(conn, owner):
    pmc, other = make_pmc(conn), make_pmc(conn)
    owner_id = uuid.uuid4() if owner == "unknown" else other.owners[0].owner_id

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no owner"):
        statement(conn, pmc, owner_id)


def test_an_owner_with_no_properties_gets_a_statement_of_zeros(conn):
    pmc = make_pmc(conn)
    owner = conn.execute(
        "INSERT INTO trust_owners (pmc_id, display_name) VALUES (%s, 'Owner 9') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]

    rows = statement(conn, pmc, owner)

    assert [item for item, *_ in items(rows)][:3] == ["PMC", "owner", "period"]
    assert totals(rows) == dict.fromkeys(
        [
            "all properties: opening balance",
            "all properties: money in",
            "all properties: money out",
            "all properties: closing balance",
        ],
        Decimal("0.00"),
    )


def test_the_period_includes_its_start_and_not_its_end(conn):
    pmc = make_pmc(conn)
    owner = pmc.owners[0]
    transfer_batch(conn, [(pmc.operating_cash, owner.account, RENT)], event_at=JAN)
    transfer_batch(conn, [(pmc.operating_cash, owner.account, "10.00")], event_at=FEB)

    rows = statement(conn, pmc, owner.owner_id)

    assert items(rows)[3:6] == [
        ("opening balance", None, Decimal("0.00")),
        ("posting", RENT, RENT),
        ("closing balance", None, RENT),
    ]
    assert totals(rows)["all properties: money in"] == RENT


def test_a_quiet_period_carries_the_balance_through(conn):
    pmc = make_pmc(conn)
    owner = pmc.owners[0]
    transfer_batch(
        conn,
        [(pmc.operating_cash, owner.account, RENT)],
        event_at=datetime(2025, 12, 1, tzinfo=UTC),
    )

    rows = statement(conn, pmc, owner.owner_id)

    assert items(rows)[3:5] == [("opening balance", None, RENT), ("closing balance", None, RENT)]
    assert totals(rows)["all properties: money out"] == 0


def test_dates_read_the_same_in_any_time_zone(conn, database_url):
    pmc = make_pmc(conn)
    owner = pmc.owners[0]
    late_on_the_31st = datetime(2026, 1, 31, 23, 30, tzinfo=UTC)
    transfer_batch(conn, [(pmc.operating_cash, owner.account, RENT)], event_at=late_on_the_31st)

    readings = []
    for zone in ("UTC", "America/Los_Angeles", "Pacific/Auckland"):
        with psycopg.connect(database_url, autocommit=True) as session:
            session.execute(f"SET TIME ZONE '{zone}'")
            readings.append(statement(session, pmc, owner.owner_id))

    assert readings[0] == readings[1] == readings[2]
    assert readings[0][4][2] == "2026-01-31"  # in Auckland it is already Feb 1


def test_the_app_role_can_run_the_statement(conn, app_conn):
    pmc = make_pmc(conn)
    owner = pmc.owners[0]
    transfer_batch(conn, [(pmc.operating_cash, owner.account, RENT)], event_at=JAN)

    assert statement(app_conn, pmc, owner.owner_id) == statement(conn, pmc, owner.owner_id)
