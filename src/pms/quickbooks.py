"""The trust ledger for QuickBooks (the founder's review q117): the ledger's transfers over days
(UTC) as general journal entries QuickBooks imports, each ledger account named as the company's
QuickBooks account for it (trust_quickbooks_accounts, migration 0102), or by a name the PMS makes
from its kind and holder ("Owner funds:Owner 1 - Property 1", a subaccount of Owner funds).

A transfer moves money from one ledger account to another; as a journal entry it debits the
account it leaves and credits the account it reaches: money coming into a trust bank account
debits its cash (an asset) and credits what is held for someone (a liability), and a payout
the other way round. So every entry balances, and an account's debits less its credits over the
days are its movement in the ledger.

Two files: QuickBooks Desktop's IIF (a TRNS line, an SPL line that balances it, ENDTRNS) and
QuickBooks Online's journal entry import (CSV: Journal No., Journal Date, Account Name, Debits,
Credits, Description, Name). Both read in Intuit's help and community answers, not tried in a
QuickBooks company file (NOT VERIFIED). The export reads; it writes nothing and moves no money.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import psycopg

from pms import exports

# A ledger account's QuickBooks parent account, by its kind.
PARENTS = {
    "bank_cash": "Trust cash",
    "owner_property": "Owner funds",
    "prepaid_rent": "Prepaid rent",
    "tenant_deposit": "Tenant deposits",
    "vendor_payable": "Vendor bills set aside",
    "pmc_income": "Fees earned in trust",
}

ACCOUNTS = """
SELECT t.ledger_account_id, t.kind, b.display_name,
       CASE t.kind
           WHEN 'bank_cash' THEN b.display_name
           WHEN 'owner_property' THEN o.display_name || ' - ' || pr.display_name
           WHEN 'vendor_payable' THEN v.display_name
           WHEN 'pmc_income' THEN b.display_name
           ELSE tn.display_name || coalesce(' (' || t.deposit_kind || ' deposit)', '')
       END,
       (SELECT q.name FROM trust_quickbooks_accounts AS q
        WHERE q.pmc_id = t.pmc_id AND q.ledger_account_id = t.ledger_account_id
        ORDER BY q.id DESC LIMIT 1)
FROM trust_ledger_accounts AS t
JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id
LEFT JOIN trust_owners AS o ON o.id = t.owner_id
LEFT JOIN trust_properties AS pr ON pr.id = t.property_id
LEFT JOIN trust_tenants AS tn ON tn.id = t.tenant_id
LEFT JOIN trust_vendors AS v ON v.id = t.vendor_id
WHERE t.pmc_id = %(pmc)s
"""

# A transfer never crosses PMCs (CLAUDE.md), so its from-account places it.
TRANSFERS = """
SELECT tr.id, tr.event_at, tr.from_account_id, tr.to_account_id, tr.amount,
       tr.metadata ->> 'memo'
FROM pgledger_transfers AS tr
JOIN trust_ledger_accounts AS t ON t.ledger_account_id = tr.from_account_id
WHERE t.pmc_id = %(pmc)s AND tr.event_at >= %(start)s AND tr.event_at < %(end)s
ORDER BY tr.event_at, tr.created_at, tr.id
"""


@dataclass(frozen=True)
class Account:
    ledger_account_id: str
    kind: str
    bank: str
    holder: str
    default_name: str
    name: str | None  # the company's own name for it, if staff set one

    @property
    def in_quickbooks(self) -> str:
        return self.name or self.default_name


@dataclass(frozen=True)
class Entry:
    number: str  # the day and its place that day: 20260131-2
    day: date
    debit: str  # the QuickBooks account the money left
    credit: str  # and the one it reached
    amount: Decimal
    memo: str


def part(text: str) -> str:
    """A name as one part of a QuickBooks account name: no colon (it nests accounts), tab or
    line break, its spaces single."""
    return re.sub(r"\s+", " ", text.replace(":", "-")).strip() or "-"


def accounts(conn: psycopg.Connection[Any], pmc_id: Any) -> list[Account]:
    found = [
        Account(
            ledger_account_id=account_id,
            kind=kind,
            bank=bank,
            holder=holder or "",
            default_name=f"{PARENTS[kind]}:{part(holder or '')}",
            name=name,
        )
        for account_id, kind, bank, holder, name in conn.execute(
            ACCOUNTS, {"pmc": pmc_id}
        ).fetchall()
    ]
    return sorted(found, key=lambda a: (a.default_name, a.ledger_account_id))


def journal(conn: psycopg.Connection[Any], pmc_id: Any, first: date, last: date) -> list[Entry]:
    """The ledger's transfers from the start of `first` to the end of `last` (UTC)."""
    if last < first:
        raise exports.ExportError("a journal runs from a day to the same day or a later one")
    names = {a.ledger_account_id: a.in_quickbooks for a in accounts(conn, pmc_id)}
    entries: list[Entry] = []
    seen: dict[date, int] = {}
    for _, at, source, target, amount, memo in conn.execute(
        TRANSFERS,
        {
            "pmc": pmc_id,
            "start": datetime.combine(first, time(), UTC),
            "end": datetime.combine(last + timedelta(days=1), time(), UTC),
        },
    ).fetchall():
        day = at.astimezone(UTC).date()
        seen[day] = seen.get(day, 0) + 1
        entries.append(
            Entry(
                number=f"{day:%Y%m%d}-{seen[day]}",
                day=day,
                debit=names[source],
                credit=names[target],
                amount=amount,
                memo=re.sub(r"\s+", " ", memo or "").strip(),
            )
        )
    return entries


def _iif_cell(text: str) -> str:
    # IIF is tab-separated: a tab, line break or quote in a cell would break its line.
    return re.sub(r'[\t\r\n"]+', " ", text)


def iif(entries: list[Entry]) -> bytes:
    """QuickBooks Desktop: File, Utilities, Import, IIF Files. Each entry a TRNS line debiting
    (a positive amount) and an SPL line crediting (the same, negative), so it sums to zero."""
    lines = [
        "!TRNS\tTRNSID\tTRNSTYPE\tDATE\tACCNT\tAMOUNT\tDOCNUM\tMEMO",
        "!SPL\tSPLID\tTRNSTYPE\tDATE\tACCNT\tAMOUNT\tDOCNUM\tMEMO",
        "!ENDTRNS",
    ]
    for e in entries:
        day = f"{e.day:%m/%d/%Y}"
        for line, account, amount in (("TRNS", e.debit, e.amount), ("SPL", e.credit, -e.amount)):
            cells = (line, "", "GENERAL JOURNAL", day, account, f"{amount:.2f}", e.number, e.memo)
            lines.append("\t".join(_iif_cell(cell) for cell in cells))
        lines.append("ENDTRNS")
    # QuickBooks Desktop reads an IIF file as Windows text.
    return ("\r\n".join(lines) + "\r\n").encode("cp1252", errors="replace")


ONLINE_COLUMNS = (
    "Journal No.",
    "Journal Date",
    "Account Name",
    "Debits",
    "Credits",
    "Description",
    "Name",
)


def online_csv(entries: list[Entry]) -> bytes:
    """QuickBooks Online: Settings, Import data, Journal entries. Two lines an entry under one
    journal number: the debit, then the credit."""
    rows: list[tuple[Any, ...]] = []
    for e in entries:
        day = f"{e.day:%m/%d/%Y}"
        rows.append((e.number, day, e.debit, f"{e.amount:.2f}", "", e.memo, ""))
        rows.append((e.number, day, e.credit, "", f"{e.amount:.2f}", e.memo, ""))
    return exports.to_csv(exports.Table("journal", ONLINE_COLUMNS, tuple(rows)))
