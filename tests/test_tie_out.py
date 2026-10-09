"""Each trust bank account ties out: its book cash equals everything held for someone in it.

Checked by trust_check_bank_tie_out at commit (migration 20261009000005), so money held in one
trust bank account moves to another only together with its cash.
"""

import shutil
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from helpers import bank_sums, make_pmc, snapshot, transfer, transfer_batch

from tools.check_rollback import Dbmate
from tools.scratch_db import scratch_database

ROOT = Path(__file__).resolve().parents[1]
DEPOSIT = Decimal("1500.00")
DAMAGES = Decimal("320.00")


def funded(conn):
    """A PMC holding a security deposit, and rent for the first owner."""
    pmc = make_pmc(conn)
    transfer(conn, pmc.deposit_cash, pmc.tenant_deposit, DEPOSIT)
    transfer(conn, pmc.operating_cash, pmc.owners[0].account, "2000.00")
    return pmc


def test_every_trust_bank_account_ties_out_after_ordinary_postings(conn):
    pmc = funded(conn)
    transfer(conn, pmc.operating_cash, pmc.prepaid_rent, "950.00")
    transfer(conn, pmc.owners[0].account, pmc.vendor_payable, "410.00")
    transfer(conn, pmc.vendor_payable, pmc.operating_cash, "410.00")  # the bill is paid

    assert bank_sums(conn, pmc.pmc_id) == {
        pmc.operating_bank_id: 0,
        pmc.deposit_bank_id: 0,
    }


def test_held_money_cannot_change_trust_account_without_its_cash(conn):
    pmc = funded(conn)
    before = snapshot(conn, pmc.accounts())

    with pytest.raises(psycopg.errors.CheckViolation, match="does not tie out"):
        transfer(conn, pmc.tenant_deposit, pmc.owners[0].account, DAMAGES)

    assert snapshot(conn, pmc.accounts()) == before


def test_a_deposit_kept_for_damages_moves_with_its_cash(conn):
    pmc = funded(conn)
    owner = pmc.owners[0].account

    transfer_batch(
        conn,
        [
            (pmc.tenant_deposit, owner, DAMAGES),  # the owner is paid for the repairs...
            (pmc.operating_cash, pmc.deposit_cash, DAMAGES),  # ...with cash between the banks
        ],
    )

    assert bank_sums(conn, pmc.pmc_id) == {pmc.operating_bank_id: 0, pmc.deposit_bank_id: 0}


@pytest.mark.parametrize("second_leg", [True, False])
def test_the_check_runs_at_commit(conn, database_url, second_leg):
    pmc = funded(conn)
    owner = pmc.owners[0].account
    before = snapshot(conn, pmc.accounts())

    with psycopg.connect(database_url) as tx:
        transfer(tx, pmc.tenant_deposit, owner, DAMAGES)  # off until the cash moves too
        if second_leg:
            transfer(tx, pmc.operating_cash, pmc.deposit_cash, DAMAGES)
            tx.commit()
        else:
            with pytest.raises(psycopg.errors.CheckViolation, match="does not tie out"):
                tx.commit()

    if second_leg:
        assert bank_sums(conn, pmc.pmc_id) == {pmc.operating_bank_id: 0, pmc.deposit_bank_id: 0}
    else:
        assert snapshot(conn, pmc.accounts()) == before


@pytest.mark.parametrize(
    ("kind", "holder"),
    [("vendor_payable", None), ("prepaid_rent", None), ("prepaid_rent", "owner")],
)
def test_each_new_kind_needs_its_holder_and_nothing_else(conn, kind, holder):
    pmc = make_pmc(conn)
    owner = pmc.owners[0]
    with pytest.raises(psycopg.errors.CheckViolation, match="kind_shape"):
        conn.execute(
            "SELECT trust_open_ledger_account(%s, %s, %s, %s, %s, NULL)",
            (
                pmc.pmc_id,
                pmc.operating_bank_id,
                kind,
                owner.owner_id if holder == "owner" else None,
                owner.property_id if holder == "owner" else None,
            ),
        )


def test_one_prepaid_rent_and_one_vendor_account_per_holder(conn):
    pmc = make_pmc(conn)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]

    with pytest.raises(psycopg.errors.UniqueViolation, match="one_prepaid_rent"):
        conn.execute(
            "SELECT trust_open_ledger_account(%s, %s, 'prepaid_rent', NULL, NULL, %s)",
            (pmc.pmc_id, pmc.operating_bank_id, tenant),
        )
    with pytest.raises(psycopg.errors.UniqueViolation, match="one_vendor_payable"):
        conn.execute(
            "SELECT trust_open_vendor_account(%s, %s, %s)",
            (pmc.pmc_id, pmc.operating_bank_id, pmc.vendor_id),
        )


def test_a_vendor_account_stays_in_its_vendors_pmc(conn):
    pmc, other = make_pmc(conn), make_pmc(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="vendor_id"):
        conn.execute(
            "SELECT trust_open_vendor_account(%s, %s, %s)",
            (pmc.pmc_id, pmc.operating_bank_id, other.vendor_id),
        )


def test_the_app_role_adds_vendors_and_opens_their_accounts(conn, app_conn):
    pmc = make_pmc(conn)
    vendor = app_conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor 2') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]

    account = app_conn.execute(
        "SELECT trust_open_vendor_account(%s, %s, %s)",
        (pmc.pmc_id, pmc.operating_bank_id, vendor),
    ).fetchone()[0]

    kind = conn.execute(
        "SELECT kind FROM trust_ledger_accounts WHERE ledger_account_id = %s", (account,)
    ).fetchone()[0]
    assert kind == "vendor_payable"


def deposit_and_owner_before_vendors(conn):
    """A funded tenant deposit and an owner account, on the schema before vendors existed
    (helpers.make_pmc needs trust_vendors)."""

    def one(query, *params):
        return conn.execute(query, params).fetchone()[0]

    pmc = one("INSERT INTO trust_pmcs (display_name) VALUES ('Synthetic PMC') RETURNING pmc_id")
    bank = {
        kind: one(
            "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
            " VALUES (%s, %s, 'Synthetic account') RETURNING id",
            pmc,
            kind,
        )
        for kind in ("operating", "security_deposit")
    }
    owner = one(
        "INSERT INTO trust_owners (pmc_id, display_name) VALUES (%s, 'Owner 1') RETURNING id", pmc
    )
    prop = one(
        "INSERT INTO trust_properties (pmc_id, display_name)"
        " VALUES (%s, 'Property 1') RETURNING id",
        pmc,
    )
    tenant = one(
        "INSERT INTO trust_tenants (pmc_id, property_id, display_name)"
        " VALUES (%s, %s, 'Tenant 1') RETURNING id",
        pmc,
        prop,
    )
    opened = "SELECT trust_open_ledger_account(%s, %s, %s, %s, %s, %s)"
    deposit_cash = one(opened, pmc, bank["security_deposit"], "bank_cash", None, None, None)
    deposit = one(opened, pmc, bank["security_deposit"], "tenant_deposit", None, None, tenant)
    owner_account = one(opened, pmc, bank["operating"], "owner_property", owner, prop, None)
    transfer(conn, deposit_cash, deposit, DEPOSIT)
    return deposit, owner_account


def test_the_migration_refuses_books_that_already_do_not_tie_out(database_url, tmp_path):
    migrations = sorted((ROOT / "db/migrations").glob("*.sql"))
    tie_out = next(m for m in migrations if m.name.startswith("20261009000005"))
    for migration in migrations[: migrations.index(tie_out)]:
        shutil.copy(migration, tmp_path)

    with scratch_database(database_url, "pms_tie_out") as url:
        dbmate = Dbmate(shutil.which("dbmate"), tmp_path, url)
        assert dbmate.run("--no-dump-schema", "up").returncode == 0
        with psycopg.connect(url, autocommit=True) as conn:
            deposit, owner = deposit_and_owner_before_vendors(conn)
            # Allowed before the tie-out rule: held money changes bank account without its cash.
            transfer(conn, deposit, owner, DAMAGES)
        shutil.copy(tie_out, tmp_path)

        applied = dbmate.run("--no-dump-schema", "up")

    assert applied.returncode != 0
    assert "does not tie out; fix its books before migrating" in applied.stderr
