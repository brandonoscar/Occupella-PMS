"""Owner contributions: an owner's money in is posted as its own kind, never matched to a
tenant's charge (migration 20261010000026).
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_unit,
    apply_payment,
    approve,
    charge,
    contribute,
    make_pmc,
    open_account,
    open_lease,
    snapshot,
)

JAN_10 = datetime(2026, 1, 10, 12, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def test_a_contribution_moves_cash_into_the_owners_account(conn, app_conn, pmc):
    account = pmc.owners[0].account

    paid = contribute(app_conn, pmc.pmc_id, account, "roof-1", "2500.00", JAN_10, "Roof repair")

    row = conn.execute(
        "SELECT from_account_id, to_account_id, amount, event_at, metadata ->> 'memo'"
        " FROM pgledger_transfers WHERE id = %s",
        (paid,),
    ).fetchone()
    assert row == (pmc.operating_cash, account, Decimal("2500.00"), JAN_10, "Roof repair")
    assert conn.execute(
        "SELECT request_key, ledger_account_id, amount FROM trust_owner_contributions"
        " WHERE transfer_id = %s",
        (paid,),
    ).fetchone() == ("roof-1", account, Decimal("2500.00"))


def test_a_contribution_with_no_memo_says_what_it_is(conn, app_conn, pmc):
    paid = contribute(app_conn, pmc.pmc_id, pmc.owners[0].account, "c-1", "10.00", JAN_10)

    memo = conn.execute(
        "SELECT metadata ->> 'memo' FROM pgledger_transfers WHERE id = %s", (paid,)
    ).fetchone()[0]
    assert memo == "Owner contribution"


def test_the_same_request_again_returns_the_original(conn, app_conn, pmc):
    account = pmc.owners[0].account
    first = contribute(app_conn, pmc.pmc_id, account, "c-1", "10.00", JAN_10)
    before = snapshot(conn, pmc.accounts())

    assert contribute(app_conn, pmc.pmc_id, account, "c-1", "10.00", JAN_10) == first
    assert snapshot(conn, pmc.accounts()) == before


@pytest.mark.parametrize("change", ["another amount", "another account"])
def test_a_different_contribution_under_a_used_key_is_refused(conn, app_conn, pmc, change):
    contribute(app_conn, pmc.pmc_id, pmc.owners[0].account, "c-1", "10.00", JAN_10)
    account, amount = {
        "another amount": (pmc.owners[0].account, "11.00"),
        "another account": (pmc.owners[1].account, "10.00"),
    }[change]
    before = snapshot(conn, pmc.accounts())

    with pytest.raises(psycopg.errors.UniqueViolation, match="already recorded"):
        contribute(app_conn, pmc.pmc_id, account, "c-1", amount, JAN_10)
    assert snapshot(conn, pmc.accounts()) == before


@pytest.mark.parametrize("amount", ["0", "-5.00", "10.005", None])
def test_a_contribution_is_a_positive_amount_in_cents(conn, app_conn, pmc, amount):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="positive amount in cents"):
        contribute(app_conn, pmc.pmc_id, pmc.owners[0].account, "c-1", amount, JAN_10)


@pytest.mark.parametrize("account", ["prepaid rent", "another PMC's owner", "no account"])
def test_a_contribution_goes_into_an_owners_property_account_of_the_pmc(
    conn, app_conn, pmc, account
):
    other = make_pmc(conn)
    target = {
        "prepaid rent": pmc.prepaid_rent,
        "another PMC's owner": other.owners[0].account,
        "no account": "acct_none",
    }[account]

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="not an owner's property"):
        contribute(app_conn, pmc.pmc_id, target, "c-1", "10.00", JAN_10)


def test_a_contribution_needs_its_trust_bank_accounts_cash_account(conn, app_conn, pmc):
    bank = conn.execute(
        "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
        " VALUES (%s, 'operating', 'Synthetic second operating account') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    account = open_account(
        conn,
        pmc.pmc_id,
        bank,
        "owner_property",
        pmc.owners[0].owner_id,
        pmc.owners[0].property_id,
    )

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="open the cash account"):
        contribute(app_conn, pmc.pmc_id, account, "c-1", "10.00", JAN_10)


def test_a_contribution_is_never_matched_to_a_tenants_charge(conn, app_conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    lease = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "1500.00", [tenant])
    rent = charge(conn, pmc.pmc_id, lease, date(2026, 1, 1), "1500.00")
    paid = contribute(app_conn, pmc.pmc_id, pmc.owners[0].account, "c-1", "1500.00", JAN_10)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="an owner's contribution"):
        apply_payment(app_conn, pmc.pmc_id, rent, paid, "1500.00")


def test_a_contribution_respects_a_closed_period(conn, app_conn, pmc):
    approve(
        app_conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 2, 1, tzinfo=UTC),
    )

    with pytest.raises(psycopg.errors.CheckViolation, match="reconciled and closed"):
        contribute(app_conn, pmc.pmc_id, pmc.owners[0].account, "c-1", "10.00", JAN_10)


def test_a_contribution_is_posted_at_read_committed_only(database_url, pmc):
    with psycopg.connect(database_url) as session:
        session.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with pytest.raises(psycopg.errors.InvalidTransactionState, match="READ COMMITTED"):
            contribute(session, pmc.pmc_id, pmc.owners[0].account, "c-1", "10.00", JAN_10)


def test_contributions_are_records(conn, app_conn, pmc):
    contribute(app_conn, pmc.pmc_id, pmc.owners[0].account, "c-1", "10.00", JAN_10)

    for statement in (
        "UPDATE trust_owner_contributions SET amount = 1",
        "DELETE FROM trust_owner_contributions",
    ):
        with pytest.raises(psycopg.errors.Error, match="append-only|history"):
            conn.execute(statement)
