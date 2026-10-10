"""Helpers shared by the tests: a synthetic PMC and thin wrappers over the ledger functions.

Every fixture is synthetic: no real names, addresses, bank numbers or tax IDs.
"""

import os
import shutil
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from tools.check_rollback import Dbmate
from tools.scratch_db import scratch_database

MIGRATIONS = Path(
    os.environ.get("PMS_MIGRATIONS_DIR", Path(__file__).resolve().parents[1] / "db/migrations")
)

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


def transfer_batch(connection, requests, event_at=None) -> list[str]:
    """Post several transfers in one call: all of them are written, or none. Only the owner
    role may; the app posts through `post`, with an idempotency key."""
    rows = sql.SQL(", ").join(sql.SQL("(%s, %s, %s::numeric)::transfer_request") for _ in requests)
    query = sql.SQL("SELECT id FROM pgledger_create_transfers(ARRAY[{}], %s)").format(rows)
    params = [*(value for request in requests for value in request), event_at]
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


def approve(
    connection,
    pmc_id,
    bank_id,
    period_start,
    period_end,
    statement_balance="0.00",
    prepared_by="Preparer 1",
    approved_by="Approver 1",
) -> UUID:
    """Approve a reconciliation of one trust bank account, closing it through period_end."""
    return _one(
        connection,
        "SELECT trust_approve_reconciliation(%s, %s, %s, %s, %s, %s, %s)",
        (
            pmc_id,
            bank_id,
            period_start,
            period_end,
            Decimal(statement_balance),
            prepared_by,
            approved_by,
        ),
    )


def add_unit(connection, pmc_id, property_id, name="Unit 1") -> UUID:
    return _one(
        connection,
        "INSERT INTO trust_units (pmc_id, property_id, display_name) VALUES (%s, %s, %s)"
        " RETURNING id",
        (pmc_id, property_id, name),
    )


def add_tenant(connection, pmc_id, property_id, name) -> UUID:
    return _one(
        connection,
        "INSERT INTO trust_tenants (pmc_id, property_id, display_name) VALUES (%s, %s, %s)"
        " RETURNING id",
        (pmc_id, property_id, name),
    )


def open_lease(connection, pmc_id, unit_id, starts_on, ends_on, rent, tenant_ids) -> UUID:
    return _one(
        connection,
        "SELECT trust_open_lease(%s, %s, %s, %s, %s, %s::uuid[])",
        (pmc_id, unit_id, starts_on, ends_on, Decimal(rent), list(tenant_ids)),
    )


def end_lease(connection, pmc_id, lease_id, ends_on) -> None:
    connection.execute("SELECT trust_end_lease(%s, %s, %s)", (pmc_id, lease_id, ends_on))


def charge(connection, pmc_id, lease_id, due_on, amount, kind="rent", memo=None) -> UUID:
    return _one(
        connection,
        "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (pmc_id, lease_id, due_on, kind, Decimal(amount), memo),
    )


def apply_payment(connection, pmc_id, charge_id, transfer_id, amount) -> Decimal:
    return _one(
        connection,
        "SELECT trust_apply_payment(%s, %s, %s, %s)",
        (pmc_id, charge_id, transfer_id, None if amount is None else Decimal(amount)),
    )


def reverse_payment(connection, pmc_id, charge_id, transfer_id, reversal_id, amount) -> Decimal:
    return _one(
        connection,
        "SELECT trust_reverse_payment(%s, %s, %s, %s, %s)",
        (
            pmc_id,
            charge_id,
            transfer_id,
            reversal_id,
            None if amount is None else Decimal(amount),
        ),
    )


def add_agreement(
    connection,
    pmc_id,
    account,
    starts_on,
    fee_percent="0",
    minimum_fee="0",
    flat_fee="0",
    leasing_fee_percent="0",
    reserve="0",
) -> UUID:
    """A management agreement for one owner's property account, in force from starts_on."""
    return _one(
        connection,
        "INSERT INTO trust_management_agreements (pmc_id, ledger_account_id, starts_on,"
        " fee_percent, minimum_fee, flat_fee, leasing_fee_percent, reserve)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            pmc_id,
            account,
            starts_on,
            Decimal(fee_percent),
            Decimal(minimum_fee),
            Decimal(flat_fee),
            Decimal(leasing_fee_percent),
            Decimal(reserve),
        ),
    )


def post_management_fee(connection, pmc_id, account, period_start, period_end, event_at):
    return _one(
        connection,
        "SELECT trust_post_management_fee(%s, %s, %s, %s, %s)",
        (pmc_id, account, period_start, period_end, event_at),
    )


def post_leasing_fee(connection, pmc_id, lease_id, account, event_at):
    return _one(
        connection,
        "SELECT trust_post_leasing_fee(%s, %s, %s, %s)",
        (pmc_id, lease_id, account, event_at),
    )


def draw_owner(connection, pmc_id, account, request_key, amount, event_at):
    """Pay the owner `amount` (None: everything available) from one property's account."""
    return _one(
        connection,
        "SELECT trust_draw_owner(%s, %s, %s, %s, %s)",
        (pmc_id, account, request_key, None if amount is None else Decimal(amount), event_at),
    )


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


@contextmanager
def language_sorted_database(database_url) -> Iterator[psycopg.Connection]:
    """A connection to a fresh, fully migrated database that sorts text by English rules (ICU
    en-US), as most servers do: "owner a" sorts before "Owner b" there, and after it in byte
    order. The test database itself may sort either way, so a report's ordering is checked here."""
    with scratch_database(database_url, "pms_icu", icu_locale="en-US") as url:
        migrated = Dbmate(shutil.which("dbmate"), MIGRATIONS, url).run("--no-dump-schema", "up")
        assert migrated.returncode == 0, migrated.stderr
        with psycopg.connect(url, autocommit=True) as conn:
            assert conn.execute("SELECT 'owner a' < 'Owner b'").fetchone() == (True,)
            yield conn


def wait_until_blocked(conn, pid):
    for _ in range(1000):
        row = conn.execute(
            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", (pid,)
        ).fetchone()
        if row and row[0] == "Lock":
            return
        time.sleep(0.01)
    raise AssertionError("the second transaction never waited for the first")


def in_background(database_url, work):
    """Run work(connection) on a new autocommit connection in a thread; return the thread,
    the connection's backend pid and a dict that receives the result or the error."""
    outcome: dict = {}
    ready = threading.Event()
    pid: list[int] = []

    def run():
        with psycopg.connect(database_url, autocommit=True) as worker:
            worker.execute("SET lock_timeout = '20s'")
            pid.append(worker.execute("SELECT pg_backend_pid()").fetchone()[0])
            ready.set()
            try:
                outcome["result"] = work(worker)
            except Exception as exc:  # asserted by the caller
                outcome["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    assert ready.wait(timeout=30)
    return thread, pid[0], outcome
