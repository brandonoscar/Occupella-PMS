"""Vendor bills: entered against an owner's property, approved by the owner above the approval
limit, set aside from the owner's money, then paid (migration 20261010000017).

Setting a bill aside moves its amount from the owner's account to the vendor's (vendor_payable);
paying it sends that money out through the trust bank account's cash. Each step happens once per
bill and is safe to retry. A draw keeps back unpaid bills on top of the reserve.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_agreement,
    approve,
    approve_bill,
    balance,
    draw_owner,
    enter_bill,
    in_background,
    make_pmc,
    pay_bill,
    set_aside_bill,
    transfer_batch,
    wait_until_blocked,
)

JAN_1, JAN_15, FEB_1 = date(2026, 1, 1), date(2026, 1, 15), date(2026, 2, 1)


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
def funded(conn, pmc, owner):
    """Owner 1's account holding 1000.00 from Jan 1, under an agreement with a 500.00 approval
    limit and a 100.00 reserve."""
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, reserve="100", approval_limit="500")
    transfer_batch(conn, [(pmc.operating_cash, owner, "1000.00")], at(JAN_1))
    return owner


def bill(conn, pmc, account, amount="250.00", reference="INV-1", bill_date=JAN_15, vendor=None):
    return enter_bill(
        conn,
        pmc.pmc_id,
        vendor or pmc.vendor_id,
        account,
        reference,
        bill_date,
        bill_date,
        amount,
    )


def memos(conn, source, target):
    return conn.execute(
        "SELECT amount, metadata ->> 'memo' FROM pgledger_transfers"
        " WHERE from_account_id = %s AND to_account_id = %s ORDER BY event_at, id",
        (source, target),
    ).fetchall()


def steps(conn, bill_id):
    return conn.execute(
        "SELECT step FROM trust_bill_payments WHERE bill_id = %s ORDER BY step", (bill_id,)
    ).fetchall()


# --- entering bills ------------------------------------------------------------------------


def test_the_app_enters_a_bill_against_an_owners_property(conn, app_conn, pmc, owner):
    bill_id = bill(app_conn, pmc, owner)

    assert conn.execute(
        "SELECT vendor_id, ledger_account_id, reference, bill_date, due_on, amount, memo"
        " FROM trust_bills WHERE id = %s",
        (bill_id,),
    ).fetchone() == (pmc.vendor_id, owner, "INV-1", JAN_15, JAN_15, Decimal("250.00"), "Repair")


def test_a_vendors_bill_is_entered_once(conn, pmc, owner):
    other_vendor = conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor 2') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    bill(conn, pmc, owner)
    bill(conn, pmc, owner, vendor=other_vendor)  # another vendor's numbers are its own

    with pytest.raises(psycopg.errors.UniqueViolation):
        bill(conn, pmc, pmc.owners[1].account)


@pytest.mark.parametrize("which", ["the PMC's fee account", "a deposit", "another PMC's owner"])
def test_a_bill_is_only_against_an_owners_property_account_of_its_pmc(conn, pmc, which):
    account = {
        "the PMC's fee account": pmc.pmc_income,
        "a deposit": pmc.tenant_deposit,
        "another PMC's owner": make_pmc(conn).owners[0].account,
    }[which]

    with pytest.raises(psycopg.errors.CheckViolation, match="owner's property account"):
        bill(conn, pmc, account)


def test_a_bill_is_from_a_vendor_of_its_pmc(conn, pmc, owner):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        bill(conn, pmc, owner, vendor=make_pmc(conn).vendor_id)


@pytest.mark.parametrize(
    "column, value",
    [
        ("amount", "0"),
        ("amount", "-1"),
        ("reference", ""),
        ("memo", ""),
        ("due_on", date(2026, 1, 14)),
    ],
)
def test_a_bills_details_stay_in_range(conn, pmc, owner, column, value):
    row = {
        "pmc_id": pmc.pmc_id,
        "vendor_id": pmc.vendor_id,
        "ledger_account_id": owner,
        "reference": "INV-1",
        "bill_date": JAN_15,
        "due_on": JAN_15,
        "amount": Decimal("10"),
        "memo": "Repair",
    }
    row[column] = value

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            f"INSERT INTO trust_bills ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})",
            list(row.values()),
        )


# --- the owner's approval -------------------------------------------------------------------


def test_a_bill_within_the_approval_limit_is_set_aside_without_approval(
    conn, app_conn, pmc, funded
):
    bill_id = bill(conn, pmc, funded, amount="500.00")

    set_aside_bill(app_conn, pmc.pmc_id, bill_id, at(JAN_15))

    assert balance(conn, funded) == Decimal("500.00")
    assert balance(conn, pmc.vendor_payable) == Decimal("500.00")
    assert memos(conn, funded, pmc.vendor_payable) == [(Decimal("500.00"), "Bill INV-1 set aside")]


def test_a_bill_over_the_limit_waits_for_the_owners_approval(conn, app_conn, pmc, funded):
    bill_id = bill(conn, pmc, funded, amount="500.01")

    with pytest.raises(psycopg.errors.CheckViolation, match="approval limit of 500"):
        set_aside_bill(conn, pmc.pmc_id, bill_id, at(JAN_15))
    assert steps(conn, bill_id) == []

    approve_bill(app_conn, pmc.pmc_id, bill_id, JAN_15, "Owner 1, by email")
    set_aside_bill(conn, pmc.pmc_id, bill_id, at(JAN_15))
    assert balance(conn, pmc.vendor_payable) == Decimal("500.01")


def test_without_an_agreement_every_bill_needs_approval(conn, pmc, owner):
    transfer_batch(conn, [(pmc.operating_cash, owner, "1.00")], at(JAN_1))
    bill_id = bill(conn, pmc, owner, amount="0.01")

    with pytest.raises(psycopg.errors.CheckViolation, match="approval limit of 0"):
        set_aside_bill(conn, pmc.pmc_id, bill_id, at(JAN_15))


def test_the_limit_in_force_on_the_bills_date_applies(conn, pmc, owner):
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, approval_limit="100")
    add_agreement(conn, pmc.pmc_id, owner, FEB_1, approval_limit="1000")
    transfer_batch(conn, [(pmc.operating_cash, owner, "2000.00")], at(JAN_1))
    january = bill(conn, pmc, owner, amount="500.00", reference="INV-1", bill_date=JAN_15)
    february = bill(conn, pmc, owner, amount="500.00", reference="INV-2", bill_date=FEB_1)

    # Set aside in February, yet January's bill keeps January's limit.
    with pytest.raises(psycopg.errors.CheckViolation, match="approval limit of 100"):
        set_aside_bill(conn, pmc.pmc_id, january, at(FEB_1))
    set_aside_bill(conn, pmc.pmc_id, february, at(FEB_1))


def test_agreements_made_before_the_limit_existed_need_approval_for_every_bill(conn, pmc, owner):
    # The column's default, as the migration gives agreements that are already there.
    conn.execute(
        "INSERT INTO trust_management_agreements (pmc_id, ledger_account_id, starts_on)"
        " VALUES (%s, %s, %s)",
        (pmc.pmc_id, owner, JAN_1),
    )
    transfer_batch(conn, [(pmc.operating_cash, owner, "1.00")], at(JAN_1))

    with pytest.raises(psycopg.errors.CheckViolation, match="approval limit of 0"):
        set_aside_bill(conn, pmc.pmc_id, bill(conn, pmc, owner, amount="0.01"), at(JAN_15))


def test_an_approval_is_for_a_bill_of_its_pmc_says_who_gave_it_and_is_given_once(conn, pmc, funded):
    bill_id = bill(conn, pmc, funded)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        approve_bill(conn, make_pmc(conn).pmc_id, bill_id, JAN_15, "Someone else")
    with pytest.raises(psycopg.errors.CheckViolation):
        approve_bill(conn, pmc.pmc_id, bill_id, JAN_15, "")
    approve_bill(conn, pmc.pmc_id, bill_id, JAN_15, "Owner 1, by phone")
    with pytest.raises(psycopg.errors.UniqueViolation):
        approve_bill(conn, pmc.pmc_id, bill_id, JAN_15, "Owner 1, again")


# --- setting a bill aside ------------------------------------------------------------------


def test_setting_a_bill_aside_again_returns_its_transfer_and_moves_money_once(conn, pmc, funded):
    bill_id = bill(conn, pmc, funded)
    first = set_aside_bill(conn, pmc.pmc_id, bill_id, at(JAN_15))

    assert set_aside_bill(conn, pmc.pmc_id, bill_id, at(FEB_1)) == first
    assert memos(conn, funded, pmc.vendor_payable) == [(Decimal("250.00"), "Bill INV-1 set aside")]


def test_a_bill_the_owner_cannot_cover_is_refused_and_nothing_written(conn, pmc, funded):
    bill_id = bill(conn, pmc, funded, amount="1000.01")
    approve_bill(conn, pmc.pmc_id, bill_id, JAN_15, "Owner 1, by email")

    with pytest.raises(psycopg.errors.CheckViolation, match="below zero"):
        set_aside_bill(conn, pmc.pmc_id, bill_id, at(JAN_15))
    assert steps(conn, bill_id) == []


def test_a_bill_can_use_the_reserve(conn, pmc, funded):
    # The reserve is kept back from the owner for bills like this one.
    bill_id = bill(conn, pmc, funded, amount="1000.00")
    approve_bill(conn, pmc.pmc_id, bill_id, JAN_15, "Owner 1, by email")

    set_aside_bill(conn, pmc.pmc_id, bill_id, at(JAN_15))
    assert balance(conn, funded) == 0


def test_setting_aside_needs_the_vendors_account_in_the_owners_bank_account(conn, pmc, funded):
    vendor = conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor 2') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    bill_id = bill(conn, pmc, funded, vendor=vendor)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="open the vendor's account"):
        set_aside_bill(conn, pmc.pmc_id, bill_id, at(JAN_15))


@pytest.mark.parametrize("step", ["set aside", "pay"])
def test_a_bill_of_another_pmc_or_none_is_refused(conn, pmc, funded, step):
    act = set_aside_bill if step == "set aside" else pay_bill
    bill_id = bill(conn, pmc, funded)

    for pmc_id, wanted in [(make_pmc(conn).pmc_id, bill_id), (pmc.pmc_id, uuid.uuid4())]:
        with pytest.raises(psycopg.errors.InvalidParameterValue, match="no bill"):
            act(conn, pmc_id, wanted, at(JAN_15))


# --- paying a bill -------------------------------------------------------------------------


def test_paying_a_bill_sends_the_money_set_aside_out_through_cash(conn, app_conn, pmc, funded):
    bill_id = bill(conn, pmc, funded)
    set_aside_bill(conn, pmc.pmc_id, bill_id, at(JAN_15))

    paid = pay_bill(app_conn, pmc.pmc_id, bill_id, at(JAN_15, 13))

    assert pay_bill(conn, pmc.pmc_id, bill_id, at(FEB_1)) == paid  # a retry
    assert balance(conn, pmc.vendor_payable) == 0
    assert balance(conn, funded) == Decimal("750.00")
    assert memos(conn, pmc.vendor_payable, pmc.operating_cash) == [
        (Decimal("250.00"), "Bill INV-1 paid")
    ]
    assert steps(conn, bill_id) == [("paid",), ("set_aside",)]


def test_a_bill_is_set_aside_before_it_is_paid(conn, pmc, funded):
    bill_id = bill(conn, pmc, funded)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="not set aside"):
        pay_bill(conn, pmc.pmc_id, bill_id, at(JAN_15))

    set_aside_bill(conn, pmc.pmc_id, bill_id, at(JAN_15))
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="paid before that"):
        pay_bill(conn, pmc.pmc_id, bill_id, at(JAN_15, 11, 59))
    pay_bill(conn, pmc.pmc_id, bill_id, at(JAN_15))  # the same moment is fine


def test_a_payment_with_no_date_is_dated_now_and_not_before_the_set_aside(conn, pmc, funded):
    later = datetime(2099, 1, 1, tzinfo=UTC)  # set aside ahead of time
    bill_id = bill(conn, pmc, funded)
    set_aside_bill(conn, pmc.pmc_id, bill_id, later)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="paid before that"):
        pay_bill(conn, pmc.pmc_id, bill_id, None)
    pay_bill(conn, pmc.pmc_id, bill_id, later)


def test_a_bill_is_not_set_aside_or_paid_in_a_reconciled_period(conn, pmc, funded):
    bill_id = bill(conn, pmc, funded)
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, at(JAN_1, 0), at(FEB_1, 0))

    with pytest.raises(psycopg.errors.CheckViolation, match="reconciled and closed"):
        set_aside_bill(conn, pmc.pmc_id, bill_id, at(JAN_15))
    set_aside_bill(conn, pmc.pmc_id, bill_id, at(FEB_1))
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, at(FEB_1, 0), at(FEB_1, 13))
    with pytest.raises(psycopg.errors.CheckViolation, match="reconciled and closed"):
        pay_bill(conn, pmc.pmc_id, bill_id, at(FEB_1))
    pay_bill(conn, pmc.pmc_id, bill_id, at(FEB_1, 13))


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
@pytest.mark.parametrize("step", ["set aside", "pay"])
def test_bills_are_set_aside_and_paid_at_read_committed(
    conn, database_url, pmc, funded, isolation, step
):
    act = set_aside_bill if step == "set aside" else pay_bill
    bill_id = bill(conn, pmc, funded)
    with psycopg.connect(database_url) as tx:
        tx.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
        with pytest.raises(psycopg.errors.InvalidTransactionState, match="READ COMMITTED"):
            act(tx, pmc.pmc_id, bill_id, at(JAN_15))


@pytest.mark.timing
@pytest.mark.parametrize("step", ["set aside", "pay"])
def test_the_same_step_taken_twice_at_once_moves_money_once(conn, database_url, pmc, funded, step):
    act, source, target = (
        (set_aside_bill, funded, pmc.vendor_payable)
        if step == "set aside"
        else (pay_bill, pmc.vendor_payable, pmc.operating_cash)
    )
    bill_id = bill(conn, pmc, funded)
    if step == "pay":
        set_aside_bill(conn, pmc.pmc_id, bill_id, at(JAN_15))

    with psycopg.connect(database_url) as first:
        taken = act(first, pmc.pmc_id, bill_id, at(JAN_15))  # not committed yet
        thread, pid, outcome = in_background(
            database_url, lambda worker: act(worker, pmc.pmc_id, bill_id, at(JAN_15))
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert outcome == {"result": taken}
    assert len(memos(conn, source, target)) == 1


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE trust_bills SET memo = 'x'",
        "DELETE FROM trust_bill_approvals",
        "TRUNCATE trust_bill_payments",
    ],
)
def test_bills_and_their_steps_are_kept_for_good(conn, pmc, funded, statement):
    bill_id = bill(conn, pmc, funded)
    approve_bill(conn, pmc.pmc_id, bill_id, JAN_15, "Owner 1, by email")
    set_aside_bill(conn, pmc.pmc_id, bill_id, at(JAN_15))

    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)


# --- draws keep unpaid bills back -----------------------------------------------------------


def test_a_draw_keeps_back_unpaid_bills_on_top_of_the_reserve(conn, pmc, funded):
    bill(conn, pmc, funded, amount="300.00", bill_date=JAN_15)
    bill(conn, pmc, funded, amount="50.00", reference="INV-2", bill_date=FEB_1)  # not yet owed

    with pytest.raises(psycopg.errors.CheckViolation, match="unpaid bills of 300.00"):
        draw_owner(conn, pmc.pmc_id, funded, "d1", "600.01", at(JAN_15))
    assert draw_owner(conn, pmc.pmc_id, funded, "d1", None, at(JAN_15)) == Decimal("600.00")


def test_a_bill_set_aside_is_no_longer_kept_back_twice(conn, pmc, funded):
    bill_id = bill(conn, pmc, funded, amount="300.00")
    set_aside_bill(conn, pmc.pmc_id, bill_id, at(JAN_15))

    # 700.00 held, less the 100.00 reserve: the bill's money already left the account.
    assert draw_owner(conn, pmc.pmc_id, funded, "d1", None, at(JAN_15)) == Decimal("600.00")
