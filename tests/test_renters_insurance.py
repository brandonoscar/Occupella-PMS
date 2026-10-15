"""Renters insurance on file for each lease (migration 20261015000062): whether a lease requires
it and the least liability it asks, the tenants' policies with any insurer, and the report of
how each lease in effect stands on a day.
"""

from datetime import date
from decimal import Decimal

import psycopg
import pytest
from helpers import acting_as, add_unit, make_pmc, open_lease, staff_named

JAN_1, JUN_30 = date(2026, 1, 1), date(2026, 6, 30)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def lease(conn, pmc):
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "1500.00", [tenant])


def require(conn, pmc, lease, required=True, minimum="100000.00", starts=JAN_1):
    conn.execute(
        "INSERT INTO trust_renters_insurance_terms (pmc_id, lease_id, required,"
        " minimum_liability, starts_on) VALUES (%s, %s, %s, %s, %s)",
        (pmc.pmc_id, lease, required, None if minimum is None else Decimal(minimum), starts),
    )


def policy(conn, pmc, lease, number, starts, expires, limit="300000.00", by_tenant=None):
    conn.execute(
        "INSERT INTO trust_renters_insurance (pmc_id, lease_id, carrier, policy_number,"
        " starts_on, expires_on, liability_limit, submitted_by)"
        " VALUES (%s, %s, 'Carrier A', %s, %s, %s, %s, %s)",
        (
            pmc.pmc_id,
            lease,
            number,
            starts,
            expires,
            None if limit is None else Decimal(limit),
            by_tenant,
        ),
    )


def report(conn, pmc, on):
    return conn.execute(
        "SELECT required, minimum_liability, policy_number, expires_on, standing, below_minimum"
        " FROM trust_report_renters_insurance(%s, %s) ORDER BY line",
        (pmc.pmc_id, on),
    ).fetchall()


def test_a_lease_with_nothing_asked_and_nothing_on_file(conn, pmc, lease):
    assert report(conn, pmc, JUN_30) == [(False, None, None, None, "none on file", False)]


def test_the_policy_running_last_counts_and_how_it_stands(conn, pmc, lease):
    require(conn, pmc, lease)
    policy(conn, pmc, lease, "RI-1", JAN_1, date(2026, 7, 10))
    # A renewal recorded early isn't the tenant's until it starts.
    policy(conn, pmc, lease, "RI-2", date(2026, 7, 11), date(2027, 7, 10))

    assert report(conn, pmc, JUN_30) == [
        (True, Decimal("100000.00"), "RI-1", date(2026, 7, 10), "expires in 10 days", False)
    ]
    assert report(conn, pmc, date(2026, 7, 10))[0][4] == "expires today"
    assert report(conn, pmc, date(2026, 7, 11))[0][2:5] == ("RI-2", date(2027, 7, 10), "current")


def test_a_policy_run_out_or_below_the_minimum_says_so(conn, pmc, lease):
    require(conn, pmc, lease, minimum="300000.01")
    policy(conn, pmc, lease, "RI-1", JAN_1, date(2026, 3, 31))

    assert report(conn, pmc, JUN_30)[0][4:] == ("expired", True)
    # Coverage not given counts as below what the lease asks.
    policy(conn, pmc, lease, "RI-2", date(2026, 4, 1), date(2027, 3, 31), limit=None)
    assert report(conn, pmc, JUN_30)[0][4:] == ("current", True)


def test_the_terms_in_force_on_the_day_hold(conn, pmc, lease):
    require(conn, pmc, lease, required=False, minimum=None)
    require(conn, pmc, lease, minimum="50000.00", starts=date(2026, 6, 1))

    assert report(conn, pmc, date(2026, 5, 31))[0][:2] == (False, None)
    assert report(conn, pmc, JUN_30)[0][:2] == (True, Decimal("50000.00"))


def test_a_lease_not_in_effect_is_not_listed(conn, pmc, lease):
    conn.execute("SELECT trust_end_lease(%s, %s, %s)", (pmc.pmc_id, lease, date(2026, 5, 31)))
    assert report(conn, pmc, JUN_30) == []


def test_who_recorded_it_is_named(conn, pmc, lease):
    manager = staff_named(conn, pmc.pmc_id, "Manager 1")
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_lease_tenants WHERE lease_id = %s", (lease,)
    ).fetchone()[0]
    with acting_as(conn, manager):
        require(conn, pmc, lease)
        policy(conn, pmc, lease, "RI-1", JAN_1, JUN_30)
    policy(conn, pmc, lease, "RI-2", JAN_1, JUN_30, by_tenant=tenant)

    assert conn.execute(
        "SELECT policy_number, recorded_by, submitted_by FROM trust_renters_insurance"
        " WHERE lease_id = %s ORDER BY policy_number",
        (lease,),
    ).fetchall() == [("RI-1", manager, None), ("RI-2", None, tenant)]
    # Only a tenant on the lease submits one of its policies.
    other = make_pmc(conn)
    stranger = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (other.tenant_deposit,),
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        policy(conn, pmc, lease, "RI-3", JAN_1, JUN_30, by_tenant=stranger)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("carrier", " "),
        ("policy_number", "x" * 101),
        ("expires_on", date(2025, 12, 31)),
        ("liability_limit", Decimal("0")),
        ("liability_limit", Decimal("1.001")),
    ],
)
def test_a_policy_names_an_insurer_a_number_and_days_that_run_forward(
    conn, pmc, lease, column, value
):
    row = {
        "carrier": "Carrier A",
        "policy_number": "RI-1",
        "starts_on": JAN_1,
        "expires_on": JUN_30,
        "liability_limit": Decimal("100000.00"),
    } | {column: value}
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_renters_insurance (pmc_id, lease_id, carrier, policy_number,"
            " starts_on, expires_on, liability_limit) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (pmc.pmc_id, lease, *row.values()),
        )


def test_terms_ask_a_minimum_only_of_insurance_required_and_both_are_kept_for_good(
    conn, pmc, lease
):
    with pytest.raises(psycopg.errors.CheckViolation):
        require(conn, pmc, lease, required=False, minimum="100.00")
    require(conn, pmc, lease)
    policy(conn, pmc, lease, "RI-1", JAN_1, JUN_30)
    for statement in (
        "UPDATE trust_renters_insurance SET carrier = 'x'",
        "DELETE FROM trust_renters_insurance_terms",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(statement)
