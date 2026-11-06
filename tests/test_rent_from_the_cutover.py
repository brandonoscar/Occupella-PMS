"""Rent from the cutover (docs/edge-cases.md M-144): a company switching mid-month brings in
leases whose month's rent the old system charged, and collected or carried over as unpaid. The
rent run charges no rent or monthly charge due before the cutover of the trust bank account
that holds the lease's property's owners' money (trust_lease_cutover): that month is the old
system's, so the first night after the switch charges nothing again and assesses no late fee on
it. From the cutover on, each month is charged as before. Every name here is synthetic."""

from datetime import date
from decimal import Decimal

import pytest
from helpers import (
    add_late_fee_policy,
    add_recurring_charge,
    add_tenant,
    add_unit,
    charge,
    make_pmc,
    open_lease,
    open_with_balances,
)

CUTOVER = date(2026, 3, 15)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def lease_of(conn, pmc, starts_on=date(2025, 6, 1), name="Unit 1", owner=0):
    prop = pmc.owners[owner].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, name)
    tenant = add_tenant(conn, pmc.pmc_id, prop, f"Tenant of {name}")
    return open_lease(conn, pmc.pmc_id, unit, starts_on, None, "1200.00", [tenant])


def switched(conn, pmc, opened_on=CUTOVER):
    """The operating trust bank account opened with the old system's balances."""
    open_with_balances(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        opened_on,
        {pmc.owners[0].account: "500.00"},
        "500.00",
    )


def run(app_conn, pmc, day):
    """The nightly run's rent: the month of `day`, as of it."""
    return app_conn.execute(
        "SELECT trust_run_rent(%s, %s, %s)", (pmc.pmc_id, day.replace(day=1), day)
    ).fetchone()[0]


def charges(conn, lease):
    return conn.execute(
        "SELECT due_on, kind, amount FROM trust_charges WHERE lease_id = %s"
        " ORDER BY due_on, recurring_charge_id IS NOT NULL, id",
        (lease,),
    ).fetchall()


def test_the_first_night_after_a_mid_month_switch_charges_the_old_systems_month_nothing(
    conn, app_conn, pmc
):
    lease = lease_of(conn, pmc)
    add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", date(2025, 6, 1))
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, date(2025, 1, 1), 5, "50.00")
    switched(conn, pmc)

    run(app_conn, pmc, CUTOVER)
    run(app_conn, pmc, date(2026, 3, 31))

    # March's rent and pet rent were the old system's: nothing is charged, so no late fee either.
    assert charges(conn, lease) == []
    # April is the PMS's, charged on its first.
    run(app_conn, pmc, date(2026, 4, 1))
    assert charges(conn, lease) == [
        (date(2026, 4, 1), "rent", Decimal("1200.00")),
        (date(2026, 4, 1), "fee", Decimal("35.00")),
    ]


def test_a_rent_carried_over_as_unpaid_stays_the_months_only_rent(conn, app_conn, pmc):
    lease = lease_of(conn, pmc)
    charge(conn, pmc.pmc_id, lease, date(2026, 3, 1), "1200.00", memo="Rent carried over")
    switched(conn, pmc)

    run(app_conn, pmc, CUTOVER)

    assert charges(conn, lease) == [(date(2026, 3, 1), "rent", Decimal("1200.00"))]


def test_a_rent_due_on_or_after_the_cutover_is_charged(conn, app_conn, pmc):
    # A lease due on the 20th: March's rent comes due after the switch, so the PMS charges it.
    due_20th = lease_of(conn, pmc, name="Unit 20")
    conn.execute(
        "INSERT INTO trust_rent_due_days (pmc_id, lease_id, starts_on, due_day)"
        " VALUES (%s, %s, '2026-03-01', 20)",
        (pmc.pmc_id, due_20th),
    )
    # A lease that starts after the cutover is the PMS's from its first day.
    new = lease_of(conn, pmc, starts_on=date(2026, 3, 20), name="Unit new")
    # A cutover on the first: the first's rent is the PMS's.
    switched(conn, pmc, opened_on=date(2026, 3, 1))

    run(app_conn, pmc, date(2026, 3, 1))
    run(app_conn, pmc, date(2026, 3, 20))

    assert charges(conn, due_20th) == [(date(2026, 3, 20), "rent", Decimal("1200.00"))]
    assert [c[:2] for c in charges(conn, new)] == [(date(2026, 3, 20), "rent")]


def test_a_lease_due_on_its_own_day_before_the_cutover_is_the_old_systems(conn, app_conn, pmc):
    lease = lease_of(conn, pmc)
    conn.execute(
        "INSERT INTO trust_rent_due_days (pmc_id, lease_id, starts_on, due_day)"
        " VALUES (%s, %s, '2026-03-01', 10)",
        (pmc.pmc_id, lease),
    )
    add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", date(2025, 6, 1))
    switched(conn, pmc)

    run(app_conn, pmc, CUTOVER)
    assert charges(conn, lease) == []
    run(app_conn, pmc, date(2026, 4, 10))
    assert [c[:2] for c in charges(conn, lease)] == [
        (date(2026, 4, 10), "rent"),
        (date(2026, 4, 10), "fee"),
    ]


def test_run_rent_now_for_a_day_after_the_cutover_still_leaves_its_month_alone(conn, app_conn, pmc):
    # An admin's Run rent now with Rent due on the 20th: the lease's March rent was due on the
    # first, before the switch, so it stays the old system's.
    lease = lease_of(conn, pmc)
    switched(conn, pmc)
    day = date(2026, 3, 20)
    app_conn.execute("SELECT trust_run_rent(%s, %s, %s)", (pmc.pmc_id, day, day))
    assert charges(conn, lease) == []


def test_a_property_whose_money_never_switched_is_charged_as_before(conn, app_conn, pmc):
    # Owner 2's property: its trust bank account is another one, with no opening balances.
    other_bank = conn.execute(
        "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
        " VALUES (%s, 'operating', 'Synthetic second operating account') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    prop = conn.execute(
        "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, 'Property 3')"
        " RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    conn.execute(
        "SELECT trust_open_ledger_account(%s, %s, 'owner_property', %s, %s)",
        (pmc.pmc_id, other_bank, pmc.owners[1].owner_id, prop),
    )
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit 3")
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 3")
    lease = open_lease(conn, pmc.pmc_id, unit, date(2025, 6, 1), None, "900.00", [tenant])
    switched(conn, pmc)

    run(app_conn, pmc, CUTOVER)

    assert charges(conn, lease) == [(date(2026, 3, 1), "rent", Decimal("900.00"))]


def test_the_cutover_of_a_lease(conn, app_conn, pmc):
    lease = lease_of(conn, pmc)
    cutover = "SELECT trust_lease_cutover(%s)"
    assert app_conn.execute(cutover, (lease,)).fetchone()[0] is None
    switched(conn, pmc)
    assert app_conn.execute(cutover, (lease,)).fetchone()[0] == CUTOVER
