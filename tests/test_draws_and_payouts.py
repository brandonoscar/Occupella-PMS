"""Owner draws prepared on the company's draw day, and payout runs (ADR 0010 phase 1b, migration
0153). trust_prepare_owner_draws prepares each owner's draw once a month, on or after the draw day:
what each property account has available then (its balance less its reserve and the bills not yet
paid, as trust_draw_owner counts it) and how its owner is paid. Nothing is paid. A payout run's
items are draws of its owners or bills paid to its vendors, out of the run's trust bank account.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    add_agreement,
    add_staff,
    draw_owner,
    enter_bill,
    make_pmc,
    pay_bill,
    transfer_batch,
)

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)


def at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def routing(prefix8: str) -> str:
    """A synthetic routing number: the prefix 00 (the U.S. government's, never a bank's) with a
    check digit that holds."""
    d = [int(c) for c in prefix8]
    for c in range(10):
        n = [*d, c]
        if (3 * (n[0] + n[3] + n[6]) + 7 * (n[1] + n[4] + n[7]) + n[2] + n[5] + n[8]) % 10 == 0:
            return prefix8 + str(c)
    raise AssertionError


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def set_draw_day(conn, pmc, day):
    conn.execute(
        "INSERT INTO trust_owner_draw_settings (pmc_id, draw_day) VALUES (%s, %s)",
        (pmc.pmc_id, day),
    )


def prepare(conn, pmc, day):
    return conn.execute("SELECT trust_prepare_owner_draws(%s, %s)", (pmc.pmc_id, day)).fetchone()[0]


def lines(conn, run):
    return conn.execute(
        "SELECT ledger_account_id, amount, method FROM trust_draw_run_lines WHERE run_id = %s"
        " ORDER BY ledger_account_id",
        (run,),
    ).fetchall()


def test_a_routing_number_is_nine_digits_whose_check_digit_holds(conn):
    ok = routing("00123456")
    assert conn.execute("SELECT trust_routing_number_ok(%s)", (ok,)).fetchone()[0]
    wrong = ok[:8] + str((int(ok[8]) + 1) % 10)
    for number in (wrong, "12345678", "1234567890", "00123456a"):
        assert not conn.execute("SELECT trust_routing_number_ok(%s)", (number,)).fetchone()[0]


def test_no_draw_day_prepares_nothing_and_a_day_not_come_yet_neither(conn, pmc):
    assert prepare(conn, pmc, date(2026, 10, 20)) is None
    set_draw_day(conn, pmc, 25)
    assert prepare(conn, pmc, date(2026, 10, 20)) is None
    set_draw_day(conn, pmc, None)
    assert prepare(conn, pmc, date(2026, 10, 26)) is None


def test_the_draw_day_prepares_what_each_account_has_available_once_a_month(conn, pmc):
    first, second = pmc.owners
    add_agreement(conn, pmc.pmc_id, first.account, date(2026, 1, 1), reserve="100.00")
    transfer_batch(
        conn,
        [
            (pmc.operating_cash, first.account, "900.00"),
            (pmc.operating_cash, second.account, "50.00"),
        ],
        at(date(2026, 10, 1)),
    )
    # A bill dated by the draw day and due after it holds its amount back too.
    enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        first.account,
        "LATER-1",
        date(2026, 10, 10),
        date(2026, 11, 30),
        "300.00",
    )
    set_draw_day(conn, pmc, 15)
    conn.execute(
        "INSERT INTO trust_owner_payout_methods (pmc_id, owner_id, method) VALUES (%s, %s, 'EFT')",
        (pmc.pmc_id, second.owner_id),
    )

    run = prepare(conn, pmc, date(2026, 10, 15))

    assert sorted((a, amount, method) for a, amount, method in lines(conn, run)) == sorted(
        [(first.account, Decimal("500.00"), "check"), (second.account, Decimal("50.00"), "EFT")]
    )
    # Nothing was paid.
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_owner_draws WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone()[0]
        == 0
    )
    # Once a month: the next night finds the month's run; the next month makes another.
    assert prepare(conn, pmc, date(2026, 10, 16)) is None
    assert prepare(conn, pmc, date(2026, 11, 15)) is not None


def test_a_missed_draw_day_is_prepared_the_first_night_after_and_an_account_with_nothing_isnt(
    conn, pmc
):
    first, second = pmc.owners
    transfer_batch(conn, [(pmc.operating_cash, first.account, "75.00")], at(date(2026, 9, 1)))
    set_draw_day(conn, pmc, 5)

    run = prepare(conn, pmc, date(2026, 9, 9))

    assert conn.execute(
        "SELECT month, prepared_on FROM trust_draw_runs WHERE id = %s", (run,)
    ).fetchone() == (date(2026, 9, 1), date(2026, 9, 9))
    assert [line[0] for line in lines(conn, run)] == [first.account]


def test_a_prepared_run_is_paid_once(conn, pmc):
    transfer_batch(
        conn, [(pmc.operating_cash, pmc.owners[0].account, "80.00")], at(date(2026, 8, 1))
    )
    set_draw_day(conn, pmc, 1)
    run = prepare(conn, pmc, date(2026, 8, 1))
    conn.execute(
        "INSERT INTO trust_draw_run_approvals (run_id, pmc_id, request_id)"
        " VALUES (%s, %s, gen_random_uuid())",
        (run, pmc.pmc_id),
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO trust_draw_run_approvals (run_id, pmc_id, request_id)"
            " VALUES (%s, %s, gen_random_uuid())",
            (run, pmc.pmc_id),
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE trust_owner_draw_settings SET draw_day = 2",
        "DELETE FROM trust_draw_runs",
        "UPDATE trust_draw_run_lines SET amount = 1",
        "DELETE FROM trust_payee_bank_accounts",
        "UPDATE trust_payout_runs SET method = 'check'",
        "TRUNCATE trust_payout_file_downloads",
    ],
)
def test_draws_payees_and_runs_are_append_only(conn, statement):
    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)


def bank_row(conn, pmc, owner=None, vendor=None, number=None):
    return conn.execute(
        "INSERT INTO trust_payee_bank_accounts (pmc_id, owner_id, vendor_id, routing_number,"
        " account_type, account_sealed, account_last4, holder_name)"
        " VALUES (%s, %s, %s, %s, 'checking', %s, '6789', 'Owner 1') RETURNING id",
        (pmc.pmc_id, owner, vendor, number or routing("00123456"), b"\x01" + b"x" * 40),
    ).fetchone()[0]


def test_a_payees_bank_account_is_one_persons_with_a_routing_number_that_checks(conn, pmc):
    assert bank_row(conn, pmc, owner=pmc.owners[0].owner_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        bank_row(conn, pmc, owner=pmc.owners[0].owner_id, vendor=pmc.vendor_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        bank_row(conn, pmc, vendor=pmc.vendor_id, number="123456789")


def test_a_banks_ach_details_are_all_there_or_none(conn, pmc):
    conn.execute(
        "INSERT INTO trust_bank_payout_settings (pmc_id, bank_account_id, first_check_number)"
        " VALUES (%s, %s, 1001)",
        (pmc.pmc_id, pmc.operating_bank_id),
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_bank_payout_settings (pmc_id, bank_account_id, bank_routing_number)"
            " VALUES (%s, %s, %s)",
            (pmc.pmc_id, pmc.operating_bank_id, routing("00123456")),
        )


def run_row(conn, pmc, kind, method="EFT"):
    return conn.execute(
        "INSERT INTO trust_payout_runs (pmc_id, bank_account_id, kind, method, paid_on, request_id)"
        " VALUES (%s, %s, %s, %s, %s, gen_random_uuid()) RETURNING id",
        (pmc.pmc_id, pmc.operating_bank_id, kind, method, TODAY),
    ).fetchone()[0]


def test_a_runs_items_are_its_kinds_payouts_to_their_payee(conn, pmc):
    owner = pmc.owners[0]
    transfer_batch(conn, [(pmc.operating_cash, owner.account, "500.00")], at(TODAY - 5 * DAY))
    draw_owner(conn, pmc.pmc_id, owner.account, "draw-1", "100.00", at(TODAY))
    drawn = conn.execute(
        "SELECT transfer_id FROM trust_owner_draws WHERE request_key = 'draw-1'"
    ).fetchone()[0]
    add_agreement(conn, pmc.pmc_id, owner.account, TODAY - 30 * DAY, approval_limit="1000")
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, owner.account, "RUN-1", TODAY - DAY, TODAY, "40.00"
    )
    paid = pay_bill(conn, pmc.pmc_id, bill, at(TODAY))
    owners_run, bills_run = run_row(conn, pmc, "owners"), run_row(conn, pmc, "bills")
    item = (
        "INSERT INTO trust_payout_run_items (run_id, pmc_id, transfer_id, owner_id, vendor_id)"
        " VALUES (%s, %s, %s, %s, %s)"
    )

    conn.execute(item, (owners_run, pmc.pmc_id, drawn, owner.owner_id, None))
    conn.execute(item, (bills_run, pmc.pmc_id, paid, None, pmc.vendor_id))

    for refused in (
        (owners_run, pmc.pmc_id, paid, owner.owner_id, None),  # a bill in an owners' run
        (bills_run, pmc.pmc_id, drawn, None, pmc.vendor_id),  # a draw in a bills' run
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match="is not a"):
            conn.execute(item, refused)
    # A transfer is in one run.
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            item, (run_row(conn, pmc, "owners", "check"), pmc.pmc_id, drawn, owner.owner_id, None)
        )


def test_settings_and_payees_name_who_recorded_them(conn, pmc):
    staff = add_staff(conn, pmc.pmc_id)
    with acting_as(conn, staff):
        set_draw_day(conn, pmc, 10)
        row = bank_row(conn, pmc, owner=pmc.owners[0].owner_id)
    assert (
        conn.execute(
            "SELECT recorded_by FROM trust_owner_draw_settings WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone()[0]
        == staff
    )
    assert (
        conn.execute(
            "SELECT recorded_by FROM trust_payee_bank_accounts WHERE id = %s", (row,)
        ).fetchone()[0]
        == staff
    )
