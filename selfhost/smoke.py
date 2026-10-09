"""Self-host smoke test: proves the ledger runs on plain Postgres, with no Supabase features.

Against the database in $DATABASE_URL (already migrated by `dbmate up`), it:
  1. seeds a synthetic 50-door PMC: 10 owners, 50 properties, 50 tenants, and a security
     deposit held for each tenant;
  2. posts one month of rent for every door;
  3. runs the first owner's statement for that month, which must match the owner's ledgers;
  4. checks health: the database answers and every ledger invariant holds.

Every name and amount is synthetic. Exit code 0 means healthy.

usage: python -m selfhost.smoke
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg

DOORS = 50
OWNERS = 10
CHECKS = {
    "transfers whose entries don't balance": """
        SELECT count(*) FROM (
            SELECT t.id FROM pgledger_transfers t
            JOIN pgledger_entries e ON e.transfer_id = t.id
            GROUP BY t.id HAVING count(*) <> 2 OR sum(e.amount) <> 0
        ) bad""",
    "accounts whose balance isn't the sum of their entries": """
        SELECT count(*) FROM (
            SELECT a.id FROM pgledger_accounts a
            LEFT JOIN pgledger_entries e ON e.account_id = a.id
            GROUP BY a.id, a.balance HAVING a.balance <> coalesce(sum(e.amount), 0)
        ) bad""",
    "held accounts below zero": """
        SELECT count(*) FROM trust_ledger_accounts t
        JOIN pgledger_accounts a ON a.id = t.ledger_account_id
        WHERE t.kind <> 'bank_cash' AND a.balance < 0""",
    "trust bank accounts whose book cash doesn't equal what they hold for others": """
        SELECT count(*) FROM (
            SELECT t.bank_account_id FROM trust_ledger_accounts t
            JOIN pgledger_accounts a ON a.id = t.ledger_account_id
            GROUP BY t.bank_account_id HAVING sum(a.balance) <> 0
        ) bad""",
}


@dataclass
class Seeded:
    pmc_id: Any
    operating_cash: str
    deposit_cash: str
    doors: list[str] = field(default_factory=list)  # owner_property account per door
    owners: list[Any] = field(default_factory=list)  # owner ids, in order


def one(conn: psycopg.Connection[Any], query: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(query.encode(), params).fetchone()
    assert row is not None
    return row[0]


def wait_for_database(url: str, attempts: int = 30, delay: float = 1.0) -> psycopg.Connection[Any]:
    for attempt in range(1, attempts + 1):
        try:
            return psycopg.connect(url, autocommit=True)
        except psycopg.OperationalError:
            if attempt == attempts:
                raise
            time.sleep(delay)
    raise AssertionError("unreachable")


def open_account(conn: psycopg.Connection[Any], *args: Any) -> str:
    padded = (*args, None, None, None)[:6]
    return str(one(conn, "SELECT trust_open_ledger_account(%s, %s, %s, %s, %s, %s)", padded))


def rent_for(door: int) -> Decimal:
    return Decimal(1150 + 25 * (door % 9))


def transfer(
    conn: psycopg.Connection[Any], source: str, target: str, amount: Decimal, when: date
) -> None:
    event_at = datetime(when.year, when.month, when.day, tzinfo=UTC)
    conn.execute(
        "SELECT pgledger_create_transfer(%s, %s, %s, %s)", (source, target, amount, event_at)
    )


def seed(conn: psycopg.Connection[Any], doors: int = DOORS, owners: int = OWNERS) -> Seeded:
    pmc_id = one(
        conn,
        "INSERT INTO trust_pmcs (display_name) VALUES (%s) RETURNING pmc_id",
        (f"Synthetic Self-Host PMC {doors} doors",),
    )
    banks = {
        kind: one(
            conn,
            "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
            " VALUES (%s, %s, %s) RETURNING id",
            (pmc_id, kind, f"Synthetic {kind} trust account"),
        )
        for kind in ("operating", "security_deposit")
    }
    seeded = Seeded(
        pmc_id=pmc_id,
        operating_cash=open_account(conn, pmc_id, banks["operating"], "bank_cash"),
        deposit_cash=open_account(conn, pmc_id, banks["security_deposit"], "bank_cash"),
    )
    open_account(conn, pmc_id, banks["operating"], "pmc_income")
    owner_ids = [
        one(
            conn,
            "INSERT INTO trust_owners (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
            (pmc_id, f"Owner {n:02d}"),
        )
        for n in range(1, owners + 1)
    ]
    seeded.owners = owner_ids
    for door in range(1, doors + 1):
        owner_id = owner_ids[(door - 1) % owners]
        property_id = one(
            conn,
            "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
            (pmc_id, f"Door {door:02d}"),
        )
        seeded.doors.append(
            open_account(conn, pmc_id, banks["operating"], "owner_property", owner_id, property_id)
        )
        tenant_id = one(
            conn,
            "INSERT INTO trust_tenants (pmc_id, property_id, display_name)"
            " VALUES (%s, %s, %s) RETURNING id",
            (pmc_id, property_id, f"Tenant {door:02d}"),
        )
        deposit = open_account(
            conn, pmc_id, banks["security_deposit"], "tenant_deposit", None, None, tenant_id
        )
        transfer(conn, seeded.deposit_cash, deposit, rent_for(door), date(2026, 1, 1))
    return seeded


def post_month_of_rent(conn: psycopg.Connection[Any], seeded: Seeded, month: date) -> Decimal:
    total = Decimal(0)
    for door, account in enumerate(seeded.doors, start=1):
        transfer(conn, seeded.operating_cash, account, rent_for(door), month)
        total += rent_for(door)
    return total


@dataclass
class Statement:
    owner: str
    properties: int
    closing: Decimal  # all properties, from the statement
    held: Decimal  # the same owner's ledger balances, read directly


def owner_statement(conn: psycopg.Connection[Any], seeded: Seeded, month: date) -> Statement:
    """The first owner's statement for the month, and what their ledgers hold, to compare."""
    start = datetime(month.year, month.month, 1, tzinfo=UTC)
    end = datetime(month.year + month.month // 12, month.month % 12 + 1, 1, tzinfo=UTC)
    owner_id = seeded.owners[0]
    rows = conn.execute(
        "SELECT item, detail, balance FROM trust_report_owner_statement(%s, %s, %s, %s)",
        (seeded.pmc_id, owner_id, start, end),
    ).fetchall()
    held = one(
        conn,
        "SELECT coalesce(sum(a.balance), 0) FROM trust_ledger_accounts t"
        " JOIN pgledger_accounts a ON a.id = t.ledger_account_id"
        " WHERE t.owner_id = %s AND t.kind = 'owner_property'",
        (owner_id,),
    )
    return Statement(
        owner=next(detail for item, detail, _ in rows if item == "owner"),
        properties=sum(1 for item, _, _ in rows if item == "closing balance"),
        closing=next(b for item, _, b in rows if item == "all properties: closing balance"),
        held=held,
    )


def health(conn: psycopg.Connection[Any]) -> list[str]:
    problems = []
    if one(conn, "SELECT 1") != 1:
        problems.append("database did not answer SELECT 1")
    for label, query in CHECKS.items():
        count = one(conn, query)
        if count:
            problems.append(f"{count} {label}")
    return problems


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("FAIL: DATABASE_URL is not set.")
        return 1
    with wait_for_database(url) as conn:
        seeded = seed(conn)
        print(
            f"seeded: 1 PMC, {OWNERS} owners, {len(seeded.doors)} doors with tenants and deposits"
        )
        total = post_month_of_rent(conn, seeded, date(2026, 2, 1))
        print(f"posted: {len(seeded.doors)} rent payments for 2026-02, {total} in total")
        statement = owner_statement(conn, seeded, date(2026, 2, 1))
        print(
            f"owner statement: {statement.owner}, 2026-02, {statement.properties} properties,"
            f" closing {statement.closing}"
        )
        problems = health(conn)
        if statement.closing != statement.held:
            problems.append(
                f"{statement.owner}'s statement closes at {statement.closing}"
                f" but their ledgers hold {statement.held}"
            )
    for problem in problems:
        print(f"FAIL: {problem}")
    if not problems:
        print("healthy: every ledger invariant holds")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
