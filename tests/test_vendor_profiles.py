"""A vendor's profile (migration 20261016000073): their trade, whether they are a 1099 vendor and
how they are paid, a row after row naming who recorded it; no tax ID is kept."""

import psycopg
import pytest
from helpers import acting_as, add_staff, make_pmc


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def add_profile(conn, pmc_id, vendor, trade=None, gets_1099=False, method=None):
    conn.execute(
        "INSERT INTO trust_vendor_profiles (pmc_id, vendor_id, trade, gets_1099, payment_method)"
        " VALUES (%s, %s, %s, %s, %s)",
        (pmc_id, vendor, trade, gets_1099, method),
    )


def test_profiles_are_kept_row_after_row_naming_who_recorded_them(conn, app_conn, pmc):
    staff = add_staff(conn, pmc.pmc_id)
    with acting_as(app_conn, staff):
        add_profile(app_conn, pmc.pmc_id, pmc.vendor_id, "Plumbing", True, "EFT")
        add_profile(app_conn, pmc.pmc_id, pmc.vendor_id, None, False, "check")

    assert conn.execute(
        "SELECT trade, gets_1099, payment_method, recorded_by FROM trust_vendor_profiles"
        " WHERE vendor_id = %s ORDER BY created_at",
        (pmc.vendor_id,),
    ).fetchall() == [("Plumbing", True, "EFT", staff), (None, False, "check", staff)]


@pytest.mark.parametrize(
    ("trade", "gets_1099", "method"),
    [(" ", False, None), ("x" * 101, False, None), (None, None, None), (None, False, "cash")],
)
def test_a_profile_takes_a_trade_a_1099_answer_and_a_known_method(
    conn, pmc, trade, gets_1099, method
):
    with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation)):
        add_profile(conn, pmc.pmc_id, pmc.vendor_id, trade, gets_1099, method)


def test_a_profile_is_of_a_vendor_of_its_pmc(conn, pmc):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        add_profile(conn, make_pmc(conn).pmc_id, pmc.vendor_id)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE trust_vendor_profiles SET trade = 'Roofing'",
        "DELETE FROM trust_vendor_profiles",
        "TRUNCATE trust_vendor_profiles",
    ],
)
def test_profiles_are_kept_for_good(conn, pmc, statement):
    add_profile(conn, pmc.pmc_id, pmc.vendor_id, "Plumbing")
    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)


def test_the_ai_reads_profiles_and_writes_none(conn, ai_conn, pmc):
    add_profile(conn, pmc.pmc_id, pmc.vendor_id, "Plumbing", True)
    assert ai_conn.execute(
        "SELECT trade, gets_1099 FROM trust_vendor_profiles WHERE vendor_id = %s",
        (pmc.vendor_id,),
    ).fetchall() == [("Plumbing", True)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        add_profile(ai_conn, pmc.pmc_id, pmc.vendor_id)
