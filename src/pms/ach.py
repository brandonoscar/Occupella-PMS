"""An ACH file (NACHA's fixed-width records) of credits: the payouts staff recorded as sent by
EFT, which the trust bank account's bank then sends (migration 20261022000156; the founder,
2026-10-23). It is written from rows that never change, so the same file comes out each time it
is downloaded.

A file is a header (1), a batch (5, its entries 6, its control 8) for each kind of entry, owners'
draws as PPD and vendors' bills as CCD, then the file's control (9), padded with lines of 9s to a
multiple of ten records. Every record is 94 characters; lines end with CR LF. Credits only, with
no offsetting debit (an unbalanced file, as most banks take from a business): the bank takes the
total from the trust bank account. Text is upper case, in the characters NACHA allows.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

RECORD = 94
BLOCK = 10
CREDITS = "220"  # the service class of a batch of credits only
# What each kind of batch carries, ten characters, which payees' banks may show.
DESCRIPTION = {"PPD": "OWNER DRAW", "CCD": "VENDOR PAY"}
# The transaction code of a credit to a checking or a savings account.
CREDIT_TO = {"checking": "22", "savings": "32"}
NOT_ALLOWED = re.compile(r"[^A-Z0-9 .,&'/()#-]")

Sec = Literal["PPD", "CCD"]


@dataclass(frozen=True)
class Originator:
    """The trust bank account's ACH settings, as its bank gave them."""

    bank_routing: str  # nine digits: the immediate destination and the trace numbers' first eight
    bank_name: str  # 23 characters at most
    origin: str  # the immediate origin, nine or ten characters
    company_name: str  # 16 characters at most
    company_id: str  # 10 characters at most


@dataclass(frozen=True)
class Entry:
    """One payout: into whose account, how much, and what it is (15 characters shown)."""

    sec: Sec
    routing: str
    account_type: Literal["checking", "savings"]
    account_number: str
    amount: Decimal
    identification: str
    name: str


def text(value: str, size: int) -> str:
    """Left-justified, upper case, in NACHA's characters (others become a space), cut to size."""
    plain = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().upper()
    return NOT_ALLOWED.sub(" ", plain)[:size].ljust(size)


def number(value: int, size: int) -> str:
    """Right-justified, zero-filled; a value too big for its field is refused, never cut."""
    shown = str(value)
    if value < 0 or len(shown) > size:
        raise ValueError(f"{value} doesn't fit in {size} digits")
    return shown.rjust(size, "0")


def cents(amount: Decimal) -> int:
    whole = amount * 100
    if whole != whole.to_integral_value() or amount <= 0:
        raise ValueError(f"{amount} is not a whole number of cents above 0")
    return int(whole)


def entry_hash(routings: list[str]) -> int:
    """The sum of the payees' banks' first eight digits, its last ten digits kept."""
    return sum(int(r[:8]) for r in routings) % 10**10


def build(
    originator: Originator,
    created_at: datetime,
    modifier: str,
    effective_on: date,
    entries: list[Entry],
) -> str:
    """The file, its entries in the order given within each batch (PPD first, then CCD)."""
    if not entries:
        raise ValueError("a file carries at least one entry")
    odfi = originator.bank_routing[:8]
    origin = originator.origin.rjust(10)
    lines = [
        "1"
        + "01"
        + " "
        + originator.bank_routing
        + text(origin, 10)
        + created_at.strftime("%y%m%d%H%M")
        + modifier
        + "094"
        + number(BLOCK, 2)
        + "1"
        + text(originator.bank_name, 23)
        + text(originator.company_name, 23)
        + " " * 8
    ]
    trace = 0
    batches = 0
    total_hash = 0
    total = 0
    for sec in ("PPD", "CCD"):
        batch = [e for e in entries if e.sec == sec]
        if not batch:
            continue
        batches += 1
        lines.append(
            "5"
            + CREDITS
            + text(originator.company_name, 16)
            + " " * 20
            + text(originator.company_id, 10)
            + sec
            + text(DESCRIPTION[sec], 10)
            + " " * 6
            + effective_on.strftime("%y%m%d")
            + " " * 3
            + "1"
            + odfi
            + number(batches, 7)
        )
        credit = 0
        for e in batch:
            trace += 1
            amount = cents(e.amount)
            credit += amount
            lines.append(
                "6"
                + CREDIT_TO[e.account_type]
                + e.routing
                + text(e.account_number, 17)
                + number(amount, 10)
                + text(e.identification, 15)
                + text(e.name, 22)
                + "  "
                + "0"
                + odfi
                + number(trace, 7)
            )
        hashed = entry_hash([e.routing for e in batch])
        total_hash += sum(int(e.routing[:8]) for e in batch)
        total += credit
        lines.append(
            "8"
            + CREDITS
            + number(len(batch), 6)
            + number(hashed, 10)
            + number(0, 12)
            + number(credit, 12)
            + text(originator.company_id, 10)
            + " " * 19
            + " " * 6
            + odfi
            + number(batches, 7)
        )
    records = len(lines) + 1
    blocks = -(-records // BLOCK)
    lines.append(
        "9"
        + number(batches, 6)
        + number(blocks, 6)
        + number(len(entries), 8)
        + number(total_hash % 10**10, 10)
        + number(0, 12)
        + number(total, 12)
        + " " * 39
    )
    lines.extend(["9" * RECORD] * (blocks * BLOCK - records))
    for line in lines:
        assert len(line) == RECORD, (len(line), line)
    return "".join(line + "\r\n" for line in lines)
