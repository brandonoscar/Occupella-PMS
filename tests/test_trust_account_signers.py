"""Who may pay money out of each trust bank account (migration 20261015000060): its broker on
file, named by an admin, and the signers the broker authorized, licensed or bonded. Once an
account has its broker on file, a payout from it by any other named staff member is refused.

The rules hold for the staff member the application names (trust.staff_id); a write that names
nobody (the server's command line) is not held to them, as with the roles.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    add_agreement,
    admin_of,
    balance,
    draw_owner,
    enter_bill,
    in_background,
    make_pmc,
    pay_bill,
    post_entry,
    propose_entry,
    set_aside_bill,
    sign_for,
    staff_named,
    transfer_batch,
    wait_until_blocked,
    withdraw_pmc_fees,
)

JAN_1, JAN_15, JAN_16 = date(2026, 1, 1), date(2026, 1, 15), date(2026, 1, 16)


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def not_allowed(match):
    return pytest.raises(psycopg.errors.InsufficientPrivilege, match=match)


@pytest.fixture
def pmc(conn):
    pmc = make_pmc(conn)
    # Owner 1 holds 1000.00 in the operating trust account.
    transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "1000.00")], at(JAN_1))
    return pmc


@pytest.fixture
def admin(conn, pmc):
    return admin_of(conn, pmc.pmc_id)


@pytest.fixture
def broker(conn, pmc, admin):
    """The operating account's broker on file, named by the admin."""
    broker = staff_named(conn, pmc.pmc_id, "Broker 1", ["disburse", "approve"])
    sign_for(conn, pmc.pmc_id, pmc.operating_bank_id, broker, "broker", by=admin)
    return broker


@pytest.fixture
def clerk(conn, pmc):
    """Holds disburse; signs nothing until the broker authorizes them."""
    return staff_named(conn, pmc.pmc_id, "Clerk 1", ["disburse"])


def draw(conn, pmc, staff, key, amount="10.00", day=JAN_15):
    with acting_as(conn, staff):
        return draw_owner(conn, pmc.pmc_id, pmc.owners[0].account, key, amount, at(day))


def test_with_no_broker_on_file_the_roles_alone_apply(conn, pmc, clerk):
    assert draw(conn, pmc, clerk, "draw-1") == Decimal("10.00")


def test_once_an_account_has_its_broker_only_its_signers_pay_out(conn, pmc, broker, clerk):
    with not_allowed("Clerk 1 is not an authorized signer of Synthetic operating trust account"):
        draw(conn, pmc, clerk, "draw-1")
    assert draw(conn, pmc, broker, "draw-2") == Decimal("10.00")

    sign_for(conn, pmc.pmc_id, pmc.operating_bank_id, clerk, license_number="LIC-0002", by=broker)
    assert draw(conn, pmc, clerk, "draw-3") == Decimal("10.00")
    # Taken away, they sign no more.
    sign_for(conn, pmc.pmc_id, pmc.operating_bank_id, clerk, granted=False, on=JAN_16, by=broker)
    with not_allowed("Clerk 1 is not an authorized signer"):
        draw(conn, pmc, clerk, "draw-4")
    # A signer of another account signs nothing here.
    sign_for(conn, pmc.pmc_id, pmc.deposit_bank_id, broker, "broker", by=admin_of(conn, pmc.pmc_id))
    sign_for(conn, pmc.pmc_id, pmc.deposit_bank_id, clerk, by=broker)
    with not_allowed("Clerk 1 is not an authorized signer"):
        draw(conn, pmc, clerk, "draw-5")
    # The command line names nobody and is not held to it.
    draw(conn, pmc, None, "draw-6")
    assert balance(conn, pmc.owners[0].account) == Decimal("970.00")


def test_a_bonded_signer_pays_out_only_while_the_bond_is_in_force(conn, pmc, broker, clerk):
    sign_for(conn, pmc.pmc_id, pmc.operating_bank_id, clerk, bond=("50000.00", JAN_15), by=broker)

    assert draw(conn, pmc, clerk, "on-the-last-day", day=JAN_15) == Decimal("10.00")
    with not_allowed(
        "Clerk 1's bond to sign Synthetic operating trust account ran out on 2026-01-15"
    ):
        draw(conn, pmc, clerk, "the-day-after", day=JAN_16)
    # The broker records the renewed bond.
    sign_for(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        clerk,
        bond=("50000.00", date(2027, 1, 15)),
        on=JAN_16,
        by=broker,
    )
    assert draw(conn, pmc, clerk, "renewed", day=JAN_16) == Decimal("10.00")


@pytest.fixture
def payable(conn, pmc):
    add_agreement(
        conn, pmc.pmc_id, pmc.owners[0].account, JAN_1, minimum_fee="50", approval_limit="10000"
    )
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, "INV-1", JAN_1, JAN_15, "100.00"
    )
    set_aside_bill(conn, pmc.pmc_id, bill, at(JAN_1, 13))
    return bill


@pytest.fixture
def fees(conn, pmc, payable):
    """The month's $50.00 management fee, taken from Owner 1 under the bill's agreement."""
    conn.execute(
        "SELECT trust_post_management_fee(%s, %s, %s, %s, %s)",
        (pmc.pmc_id, pmc.owners[0].account, JAN_1, date(2026, 2, 1), at(JAN_1, 14)),
    )


def test_every_payout_from_the_account_takes_a_signer(conn, pmc, broker, clerk, payable, fees):
    bank = pmc.operating_bank_id
    with not_allowed("Clerk 1 is not an authorized signer"), acting_as(conn, clerk):
        pay_bill(conn, pmc.pmc_id, payable, at(JAN_15))
    with not_allowed("Clerk 1 is not an authorized signer"), acting_as(conn, clerk):
        withdraw_pmc_fees(conn, pmc.pmc_id, bank, "fees-1", None, at(JAN_15))

    sign_for(conn, pmc.pmc_id, bank, clerk, by=broker)
    with acting_as(conn, clerk):
        pay_bill(conn, pmc.pmc_id, payable, at(JAN_15))
        assert withdraw_pmc_fees(conn, pmc.pmc_id, bank, "fees-1", None, at(JAN_15)) == 50


def test_a_journal_entry_that_takes_cash_out_takes_a_signer(conn, pmc, broker):
    bookkeeper = staff_named(conn, pmc.pmc_id, "Bookkeeper 1")
    manager = staff_named(conn, pmc.pmc_id, "Manager 1", ["approve"])
    # Money out of the bank (to its cash): the poster must sign the account.
    out = propose_entry(
        conn,
        pmc.pmc_id,
        bookkeeper,
        JAN_15,
        "Refund sent",
        [(pmc.owners[0].account, pmc.operating_cash, "5.00")],
    )
    with not_allowed("Manager 1 is not an authorized signer"):
        post_entry(conn, pmc.pmc_id, out, manager)
    # Between two owners' accounts, no cash moves: the roles alone apply.
    between = propose_entry(
        conn,
        pmc.pmc_id,
        bookkeeper,
        JAN_15,
        "Wrong owner",
        [(pmc.owners[0].account, pmc.owners[1].account, "5.00")],
    )
    post_entry(conn, pmc.pmc_id, between, manager)
    sign_for(conn, pmc.pmc_id, pmc.operating_bank_id, manager, by=broker)
    post_entry(conn, pmc.pmc_id, out, manager)
    assert balance(conn, pmc.owners[0].account) == Decimal("990.00")


def test_only_an_admin_names_the_broker_and_only_the_broker_authorizes_signers(
    conn, pmc, admin, clerk
):
    bank = pmc.operating_bank_id
    with not_allowed("Clerk 1 may not name a trust bank account's broker"):
        sign_for(conn, pmc.pmc_id, bank, clerk, "broker", by=clerk)
    broker = staff_named(conn, pmc.pmc_id, "Broker 1")
    sign_for(conn, pmc.pmc_id, bank, broker, "broker", by=admin)
    # The admin isn't the broker: only the broker authorizes signers.
    with not_allowed("only this trust bank account's broker authorizes its signers"):
        sign_for(conn, pmc.pmc_id, bank, clerk, by=admin)
    sign_for(conn, pmc.pmc_id, bank, clerk, by=broker)
    # The audit log and the row name who changed it.
    assert conn.execute(
        "SELECT staff_id, changed_by FROM trust_account_signers WHERE bank_account_id = %s"
        " ORDER BY created_at",
        (bank,),
    ).fetchall() == [(broker, admin), (clerk, broker)]


def test_one_broker_per_account_and_signers_only_while_it_has_one(conn, pmc, admin, broker, clerk):
    bank = pmc.operating_bank_id
    other = staff_named(conn, pmc.pmc_id, "Broker 2")
    sign_for(conn, pmc.pmc_id, bank, clerk, by=broker)
    with pytest.raises(psycopg.errors.UniqueViolation, match="has a broker on file"):
        sign_for(conn, pmc.pmc_id, bank, other, "broker", by=admin)
    # Taking the broker away leaves the clerk signing with no broker: refused at commit.
    with (
        pytest.raises(psycopg.errors.CheckViolation, match="signers need its broker on file"),
        conn.transaction(),
    ):
        sign_for(conn, pmc.pmc_id, bank, broker, "broker", granted=False, by=admin)
    # Replaced in the same change, it stands.
    with conn.transaction():
        sign_for(conn, pmc.pmc_id, bank, broker, "broker", granted=False, by=admin)
        sign_for(conn, pmc.pmc_id, bank, other, "broker", license_number="LIC-0009", by=admin)
    assert conn.execute("SELECT trust_broker_of(%s)", (bank,)).fetchone() == (other,)
    with pytest.raises(
        psycopg.errors.CheckViolation, match="the broker signs this trust bank account"
    ):
        sign_for(conn, pmc.pmc_id, bank, other, by=other)


@pytest.mark.timing
def test_two_brokers_named_at_once_the_second_waits_and_is_refused(conn, database_url, pmc, admin):
    bank = pmc.operating_bank_id
    first, second = (staff_named(conn, pmc.pmc_id, f"Broker {n}") for n in (1, 2))
    with psycopg.connect(database_url) as naming:
        sign_for(naming, pmc.pmc_id, bank, first, "broker", by=admin)  # not committed yet
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: sign_for(worker, pmc.pmc_id, bank, second, "broker", by=admin),
        )
        wait_until_blocked(conn, pid)
        naming.commit()
    thread.join(timeout=30)

    error = outcome.get("error")
    assert isinstance(error, psycopg.errors.UniqueViolation), outcome
    assert "has a broker on file" in str(error)
    assert conn.execute("SELECT trust_broker_of(%s)", (bank,)).fetchone() == (first,)


@pytest.mark.parametrize(
    ("role", "granted", "license_number", "bond"),
    [
        ("signer", True, None, None),  # neither licensed nor bonded
        ("broker", True, None, ("100.00", JAN_15)),  # a broker is licensed
        ("signer", True, "", None),
        ("signer", True, "L" * 51, None),
        ("signer", True, None, ("0", JAN_15)),
        ("signer", True, None, ("100.001", JAN_15)),
        ("signer", False, "LIC-0003", None),  # taking it away says nothing more
    ],
)
def test_a_grant_says_how_they_may_sign(
    conn, pmc, broker, clerk, role, granted, license_number, bond
):
    amount, expires = bond if bond is not None else (None, None)
    with pytest.raises(psycopg.errors.CheckViolation), acting_as(conn, None):
        conn.execute(
            "INSERT INTO trust_account_signers (pmc_id, bank_account_id, staff_id, role, granted,"
            " license_number, bond_amount, bond_expires_on, authorized_on)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                pmc.pmc_id,
                pmc.operating_bank_id,
                clerk if role == "signer" else broker,
                role,
                granted,
                license_number,
                None if amount is None else Decimal(amount),
                expires,
                JAN_1,
            ),
        )


def test_taking_away_what_is_not_held_is_refused(conn, pmc, broker, clerk):
    with pytest.raises(psycopg.errors.CheckViolation, match="nothing to take away"):
        sign_for(conn, pmc.pmc_id, pmc.operating_bank_id, clerk, granted=False, by=broker)
    with pytest.raises(psycopg.errors.CheckViolation, match="nothing to take away"):
        sign_for(conn, pmc.pmc_id, pmc.operating_bank_id, broker, granted=False, by=broker)


@pytest.mark.parametrize(
    "statement",
    ["UPDATE trust_account_signers SET granted = false", "DELETE FROM trust_account_signers"],
)
def test_signers_are_kept_for_good(conn, pmc, broker, statement):
    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)


def test_the_report_lists_each_accounts_broker_and_signers_as_of_a_day(conn, pmc, broker, clerk):
    bank = pmc.operating_bank_id
    second = staff_named(conn, pmc.pmc_id, "Clerk 2", ["disburse"])
    sign_for(conn, pmc.pmc_id, bank, clerk, bond=("25000.00", date(2026, 2, 10)), by=broker)
    sign_for(conn, pmc.pmc_id, bank, second, bond=("25000.00", JAN_15), on=JAN_15, by=broker)

    def report(on):
        return conn.execute(
            "SELECT bank_account, signer, role, license_number, bond_amount, standing"
            " FROM trust_report_trust_account_signers(%s, %s) ORDER BY line",
            (pmc.pmc_id, on),
        ).fetchall()

    assert report(JAN_16) == [
        ("Synthetic operating trust account", "Broker 1", "broker", "LIC-0001", None, "licensed"),
        (
            "Synthetic operating trust account",
            "Clerk 1",
            "signer",
            None,
            Decimal("25000.00"),
            "bond runs out in 25 days",
        ),
        (
            "Synthetic operating trust account",
            "Clerk 2",
            "signer",
            None,
            Decimal("25000.00"),
            "bond ran out",
        ),
        ("Synthetic security_deposit trust account", None, None, None, None, "no broker on file"),
    ]
    # Before Clerk 2 was authorized, they aren't listed.
    assert [r[1] for r in report(JAN_1)] == ["Broker 1", "Clerk 1", None]
