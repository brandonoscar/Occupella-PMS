"""Helpers shared by the tests: a synthetic PMC and thin wrappers over the ledger functions.

Every fixture is synthetic: no real names, addresses, bank numbers or tax IDs.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from psycopg import sql


@dataclass
class Owner:
    owner_id: UUID
    property_id: UUID
    account: str  # owner_property ledger account


@dataclass
class Pmc:
    pmc_id: UUID
    operating_bank_id: UUID
    deposit_bank_id: UUID
    operating_cash: str  # bank_cash ledger account of the operating trust account
    deposit_cash: str  # bank_cash ledger account of the security-deposit trust account
    pmc_income: str
    tenant_deposit: str
    owners: list[Owner] = field(default_factory=list)


def _one(connection, query, params=()):
    return connection.execute(query, params).fetchone()[0]


def open_account(
    connection, pmc_id, bank_id, kind, owner_id=None, property_id=None, tenant_id=None
):
    return _one(
        connection,
        "SELECT trust_open_ledger_account(%s, %s, %s, %s, %s, %s)",
        (pmc_id, bank_id, kind, owner_id, property_id, tenant_id),
    )


def make_pmc(connection, owners=2) -> Pmc:
    """A synthetic PMC: two trust bank accounts, `owners` owners with one property each, one
    tenant, and every kind of ledger account. Balances all start at zero."""
    tag = uuid.uuid4().hex[:8]
    pmc_id = _one(
        connection,
        "INSERT INTO trust_pmcs (display_name) VALUES (%s) RETURNING pmc_id",
        (f"Synthetic PMC {tag}",),
    )
    banks = {}
    for kind in ("operating", "security_deposit"):
        banks[kind] = _one(
            connection,
            "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
            " VALUES (%s, %s, %s) RETURNING id",
            (pmc_id, kind, f"Synthetic {kind} trust account"),
        )

    owner_rows = []
    for n in range(1, owners + 1):
        owner_id = _one(
            connection,
            "INSERT INTO trust_owners (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
            (pmc_id, f"Owner {n}"),
        )
        property_id = _one(
            connection,
            "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
            (pmc_id, f"Property {n}"),
        )
        account = open_account(
            connection, pmc_id, banks["operating"], "owner_property", owner_id, property_id
        )
        owner_rows.append(Owner(owner_id, property_id, account))

    tenant_id = _one(
        connection,
        "INSERT INTO trust_tenants (pmc_id, property_id, display_name)"
        " VALUES (%s, %s, %s) RETURNING id",
        (pmc_id, owner_rows[0].property_id, "Tenant 1"),
    )

    return Pmc(
        pmc_id=pmc_id,
        operating_bank_id=banks["operating"],
        deposit_bank_id=banks["security_deposit"],
        operating_cash=open_account(connection, pmc_id, banks["operating"], "bank_cash"),
        deposit_cash=open_account(connection, pmc_id, banks["security_deposit"], "bank_cash"),
        pmc_income=open_account(connection, pmc_id, banks["operating"], "pmc_income"),
        tenant_deposit=open_account(
            connection, pmc_id, banks["security_deposit"], "tenant_deposit", tenant_id=tenant_id
        ),
        owners=owner_rows,
    )


def transfer(connection, from_account, to_account, amount) -> str:
    return _one(
        connection,
        "SELECT id FROM pgledger_create_transfer(%s, %s, %s)",
        (from_account, to_account, Decimal(amount)),
    )


def transfer_batch(connection, requests) -> list[str]:
    """Post several transfers in one call: all of them are written, or none."""
    rows = sql.SQL(", ").join(sql.SQL("(%s, %s, %s::numeric)::transfer_request") for _ in requests)
    query = sql.SQL("SELECT id FROM pgledger_create_transfers(ARRAY[{}])").format(rows)
    params = [value for request in requests for value in request]
    return [row[0] for row in connection.execute(query, params).fetchall()]


def balance(connection, account) -> Decimal:
    return _one(connection, "SELECT balance FROM pgledger_accounts WHERE id = %s", (account,))


def snapshot(connection, accounts):
    """Everything a refused transfer must leave untouched: balances, versions, transfers and
    entries for the given accounts."""
    return connection.execute(
        """
        SELECT
            (SELECT array_agg((id, balance, version) ORDER BY id)
               FROM pgledger_accounts WHERE id = ANY(%(ids)s)),
            (SELECT count(*) FROM pgledger_transfers
              WHERE from_account_id = ANY(%(ids)s) OR to_account_id = ANY(%(ids)s)),
            (SELECT count(*) FROM pgledger_entries WHERE account_id = ANY(%(ids)s))
        """,
        {"ids": list(accounts)},
    ).fetchone()
