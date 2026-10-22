"""A lease's security deposit statement as a letter to one tenant (migration 0120; the founder's
review q039; docs/law.md): addressed to their forwarding address, what the deposits held came to,
what was kept and for what with each fee's receipts named, what went back to whom and when, then
the receipts and the lease's move-out photos themselves, so one PDF is what a state that asks for
them wants mailed or sent. The figures are trust_report_deposit_return's (src/pms/api/moveout.py
reads them); the PMS writes no state's own wording.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from fpdf import FPDF
from fpdf.errors import FPDFException
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError

from pms.notice_letters import us


@dataclass(frozen=True)
class File:
    title: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class Kept:
    """A fee the deposits paid, and the receipts on file for it."""

    label: str
    amount: Decimal
    receipts: tuple[str, ...]


@dataclass(frozen=True)
class Statement:
    company: str
    street: str
    dated_on: date
    to_name: str
    # The forwarding address's lines; none on file, none.
    to_address: tuple[str, ...]
    # trust_report_deposit_return's lines, as (item, detail, amount).
    lines: tuple[tuple[str, str | None, Decimal | None], ...]
    kept: tuple[Kept, ...]
    # The receipts, then the move-out photos, in the order the letter names them.
    files: tuple[File, ...] = field(default_factory=tuple)
    photos: int = 0


def _latin1(value: str) -> str:
    """The PDF's built-in font has Latin-1 only; anything else prints as "?"."""
    return value.encode("latin-1", "replace").decode("latin-1")


def dollars(amount: Decimal) -> str:
    return f"${amount:,.2f}"


class _Pdf(FPDF):
    def footer(self) -> None:
        self.set_y(-30)
        self.set_font("Helvetica", size=7)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def _new(title: str, generated: datetime) -> _Pdf:
    pdf = _Pdf(orientation="portrait", unit="pt", format="letter")
    pdf.set_creation_date(generated.astimezone(UTC))
    pdf.set_title(_latin1(title))
    pdf.set_author("Occupella PMS")
    pdf.set_margins(60, 54, 60)
    pdf.set_auto_page_break(True, margin=54)
    return pdf


def _letter(statement: Statement, generated: datetime) -> bytes:
    pdf = _new("Security deposit statement", generated)
    pdf.add_page()
    pdf.set_font("Helvetica", style="B", size=10)
    head = statement.company if not statement.street else f"{statement.company}\n{statement.street}"
    pdf.multi_cell(0, 13, _latin1(head), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(12)
    pdf.set_font("Helvetica", size=10)
    pdf.multi_cell(0, 13, us(statement.dated_on), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)
    address = statement.to_address or ("No forwarding address on file",)
    pdf.multi_cell(
        0, 13, _latin1("\n".join((statement.to_name, *address))), new_x="LMARGIN", new_y="NEXT"
    )
    pdf.ln(14)
    pdf.set_font("Helvetica", style="B", size=13)
    pdf.multi_cell(0, 17, "Security deposit statement", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font("Helvetica", size=10)
    width = pdf.epw - 90
    for item, detail, amount in statement.lines:
        if item == "PMC":
            continue
        text = item[:1].upper() + item[1:] + (f": {detail}" if detail else "")
        y = pdf.get_y()
        pdf.multi_cell(width, 14, _latin1(text), new_x="RIGHT", new_y="TOP")
        bottom = pdf.get_y()
        if amount is not None:
            pdf.set_xy(pdf.l_margin + width, y)
            pdf.cell(90, 14, dollars(amount), align="R")
        pdf.set_xy(pdf.l_margin, max(bottom, y + 14))
    pdf.ln(10)
    pdf.set_font("Helvetica", style="B", size=11)
    pdf.multi_cell(0, 15, "Receipts and invoices", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    if not statement.kept:
        pdf.multi_cell(0, 14, "Nothing was kept from the deposits.", new_x="LMARGIN", new_y="NEXT")
    for kept in statement.kept:
        on_file = "; ".join(kept.receipts) if kept.receipts else "no receipt on file"
        pdf.multi_cell(
            0,
            14,
            _latin1(f"{kept.label}, {dollars(kept.amount)}: {on_file}"),
            new_x="LMARGIN",
            new_y="NEXT",
        )
    pdf.ln(6)
    attached = len(statement.files) - statement.photos
    pdf.multi_cell(
        0,
        14,
        f"Attached after this letter: {attached} receipt file(s) and {statement.photos}"
        " move-out photo(s).",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(6)
    reach = f" at {statement.street}" if statement.street else ""
    pdf.multi_cell(
        0,
        14,
        _latin1(f"Questions about this statement: write to {statement.company}{reach}."),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    return bytes(pdf.output())


def _image_page(file: File, generated: datetime) -> bytes:
    """One page holding a receipt's or photo's image, its title above it."""
    pdf = _new(file.title, generated)
    pdf.add_page()
    pdf.set_font("Helvetica", size=9)
    pdf.multi_cell(0, 12, _latin1(file.title), new_x="LMARGIN", new_y="NEXT")
    pdf.image(
        io.BytesIO(file.content), x=pdf.l_margin, w=pdf.epw, keep_aspect_ratio=True, h=pdf.eph - 30
    )
    return bytes(pdf.output())


def _unreadable_page(file: File, generated: datetime) -> bytes:
    pdf = _new(file.title, generated)
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    pdf.multi_cell(
        0,
        14,
        _latin1(f"{file.title} could not be added here: it is with the lease's files."),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    return bytes(pdf.output())


def render(statement: Statement, generated: datetime) -> bytes:
    """The letter, then each receipt and photo: a PDF's pages as they are, an image on a page of
    its own, and a PDF or an image that can't be read named on a page saying where it is."""
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(_letter(statement, generated))))
    for file in statement.files:
        if file.content_type == "application/pdf":
            try:
                writer.append(PdfReader(io.BytesIO(file.content)))
                continue
            except PyPdfError:
                part = _unreadable_page(file, generated)
        else:
            # A photo whose first bytes are an image's but whose rest is not (cut short, say)
            # passes the upload's check; the image library refuses it here.
            try:
                part = _image_page(file, generated)
            except (OSError, ValueError, FPDFException):
                part = _unreadable_page(file, generated)
        writer.append(PdfReader(io.BytesIO(part)))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
