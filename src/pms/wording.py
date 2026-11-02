"""A report's words as people read them (ADR 0010, rule 2; the founder's answer of 2026-10-26).

A report function writes the ledger's own words: an account named by its kind
("owner_property: Demo Owner A / Elm Court"), a trust bank account's kind in brackets, "PMC" and
"posting" as heading words, the step a bill no longer has ("Bill RF-1 set aside"), and days as
YYYY-MM-DD. Those stay as they are in the database, in the report functions and their golden
cases, and in what the portal's own pages parse (`deps.Rows`, the owner's statement). This one
word list turns them into the portal's words wherever a report is read: on Reports, on the
general ledger page, and in every CSV, Excel and PDF download (`exports.export`), its columns
under the same headings on screen and in a download.

What it never touches: the audit log, whose cells are the database's own rows (a record, not
words to read); the ledger and records zips and the QuickBooks file, which are data for another
system; and a report's numbers. A day keeps its time zone: the reports say UTC because their
days end at midnight UTC.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pms.exports import Table

# A ledger account's kind, as the account's name starts: "owner_property: ..." reads "Owner: ...".
KINDS = {
    "bank_cash": "Bank",
    "owner_property": "Owner",
    "pmc_income": "Company fees",
    "prepaid_rent": "Prepaid rent",
    "tenant_deposit": "Deposit",
    "vendor_payable": "Vendor",
}
# A whole cell, maybe after "all properties: ".
WORDS = {
    "PMC": "Company",
    "posting": "transaction",
    "set aside for vendors' bills": "held for vendors' bills",
    "bills not yet set aside": "bills held, nothing paid yet",
}

# A column whose name, its underscores read as spaces, isn't the heading people use. The PDF's
# font is Latin-1, so a range takes a hyphen.
COLUMNS = {
    "days_1_30": "1-30 days",
    "days_31_60": "31-60 days",
    "days_61_90": "61-90 days",
    "over_90": "Over 90 days",
    "set_aside": "Held for the vendor",
    "schedule_e": "Schedule E",
    "w9_on_file": "W-9 on file",
    "db_role": "Database role",
}

_KIND = re.compile(r"^(" + "|".join(KINDS) + r"): ")
_WHOLE = re.compile(r"^(all properties: )?(.*)$", re.S)
# A bill's money leaving its owner's account: the memo is "Bill RF-1 set aside" (phase 1a kept
# that write path); the bill is what it paid for.
_SET_ASIDE = re.compile(r"(^|- )(Bill .+) set aside$", re.S)
_BANK_KIND = re.compile(r" \(security_deposit\)$")
# An owner's statement runs from one midnight (UTC) to the next day's, the last not included.
_PERIOD = re.compile(r"^(\d{4}-\d{2}-\d{2}) 00:00:00 to (\d{4}-\d{2}-\d{2}) 00:00:00 UTC$")
_DAY = re.compile(r"(?<![\w-])(\d{4})-(\d{2})-(\d{2})(?![\w-])")


def us_day(day: date) -> str:
    return day.strftime("%m/%d/%Y")


def plain_text(text: str) -> str:
    """A report's cell in the portal's words, its days as MM/DD/YYYY."""
    period = _PERIOD.match(text)
    if period:
        first, after = date.fromisoformat(period[1]), date.fromisoformat(period[2])
        return f"{us_day(first)} to {us_day(after - timedelta(days=1))} UTC"
    text = _KIND.sub(lambda kind: KINDS[kind[1]] + ": ", text)
    text = _BANK_KIND.sub(" (security deposit)", text)
    text = _SET_ASIDE.sub(r"\1\2", text)
    whole = _WHOLE.match(text)
    if whole and whole[2] in WORDS:
        text = (whole[1] or "") + WORDS[whole[2]]
    return _DAY.sub(r"\2/\3/\1", text)


def plain(value: Any) -> Any:
    """A cell in plain words; numbers, days and anything not text stay as they are."""
    return plain_text(value) if isinstance(value, str) else value


def as_text(value: Any) -> Any:
    """A day or a time as text, the way the portal writes it; anything else as it is."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%m/%d/%Y %H:%M:%S UTC")
    if isinstance(value, date):
        return us_day(value)
    return value


def heading(column: str) -> str:
    """A column's name as a heading: "monthly_rent" reads "Monthly rent"."""
    if column in COLUMNS:
        return COLUMNS[column]
    words = column.replace("_", " ")
    return words[:1].upper() + words[1:]


def is_audit_log(table: Table) -> bool:
    return table.title.startswith("audit log (")


def plain_table(table: Table) -> Table:
    """The table's title and cells in plain words (the audit log's cells stay as the database
    wrote them)."""
    title = plain_text(table.title)
    if is_audit_log(table):
        return replace(table, title=title)
    rows = tuple(tuple(plain(v) for v in row) for row in table.rows)
    return replace(table, title=title, rows=rows)


def days_as_text(table: Table) -> Table:
    """The table with its days and times written out, for CSV, PDF and the screen."""
    return replace(table, rows=tuple(tuple(as_text(v) for v in row) for row in table.rows))


def headed(table: Table) -> Table:
    """The table with its columns as headings, for a download."""
    return replace(table, columns=tuple(heading(c) for c in table.columns))
