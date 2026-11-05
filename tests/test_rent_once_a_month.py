"""Rent and recurring charges once a month (migration 20261105000400; docs/edge-cases.md M-034,
M-035, D-025): the rent run charges a lease due on the first its month's rent once, whatever day
it is run for. A run for a mid-month day (Run rent now's Rent due on set to the 15th), or a
carried-over rent due on another day of the month, never charges the month again; each monthly
charge (pet rent, parking) likewise. Every name here is synthetic."""

from datetime import date
from decimal import Decimal

import pytest
from helpers import add_recurring_charge, add_tenant, add_unit, make_pmc, open_lease

OCT_1, OCT_15, NOV_1 = date(2026, 10, 1), date(2026, 10, 15), date(2026, 11, 1)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def lease_of(conn, pmc, starts_on=date(2026, 1, 1), name="Unit 1"):
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, name)
    tenant = add_tenant(conn, pmc.pmc_id, prop, f"Tenant of {name}")
    return open_lease(conn, pmc.pmc_id, unit, starts_on, None, "1200.00", [tenant])


def charges(conn, lease):
    return conn.execute(
        "SELECT due_on, kind, amount, recurring_charge_id IS NOT NULL FROM trust_charges"
        " WHERE lease_id = %s ORDER BY due_on, recurring_charge_id IS NOT NULL, id",
        (lease,),
    ).fetchall()


def run(app_conn, pmc, due_on):
    return app_conn.execute(
        "SELECT trust_run_rent(%s, %s, %s)", (pmc.pmc_id, due_on, due_on)
    ).fetchone()[0]


def test_a_mid_month_run_charges_no_month_twice(conn, app_conn, pmc):
    lease = lease_of(conn, pmc)
    add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", date(2026, 1, 1))

    run(app_conn, pmc, OCT_1)
    run(app_conn, pmc, OCT_15)  # Run rent now with Rent due on set to the 15th
    run(app_conn, pmc, date(2026, 10, 31))

    assert charges(conn, lease) == [
        (OCT_1, "rent", Decimal("1200.00"), False),
        (OCT_1, "fee", Decimal("35.00"), True),
    ]
    # The next month is charged on its first, once.
    run(app_conn, pmc, NOV_1)
    run(app_conn, pmc, date(2026, 11, 2))
    assert [c for c in charges(conn, lease) if c[0] == NOV_1] == [
        (NOV_1, "rent", Decimal("1200.00"), False),
        (NOV_1, "fee", Decimal("35.00"), True),
    ]
    assert len(charges(conn, lease)) == 4


def test_a_month_whose_rent_came_over_on_another_day_is_not_charged_again(conn, app_conn, pmc):
    # The old system's unpaid October rent, carried over as a charge due on the 3rd (switching).
    lease = lease_of(conn, pmc)
    conn.execute(
        "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)"
        " VALUES (%s, %s, '2026-10-03', 'rent', 1200.00, 'Rent carried over')",
        (pmc.pmc_id, lease),
    )
    run(app_conn, pmc, OCT_1)
    assert charges(conn, lease) == [(date(2026, 10, 3), "rent", Decimal("1200.00"), False)]


def test_a_mid_month_run_still_charges_a_month_not_charged_yet(conn, app_conn, pmc):
    # The server was down on the first: a run for the 15th charges what the first would have.
    lease = lease_of(conn, pmc)
    run(app_conn, pmc, OCT_15)
    assert charges(conn, lease) == [(OCT_15, "rent", Decimal("1200.00"), False)]
    run(app_conn, pmc, OCT_1)
    assert len(charges(conn, lease)) == 1


def test_each_lease_is_counted_on_its_own(conn, app_conn, pmc):
    # One lease charged on the first, another opened after it ran but in effect then: the
    # mid-month run charges only the one not charged.
    first = lease_of(conn, pmc, name="Unit 1")
    run(app_conn, pmc, OCT_1)
    second = lease_of(conn, pmc, starts_on=date(2026, 9, 1), name="Unit 2")
    run(app_conn, pmc, OCT_15)
    assert [c[0] for c in charges(conn, first)] == [OCT_1]
    assert [c[0] for c in charges(conn, second)] == [OCT_15]


def test_another_company_is_not_counted(conn, app_conn, pmc):
    other = make_pmc(conn)
    mine, theirs = lease_of(conn, pmc), lease_of(conn, other)
    run(app_conn, other, OCT_1)
    run(app_conn, pmc, OCT_15)
    assert [c[0] for c in charges(conn, mine)] == [OCT_15]
    assert [c[0] for c in charges(conn, theirs)] == [OCT_1]
