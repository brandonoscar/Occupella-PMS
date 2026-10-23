"""Checks printed on a company's pre-printed voucher check stock (migration 20261022000157; the
founder, 2026-10-23): one US letter page a check, the check in the top, middle or bottom third and
a stub in each of the other two. The stock carries the bank's details, the company's and the MICR
line; the PMS prints the date, the payee and the address a window envelope shows, the amount in
figures and in words, the memo, and on the stubs the check's number and what it pays. Nobody's
signature is printed: someone the trust bank account allows signs it by hand (docs/law.md).

Where each part sits is the usual place on voucher stock, which staff move for their printer
(shift_right and shift_down, in points). It is written from rows that never change, so a run comes
out the same each time it is downloaded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from fpdf import FPDF

PAGE_WIDTH = 612  # US letter, in points
THIRD = 264  # each part of a voucher page: 11 inches / 3
POSITIONS = ("top", "middle", "bottom")

ONES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen",
)  # fmt: skip
TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
GROUPS = ((10**9, "billion"), (10**6, "million"), (1000, "thousand"))


def _below_thousand(n: int) -> list[str]:
    words: list[str] = []
    if n >= 100:
        words += [ONES[n // 100], "hundred"]
        n %= 100
    if n >= 20:
        words.append(TENS[n // 10] + ("-" + ONES[n % 10] if n % 10 else ""))
    elif n > 0:
        words.append(ONES[n])
    return words


def dollars_in_words(n: int) -> str:
    """Whole dollars in words, as a check writes them: 1125 is "one thousand one hundred
    twenty-five"."""
    if n < 0 or n >= 10**12:
        raise ValueError(f"{n} is not a check's dollars")
    if n == 0:
        return "zero"
    words: list[str] = []
    for size, name in GROUPS:
        if n >= size:
            words += [*_below_thousand(n // size), name]
            n %= size
    words += _below_thousand(n)
    return " ".join(words)


def amount_in_words(amount: Decimal) -> str:
    """The legal amount: "One thousand one hundred twenty-five and 50/100"."""
    cents = int(amount * 100)
    if amount <= 0 or cents != amount * 100:
        raise ValueError(f"{amount} is not a whole number of cents above 0")
    text = f"{dollars_in_words(cents // 100)} and {cents % 100:02d}/100"
    return text[0].upper() + text[1:]


def figures(amount: Decimal) -> str:
    """The amount in the box, guarded with asterisks: "**1,125.50"."""
    return f"**{amount:,.2f}"


@dataclass(frozen=True)
class Check:
    number: int
    dated_on: date
    payee: str
    # The payee's mailing address, for a window envelope (none when none is on file).
    address: tuple[str, ...]
    amount: Decimal
    memo: str
    # What it pays, as the stubs list it.
    stub: tuple[tuple[str, Decimal], ...] = field(default=())


@dataclass(frozen=True)
class Layout:
    position: str = "top"
    shift_right: int = 0
    shift_down: int = 0


def _latin1(value: str) -> str:
    """The PDF's built-in font has Latin-1 only; anything else prints as "?"."""
    return value.encode("latin-1", "replace").decode("latin-1")


def _us(day: date) -> str:
    return f"{day:%m/%d/%Y}"


def render(checks: list[Check], layout: Layout, company: str) -> bytes:
    """A page for each check, in the order given (their numbers' order on the stock)."""
    if not checks:
        raise ValueError("a run prints at least one check")
    pdf = FPDF(unit="pt", format="letter")
    pdf.set_auto_page_break(False)
    pdf.set_creator("Occupella PMS")
    pdf.set_title(_latin1(f"Checks {checks[0].number} to {checks[-1].number}"))
    at = POSITIONS.index(layout.position)
    for check in checks:
        pdf.add_page()
        stubs = [n for n in range(3) if n != at]
        _check(pdf, check, layout.shift_right, at * THIRD + layout.shift_down)
        for n in stubs:
            _stub(pdf, check, company, layout.shift_right, n * THIRD + layout.shift_down)
    return bytes(pdf.output())


def _text(pdf: FPDF, x: float, y: float, value: str, size: float = 10, style: str = "") -> None:
    pdf.set_font("Helvetica", style=style, size=size)
    pdf.text(x, y, _latin1(value))


def _check(pdf: FPDF, check: Check, dx: float, top: float) -> None:
    _text(pdf, 468 + dx, top + 54, _us(check.dated_on))
    _text(pdf, 72 + dx, top + 92, check.payee, style="B")
    _text(pdf, 504 + dx, top + 92, figures(check.amount), style="B")
    words = amount_in_words(check.amount)
    _text(pdf, 36 + dx, top + 117, words + " " + "*" * max(3, 88 - len(words)), size=9.5)
    for n, line in enumerate((check.payee, *check.address)):
        _text(pdf, 72 + dx, top + 150 + 12 * n, line, size=9.5)
    _text(pdf, 36 + dx, top + 210, f"Memo: {check.memo}", size=9)


def _stub(pdf: FPDF, check: Check, company: str, dx: float, top: float) -> None:
    _text(pdf, 36 + dx, top + 36, company, style="B")
    _text(pdf, 36 + dx, top + 54, check.payee)
    _text(pdf, 360 + dx, top + 54, f"Check {check.number}")
    _text(pdf, 468 + dx, top + 54, _us(check.dated_on))
    y = top + 80
    for what, amount in check.stub[:9]:
        _text(pdf, 36 + dx, y, what, size=9)
        _text(pdf, 504 + dx, y, f"{amount:,.2f}", size=9)
        y += 14
    if len(check.stub) > 9:
        _text(pdf, 36 + dx, y, f"and {len(check.stub) - 9} more", size=9)
    _text(pdf, 36 + dx, top + 228, check.memo, size=9)
    _text(pdf, 504 + dx, top + 228, f"{check.amount:,.2f}", size=9, style="B")
