"""Prorated rent (migration 20261013000045): the rent run charges a part month for its actual
days, monthly rent x days of the lease in the month / days in the month, rounded to the cent.
A lease in effect on the due date is charged for the days of the month it is in effect, due that
day; a lease starting later that month, with no rent charged in the month yet, is charged its
first part month, due on its first day. The run never reaches back into an earlier month."""

from datetime import date
from decimal import Decimal

from helpers import add_unit, charge, charge_rent_due, make_pmc, open_lease

RENT = Decimal("1500.00")
MAR_1, APR_1 = date(2026, 3, 1), date(2026, 4, 1)


def lease_on(conn, pmc, starts_on, ends_on=None, rent=RENT, name="Unit 1"):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    return open_lease(conn, pmc.pmc_id, unit, starts_on, ends_on, rent, [tenant])


def rents(conn, lease):
    return conn.execute(
        "SELECT due_on, amount, memo FROM trust_charges WHERE lease_id = %s AND kind = 'rent'"
        " ORDER BY due_on",
        (lease,),
    ).fetchall()


def test_a_lease_starting_later_in_the_month_is_charged_its_part_month_due_its_first_day(
    conn, app_conn
):
    pmc = make_pmc(conn)
    lease = lease_on(conn, pmc, date(2026, 3, 15))

    assert charge_rent_due(app_conn, pmc.pmc_id, MAR_1) == 1
    assert charge_rent_due(app_conn, pmc.pmc_id, APR_1) == 1
    assert rents(conn, lease) == [
        (  # 1500.00 x 17 / 31 = 822.5806...
            date(2026, 3, 15),
            Decimal("822.58"),
            "Rent due 2026-03-15, 2026-03-15 to 2026-03-31 (17 of 31 days)",
        ),
        (APR_1, RENT, "Rent due 2026-04-01"),
    ]


def test_a_lease_ending_in_the_month_is_charged_to_its_last_day(conn):
    pmc = make_pmc(conn)
    lease = lease_on(conn, pmc, date(2026, 1, 1), date(2026, 3, 10))

    charge_rent_due(conn, pmc.pmc_id, MAR_1)

    assert rents(conn, lease) == [  # 1500.00 x 10 / 31 = 483.8709...
        (MAR_1, Decimal("483.87"), "Rent due 2026-03-01, 2026-03-01 to 2026-03-10 (10 of 31 days)")
    ]


def test_a_lease_starting_and_ending_in_one_month_is_charged_its_days(conn):
    pmc = make_pmc(conn)
    lease = lease_on(conn, pmc, date(2026, 3, 10), date(2026, 3, 20))

    charge_rent_due(conn, pmc.pmc_id, MAR_1)

    assert rents(conn, lease) == [  # 1500.00 x 11 / 31 = 532.2580...
        (
            date(2026, 3, 10),
            Decimal("532.26"),
            "Rent due 2026-03-10, 2026-03-10 to 2026-03-20 (11 of 31 days)",
        )
    ]


def test_a_month_is_its_own_number_of_days(conn):
    """February 2028 has 29 days, April 30."""
    pmc = make_pmc(conn)
    leap = lease_on(conn, pmc, date(2028, 2, 15), name="Unit 1")
    april = lease_on(conn, pmc, date(2026, 4, 11), date(2026, 4, 30), name="Unit 2")

    charge_rent_due(conn, pmc.pmc_id, date(2028, 2, 1))
    charge_rent_due(conn, pmc.pmc_id, APR_1)

    assert [r[1:] for r in rents(conn, leap)] == [  # 1500.00 x 15 / 29 = 775.8620...
        (Decimal("775.86"), "Rent due 2028-02-15, 2028-02-15 to 2028-02-29 (15 of 29 days)")
    ]
    assert [r[1:] for r in rents(conn, april)] == [  # 1500.00 x 20 / 30
        (Decimal("1000.00"), "Rent due 2026-04-11, 2026-04-11 to 2026-04-30 (20 of 30 days)")
    ]


def test_half_a_cent_rounds_up(conn):
    pmc = make_pmc(conn)
    lease = lease_on(conn, pmc, date(2026, 4, 16), rent=Decimal("1000.01"))

    charge_rent_due(conn, pmc.pmc_id, APR_1)

    assert rents(conn, lease)[0][1] == Decimal("500.01")  # 1000.01 x 15 / 30 = 500.005


def test_a_part_month_that_rounds_to_nothing_is_not_charged(conn):
    pmc = make_pmc(conn)
    ending = lease_on(conn, pmc, date(2026, 1, 1), MAR_1, rent=Decimal("0.01"), name="Unit 1")
    starting = lease_on(conn, pmc, date(2026, 3, 31), rent=Decimal("0.01"), name="Unit 2")

    assert charge_rent_due(conn, pmc.pmc_id, MAR_1) == 0  # 0.01 x 1 / 31, twice
    assert rents(conn, ending) == rents(conn, starting) == []


def test_a_run_mid_month_charges_from_the_leases_first_day(conn):
    pmc = make_pmc(conn)
    lease = lease_on(conn, pmc, date(2026, 3, 5))

    assert charge_rent_due(conn, pmc.pmc_id, date(2026, 3, 10)) == 1
    assert rents(conn, lease) == [  # 1500.00 x 27 / 31 = 1306.4516...
        (
            date(2026, 3, 10),
            Decimal("1306.45"),
            "Rent due 2026-03-10, 2026-03-05 to 2026-03-31 (27 of 31 days)",
        )
    ]


def test_a_lease_entered_after_the_months_run_is_charged_when_it_runs_again(conn):
    pmc = make_pmc(conn)
    early = lease_on(conn, pmc, date(2026, 1, 1), name="Unit 1")
    assert charge_rent_due(conn, pmc.pmc_id, MAR_1) == 1
    late = lease_on(conn, pmc, date(2026, 3, 20), name="Unit 2")

    assert charge_rent_due(conn, pmc.pmc_id, MAR_1) == 1
    assert charge_rent_due(conn, pmc.pmc_id, MAR_1) == 0
    assert len(rents(conn, early)) == 1
    assert [r[0] for r in rents(conn, late)] == [date(2026, 3, 20)]


def test_the_run_never_reaches_back_into_an_earlier_month(conn):
    """A lease brought over from another system that started last month had that month's rent
    charged there."""
    pmc = make_pmc(conn)
    lease = lease_on(conn, pmc, date(2026, 2, 15))

    assert charge_rent_due(conn, pmc.pmc_id, MAR_1) == 1
    assert rents(conn, lease) == [(MAR_1, RENT, "Rent due 2026-03-01")]


def test_a_lease_starting_next_month_is_not_charged(conn):
    pmc = make_pmc(conn)
    lease = lease_on(conn, pmc, date(2026, 4, 5))

    assert charge_rent_due(conn, pmc.pmc_id, MAR_1) == 0
    assert rents(conn, lease) == []


def test_rent_already_charged_in_the_first_month_stops_the_part_month(conn):
    pmc = make_pmc(conn)
    lease = lease_on(conn, pmc, date(2026, 3, 15))
    charge(conn, pmc.pmc_id, lease, date(2026, 3, 20), "700.00")  # entered by hand

    assert charge_rent_due(conn, pmc.pmc_id, MAR_1) == 0
    assert rents(conn, lease) == [(date(2026, 3, 20), Decimal("700.00"), None)]


def test_another_pmcs_lease_starting_later_that_month_is_not_charged(conn):
    pmc, other = make_pmc(conn), make_pmc(conn)
    theirs = lease_on(conn, other, date(2026, 3, 15))

    assert charge_rent_due(conn, pmc.pmc_id, MAR_1) == 0
    assert rents(conn, theirs) == []
