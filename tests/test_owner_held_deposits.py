"""Deposits the owner holds (migration 20261106000241): a lease's deposit the owner keeps, never in
trust, recorded per lease, tenant and kind with its amount and day, naming who; and, at move-out,
what the owner returned and kept of it, once. No money moves, so every trust bank account still
ties out and no ledger account changes; the tenant ledger shows each as a memo line that changes
no balance."""

import uuid
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    add_staff,
    add_unit,
    bank_sums,
    charge,
    end_lease,
    make_pmc,
    open_lease,
)

JAN_1 = date(2026, 1, 1)
JUN_30 = date(2026, 6, 30)


def one(conn, query, params=()):
    return conn.execute(query, params).fetchone()[0]


@pytest.fixture
def home(conn):
    pmc = make_pmc(conn)
    tenant = one(
        conn,
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    )
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "1000.00", [tenant])
    return pmc, lease, tenant


def hold(conn, pmc, lease, tenant, amount="1500.00", kind="security", on=JAN_1, staff=None):
    with acting_as(conn, staff):
        return one(
            conn,
            "INSERT INTO trust_owner_held_deposits (pmc_id, lease_id, tenant_id, deposit_kind,"
            " amount, held_since, memo) VALUES (%s, %s, %s, %s, %s, %s, 'Owner keeps it')"
            " RETURNING id",
            (pmc.pmc_id, lease, tenant, kind, Decimal(amount), on),
        )


def settle(conn, pmc, held, returned, kept, on=JUN_30, staff=None):
    with acting_as(conn, staff):
        conn.execute(
            "INSERT INTO trust_owner_held_deposit_outcomes"
            " (held_id, pmc_id, returned, kept, on_day) VALUES (%s, %s, %s, %s, %s)",
            (held, pmc.pmc_id, Decimal(returned), Decimal(kept), on),
        )


def books(conn, pmc):
    """Everything the trust ledger holds for the PMC: each account's balance and the transfers."""
    balances = conn.execute(
        "SELECT t.ledger_account_id, a.balance FROM trust_ledger_accounts AS t"
        " JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id WHERE t.pmc_id = %s"
        " ORDER BY 1",
        (pmc.pmc_id,),
    ).fetchall()
    transfers = one(conn, "SELECT count(*) FROM pgledger_transfers")
    return balances, transfers


def test_a_deposit_the_owner_holds_is_recorded_naming_who_and_never_in_trust(conn, home):
    pmc, lease, tenant = home
    staff = add_staff(conn, pmc.pmc_id)
    before = books(conn, pmc)

    held = hold(conn, pmc, lease, tenant, staff=staff)
    hold(conn, pmc, lease, tenant, "200.00", kind="pet", staff=staff)

    assert conn.execute(
        "SELECT deposit_kind, amount, held_since, recorded_by FROM trust_owner_held_deposits"
        " WHERE id = %s",
        (held,),
    ).fetchone() == ("security", Decimal("1500.00"), JAN_1, staff)
    assert books(conn, pmc) == before
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_one_per_lease_tenant_and_kind_for_a_tenant_on_the_lease_by_today(conn, home):
    pmc, lease, tenant = home
    hold(conn, pmc, lease, tenant)
    other = make_pmc(conn)
    stranger = one(
        conn,
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (other.prepaid_rent,),
    )

    with pytest.raises(psycopg.errors.UniqueViolation):
        hold(conn, pmc, lease, tenant, "10.00")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        hold(conn, pmc, lease, stranger, kind="key")
    with pytest.raises(psycopg.errors.CheckViolation, match="not after today"):
        hold(conn, pmc, lease, tenant, kind="key", on=date(2999, 1, 1))
    with pytest.raises(psycopg.errors.CheckViolation):
        hold(conn, pmc, lease, tenant, "0.00", kind="key")
    with pytest.raises(psycopg.errors.CheckViolation):
        hold(conn, pmc, lease, tenant, kind="cleaning")


def test_at_move_out_the_owner_returns_or_keeps_it_once_and_nothing_moves(conn, home):
    pmc, lease, tenant = home
    staff = add_staff(conn, pmc.pmc_id)
    held = hold(conn, pmc, lease, tenant)

    with pytest.raises(psycopg.errors.CheckViolation, match="once its last day is set"):
        settle(conn, pmc, held, "1500.00", "0.00")
    end_lease(conn, pmc.pmc_id, lease, JUN_30)
    before = books(conn, pmc)
    with pytest.raises(psycopg.errors.CheckViolation, match=r"come to the 1500\.00"):
        settle(conn, pmc, held, "1000.00", "0.00")
    with pytest.raises(psycopg.errors.CheckViolation, match="before it was held"):
        settle(conn, pmc, held, "1500.00", "0.00", on=date(2025, 12, 31))
    with pytest.raises(
        psycopg.errors.CheckViolation,
        match="what the owner did is recorded on a day not after today, not 2999-01-01",
    ):
        settle(conn, pmc, held, "1500.00", "0.00", on=date(2999, 1, 1))
    settle(conn, pmc, held, "1200.00", "300.00", staff=staff)
    with pytest.raises(psycopg.errors.UniqueViolation):
        settle(conn, pmc, held, "1500.00", "0.00")

    assert conn.execute(
        "SELECT returned, kept, on_day, recorded_by FROM trust_owner_held_deposit_outcomes"
        " WHERE held_id = %s",
        (held,),
    ).fetchone() == (Decimal("1200.00"), Decimal("300.00"), JUN_30, staff)
    assert books(conn, pmc) == before


def test_what_the_owner_did_is_of_a_deposit_they_hold_in_its_company(conn, home):
    pmc, lease, tenant = home
    held = hold(conn, pmc, lease, tenant)
    end_lease(conn, pmc.pmc_id, lease, JUN_30)
    other = make_pmc(conn)

    # Another company's deposit, or none: the foreign key refuses it.
    for company, deposit in ((other, held), (pmc, uuid.uuid4())):
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            settle(conn, company, deposit, "1500.00", "0.00")

    assert (
        one(
            conn,
            "SELECT count(*) FROM trust_owner_held_deposit_outcomes WHERE held_id = %s",
            (held,),
        )
        == 0
    )


def test_the_tenant_ledger_shows_it_as_memo_lines_that_change_no_balance(conn, home):
    pmc, lease, tenant = home
    charge(conn, pmc.pmc_id, lease, JAN_1, "1000.00")
    held = hold(conn, pmc, lease, tenant)
    end_lease(conn, pmc.pmc_id, lease, JUN_30)
    settle(conn, pmc, held, "1200.00", "300.00")
    name = one(conn, "SELECT display_name FROM trust_tenants WHERE id = %s", (tenant,))

    rows = conn.execute(
        "SELECT on_date, item, detail, charged, paid, balance"
        " FROM trust_report_tenant_ledger(%s, %s, %s) ORDER BY line",
        (pmc.pmc_id, lease, JUN_30),
    ).fetchall()

    assert rows[1:-1] == [
        (JAN_1, "rent", None, Decimal("1000.00"), Decimal("0.00"), Decimal("1000.00")),
        (
            JAN_1,
            "owner-held deposit",
            f"{name}'s security deposit, held by the owner (not in trust): 1,500.00",
            None,
            None,
            Decimal("1000.00"),
        ),
        (
            JUN_30,
            "owner-held deposit settled",
            f"{name}'s security deposit the owner held: 1,200.00 returned, 300.00 kept",
            None,
            None,
            Decimal("1000.00"),
        ),
    ]
    assert rows[-1][1:] == (
        "balance",
        "end of day UTC",
        Decimal("1000.00"),
        Decimal("0.00"),
        Decimal("1000.00"),
    )


def test_deposits_the_owner_holds_are_never_rewritten(conn, home):
    pmc, lease, tenant = home
    hold(conn, pmc, lease, tenant)

    for table in ("trust_owner_held_deposits", "trust_owner_held_deposit_outcomes"):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(f"DELETE FROM {table}")  # noqa: S608


def test_the_owner_statement_lists_what_the_owner_holds_on_a_memo_line_not_in_the_balance(
    conn, home
):
    pmc, lease, tenant = home
    held = hold(conn, pmc, lease, tenant)
    hold(conn, pmc, lease, tenant, "200.00", kind="pet")
    name = one(conn, "SELECT display_name FROM trust_tenants WHERE id = %s", (tenant,))
    unit = one(
        conn,
        "SELECT u.display_name FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id"
        " WHERE l.id = %s",
        (lease,),
    )

    def memos(start, end):
        return [
            (item, detail, amount, balance)
            for item, detail, amount, balance in conn.execute(
                "SELECT item, detail, amount, balance FROM trust_report_owner_statement"
                "(%s, %s, %s::timestamptz, %s::timestamptz) ORDER BY line",
                (pmc.pmc_id, pmc.owners[0].owner_id, f"{start} 00:00+00", f"{end} 00:00+00"),
            ).fetchall()
            if item.startswith("memo")
        ]

    january = memos(JAN_1, date(2026, 2, 1))
    end_lease(conn, pmc.pmc_id, lease, JUN_30)
    settle(conn, pmc, held, "1500.00", "0.00")
    july = memos(date(2026, 7, 1), date(2026, 8, 1))

    assert january == [
        (
            "memo: deposit held by owner",
            f"{name}'s security deposit, {unit}: you hold it, not in trust",
            Decimal("1500.00"),
            None,
        ),
        (
            "memo: deposit held by owner",
            f"{name}'s pet deposit, {unit}: you hold it, not in trust",
            Decimal("200.00"),
            None,
        ),
    ]
    # Returned by the owner at move-out, only the pet deposit is still theirs to hold.
    assert [detail for _, detail, _, _ in july] == [
        f"{name}'s pet deposit, {unit}: you hold it, not in trust"
    ]
    assert memos(date(2025, 12, 1), JAN_1) == []
