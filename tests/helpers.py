"""Helpers shared by the tests: a synthetic PMC and thin wrappers over the ledger functions.

Every fixture is synthetic: no real names, addresses, bank numbers or tax IDs.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from psycopg import sql
from psycopg.types.json import Jsonb

# A WHERE clause for catalog queries: leave out objects that belong to an extension, such as
# plpgsql_check, which only the coverage job's test database loads. Format in the catalog
# (`'pg_proc'`) and the object's oid column (`p.oid`).
NOT_FROM_AN_EXTENSION = """
    NOT EXISTS (
        SELECT 1 FROM pg_depend d
        WHERE d.classid = {catalog}::regclass AND d.objid = {oid} AND d.deptype = 'e'
    )
"""


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
    prepaid_rent: str  # the tenant's rent paid ahead, in the operating trust account
    vendor_id: UUID
    vendor_payable: str  # money set aside for the vendor's bill, in the operating trust account
    owners: list[Owner] = field(default_factory=list)

    def accounts(self) -> list[str]:
        return [
            self.operating_cash,
            self.deposit_cash,
            self.pmc_income,
            self.tenant_deposit,
            self.prepaid_rent,
            self.vendor_payable,
            *(owner.account for owner in self.owners),
        ]

    def cash_for(self, account) -> str:
        """The bank_cash account of the trust bank account that `account` sits in."""
        in_deposit_bank = account in (self.deposit_cash, self.tenant_deposit)
        return self.deposit_cash if in_deposit_bank else self.operating_cash


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

    vendor_id = _one(
        connection,
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
        (pmc_id, "Vendor 1"),
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
        prepaid_rent=open_account(
            connection, pmc_id, banks["operating"], "prepaid_rent", tenant_id=tenant_id
        ),
        vendor_id=vendor_id,
        vendor_payable=_one(
            connection,
            "SELECT trust_open_vendor_account(%s, %s, %s)",
            (pmc_id, banks["operating"], vendor_id),
        ),
        owners=owner_rows,
    )


def bank_sums(connection, pmc_id) -> dict:
    """Per trust bank account of the PMC, the sum of every balance in it: zero when it ties out."""
    return dict(
        connection.execute(
            """
            SELECT t.bank_account_id, sum(a.balance)
            FROM trust_ledger_accounts t JOIN pgledger_accounts a ON a.id = t.ledger_account_id
            WHERE t.pmc_id = %s
            GROUP BY t.bank_account_id
            """,
            (pmc_id,),
        ).fetchall()
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


def post(connection, pmc_id, key, requests, event_at=None, metadata=None) -> list[str]:
    """Post through trust_post_transfers with an idempotency key; the transfer ids, in order."""
    rows = sql.SQL(", ").join(sql.SQL("(%s, %s, %s::numeric)::transfer_request") for _ in requests)
    query = sql.SQL(
        "SELECT id FROM trust_post_transfers(%s, %s, ARRAY[{}]::transfer_request[], %s, %s)"
    ).format(rows)
    params = [pmc_id, key, *(value for request in requests for value in request)]
    params += [event_at, None if metadata is None else Jsonb(metadata)]
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


RUNNING_BALANCE_BREAKS = """
WITH ordered AS (
    SELECT e.account_id, e.account_version, e.amount,
           e.account_previous_balance AS previous, e.account_current_balance AS current,
           lag(e.account_current_balance, 1, 0::numeric) OVER w AS expected_previous,
           row_number() OVER w AS position
    FROM pgledger_entries e
    WHERE e.account_id = ANY(%(ids)s)
    WINDOW w AS (PARTITION BY e.account_id ORDER BY e.account_version)
)
SELECT account_id, account_version FROM ordered
WHERE previous <> expected_previous OR previous + amount <> current
   OR account_version <> position
UNION ALL
SELECT a.id, a.version
FROM pgledger_accounts a
LEFT JOIN LATERAL (
    SELECT count(*) AS entries,
           (array_agg(e.account_current_balance ORDER BY e.account_version DESC))[1] AS last
    FROM pgledger_entries e WHERE e.account_id = a.id
) s ON true
WHERE a.id = ANY(%(ids)s) AND (a.version <> s.entries OR a.balance <> coalesce(s.last, 0))
"""


def running_balance_breaks(connection, accounts) -> list[tuple]:
    """Entries whose running balance doesn't chain, or accounts whose version and balance don't
    match their last entry. Each entry must start where the previous one ended (from zero), add
    its amount, and carry the account's version: 1, 2, 3... A ledger an auditor can walk line
    by line (Cal. Reg. 2831's running balance)."""
    return connection.execute(RUNNING_BALANCE_BREAKS, {"ids": list(accounts)}).fetchall()
