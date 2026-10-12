"""Bank statements as banks export them: CSV or OFX, read into lines the PMS can import.

A CSV statement has a header row. It needs a date column and either an amount column (money
in positive) or a pair of columns for money out and money in; a description and a reference
column are read when there is one. Dates are YYYY-MM-DD or MM/DD/YYYY; amounts may carry a
dollar sign, thousands commas, a leading minus or parentheses for money out.

An OFX statement (1.x SGML or 2.x XML) gives its own days (DTSTART, DTEND), its closing balance
(LEDGERBAL) and each transaction (STMTTRN: DTPOSTED, TRNAMT, FITID, NAME, MEMO, CHECKNUM). It is
read with plain text matching, never an XML parser, so nothing in the file is fetched or
expanded.
"""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

MAX_LINES = 20000
CENT = Decimal("0.01")


class StatementError(ValueError):
    """The file can't be read as a statement; the message says where and why."""


@dataclass(frozen=True)
class Line:
    posted_on: date
    amount: Decimal  # money in is positive
    description: str
    reference: str | None


@dataclass(frozen=True)
class Statement:
    lines: list[Line]
    first_day: date | None = None  # what the file says, if it says
    last_day: date | None = None
    closing_balance: Decimal | None = None


DATE_HEADERS = ("date", "posted", "posted on", "posting date", "transaction date", "post date")
AMOUNT_HEADERS = ("amount", "transaction amount")
OUT_HEADERS = ("debit", "debits", "withdrawal", "withdrawals", "money out", "paid out")
IN_HEADERS = ("credit", "credits", "deposit", "deposits", "money in", "paid in")
DESCRIPTION_HEADERS = ("description", "memo", "payee", "details", "name", "narrative")
REFERENCE_HEADERS = (
    "reference",
    "ref",
    "check number",
    "check",
    "check no",
    "transaction id",
    "id",
)


def read_amount(text: str, where: str) -> Decimal:
    cleaned = text.strip().replace("$", "").replace(",", "").replace(" ", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise StatementError(f"{where}: {text.strip()!r} is not an amount") from None
    if not amount.is_finite() or amount != amount.quantize(CENT) or abs(amount) >= 10**9:
        raise StatementError(f"{where}: {text.strip()!r} is not an amount in cents")
    return -amount if negative else amount


def read_date(text: str, where: str) -> date:
    text = text.strip()
    for form in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, form).date()
        except ValueError:
            continue
    raise StatementError(f"{where}: {text!r} is not a date (YYYY-MM-DD or MM/DD/YYYY)")


def column(headers: list[str], names: tuple[str, ...]) -> int | None:
    for i, header in enumerate(headers):
        if header in names:
            return i
    return None


def read_csv(content: str) -> Statement:
    rows = list(csv.reader(io.StringIO(content.lstrip("\ufeff"))))
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        raise StatementError("the file is empty")
    headers = [cell.strip().lower() for cell in rows[0]]
    when = column(headers, DATE_HEADERS)
    amount = column(headers, AMOUNT_HEADERS)
    out, into = column(headers, OUT_HEADERS), column(headers, IN_HEADERS)
    if when is None:
        raise StatementError("line 1: no date column (Date, Posted, Transaction date, ...)")
    if amount is None and (out is None or into is None):
        raise StatementError(
            "line 1: no amount column (Amount), or columns for money out and in (Debit, Credit)"
        )
    described = column(headers, DESCRIPTION_HEADERS)
    referenced = column(headers, REFERENCE_HEADERS)
    if len(rows) - 1 > MAX_LINES:
        raise StatementError(f"a statement has at most {MAX_LINES} lines")
    lines = []
    for n, row in enumerate(rows[1:], start=2):
        where = f"line {n}"

        def cell(i: int | None, row: list[str] = row) -> str:
            return row[i].strip() if i is not None and i < len(row) else ""

        if amount is not None:
            value = read_amount(cell(amount), where) if cell(amount) else Decimal(0)
        else:
            paid_out = read_amount(cell(out), where) if cell(out) else Decimal(0)
            paid_in = read_amount(cell(into), where) if cell(into) else Decimal(0)
            value = paid_in - abs(paid_out)
        if value == 0:
            continue  # a bank's note with no money in it
        lines.append(
            Line(
                read_date(cell(when), where),
                value,
                cell(described)[:500],
                cell(referenced)[:255] or None,
            )
        )
    return Statement(lines)


def ofx_value(block: str, tag: str) -> str | None:
    # SGML OFX leaves a value's tag open; XML closes it. Either way the value runs to the next
    # tag or the end of the line.
    found = re.search(rf"<{tag}>([^<\r\n]*)", block, re.IGNORECASE)
    return found.group(1).strip() if found and found.group(1).strip() else None


def ofx_date(text: str | None, where: str) -> date | None:
    if text is None:
        return None
    try:
        return datetime.strptime(text[:8], "%Y%m%d").date()
    except ValueError:
        raise StatementError(f"{where}: {text!r} is not an OFX date") from None


def tag(name: str) -> re.Pattern[str]:
    return re.compile(re.escape(name), re.IGNORECASE)


def ofx_blocks(content: str, opening: str, closing: str) -> list[str]:
    """The text between each opening tag and the next closing one, in order. Each search starts
    where the last one stopped, so a file of tags that never close takes linear time (a lazy
    regular expression would take quadratic time over it)."""
    opened, closed = tag(opening), tag(closing)
    blocks, at = [], 0
    while (start := opened.search(content, at)) and (end := closed.search(content, start.end())):
        blocks.append(content[start.end() : end.start()])
        at = end.end()
    return blocks


def ledger_balance(content: str) -> str | None:
    """BALAMT of the LEDGERBAL aggregate, which SGML may leave unclosed before AVAILBAL."""
    start = tag("<LEDGERBAL>").search(content)
    if start is None:
        return None
    ends = [
        found.start()
        for t in ("</LEDGERBAL>", "<AVAILBAL>")
        if (found := tag(t).search(content, start.end()))
    ]
    return ofx_value(content[start.end() : min(ends, default=len(content))], "BALAMT")


def read_ofx(content: str) -> Statement:
    if not tag("<OFX>").search(content):
        raise StatementError("no <OFX> in the file: is it an OFX or QFX statement?")
    blocks = ofx_blocks(content, "<STMTTRN>", "</STMTTRN>")
    if len(blocks) > MAX_LINES:
        raise StatementError(f"a statement has at most {MAX_LINES} lines")
    lines = []
    for n, block in enumerate(blocks, start=1):
        where = f"transaction {n}"
        posted = ofx_date(ofx_value(block, "DTPOSTED"), where)
        amount = ofx_value(block, "TRNAMT")
        if posted is None or amount is None:
            raise StatementError(f"{where}: no DTPOSTED or TRNAMT")
        value = read_amount(amount, where)
        if value == 0:
            continue
        name, memo = ofx_value(block, "NAME"), ofx_value(block, "MEMO")
        description = " ".join(part for part in (name, memo) if part)
        reference = ofx_value(block, "FITID") or ofx_value(block, "CHECKNUM")
        lines.append(Line(posted, value, description[:500], reference[:255] if reference else None))
    balance = ledger_balance(content)
    return Statement(
        lines,
        first_day=ofx_date(ofx_value(content, "DTSTART"), "DTSTART"),
        last_day=ofx_date(ofx_value(content, "DTEND"), "DTEND"),
        closing_balance=read_amount(balance, "LEDGERBAL") if balance is not None else None,
    )


def read(content: str, kind: str) -> Statement:
    """A statement file's lines (and, for OFX, its days and closing balance)."""
    return read_ofx(content) if kind == "ofx" else read_csv(content)
