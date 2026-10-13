"""Recurring lease charges (migration 20261013000037).

A lease owes some charges every month besides its rent (pet rent, parking) or gets a credit
every month (a concession). trust_charge_recurring_due charges each one in force on a due date,
while its lease is in effect, once per recurring charge and date.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_late_fee_policy,
    add_recurring_charge,
    add_unit,
    apply_payment,
    assess_late_fees,
    charge,
    charge_recurring_due,
    charge_rent_due,
    end_lease,
    in_background,
    make_pmc,
    open_lease,
    stop_recurring_charge,
    transfer_batch,
    wait_until_blocked,
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


def lease_on(conn, pmc, starts_on, ends_on=None, name="Unit 1"):
    """A lease of a new unit of Property 1, at 1500.00 a month."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name)
    return open_lease(conn, pmc.pmc_id, unit, starts_on, ends_on, "1500.00", [tenant_of(conn, pmc)])


def charges(conn, lease):
    """(due date, kind, amount, memo) of the lease's charges made for a recurring charge."""
    return conn.execute(
        "SELECT due_on, kind, amount, memo FROM trust_charges WHERE lease_id = %s"
        " AND recurring_charge_id IS NOT NULL ORDER BY due_on, memo",
        (lease,),
    ).fetchall()


def by_hand(conn, pmc, lease, recurring, due_on, kind="fee", amount="35.00"):
    """A charge entered straight into trust_charges, naming a recurring charge."""
    return conn.execute(
        "INSERT INTO trust_charges"
        " (pmc_id, lease_id, due_on, kind, amount, recurring_charge_id)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (pmc.pmc_id, lease, due_on, kind, Decimal(amount), recurring),
    ).fetchone()[0]


# --- the run ------------------------------------------------------------------------------


def test_the_run_charges_each_recurring_charge_in_force(conn, app_conn, pmc):
    lease = lease_on(conn, pmc, JAN_1)
    add_recurring_charge(app_conn, pmc.pmc_id, lease, "35.00", JAN_1, "Pet rent")
    add_recurring_charge(app_conn, pmc.pmc_id, lease, "50.00", FEB_1, "Parking")

    assert charge_recurring_due(app_conn, pmc.pmc_id, FEB_1) == 2

    assert charges(conn, lease) == [
        (FEB_1, "fee", Decimal("50.00"), "Parking due 2026-02-01"),
        (FEB_1, "fee", Decimal("35.00"), "Pet rent due 2026-02-01"),
    ]


def test_a_recurring_charge_is_charged_from_its_first_day_until_it_stops(conn, pmc):
    lease = lease_on(conn, pmc, JAN_1)
    parking = add_recurring_charge(conn, pmc.pmc_id, lease, "50.00", FEB_1, "Parking")
    stop_recurring_charge(conn, pmc.pmc_id, parking, APR_1)

    charged = {day: charge_recurring_due(conn, pmc.pmc_id, day) for day in (JAN_1, FEB_1)}
    charged |= {day: charge_recurring_due(conn, pmc.pmc_id, day) for day in (MAR_1, APR_1)}

    assert charged == {JAN_1: 0, FEB_1: 1, MAR_1: 1, APR_1: 0}
    assert [row[0] for row in charges(conn, lease)] == [FEB_1, MAR_1]


def test_a_charge_stopped_the_day_after_a_due_date_is_charged_for_it(conn, pmc):
    lease = lease_on(conn, pmc, JAN_1)
    parking = add_recurring_charge(conn, pmc.pmc_id, lease, "50.00", FEB_1, "Parking")
    stop_recurring_charge(conn, pmc.pmc_id, parking, date(2026, 2, 2))

    assert charge_recurring_due(conn, pmc.pmc_id, FEB_1) == 1


def test_only_while_its_lease_is_in_effect(conn, pmc):
    ended = lease_on(conn, pmc, JAN_1, date(2026, 2, 15), name="Unit 1")
    ends_that_day = lease_on(conn, pmc, JAN_1, MAR_1, name="Unit 2")
    starts_later = lease_on(conn, pmc, date(2026, 3, 2), name="Unit 3")
    for lease in (ended, ends_that_day, starts_later):
        add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", JAN_1)

    assert charge_recurring_due(conn, pmc.pmc_id, MAR_1) == 1
    assert [bool(charges(conn, lease)) for lease in (ended, ends_that_day, starts_later)] == [
        False,
        True,
        False,
    ]


def test_running_again_charges_nothing_more(conn, pmc):
    lease = lease_on(conn, pmc, JAN_1)
    add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", JAN_1)

    assert charge_recurring_due(conn, pmc.pmc_id, FEB_1) == 1
    assert charge_recurring_due(conn, pmc.pmc_id, FEB_1) == 0
    assert len(charges(conn, lease)) == 1


def test_rent_and_other_charges_on_the_day_do_not_stop_it(conn, pmc):
    lease = lease_on(conn, pmc, JAN_1)
    add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", JAN_1)
    charge_rent_due(conn, pmc.pmc_id, FEB_1)
    charge(conn, pmc.pmc_id, lease, FEB_1, "35.00", "fee", "Pet rent due 2026-02-01")

    assert charge_recurring_due(conn, pmc.pmc_id, FEB_1) == 1
    assert charge_rent_due(conn, pmc.pmc_id, FEB_1) == 0


def test_a_recurring_credit_comes_off_what_the_lease_owes(conn, pmc):
    lease = lease_on(conn, pmc, JAN_1)
    add_recurring_charge(conn, pmc.pmc_id, lease, "100.00", JAN_1, "Concession", "credit")
    charge_rent_due(conn, pmc.pmc_id, FEB_1)

    assert charge_recurring_due(conn, pmc.pmc_id, FEB_1) == 1

    assert charges(conn, lease) == [
        (FEB_1, "credit", Decimal("100.00"), "Concession due 2026-02-01")
    ]
    assert conn.execute("SELECT trust_lease_owed(%s)", (lease,)).fetchone() == (Decimal("1400.00"),)


def test_the_run_charges_only_its_own_pmcs_recurring_charges(conn, pmc):
    other = make_pmc(conn)
    theirs = lease_on(conn, other, JAN_1)
    add_recurring_charge(conn, other.pmc_id, theirs, "35.00", JAN_1)

    assert charge_recurring_due(conn, pmc.pmc_id, FEB_1) == 0
    assert charges(conn, theirs) == []


def test_the_run_needs_a_due_date(conn, pmc):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="due date"):
        charge_recurring_due(conn, pmc.pmc_id, None)


@pytest.mark.timing
def test_two_runs_at_once_charge_each_recurring_charge_once(conn, database_url, pmc):
    lease = lease_on(conn, pmc, JAN_1)
    add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", JAN_1)

    with psycopg.connect(database_url) as first:
        assert charge_recurring_due(first, pmc.pmc_id, FEB_1) == 1  # not committed yet
        thread, pid, outcome = in_background(
            database_url, lambda worker: charge_recurring_due(worker, pmc.pmc_id, FEB_1)
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert outcome == {"result": 0}
    assert len(charges(conn, lease)) == 1


def test_its_charges_are_paid_like_any_charge(conn, pmc):
    lease = lease_on(conn, pmc, JAN_1)
    add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", JAN_1)
    charge_recurring_due(conn, pmc.pmc_id, FEB_1)
    (pet_rent,) = conn.execute(
        "SELECT id FROM trust_charges WHERE lease_id = %s AND recurring_charge_id IS NOT NULL",
        (lease,),
    ).fetchone()
    (paid,) = transfer_batch(
        conn,
        [(pmc.operating_cash, pmc.owners[0].account, "35.00")],
        datetime(2026, 2, 3, tzinfo=UTC),
    )

    assert apply_payment(conn, pmc.pmc_id, pet_rent, paid, "35.00") == Decimal("35.00")


def test_late_fees_are_charged_on_rent_only(conn, pmc):
    lease = lease_on(conn, pmc, JAN_1)
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, 5, "50")
    add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", JAN_1)
    charge_recurring_due(conn, pmc.pmc_id, FEB_1)

    assert assess_late_fees(conn, pmc.pmc_id, MAR_1) == 0


def test_an_ended_lease_keeps_what_was_charged_before_it_ended(conn, pmc):
    lease = lease_on(conn, pmc, JAN_1)
    add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", JAN_1)
    charge_recurring_due(conn, pmc.pmc_id, FEB_1)
    end_lease(conn, pmc.pmc_id, lease, date(2026, 2, 15))

    assert charge_recurring_due(conn, pmc.pmc_id, MAR_1) == 0
    assert [row[0] for row in charges(conn, lease)] == [FEB_1]


# --- the records --------------------------------------------------------------------------


def test_the_app_records_and_stops_recurring_charges(conn, app_conn, pmc):
    lease = lease_on(conn, pmc, JAN_1)

    parking = add_recurring_charge(app_conn, pmc.pmc_id, lease, "50.00", FEB_1, "Parking")
    stop_recurring_charge(app_conn, pmc.pmc_id, parking, APR_1)

    assert conn.execute(
        "SELECT r.lease_id, r.kind, r.amount, r.memo, r.starts_on, e.ends_on"
        " FROM trust_recurring_charges r JOIN trust_recurring_charge_ends e"
        " ON e.recurring_charge_id = r.id WHERE r.id = %s",
        (parking,),
    ).fetchone() == (lease, "fee", Decimal("50.00"), "Parking", FEB_1, APR_1)


@pytest.mark.parametrize(
    ("column", "value", "why"),
    [
        ("amount", "0", "trust_recurring_charges_amount_check"),
        ("amount", "-5", "trust_recurring_charges_amount_check"),
        ("amount", "1.005", "trust_recurring_charges_amount_check"),
        ("memo", "", "trust_recurring_charges_memo_check"),
        ("kind", "rent", "trust_recurring_charges_kind_check"),
    ],
)
def test_a_recurring_charge_stays_in_range(conn, pmc, column, value, why):
    lease = lease_on(conn, pmc, JAN_1)
    row = {"kind": "fee", "amount": "35.00", "memo": "Pet rent"} | {column: value}

    with pytest.raises(psycopg.errors.CheckViolation, match=why):
        add_recurring_charge(
            conn, pmc.pmc_id, lease, row["amount"], JAN_1, row["memo"], row["kind"]
        )


def test_a_recurring_charge_is_for_a_lease_of_its_pmc(conn, pmc):
    theirs = lease_on(conn, make_pmc(conn), JAN_1)

    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="trust_leases"):
        add_recurring_charge(conn, pmc.pmc_id, theirs, "35.00", JAN_1)


def test_a_recurring_charge_stops_once_and_only_in_its_pmc(conn, pmc):
    lease = lease_on(conn, pmc, JAN_1)
    parking = add_recurring_charge(conn, pmc.pmc_id, lease, "50.00", JAN_1, "Parking")
    other = make_pmc(conn)

    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="trust_recurring_charges"):
        stop_recurring_charge(conn, other.pmc_id, parking, APR_1)
    stop_recurring_charge(conn, pmc.pmc_id, parking, APR_1)
    with pytest.raises(psycopg.errors.UniqueViolation, match="trust_recurring_charge_ends_pkey"):
        stop_recurring_charge(conn, pmc.pmc_id, parking, MAR_1)


@pytest.mark.parametrize(
    ("change", "kind", "amount"),
    [
        ("another lease", "fee", "35.00"),
        ("another kind", "credit", "35.00"),
        ("rent", "rent", "35.00"),
        ("another amount", "fee", "35.01"),
    ],
)
def test_a_charge_names_a_recurring_charge_of_its_lease_kind_and_amount(
    conn, pmc, change, kind, amount
):
    lease = lease_on(conn, pmc, JAN_1, name="Unit 1")
    pet_rent = add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", JAN_1)
    if change == "another lease":
        lease = lease_on(conn, pmc, JAN_1, name="Unit 2")

    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="recurring_charge_fkey"):
        by_hand(conn, pmc, lease, pet_rent, FEB_1, kind, amount)


def test_a_charge_names_a_recurring_charge_of_its_pmc(conn, pmc):
    other = make_pmc(conn)
    theirs = lease_on(conn, other, JAN_1)
    pet_rent = add_recurring_charge(conn, other.pmc_id, theirs, "35.00", JAN_1)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        by_hand(conn, pmc, theirs, pet_rent, FEB_1)


def test_one_charge_per_recurring_charge_and_due_date(conn, pmc):
    lease = lease_on(conn, pmc, JAN_1)
    pet_rent = add_recurring_charge(conn, pmc.pmc_id, lease, "35.00", JAN_1)
    by_hand(conn, pmc, lease, pet_rent, FEB_1)

    assert charge_recurring_due(conn, pmc.pmc_id, FEB_1) == 0
    with pytest.raises(psycopg.errors.UniqueViolation, match="one_per_recurring_due_date"):
        by_hand(conn, pmc, lease, pet_rent, FEB_1)
    by_hand(conn, pmc, lease, pet_rent, MAR_1)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE trust_recurring_charges SET amount = 1",
        "DELETE FROM trust_recurring_charges",
        "UPDATE trust_recurring_charge_ends SET ends_on = '2030-01-01'",
        "DELETE FROM trust_recurring_charge_ends",
    ],
)
def test_recurring_charges_are_kept_for_good(conn, pmc, statement):
    lease = lease_on(conn, pmc, JAN_1)
    parking = add_recurring_charge(conn, pmc.pmc_id, lease, "50.00", JAN_1, "Parking")
    stop_recurring_charge(conn, pmc.pmc_id, parking, APR_1)

    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)
