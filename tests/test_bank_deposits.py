"""Bank deposits (migration 20261018000078): what staff take to the bank together. Each item is a
transfer that brought money into its deposit's trust bank account by the deposit's day, in at
most one deposit; deposits are append-only, audited and name who recorded them. They move no
money."""

import uuid
from datetime import UTC, date, datetime

import psycopg
import pytest
from helpers import acting_as, add_staff, make_pmc, post

JAN_5 = date(2026, 1, 5)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def moved(conn, pmc, requests, day=JAN_5):
    return post(
        conn,
        pmc.pmc_id,
        f"move-{uuid.uuid4()}",
        requests,
        datetime(day.year, day.month, day.day, 12, tzinfo=UTC),
        {"memo": "Synthetic"},
    )


def deposit(conn, pmc_id, bank, day=JAN_5, by=None):
    with acting_as(conn, by):
        return conn.execute(
            "INSERT INTO trust_bank_deposits (pmc_id, bank_account_id, deposited_on)"
            " VALUES (%s, %s, %s) RETURNING id",
            (pmc_id, bank, day),
        ).fetchone()[0]


def put(conn, pmc_id, deposit_id, transfer):
    conn.execute(
        "INSERT INTO trust_bank_deposit_items (deposit_id, pmc_id, transfer_id)"
        " VALUES (%s, %s, %s)",
        (deposit_id, pmc_id, transfer),
    )


def test_a_deposit_holds_what_came_into_its_account_by_its_day(conn, pmc):
    rent, prepaid = moved(
        conn,
        pmc,
        [
            (pmc.operating_cash, pmc.owners[0].account, "1500.00"),
            (pmc.operating_cash, pmc.prepaid_rent, "50.00"),
        ],
    )
    made = deposit(conn, pmc.pmc_id, pmc.operating_bank_id)
    put(conn, pmc.pmc_id, made, rent)
    put(conn, pmc.pmc_id, made, prepaid)
    assert conn.execute(
        "SELECT count(*) FROM trust_bank_deposit_items WHERE deposit_id = %s", (made,)
    ).fetchone() == (2,)
    # In one deposit only.
    other = deposit(conn, pmc.pmc_id, pmc.operating_bank_id)
    with pytest.raises(psycopg.errors.UniqueViolation):
        put(conn, pmc.pmc_id, other, rent)


def test_money_out_another_accounts_money_or_a_later_day_is_refused(conn, pmc):
    (paid_in,) = moved(conn, pmc, [(pmc.operating_cash, pmc.owners[0].account, "900.00")])
    (paid_out,) = moved(conn, pmc, [(pmc.owners[0].account, pmc.operating_cash, "100.00")])
    (held,) = moved(conn, pmc, [(pmc.deposit_cash, pmc.tenant_deposit, "800.00")])
    (later,) = moved(
        conn, pmc, [(pmc.operating_cash, pmc.owners[1].account, "10.00")], date(2026, 1, 6)
    )
    made = deposit(conn, pmc.pmc_id, pmc.operating_bank_id)
    for transfer, why in (
        (paid_out, "brought no money into"),
        (held, "brought no money into"),
        (later, "came in after the deposit"),
    ):
        with conn.transaction(), pytest.raises(psycopg.errors.CheckViolation, match=why):
            put(conn, pmc.pmc_id, made, transfer)
    put(conn, pmc.pmc_id, made, paid_in)
    # The security deposit goes in a deposit of its own account.
    put(conn, pmc.pmc_id, deposit(conn, pmc.pmc_id, pmc.deposit_bank_id), held)


def test_a_deposit_and_its_items_are_of_one_company(conn, pmc):
    other = make_pmc(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        deposit(conn, pmc.pmc_id, other.operating_bank_id)
    (theirs,) = moved(conn, other, [(other.operating_cash, other.owners[0].account, "5.00")])
    made = deposit(conn, pmc.pmc_id, pmc.operating_bank_id)
    with conn.transaction(), pytest.raises(psycopg.errors.CheckViolation):
        put(conn, pmc.pmc_id, made, theirs)  # moved no cash of this company's account
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        put(conn, other.pmc_id, made, theirs)  # the deposit isn't theirs


def test_deposits_are_append_only_audited_and_name_who_recorded_them(conn, pmc):
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    (paid_in,) = moved(conn, pmc, [(pmc.operating_cash, pmc.owners[0].account, "900.00")])
    made = deposit(conn, pmc.pmc_id, pmc.operating_bank_id, by=clerk)
    with acting_as(conn, clerk):
        put(conn, pmc.pmc_id, made, paid_in)
    assert conn.execute(
        "SELECT recorded_by FROM trust_bank_deposits WHERE id = %s", (made,)
    ).fetchone() == (clerk,)
    assert conn.execute(
        "SELECT table_name, staff_id FROM trust_audit_log WHERE pmc_id = %s"
        " AND table_name LIKE 'trust_bank_deposit%%' ORDER BY id",
        (pmc.pmc_id,),
    ).fetchall() == [("trust_bank_deposits", clerk), ("trust_bank_deposit_items", clerk)]
    for table in ("trust_bank_deposits", "trust_bank_deposit_items"):
        for query in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
                conn.execute(query)
