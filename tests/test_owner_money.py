"""The owner money loop: management agreements, management and leasing fees, and owner draws
(migration 20261010000016).

Fees move money from an owner's property account to the PMC's fee account; draws pay it out
through the trust bank account's cash. Each is safe to retry, and the no-negative rule holds.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_agreement,
    add_unit,
    apply_payment,
    approve,
    balance,
    charge,
    draw_owner,
    in_background,
    make_pmc,
    open_account,
    open_lease,
    post_leasing_fee,
    post_management_fee,
    reverse_payment,
    transfer_batch,
    wait_until_blocked,
)

JAN_1, FEB_1, MAR_1 = date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)
JAN_31 = datetime(2026, 1, 31, 23, 0, tzinfo=UTC)
RENT = Decimal("1500.00")


def at(day, hour=12, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def owner(pmc):
    """Owner 1's account for Property 1."""
    return pmc.owners[0].account


@pytest.fixture
def lease(conn, pmc):
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, JAN_1, None, RENT, [tenant])


def collect(conn, pmc, lease, amount, when, kind="rent", due=JAN_1):
    """A charge on the lease, paid into Owner 1's account at `when` and matched to it: the
    payment's transfer id."""
    charged = charge(conn, pmc.pmc_id, lease, due, amount, kind)
    (paid,) = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, amount)], when)
    apply_payment(conn, pmc.pmc_id, charged, paid, amount)
    return charged, paid


def fee_rows(conn, account):
    return conn.execute(
        "SELECT period_start, period_end, collected, fee, transfer_id IS NOT NULL"
        " FROM trust_management_fees WHERE ledger_account_id = %s ORDER BY period_start",
        (account,),
    ).fetchall()


def transfers_between(conn, source, target):
    return conn.execute(
        "SELECT amount, metadata ->> 'memo' FROM pgledger_transfers"
        " WHERE from_account_id = %s AND to_account_id = %s ORDER BY event_at, id",
        (source, target),
    ).fetchall()


# --- management agreements ---------------------------------------------------------------


def test_the_app_records_an_agreement_for_an_owners_property(conn, app_conn, pmc, owner):
    agreement = add_agreement(app_conn, pmc.pmc_id, owner, JAN_1, "8", "50", "10", "50", "200")

    assert conn.execute(
        "SELECT fee_percent, minimum_fee, flat_fee, leasing_fee_percent, reserve"
        " FROM trust_management_agreements WHERE id = %s",
        (agreement,),
    ).fetchone() == (8, 50, 10, 50, 200)


@pytest.mark.parametrize("which", ["the PMC's fee account", "a deposit", "another PMC's owner"])
def test_an_agreement_is_only_for_an_owners_property_account_of_its_pmc(conn, pmc, which):
    other = make_pmc(conn)
    account = {
        "the PMC's fee account": pmc.pmc_income,
        "a deposit": pmc.tenant_deposit,
        "another PMC's owner": other.owners[0].account,
    }[which]

    with pytest.raises(psycopg.errors.CheckViolation, match="owner's property account"):
        add_agreement(conn, pmc.pmc_id, account, JAN_1)


@pytest.mark.parametrize(
    "terms",
    [
        {"fee_percent": "100.01"},
        {"fee_percent": "-1"},
        {"minimum_fee": "-0.01"},
        {"flat_fee": "-1"},
        {"leasing_fee_percent": "101"},
        {"reserve": "-1"},
    ],
)
def test_agreement_terms_stay_in_range(conn, pmc, owner, terms):
    with pytest.raises(psycopg.errors.CheckViolation):
        add_agreement(conn, pmc.pmc_id, owner, JAN_1, **terms)


def test_new_terms_are_a_new_agreement_and_agreements_are_kept_for_good(conn, pmc, owner):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, "8")
    add_agreement(conn, pmc.pmc_id, owner, FEB_1, "10")

    with pytest.raises(psycopg.errors.UniqueViolation):
        add_agreement(conn, pmc.pmc_id, owner, FEB_1, "12")
    for statement in (
        "UPDATE trust_management_agreements SET fee_percent = 1",
        "DELETE FROM trust_management_agreements",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(statement)


# --- management fees -----------------------------------------------------------------------


def test_the_fee_is_a_percent_of_rent_collected_in_the_period(conn, app_conn, pmc, owner, lease):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, fee_percent="8", minimum_fee="50", flat_fee="10")
    collect(conn, pmc, lease, RENT, at(JAN_1, 0, 0))  # the period's first moment
    collect(conn, pmc, lease, "75.00", at(date(2026, 1, 9)), kind="fee")  # not rent
    collect(conn, pmc, lease, "100.00", at(FEB_1, 0, 0), due=FEB_1)  # the next period's
    december = datetime(2025, 12, 31, 23, 59, tzinfo=UTC)
    collect(conn, pmc, lease, "200.00", december, due=date(2025, 12, 1))  # December's

    fee = post_management_fee(app_conn, pmc.pmc_id, owner, JAN_1, FEB_1, JAN_31)

    assert fee == Decimal("130.00")  # 8% of 1500.00 = 120.00, over the 50.00 minimum, + 10.00
    assert fee_rows(conn, owner) == [(JAN_1, FEB_1, RENT, Decimal("130.00"), True)]
    assert transfers_between(conn, owner, pmc.pmc_income) == [
        (Decimal("130.00"), "Management fee 2026-01-01 to 2026-01-31")
    ]


def test_the_minimum_applies_when_little_rent_came_in(conn, pmc, owner, lease):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, fee_percent="8", minimum_fee="50", flat_fee="10")
    collect(conn, pmc, lease, "100.00", at(date(2026, 1, 5)))

    assert post_management_fee(conn, pmc.pmc_id, owner, JAN_1, FEB_1, JAN_31) == Decimal("60.00")


def test_a_bounce_in_the_period_counts_against_it(conn, pmc, owner, lease):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, fee_percent="10")
    december = datetime(2025, 12, 28, tzinfo=UTC)
    transfer_batch(conn, [(pmc.operating_cash, owner, "5000.00")], december)  # owner's float
    rent, check = collect(conn, pmc, lease, RENT, december)
    (returned,) = transfer_batch(conn, [(owner, pmc.operating_cash, RENT)], at(date(2026, 1, 4)))
    reverse_payment(conn, pmc.pmc_id, rent, check, returned, RENT)
    collect(conn, pmc, lease, "1000.00", at(date(2026, 1, 10)), due=date(2026, 1, 2))

    post_management_fee(conn, pmc.pmc_id, owner, JAN_1, FEB_1, JAN_31)

    # 1000.00 collected in January less December's 1500.00 bounced back in it: no fee, not a
    # negative one.
    assert fee_rows(conn, owner) == [(JAN_1, FEB_1, Decimal("-500.00"), Decimal("0.00"), False)]


def test_a_bounce_at_midnight_utc_counts_in_the_period_that_starts_then(conn, pmc, owner, lease):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, fee_percent="10")
    january = collect(conn, pmc, lease, RENT, at(date(2026, 1, 3)))
    february = collect(conn, pmc, lease, RENT, at(date(2026, 2, 3)), due=FEB_1)
    for (rent, check), amount, when in [
        (january, "500.00", at(FEB_1, 0, 0)),  # February's first moment
        (february, "300.00", at(MAR_1, 0, 0)),  # March's first moment
    ]:
        (returned,) = transfer_batch(conn, [(owner, pmc.operating_cash, amount)], when)
        reverse_payment(conn, pmc.pmc_id, rent, check, returned, amount)

    # February: 1500.00 collected less the 500.00 that bounced at its first moment. The 300.00
    # that bounced at March's first moment is March's.
    assert post_management_fee(conn, pmc.pmc_id, owner, FEB_1, MAR_1, at(MAR_1)) == 100


def test_taking_the_fee_again_returns_it_and_posts_once(conn, pmc, owner, lease):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, fee_percent="8")
    collect(conn, pmc, lease, RENT, at(date(2026, 1, 3)))
    post_management_fee(conn, pmc.pmc_id, owner, JAN_1, FEB_1, JAN_31)

    assert post_management_fee(conn, pmc.pmc_id, owner, JAN_1, FEB_1, JAN_31) == Decimal("120.00")
    assert len(transfers_between(conn, owner, pmc.pmc_income)) == 1


def test_a_period_overlapping_one_taken_is_refused(conn, pmc, owner, lease):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, minimum_fee="10")
    transfer_batch(conn, [(pmc.operating_cash, owner, "100.00")], at(JAN_1))
    post_management_fee(conn, pmc.pmc_id, owner, JAN_1, FEB_1, JAN_31)

    with pytest.raises(psycopg.errors.ExclusionViolation, match="overlaps"):
        post_management_fee(conn, pmc.pmc_id, owner, date(2026, 1, 15), date(2026, 2, 15), JAN_31)
    # Periods that only touch don't overlap, whichever is taken first.
    assert post_management_fee(conn, pmc.pmc_id, owner, MAR_1, date(2026, 4, 1), at(MAR_1)) == 10
    assert post_management_fee(conn, pmc.pmc_id, owner, FEB_1, MAR_1, at(MAR_1)) == 10


def test_the_agreement_in_force_on_the_periods_first_day_sets_the_terms(conn, pmc, owner, lease):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, fee_percent="8")
    add_agreement(conn, pmc.pmc_id, owner, FEB_1, fee_percent="10")
    collect(conn, pmc, lease, RENT, at(date(2026, 2, 3)), due=FEB_1)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no management agreement"):
        post_management_fee(conn, pmc.pmc_id, owner, date(2025, 12, 1), JAN_1, JAN_31)
    assert post_management_fee(conn, pmc.pmc_id, owner, FEB_1, MAR_1, at(FEB_1)) == 150


def test_a_zero_fee_is_recorded_without_a_transfer(conn, pmc, owner):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1)

    assert post_management_fee(conn, pmc.pmc_id, owner, JAN_1, FEB_1, JAN_31) == 0
    assert fee_rows(conn, owner) == [(JAN_1, FEB_1, 0, 0, False)]
    assert transfers_between(conn, owner, pmc.pmc_income) == []


def test_fees_of_a_cent_are_taken(conn, pmc, owner, lease):
    # Only a fee of 0.00 is recorded without a transfer; the smallest fees still move money.
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, flat_fee="0.01", leasing_fee_percent="0.01")
    transfer_batch(conn, [(pmc.operating_cash, owner, "1.00")], at(JAN_1))

    assert post_management_fee(conn, pmc.pmc_id, owner, JAN_1, FEB_1, JAN_31) == Decimal("0.01")
    assert post_leasing_fee(conn, pmc.pmc_id, lease, owner, JAN_31) == Decimal("0.15")
    assert transfers_between(conn, owner, pmc.pmc_income) == [
        (Decimal("0.01"), "Management fee 2026-01-01 to 2026-01-31"),
        (Decimal("0.15"), "Leasing fee, lease from 2026-01-01"),  # 0.01% of 1500.00
    ]


def test_a_fee_the_owner_cannot_cover_is_refused_and_nothing_written(conn, pmc, owner):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, minimum_fee="50")

    with pytest.raises(psycopg.errors.CheckViolation, match="below zero"):
        post_management_fee(conn, pmc.pmc_id, owner, JAN_1, FEB_1, JAN_31)
    assert fee_rows(conn, owner) == []


@pytest.mark.parametrize(("start", "end"), [(FEB_1, JAN_1), (JAN_1, JAN_1), (None, FEB_1)])
def test_a_fee_period_ends_after_it_starts(conn, pmc, owner, start, end):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="must end after"):
        post_management_fee(conn, pmc.pmc_id, owner, start, end, JAN_31)


def test_a_fee_dated_in_a_reconciled_period_is_refused(conn, pmc, owner):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, minimum_fee="10")
    transfer_batch(conn, [(pmc.operating_cash, owner, "100.00")], at(JAN_1))
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, at(JAN_1, 0), at(FEB_1, 0))

    with pytest.raises(psycopg.errors.CheckViolation, match="reconciled and closed"):
        post_management_fee(conn, pmc.pmc_id, owner, JAN_1, FEB_1, JAN_31)
    assert post_management_fee(conn, pmc.pmc_id, owner, JAN_1, FEB_1, at(FEB_1)) == 10


def second_operating_account(conn, pmc):
    """Owner 1's account for Property 1 in a second operating trust bank account that has no
    fee account for the PMC. It holds 500.00."""
    bank = conn.execute(
        "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
        " VALUES (%s, 'operating', 'Second operating account') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    owner = pmc.owners[0]
    account = open_account(
        conn, pmc.pmc_id, bank, "owner_property", owner.owner_id, owner.property_id
    )
    cash = open_account(conn, pmc.pmc_id, bank, "bank_cash")
    transfer_batch(conn, [(cash, account, "500.00")], at(JAN_1))
    add_agreement(conn, pmc.pmc_id, account, JAN_1, minimum_fee="10")
    return account


def test_fees_need_the_pmcs_fee_account_in_the_same_bank_account(conn, pmc):
    account = second_operating_account(conn, pmc)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="fee account"):
        post_management_fee(conn, pmc.pmc_id, account, JAN_1, FEB_1, JAN_31)


@pytest.mark.parametrize("which", ["the PMC's fee account", "another PMC's owner", "unknown"])
def test_fees_and_draws_are_only_from_an_owners_property_account(conn, pmc, which):
    other = make_pmc(conn)
    account = {
        "the PMC's fee account": pmc.pmc_income,
        "another PMC's owner": other.owners[0].account,
        "unknown": "pgla_not_an_account",
    }[which]

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="not an owner's property"):
        post_management_fee(conn, pmc.pmc_id, account, JAN_1, FEB_1, JAN_31)
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="not an owner's property"):
        draw_owner(conn, pmc.pmc_id, account, "draw-1", None, JAN_31)


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_fees_and_draws_are_taken_at_read_committed(database_url, pmc, owner, isolation):
    with psycopg.connect(database_url) as tx:
        tx.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
        with pytest.raises(psycopg.errors.InvalidTransactionState, match="READ COMMITTED"):
            draw_owner(tx, pmc.pmc_id, owner, "draw-1", None, JAN_31)


@pytest.mark.timing
def test_the_same_fee_taken_twice_at_once_posts_once(conn, database_url, pmc, owner, lease):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, fee_percent="8")
    collect(conn, pmc, lease, RENT, at(date(2026, 1, 3)))

    with psycopg.connect(database_url) as first:
        post_management_fee(first, pmc.pmc_id, owner, JAN_1, FEB_1, JAN_31)  # not committed
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: post_management_fee(worker, pmc.pmc_id, owner, JAN_1, FEB_1, JAN_31),
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert outcome == {"result": Decimal("120.00")}
    assert len(transfers_between(conn, owner, pmc.pmc_income)) == 1


# --- leasing fees --------------------------------------------------------------------------


def test_the_leasing_fee_is_taken_once_per_lease(conn, pmc, owner, lease):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, leasing_fee_percent="50")
    collect(conn, pmc, lease, RENT, at(date(2026, 1, 3)))

    assert post_leasing_fee(conn, pmc.pmc_id, lease, owner, at(date(2026, 1, 4))) == 750
    assert post_leasing_fee(conn, pmc.pmc_id, lease, owner, at(date(2026, 1, 9))) == 750
    assert transfers_between(conn, owner, pmc.pmc_income) == [
        (Decimal("750.00"), "Leasing fee, lease from 2026-01-01")
    ]


def test_the_leasing_fee_comes_from_an_owner_of_the_leases_property(conn, pmc, owner, lease):
    add_agreement(conn, pmc.pmc_id, pmc.owners[1].account, JAN_1, leasing_fee_percent="50")

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="lease's property"):
        post_leasing_fee(conn, pmc.pmc_id, lease, pmc.owners[1].account, JAN_31)


def test_a_leasing_fee_taken_from_one_account_is_not_taken_again_from_another(
    conn, pmc, owner, lease
):
    owner_2 = conn.execute(  # a second owner of Property 1
        "INSERT INTO trust_owners (pmc_id, display_name) VALUES (%s, 'Owner 3') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    second = open_account(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        "owner_property",
        owner_2,
        pmc.owners[0].property_id,
    )
    add_agreement(conn, pmc.pmc_id, owner, JAN_1)
    post_leasing_fee(conn, pmc.pmc_id, lease, owner, JAN_31)

    with pytest.raises(psycopg.errors.UniqueViolation, match="was taken from"):
        post_leasing_fee(conn, pmc.pmc_id, lease, second, JAN_31)


def test_a_leasing_fee_needs_a_lease_of_the_pmc_and_an_agreement(conn, pmc, owner, lease):
    other = make_pmc(conn)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no management agreement"):
        post_leasing_fee(conn, pmc.pmc_id, lease, owner, JAN_31)
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no lease"):
        post_leasing_fee(conn, pmc.pmc_id, uuid.uuid4(), owner, JAN_31)
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no lease"):  # another PMC's
        post_leasing_fee(conn, other.pmc_id, lease, other.owners[0].account, JAN_31)


# --- owner draws ---------------------------------------------------------------------------


def test_a_draw_pays_everything_above_the_reserve(conn, app_conn, pmc, owner):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, reserve="200")
    transfer_batch(conn, [(pmc.operating_cash, owner, RENT)], at(date(2026, 1, 3)))

    assert draw_owner(app_conn, pmc.pmc_id, owner, "jan-draw", None, JAN_31) == 1300
    assert balance(conn, owner) == 200
    assert transfers_between(conn, owner, pmc.operating_cash) == [(1300, "Owner draw")]


def test_a_draw_of_a_set_amount_stays_within_what_is_available(conn, pmc, owner):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, reserve="500")
    transfer_batch(conn, [(pmc.operating_cash, owner, RENT)], at(date(2026, 1, 3)))

    with pytest.raises(psycopg.errors.CheckViolation, match="1000.00 is available"):
        draw_owner(conn, pmc.pmc_id, owner, "too-much", "1000.01", JAN_31)
    assert draw_owner(conn, pmc.pmc_id, owner, "some", "600.00", JAN_31) == 600
    assert draw_owner(conn, pmc.pmc_id, owner, "the-rest", "400.00", JAN_31) == 400


def test_a_draw_retried_with_its_key_returns_the_original(conn, pmc, owner):
    transfer_batch(conn, [(pmc.operating_cash, owner, RENT)], at(date(2026, 1, 3)))
    draw_owner(conn, pmc.pmc_id, owner, "jan-draw", None, JAN_31)

    # Nothing is available now, yet the retry returns the first draw and pays nothing more.
    assert draw_owner(conn, pmc.pmc_id, owner, "jan-draw", None, JAN_31) == RENT
    assert draw_owner(conn, pmc.pmc_id, owner, "jan-draw", RENT, JAN_31) == RENT
    with pytest.raises(psycopg.errors.UniqueViolation, match="already paid"):
        draw_owner(conn, pmc.pmc_id, owner, "jan-draw", "5.00", JAN_31)
    with pytest.raises(psycopg.errors.UniqueViolation, match="already paid"):
        draw_owner(conn, pmc.pmc_id, pmc.owners[1].account, "jan-draw", None, JAN_31)
    assert len(transfers_between(conn, owner, pmc.operating_cash)) == 1


def test_a_draw_with_nothing_available_is_refused(conn, pmc, owner):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, reserve="200")
    transfer_batch(conn, [(pmc.operating_cash, owner, "150.00")], at(date(2026, 1, 3)))

    with pytest.raises(psycopg.errors.CheckViolation, match="0 is available"):
        draw_owner(conn, pmc.pmc_id, owner, "draw-1", None, JAN_31)


def test_a_draw_pays_the_last_cent_above_the_reserve(conn, pmc, owner):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, reserve="200")
    transfer_batch(conn, [(pmc.operating_cash, owner, "200.01")], at(date(2026, 1, 3)))

    assert draw_owner(conn, pmc.pmc_id, owner, "draw-1", None, JAN_31) == Decimal("0.01")


def test_a_draw_with_no_date_is_dated_now_and_keeps_todays_reserve(conn, pmc, owner):
    # pgledger dates a transfer with no date now; the reserve must be the one in force now, not
    # none at all.
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, reserve="200")
    transfer_batch(conn, [(pmc.operating_cash, owner, RENT)], at(date(2026, 1, 3)))

    assert draw_owner(conn, pmc.pmc_id, owner, "draw-1", None, None) == 1300
    assert balance(conn, owner) == 200
    (dated,) = conn.execute(
        "SELECT t.event_at BETWEEN now() - interval '1 minute' AND now() FROM trust_owner_draws d"
        " JOIN pgledger_transfers t ON t.id = d.transfer_id"
        " WHERE d.pmc_id = %s AND d.request_key = 'draw-1'",
        (pmc.pmc_id,),
    ).fetchone()
    assert dated


def test_the_reserve_in_force_on_the_draws_day_applies(conn, pmc, owner):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, reserve="200")
    add_agreement(conn, pmc.pmc_id, owner, MAR_1, reserve="500")
    transfer_batch(conn, [(pmc.operating_cash, owner, RENT)], at(date(2026, 1, 3)))

    assert draw_owner(conn, pmc.pmc_id, owner, "march", None, at(MAR_1, 0, 0)) == 1000


@pytest.mark.parametrize(("key", "amount"), [("", None), (None, None), ("d", "0"), ("d", "-1")])
def test_a_draw_needs_a_key_and_a_positive_amount(conn, pmc, owner, key, amount):
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        draw_owner(conn, pmc.pmc_id, owner, key, amount, JAN_31)


@pytest.mark.timing
def test_two_draws_at_once_cannot_pay_out_more_than_is_there(conn, database_url, pmc, owner):
    transfer_batch(conn, [(pmc.operating_cash, owner, RENT)], at(date(2026, 1, 3)))

    with psycopg.connect(database_url) as first:
        draw_owner(first, pmc.pmc_id, owner, "first", None, JAN_31)  # not committed yet
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: draw_owner(worker, pmc.pmc_id, owner, "second", None, JAN_31),
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert balance(conn, owner) == 0


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE trust_owner_draws SET amount = amount + 1",
        "DELETE FROM trust_owner_draws",
        "UPDATE trust_management_fees SET fee = fee + 1",
        "DELETE FROM trust_leasing_fees",
    ],
)
def test_fees_and_draws_are_kept_for_good(conn, pmc, owner, statement):
    transfer_batch(conn, [(pmc.operating_cash, owner, RENT)], at(date(2026, 1, 3)))
    draw_owner(conn, pmc.pmc_id, owner, "jan-draw", "1.00", JAN_31)

    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)


def test_an_owner_draw_cannot_be_recorded_as_a_bounced_payment(conn, pmc, owner, lease):
    # Both move money from the owner's account to cash, but a draw paid the owner: recording it
    # as the payment coming back would make a paid charge read as owed again.
    rent, paid = collect(conn, pmc, lease, RENT, at(date(2026, 1, 3)))
    draw_owner(conn, pmc.pmc_id, owner, "jan-draw", RENT, JAN_31)
    (draw,) = conn.execute(
        "SELECT transfer_id FROM trust_owner_draws WHERE pmc_id = %s AND request_key = 'jan-draw'",
        (pmc.pmc_id,),
    ).fetchone()

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="paid the owner"):
        reverse_payment(conn, pmc.pmc_id, rent, paid, draw, RENT)
