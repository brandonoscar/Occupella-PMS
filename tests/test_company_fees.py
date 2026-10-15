"""The company takes its earned fees out of trust (migration 20261015000059).

A management or leasing fee moves money from an owner's account to the PMC's fee account, which
still sits in the trust bank account. Leaving it there mixes the company's own money with its
clients' (commingling), so trust_withdraw_pmc_fees pays it out through the account's cash: never
more than the fee account holds, once per request key, by a staff member who may disburse.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    add_agreement,
    add_staff,
    approve,
    balance,
    bank_sums,
    give_roles,
    in_background,
    make_pmc,
    post_management_fee,
    transfer_batch,
    wait_until_blocked,
    withdraw_pmc_fees,
)

JAN_1, FEB_1 = date(2026, 1, 1), date(2026, 2, 1)
JAN_31 = datetime(2026, 1, 31, 23, 0, tzinfo=UTC)


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def earned(conn, pmc):
    """The company earned a 150.00 management fee from Owner 1 in January."""
    owner = pmc.owners[0].account
    transfer_batch(conn, [(pmc.operating_cash, owner, "1500.00")], at(date(2026, 1, 3)))
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, minimum_fee="150")
    post_management_fee(conn, pmc.pmc_id, owner, JAN_1, FEB_1, at(date(2026, 1, 31), 9))
    return Decimal("150.00")


def withdrawals(conn, pmc):
    return conn.execute(
        "SELECT t.amount, t.metadata ->> 'memo' FROM trust_pmc_fee_withdrawals AS w"
        " JOIN pgledger_transfers AS t ON t.id = w.transfer_id"
        " WHERE w.pmc_id = %s AND t.from_account_id = %s AND t.to_account_id = %s"
        " ORDER BY t.event_at, t.id",
        (pmc.pmc_id, pmc.pmc_income, pmc.operating_cash),
    ).fetchall()


def test_the_company_takes_out_everything_its_fee_account_holds(conn, app_conn, pmc, earned):
    taken = withdraw_pmc_fees(app_conn, pmc.pmc_id, pmc.operating_bank_id, "feb", None, JAN_31)

    assert taken == earned
    assert balance(conn, pmc.pmc_income) == 0
    assert withdrawals(conn, pmc) == [(earned, "Company fees taken out of trust")]
    # The owner's money stays where it was; the bank account still ties out.
    assert balance(conn, pmc.owners[0].account) == Decimal("1350.00")
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_a_set_amount_is_taken_and_never_more_than_is_there(conn, pmc, earned):
    bank = pmc.operating_bank_id

    with pytest.raises(psycopg.errors.CheckViolation, match="150.00 of the company's fees is in"):
        withdraw_pmc_fees(conn, pmc.pmc_id, bank, "too-much", "150.01", JAN_31)
    assert withdraw_pmc_fees(conn, pmc.pmc_id, bank, "some", "100.00", JAN_31) == 100
    assert withdraw_pmc_fees(conn, pmc.pmc_id, bank, "the-rest", "50.00", JAN_31) == 50
    with pytest.raises(
        psycopg.errors.CheckViolation, match="trust: 0.00 of the company's fees is in"
    ):
        withdraw_pmc_fees(conn, pmc.pmc_id, bank, "nothing-left", None, JAN_31)
    memo = "Company fees taken out of trust"
    assert withdrawals(conn, pmc) == [(100, memo), (50, memo)]


def test_a_withdrawal_retried_with_its_key_returns_the_original(conn, pmc, earned):
    bank = pmc.operating_bank_id
    withdraw_pmc_fees(conn, pmc.pmc_id, bank, "feb", None, JAN_31)

    # Nothing is left now, yet the retry returns the first withdrawal and takes nothing more.
    assert withdraw_pmc_fees(conn, pmc.pmc_id, bank, "feb", None, JAN_31) == earned
    assert withdraw_pmc_fees(conn, pmc.pmc_id, bank, "feb", earned, JAN_31) == earned
    with pytest.raises(psycopg.errors.UniqueViolation, match="already took 150.00"):
        withdraw_pmc_fees(conn, pmc.pmc_id, bank, "feb", "5.00", JAN_31)
    assert len(withdrawals(conn, pmc)) == 1


@pytest.mark.parametrize(("key", "amount"), [("", None), (None, None), ("k", "0"), ("k", "-1")])
def test_a_withdrawal_needs_a_key_and_a_positive_amount(conn, pmc, earned, key, amount):
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        withdraw_pmc_fees(conn, pmc.pmc_id, pmc.operating_bank_id, key, amount, JAN_31)


@pytest.mark.parametrize("which", ["security deposit", "another PMC's", "unknown"])
def test_only_a_trust_bank_account_holding_the_companys_fees(conn, pmc, earned, which):
    other = make_pmc(conn)
    bank = {
        "security deposit": pmc.deposit_bank_id,
        "another PMC's": other.operating_bank_id,
        "unknown": other.pmc_id,
    }[which]

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="holds no fee account"):
        withdraw_pmc_fees(conn, pmc.pmc_id, bank, "feb", None, JAN_31)


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_the_companys_fees_are_taken_at_read_committed(database_url, pmc, earned, isolation):
    with psycopg.connect(database_url) as tx:
        tx.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
        with pytest.raises(psycopg.errors.InvalidTransactionState, match="READ COMMITTED"):
            withdraw_pmc_fees(tx, pmc.pmc_id, pmc.operating_bank_id, "feb", None, JAN_31)


@pytest.mark.timing
def test_two_withdrawals_at_once_cannot_take_more_than_is_there(conn, database_url, pmc, earned):
    bank = pmc.operating_bank_id
    with psycopg.connect(database_url) as first:
        withdraw_pmc_fees(first, pmc.pmc_id, bank, "first", None, JAN_31)  # not committed yet
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: withdraw_pmc_fees(worker, pmc.pmc_id, bank, "second", None, JAN_31),
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert balance(conn, pmc.pmc_income) == 0
    assert len(withdrawals(conn, pmc)) == 1


def test_a_withdrawal_dated_in_a_reconciled_period_is_refused(conn, pmc, earned):
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, at(JAN_1, 0), at(FEB_1, 0))

    with pytest.raises(psycopg.errors.CheckViolation, match="reconciled and closed"):
        withdraw_pmc_fees(conn, pmc.pmc_id, pmc.operating_bank_id, "jan", None, JAN_31)
    assert withdraw_pmc_fees(conn, pmc.pmc_id, pmc.operating_bank_id, "feb", None, at(FEB_1))


def test_taking_the_companys_fees_takes_a_staff_member_who_may_disburse(
    conn, app_conn, pmc, earned
):
    bank = pmc.operating_bank_id
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    give_roles(conn, pmc.pmc_id, clerk, ["approve"])
    with (
        acting_as(app_conn, clerk),
        pytest.raises(psycopg.errors.InsufficientPrivilege, match="Clerk 1 may not pay out money"),
    ):
        withdraw_pmc_fees(app_conn, pmc.pmc_id, bank, "feb", None, JAN_31)
    app_conn.rollback()

    give_roles(conn, pmc.pmc_id, clerk, ["disburse"])
    with acting_as(app_conn, clerk):
        assert withdraw_pmc_fees(app_conn, pmc.pmc_id, bank, "feb", None, JAN_31) == earned
    # The audit log names who took them.
    assert conn.execute(
        "SELECT staff_id FROM trust_audit_log"
        " WHERE table_name = 'trust_pmc_fee_withdrawals' AND pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall() == [(clerk,)]


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE trust_pmc_fee_withdrawals SET amount = amount + 1",
        "DELETE FROM trust_pmc_fee_withdrawals",
    ],
)
def test_withdrawals_are_kept_for_good(conn, pmc, earned, statement):
    withdraw_pmc_fees(conn, pmc.pmc_id, pmc.operating_bank_id, "feb", "1.00", JAN_31)

    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)


def test_the_cash_flow_report_counts_it_as_fees_paid_to_the_company(conn, pmc, earned):
    withdraw_pmc_fees(conn, pmc.pmc_id, pmc.operating_bank_id, "feb", None, JAN_31)

    lines = conn.execute(
        "SELECT item, amount FROM trust_report_cash_flow(%s, %s, %s)"
        " WHERE section = 'all trust bank accounts' AND item LIKE 'out: %%' ORDER BY line",
        (pmc.pmc_id, JAN_1, date(2026, 1, 31)),
    ).fetchall()
    assert lines == [("out: fees paid to the company", earned)]
