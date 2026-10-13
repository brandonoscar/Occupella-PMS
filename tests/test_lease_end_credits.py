"""Credits for the days after a lease's end (migration 20261013000051).

A lease whose end is set, moved or cleared after the rent run charged a month keeps each charged
month at the days it is in effect, by actual days as the run charges them: trust_end_lease adds a
credit for the days after an earlier end, or a fee for the days back when the end moves later,
due on the charge's own day. Only charges that stand at what the run would charge (rent and a
recurring charge's) are followed; anything else is left for staff. A renewal ends the old lease
this way and charges the new one its days of the month.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_late_fee_policy,
    add_recurring_charge,
    add_staff,
    add_unit,
    apply_payment,
    assess_late_fees,
    charge,
    charge_recurring_due,
    charge_rent_due,
    complete_offer,
    end_lease,
    in_background,
    make_pmc,
    offer_lease,
    open_lease,
    send_offer,
    sign_offer,
    transfer_batch,
    wait_until_blocked,
)

JAN_1, MAR_1, APR_1 = date(2026, 1, 1), date(2026, 3, 1), date(2026, 4, 1)
MAR_10, MAR_20 = date(2026, 3, 10), date(2026, 3, 20)
RENT = Decimal("1500.00")


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]


def lease_on(conn, pmc, starts_on=JAN_1, ends_on=None, name="Unit 1"):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name)
    return open_lease(conn, pmc.pmc_id, unit, starts_on, ends_on, RENT, [tenant_of(conn, pmc)])


def adjustments(conn, lease):
    """(due date, kind, amount, memo) of the credits and fees that followed the lease's end."""
    return conn.execute(
        "SELECT a.due_on, a.kind, a.amount, a.memo FROM trust_lease_end_adjustments AS x"
        " JOIN trust_charges AS a ON a.id = x.adjustment_charge_id"
        " WHERE a.lease_id = %s ORDER BY x.created_at, a.due_on, a.memo",
        (lease,),
    ).fetchall()


def owed(conn, lease):
    return conn.execute("SELECT trust_lease_owed(%s)", (lease,)).fetchone()[0]


# --- an end set, moved or cleared after the run ----------------------------------------------


def test_an_end_set_mid_month_after_the_run_credits_the_days_after_it(conn, app_conn, pmc):
    lease = lease_on(conn, pmc)
    charge_rent_due(conn, pmc.pmc_id, MAR_1)

    end_lease(app_conn, pmc.pmc_id, lease, MAR_10)

    assert adjustments(conn, lease) == [  # 1500.00 less 1500.00 x 10 / 31 = 483.87
        (
            MAR_1,
            "credit",
            Decimal("1016.13"),
            "Rent due 2026-03-01: the days after the lease ends 2026-03-10",
        )
    ]
    assert owed(conn, lease) == Decimal("483.87")


def test_a_month_wholly_after_the_end_is_credited_whole(conn, pmc):
    lease = lease_on(conn, pmc)
    charge_rent_due(conn, pmc.pmc_id, MAR_1)
    charge_rent_due(conn, pmc.pmc_id, APR_1)

    end_lease(conn, pmc.pmc_id, lease, MAR_10)

    assert [row[:3] for row in adjustments(conn, lease)] == [
        (MAR_1, "credit", Decimal("1016.13")),
        (APR_1, "credit", RENT),
    ]
    assert owed(conn, lease) == Decimal("483.87")


def test_a_later_end_or_none_charges_the_days_back(conn, pmc):
    lease = lease_on(conn, pmc)
    charge_rent_due(conn, pmc.pmc_id, MAR_1)

    end_lease(conn, pmc.pmc_id, lease, MAR_10)
    end_lease(conn, pmc.pmc_id, lease, MAR_20)
    end_lease(conn, pmc.pmc_id, lease, None)

    assert adjustments(conn, lease) == [
        (
            MAR_1,
            "credit",
            Decimal("1016.13"),
            "Rent due 2026-03-01: the days after the lease ends 2026-03-10",
        ),
        (  # 1500.00 x 20 / 31 = 967.74, less 483.87
            MAR_1,
            "fee",
            Decimal("483.87"),
            "Rent due 2026-03-01: the days back, the lease now ends 2026-03-20",
        ),
        (
            MAR_1,
            "fee",
            Decimal("532.26"),
            "Rent due 2026-03-01: the days back, the lease no longer ends",
        ),
    ]
    assert owed(conn, lease) == RENT


def test_an_earlier_end_again_credits_more(conn, pmc):
    lease = lease_on(conn, pmc)
    charge_rent_due(conn, pmc.pmc_id, MAR_1)

    end_lease(conn, pmc.pmc_id, lease, MAR_20)
    end_lease(conn, pmc.pmc_id, lease, MAR_10)

    assert [row[:3] for row in adjustments(conn, lease)] == [
        (MAR_1, "credit", Decimal("532.26")),
        (MAR_1, "credit", Decimal("483.87")),
    ]
    assert owed(conn, lease) == Decimal("483.87")


def test_the_same_end_again_changes_nothing(conn, pmc):
    lease = lease_on(conn, pmc)
    charge_rent_due(conn, pmc.pmc_id, MAR_1)

    end_lease(conn, pmc.pmc_id, lease, MAR_10)
    end_lease(conn, pmc.pmc_id, lease, MAR_10)

    assert len(adjustments(conn, lease)) == 1


def test_recurring_fees_and_credits_follow_the_end(conn, pmc):
    lease = lease_on(conn, pmc)
    add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", JAN_1, "Pet rent")
    add_recurring_charge(conn, pmc.pmc_id, lease, "100.00", JAN_1, "Concession", "credit")
    charge_recurring_due(conn, pmc.pmc_id, MAR_1)

    end_lease(conn, pmc.pmc_id, lease, MAR_10)

    assert adjustments(conn, lease) == [
        (  # the concession was 100.00, now 100.00 x 10 / 31 = 32.26: 67.74 back
            MAR_1,
            "fee",
            Decimal("67.74"),
            "Concession due 2026-03-01: the days after the lease ends 2026-03-10",
        ),
        (  # 35.00 less 35.00 x 10 / 31 = 11.29
            MAR_1,
            "credit",
            Decimal("23.71"),
            "Pet rent due 2026-03-01: the days after the lease ends 2026-03-10",
        ),
    ]


def test_an_end_set_before_the_run_needs_no_credit(conn, pmc):
    lease = lease_on(conn, pmc)
    end_lease(conn, pmc.pmc_id, lease, MAR_10)

    charge_rent_due(conn, pmc.pmc_id, MAR_1)

    assert adjustments(conn, lease) == []
    assert owed(conn, lease) == Decimal("483.87")


def test_a_charge_at_another_amount_is_left_for_staff(conn, pmc):
    """Rent entered by hand (or carried over) at something the run would not charge."""
    lease = lease_on(conn, pmc)
    charge(conn, pmc.pmc_id, lease, MAR_1, "1400.00")
    charge(conn, pmc.pmc_id, lease, MAR_1, "50.00", "fee")  # a one-off fee is never followed

    end_lease(conn, pmc.pmc_id, lease, MAR_10)

    assert adjustments(conn, lease) == []


def test_a_one_off_fee_or_credit_is_never_followed_even_at_the_rents_amount(conn, pmc):
    lease = lease_on(conn, pmc)
    charge(conn, pmc.pmc_id, lease, MAR_1, "1500.00", "fee")
    charge(conn, pmc.pmc_id, lease, MAR_1, "1500.00", "credit")

    end_lease(conn, pmc.pmc_id, lease, MAR_10)

    assert adjustments(conn, lease) == []


def test_only_the_leases_own_charges_follow_its_end(conn, pmc):
    lease, other = lease_on(conn, pmc, name="Unit 1"), lease_on(conn, pmc, name="Unit 2")
    charge_rent_due(conn, pmc.pmc_id, MAR_1)

    end_lease(conn, pmc.pmc_id, lease, MAR_10)

    assert adjustments(conn, other) == []
    assert owed(conn, other) == RENT


def test_the_credit_comes_off_the_rent_so_it_is_not_late(conn, pmc):
    lease = lease_on(conn, pmc)
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, 5, "50")
    charge_rent_due(conn, pmc.pmc_id, MAR_1)
    end_lease(conn, pmc.pmc_id, lease, MAR_10)
    rent = conn.execute(
        "SELECT id FROM trust_charges WHERE lease_id = %s AND kind = 'rent'", (lease,)
    ).fetchone()[0]
    (paid,) = transfer_batch(
        conn,
        [(pmc.operating_cash, pmc.owners[0].account, "483.87")],
        datetime(2026, 3, 2, tzinfo=UTC),
    )
    apply_payment(conn, pmc.pmc_id, rent, paid, "483.87")

    assert assess_late_fees(conn, pmc.pmc_id, APR_1) == 0


# --- renewals ---------------------------------------------------------------------------------


def renew(conn, pmc, lease, starts_on, rent="1600.00"):
    staff = add_staff(conn, pmc.pmc_id, "Leasing 1")
    unit = conn.execute("SELECT unit_id FROM trust_leases WHERE id = %s", (lease,)).fetchone()[0]
    offer = offer_lease(conn, pmc.pmc_id, unit, staff, starts_on, rent=rent, renews_lease_id=lease)
    send_offer(conn, pmc.pmc_id, offer, staff)
    sign_offer(conn, pmc.pmc_id, offer, 1)
    return complete_offer(conn, pmc.pmc_id, offer, staff)


def rents(conn, lease):
    return conn.execute(
        "SELECT due_on, amount, memo FROM trust_charges WHERE lease_id = %s AND kind = 'rent'",
        (lease,),
    ).fetchall()


def test_a_renewal_mid_month_after_the_run_keeps_the_month_whole(conn, pmc):
    lease = lease_on(conn, pmc)
    charge_rent_due(conn, pmc.pmc_id, MAR_1)

    renewed = renew(conn, pmc, lease, date(2026, 3, 15))

    # The old lease: 14 days at 1500.00 (677.42); the new one: 17 at 1600.00 (877.42).
    assert [row[:3] for row in adjustments(conn, lease)] == [(MAR_1, "credit", Decimal("822.58"))]
    assert owed(conn, lease) == Decimal("677.42")
    assert rents(conn, renewed) == [
        (
            date(2026, 3, 15),
            Decimal("877.42"),
            "Rent due 2026-03-15, 2026-03-15 to 2026-03-31 (17 of 31 days)",
        )
    ]
    assert charge_rent_due(conn, pmc.pmc_id, MAR_1) == 0  # the month is charged already


def test_a_renewal_in_a_month_not_yet_charged_waits_for_the_run(conn, pmc):
    lease = lease_on(conn, pmc)

    renewed = renew(conn, pmc, lease, date(2026, 3, 15))

    assert adjustments(conn, lease) == []
    assert rents(conn, renewed) == []
    assert charge_rent_due(conn, pmc.pmc_id, MAR_1) == 2  # the old lease's 14 days, the new 17


# --- the records ------------------------------------------------------------------------------


def test_each_adjustment_names_the_charge_it_adjusts_and_the_end_it_follows(conn, pmc):
    lease = lease_on(conn, pmc)
    charge_rent_due(conn, pmc.pmc_id, MAR_1)
    end_lease(conn, pmc.pmc_id, lease, MAR_10)
    end_lease(conn, pmc.pmc_id, lease, None)

    assert conn.execute(
        "SELECT c.kind, x.ends_on FROM trust_lease_end_adjustments AS x"
        " JOIN trust_charges AS c ON c.id = x.charge_id WHERE c.lease_id = %s"
        " ORDER BY x.created_at",
        (lease,),
    ).fetchall() == [("rent", MAR_10), ("rent", None)]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE trust_lease_end_adjustments SET ends_on = NULL",
        "DELETE FROM trust_lease_end_adjustments",
    ],
)
def test_adjustments_are_kept_for_good(conn, pmc, statement):
    lease = lease_on(conn, pmc)
    charge_rent_due(conn, pmc.pmc_id, MAR_1)
    end_lease(conn, pmc.pmc_id, lease, MAR_10)

    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)


def test_the_app_and_the_ai_only_read_adjustments(conn, app_conn, ai_conn, pmc):
    lease = lease_on(conn, pmc)
    charge_rent_due(conn, pmc.pmc_id, MAR_1)
    end_lease(conn, pmc.pmc_id, lease, MAR_10)
    row = conn.execute(
        "SELECT adjustment_charge_id, charge_id FROM trust_lease_end_adjustments WHERE pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchone()

    for role in (app_conn, ai_conn):
        assert role.execute(
            "SELECT count(*) FROM trust_lease_end_adjustments WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone() == (1,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            role.execute(
                "INSERT INTO trust_lease_end_adjustments (adjustment_charge_id, pmc_id, charge_id)"
                " VALUES (%s, %s, %s)",
                (row[0], pmc.pmc_id, row[1]),
            )


# --- a run and an end at once -----------------------------------------------------------------


@pytest.mark.timing
def test_an_end_waits_for_a_run_under_way_and_credits_what_it_charged(conn, database_url, pmc):
    lease = lease_on(conn, pmc)

    with psycopg.connect(database_url) as run:
        assert charge_rent_due(run, pmc.pmc_id, MAR_1) == 1  # not committed yet
        thread, pid, outcome = in_background(
            database_url, lambda worker: end_lease(worker, pmc.pmc_id, lease, MAR_10)
        )
        wait_until_blocked(conn, pid)
        run.commit()
    thread.join(timeout=30)

    assert "error" not in outcome
    assert [row[:3] for row in adjustments(conn, lease)] == [(MAR_1, "credit", Decimal("1016.13"))]


@pytest.mark.timing
def test_a_run_waits_for_an_end_under_way_and_charges_by_it(conn, database_url, pmc):
    lease = lease_on(conn, pmc)

    with psycopg.connect(database_url) as ending:
        end_lease(ending, pmc.pmc_id, lease, MAR_10)  # not committed yet
        thread, pid, outcome = in_background(
            database_url, lambda worker: charge_rent_due(worker, pmc.pmc_id, MAR_1)
        )
        wait_until_blocked(conn, pid)
        ending.commit()
    thread.join(timeout=30)

    assert outcome == {"result": 1}
    assert adjustments(conn, lease) == []
    assert owed(conn, lease) == Decimal("483.87")


@pytest.mark.timing
def test_the_recurring_run_waits_for_an_end_under_way_and_charges_by_it(conn, database_url, pmc):
    lease = lease_on(conn, pmc)
    add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", JAN_1, "Pet rent")

    with psycopg.connect(database_url) as ending:
        end_lease(ending, pmc.pmc_id, lease, MAR_10)  # not committed yet
        thread, pid, outcome = in_background(
            database_url, lambda worker: charge_recurring_due(worker, pmc.pmc_id, MAR_1)
        )
        wait_until_blocked(conn, pid)
        ending.commit()
    thread.join(timeout=30)

    assert outcome == {"result": 1}
    assert adjustments(conn, lease) == []
    assert owed(conn, lease) == Decimal("11.29")  # 35.00 x 10 / 31
