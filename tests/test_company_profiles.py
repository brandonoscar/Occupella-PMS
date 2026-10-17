"""The company's profile (migration 0075, ADR 0006 step 12): its mailing address, whole or none,
and the late fee and NSF fee terms a new property starts with, the late fee terms whole or none.
Append-only, the latest row holding, naming who recorded it; the AI role never reads it.
Every address here is synthetic."""

import psycopg
import pytest
from helpers import acting_as, add_staff, make_pmc

ADDRESS = {
    "address_line_1": "100 Example Street",
    "city": "Sampletown",
    "state": "ZZ",
    "postal_code": "00000",
}
LATE = {"late_grace_days": 5, "late_flat_fee": "50.00", "late_percent": "0"}


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def save(conn, pmc, **fields):
    names = ", ".join(["pmc_id", *fields])
    with conn.transaction():
        return conn.execute(
            f"INSERT INTO trust_company_profiles ({names})"
            f" VALUES ({', '.join(['%s'] * (len(fields) + 1))}) RETURNING id",
            (pmc.pmc_id, *fields.values()),
        ).fetchone()[0]


def refused(match, error=psycopg.errors.CheckViolation):
    return pytest.raises(error, match=match)


def test_a_profile_may_hold_nothing_an_address_default_fees_or_all(conn, pmc):
    save(conn, pmc)
    save(conn, pmc, **ADDRESS, address_line_2="Suite 2")
    save(conn, pmc, **LATE, late_maximum="100.00", nsf_fee="35.00")
    save(conn, pmc, **ADDRESS, **LATE, nsf_fee="0.00")
    assert conn.execute(
        "SELECT count(*) FROM trust_company_profiles WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (4,)


@pytest.mark.parametrize(
    "fields",
    [
        {"address_line_1": "100 Example Street"},
        {**ADDRESS, "city": None},
        {"address_line_2": "Suite 2"},
        {"late_grace_days": 5},
        {**LATE, "late_percent": None},
        {"late_maximum": "100.00"},
    ],
)
def test_an_address_and_late_fee_terms_are_whole_or_not_there(conn, pmc, fields):
    with refused("trust_company_profiles_check"):
        save(conn, pmc, **fields)


@pytest.mark.parametrize(
    ("fields", "check"),
    [
        ({**ADDRESS, "address_line_1": " "}, "address_line_1_check"),
        ({**ADDRESS, "city": "x" * 101}, "city_check"),
        ({**LATE, "late_grace_days": 366}, "late_grace_days_check"),
        ({**LATE, "late_flat_fee": "-1.00"}, "late_flat_fee_check"),
        ({**LATE, "late_flat_fee": "1.001"}, "late_flat_fee_check"),
        ({**LATE, "late_percent": "100.01"}, "late_percent_check"),
        ({**LATE, "late_maximum": "-0.01"}, "late_maximum_check"),
        ({"nsf_fee": "-1.00"}, "nsf_fee_check"),
        ({"nsf_fee": "35.005"}, "nsf_fee_check"),
    ],
)
def test_its_terms_stay_in_range(conn, pmc, fields, check):
    with refused(f"trust_company_profiles_{check}"):
        save(conn, pmc, **fields)


def test_a_profile_is_append_only_names_who_recorded_it_and_is_logged(conn, pmc):
    staff = add_staff(conn, pmc.pmc_id)
    with acting_as(conn, staff):
        made = save(conn, pmc, **ADDRESS)
    assert conn.execute(
        "SELECT recorded_by FROM trust_company_profiles WHERE id = %s", (made,)
    ).fetchone() == (staff,)
    assert conn.execute(
        "SELECT staff_id FROM trust_audit_log WHERE table_name = 'trust_company_profiles'"
        " AND row_data->>'id' = %s",
        (str(made),),
    ).fetchone() == (staff,)
    for change in (
        "UPDATE trust_company_profiles SET city = 'Elsewhere'",
        "DELETE FROM trust_company_profiles",
    ):
        with refused("append-only", psycopg.errors.RestrictViolation), conn.transaction():
            conn.execute(change)


def test_a_profile_names_only_its_own_companys_staff(conn, pmc):
    other = make_pmc(conn)
    theirs = add_staff(conn, other.pmc_id)
    with acting_as(conn, theirs), refused("foreign key", psycopg.errors.ForeignKeyViolation):
        save(conn, pmc, **ADDRESS)
