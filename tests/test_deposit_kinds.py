"""A deposit's kind (migration 20261018000092; the founder's review, q043): a tenant's pet or key
deposit is held in a deposit account of its own, beside their security deposit, in the security-
deposit trust bank account; a departing tenant's deposits move to the staying tenant's of the same
kind, and the register and the return's statement name each kind. The API's side is in
tests/api/test_deposit_kinds_api.py. Every name here is synthetic."""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_tenant,
    add_unit,
    balance,
    bank_sums,
    charge,
    make_pmc,
    open_lease,
    return_deposit,
    transfer_batch,
)

JAN_1, MAR_1, JUN_30, JUL_5 = (
    date(2026, 1, 1),
    date(2026, 3, 1),
    date(2026, 6, 30),
    date(2026, 7, 5),
)


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def open_deposit(conn, pmc_id, bank, tenant, kind):
    return conn.execute(
        "SELECT trust_open_deposit_account(%s, %s, %s, %s)", (pmc_id, bank, tenant, kind)
    ).fetchone()[0]


def kind_of(conn, account):
    return conn.execute(
        "SELECT kind, deposit_kind FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (account,),
    ).fetchone()


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def tenant(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]


def test_a_tenant_holds_a_deposit_account_of_each_kind_found_or_opened(conn, pmc, tenant):
    pet = open_deposit(conn, pmc.pmc_id, pmc.deposit_bank_id, tenant, "pet")
    key = open_deposit(conn, pmc.pmc_id, pmc.deposit_bank_id, tenant, "key")

    assert (
        open_deposit(conn, pmc.pmc_id, pmc.deposit_bank_id, tenant, "security")
        == pmc.tenant_deposit
    )
    assert open_deposit(conn, pmc.pmc_id, pmc.deposit_bank_id, tenant, "pet") == pet
    assert len({pmc.tenant_deposit, pet, key}) == 3
    assert [kind_of(conn, a) for a in (pmc.tenant_deposit, pet, key)] == [
        ("tenant_deposit", None),
        ("tenant_deposit", "pet"),
        ("tenant_deposit", "key"),
    ]
    # Another tenant's of a kind is their own.
    other = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 2")
    assert open_deposit(conn, pmc.pmc_id, pmc.deposit_bank_id, other, "pet") not in {pet, key}


def test_a_deposit_is_of_a_known_kind_and_only_a_tenant_deposit_has_one(conn, pmc, tenant):
    for kind in ("cleaning", None, ""):
        with pytest.raises(psycopg.errors.InvalidParameterValue, match="security, pet or key"):
            open_deposit(conn, pmc.pmc_id, pmc.deposit_bank_id, tenant, kind)
    # Held in trust like any deposit: never in the operating trust bank account.
    with pytest.raises(psycopg.errors.CheckViolation):
        open_deposit(conn, pmc.pmc_id, pmc.operating_bank_id, tenant, "pet")
    pgledger_id = conn.execute(
        "SELECT id FROM pgledger_create_account('tenant_deposit', 'USD', false)"
    ).fetchone()[0]
    for kind, deposit_kind in (("prepaid_rent", "pet"), ("tenant_deposit", "cleaning")):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO trust_ledger_accounts (ledger_account_id, pmc_id, bank_account_id,"
                " kind, tenant_id, deposit_kind) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    pgledger_id,
                    pmc.pmc_id,
                    pmc.operating_bank_id if kind == "prepaid_rent" else pmc.deposit_bank_id,
                    kind,
                    tenant,
                    deposit_kind,
                ),
            )
    # One account of a kind per tenant and trust bank account.
    pet = open_deposit(conn, pmc.pmc_id, pmc.deposit_bank_id, tenant, "pet")
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO trust_ledger_accounts (ledger_account_id, pmc_id, bank_account_id, kind,"
            " tenant_id, deposit_kind) VALUES (%s, %s, %s, 'tenant_deposit', %s, 'pet')",
            (pgledger_id, pmc.pmc_id, pmc.deposit_bank_id, tenant),
        )
    assert pet is not None


def test_a_leaving_tenants_deposits_move_to_the_staying_tenants_of_the_same_kind(conn, pmc, tenant):
    staying = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 2")
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "1000.00", [tenant, staying])
    pet = open_deposit(conn, pmc.pmc_id, pmc.deposit_bank_id, tenant, "pet")
    theirs = open_deposit(conn, pmc.pmc_id, pmc.deposit_bank_id, staying, "security")
    transfer_batch(
        conn,
        [
            (pmc.deposit_cash, pmc.tenant_deposit, "1000.00"),
            (pmc.deposit_cash, pet, "300.00"),
            (pmc.deposit_cash, theirs, "500.00"),
        ],
        at(JAN_1),
    )

    moved = conn.execute(
        "SELECT trust_remove_lease_tenant(%s, %s, %s, %s, %s)",
        (pmc.pmc_id, lease, tenant, MAR_1, staying),
    ).fetchone()[0]

    their_pet = open_deposit(conn, pmc.pmc_id, pmc.deposit_bank_id, staying, "pet")
    assert moved == Decimal("1300.00")
    assert (balance(conn, pmc.tenant_deposit), balance(conn, pet)) == (0, 0)
    assert (balance(conn, theirs), balance(conn, their_pet)) == (
        Decimal("1500.00"),
        Decimal("300.00"),
    )
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}


def test_the_statement_and_the_register_name_each_kind(conn, pmc, tenant):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, JUN_30, "1000.00", [tenant])
    pet = open_deposit(conn, pmc.pmc_id, pmc.deposit_bank_id, tenant, "pet")
    transfer_batch(
        conn,
        [(pmc.deposit_cash, pmc.tenant_deposit, "1000.00"), (pmc.deposit_cash, pet, "300.00")],
        at(JAN_1),
    )
    register = conn.execute(
        "SELECT item, tenant, held FROM trust_report_security_deposits(%s, %s) WHERE held IS NOT"
        " NULL AND tenant IS NOT NULL",
        (pmc.pmc_id, MAR_1),
    ).fetchall()
    charge(conn, pmc.pmc_id, lease, JUN_30, "1100.00", memo="June rent")

    back = return_deposit(conn, pmc.pmc_id, lease, "back", at(JUL_5))
    statement = conn.execute(
        "SELECT item, detail, amount FROM trust_report_deposit_return(%s, %s)"
        " WHERE item IN ('deposit held', 'returned to')",
        (pmc.pmc_id, lease),
    ).fetchall()

    assert register == [
        ("deposit", "Tenant 1", Decimal("1000.00")),
        ("pet deposit", "Tenant 1", Decimal("300.00")),
    ]
    assert back == Decimal("200.00")
    assert statement == [
        ("deposit held", "Tenant 1", Decimal("1000.00")),
        ("deposit held", "Tenant 1 (pet deposit)", Decimal("300.00")),
        ("returned to", "Tenant 1 (pet deposit)", Decimal("200.00")),
    ]
    assert (balance(conn, pmc.tenant_deposit), balance(conn, pet)) == (0, 0)


def test_a_key_deposit_reads_as_one(conn, pmc, tenant):
    key = open_deposit(conn, pmc.pmc_id, pmc.deposit_bank_id, tenant, "key")
    transfer_batch(conn, [(pmc.deposit_cash, key, "50.00")], at(JAN_1))

    assert conn.execute(
        "SELECT item FROM trust_report_security_deposits(%s, %s) WHERE tenant = 'Tenant 1'",
        (pmc.pmc_id, MAR_1),
    ).fetchall() == [("key deposit",)]
