"""How a payout left trust (migration 0068, ADR 0006): a row of trust_payout_methods says an owner
draw or a bill's payment of the PMC was sent by check or by EFT, once per payout; nothing else
takes one, it is append-only and audited, and it moves no money."""

import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from helpers import (
    approve_bill,
    contribute,
    draw_owner,
    enter_bill,
    make_pmc,
    pay_bill,
    set_aside_bill,
)

NOW = datetime.now(UTC)
TODAY = NOW.date()


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def method(conn, pmc, transfer, how="check"):
    conn.execute(
        "INSERT INTO trust_payout_methods (pmc_id, transfer_id, method) VALUES (%s, %s, %s)",
        (pmc.pmc_id, transfer, how),
    )


def a_draw(conn, pmc):
    account = pmc.owners[0].account
    contribute(conn, pmc.pmc_id, account, f"in/{uuid.uuid4()}", "100.00")
    key = f"draw/{uuid.uuid4()}"
    draw_owner(conn, pmc.pmc_id, account, key, "40.00", NOW)
    return conn.execute(
        "SELECT transfer_id FROM trust_owner_draws WHERE pmc_id = %s AND request_key = %s",
        (pmc.pmc_id, key),
    ).fetchone()[0]


def test_an_owner_draw_and_a_bill_paid_each_take_one(conn, pmc):
    draw = a_draw(conn, pmc)
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, "INV-1", TODAY, TODAY, "10.00"
    )
    approve_bill(conn, pmc.pmc_id, bill, TODAY, "Owner, by phone")
    set_aside = set_aside_bill(conn, pmc.pmc_id, bill, NOW)
    paid = pay_bill(conn, pmc.pmc_id, bill, NOW)

    method(conn, pmc, draw, "EFT")
    method(conn, pmc, paid)

    assert conn.execute(
        "SELECT transfer_id, method FROM trust_payout_methods WHERE pmc_id = %s"
        ' ORDER BY method COLLATE "C"',
        (pmc.pmc_id,),
    ).fetchall() == [(draw, "EFT"), (paid, "check")]
    # Once per payout.
    with pytest.raises(psycopg.errors.UniqueViolation):
        method(conn, pmc, draw)
    # A bill set aside hasn't left trust.
    with pytest.raises(psycopg.errors.CheckViolation, match="not an owner draw or a bill paid"):
        method(conn, pmc, set_aside)


def test_nothing_else_takes_one(conn, pmc):
    contribution = contribute(conn, pmc.pmc_id, pmc.owners[0].account, "in/1", "5.00")
    with pytest.raises(psycopg.errors.CheckViolation, match="not an owner draw or a bill paid"):
        method(conn, pmc, contribution)
    with pytest.raises(psycopg.errors.CheckViolation, match="trust_payout_methods_method_check"):
        method(conn, pmc, a_draw(conn, pmc), "wire")


def test_another_companys_draw_takes_none_of_ours(conn, pmc):
    theirs = a_draw(conn, make_pmc(conn))
    with pytest.raises(psycopg.errors.CheckViolation, match="not an owner draw or a bill paid"):
        method(conn, pmc, theirs)


def test_a_payout_method_is_never_rewritten(conn, pmc):
    draw = a_draw(conn, pmc)
    method(conn, pmc, draw)
    for change in (
        "UPDATE trust_payout_methods SET method = 'EFT'",
        "DELETE FROM trust_payout_methods",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(change + " WHERE transfer_id = %s", (draw,))
