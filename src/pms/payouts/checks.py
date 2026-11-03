"""Checks printed on the company's pre-printed stock (the founder's answer of 2026-10-24, ADR 0010):
the stock carries the bank's details, the account and the MICR line, so the PMS prints only what
changes from check to check. A US letter page per check, the check on top and two stubs below
(one for the payee, one for the company's file): the date, the payee, the amount in figures and
in words, the payee's mailing address where a window envelope shows it, the memo, and on each
stub the check's number and what it pays.

Where stock puts each box differs; these places follow the common check-on-top layout and are
NOT VERIFIED against a real sheet of stock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from fpdf import FPDF

from pms import pdf_fonts

ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
TENS = ["_", "_", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
GROUPS = ("", " thousand", " million")


def _under_thousand(n: int) -> str:
    words = []
    if n >= 100:
        words.append(f"{ONES[n // 100]} hundred")
        n %= 100
    if n >= 20:
        words.append(TENS[n // 10] + (f"-{ONES[n % 10]}" if n % 10 else ""))
    elif n or not words:
        words.append(ONES[n])
    return " ".join(words)


def in_words(amount: Decimal) -> str:
    """'Two hundred forty and 00/100 dollars': a check's amount as its words line writes it."""
    if amount <= 0 or amount != amount.quantize(Decimal("0.01")) or amount >= 10**9:
        raise ValueError(f"a check is for a positive amount in cents below a billion, not {amount}")
    dollars, cents = divmod(int(amount * 100), 100)
    parts: list[str] = []
    group = 0
    while dollars:
        dollars, rest = divmod(dollars, 1000)
        if rest:
            parts.insert(0, _under_thousand(rest) + GROUPS[group])
        group += 1
    words = " ".join(parts) or "zero"
    return f"{words[0].upper()}{words[1:]} and {cents:02d}/100 dollars"


@dataclass(frozen=True)
class Check:
    number: str
    on: date
    payee: str
    amount: Decimal
    memo: str
    # The payee's mailing address, a line each, for a window envelope; none if not on file.
    address: list[str] = field(default_factory=list)
    # What it pays, a line each, on the stubs: "Bill PLB-1001, $240.00", "Owner draw, Elm Court".
    lines: list[str] = field(default_factory=list)


def _money(amount: Decimal) -> str:
    return f"${amount:,.2f}"


def _stub(pdf: FPDF, top: float, company: str, check: Check) -> None:
    pdf.set_xy(15, top)
    pdf.set_font(pdf_fonts.FAMILY, style="B", size=10)
    pdf.cell(120, 6, pdf_fonts.printable(company))
    pdf.cell(0, 6, pdf_fonts.printable(f"Check {check.number}"), align="R")
    pdf.set_font(pdf_fonts.FAMILY, size=9)
    pdf.set_xy(15, top + 8)
    pdf.cell(120, 5, pdf_fonts.printable(f"Paid to {check.payee}"))
    pdf.cell(0, 5, check.on.strftime("%m/%d/%Y"), align="R")
    pdf.set_xy(15, top + 14)
    for line in check.lines[:12]:
        pdf.set_x(15)
        pdf.cell(0, 5, pdf_fonts.printable(line), new_x="LMARGIN", new_y="NEXT")
    pdf.set_xy(15, top + 76)
    pdf.set_font(pdf_fonts.FAMILY, style="B", size=10)
    pdf.cell(0, 6, pdf_fonts.printable(f"Total {_money(check.amount)}"), align="R")


def write(company: str, checks: list[Check]) -> bytes:
    """One page per check, in the order given."""
    if not checks:
        raise ValueError("no checks to print")
    pdf = pdf_fonts.Pdf(unit="mm", format="Letter")
    pdf.set_auto_page_break(False)
    pdf.set_title(pdf_fonts.printable(f"{company} checks"))
    for check in checks:
        pdf.add_page()
        # The check: the top third (93 mm) of the sheet.
        pdf.set_font(pdf_fonts.FAMILY, size=10)
        pdf.set_xy(160, 18)
        pdf.cell(40, 6, check.on.strftime("%m/%d/%Y"), align="R")
        pdf.set_xy(32, 33)
        pdf.cell(130, 6, pdf_fonts.printable(check.payee))
        pdf.set_xy(170, 33)
        pdf.cell(30, 6, f"**{check.amount:,.2f}", align="R")
        pdf.set_xy(15, 43)
        pdf.cell(185, 6, pdf_fonts.printable(in_words(check.amount)).ljust(90, "*"))
        pdf.set_xy(25, 53)
        for line in [check.payee, *check.address][:5]:
            pdf.set_x(25)
            pdf.cell(100, 5, pdf_fonts.printable(line), new_x="LMARGIN", new_y="NEXT")
        pdf.set_xy(15, 78)
        pdf.cell(100, 6, pdf_fonts.printable(f"Memo: {check.memo}"))
        # The stubs: the middle and bottom thirds.
        _stub(pdf, 98, company, check)
        _stub(pdf, 191, company, check)
    return bytes(pdf.output())
