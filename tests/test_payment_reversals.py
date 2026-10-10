"""A payment that bounces: its reversing transfer is recorded against the match, and the charge
counts as unpaid again from the reversal's date (migration 20261010000014).

Matches are append-only, so this is how one is undone: never past the match, never spending the
reversing transfer past its amount, and safe to retry.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_unit,
    apply_payment,
    charge,
    in_background,
    make_pmc,
    open_lease,
    reverse_payment,
    transfer_batch,
    wait_until_blocked,
)

JAN_1 = date(2026, 1, 1)
RENT = Decimal("1500.00")
PAID_ON = datetime(2026, 1, 3, tzinfo=UTC)
BOUNCED_ON = datetime(2026, 1, 8, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    """A PMC whose Owner 1 already holds money from December, so a test can post more than one
    reversing transfer out of the owner's account."""
    pmc = make_pmc(conn)
    december = datetime(2025, 12, 1, tzinfo=UTC)
    transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "5000.00")], december)
    return pmc


@pytest.fixture
def lease(conn, pmc):
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, JAN_1, None, RENT, [tenant])


@pytest.fixture
def paid(conn, pmc, lease):
    """January's rent, paid in full on Jan 3: (charge id, payment transfer id)."""
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, RENT)
    payment = pay(conn, pmc)
    apply_payment(conn, pmc.pmc_id, rent, payment, RENT)
    return rent, payment


def pay(conn, pmc, amount=RENT, source=None, when=PAID_ON):
    source = pmc.operating_cash if source is None else source
    return transfer_batch(conn, [(source, pmc.owners[0].account, amount)], event_at=when)[0]


def bounce(conn, pmc, amount=RENT, back_to=None, when=BOUNCED_ON):
    """The reversing transfer: money back out of Owner 1's account."""
    back_to = pmc.operating_cash if back_to is None else back_to
    return transfer_batch(conn, [(pmc.owners[0].account, back_to, amount)], event_at=when)[0]


def reversals(conn, pmc):
    return conn.execute(
        "SELECT charge_id, transfer_id, reversal_id, amount FROM trust_payment_reversals"
        " WHERE pmc_id = %s ORDER BY created_at, reversal_id",
        (pmc.pmc_id,),
    ).fetchall()


def test_a_bounced_payment_is_reversed_and_the_charge_can_be_paid_again(conn, app_conn, pmc, paid):
    rent, payment = paid
    second_try = pay(conn, pmc, when=datetime(2026, 1, 12, tzinfo=UTC))
    with pytest.raises(psycopg.errors.CheckViolation, match="already paid"):
        apply_payment(conn, pmc.pmc_id, rent, second_try, RENT)

    returned = bounce(conn, pmc)
    assert reverse_payment(app_conn, pmc.pmc_id, rent, payment, returned, RENT) == RENT

    assert reversals(conn, pmc) == [(rent, payment, returned, RENT)]
    assert apply_payment(conn, pmc.pmc_id, rent, second_try, RENT) == RENT


def test_the_same_reversal_retried_returns_the_original(conn, pmc, paid):
    rent, payment = paid
    returned = bounce(conn, pmc)
    reverse_payment(conn, pmc.pmc_id, rent, payment, returned, RENT)

    assert reverse_payment(conn, pmc.pmc_id, rent, payment, returned, RENT) == RENT
    with pytest.raises(psycopg.errors.UniqueViolation, match="already reverses 1500.00"):
        reverse_payment(conn, pmc.pmc_id, rent, payment, returned, "1000.00")
    assert len(reversals(conn, pmc)) == 1


def test_a_reversal_undoes_no_more_than_the_match(conn, pmc, paid):
    rent, payment = paid
    first, second = bounce(conn, pmc, "1000.00"), bounce(conn, pmc, "500.01")
    reverse_payment(conn, pmc.pmc_id, rent, payment, first, "1000.00")

    with pytest.raises(psycopg.errors.CheckViolation, match="reversed already"):
        reverse_payment(conn, pmc.pmc_id, rent, payment, second, "500.01")
    assert reverse_payment(conn, pmc.pmc_id, rent, payment, second, "500.00") == Decimal("500.00")


def test_a_reversing_transfer_is_spent_no_more_than_its_amount(conn, pmc, lease):
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, "1000.00")
    fee = charge(conn, pmc.pmc_id, lease, JAN_1, "50.00", "fee")
    payment = pay(conn, pmc, "1050.00")
    apply_payment(conn, pmc.pmc_id, rent, payment, "1000.00")
    apply_payment(conn, pmc.pmc_id, fee, payment, "50.00")
    returned = bounce(conn, pmc, "1025.00")
    reverse_payment(conn, pmc.pmc_id, rent, payment, returned, "1000.00")

    with pytest.raises(psycopg.errors.CheckViolation, match="moved 1025.00"):
        reverse_payment(conn, pmc.pmc_id, fee, payment, returned, "50.00")
    assert reverse_payment(conn, pmc.pmc_id, fee, payment, returned, "25.00") == Decimal("25.00")


def test_money_paid_from_prepaid_rent_is_reversed_back_into_it(conn, pmc, lease):
    transfer_batch(conn, [(pmc.operating_cash, pmc.prepaid_rent, RENT)], event_at=PAID_ON)
    rent = charge(conn, pmc.pmc_id, lease, JAN_1, RENT)
    applied = pay(conn, pmc, source=pmc.prepaid_rent)
    apply_payment(conn, pmc.pmc_id, rent, applied, RENT)
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="back the way"):
        reverse_payment(conn, pmc.pmc_id, rent, applied, bounce(conn, pmc), RENT)

    undone = bounce(conn, pmc, back_to=pmc.prepaid_rent)

    assert reverse_payment(conn, pmc.pmc_id, rent, applied, undone, RENT) == RENT


@pytest.mark.parametrize(
    "wrong", ["another payment", "into the PMC's fees", "out of another owner's account"]
)
def test_a_transfer_that_does_not_move_the_money_back_is_refused(conn, pmc, paid, wrong):
    rent, payment = paid
    owner_2 = pmc.owners[1].account
    transfer_batch(conn, [(pmc.operating_cash, owner_2, RENT)], event_at=PAID_ON)
    source, target = {
        "another payment": (pmc.operating_cash, pmc.owners[0].account),
        "into the PMC's fees": (pmc.owners[0].account, pmc.pmc_income),
        "out of another owner's account": (owner_2, pmc.operating_cash),
    }[wrong]
    other = transfer_batch(conn, [(source, target, RENT)], event_at=BOUNCED_ON)[0]

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="back the way"):
        reverse_payment(conn, pmc.pmc_id, rent, payment, other, RENT)
    assert reversals(conn, pmc) == []


def test_a_reversal_dated_before_the_payment_is_refused(conn, pmc, paid):
    rent, payment = paid
    early = bounce(conn, pmc, when=datetime(2026, 1, 2, tzinfo=UTC))
    same_moment = bounce(conn, pmc, when=PAID_ON)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="dated before"):
        reverse_payment(conn, pmc.pmc_id, rent, payment, early, RENT)
    assert reverse_payment(conn, pmc.pmc_id, rent, payment, same_moment, RENT) == RENT


def test_reversing_what_was_never_matched_is_refused(conn, pmc, lease, paid):
    rent, payment = paid
    other = make_pmc(conn)
    fee = charge(conn, pmc.pmc_id, lease, JAN_1, "35.00", "fee")
    returned = bounce(conn, pmc)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="pays nothing of charge"):
        reverse_payment(conn, pmc.pmc_id, fee, payment, returned, "35.00")
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no charge"):
        reverse_payment(conn, other.pmc_id, rent, payment, returned, RENT)
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no transfer"):
        reverse_payment(conn, pmc.pmc_id, rent, payment, "pglt_not_a_transfer", RENT)


@pytest.mark.parametrize("amount", ["0", "-1.00", None])
def test_a_reversal_undoes_a_positive_amount(conn, pmc, paid, amount):
    rent, payment = paid

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="positive amount"):
        reverse_payment(conn, pmc.pmc_id, rent, payment, bounce(conn, pmc), amount)


def test_the_smallest_reversal_a_cent_is_recorded(conn, pmc, paid):
    rent, payment = paid
    cent = Decimal("0.01")

    assert reverse_payment(conn, pmc.pmc_id, rent, payment, bounce(conn, pmc, cent), cent) == cent


def test_a_bounced_payment_never_pays_anything_else(conn, pmc, lease, paid):
    rent, payment = paid
    reverse_payment(conn, pmc.pmc_id, rent, payment, bounce(conn, pmc), RENT)
    fee = charge(conn, pmc.pmc_id, lease, JAN_1, "35.00", "fee")

    with pytest.raises(psycopg.errors.CheckViolation, match="already pays 1500.00"):
        apply_payment(conn, pmc.pmc_id, fee, payment, "35.00")


def test_a_kept_deposit_paid_and_reversed_moves_with_its_cash(conn, pmc, lease):
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, RENT)], event_at=PAID_ON)
    damages = charge(conn, pmc.pmc_id, lease, JAN_1, "320.00", "fee")
    kept = transfer_batch(
        conn,
        [
            (pmc.tenant_deposit, pmc.owners[0].account, "320.00"),
            (pmc.operating_cash, pmc.deposit_cash, "320.00"),
        ],
        event_at=PAID_ON,
    )[0]
    apply_payment(conn, pmc.pmc_id, damages, kept, "320.00")
    # The deposit goes back to the tenant, and its cash with it.
    returned = transfer_batch(
        conn,
        [
            (pmc.owners[0].account, pmc.tenant_deposit, "320.00"),
            (pmc.deposit_cash, pmc.operating_cash, "320.00"),
        ],
        event_at=BOUNCED_ON,
    )[0]

    assert reverse_payment(conn, pmc.pmc_id, damages, kept, returned, "320.00") == Decimal("320.00")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE trust_payment_reversals SET amount = amount + 1",
        "DELETE FROM trust_payment_reversals",
    ],
)
def test_reversals_are_kept_for_good(conn, pmc, paid, statement):
    rent, payment = paid
    reverse_payment(conn, pmc.pmc_id, rent, payment, bounce(conn, pmc), RENT)

    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)


def test_the_app_reverses_only_through_the_function(conn, app_conn, pmc, paid):
    rent, payment = paid

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute(
            "INSERT INTO trust_payment_reversals"
            " (pmc_id, charge_id, transfer_id, reversal_id, amount) VALUES (%s, %s, %s, %s, %s)",
            (pmc.pmc_id, rent, payment, bounce(conn, pmc), RENT),
        )


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_reversals_are_recorded_at_read_committed(conn, database_url, pmc, paid, isolation):
    rent, payment = paid
    returned = bounce(conn, pmc)

    with psycopg.connect(database_url) as tx:
        tx.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
        with pytest.raises(psycopg.errors.InvalidTransactionState, match="READ COMMITTED"):
            reverse_payment(tx, pmc.pmc_id, rent, payment, returned, RENT)


@pytest.mark.timing
def test_two_reversals_at_once_cannot_undo_more_than_the_match(conn, database_url, pmc, paid):
    rent, payment = paid
    first, second = bounce(conn, pmc), bounce(conn, pmc)

    with psycopg.connect(database_url) as tx:
        reverse_payment(tx, pmc.pmc_id, rent, payment, first, RENT)  # not committed yet
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: reverse_payment(worker, pmc.pmc_id, rent, payment, second, RENT),
        )
        wait_until_blocked(conn, pid)
        tx.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert [row[2] for row in reversals(conn, pmc)] == [first]


@pytest.mark.timing
def test_a_payment_waits_for_a_reversal_in_progress_and_then_counts_it(
    conn, database_url, pmc, paid
):
    rent, payment = paid
    second_try = pay(conn, pmc, when=datetime(2026, 1, 12, tzinfo=UTC))

    with psycopg.connect(database_url) as tx:
        reverse_payment(tx, pmc.pmc_id, rent, payment, bounce(conn, pmc), RENT)  # uncommitted
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: apply_payment(worker, pmc.pmc_id, rent, second_try, RENT),
        )
        wait_until_blocked(conn, pid)
        tx.commit()
    thread.join(timeout=30)

    assert outcome == {"result": RENT}
