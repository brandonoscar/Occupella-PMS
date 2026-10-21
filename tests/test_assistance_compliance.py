"""Keeping a housing-assistance lease in compliance (migration 20261021000108; the founder's
review q143): the household's income certifications, the home's inspections for its authority,
and trust_report_assistance_compliance, which says where each lease with assistance stands on a
day without printing anyone's income."""

from datetime import date

import psycopg
import pytest
from helpers import add_authority, add_tenant, add_unit, assist, make_pmc, open_lease

DAY = date(2026, 6, 15)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def lease(conn, pmc, unit="Unit 1", tenant="Tenant 9", starts=date(2025, 1, 1)):
    prop = pmc.owners[0].property_id
    return open_lease(
        conn, pmc.pmc_id, add_unit(conn, pmc.pmc_id, prop, unit), starts, None, "1500.00",
        [add_tenant(conn, pmc.pmc_id, prop, tenant)],
    )  # fmt: skip


def certify(conn, pmc_id, lease_id, effective, due, kind="annual", income="18000.00", size=3):
    conn.execute(
        "INSERT INTO trust_income_certifications (pmc_id, lease_id, kind, effective_on,"
        " household_size, annual_income, next_due_on) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (pmc_id, lease_id, kind, effective, size, income, due),
    )


def inspect(conn, pmc_id, lease_id, authority, day, result, due):
    conn.execute(
        "INSERT INTO trust_assistance_inspections (pmc_id, lease_id, housing_authority_id,"
        " inspected_on, result, due_on) VALUES (%s, %s, %s, %s, %s, %s)",
        (pmc_id, lease_id, authority, day, result, due),
    )


def report(conn, pmc_id, day=DAY):
    return conn.execute(
        "SELECT item, unit, tenants, authority, certified, recertify_by, recertification,"
        " inspected, inspect_by, inspection"
        " FROM trust_report_assistance_compliance(%s, %s) WHERE line > 2 ORDER BY line",
        (pmc_id, day),
    ).fetchall()


def test_each_assisted_lease_says_where_its_certification_and_inspection_stand(conn, pmc):
    authority = add_authority(conn, pmc.pmc_id)
    current, soon, late, never = (
        lease(conn, pmc, f"Unit {n}", f"Tenant {n}") for n in (1, 2, 3, 4)
    )
    for one in (current, soon, late, never):
        assist(conn, pmc.pmc_id, one, authority, date(2025, 1, 1), "700.00")
    lease(conn, pmc, "Unit 5", "Tenant 5")  # no assistance: left out
    certify(conn, pmc.pmc_id, current, date(2026, 1, 1), date(2027, 1, 1))
    inspect(conn, pmc.pmc_id, current, authority, date(2025, 12, 1), "passed", date(2027, 12, 1))
    certify(conn, pmc.pmc_id, soon, date(2025, 9, 1), date(2026, 9, 1))
    inspect(conn, pmc.pmc_id, soon, authority, date(2026, 6, 1), "failed", date(2026, 7, 1))
    certify(conn, pmc.pmc_id, late, date(2025, 1, 1), date(2026, 1, 1), kind="move-in")
    inspect(conn, pmc.pmc_id, late, authority, date(2026, 3, 1), "failed", date(2026, 4, 1))

    rows = report(conn, pmc.pmc_id)

    assert rows == [
        ("assisted lease", "Unit 1", "Tenant 1", "Synthetic Housing Authority", "annual 01/01/2026",
         date(2027, 1, 1), "current", "passed 12/01/2025", date(2027, 12, 1), "current"),
        ("assisted lease", "Unit 2", "Tenant 2", "Synthetic Housing Authority", "annual 09/01/2025",
         date(2026, 9, 1), "due within 120 days", "failed 06/01/2026", date(2026, 7, 1),
         "failed, reinspection due"),
        ("assisted lease", "Unit 3", "Tenant 3", "Synthetic Housing Authority",
         "move-in 01/01/2025", date(2026, 1, 1), "overdue", "failed 03/01/2026", date(2026, 4, 1),
         "failed, reinspection overdue"),
        ("assisted lease", "Unit 4", "Tenant 4", "Synthetic Housing Authority", None, None,
         "none on file", None, None, "none on file"),
        ("total", None, None, None, None, None, "2 to certify now, 1 within 120 days",
         None, None, "2 to inspect now, 1 within 120 days or failed"),
    ]  # fmt: skip


def test_the_latest_by_its_day_holds_and_one_to_come_counts_once_it_takes_effect(conn, pmc):
    authority = add_authority(conn, pmc.pmc_id)
    one = lease(conn, pmc)
    assist(conn, pmc.pmc_id, one, authority, date(2025, 1, 1), "700.00")
    certify(conn, pmc.pmc_id, one, date(2025, 7, 1), date(2026, 7, 1))
    # An interim one later keeps the annual's due day; next year's takes effect in July.
    certify(conn, pmc.pmc_id, one, date(2026, 2, 1), date(2026, 7, 1), kind="interim")
    certify(conn, pmc.pmc_id, one, date(2026, 7, 1), date(2027, 7, 1))

    before, after = report(conn, pmc.pmc_id), report(conn, pmc.pmc_id, date(2026, 7, 2))

    assert before[0][4:7] == ("interim 02/01/2026", date(2026, 7, 1), "due within 120 days")
    assert after[0][4:7] == ("annual 07/01/2026", date(2027, 7, 1), "current")


def test_a_lease_without_assistance_in_force_is_left_out(conn, pmc):
    authority = add_authority(conn, pmc.pmc_id)
    ended = lease(conn, pmc)
    assist(conn, pmc.pmc_id, ended, authority, date(2025, 1, 1), "700.00")
    assist(conn, pmc.pmc_id, ended, authority, date(2026, 6, 1), "0.00")

    assert report(conn, pmc.pmc_id) == [
        ("total", None, None, None, None, None, "0 to certify now, 0 within 120 days",
         None, None, "0 to inspect now, 0 within 120 days or failed"),
    ]  # fmt: skip


def test_records_are_of_an_assisted_lease_within_its_days(conn, pmc):
    authority = add_authority(conn, pmc.pmc_id)
    plain = lease(conn, pmc, "Unit 1", "Tenant 1")
    one = lease(conn, pmc, "Unit 2", "Tenant 2", starts=date(2025, 3, 1))
    assist(conn, pmc.pmc_id, one, authority, date(2025, 3, 1), "700.00")

    with pytest.raises(psycopg.errors.CheckViolation, match="no housing assistance on file"):
        certify(conn, pmc.pmc_id, plain, date(2026, 1, 1), date(2027, 1, 1))
    with pytest.raises(psycopg.errors.CheckViolation, match="is outside the lease"):
        certify(conn, pmc.pmc_id, one, date(2025, 2, 1), date(2026, 2, 1))
    with pytest.raises(psycopg.errors.CheckViolation, match="recorded once it happened"):
        inspect(conn, pmc.pmc_id, one, authority, date(2099, 1, 1), "passed", date(2100, 1, 1))
    with pytest.raises(psycopg.errors.CheckViolation):  # due after the day itself
        certify(conn, pmc.pmc_id, one, date(2026, 1, 1), date(2026, 1, 1))
    with pytest.raises(psycopg.errors.CheckViolation):
        certify(conn, pmc.pmc_id, one, date(2026, 1, 1), date(2027, 1, 1), size=0)
    with pytest.raises(psycopg.errors.CheckViolation):
        inspect(conn, pmc.pmc_id, one, authority, date(2026, 1, 1), "maybe", date(2026, 2, 1))


def test_records_are_never_rewritten_and_the_ai_reads_no_income(conn, ai_conn, pmc):
    authority = add_authority(conn, pmc.pmc_id)
    one = lease(conn, pmc)
    assist(conn, pmc.pmc_id, one, authority, date(2025, 1, 1), "700.00")
    certify(conn, pmc.pmc_id, one, date(2026, 1, 1), date(2027, 1, 1))
    inspect(conn, pmc.pmc_id, one, authority, date(2026, 1, 5), "passed", date(2028, 1, 5))

    for change in (
        "UPDATE trust_income_certifications SET annual_income = 0",
        "DELETE FROM trust_income_certifications",
        "UPDATE trust_assistance_inspections SET result = 'failed'",
        "DELETE FROM trust_assistance_inspections",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(change)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ai_conn.execute("SELECT annual_income FROM trust_income_certifications")
    # The AI runs the report: days and words, nothing of the household's income or size.
    standing = report(ai_conn, pmc.pmc_id)[0][4:7]
    assert standing == ("annual 01/01/2026", date(2027, 1, 1), "current")


@pytest.mark.parametrize("day", [None])
def test_the_report_needs_a_day_and_a_company(conn, pmc, day):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="as of a day"):
        report(conn, pmc.pmc_id, day)
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no PMC"):
        report(conn, make_pmc(conn).owners[0].owner_id)
