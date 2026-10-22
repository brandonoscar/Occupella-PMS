"""A deposit statement as a letter (src/pms/deposit_statement.py; the founder's review q039): the
company's letterhead, the tenant's forwarding address or a line saying none is on file, the
report's lines, each kept fee with its receipts or "no receipt on file", then each file: a PDF's
own pages, an image on a page of its own, and a PDF that can't be read named on a page saying
where it is. Every name and address is synthetic."""

import io
from datetime import UTC, date, datetime
from decimal import Decimal

from fpdf import FPDF
from helpers import PDF, PNG, png
from pypdf import PdfReader

from pms import deposit_statement as ds

MADE = datetime(2026, 10, 22, 12, tzinfo=UTC)


def a_pdf(words):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, words)
    return bytes(pdf.output())


def statement(**change):
    base = {
        "company": "Synthetic Homes LLC",
        "street": "100 Example Way, Sampletown, CA 90000",
        "dated_on": date(2026, 10, 22),
        "to_name": "Morgan Demo",
        "to_address": ("200 Sample Road", "Testville, OR 97000"),
        "lines": (
            ("PMC", "Synthetic Homes LLC", None),
            ("property and unit", "Birch Flats / Unit A", None),
            ("deposit held", "Morgan Demo", Decimal("1200.00")),
            ("kept for", "2026-10-31 fee: Carpet cleaning", Decimal("350.00")),
            ("returned to", "Morgan Demo", Decimal("850.00")),
        ),
        "kept": (ds.Kept("Carpet cleaning (due 10/31/2026)", Decimal("350.00"), ("Invoice",)),),
    }
    return ds.Statement(**(base | change))


def pages(data):
    return [page.extract_text() for page in PdfReader(io.BytesIO(data)).pages]


def test_the_letter_names_the_company_the_tenant_and_what_was_kept():
    (letter,) = pages(ds.render(statement(), MADE))

    for words in (
        "Synthetic Homes LLC",
        "100 Example Way, Sampletown, CA 90000",
        "10/22/2026",
        "Morgan Demo",
        "200 Sample Road",
        "Testville, OR 97000",
        "Security deposit statement",
        "Property and unit: Birch Flats / Unit A",
        "$1,200.00",
        "Carpet cleaning (due 10/31/2026), $350.00: Invoice",
        "Attached after this letter: 0 receipt file(s) and 0 move-out photo(s).",
        "Questions about this statement: write to Synthetic Homes LLC at 100 Example Way",
    ):
        assert words in letter, words
    assert "PMC:" not in letter  # the letterhead names it


def test_without_an_address_or_anything_kept_the_letter_says_so():
    (letter,) = pages(
        ds.render(statement(street="", to_address=(), kept=(ds.Kept("Fee", Decimal(1), ()),)), MADE)
    )
    assert "No forwarding address on file" in letter
    assert "Fee, $1.00: no receipt on file" in letter
    assert "write to Synthetic Homes LLC." in letter

    (nothing,) = pages(ds.render(statement(kept=()), MADE))
    assert "Nothing was kept from the deposits." in nothing


def test_each_file_follows_the_letter_on_pages_of_its_own():
    files = (
        ds.File("Invoice CC-1", "application/pdf", a_pdf("Synthetic invoice CC-1")),
        ds.File("Broken PDF", "application/pdf", PDF),  # starts as a PDF does; no PDF inside
        ds.File("Living room", "image/png", png()),
        # Starts as a PNG does, so the upload took it; the rest is no image.
        ds.File("Kitchen", "image/png", PNG),
    )

    letter, invoice, broken, photo, cut = pages(ds.render(statement(files=files, photos=2), MADE))

    assert "2 receipt file(s) and 2 move-out photo(s)" in letter
    assert "Synthetic invoice CC-1" in invoice
    assert "Broken PDF could not be added here: it is with the lease's files." in broken
    assert "Living room" in photo
    assert "Kitchen could not be added here: it is with the lease's files." in cut
