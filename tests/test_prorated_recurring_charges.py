"""Prorated recurring charges (migration 20261013000048): the rent run charges a recurring fee or
credit for a part month the way it charges rent (tests/test_prorated_rent.py): its amount x days
of the lease in the month / days in the month, rounded to the cent. One in force on the due date
of a lease in effect then is charged for the days of the month the lease is in effect, due that
day; one in force on the first day of a lease starting later that month, with nothing charged for
it in the month yet, is charged its first part month, due on that day. A charge naming a
recurring charge is that amount for its due date's month (the trust_recurring_charge_amount
trigger)."""

from datetime import date
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_recurring_charge,
    add_unit,
    charge_recurring_due,
    make_pmc,
    open_lease,
    stop_recurring_charge,
)

MAR_1, APR_1 = date(2026, 3, 1), date(2026, 4, 1)
MAR_15 = date(2026, 3, 15)


def lease_on(conn, pmc, starts_on, ends_on=None, name="Unit 1"):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    return open_lease(conn, pmc.pmc_id, unit, starts_on, ends_on, "1500.00", [tenant])


def charged(conn, lease):
    """(due date, kind, amount, memo) of the lease's charges made for a recurring charge."""
    return conn.execute(
        "SELECT due_on, kind, amount, memo FROM trust_charges WHERE lease_id = %s"
        " AND recurring_charge_id IS NOT NULL ORDER BY due_on, memo",
        (lease,),
    ).fetchall()


def by_hand(conn, pmc, lease, recurring, due_on, amount, kind="fee"):
    """A charge entered straight into trust_charges, naming a recurring charge."""
    return conn.execute(
        "INSERT INTO trust_charges"
        " (pmc_id, lease_id, due_on, kind, amount, recurring_charge_id)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (pmc.pmc_id, lease, due_on, kind, Decimal(amount), recurring),
    ).fetchone()[0]


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


# --- the run ------------------------------------------------------------------------------


def test_a_lease_starting_later_in_the_month_is_charged_its_part_month_due_its_first_day(
    conn, app_conn, pmc
):
    lease = lease_on(conn, pmc, MAR_15)
    add_recurring_charge(app_conn, pmc.pmc_id, lease, "35.00", MAR_15, "Pet rent")
    add_recurring_charge(app_conn, pmc.pmc_id, lease, "100.00", MAR_15, "Concession", "credit")

    assert charge_recurring_due(app_conn, pmc.pmc_id, MAR_1) == 2
    assert charge_recurring_due(app_conn, pmc.pmc_id, APR_1) == 2
    assert charged(conn, lease) == [
        (  # 100.00 x 17 / 31 = 54.8387...
            MAR_15,
            "credit",
            Decimal("54.84"),
            "Concession due 2026-03-15, 2026-03-15 to 2026-03-31 (17 of 31 days)",
        ),
        (  # 35.00 x 17 / 31 = 19.1935...
            MAR_15,
            "fee",
            Decimal("19.19"),
            "Pet rent due 2026-03-15, 2026-03-15 to 2026-03-31 (17 of 31 days)",
        ),
        (APR_1, "credit", Decimal("100.00"), "Concession due 2026-04-01"),
        (APR_1, "fee", Decimal("35.00"), "Pet rent due 2026-04-01"),
    ]


def test_a_lease_ending_in_the_month_is_charged_to_its_last_day(conn, pmc):
    lease = lease_on(conn, pmc, date(2026, 1, 1), date(2026, 3, 10))
    add_recurring_charge(conn, pmc.pmc_id, lease, "50.00", date(2026, 1, 1), "Parking")

    assert charge_recurring_due(conn, pmc.pmc_id, MAR_1) == 1
    assert charged(conn, lease) == [  # 50.00 x 10 / 31 = 16.1290...
        (
            MAR_1,
            "fee",
            Decimal("16.13"),
            "Parking due 2026-03-01, 2026-03-01 to 2026-03-10 (10 of 31 days)",
        )
    ]


def test_a_lease_starting_and_ending_in_one_month_is_charged_its_days(conn, pmc):
    lease = lease_on(conn, pmc, date(2026, 3, 10), date(2026, 3, 20))
    add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", date(2026, 3, 10))

    assert charge_recurring_due(conn, pmc.pmc_id, MAR_1) == 1
    assert charged(conn, lease) == [  # 35.00 x 11 / 31 = 12.4193...
        (
            date(2026, 3, 10),
            "fee",
            Decimal("12.42"),
            "Pet rent due 2026-03-10, 2026-03-10 to 2026-03-20 (11 of 31 days)",
        )
    ]


def test_half_a_cent_rounds_up(conn, pmc):
    lease = lease_on(conn, pmc, date(2026, 4, 16))
    add_recurring_charge(conn, pmc.pmc_id, lease, "10.01", date(2026, 4, 16))

    charge_recurring_due(conn, pmc.pmc_id, APR_1)

    assert charged(conn, lease)[0][2] == Decimal("5.01")  # 10.01 x 15 / 30 = 5.005


def test_a_part_month_that_rounds_to_nothing_is_not_charged(conn, pmc):
    ending = lease_on(conn, pmc, date(2026, 1, 1), MAR_1, name="Unit 1")
    starting = lease_on(conn, pmc, date(2026, 3, 31), name="Unit 2")
    add_recurring_charge(conn, pmc.pmc_id, ending, "0.01", date(2026, 1, 1))
    add_recurring_charge(conn, pmc.pmc_id, starting, "0.01", date(2026, 3, 31))

    assert charge_recurring_due(conn, pmc.pmc_id, MAR_1) == 0  # 0.01 x 1 / 31, twice
    assert charged(conn, ending) == charged(conn, starting) == []


def test_a_run_mid_month_charges_from_the_leases_first_day(conn, pmc):
    lease = lease_on(conn, pmc, date(2026, 3, 5))
    add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", date(2026, 3, 5))

    assert charge_recurring_due(conn, pmc.pmc_id, date(2026, 3, 10)) == 1
    assert charged(conn, lease) == [  # 35.00 x 27 / 31 = 30.4838...
        (
            date(2026, 3, 10),
            "fee",
            Decimal("30.48"),
            "Pet rent due 2026-03-10, 2026-03-05 to 2026-03-31 (27 of 31 days)",
        )
    ]


def test_the_run_never_reaches_back_into_an_earlier_month(conn, pmc):
    lease = lease_on(conn, pmc, date(2026, 2, 15))
    add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", date(2026, 2, 15))

    assert charge_recurring_due(conn, pmc.pmc_id, MAR_1) == 1
    assert charged(conn, lease) == [(MAR_1, "fee", Decimal("35.00"), "Pet rent due 2026-03-01")]


@pytest.mark.parametrize(
    ("starts_on", "ends_on", "count"),
    [
        (MAR_15, None, 1),
        (date(2026, 3, 16), None, 0),  # first charged on a due date from the 16th
        (date(2026, 3, 1), MAR_15, 0),  # stopped from the lease's first day
        (date(2026, 3, 1), date(2026, 3, 16), 1),
    ],
)
def test_the_first_part_month_is_of_charges_in_force_on_the_leases_first_day(
    conn, pmc, starts_on, ends_on, count
):
    lease = lease_on(conn, pmc, MAR_15)
    recurring = add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", starts_on)
    if ends_on:
        stop_recurring_charge(conn, pmc.pmc_id, recurring, ends_on)

    assert charge_recurring_due(conn, pmc.pmc_id, MAR_1) == count
    assert [row[0] for row in charged(conn, lease)] == [MAR_15] * count


def test_runs_again_in_the_month_charge_the_first_part_month_once(conn, pmc):
    lease = lease_on(conn, pmc, date(2026, 3, 20))
    add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", date(2026, 3, 20))

    assert charge_recurring_due(conn, pmc.pmc_id, MAR_1) == 1
    assert charge_recurring_due(conn, pmc.pmc_id, MAR_1) == 0
    assert charge_recurring_due(conn, pmc.pmc_id, date(2026, 3, 5)) == 0
    assert [row[0] for row in charged(conn, lease)] == [date(2026, 3, 20)]


def test_a_charge_already_in_the_first_month_stops_the_part_month(conn, pmc):
    lease = lease_on(conn, pmc, MAR_15)
    recurring = add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", MAR_15)
    by_hand(conn, pmc, lease, recurring, date(2026, 3, 20), "19.19")

    assert charge_recurring_due(conn, pmc.pmc_id, MAR_1) == 0
    assert [row[:3] for row in charged(conn, lease)] == [
        (date(2026, 3, 20), "fee", Decimal("19.19"))
    ]


def test_another_pmcs_lease_starting_later_that_month_is_not_charged(conn, pmc):
    other = make_pmc(conn)
    theirs = lease_on(conn, other, MAR_15)
    add_recurring_charge(conn, other.pmc_id, theirs, "35.00", MAR_15)

    assert charge_recurring_due(conn, pmc.pmc_id, MAR_1) == 0
    assert charged(conn, theirs) == []


# --- the amount of a charge naming one ------------------------------------------------------


def test_a_charge_naming_a_recurring_charge_is_its_amount_for_the_month(conn, app_conn, pmc):
    lease = lease_on(conn, pmc, MAR_15)
    recurring = add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", MAR_15)

    with pytest.raises(psycopg.errors.CheckViolation, match="is 19.19 for the days.*not 35.00"):
        by_hand(app_conn, pmc, lease, recurring, date(2026, 3, 20), "35.00")
    app_conn.rollback()
    by_hand(app_conn, pmc, lease, recurring, date(2026, 3, 20), "19.19")
    by_hand(app_conn, pmc, lease, recurring, APR_1, "35.00")


def test_a_charge_naming_a_recurring_charge_is_in_a_month_its_lease_is_in_effect(conn, pmc):
    lease = lease_on(conn, pmc, MAR_15, date(2026, 4, 30))
    recurring = add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", MAR_15)

    for due_on in (date(2026, 2, 20), date(2026, 5, 1)):
        with pytest.raises(psycopg.errors.CheckViolation, match="for the days its lease"):
            by_hand(conn, pmc, lease, recurring, due_on, "35.00")


@pytest.mark.parametrize("change", ["another lease", "another kind"])
def test_a_charge_naming_another_leases_or_kinds_recurring_charge_is_left_to_the_key(
    conn, pmc, change
):
    """Not held to the amount for the recurring charge's own lease (19.19 in March): the key
    refuses it."""
    mid_month = lease_on(conn, pmc, MAR_15, name="Unit 1")
    recurring = add_recurring_charge(conn, pmc.pmc_id, mid_month, "35.00", MAR_15)
    lease, kind = mid_month, "credit"
    if change == "another lease":
        lease, kind = lease_on(conn, pmc, date(2026, 1, 1), name="Unit 2"), "fee"

    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="recurring_charge_fkey"):
        by_hand(conn, pmc, lease, recurring, date(2026, 3, 20), "35.00", kind)
