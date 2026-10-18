"""Waiving a charge (migration 20261018000079): a waiver is a credit linked to the charge it
waives, and it comes off that charge first, where other credits come off the oldest open charges.
Never more than is left of the charge; payments never match past what was paid and waived of it;
the deposit kept at move-out leaves it alone. Append-only and audited."""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    add_staff,
    add_unit,
    apply_payment,
    charge,
    make_pmc,
    open_lease,
    return_deposit,
    reverse_payment,
    transfer_batch,
)

JAN_1, JAN_7, JUN_30, JUL_5 = (
    date(2026, 1, 1),
    date(2026, 1, 7),
    date(2026, 6, 30),
    date(2026, 7, 5),
)


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def tenant(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]


@pytest.fixture
def lease(conn, pmc, tenant):
    """January to June at 1000.00 a month: January's rent, and its 50.00 late fee."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease_id = open_lease(conn, pmc.pmc_id, unit, JAN_1, JUN_30, "1000.00", [tenant])
    charge(conn, pmc.pmc_id, lease_id, JAN_1, "1000.00", memo="Rent")
    charge(conn, pmc.pmc_id, lease_id, JAN_7, "50.00", kind="fee", memo="Late fee")
    return lease_id


def charge_of(conn, lease, memo):
    return conn.execute(
        "SELECT id FROM trust_charges WHERE lease_id = %s AND memo = %s", (lease, memo)
    ).fetchone()[0]


def waive(conn, pmc_id, lease, waived, amount, by=None, reason="Courtesy"):
    """A credit of the lease linked to the charge it waives; the credit's id."""
    with acting_as(conn, by), conn.transaction():
        credit = charge(conn, pmc_id, lease, JAN_7, amount, kind="credit", memo="Waived")
        conn.execute(
            "INSERT INTO trust_charge_waivers (credit_charge_id, pmc_id, waived_charge_id, reason)"
            " VALUES (%s, %s, %s, %s)",
            (credit, pmc_id, waived, reason),
        )
    return credit


def open_of(conn, pmc_id, lease, at_="infinity"):
    return dict(
        conn.execute(
            "SELECT c.memo, o.open FROM trust_open_charges(%s, %s, %s) AS o"
            " JOIN trust_charges AS c ON c.id = o.charge_id ORDER BY c.due_on",
            (pmc_id, lease, at_),
        ).fetchall()
    )


def paid(conn, pmc, amount, day=JAN_7):
    (moved,) = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, amount)], at(day))
    return moved


def test_a_waiver_comes_off_its_own_charge_where_a_credit_comes_off_the_oldest(conn, pmc, lease):
    fee = charge_of(conn, lease, "Late fee")
    waive(conn, pmc.pmc_id, lease, fee, "50.00")
    assert open_of(conn, pmc.pmc_id, lease) == {"Rent": Decimal("1000.00"), "Late fee": 0}
    # A plain credit of the same amount would have come off January's rent instead.
    charge(conn, pmc.pmc_id, lease, JAN_7, "50.00", kind="credit", memo="Concession")
    assert open_of(conn, pmc.pmc_id, lease) == {"Rent": Decimal("950.00"), "Late fee": 0}
    assert conn.execute("SELECT trust_lease_owed(%s)", (lease,)).fetchone() == (Decimal("950.00"),)


def test_part_of_a_charge_is_waived_and_never_more_than_is_left_of_it(conn, pmc, lease):
    fee = charge_of(conn, lease, "Late fee")
    apply_payment(conn, pmc.pmc_id, fee, paid(conn, pmc, "30.00"), "30.00")
    with pytest.raises(psycopg.errors.CheckViolation, match="20.00 is left of charge"):
        waive(conn, pmc.pmc_id, lease, fee, "25.00")
    waive(conn, pmc.pmc_id, lease, fee, "15.00")
    assert open_of(conn, pmc.pmc_id, lease)["Late fee"] == Decimal("5.00")
    waive(conn, pmc.pmc_id, lease, fee, "5.00")
    # Paid and waived in full: no payment matches it again.
    with pytest.raises(psycopg.errors.CheckViolation, match="already paid or waived"):
        apply_payment(conn, pmc.pmc_id, fee, paid(conn, pmc, "1.00"), "1.00")


def test_a_bounce_after_a_waiver_leaves_owed_what_was_not_waived(conn, pmc, lease):
    fee = charge_of(conn, lease, "Late fee")
    payment = paid(conn, pmc, "30.00")
    apply_payment(conn, pmc.pmc_id, fee, payment, "30.00")
    waive(conn, pmc.pmc_id, lease, fee, "20.00")
    (back,) = transfer_batch(
        conn, [(pmc.owners[0].account, pmc.operating_cash, "30.00")], at(date(2026, 1, 9))
    )
    reverse_payment(conn, pmc.pmc_id, fee, payment, back, "30.00")
    assert open_of(conn, pmc.pmc_id, lease)["Late fee"] == Decimal("30.00")


def test_only_a_charge_is_waived_by_a_credit_of_its_own_lease(conn, pmc, lease, tenant):
    fee = charge_of(conn, lease, "Late fee")
    with (
        pytest.raises(psycopg.errors.CheckViolation, match="a waiver is a credit, not a fee"),
        conn.transaction(),
    ):
        extra = charge(conn, pmc.pmc_id, lease, JAN_7, "5.00", kind="fee")
        conn.execute(
            "INSERT INTO trust_charge_waivers (credit_charge_id, pmc_id, waived_charge_id,"
            " reason) VALUES (%s, %s, %s, 'x')",
            (extra, pmc.pmc_id, fee),
        )
    concession = charge(conn, pmc.pmc_id, lease, JAN_7, "5.00", kind="credit", memo="Concession")
    with pytest.raises(psycopg.errors.CheckViolation, match="a credit is not waived"):
        waive(conn, pmc.pmc_id, lease, concession, "1.00")
    other_unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 2")
    other = open_lease(conn, pmc.pmc_id, other_unit, JAN_1, None, "900.00", [tenant])
    with pytest.raises(psycopg.errors.CheckViolation, match="own lease"):
        waive(conn, pmc.pmc_id, other, fee, "1.00")
    theirs = make_pmc(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        waive(conn, theirs.pmc_id, lease, fee, "1.00")
    # A waiver row naming another company's credit, or waiving another company's charge.
    ours = charge(conn, pmc.pmc_id, lease, JAN_7, "1.00", kind="credit", memo="Ours")
    their_unit = add_unit(conn, theirs.pmc_id, theirs.owners[0].property_id)
    their_tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (theirs.tenant_deposit,),
    ).fetchone()[0]
    their_lease = open_lease(conn, theirs.pmc_id, their_unit, JAN_1, None, "900.00", [their_tenant])
    their_fee = charge(conn, theirs.pmc_id, their_lease, JAN_7, "5.00", kind="fee")
    for pmc_id, credit, waived in ((theirs.pmc_id, ours, fee), (pmc.pmc_id, ours, their_fee)):
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
            conn.execute(
                "INSERT INTO trust_charge_waivers (credit_charge_id, pmc_id, waived_charge_id,"
                " reason) VALUES (%s, %s, %s, 'x')",
                (credit, pmc_id, waived),
            )
    # One waiver per credit.
    credit = waive(conn, pmc.pmc_id, lease, fee, "1.00")
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO trust_charge_waivers (credit_charge_id, pmc_id, waived_charge_id, reason)"
            " VALUES (%s, %s, %s, 'again')",
            (credit, pmc.pmc_id, fee),
        )


def test_the_delinquency_report_owes_nothing_of_what_was_waived(conn, pmc, lease):
    waive(conn, pmc.pmc_id, lease, charge_of(conn, lease, "Late fee"), "50.00")
    rows = conn.execute(
        "SELECT item, days_1_30, total FROM trust_report_delinquency(%s, %s) WHERE item = 'lease'",
        (pmc.pmc_id, date(2026, 1, 20)),
    ).fetchall()
    assert rows == [("lease", Decimal("1000.00"), Decimal("1000.00"))]
    # Before the waiver's day, the fee was owed.
    assert conn.execute(
        "SELECT total FROM trust_report_delinquency(%s, %s) WHERE item = 'lease'",
        (pmc.pmc_id, date(2026, 1, 6)),
    ).fetchone() == (Decimal("1000.00"),)


def test_a_deposit_kept_at_move_out_leaves_a_waived_charge_alone(conn, pmc, lease):
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "1500.00")], at(JAN_1))
    fee = charge_of(conn, lease, "Late fee")
    # The fee is older than nothing else: kept money would reach it after the rent; waive it.
    waive(conn, pmc.pmc_id, lease, fee, "50.00")
    return_deposit(conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5))
    assert conn.execute(
        "SELECT kept, returned FROM trust_deposit_returns WHERE lease_id = %s", (lease,)
    ).fetchone() == (Decimal("1000.00"), Decimal("500.00"))
    assert conn.execute(
        "SELECT c.memo, x.amount FROM trust_deposit_return_lines AS x"
        " JOIN trust_charges AS c ON c.id = x.charge_id WHERE x.lease_id = %s",
        (lease,),
    ).fetchall() == [("Rent", Decimal("1000.00"))]


def test_a_deposit_kept_passes_over_a_waived_charge_older_than_what_is_owed(conn, pmc, lease):
    # January's rent is paid and its late fee waived: what is owed is February's rent, after both.
    rent = charge_of(conn, lease, "Rent")
    apply_payment(conn, pmc.pmc_id, rent, paid(conn, pmc, "1000.00"), "1000.00")
    waive(conn, pmc.pmc_id, lease, charge_of(conn, lease, "Late fee"), "50.00")
    charge(conn, pmc.pmc_id, lease, date(2026, 2, 1), "1000.00", memo="February rent")
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "1500.00")], at(JAN_1))
    return_deposit(conn, pmc.pmc_id, lease, "move-out-2", at(JUL_5))
    assert conn.execute(
        "SELECT c.memo, x.amount FROM trust_deposit_return_lines AS x"
        " JOIN trust_charges AS c ON c.id = x.charge_id WHERE x.lease_id = %s",
        (lease,),
    ).fetchall() == [("February rent", Decimal("1000.00"))]


def test_waivers_are_append_only_audited_and_name_who_waived(conn, pmc, lease):
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    credit = waive(conn, pmc.pmc_id, lease, charge_of(conn, lease, "Late fee"), "10.00", by=clerk)
    assert conn.execute(
        "SELECT recorded_by, reason FROM trust_charge_waivers WHERE credit_charge_id = %s",
        (credit,),
    ).fetchone() == (clerk, "Courtesy")
    assert conn.execute(
        "SELECT staff_id FROM trust_audit_log WHERE table_name = 'trust_charge_waivers'"
        " AND pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall() == [(clerk,)]
    for query in (
        "UPDATE trust_charge_waivers SET reason = 'x'",
        "DELETE FROM trust_charge_waivers",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(query)
    with pytest.raises(psycopg.errors.CheckViolation):
        waive(conn, pmc.pmc_id, lease, charge_of(conn, lease, "Late fee"), "1.00", reason=" ")


def test_a_waiver_is_written_at_read_committed(conn, pmc, lease):
    fee = charge_of(conn, lease, "Late fee")
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        credit = charge(conn, pmc.pmc_id, lease, JAN_7, "1.00", kind="credit")
        with pytest.raises(psycopg.errors.InvalidTransactionState, match="READ COMMITTED"):
            conn.execute(
                "INSERT INTO trust_charge_waivers (credit_charge_id, pmc_id, waived_charge_id,"
                " reason) VALUES (%s, %s, %s, 'x')",
                (credit, pmc.pmc_id, fee),
            )
        raise psycopg.Rollback()
