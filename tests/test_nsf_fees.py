"""NSF fees (migration 20261013000044): a property's NSF fee, in force from a day, charged to the
lease of a payment the bank returned, under the terms in force the day it came back (UTC), once
per returned payment."""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_late_fee_policy,
    add_nsf_fee_policy,
    add_unit,
    apply_payment,
    assess_late_fees,
    charge,
    charge_nsf_fee,
    in_background,
    make_pmc,
    open_lease,
    post,
    wait_until_blocked,
)

JAN_1, JAN_10 = date(2026, 1, 1), date(2026, 1, 10)
RENT = Decimal("1500.00")
PAID_ON = datetime(2026, 1, 3, 12, tzinfo=UTC)
RETURNED_ON = datetime(2026, 1, 8, 12, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def lease(conn, pmc):
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, JAN_1, None, RENT, [tenant])


def paid(conn, pmc, lease, key="pay-1", amount=RENT, when=PAID_ON):
    """A tenant's payment into Owner 1's account, posted the way the rent page posts one."""
    meta = {"kind": "tenant payment", "lease_id": str(lease), "reference": None, "key": key}
    return post(
        conn, pmc.pmc_id, key, [(pmc.operating_cash, pmc.owners[0].account, amount)], when, meta
    )


def returned(conn, pmc, lease, key="pay-1", amount=RENT, when=RETURNED_ON, lease_id=None):
    """The bank sends the payment back: its key, as the rent page posts a bounce."""
    meta = {
        "kind": "bounced payment",
        "lease_id": str(lease if lease_id is None else lease_id),
        "reference": None,
        "key": f"bounce/{key}",
    }
    post(
        conn,
        pmc.pmc_id,
        f"bounce/{key}",
        [(pmc.owners[0].account, pmc.operating_cash, amount)],
        when,
        meta,
    )
    return f"bounce/{key}"


def nsf_fees(conn, pmc):
    return conn.execute(
        "SELECT f.returned_key, f.policy_id, f.fee, c.lease_id, c.kind, c.due_on, c.amount,"
        " c.memo FROM trust_nsf_fees AS f JOIN trust_charges AS c ON c.id = f.fee_charge_id"
        " WHERE f.pmc_id = %s ORDER BY f.created_at, f.returned_key",
        (pmc.pmc_id,),
    ).fetchall()


def test_a_returned_payment_is_charged_the_propertys_nsf_fee_due_the_day_it_came_back(
    conn, app_conn, pmc, lease
):
    terms = add_nsf_fee_policy(app_conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, "35.00")
    paid(conn, pmc, lease)
    key = returned(conn, pmc, lease)

    fee = charge_nsf_fee(app_conn, pmc.pmc_id, key)

    assert fee is not None
    assert nsf_fees(conn, pmc) == [
        (
            key,
            terms,
            Decimal("35.00"),
            lease,
            "fee",
            date(2026, 1, 8),
            Decimal("35.00"),
            "NSF fee, payment returned 2026-01-08",
        ),
    ]
    # A charge like any other: a payment pays it.
    payment = paid(conn, pmc, lease, "pay-2", Decimal("35.00"))[0]
    assert apply_payment(conn, pmc.pmc_id, fee, payment, Decimal("35.00")) == Decimal("35.00")


def test_the_same_return_again_returns_the_same_fee(conn, app_conn, pmc, lease):
    add_nsf_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, "35.00")
    paid(conn, pmc, lease)
    key = returned(conn, pmc, lease)

    first = charge_nsf_fee(app_conn, pmc.pmc_id, key)

    assert charge_nsf_fee(app_conn, pmc.pmc_id, key) == first
    assert len(nsf_fees(conn, pmc)) == 1


def test_the_terms_are_those_in_force_the_day_it_came_back(conn, pmc, lease):
    prop = pmc.owners[0].property_id
    add_nsf_fee_policy(conn, pmc.pmc_id, prop, JAN_1, "25.00")
    add_nsf_fee_policy(conn, pmc.pmc_id, prop, JAN_10, "40.00")
    for key, when in (("before", RETURNED_ON), ("on", datetime(2026, 1, 10, tzinfo=UTC))):
        paid(conn, pmc, lease, key)
        charge_nsf_fee(conn, pmc.pmc_id, returned(conn, pmc, lease, key, when=when))

    assert [(row[0], row[2], row[5]) for row in nsf_fees(conn, pmc)] == [
        ("bounce/before", Decimal("25.00"), date(2026, 1, 8)),
        ("bounce/on", Decimal("40.00"), JAN_10),
    ]


def test_the_day_it_came_back_is_the_utc_day(conn, pmc, lease):
    """Late on Jan 9 in New York is Jan 10 in UTC, when the higher fee is in force."""
    prop = pmc.owners[0].property_id
    add_nsf_fee_policy(conn, pmc.pmc_id, prop, JAN_1, "25.00")
    add_nsf_fee_policy(conn, pmc.pmc_id, prop, JAN_10, "40.00")
    paid(conn, pmc, lease)
    late_evening = datetime(2026, 1, 10, 3, 30, tzinfo=UTC)
    conn.execute("SET TIME ZONE 'America/New_York'")
    charge_nsf_fee(conn, pmc.pmc_id, returned(conn, pmc, lease, when=late_evening))

    assert [(row[2], row[5]) for row in nsf_fees(conn, pmc)] == [(Decimal("40.00"), JAN_10)]


def test_no_terms_or_a_fee_of_nothing_charges_none(conn, pmc, lease):
    paid(conn, pmc, lease, "a")
    assert charge_nsf_fee(conn, pmc.pmc_id, returned(conn, pmc, lease, "a")) is None
    add_nsf_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, "35.00")
    add_nsf_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, date(2026, 1, 5), "0")
    paid(conn, pmc, lease, "b")

    assert charge_nsf_fee(conn, pmc.pmc_id, returned(conn, pmc, lease, "b")) is None
    assert nsf_fees(conn, pmc) == []


def test_terms_that_start_after_the_return_charge_none(conn, pmc, lease):
    add_nsf_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_10, "35.00")
    paid(conn, pmc, lease)

    assert charge_nsf_fee(conn, pmc.pmc_id, returned(conn, pmc, lease)) is None


def test_another_propertys_terms_do_not_apply(conn, pmc, lease):
    add_nsf_fee_policy(conn, pmc.pmc_id, pmc.owners[1].property_id, JAN_1, "35.00")
    paid(conn, pmc, lease)

    assert charge_nsf_fee(conn, pmc.pmc_id, returned(conn, pmc, lease)) is None


def test_only_a_returned_payment_is_charged(conn, pmc, lease):
    add_nsf_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, "35.00")
    paid(conn, pmc, lease)
    other = make_pmc(conn)

    for pmc_id, key in (
        (pmc.pmc_id, "pay-1"),  # the payment itself
        (pmc.pmc_id, "bounce/nothing"),  # no such key
        (other.pmc_id, returned(conn, pmc, lease)),  # another PMC's return
    ):
        with pytest.raises(psycopg.errors.InvalidParameterValue, match="no returned payment"):
            charge_nsf_fee(conn, pmc_id, key)
    assert nsf_fees(conn, pmc) == []


def test_a_return_naming_no_lease_of_the_pmc_is_refused(conn, pmc, lease):
    other = make_pmc(conn)
    unit = add_unit(conn, other.pmc_id, other.owners[0].property_id)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (other.prepaid_rent,),
    ).fetchone()[0]
    theirs = open_lease(conn, other.pmc_id, unit, JAN_1, None, RENT, [tenant])
    paid(conn, pmc, lease)
    key = returned(conn, pmc, lease, lease_id=theirs)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="names no lease of PMC"):
        charge_nsf_fee(conn, pmc.pmc_id, key)


def test_postings_that_are_not_money_sent_back_are_refused(conn, pmc, lease):
    """Its metadata says bounced, but the money went into the owner's account, not back out."""
    add_nsf_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, "35.00")
    meta = {"kind": "bounced payment", "lease_id": str(lease)}
    post(
        conn,
        pmc.pmc_id,
        "bounce/x",
        [(pmc.operating_cash, pmc.owners[0].account, RENT)],
        RETURNED_ON,
        meta,
    )

    with pytest.raises(psycopg.errors.CheckViolation, match="not a returned payment"):
        charge_nsf_fee(conn, pmc.pmc_id, "bounce/x")


def test_money_sent_back_out_of_prepaid_rent_is_a_return(conn, pmc, lease):
    add_nsf_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, "35.00")
    meta = {"kind": "tenant payment", "lease_id": str(lease)}
    post(conn, pmc.pmc_id, "p", [(pmc.operating_cash, pmc.prepaid_rent, RENT)], PAID_ON, meta)
    post(
        conn,
        pmc.pmc_id,
        "bounce/p",
        [(pmc.prepaid_rent, pmc.operating_cash, RENT)],
        RETURNED_ON,
        {"kind": "bounced payment", "lease_id": str(lease)},
    )

    assert charge_nsf_fee(conn, pmc.pmc_id, "bounce/p") is not None


def test_an_nsf_fee_is_charged_at_read_committed_only(database_url, pmc, lease, conn):
    paid(conn, pmc, lease)
    key = returned(conn, pmc, lease)
    with psycopg.connect(database_url) as tx:
        tx.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with pytest.raises(psycopg.errors.InvalidTransactionState, match="READ COMMITTED"):
            charge_nsf_fee(tx, pmc.pmc_id, key)


@pytest.mark.timing
def test_two_calls_at_once_charge_one_fee(conn, database_url, pmc, lease):
    add_nsf_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, "35.00")
    paid(conn, pmc, lease)
    key = returned(conn, pmc, lease)

    with psycopg.connect(database_url) as first:
        fee = charge_nsf_fee(first, pmc.pmc_id, key)  # not committed yet
        thread, pid, outcome = in_background(
            database_url, lambda worker: charge_nsf_fee(worker, pmc.pmc_id, key)
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert outcome == {"result": fee}
    assert len(nsf_fees(conn, pmc)) == 1


def test_nsf_fee_terms_are_whole_cents_and_never_negative(conn, pmc):
    prop = pmc.owners[0].property_id
    for fee in ("-1", "35.001"):
        with pytest.raises(psycopg.errors.CheckViolation):
            add_nsf_fee_policy(conn, pmc.pmc_id, prop, JAN_1, fee)
    add_nsf_fee_policy(conn, pmc.pmc_id, prop, JAN_1, "35.00")
    with pytest.raises(psycopg.errors.UniqueViolation):
        add_nsf_fee_policy(conn, pmc.pmc_id, prop, JAN_1, "40.00")
    other = make_pmc(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        add_nsf_fee_policy(conn, other.pmc_id, prop, JAN_10, "35.00")


def test_nsf_fees_and_their_terms_are_never_rewritten(conn, pmc, lease):
    add_nsf_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, "35.00")
    paid(conn, pmc, lease)
    charge_nsf_fee(conn, pmc.pmc_id, returned(conn, pmc, lease))
    for table in ("trust_nsf_fee_policies", "trust_nsf_fees"):
        for statement in (f"UPDATE {table} SET fee = 1", f"DELETE FROM {table}"):
            with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
                conn.execute(statement)


def test_a_late_fee_is_not_charged_on_an_nsf_fee(conn, pmc, lease):
    """Late fees are charged on rent only; an NSF fee left unpaid draws none."""
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, 0, "50")
    add_nsf_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, "35.00")
    rent = charge(conn, pmc.pmc_id, lease, date(2026, 1, 5), RENT)  # paid on time
    payment = paid(conn, pmc, lease)[0]
    apply_payment(conn, pmc.pmc_id, rent, payment, RENT)
    paid(conn, pmc, lease, "pay-2", Decimal("10.00"))
    charge_nsf_fee(conn, pmc.pmc_id, returned(conn, pmc, lease, "pay-2", Decimal("10.00")))

    assert assess_late_fees(conn, pmc.pmc_id, date(2026, 3, 1)) == 0
