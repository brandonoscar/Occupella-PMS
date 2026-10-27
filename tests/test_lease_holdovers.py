"""A fixed-term lease that runs out with no renewal signed goes month to month (ADR 0010 phase 1c,
part E; migration 0154). trust_roll_over_leases rolls each lease whose term ran out in the week
before the day, once: its end is cleared through trust_end_lease at the same rent, and the days
of its last month credited when its term was set to end are charged back. A lease never rolls
over when staff gave its end (a move-out, a notice), when another lease follows it at the unit,
when its tenants moved to another unit, when its deposit was returned, or when it was entered
after its end date had passed."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_tenant,
    add_unit,
    charge_rent_due,
    end_lease,
    make_pmc,
    open_lease,
)

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def lease(conn, pmc, ends_on, starts_on=None, unit=None, rent="1000.00"):
    """A lease of a tenant of its own (a tenant on two leases keeps one deposit with the other)."""
    place = pmc.owners[0].property_id
    unit = unit or add_unit(conn, pmc.pmc_id, place)
    tenant = add_tenant(conn, pmc.pmc_id, place, f"Tenant of {unit}")
    starts_on = starts_on or ends_on - 120 * DAY
    return open_lease(conn, pmc.pmc_id, unit, starts_on, ends_on, rent, [tenant])


def roll(conn, pmc, day):
    return conn.execute("SELECT trust_roll_over_leases(%s, %s)", (pmc.pmc_id, day)).fetchone()[0]


def ends(conn, lease_id):
    return conn.execute("SELECT ends_on FROM trust_leases WHERE id = %s", (lease_id,)).fetchone()[0]


def test_a_term_that_ran_out_goes_month_to_month_once(conn, pmc):
    term_end = TODAY + 5 * DAY
    lease_id = lease(conn, pmc, term_end)

    assert roll(conn, pmc, term_end) == 0  # its last day: not yet
    assert roll(conn, pmc, term_end + DAY) == 1
    assert ends(conn, lease_id) is None
    assert conn.execute(
        "SELECT term_ended_on, rolled_on FROM trust_lease_holdovers WHERE lease_id = %s",
        (lease_id,),
    ).fetchone() == (term_end, term_end + DAY)
    # Once: the next night finds it month to month already.
    assert roll(conn, pmc, term_end + 2 * DAY) == 0


def test_a_missed_night_still_rolls_it_within_a_week_but_no_later(conn, pmc):
    first = lease(conn, pmc, TODAY + 3 * DAY)
    second = lease(conn, pmc, TODAY + 2 * DAY)

    assert roll(conn, pmc, TODAY + 10 * DAY) == 1  # three's week holds, two's ran out a day ago
    assert ends(conn, first) is None
    assert ends(conn, second) == TODAY + 2 * DAY


def test_the_days_of_its_last_month_come_back_at_the_same_rent(conn, pmc):
    # A term ending mid-month: its month was charged for its days; going month to month charges
    # the rest of the month at the same rent.
    month = (TODAY + 40 * DAY).replace(day=1)
    term_end = month + 14 * DAY
    lease_id = lease(conn, pmc, term_end, starts_on=month - 90 * DAY)
    charge_rent_due(conn, pmc.pmc_id, month)
    charged = conn.execute(
        "SELECT sum(CASE WHEN kind = 'credit' THEN -amount ELSE amount END) FROM trust_charges"
        " WHERE lease_id = %s AND due_on >= %s",
        (lease_id, month),
    )

    assert charged.fetchone()[0] < Decimal("1000.00")
    roll(conn, pmc, term_end + DAY)
    assert conn.execute(
        "SELECT sum(CASE WHEN kind = 'credit' THEN -amount ELSE amount END) FROM trust_charges"
        " WHERE lease_id = %s AND due_on >= %s",
        (lease_id, month),
    ).fetchone()[0] == Decimal("1000.00")
    assert conn.execute(
        "SELECT monthly_rent FROM trust_leases WHERE id = %s", (lease_id,)
    ).fetchone()[0] == Decimal("1000.00")


def test_an_end_staff_gave_never_rolls_over(conn, pmc):
    lease_id = lease(conn, pmc, TODAY + 60 * DAY)
    # A move-out ten days from now, recorded by staff.
    end_lease(conn, pmc.pmc_id, lease_id, TODAY + 10 * DAY)
    conn.execute(
        "INSERT INTO trust_lease_end_notices (pmc_id, lease_id, ends_on) VALUES (%s, %s, %s)",
        (pmc.pmc_id, lease_id, TODAY + 10 * DAY),
    )

    assert roll(conn, pmc, TODAY + 11 * DAY) == 0
    assert ends(conn, lease_id) == TODAY + 10 * DAY


def test_a_lease_that_another_follows_its_tenants_left_or_whose_deposit_came_back_stays_ended(
    conn, pmc
):
    term_end = TODAY + 5 * DAY
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 7")
    followed = lease(conn, pmc, term_end, unit=unit)
    lease(conn, pmc, term_end + 400 * DAY, starts_on=term_end + 10 * DAY, unit=unit)
    returned = lease(conn, pmc, term_end)
    # Its deposit settled at move-out (as trust_return_deposit records it).
    conn.execute(
        "INSERT INTO trust_deposit_returns (lease_id, pmc_id, request_key, returned_at, held, kept,"
        " returned) VALUES (%s, %s, 'dep-1', now(), 500, 0, 500)",
        (returned, pmc.pmc_id),
    )
    # Its tenants moved to another unit.
    moved, to = lease(conn, pmc, term_end), lease(conn, pmc, term_end + 400 * DAY)
    conn.execute(
        "INSERT INTO trust_lease_transfers (from_lease_id, pmc_id, to_lease_id, moved_on,"
        " owner_account) VALUES (%s, %s, %s, %s, %s)",
        (moved, pmc.pmc_id, to, term_end, pmc.owners[0].account),
    )

    assert roll(conn, pmc, term_end + DAY) == 0
    assert [ends(conn, each) for each in (followed, returned, moved)] == [term_end] * 3


def test_a_lease_entered_after_its_end_had_passed_stays_as_it_was(conn, pmc):
    lease_id = lease(conn, pmc, TODAY - 2 * DAY)
    assert roll(conn, pmc, TODAY) == 0
    assert ends(conn, lease_id) == TODAY - 2 * DAY


def test_holdovers_and_end_notices_are_append_only(conn, pmc):
    for statement in (
        "UPDATE trust_lease_holdovers SET rolled_on = rolled_on",
        "DELETE FROM trust_lease_end_notices",
        "TRUNCATE trust_payment_notes",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(statement)


def test_a_day_is_needed(conn, pmc):
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        roll(conn, pmc, None)
