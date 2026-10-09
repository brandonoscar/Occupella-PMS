"""Each kind of money sits in its own kind of trust bank account, for every PMC: tenant deposits
in the security-deposit account, everything else held in trust in the operating one.

Migration 20261009000008 (issue #6). With the tie-out, the deposit account's cash always equals
the deposits it holds.
"""

import shutil
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from helpers import bank_sums, make_pmc, transfer, transfer_batch

from tools.check_rollback import Dbmate
from tools.scratch_db import scratch_database

ROOT = Path(__file__).resolve().parents[1]
WRONG_BANK = "accounts belong in the"


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]


def opened_accounts(conn):
    """Every pgledger account and every trust row, whoever opened them."""
    return conn.execute(
        "SELECT (SELECT count(*) FROM pgledger_accounts),"
        " (SELECT count(*) FROM trust_ledger_accounts)"
    ).fetchone()


def open_in_the_other_bank(conn, pmc, kind, tenant):
    """Open a `kind` account in the trust bank account it doesn't belong in."""
    owner = pmc.owners[0]
    if kind == "vendor_payable":
        return conn.execute(
            "SELECT trust_open_vendor_account(%s, %s, %s)",
            (pmc.pmc_id, pmc.deposit_bank_id, pmc.vendor_id),
        )
    holders = {
        "tenant_deposit": (pmc.operating_bank_id, None, None, tenant),
        "owner_property": (pmc.deposit_bank_id, owner.owner_id, owner.property_id, None),
        "pmc_income": (pmc.deposit_bank_id, None, None, None),
        "prepaid_rent": (pmc.deposit_bank_id, None, None, tenant),
    }
    bank, owner_id, property_id, tenant_id = holders[kind]
    return conn.execute(
        "SELECT trust_open_ledger_account(%s, %s, %s, %s, %s, %s)",
        (pmc.pmc_id, bank, kind, owner_id, property_id, tenant_id),
    )


@pytest.mark.parametrize("role", ["owner", "app"])
@pytest.mark.parametrize(
    "kind", ["tenant_deposit", "owner_property", "pmc_income", "prepaid_rent", "vendor_payable"]
)
def test_money_held_in_trust_opens_only_in_its_own_kind_of_account(conn, app_conn, role, kind):
    pmc = make_pmc(conn)
    tenant = tenant_of(conn, pmc)
    before = opened_accounts(conn)

    with pytest.raises(psycopg.errors.CheckViolation, match=WRONG_BANK):
        open_in_the_other_bank(conn if role == "owner" else app_conn, pmc, kind, tenant)

    assert opened_accounts(conn) == before  # not even the bare pgledger account


def test_each_trust_bank_account_holds_only_its_own_kind_of_money(conn):
    pmc = make_pmc(conn)

    kinds = conn.execute(
        "SELECT b.kind, array_agg(DISTINCT t.kind ORDER BY t.kind)"
        " FROM trust_ledger_accounts t JOIN trust_bank_accounts b ON b.id = t.bank_account_id"
        " WHERE t.pmc_id = %s GROUP BY b.kind",
        (pmc.pmc_id,),
    ).fetchall()

    assert dict(kinds) == {
        "operating": [
            "bank_cash",
            "owner_property",
            "pmc_income",
            "prepaid_rent",
            "vendor_payable",
        ],
        "security_deposit": ["bank_cash", "tenant_deposit"],
    }


def test_the_deposit_accounts_cash_is_exactly_the_deposits_it_holds(conn):
    pmc = make_pmc(conn)
    owner = pmc.owners[0].account
    transfer(conn, pmc.deposit_cash, pmc.tenant_deposit, "1500.00")  # deposit received
    # At move-out, 320.00 is kept for damages and paid to the owner with its cash...
    transfer_batch(
        conn,
        [(pmc.tenant_deposit, owner, "320.00"), (pmc.operating_cash, pmc.deposit_cash, "320.00")],
    )
    transfer(conn, pmc.tenant_deposit, pmc.deposit_cash, "1180.00")  # ...and the rest refunded

    deposit_cash, deposits = conn.execute(
        "SELECT -sum(a.balance) FILTER (WHERE t.kind = 'bank_cash'),"
        " sum(a.balance) FILTER (WHERE t.kind = 'tenant_deposit')"
        " FROM trust_ledger_accounts t JOIN pgledger_accounts a ON a.id = t.ledger_account_id"
        " WHERE t.bank_account_id = %s",
        (pmc.deposit_bank_id,),
    ).fetchone()
    assert deposit_cash == deposits == 0
    assert bank_sums(conn, pmc.pmc_id) == {pmc.operating_bank_id: 0, pmc.deposit_bank_id: 0}


@pytest.mark.parametrize("bank", ["deposit", "operating", "unused"])
def test_a_trust_bank_accounts_kind_never_changes(conn, bank):
    pmc = make_pmc(conn)
    unused = conn.execute(
        "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
        " VALUES (%s, 'operating', 'Synthetic spare trust account') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    bank_id = {"deposit": pmc.deposit_bank_id, "operating": pmc.operating_bank_id}.get(bank, unused)
    kind = conn.execute("SELECT kind FROM trust_bank_accounts WHERE id = %s", (bank_id,)).fetchone()

    with pytest.raises(psycopg.errors.RestrictViolation, match="kind never changes"):
        conn.execute(
            "UPDATE trust_bank_accounts"
            " SET kind = CASE kind WHEN 'operating' THEN 'security_deposit' ELSE 'operating' END"
            " WHERE id = %s",
            (bank_id,),
        )

    assert (
        conn.execute("SELECT kind FROM trust_bank_accounts WHERE id = %s", (bank_id,)).fetchone()
        == kind
    )


def test_a_trust_bank_account_can_still_be_renamed(conn, app_conn):
    pmc = make_pmc(conn)

    app_conn.execute(
        "UPDATE trust_bank_accounts SET display_name = 'Renamed deposit account' WHERE id = %s",
        (pmc.deposit_bank_id,),
    )
    assert conn.execute(
        "SELECT kind, display_name FROM trust_bank_accounts WHERE id = %s", (pmc.deposit_bank_id,)
    ).fetchone() == ("security_deposit", "Renamed deposit account")

    # A tool that saves the whole row writes the kind back unchanged; the rename still lands.
    conn.execute(
        "UPDATE trust_bank_accounts SET kind = kind, display_name = 'Saved whole' WHERE id = %s",
        (pmc.deposit_bank_id,),
    )
    assert conn.execute(
        "SELECT kind, display_name FROM trust_bank_accounts WHERE id = %s", (pmc.deposit_bank_id,)
    ).fetchone() == ("security_deposit", "Saved whole")


def test_the_migration_refuses_books_that_already_mix_them(database_url, tmp_path):
    migrations = sorted((ROOT / "db/migrations").glob("*.sql"))
    separation = next(m for m in migrations if m.name.startswith("20261009000008"))
    for migration in migrations[: migrations.index(separation)]:
        shutil.copy(migration, tmp_path)

    with scratch_database(database_url, "pms_deposit_account") as url:
        dbmate = Dbmate(shutil.which("dbmate"), tmp_path, url)
        assert dbmate.run("--no-dump-schema", "up").returncode == 0
        with psycopg.connect(url, autocommit=True) as conn:
            pmc = make_pmc(conn)
            # Allowed before this migration: a deposit held in the operating account.
            conn.execute(
                "SELECT trust_open_ledger_account(%s, %s, 'tenant_deposit', NULL, NULL, %s)",
                (pmc.pmc_id, pmc.operating_bank_id, tenant_of(conn, pmc)),
            )
        shutil.copy(separation, tmp_path)

        applied = dbmate.run("--no-dump-schema", "up")

    assert applied.returncode != 0
    assert "tenant_deposit account" in applied.stderr
    assert "is in the operating trust bank account; move it before migrating" in applied.stderr


def test_the_migration_applies_to_books_that_keep_them_apart(database_url, tmp_path):
    migrations = sorted((ROOT / "db/migrations").glob("*.sql"))
    separation = next(m for m in migrations if m.name.startswith("20261009000008"))
    for migration in migrations[: migrations.index(separation)]:
        shutil.copy(migration, tmp_path)

    with scratch_database(database_url, "pms_deposit_account_ok") as url:
        dbmate = Dbmate(shutil.which("dbmate"), tmp_path, url)
        assert dbmate.run("--no-dump-schema", "up").returncode == 0
        with psycopg.connect(url, autocommit=True) as conn:
            pmc = make_pmc(conn)
            transfer(conn, pmc.deposit_cash, pmc.tenant_deposit, Decimal("900.00"))
        shutil.copy(separation, tmp_path)

        applied = dbmate.run("--no-dump-schema", "up")

    assert applied.returncode == 0, applied.stderr
