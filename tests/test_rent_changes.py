"""A lease's rent changes from the first of a month, without a renewal (migration
20261015000061): after written notice of a full month, for a lease with an end date only as its
terms allow, and never for a month already charged. The rent run charges the rent in force, a
lease's end adjusts at it, and the reports show it.
"""

from datetime import date
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    add_unit,
    charge_rent_due,
    end_lease,
    make_pmc,
    open_lease,
    staff_named,
)

JAN_1, FEB_1, MAR_1, APR_1 = date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]


def a_lease(conn, pmc, starts=JAN_1, ends=None, rent="1000.00", unit="Unit 1"):
    unit_id = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, unit)
    return open_lease(conn, pmc.pmc_id, unit_id, starts, ends, rent, [tenant_of(conn, pmc)])


def change(conn, pmc, lease, effective, rent, notice, served="mail", terms=None):
    conn.execute(
        "INSERT INTO trust_rent_changes (pmc_id, lease_id, effective_on, monthly_rent,"
        " notice_given_on, served_by, lease_terms) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (pmc.pmc_id, lease, effective, Decimal(rent), notice, served, terms),
    )


def rents(conn, lease):
    return conn.execute(
        "SELECT due_on, amount FROM trust_charges WHERE lease_id = %s AND kind = 'rent'"
        " ORDER BY due_on",
        (lease,),
    ).fetchall()


def test_the_rent_run_charges_the_rent_in_force_each_month(conn, pmc):
    lease = a_lease(conn, pmc)
    change(conn, pmc, lease, MAR_1, "1100.00", FEB_1)

    for due in (JAN_1, FEB_1, MAR_1, APR_1):
        charge_rent_due(conn, pmc.pmc_id, due)

    assert rents(conn, lease) == [
        (JAN_1, Decimal("1000.00")),
        (FEB_1, Decimal("1000.00")),
        (MAR_1, Decimal("1100.00")),
        (APR_1, Decimal("1100.00")),
    ]
    assert conn.execute("SELECT trust_rent_on(%s, %s)", (lease, date(2026, 2, 28))).fetchone() == (
        Decimal("1000.00"),
    )


def test_a_later_change_takes_over_from_its_month(conn, pmc):
    lease = a_lease(conn, pmc, starts=date(2026, 1, 15))
    change(conn, pmc, lease, MAR_1, "1100.00", JAN_1)
    change(conn, pmc, lease, date(2026, 5, 1), "950.00", date(2026, 3, 20))

    for month in range(1, 7):
        charge_rent_due(conn, pmc.pmc_id, date(2026, month, 1))

    # January's part month at the lease's own rent (17 of 31 days), then each month's in force.
    assert [amount for _, amount in rents(conn, lease)] == [
        Decimal("548.39"),
        Decimal("1000.00"),
        Decimal("1100.00"),
        Decimal("1100.00"),
        Decimal("950.00"),
        Decimal("950.00"),
    ]


@pytest.mark.parametrize(
    ("notice", "allowed"),
    [(FEB_1, True), (date(2026, 1, 20), True), (date(2026, 2, 2), False), (MAR_1, False)],
)
def test_written_notice_of_a_full_month_comes_first(conn, pmc, notice, allowed):
    lease = a_lease(conn, pmc)
    if allowed:
        change(conn, pmc, lease, MAR_1, "1100.00", notice)
    else:
        with pytest.raises(psycopg.errors.CheckViolation, match="trust_rent_changes_check"):
            change(conn, pmc, lease, MAR_1, "1100.00", notice)


def test_a_change_starts_on_the_first_of_a_month_after_the_lease_starts_and_within_it(conn, pmc):
    lease = a_lease(conn, pmc, starts=FEB_1, ends=date(2026, 12, 31))
    with pytest.raises(psycopg.errors.CheckViolation, match="effective_on_check"):
        change(conn, pmc, lease, date(2026, 3, 15), "1100.00", JAN_1, terms="Clause 4")
    with pytest.raises(psycopg.errors.CheckViolation, match="after the lease starts"):
        change(conn, pmc, lease, FEB_1, "1100.00", date(2025, 12, 1), terms="Clause 4")
    with pytest.raises(psycopg.errors.CheckViolation, match="after the lease starts"):
        change(conn, pmc, lease, date(2027, 1, 1), "1100.00", JAN_1, terms="Clause 4")
    change(conn, pmc, lease, MAR_1, "1100.00", JAN_1, terms="Clause 4")


def test_a_lease_with_an_end_date_changes_rent_only_as_its_terms_allow(conn, pmc):
    lease = a_lease(conn, pmc, ends=date(2026, 12, 31))
    with pytest.raises(psycopg.errors.CheckViolation, match="name the term"):
        change(conn, pmc, lease, MAR_1, "1100.00", FEB_1)

    change(conn, pmc, lease, MAR_1, "1100.00", FEB_1, terms="Section 3: rent may rise on 30 days")

    assert conn.execute(
        "SELECT lease_terms FROM trust_rent_changes WHERE lease_id = %s", (lease,)
    ).fetchone() == ("Section 3: rent may rise on 30 days",)


def test_a_month_already_charged_keeps_its_rent(conn, pmc):
    lease = a_lease(conn, pmc)
    charge_rent_due(conn, pmc.pmc_id, MAR_1)

    with pytest.raises(psycopg.errors.CheckViolation, match="rent from 2026-03-01 is charged"):
        change(conn, pmc, lease, MAR_1, "1100.00", FEB_1)
    change(conn, pmc, lease, APR_1, "1100.00", MAR_1)


def test_a_lease_ended_mid_month_keeps_the_days_at_the_rent_in_force(conn, pmc):
    lease = a_lease(conn, pmc)
    change(conn, pmc, lease, MAR_1, "1240.00", FEB_1)
    charge_rent_due(conn, pmc.pmc_id, MAR_1)

    end_lease(conn, pmc.pmc_id, lease, date(2026, 3, 15))

    # March came to 15 of its 31 days at the new rent: 1240.00 less a credit of 640.00.
    assert conn.execute(
        "SELECT kind, amount FROM trust_charges WHERE lease_id = %s AND kind = 'credit'", (lease,)
    ).fetchall() == [("credit", Decimal("640.00"))]


def test_one_change_per_lease_and_month_named_and_kept_for_good(conn, pmc):
    lease = a_lease(conn, pmc)
    manager = staff_named(conn, pmc.pmc_id, "Manager 1")
    with acting_as(conn, manager):
        change(conn, pmc, lease, MAR_1, "1100.00", FEB_1)
    with pytest.raises(psycopg.errors.UniqueViolation):
        change(conn, pmc, lease, MAR_1, "1200.00", FEB_1)
    assert conn.execute(
        "SELECT recorded_by FROM trust_rent_changes WHERE lease_id = %s", (lease,)
    ).fetchone() == (manager,)
    for statement in (
        "UPDATE trust_rent_changes SET monthly_rent = 1",
        "DELETE FROM trust_rent_changes",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(statement)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("monthly_rent", "0"),
        ("monthly_rent", "1.001"),
        ("served_by", "carrier pigeon"),
        ("lease_terms", " "),
    ],
)
def test_a_change_names_a_rent_and_how_notice_was_served(conn, pmc, column, value):
    lease = a_lease(conn, pmc, ends=date(2026, 12, 31))
    fields = {"monthly_rent": Decimal("1100.00"), "served_by": "mail", "lease_terms": "Clause 4"}
    fields[column] = Decimal(value) if column == "monthly_rent" else value
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_rent_changes (pmc_id, lease_id, effective_on, monthly_rent,"
            " notice_given_on, served_by, lease_terms) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                pmc.pmc_id,
                lease,
                MAR_1,
                fields["monthly_rent"],
                FEB_1,
                fields["served_by"],
                fields["lease_terms"],
            ),
        )


def test_the_reports_show_the_rent_in_force_on_their_day(conn, pmc):
    lease = a_lease(conn, pmc, ends=date(2026, 4, 30))
    change(conn, pmc, lease, MAR_1, "1100.00", FEB_1, terms="Clause 4")

    def rent_roll(on):
        return conn.execute(
            "SELECT monthly_rent FROM trust_report_rent_roll(%s, %s) WHERE item = 'unit'",
            (pmc.pmc_id, on),
        ).fetchall()

    assert rent_roll(date(2026, 2, 15)) == [(Decimal("1000.00"),)]
    assert rent_roll(date(2026, 3, 2)) == [(Decimal("1100.00"),)]
    assert conn.execute(
        "SELECT monthly_rent FROM trust_report_lease_expirations(%s, %s) WHERE item = 'lease'",
        (pmc.pmc_id, date(2026, 3, 2)),
    ).fetchall() == [(Decimal("1100.00"),)]
    assert conn.execute(
        "SELECT monthly_rent FROM trust_report_tenant_directory(%s, %s)"
        " WHERE monthly_rent IS NOT NULL",
        (pmc.pmc_id, date(2026, 3, 2)),
    ).fetchall() == [(Decimal("1100.00"),)]
    # Once it ended, the unit's last rent was the one in force at its end.
    vacancy = conn.execute(
        "SELECT * FROM trust_report_vacancy(%s, %s)", (pmc.pmc_id, date(2026, 5, 10))
    ).fetchall()
    assert any(Decimal("1100.00") in row for row in vacancy), vacancy
