"""Vendor insurance and move-in/move-out inspections (migration 20261015000058), and their
reports at the edges their golden cases don't pin: which policy counts on a day, which
inspection of each kind counts, and the records' own rules."""

import uuid
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from helpers import acting_as, add_staff, add_unit, make_pmc, open_lease

DAY = date(2026, 6, 30)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def add_policy(conn, pmc_id, vendor, starts, expires, coverage="general liability", number="GL-1"):
    conn.execute(
        "INSERT INTO trust_vendor_insurance (pmc_id, vendor_id, coverage, carrier, policy_number,"
        " starts_on, expires_on) VALUES (%s, %s, %s, 'Carrier A', %s, %s, %s)",
        (pmc_id, vendor, coverage, number, starts, expires),
    )


def standing(conn, pmc, on):
    return [
        (r[0], r[1], r[2])
        for r in conn.execute(
            "SELECT item, policy_number, standing FROM trust_report_vendor_insurance(%s, %s)"
            " WHERE item NOT IN ('PMC', 'as of')",
            (pmc.pmc_id, on),
        ).fetchall()
    ]


def test_a_vendor_is_covered_by_the_policy_running_on_the_day(conn, pmc):
    add_policy(conn, pmc.pmc_id, pmc.vendor_id, date(2025, 7, 1), DAY, number="GL-1")
    add_policy(conn, pmc.pmc_id, pmc.vendor_id, date(2026, 7, 1), date(2027, 6, 30), number="GL-2")

    assert standing(conn, pmc, date(2025, 6, 30)) == [("no insurance on file", None, None)]
    assert standing(conn, pmc, DAY - date.resolution * 30) == [
        ("policy", "GL-1", "expires in 30 days")
    ]
    assert standing(conn, pmc, DAY - date.resolution * 31) == [("policy", "GL-1", "current")]
    assert standing(conn, pmc, DAY - date.resolution) == [("policy", "GL-1", "expires in 1 day")]
    assert standing(conn, pmc, DAY) == [("policy", "GL-1", "expires today")]
    # The renewal takes over the day it starts.
    assert standing(conn, pmc, date(2026, 7, 1)) == [("policy", "GL-2", "current")]
    assert standing(conn, pmc, date(2027, 7, 1)) == [("policy", "GL-2", "expired")]


def test_vendor_insurance_needs_a_day_and_a_known_pmc(conn, pmc):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="as of a day"):
        standing(conn, pmc, None)
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no PMC"):
        conn.execute("SELECT * FROM trust_report_vendor_insurance(%s, %s)", (uuid.uuid4(), DAY))


def test_a_policy_is_of_a_vendor_of_its_pmc_and_runs_forward(conn, pmc):
    other = make_pmc(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        add_policy(conn, pmc.pmc_id, other.vendor_id, date(2026, 1, 1), DAY)
    with pytest.raises(psycopg.errors.CheckViolation, match="trust_vendor_insurance_check"):
        add_policy(conn, pmc.pmc_id, pmc.vendor_id, DAY, date(2026, 1, 1))
    with pytest.raises(psycopg.errors.CheckViolation, match="coverage_check"):
        add_policy(conn, pmc.pmc_id, pmc.vendor_id, date(2026, 1, 1), DAY, coverage="flood")


@pytest.fixture
def lease(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    return open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), DAY, "1500.00", [tenant])


def inspect(conn, pmc_id, lease, kind, on, rooms):
    inspection = conn.execute(
        "INSERT INTO trust_inspections (pmc_id, lease_id, kind, inspected_on)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (pmc_id, lease, kind, on),
    ).fetchone()[0]
    for n, (room, condition) in enumerate(rooms, start=1):
        conn.execute(
            "INSERT INTO trust_inspection_items (inspection_id, pmc_id, line, room, condition)"
            " VALUES (%s, %s, %s, %s, %s)",
            (inspection, pmc_id, n, room, condition),
        )
    return inspection


def rooms(conn, pmc, lease):
    return [
        r[1:]
        for r in conn.execute(
            "SELECT item, room, move_in, move_out, changed FROM trust_report_inspection(%s, %s)",
            (pmc.pmc_id, lease),
        ).fetchall()
        if r[0] == "room"
    ]


def test_the_latest_inspection_of_each_kind_counts(conn, pmc, lease):
    inspect(conn, pmc.pmc_id, lease, "move-in", date(2026, 1, 2), [("Kitchen", "fair")])
    inspect(conn, pmc.pmc_id, lease, "move-in", date(2026, 1, 1), [("Kitchen", "poor")])
    assert rooms(conn, pmc, lease) == [("Kitchen", "fair", None, None)]
    inspect(conn, pmc.pmc_id, lease, "move-out", DAY, [("Kitchen", "fair")])
    assert rooms(conn, pmc, lease) == [("Kitchen", "fair", "fair", False)]
    # Recorded later for the same day: the later record counts.
    inspect(conn, pmc.pmc_id, lease, "move-out", DAY, [("Kitchen", "damaged")])
    assert rooms(conn, pmc, lease) == [("Kitchen", "fair", "damaged", True)]


def test_a_lease_with_no_inspection_says_so(conn, pmc, lease):
    rows = conn.execute(
        "SELECT item, move_in, move_out FROM trust_report_inspection(%s, %s)", (pmc.pmc_id, lease)
    ).fetchall()
    assert ("inspected", "no move-in inspection", "no move-out inspection") in rows
    assert rooms(conn, pmc, lease) == []


def test_an_inspection_is_of_a_lease_of_its_pmc_and_names_each_room_once(conn, pmc, lease):
    other = make_pmc(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        inspect(conn, other.pmc_id, lease, "move-in", DAY, [])
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no lease"):
        conn.execute("SELECT * FROM trust_report_inspection(%s, %s)", (other.pmc_id, lease))
    with pytest.raises(psycopg.errors.UniqueViolation, match="trust_inspection_items_room"):
        inspect(conn, pmc.pmc_id, lease, "move-in", DAY, [("Kitchen", "good"), ("KITCHEN", "fair")])


def test_the_records_are_append_only_and_name_who_recorded_them(conn, pmc, lease):
    staff = add_staff(conn, pmc.pmc_id, "Staff 1")
    with acting_as(conn, staff):
        inspect(conn, pmc.pmc_id, lease, "move-in", DAY, [("Kitchen", "good")])
        add_policy(conn, pmc.pmc_id, pmc.vendor_id, date(2026, 1, 1), DAY)
    assert conn.execute(
        "SELECT (SELECT recorded_by FROM trust_inspections WHERE lease_id = %s),"
        " (SELECT recorded_by FROM trust_vendor_insurance WHERE vendor_id = %s)",
        (lease, pmc.vendor_id),
    ).fetchone() == (staff, staff)
    for table in ("trust_vendor_insurance", "trust_inspections", "trust_inspection_items"):
        for query in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
                conn.execute(query)


def test_the_ai_reads_them_and_runs_their_reports(ai_conn, conn, pmc, lease):
    add_policy(conn, pmc.pmc_id, pmc.vendor_id, date(2026, 1, 1), DAY)
    assert ai_conn.execute(
        "SELECT count(*) FROM trust_report_vendor_insurance(%s, %s)", (pmc.pmc_id, DAY)
    ).fetchone()[0] == Decimal(3)
    assert (
        ai_conn.execute(
            "SELECT count(*) FROM trust_report_inspection(%s, %s)", (pmc.pmc_id, lease)
        ).fetchone()[0]
        >= 3
    )
