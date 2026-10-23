"""A company's notice templates filled in (migration 0119; the founder's review q095): the blanks a
template may use, such as {{tenant_names}}, what each is, the template's text with them filled,
and the notice as a one-letter PDF. The words are the company's; the PMS writes no notice text and
checks no state's notice period or wording. Since migration 0121 a template may be a company's own
lease instead, with blanks a draft lease's terms fill (LEASE_FIELDS), made into the PDF it sends
for signing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from fpdf import FPDF

# The blanks a template may use, and what each is filled with.
FIELDS: dict[str, str] = {
    "tenant_names": 'the lease\'s tenants, as "Morgan Demo and Jamie Sample"',
    "property": "the property's name",
    "unit": "the unit's name",
    "monthly_rent": "the rent in force on the notice's day, as $1,395.00",
    "balance_due": "what the lease owes now, as $1,550.00",
    "open_charges": "what is open of each charge now, a line each with its due day, and the total",
    "lease_start": "the lease's first day, as 01/01/2026",
    "lease_end": 'the lease\'s last day, or "month to month"',
    "notice_date": "the day the notice is dated, as 10/21/2026",
    "company_name": "the company's name",
    "company_address": "the company's mailing address, on one line",
    "staff_name": "who made the notice",
}
# The blanks a lease template may use (migration 0121), filled from a draft lease's terms.
LEASE_FIELDS: dict[str, str] = {
    "tenant_names": 'who signs as a tenant, as "Morgan Demo and Jamie Sample"',
    "guarantor_names": "who signs as a guarantor, or none",
    "property": "the property's name",
    "unit": "the unit's name",
    "monthly_rent": "the rent a month, as $1,395.00",
    "security_deposit": "the security deposit asked, as $1,395.00, or none",
    "monthly_charges": 'each monthly fee or credit besides rent, as "Pet rent, $25.00 a month"',
    "lease_start": "the lease's first day, as 01/01/2026",
    "lease_end": 'the lease\'s last day, or "month to month"',
    "lease_date": "the day the lease is made, as 10/21/2026",
    "company_name": "the company's name",
    "company_address": "the company's mailing address, on one line",
}
# No part of a blank can match two ways (a name's spaces are stripped after), so a long run of
# spaces in a template takes linear time, never polynomial (CodeQL py/polynomial-redos).
BLANK = re.compile(r"\{\{([^{}]*)\}\}")


def unknown(text: str, fields: dict[str, str] = FIELDS) -> list[str]:
    """The blanks in a text that are not a blank the PMS fills, each once, as written."""
    seen: list[str] = []
    for name in (found.strip() for found in BLANK.findall(text)):
        if name not in fields and name not in seen:
            seen.append(name)
    return seen


def fill(text: str, values: dict[str, str]) -> str:
    """The text with each blank replaced by its value. Every blank must be one the values name."""
    return BLANK.sub(lambda found: values[found.group(1).strip()], text)


def us(day: date) -> str:
    return f"{day:%m/%d/%Y}"


def names(people: list[str]) -> str:
    """'A', 'A and B', 'A, B and C'."""
    if len(people) <= 2:
        return " and ".join(people)
    return f"{', '.join(people[:-1])} and {people[-1]}"


@dataclass(frozen=True)
class Letter:
    title: str
    letterhead: str
    dated_on: date
    body: str


def _latin1(value: str) -> str:
    """The PDF's built-in font has Latin-1 only; anything else prints as "?"."""
    return value.encode("latin-1", "replace").decode("latin-1")


class _Pdf(FPDF):
    def footer(self) -> None:
        self.set_y(-30)
        self.set_font("Helvetica", size=7)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def render(letter: Letter, generated: datetime) -> bytes:
    """The notice as a letter: the company's letterhead, the day, the title, then its text."""
    return render_all([letter], generated)


def render_all(letters: list[Letter], generated: datetime) -> bytes:
    """Notices to print together, each a letter starting on a page of its own."""
    pdf = _Pdf(orientation="portrait", unit="pt", format="letter")
    pdf.set_creation_date(generated.astimezone(UTC))
    pdf.set_title(_latin1(letters[0].title))
    pdf.set_author("Occupella PMS")
    pdf.set_margins(60, 54, 60)
    pdf.set_auto_page_break(True, margin=54)
    for letter in letters:
        _letter(pdf, letter)
    return bytes(pdf.output())


def _letter(pdf: FPDF, letter: Letter) -> None:
    pdf.add_page()
    pdf.set_font("Helvetica", style="B", size=10)
    pdf.multi_cell(0, 13, _latin1(letter.letterhead), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(12)
    pdf.set_font("Helvetica", size=10)
    pdf.multi_cell(0, 13, us(letter.dated_on), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(12)
    pdf.set_font("Helvetica", style="B", size=13)
    pdf.multi_cell(0, 17, _latin1(letter.title), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    pdf.set_font("Helvetica", size=10)
    pdf.multi_cell(0, 14, _latin1(letter.body), new_x="LMARGIN", new_y="NEXT")
