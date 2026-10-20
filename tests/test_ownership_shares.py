"""A property owned in shares (migration 20261019000094, the founder's review q044): each owner's
percent of it, from a day, together 100, in one trust bank account; an amount split by the shares
in force to the cent, the cents left over to the largest remainders; and what a lease owes, kept
from its deposits at move-out, going to the owners by their shares. Every name is synthetic."""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    add_staff,
    add_unit,
    balance,
    bank_sums,
    charge,
    co_own,
    make_pmc,
    open_account,
    open_lease,
    return_deposit,
    set_ownership,
    split_by_ownership,
    transfer_batch,
)

JAN_1, MAR_1, JUN_1, JUN_30, JUL_5 = (
    date(2026, 1, 1),
    date(2026, 3, 1),
    date(2026, 6, 1),
    date(2026, 6, 30),
    date(2026, 7, 5),
)


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def owners(conn, pmc):
    """Property 1's two owners' accounts: Owner 1's, and Owner 2's as a co-owner."""
    return pmc.owners[0].account, co_own(conn, pmc)


def shares_on(conn, pmc, day):
    return conn.execute(
        "SELECT ledger_account_id, percent FROM trust_ownership_on(%s, %s)",
        (pmc.owners[0].property_id, day),
    ).fetchall()


# --- the shares --------------------------------------------------------------------------------


def test_the_latest_shares_from_a_day_on_or_before_it_hold(conn, pmc, owners):
    first, second = owners
    prop = pmc.owners[0].property_id
    assert shares_on(conn, pmc, JUN_1) == []  # none on file: no shares
    set_ownership(conn, pmc.pmc_id, prop, MAR_1, [(first, 60), (second, 40)])
    set_ownership(conn, pmc.pmc_id, prop, JUN_1, [(first, 50), (second, 50)])

    assert shares_on(conn, pmc, date(2026, 2, 28)) == []
    assert dict(shares_on(conn, pmc, MAR_1)) == {first: 60, second: 40}
    assert dict(shares_on(conn, pmc, JUN_1)) == {first: 50, second: 50}
    # Recorded again for the same day: the latest recorded holds.
    set_ownership(conn, pmc.pmc_id, prop, JUN_1, [(first, 100)])
    assert shares_on(conn, pmc, JUL_5) == [(first, 100)]


def test_an_amount_splits_to_the_cent_and_the_parts_come_to_it(conn, pmc, owners):
    first, second = owners
    prop = pmc.owners[0].property_id
    set_ownership(conn, pmc.pmc_id, prop, JAN_1, [(first, 60), (second, 40)])
    assert dict(split_by_ownership(conn, prop, JUN_1, Decimal("1000.00"))) == {
        first: Decimal("600.00"),
        second: Decimal("400.00"),
    }
    # 0.05 at 60/40 is 0.03 and 0.02.
    assert dict(split_by_ownership(conn, prop, JUN_1, Decimal("0.05"))) == {
        first: Decimal("0.03"),
        second: Decimal("0.02"),
    }
    # Nothing, or less, splits into nothing.
    assert split_by_ownership(conn, prop, JUN_1, Decimal("0")) == []
    assert split_by_ownership(conn, prop, JUN_1, Decimal("-5.00")) == []


def test_the_cents_left_over_go_to_the_largest_remainders_then_the_first_account(conn, pmc):
    third = make_pmc(conn, owners=3)
    prop = third.owners[0].property_id
    accounts = sorted([third.owners[0].account, co_own(conn, third, 1), co_own(conn, third, 2)])
    set_ownership(
        conn,
        third.pmc_id,
        prop,
        JAN_1,
        [(accounts[0], "33.3333"), (accounts[1], "33.3333"), (accounts[2], "33.3334")],
    )
    # 100.00: 3333.33, 3333.33 and 3333.34 cents; the one cent left goes to the largest
    # remainder, the third's.
    assert split_by_ownership(conn, prop, JUN_1, Decimal("100.00")) == [
        (accounts[0], Decimal("33.33")),
        (accounts[1], Decimal("33.33")),
        (accounts[2], Decimal("33.34")),
    ]
    # 0.02: two cents for three owners, the remainders tie but the third's is larger.
    parts = split_by_ownership(conn, prop, JUN_1, Decimal("0.02"))
    assert sum(amount for _, amount in parts) == Decimal("0.02")
    assert parts == [(accounts[0], Decimal("0.01")), (accounts[2], Decimal("0.01"))]

    halves = make_pmc(conn)
    prop = halves.owners[0].property_id
    pair = sorted([halves.owners[0].account, co_own(conn, halves)])
    set_ownership(conn, halves.pmc_id, prop, JAN_1, [(pair[1], 50), (pair[0], 50)])
    # One cent at 50/50: an even tie, so the account first by id; the other's nothing is left out.
    assert split_by_ownership(conn, prop, JUN_1, Decimal("0.01")) == [(pair[0], Decimal("0.01"))]


def test_shares_come_to_100_at_commit(conn, pmc, owners):
    first, second = owners
    prop = pmc.owners[0].property_id
    with pytest.raises(psycopg.errors.CheckViolation, match="come to 100%, not 90%"):
        set_ownership(conn, pmc.pmc_id, prop, JAN_1, [(first, 60), (second, 30)])
    with (
        pytest.raises(psycopg.errors.CheckViolation, match="come to 100%, not 0%"),
        conn.transaction(),
    ):
        conn.execute(
            "INSERT INTO trust_ownership_shares (pmc_id, property_id, starts_on)"
            " VALUES (%s, %s, %s)",
            (pmc.pmc_id, prop, JAN_1),
        )
    assert shares_on(conn, pmc, JUN_1) == []


@pytest.mark.parametrize("percent", ["0", "-10", "100.5", "33.33333"])
def test_a_percent_is_more_than_0_up_to_100_to_4_decimals(conn, pmc, owners, percent):
    with pytest.raises(psycopg.errors.CheckViolation):
        set_ownership(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, [(owners[0], percent)])


def test_a_share_is_an_owner_account_of_the_property_and_its_company(conn, pmc, owners):
    prop = pmc.owners[0].property_id
    other_property = pmc.owners[1].account  # Owner 2's account at Property 2
    with pytest.raises(psycopg.errors.CheckViolation, match="not an owner's account at the"):
        set_ownership(conn, pmc.pmc_id, prop, JAN_1, [(owners[0], 50), (other_property, 50)])
    with pytest.raises(psycopg.errors.CheckViolation, match="not an owner's account"):
        set_ownership(conn, pmc.pmc_id, prop, JAN_1, [(pmc.prepaid_rent, 100)])
    # Another company's shares can't name this one's property.
    elsewhere = make_pmc(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        set_ownership(conn, elsewhere.pmc_id, prop, JAN_1, [(owners[0], 100)])


def test_the_owners_accounts_are_in_one_trust_bank_account(conn, pmc):
    bank = conn.execute(
        "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
        " VALUES (%s, 'operating', 'Synthetic second operating account') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    open_account(conn, pmc.pmc_id, bank, "bank_cash")
    apart = co_own(conn, pmc, bank_id=bank)
    with pytest.raises(psycopg.errors.CheckViolation, match="in one trust bank account"):
        set_ownership(
            conn,
            pmc.pmc_id,
            pmc.owners[0].property_id,
            JAN_1,
            [(pmc.owners[0].account, 50), (apart, 50)],
        )


def test_shares_are_append_only_audited_and_name_who_recorded_them(conn, pmc, owners):
    staff = add_staff(conn, pmc.pmc_id, "Staff Synthetic")
    with acting_as(conn, staff):
        share = set_ownership(
            conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, [(owners[0], 60), (owners[1], 40)]
        )
    assert conn.execute(
        "SELECT recorded_by FROM trust_ownership_shares WHERE id = %s", (share,)
    ).fetchone() == (staff,)
    for table in ("trust_ownership_shares", "trust_ownership_share_lines"):
        assert conn.execute(
            "SELECT count(*), min(staff_id::text) FROM trust_audit_log"
            " WHERE table_name = %s AND pmc_id = %s",
            (table, pmc.pmc_id),
        ).fetchone() == (1 if table == "trust_ownership_shares" else 2, str(staff))
        for query in (f"UPDATE {table} SET created_at = now()", f"DELETE FROM {table}"):
            with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
                conn.execute(query)


def test_the_app_records_shares_and_the_ai_only_reads_them(conn, app_conn, ai_conn, pmc, owners):
    set_ownership(
        app_conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, [(owners[0], 60), (owners[1], 40)]
    )
    assert ai_conn.execute("SELECT count(*) FROM trust_ownership_share_lines").fetchone()[0] >= 2
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        set_ownership(ai_conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, [(owners[0], 100)])


# --- the deposit kept at move-out --------------------------------------------------------------


def test_what_a_lease_owes_kept_from_its_deposit_goes_to_its_owners_by_their_shares(
    conn, app_conn, pmc, owners
):
    first, second = owners
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, JUN_30, "1000.00", [tenant])
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "1500.00")], at(JAN_1))
    rent = charge(conn, pmc.pmc_id, lease, JUN_1, "1000.00", "rent", "June rent")
    fee = charge(conn, pmc.pmc_id, lease, JUN_1, "100.01", "fee", "Lost key")
    set_ownership(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, [(first, 60), (second, 40)])

    # With shares in force, no account is named, and one named is not needed.
    returned = return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5))

    assert returned == Decimal("399.99")
    # 1000.00 of rent: 600.00 and 400.00; 100.01 of the fee: 60.01 (60.006 rounded up, the larger
    # remainder) and 40.00.
    assert balance(conn, first) == Decimal("660.01")
    assert balance(conn, second) == Decimal("440.00")
    paid = conn.execute(
        "SELECT p.charge_id, a.ledger_account_id, sum(p.amount) FROM trust_charge_payments AS p"
        " JOIN pgledger_transfers AS t ON t.id = p.transfer_id"
        " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id"
        " WHERE p.charge_id = ANY(%s) GROUP BY 1, 2",
        ([rent, fee],),
    ).fetchall()
    assert {(c, a): amount for c, a, amount in paid} == {
        (rent, first): Decimal("600.00"),
        (rent, second): Decimal("400.00"),
        (fee, first): Decimal("60.01"),
        (fee, second): Decimal("40.00"),
    }
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_without_shares_kept_money_still_goes_to_the_one_named(conn, app_conn, pmc, owners):
    first, second = owners
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, JUN_30, "1000.00", [tenant])
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "500.00")], at(JAN_1))
    charge(conn, pmc.pmc_id, lease, JUN_1, "300.00", "rent", "June rent")
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no account given"):
        return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5))

    assert return_deposit(app_conn, pmc.pmc_id, lease, "move-out-1", at(JUL_5), second) == 200
    assert (balance(conn, first), balance(conn, second)) == (Decimal(0), Decimal("300.00"))
