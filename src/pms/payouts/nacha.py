"""An ACH file in NACHA's format: credits from a trust bank account to owners and vendors, for the
company to upload to its own bank. The PMS writes the file; the bank moves the money.

The file is records of 94 characters: a file header (1), a batch header (5) for each batch, an
entry (6) for each payee, a batch control (8), a file control (9), then lines of nines to fill
the last block of ten. Each batch credits only (service class 220): owners' draws as PPD (a
person's account), vendors' bills as CCD (a company's). Money is in cents, right-justified with
zeros; names and ids in capitals, left-justified with spaces. An entry's trace number is the
bank's routing (its first eight digits) and the entry's place in the file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

RECORD = 94
BLOCK = 10
# NACHA asks for the alphanumeric characters and a few signs; anything else becomes a space.
ALLOWED = re.compile(r"[^A-Z0-9 .,&'/()-]")


@dataclass(frozen=True)
class Origin:
    """What the company's bank gives it to put in each file."""

    bank_routing_number: str  # the bank the file goes to (Immediate Destination)
    bank_name: str
    origin_id: str  # Immediate Origin, ten characters as the bank gives them
    origin_name: str
    company_id: str  # each batch's Company Identification, ten characters
    company_name: str


@dataclass(frozen=True)
class Entry:
    routing_number: str
    account_number: str
    savings: bool
    amount: Decimal
    name: str
    reference: str  # the Individual Identification Number: what the payee sees it is for


@dataclass(frozen=True)
class Batch:
    sec_code: str  # PPD (owners) or CCD (vendors)
    description: str  # Company Entry Description, at most ten characters: OWNER DRAW, VENDOR PAY
    effective_on: date
    entries: list[Entry]


def text(value: str, width: int) -> str:
    """Capitals, NACHA's characters only, left-justified and cut to the width."""
    return ALLOWED.sub(" ", value.upper())[:width].ljust(width)


def number(value: int, width: int) -> str:
    digits = str(value)
    if value < 0 or len(digits) > width:
        raise ValueError(f"{value} doesn't fit in {width} digits")
    return digits.rjust(width, "0")


def cents(amount: Decimal) -> int:
    if amount != amount.quantize(Decimal("0.01")) or amount <= 0:
        raise ValueError(f"an ACH credit is a positive amount in cents, not {amount}")
    return int(amount * 100)


def write(origin: Origin, batches: list[Batch], created_at: datetime, file_id: str = "A") -> str:
    """The whole file, each record on a line of its own (ending in a newline)."""
    odfi = origin.bank_routing_number[:8]
    lines = [
        "1"
        + "01"
        + " "
        + origin.bank_routing_number
        + text(origin.origin_id, 10)
        + created_at.strftime("%y%m%d%H%M")
        + text(file_id, 1)
        + "094"
        + "10"
        + "1"
        + text(origin.bank_name, 23)
        + text(origin.origin_name, 23)
        + " " * 8
    ]
    trace = 0
    total_entries = total_hash = total_credit = 0
    for n, batch in enumerate(batches, start=1):
        if not batch.entries:
            raise ValueError("a batch credits someone")
        lines.append(
            "5"
            + "220"
            + text(origin.company_name, 16)
            + " " * 20
            + text(origin.company_id, 10)
            + text(batch.sec_code, 3)
            + text(batch.description, 10)
            + " " * 6
            + batch.effective_on.strftime("%y%m%d")
            + " " * 3
            + "1"
            + odfi
            + number(n, 7)
        )
        entry_hash = credit = 0
        for entry in batch.entries:
            if not re.fullmatch(r"[0-9]{9}", entry.routing_number):
                raise ValueError("a routing number is nine digits")
            if not re.fullmatch(r"[0-9A-Za-z-]{1,17}", entry.account_number):
                raise ValueError("an account number is at most 17 digits")
            trace += 1
            amount = cents(entry.amount)
            lines.append(
                "6"
                + ("32" if entry.savings else "22")
                + entry.routing_number
                + entry.account_number.ljust(17)
                + number(amount, 10)
                + text(entry.reference, 15)
                + text(entry.name, 22)
                + "  "
                + "0"
                + odfi
                + number(trace, 7)
            )
            entry_hash += int(entry.routing_number[:8])
            credit += amount
        lines.append(
            "8"
            + "220"
            + number(len(batch.entries), 6)
            + number(entry_hash % 10**10, 10)
            + number(0, 12)
            + number(credit, 12)
            + text(origin.company_id, 10)
            + " " * 19
            + " " * 6
            + odfi
            + number(n, 7)
        )
        total_entries += len(batch.entries)
        total_hash += entry_hash
        total_credit += credit
    records = len(lines) + 1
    lines.append(
        "9"
        + number(len(batches), 6)
        + number(-(-records // BLOCK), 6)
        + number(total_entries, 8)
        + number(total_hash % 10**10, 10)
        + number(0, 12)
        + number(total_credit, 12)
        + " " * 39
    )
    while len(lines) % BLOCK:
        lines.append("9" * RECORD)
    for line in lines:
        assert len(line) == RECORD, line
    return "\n".join(lines) + "\n"
