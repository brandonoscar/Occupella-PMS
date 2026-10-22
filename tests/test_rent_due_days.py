"""A lease's rent due day (migration 20261018000081; the founder's review, q013).

Rent is due on the first unless a lease picks another day, 1 to 28, from the first of a month.
Leases due on the first are charged as before; another is charged on its due date, kept within
the days the lease is in effect that month (its first day if it starts after it, its last if it
ends before it), the month's rent and recurring charges for those days, once a month. A month
whose rent is charged keeps its due date. Late fees count their grace from the due date, or from
the day the rent was charged if that came later (migration 0151). Every name here is
synthetic."""

from datetime import date
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    add_late_fee_policy,
    add_recurring_charge,
    add_staff,
    add_tenant,
    add_unit,
    charge_rent_due,
    charged_on,
    end_lease,
    make_pmc,
    open_lease,
    rent_charged_when_due,
    stop_recurring_charge,
)

OCT_1, NOV_1, DEC_1 = date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def lease_of(conn, pmc, starts=date(2026, 9, 1), ends=None, rent="1000.00", unit="Unit 1"):
    tenant = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, f"Tenant of {unit}")
    unit_id = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name=unit)
    return open_lease(conn, pmc.pmc_id, unit_id, starts, ends, rent, [tenant])


def due_day(conn, pmc_id, lease, starts_on, day, by=None):
    with acting_as(conn, by):
        return conn.execute(
            "INSERT INTO trust_rent_due_days (pmc_id, lease_id, starts_on, due_day)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (pmc_id, lease, starts_on, day),
        ).fetchone()[0]


def run(conn, pmc_id, day):
    """The nightly run on a day: the month's rent as of it, and late fees."""
    conn.execute("SELECT trust_run_rent(%s, %s, %s)", (pmc_id, day.replace(day=1), day))


def charged(conn, lease, kind="rent"):
    return conn.execute(
        "SELECT due_on, amount, memo FROM trust_charges WHERE lease_id = %s AND kind = %s"
        " AND (%s <> 'fee' OR recurring_charge_id IS NOT NULL) ORDER BY due_on, id",
        (lease, kind, kind),
    ).fetchall()


def test_rent_due_on_the_tenth_is_charged_on_the_tenth_once(conn, pmc):
    lease = lease_of(conn, pmc)
    due_day(conn, pmc.pmc_id, lease, OCT_1, 10)
    run(conn, pmc.pmc_id, OCT_1)
    assert charged(conn, lease) == []  # the first is not its day any more
    run(conn, pmc.pmc_id, date(2026, 10, 9))
    assert charged(conn, lease) == []
    run(conn, pmc.pmc_id, date(2026, 10, 10))
    run(conn, pmc.pmc_id, date(2026, 10, 25))
    assert charged(conn, lease) == [(date(2026, 10, 10), Decimal("1000.00"), "Rent due 2026-10-10")]
    assert conn.execute(
        "SELECT trust_rent_due_day(%s, %s), trust_rent_due_on(%s, %s)",
        (lease, date(2026, 9, 30), lease, NOV_1),
    ).fetchone() == (1, date(2026, 11, 10))


def test_a_lease_due_on_the_first_is_charged_as_before(conn, pmc):
    lease = lease_of(conn, pmc)
    other = lease_of(conn, pmc, unit="Unit 2")
    due_day(conn, pmc.pmc_id, other, OCT_1, 15)
    assert charge_rent_due(conn, pmc.pmc_id, OCT_1) == 1
    assert charged(conn, lease) == [(OCT_1, Decimal("1000.00"), "Rent due 2026-10-01")]
    assert charged(conn, other) == []


def test_part_months_keep_the_due_date_within_the_leases_days(conn, pmc):
    # Starts after its due day: due on its first day, for its days.
    late = lease_of(conn, pmc, starts=date(2026, 10, 15), unit="Unit 2")
    due_day(conn, pmc.pmc_id, late, OCT_1, 10)
    # Starts before its due day: due on it, for its days from the start.
    early = lease_of(conn, pmc, starts=date(2026, 10, 5), unit="Unit 3")
    due_day(conn, pmc.pmc_id, early, OCT_1, 20)
    # Ends before its due day: due on its last day, for its days to then.
    ending = lease_of(conn, pmc, ends=date(2026, 10, 7), unit="Unit 4")
    due_day(conn, pmc.pmc_id, ending, OCT_1, 10)
    for day in range(1, 32):
        run(conn, pmc.pmc_id, date(2026, 10, day))
    assert charged(conn, late) == [
        (
            date(2026, 10, 15),
            Decimal("548.39"),
            "Rent due 2026-10-15, 2026-10-15 to 2026-10-31 (17 of 31 days)",
        )
    ]
    assert charged(conn, early) == [
        (
            date(2026, 10, 20),
            Decimal("870.97"),
            "Rent due 2026-10-20, 2026-10-05 to 2026-10-31 (27 of 31 days)",
        )
    ]
    assert charged(conn, ending) == [
        (
            date(2026, 10, 7),
            Decimal("225.81"),
            "Rent due 2026-10-07, 2026-10-01 to 2026-10-07 (7 of 31 days)",
        )
    ]


def test_recurring_charges_are_due_with_the_rent(conn, pmc):
    lease = lease_of(conn, pmc, ends=date(2026, 11, 20))
    due_day(conn, pmc.pmc_id, lease, OCT_1, 12)
    add_recurring_charge(conn, pmc.pmc_id, lease, "40.00", OCT_1)
    stopped = add_recurring_charge(conn, pmc.pmc_id, lease, "15.00", OCT_1, memo="Parking")
    stop_recurring_charge(conn, pmc.pmc_id, stopped, date(2026, 11, 5))
    run(conn, pmc.pmc_id, date(2026, 10, 11))
    assert charged(conn, lease, "fee") == []  # nothing before its due day
    for day in (date(2026, 10, 12), date(2026, 11, 12), date(2026, 11, 30)):
        run(conn, pmc.pmc_id, day)
    assert sorted((d, a) for d, a, _ in charged(conn, lease, "fee")) == [
        (date(2026, 10, 12), Decimal("15.00")),
        (date(2026, 10, 12), Decimal("40.00")),
        # November: the lease ends on the 20th; parking stopped on the 5th, before the 12th.
        (date(2026, 11, 12), Decimal("26.67")),
    ]
    assert [d for d, _, _ in charged(conn, lease)] == [date(2026, 10, 12), date(2026, 11, 12)]


def test_a_due_day_starts_after_the_last_month_charged_within_the_lease(conn, pmc):
    lease = lease_of(conn, pmc, ends=date(2027, 3, 31))
    run(conn, pmc.pmc_id, OCT_1)  # October is charged, due on the first
    with pytest.raises(psycopg.errors.CheckViolation, match="is charged already"):
        due_day(conn, pmc.pmc_id, lease, OCT_1, 10)
    with pytest.raises(psycopg.errors.CheckViolation, match="a month of the lease"):
        due_day(conn, pmc.pmc_id, lease, date(2027, 4, 1), 10)
    with pytest.raises(psycopg.errors.CheckViolation, match="a month of the lease"):
        due_day(conn, pmc.pmc_id, lease, date(2026, 8, 1), 10)
    for starts_on, day in ((date(2026, 11, 2), 10), (NOV_1, 29), (NOV_1, 0)):
        with pytest.raises(psycopg.errors.CheckViolation):
            due_day(conn, pmc.pmc_id, lease, starts_on, day)
    due_day(conn, pmc.pmc_id, lease, NOV_1, 10)
    with pytest.raises(psycopg.errors.UniqueViolation):
        due_day(conn, pmc.pmc_id, lease, NOV_1, 15)
    # Another company's lease takes none.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        due_day(conn, make_pmc(conn).pmc_id, lease, DEC_1, 5)
    # October stays due on the first; November on the 10th; back to the first from December.
    due_day(conn, pmc.pmc_id, lease, DEC_1, 1)
    for day in (date(2026, 11, 1), date(2026, 11, 10), date(2026, 12, 1)):
        run(conn, pmc.pmc_id, day)
    assert [d for d, _, _ in charged(conn, lease)] == [OCT_1, date(2026, 11, 10), DEC_1]


def test_due_days_are_append_only_audited_and_name_who_recorded_them(conn, pmc):
    lease = lease_of(conn, pmc)
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    made = due_day(conn, pmc.pmc_id, lease, NOV_1, 5, by=clerk)
    assert conn.execute(
        "SELECT recorded_by FROM trust_rent_due_days WHERE id = %s", (made,)
    ).fetchone() == (clerk,)
    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute("UPDATE trust_rent_due_days SET due_day = 6")
    assert conn.execute(
        "SELECT staff_id FROM trust_audit_log WHERE table_name = 'trust_rent_due_days'"
        " AND row_data ->> 'id' = %s",
        (str(made),),
    ).fetchone() == (clerk,)


def test_late_fees_count_their_grace_from_the_due_date(conn, pmc):
    lease = lease_of(conn, pmc)
    due_day(conn, pmc.pmc_id, lease, OCT_1, 10)
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, date(2026, 9, 1), 5, "50.00")
    for day in (date(2026, 10, 10), date(2026, 10, 15)):
        run(conn, pmc.pmc_id, day)
        # The night of the 10th charged the rent: say so to the database, whose clock reads today.
        rent_charged_when_due(conn, pmc.pmc_id)
    assert conn.execute(
        "SELECT count(*) FROM trust_late_fees AS f JOIN trust_charges AS r"
        " ON r.id = f.rent_charge_id WHERE r.lease_id = %s",
        (lease,),
    ).fetchone() == (0,)  # its fifth grace day isn't over
    run(conn, pmc.pmc_id, date(2026, 10, 16))
    assert conn.execute(
        "SELECT r.due_on, f.fee FROM trust_late_fees AS f JOIN trust_charges AS r"
        " ON r.id = f.rent_charge_id WHERE r.lease_id = %s",
        (lease,),
    ).fetchall() == [(date(2026, 10, 10), Decimal("50.00"))]


def test_rent_charged_nights_after_its_due_day_has_its_grace_from_that_night(conn, pmc):
    """The nightly run didn't run from the 9th to the 19th: the rent due on the 10th was charged
    on the night of the 20th, and its five days' grace runs from then (migration 0151)."""
    lease = lease_of(conn, pmc)
    due_day(conn, pmc.pmc_id, lease, OCT_1, 10)
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, date(2026, 9, 1), 5, "50.00")

    run(conn, pmc.pmc_id, date(2026, 10, 20))
    (rent,) = conn.execute(
        "SELECT id FROM trust_charges WHERE lease_id = %s AND kind = 'rent'", (lease,)
    ).fetchone()
    charged_on(conn, [rent], date(2026, 10, 20))
    run(conn, pmc.pmc_id, date(2026, 10, 25))
    assert conn.execute(
        "SELECT count(*) FROM trust_late_fees WHERE rent_charge_id = %s", (rent,)
    ).fetchone() == (0,)  # its fifth grace day isn't over
    run(conn, pmc.pmc_id, date(2026, 10, 26))
    assert conn.execute(
        "SELECT c.due_on, f.fee FROM trust_late_fees AS f JOIN trust_charges AS c"
        " ON c.id = f.fee_charge_id WHERE f.rent_charge_id = %s",
        (rent,),
    ).fetchall() == [(date(2026, 10, 26), Decimal("50.00"))]


def test_a_lease_ending_after_its_month_was_charged_is_credited_its_days(conn, pmc):
    lease = lease_of(conn, pmc)
    due_day(conn, pmc.pmc_id, lease, OCT_1, 10)
    run(conn, pmc.pmc_id, date(2026, 10, 10))
    end_lease(conn, pmc.pmc_id, lease, date(2026, 10, 20))
    assert conn.execute(
        "SELECT due_on, kind, amount FROM trust_charges WHERE lease_id = %s AND kind = 'credit'",
        (lease,),
    ).fetchall() == [(date(2026, 10, 10), "credit", Decimal("354.84"))]


def test_the_run_counts_what_it_charged_on_due_days(conn, pmc):
    lease = lease_of(conn, pmc)
    due_day(conn, pmc.pmc_id, lease, OCT_1, 3)
    add_recurring_charge(conn, pmc.pmc_id, lease, "40.00", OCT_1)
    run(conn, pmc.pmc_id, date(2026, 10, 3))
    assert conn.execute(
        "SELECT rents, recurring FROM trust_rent_runs WHERE pmc_id = %s ORDER BY created_at DESC"
        " LIMIT 1",
        (pmc.pmc_id,),
    ).fetchone() == (1, 1)


@pytest.mark.parametrize(
    "charge", ["trust_charge_rent_on_due_days", "trust_charge_recurring_on_due_days"]
)
@pytest.mark.parametrize(("month", "as_of"), [(None, OCT_1), (OCT_1, None)])
def test_a_due_day_run_needs_its_month_and_its_day(conn, pmc, charge, month, as_of):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="as of a day"):
        conn.execute(f"SELECT {charge}(%s, %s, %s)", (pmc.pmc_id, month, as_of))


def test_a_month_with_its_rent_charged_already_is_not_charged_again(conn, pmc):
    # October's rent and parking came over from the old system, due on the 3rd; the lease is due
    # on the 10th from October.
    lease = lease_of(conn, pmc)
    due_day(conn, pmc.pmc_id, lease, OCT_1, 10)
    parking = add_recurring_charge(conn, pmc.pmc_id, lease, "40.00", OCT_1)
    conn.execute(
        "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo,"
        " recurring_charge_id) VALUES (%s, %s, '2026-10-03', 'rent', 1000.00, 'Carried over',"
        " NULL), (%s, %s, '2026-10-03', 'fee', 40.00, 'Carried over', %s)",
        (pmc.pmc_id, lease, pmc.pmc_id, lease, parking),
    )
    run(conn, pmc.pmc_id, date(2026, 10, 10))
    assert charged(conn, lease) == [(date(2026, 10, 3), Decimal("1000.00"), "Carried over")]
    assert charged(conn, lease, "fee") == [(date(2026, 10, 3), Decimal("40.00"), "Carried over")]
