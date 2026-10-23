"""Checks in the database (migration 20261022000157): where an account's checks print, runs dated
the day printed, each check a payout recorded as sent by check from the run's account for its
whole amount, numbered once per account (a void's number too), on one check not voided, every run
whole at commit; append-only, audited, disburse for a named staff member; the AI role reads none
of it. The API's side is in tests/api/test_checks_api.py."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    ROLES,
    add_staff,
    approve_bill,
    contribute,
    draw_owner,
    enter_bill,
    give_roles,
    make_pmc,
    pay_bill,
    set_aside_bill,
)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def method(conn, pmc, transfer, how="check"):
    conn.execute(
        "INSERT INTO trust_payout_methods (pmc_id, transfer_id, method) VALUES (%s, %s, %s)",
        (pmc.pmc_id, transfer, how),
    )
    return transfer


def a_draw(conn, pmc, amount="40.00", how="check"):
    account = pmc.owners[0].account
    contribute(conn, pmc.pmc_id, account, f"in/{uuid.uuid4()}", "500.00")
    key = f"draw/{uuid.uuid4()}"
    draw_owner(conn, pmc.pmc_id, account, key, amount, datetime.now(UTC))
    transfer = conn.execute(
        "SELECT transfer_id FROM trust_owner_draws WHERE pmc_id = %s AND request_key = %s",
        (pmc.pmc_id, key),
    ).fetchone()[0]
    return method(conn, pmc, transfer, how)


def a_bill_paid(conn, pmc, amount="10.00"):
    today = datetime.now(UTC).date()
    contribute(conn, pmc.pmc_id, pmc.owners[0].account, f"in/{uuid.uuid4()}", amount)
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, f"INV-{uuid.uuid4().hex[:6]}",
        today, today, amount,
    )  # fmt: skip
    approve_bill(conn, pmc.pmc_id, bill, today, "Owner, by phone")
    set_aside_bill(conn, pmc.pmc_id, bill, datetime.now(UTC))
    return method(conn, pmc, pay_bill(conn, pmc.pmc_id, bill, datetime.now(UTC)))


def print_run(conn, pmc, items, first=1001, bank=None, checks=None, total=None):
    """A run and its checks (transfer, amount), numbered from first, in one transaction."""
    bank = bank or pmc.operating_bank_id
    with conn.transaction():
        run_id = conn.execute(
            "INSERT INTO trust_check_runs (pmc_id, bank_account_id, checks, total)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (
                pmc.pmc_id,
                bank,
                len(items) if checks is None else checks,
                sum(Decimal(a) for _, a in items) if total is None else total,
            ),
        ).fetchone()[0]
        made = []
        for number, (transfer, amount) in enumerate(items, start=first):
            made.append(
                conn.execute(
                    "INSERT INTO trust_checks (pmc_id, run_id, bank_account_id, check_number,"
                    " transfer_id, amount) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (pmc.pmc_id, run_id, bank, number, transfer, amount),
                ).fetchone()[0]
            )
    return run_id, made


def void(conn, pmc, check_id, reason="Spoiled in the printer"):
    conn.execute(
        "INSERT INTO trust_check_voids (pmc_id, check_id, reason) VALUES (%s, %s, %s)",
        (pmc.pmc_id, check_id, reason),
    )


def test_a_run_prints_payouts_by_check_numbered_from_the_first_sheet(conn, pmc):
    draw, bill = a_draw(conn, pmc, "40.00"), a_bill_paid(conn, pmc, "10.00")

    run_id, _ = print_run(conn, pmc, [(draw, "40.00"), (bill, "10.00")], first=1001)

    assert conn.execute(
        "SELECT dated_on, checks, total FROM trust_check_runs WHERE id = %s", (run_id,)
    ).fetchone() == (datetime.now(UTC).date(), 2, Decimal("50.00"))
    assert conn.execute(
        "SELECT check_number, transfer_id FROM trust_checks WHERE run_id = %s ORDER BY 1",
        (run_id,),
    ).fetchall() == [(1001, draw), (1002, bill)]


def test_a_check_is_a_payout_by_check_of_its_account_for_its_whole_amount(conn, pmc):
    by_eft = a_draw(conn, pmc, how="EFT")
    draw = a_draw(conn, pmc, "40.00")
    other = make_pmc(conn)

    for items, refusal, bank in (
        ([(by_eft, "40.00")], "recorded as sent by check", None),
        ([(a_draw(conn, other), "40.00")], "recorded as sent by check", None),
        ([(draw, "40.00")], "from its own trust bank account", pmc.deposit_bank_id),
        ([(draw, "39.99")], "whole amount, 40.00", None),
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match=refusal):
            print_run(conn, pmc, items, bank=bank)
    assert conn.execute(
        "SELECT count(*) FROM trust_check_runs WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_a_number_is_used_once_an_account_and_a_payout_is_on_one_check_not_voided(conn, pmc):
    draw = a_draw(conn, pmc)
    _, (first,) = print_run(conn, pmc, [(draw, "40.00")], first=1001)

    with pytest.raises(psycopg.errors.CheckViolation, match="on a check already"):
        print_run(conn, pmc, [(draw, "40.00")], first=1002)
    void(conn, pmc, first)
    # A void's number stays used: the payout is printed again under a new one.
    with pytest.raises(psycopg.errors.UniqueViolation):
        print_run(conn, pmc, [(draw, "40.00")], first=1001)
    _, (second,) = print_run(conn, pmc, [(draw, "40.00")], first=1002)
    with pytest.raises(psycopg.errors.UniqueViolation):
        void(conn, pmc, first)
    with pytest.raises(psycopg.errors.CheckViolation):
        void(conn, pmc, second, reason="")


def test_a_run_comes_to_its_count_and_total_and_takes_nothing_after(conn, pmc):
    draw, later = a_draw(conn, pmc, "40.00"), a_draw(conn, pmc, "5.00")

    for checks, total in ((2, 40), (1, 45)):
        with pytest.raises(psycopg.errors.CheckViolation, match="it says"):
            print_run(conn, pmc, [(draw, "40.00")], checks=checks, total=total)
    run_id, _ = print_run(conn, pmc, [(draw, "40.00")])
    with pytest.raises(psycopg.errors.CheckViolation, match="it says 1 of 40"):
        conn.execute(
            "INSERT INTO trust_checks (pmc_id, run_id, bank_account_id, check_number,"
            " transfer_id, amount) VALUES (%s, %s, %s, 2001, %s, 5.00)",
            (pmc.pmc_id, run_id, pmc.operating_bank_id, later),
        )


def test_settings_are_where_checks_sit_and_how_far_printing_moves(conn, pmc):
    def keep(position, right=0, down=0):
        conn.execute(
            "INSERT INTO trust_check_settings (pmc_id, bank_account_id, position, shift_right,"
            " shift_down) VALUES (%s, %s, %s, %s, %s)",
            (pmc.pmc_id, pmc.operating_bank_id, position, right, down),
        )

    keep("middle", 36, -36)
    for bad in (("side",), ("top", 37), ("top", 0, -37)):
        with pytest.raises(psycopg.errors.CheckViolation):
            keep(*bad)


def test_rows_are_never_changed_and_every_one_is_audited(conn, pmc):
    conn.execute(
        "INSERT INTO trust_check_settings (pmc_id, bank_account_id, position, shift_right,"
        " shift_down) VALUES (%s, %s, 'top', 0, 0)",
        (pmc.pmc_id, pmc.operating_bank_id),
    )
    _, (check,) = print_run(conn, pmc, [(a_draw(conn, pmc), "40.00")])
    void(conn, pmc, check)

    for table in ("trust_check_settings", "trust_check_runs", "trust_checks",
                  "trust_check_voids"):  # fmt: skip
        for change in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with pytest.raises(psycopg.errors.RestrictViolation), conn.transaction():
                conn.execute(change)
        assert conn.execute(
            "SELECT count(*) FROM trust_audit_log WHERE table_name = %s AND pmc_id = %s",
            (table, pmc.pmc_id),
        ).fetchone() == (1,)


def test_a_named_staff_member_needs_disburse(conn, pmc):
    clerk = add_staff(conn, pmc.pmc_id, "Clerk")
    give_roles(conn, pmc.pmc_id, clerk, [r for r in ROLES if r != "disburse"])
    payer = add_staff(conn, pmc.pmc_id, "Payer")
    give_roles(conn, pmc.pmc_id, payer, ["disburse"])
    draw = a_draw(conn, pmc)

    def as_staff(staff_id, write):
        with conn.transaction():
            conn.execute("SELECT set_config('trust.staff_id', %s, true)", (str(staff_id),))
            return write()

    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="disburse"):
        as_staff(clerk, lambda: print_run(conn, pmc, [(draw, "40.00")]))
    run_id, (check,) = as_staff(payer, lambda: print_run(conn, pmc, [(draw, "40.00")]))
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="disburse"):
        as_staff(clerk, lambda: void(conn, pmc, check))
    as_staff(payer, lambda: void(conn, pmc, check))
    assert conn.execute(
        "SELECT (SELECT recorded_by FROM trust_check_runs WHERE id = %s),"
        " (SELECT recorded_by FROM trust_check_voids WHERE check_id = %s)",
        (run_id, check),
    ).fetchone() == (payer, payer)


def test_the_ai_role_reads_none_of_it(ai_conn):
    for table in ("trust_check_settings", "trust_check_runs", "trust_checks",
                  "trust_check_voids"):  # fmt: skip
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ai_conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
