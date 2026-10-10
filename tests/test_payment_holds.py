"""Payment holds: once an eviction is filed, payments dated on or after the hold's first day are
not matched to the lease's charges, unless the PMC allows one (migration 20261010000025).
"""

from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from helpers import (
    add_tenant,
    add_unit,
    allow_payment,
    apply_payment,
    charge,
    hold_payments,
    in_background,
    make_pmc,
    open_lease,
    release_hold,
    transfer_batch,
    wait_until_blocked,
)

MAR_1, APR_1 = date(2026, 3, 1), date(2026, 4, 1)
TICK = timedelta(microseconds=1)
HELD = "is on a payment hold"


def midnight(day):
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def lease(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    return open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "1500.00", [tenant])


def rent_paid_at(conn, pmc, lease, when, amount="1500.00", due=date(2026, 2, 1)):
    """A rent charge and a payment for it dated `when`: (charge, transfer)."""
    rent = charge(conn, pmc.pmc_id, lease, due, amount)
    (paid,) = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, amount)], when)
    return rent, paid


def test_a_payment_dated_from_the_holds_first_day_is_refused(conn, app_conn, pmc, lease):
    hold_payments(app_conn, pmc.pmc_id, lease, MAR_1)
    rent, paid = rent_paid_at(conn, pmc, lease, midnight(MAR_1))

    with pytest.raises(psycopg.errors.CheckViolation, match=HELD) as refused:
        apply_payment(app_conn, pmc.pmc_id, rent, paid, "1500.00")

    assert "from 2026-03-01 (Eviction filed)" in str(refused.value)
    assert not conn.execute(
        "SELECT 1 FROM trust_charge_payments WHERE charge_id = %s", (rent,)
    ).fetchall()


def test_a_payment_dated_before_the_hold_is_matched(conn, app_conn, pmc, lease):
    hold_payments(app_conn, pmc.pmc_id, lease, MAR_1)
    rent, paid = rent_paid_at(conn, pmc, lease, midnight(MAR_1) - TICK)

    assert apply_payment(app_conn, pmc.pmc_id, rent, paid, "1500.00") == 1500


def test_a_match_made_before_the_hold_stands_and_retries(conn, app_conn, pmc, lease):
    rent, paid = rent_paid_at(conn, pmc, lease, midnight(MAR_1) + timedelta(days=3))
    apply_payment(app_conn, pmc.pmc_id, rent, paid, "1000.00")
    hold_payments(app_conn, pmc.pmc_id, lease, MAR_1)

    assert apply_payment(app_conn, pmc.pmc_id, rent, paid, "1000.00") == 1000  # a retry
    with pytest.raises(psycopg.errors.CheckViolation, match=HELD):
        apply_payment(  # a new match of the same transfer to another charge
            app_conn,
            pmc.pmc_id,
            charge(conn, pmc.pmc_id, lease, date(2026, 3, 1), "500.00"),
            paid,
            "500.00",
        )


def test_a_release_ends_the_hold_from_its_day(conn, app_conn, pmc, lease):
    hold = hold_payments(app_conn, pmc.pmc_id, lease, MAR_1)
    release_hold(app_conn, pmc.pmc_id, hold, APR_1)
    late, late_paid = rent_paid_at(conn, pmc, lease, midnight(APR_1) - TICK)
    after, after_paid = rent_paid_at(conn, pmc, lease, midnight(APR_1), due=date(2026, 4, 1))

    with pytest.raises(psycopg.errors.CheckViolation, match=HELD):
        apply_payment(app_conn, pmc.pmc_id, late, late_paid, "1500.00")
    assert apply_payment(app_conn, pmc.pmc_id, after, after_paid, "1500.00") == 1500


@pytest.mark.parametrize("ends_on", [MAR_1, MAR_1 - timedelta(days=1)])
def test_a_release_must_end_the_hold_after_it_starts(conn, app_conn, pmc, lease, ends_on):
    hold = hold_payments(app_conn, pmc.pmc_id, lease, MAR_1)

    with pytest.raises(psycopg.errors.CheckViolation, match="its release must end it later"):
        release_hold(app_conn, pmc.pmc_id, hold, ends_on)


def test_each_release_is_checked_against_its_own_hold(conn, app_conn, pmc, lease):
    later = hold_payments(app_conn, pmc.pmc_id, lease, date(2030, 6, 1), "Later filing")
    earlier = hold_payments(app_conn, pmc.pmc_id, lease, date(2026, 1, 1), "Earlier filing")

    # Ends after the earlier hold starts, but before its own: refused.
    with pytest.raises(psycopg.errors.CheckViolation, match="its release must end it later"):
        release_hold(app_conn, pmc.pmc_id, later, date(2030, 1, 1))
    release_hold(app_conn, pmc.pmc_id, earlier, date(2026, 2, 1))


def test_a_hold_is_released_once(conn, app_conn, pmc, lease):
    hold = hold_payments(app_conn, pmc.pmc_id, lease, MAR_1)
    release_hold(app_conn, pmc.pmc_id, hold, APR_1)

    with pytest.raises(psycopg.errors.UniqueViolation):
        release_hold(app_conn, pmc.pmc_id, hold, date(2026, 5, 1))


def test_an_allowed_payment_is_matched_and_only_that_one(conn, app_conn, pmc, lease):
    hold = hold_payments(app_conn, pmc.pmc_id, lease, MAR_1)
    rent, allowed = rent_paid_at(conn, pmc, lease, midnight(MAR_1) + timedelta(days=2))
    other, refused = rent_paid_at(conn, pmc, lease, midnight(MAR_1) + timedelta(days=3), due=MAR_1)
    allow_payment(app_conn, pmc.pmc_id, hold, allowed, "Manager 1")

    assert apply_payment(app_conn, pmc.pmc_id, rent, allowed, "1500.00") == 1500
    with pytest.raises(psycopg.errors.CheckViolation, match=HELD):
        apply_payment(app_conn, pmc.pmc_id, other, refused, "1500.00")


def test_every_hold_in_force_must_allow_the_payment(conn, app_conn, pmc, lease):
    first = hold_payments(app_conn, pmc.pmc_id, lease, MAR_1)
    hold_payments(app_conn, pmc.pmc_id, lease, MAR_1 + timedelta(days=1), "Second filing")
    rent, paid = rent_paid_at(conn, pmc, lease, midnight(MAR_1) + timedelta(days=2))
    allow_payment(app_conn, pmc.pmc_id, first, paid)

    with pytest.raises(psycopg.errors.CheckViolation, match="Second filing"):
        apply_payment(app_conn, pmc.pmc_id, rent, paid, "1500.00")


def test_a_hold_on_another_lease_changes_nothing_here(conn, app_conn, pmc, lease):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2")
    tenant = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 2")
    other = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "900.00", [tenant])
    hold_payments(app_conn, pmc.pmc_id, other, MAR_1)
    rent, paid = rent_paid_at(conn, pmc, lease, midnight(MAR_1) + timedelta(days=2))

    assert apply_payment(app_conn, pmc.pmc_id, rent, paid, "1500.00") == 1500


def test_an_allowance_names_a_transfer_of_the_pmc(conn, app_conn, pmc, lease):
    hold = hold_payments(app_conn, pmc.pmc_id, lease, MAR_1)
    other = make_pmc(conn)
    (elsewhere,) = transfer_batch(conn, [(other.operating_cash, other.owners[0].account, "1.00")])

    with pytest.raises(psycopg.errors.CheckViolation, match="is not in PMC"):
        allow_payment(app_conn, pmc.pmc_id, hold, elsewhere)


def test_a_hold_is_on_a_lease_of_its_pmc(conn, app_conn, pmc, lease):
    other = make_pmc(conn)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        hold_payments(app_conn, other.pmc_id, lease, MAR_1)


@pytest.mark.parametrize(
    "table",
    ["trust_payment_holds", "trust_payment_hold_releases", "trust_payment_hold_allowances"],
)
@pytest.mark.parametrize("statement", ["UPDATE {t} SET pmc_id = pmc_id", "DELETE FROM {t}"])
def test_holds_releases_and_allowances_are_records(conn, app_conn, pmc, lease, table, statement):
    hold = hold_payments(app_conn, pmc.pmc_id, lease, MAR_1)
    release_hold(app_conn, pmc.pmc_id, hold, APR_1)
    (paid,) = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "1.00")])
    allow_payment(app_conn, pmc.pmc_id, hold, paid)

    with pytest.raises(psycopg.errors.Error, match="append-only|history"):
        conn.execute(statement.format(t=table))


def test_a_bounced_payment_is_still_reversed_on_a_held_lease(conn, app_conn, pmc, lease):
    rent, paid = rent_paid_at(conn, pmc, lease, midnight(date(2026, 2, 3)))
    apply_payment(app_conn, pmc.pmc_id, rent, paid, "1500.00")
    hold_payments(app_conn, pmc.pmc_id, lease, MAR_1)
    (back,) = transfer_batch(
        conn, [(pmc.owners[0].account, pmc.operating_cash, "1500.00")], midnight(MAR_1)
    )

    reversed_amount = app_conn.execute(
        "SELECT trust_reverse_payment(%s, %s, %s, %s, 1500)", (pmc.pmc_id, rent, paid, back)
    ).fetchone()[0]
    assert reversed_amount == 1500


@pytest.mark.timing
def test_a_match_waits_for_a_hold_being_placed_and_is_then_refused(conn, database_url, pmc, lease):
    rent, paid = rent_paid_at(conn, pmc, lease, midnight(MAR_1) + timedelta(days=1))

    with psycopg.connect(database_url) as placing:
        hold_payments(placing, pmc.pmc_id, lease, MAR_1)  # not committed yet
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: apply_payment(worker, pmc.pmc_id, rent, paid, "1500.00"),
        )
        wait_until_blocked(conn, pid)
        placing.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert HELD in str(outcome["error"])


@pytest.mark.timing
def test_a_hold_waits_for_a_match_in_flight_which_stands(conn, database_url, pmc, lease):
    rent, paid = rent_paid_at(conn, pmc, lease, midnight(MAR_1) + timedelta(days=1))

    with psycopg.connect(database_url) as matching:
        apply_payment(matching, pmc.pmc_id, rent, paid, "1500.00")  # not committed yet
        thread, pid, outcome = in_background(
            database_url, lambda worker: hold_payments(worker, pmc.pmc_id, lease, MAR_1)
        )
        wait_until_blocked(conn, pid)
        matching.commit()
    thread.join(timeout=30)

    assert "error" not in outcome, outcome
    assert conn.execute(
        "SELECT amount FROM trust_charge_payments WHERE charge_id = %s", (rent,)
    ).fetchone() == (1500,)
