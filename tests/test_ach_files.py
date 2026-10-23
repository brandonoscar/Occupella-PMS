"""Bank files in the database (migration 20261022000156): a trust bank account's ACH settings, its
files (made under the latest settings, for a weekday not before today, each day's modifiers in
turn) and their items (EFT payouts from that account, into the payee's latest bank account, for
the whole amount, in one file not voided), whole at commit; voids free a file's payouts;
append-only, audited, disburse for a named staff member; the AI role reads none of it. The API's
side is in tests/api/test_ach_api.py. Routing numbers start 00, which no bank holds."""

import uuid
from datetime import UTC, datetime, timedelta
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

from pms import sealed
from pms.api import bank_details


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def today():
    return datetime.now(UTC).date()


def weekday(start):
    """The first weekday on or after a day."""
    while start.isoweekday() > 5:
        start += timedelta(days=1)
    return start


def settings(conn, pmc, bank=None, routing="001000009"):
    return conn.execute(
        "INSERT INTO trust_ach_settings (pmc_id, bank_account_id, bank_routing, bank_name, origin,"
        " company_name, company_id) VALUES (%s, %s, %s, 'Demo Trust Bank', '1000000001',"
        " 'Demo Property Co', '1000000001') RETURNING id",
        (pmc.pmc_id, bank or pmc.operating_bank_id, routing),
    ).fetchone()[0]


def bank_account(conn, pmc, vendor=None, owner=None, removed=False):
    payee, person = ("vendor", vendor) if vendor is not None else ("owner", owner)
    values = (
        (None,) * 5
        if removed
        else (
            "Demo Payee",
            "002000008",
            "checking",
            sealed.seal("000123456789", bank_details.context(pmc.pmc_id, payee, person)),
            "6789",
        )
    )
    return conn.execute(
        "INSERT INTO trust_payee_bank_accounts (pmc_id, vendor_id, owner_id, removed, holder_name,"
        " routing_number, account_type, account_number_sealed, account_last4)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (pmc.pmc_id, vendor, owner, removed, *values),
    ).fetchone()[0]


def method(conn, pmc, transfer, how="EFT"):
    conn.execute(
        "INSERT INTO trust_payout_methods (pmc_id, transfer_id, method) VALUES (%s, %s, %s)",
        (pmc.pmc_id, transfer, how),
    )
    return transfer


def a_draw(conn, pmc, amount="40.00", how="EFT"):
    account = pmc.owners[0].account
    contribute(conn, pmc.pmc_id, account, f"in/{uuid.uuid4()}", "500.00")
    key = f"draw/{uuid.uuid4()}"
    draw_owner(conn, pmc.pmc_id, account, key, amount, datetime.now(UTC))
    transfer = conn.execute(
        "SELECT transfer_id FROM trust_owner_draws WHERE pmc_id = %s AND request_key = %s",
        (pmc.pmc_id, key),
    ).fetchone()[0]
    return method(conn, pmc, transfer, how)


def a_bill_paid(conn, pmc, amount="10.00", how="EFT"):
    contribute(conn, pmc.pmc_id, pmc.owners[0].account, f"in/{uuid.uuid4()}", amount)
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, f"INV-{uuid.uuid4().hex[:6]}",
        today(), today(), amount,
    )  # fmt: skip
    approve_bill(conn, pmc.pmc_id, bill, today(), "Owner, by phone")
    set_aside_bill(conn, pmc.pmc_id, bill, datetime.now(UTC))
    return method(conn, pmc, pay_bill(conn, pmc.pmc_id, bill, datetime.now(UTC)), how)


def make_file(conn, pmc, settings_id, items, effective=None, bank=None, entries=None, total=None):
    """A file and its items (transfer, payee's bank account, amount), in one transaction."""
    with conn.transaction():
        file_id = conn.execute(
            "INSERT INTO trust_ach_files (pmc_id, bank_account_id, settings_id, effective_on,"
            " entries, total) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (
                pmc.pmc_id,
                bank or pmc.operating_bank_id,
                settings_id,
                effective or weekday(today()),
                len(items) if entries is None else entries,
                sum(Decimal(a) for _, _, a in items) if total is None else total,
            ),
        ).fetchone()[0]
        for sequence, (transfer, payee_account, amount) in enumerate(items, start=1):
            conn.execute(
                "INSERT INTO trust_ach_file_items (pmc_id, file_id, sequence, transfer_id,"
                " payee_bank_account_id, amount) VALUES (%s, %s, %s, %s, %s, %s)",
                (pmc.pmc_id, file_id, sequence, transfer, payee_account, amount),
            )
    return file_id


def void(conn, pmc, file_id, reason="The bank refused it"):
    conn.execute(
        "INSERT INTO trust_ach_file_voids (pmc_id, file_id, reason) VALUES (%s, %s, %s)",
        (pmc.pmc_id, file_id, reason),
    )


def test_a_file_carries_eft_payouts_into_each_payees_bank_account(conn, pmc):
    ach = settings(conn, pmc)
    owner = bank_account(conn, pmc, owner=pmc.owners[0].owner_id)
    vendor = bank_account(conn, pmc, vendor=pmc.vendor_id)
    draw, bill = a_draw(conn, pmc, "40.00"), a_bill_paid(conn, pmc, "10.00")

    first = make_file(conn, pmc, ach, [(draw, owner, "40.00"), (bill, vendor, "10.00")])
    second = make_file(conn, pmc, ach, [(a_draw(conn, pmc, "5.00"), owner, "5.00")])

    assert conn.execute(
        "SELECT id, created_on, modifier, entries, total FROM trust_ach_files WHERE pmc_id = %s"
        " ORDER BY created_at",
        (pmc.pmc_id,),
    ).fetchall() == [(first, today(), "A", 2, 50), (second, today(), "B", 1, 5)]
    payout = conn.execute("SELECT * FROM trust_payout(%s, %s)", (pmc.pmc_id, bill)).fetchone()
    assert payout[:5] == ("EFT", pmc.operating_bank_id, pmc.vendor_id, None, 10)


def test_a_file_is_for_a_weekday_not_before_today_under_the_latest_settings(conn, pmc):
    old = settings(conn, pmc)
    ach = settings(conn, pmc)
    owner = bank_account(conn, pmc, owner=pmc.owners[0].owner_id)
    draw = a_draw(conn, pmc)
    saturday = today() + timedelta(days=(6 - today().isoweekday()) % 7)

    for settings_id, effective, refusal in (
        (ach, weekday(today() - timedelta(days=7)), "not before the day it is made"),
        (ach, saturday, "is a weekday"),
        (ach, saturday + timedelta(days=1), "is a weekday"),
        (old, weekday(today()), "latest ACH settings"),
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match=refusal):
            make_file(conn, pmc, settings_id, [(draw, owner, "40.00")], effective=effective)
    make_file(conn, pmc, ach, [(draw, owner, "40.00")], effective=weekday(saturday))


def test_an_item_is_an_eft_payout_of_the_files_account_for_its_whole_amount(conn, pmc):
    ach = settings(conn, pmc)
    deposits = settings(conn, pmc, bank=pmc.deposit_bank_id)
    owner = bank_account(conn, pmc, owner=pmc.owners[0].owner_id)
    vendor = bank_account(conn, pmc, vendor=pmc.vendor_id)
    by_check = a_draw(conn, pmc, how="check")
    draw = a_draw(conn, pmc, "40.00")
    other = make_pmc(conn)

    for items, refusal, bank, settings_id in (
        ([(by_check, owner, "40.00")], "recorded as sent by EFT", None, ach),
        ([(a_draw(conn, other), owner, "40.00")], "recorded as sent by EFT", None, ach),
        ([(draw, owner, "40.00")], "from its own trust bank account", pmc.deposit_bank_id,
         deposits),
        ([(draw, owner, "39.99")], "whole amount, 40.00", None, ach),
        ([(draw, vendor, "40.00")], "payee's bank account on file", None, ach),
    ):  # fmt: skip
        with pytest.raises(psycopg.errors.CheckViolation, match=refusal):
            make_file(conn, pmc, settings_id, items, bank=bank)
    assert conn.execute(
        "SELECT count(*) FROM trust_ach_files WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_it_pays_into_the_payees_latest_account_never_a_removed_one(conn, pmc):
    ach = settings(conn, pmc)
    old = bank_account(conn, pmc, owner=pmc.owners[0].owner_id)
    new = bank_account(conn, pmc, owner=pmc.owners[0].owner_id)
    draw = a_draw(conn, pmc)

    with pytest.raises(psycopg.errors.CheckViolation, match="payee's bank account on file"):
        make_file(conn, pmc, ach, [(draw, old, "40.00")])
    removal = bank_account(conn, pmc, owner=pmc.owners[0].owner_id, removed=True)
    for payee_account in (new, removal):
        with pytest.raises(psycopg.errors.CheckViolation, match="payee's bank account on file"):
            make_file(conn, pmc, ach, [(draw, payee_account, "40.00")])
    again = bank_account(conn, pmc, owner=pmc.owners[0].owner_id)
    make_file(conn, pmc, ach, [(draw, again, "40.00")])


def test_a_payout_is_in_one_file_until_that_file_is_voided(conn, pmc):
    ach = settings(conn, pmc)
    owner = bank_account(conn, pmc, owner=pmc.owners[0].owner_id)
    draw = a_draw(conn, pmc)
    first = make_file(conn, pmc, ach, [(draw, owner, "40.00")])

    with pytest.raises(psycopg.errors.CheckViolation, match="in a bank file already"):
        make_file(conn, pmc, ach, [(draw, owner, "40.00")])
    void(conn, pmc, first)
    second = make_file(conn, pmc, ach, [(draw, owner, "40.00")])
    with pytest.raises(psycopg.errors.UniqueViolation):
        void(conn, pmc, first)
    with pytest.raises(psycopg.errors.CheckViolation):
        void(conn, pmc, second, reason=" ")
    assert first != second


def test_a_file_comes_to_its_count_and_total_and_takes_nothing_after(conn, pmc):
    ach = settings(conn, pmc)
    owner = bank_account(conn, pmc, owner=pmc.owners[0].owner_id)
    draw, later = a_draw(conn, pmc, "40.00"), a_draw(conn, pmc, "5.00")

    for entries, total in ((2, 40), (1, 45)):
        with pytest.raises(psycopg.errors.CheckViolation, match="it says"):
            make_file(conn, pmc, ach, [(draw, owner, "40.00")], entries=entries, total=total)
    file_id = make_file(conn, pmc, ach, [(draw, owner, "40.00")])
    with pytest.raises(psycopg.errors.CheckViolation, match="it says 1 of 40"):
        conn.execute(
            "INSERT INTO trust_ach_file_items (pmc_id, file_id, sequence, transfer_id,"
            " payee_bank_account_id, amount) VALUES (%s, %s, 2, %s, %s, 5.00)",
            (pmc.pmc_id, file_id, later, owner),
        )


def test_a_day_takes_36_files_an_account(conn, pmc):
    ach = settings(conn, pmc)
    owner = bank_account(conn, pmc, owner=pmc.owners[0].owner_id)
    draw = a_draw(conn, pmc)
    made = []
    for _ in range(36):
        made.append(make_file(conn, pmc, ach, [(draw, owner, "40.00")]))
        void(conn, pmc, made[-1])

    with pytest.raises(psycopg.errors.CheckViolation, match="at most 36 bank files a day"):
        make_file(conn, pmc, ach, [(draw, owner, "40.00")])
    assert "".join(
        m
        for (m,) in conn.execute(
            "SELECT modifier FROM trust_ach_files WHERE pmc_id = %s ORDER BY created_at",
            (pmc.pmc_id,),
        ).fetchall()
    ) == ("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


def test_rows_are_never_changed_and_every_one_is_audited(conn, pmc):
    ach = settings(conn, pmc)
    owner = bank_account(conn, pmc, owner=pmc.owners[0].owner_id)
    file_id = make_file(conn, pmc, ach, [(a_draw(conn, pmc), owner, "40.00")])
    void(conn, pmc, file_id)

    for table in ("trust_ach_settings", "trust_ach_files", "trust_ach_file_items",
                  "trust_ach_file_voids"):  # fmt: skip
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
    owner = bank_account(conn, pmc, owner=pmc.owners[0].owner_id)
    draw = a_draw(conn, pmc)

    def as_staff(staff_id, write):
        with conn.transaction():
            conn.execute("SELECT set_config('trust.staff_id', %s, true)", (str(staff_id),))
            return write()

    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="disburse"):
        as_staff(clerk, lambda: settings(conn, pmc))
    ach = as_staff(payer, lambda: settings(conn, pmc))
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="disburse"):
        as_staff(clerk, lambda: make_file(conn, pmc, ach, [(draw, owner, "40.00")]))
    file_id = as_staff(payer, lambda: make_file(conn, pmc, ach, [(draw, owner, "40.00")]))
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="disburse"):
        as_staff(clerk, lambda: void(conn, pmc, file_id))
    as_staff(payer, lambda: void(conn, pmc, file_id))
    assert conn.execute(
        "SELECT (SELECT recorded_by FROM trust_ach_settings WHERE id = %s),"
        " (SELECT recorded_by FROM trust_ach_files WHERE id = %s),"
        " (SELECT recorded_by FROM trust_ach_file_voids WHERE file_id = %s)",
        (ach, file_id, file_id),
    ).fetchone() == (payer, payer, payer)


def test_the_ai_role_reads_none_of_it(conn, ai_conn, pmc):
    for table in ("trust_ach_settings", "trust_ach_files", "trust_ach_file_items",
                  "trust_ach_file_voids"):  # fmt: skip
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ai_conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ai_conn.execute("SELECT * FROM trust_payout(%s, 'x')", (pmc.pmc_id,)).fetchall()
