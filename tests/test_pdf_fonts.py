"""Every PDF prints people's names as they wrote them (src/pms/pdf_fonts.py, 11/03): the font is
IBM Plex Sans, embedded, so accented and other Latin letters, Greek, Cyrillic and typed
punctuation print; what it has no letter for prints "?", never an empty box."""

import hashlib
import io
from datetime import UTC, date, datetime
from decimal import Decimal

from pypdf import PdfReader

from pms import deposit_statement as ds
from pms import exports, notice_letters, pdf_fonts
from pms.leasing import certificate
from pms.payouts import checks

GENERATED = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
# Synthetic names in the letters a property manager's tenants, owners and vendors write with.
NAMES = (
    "Nguyễn Văn An",
    "Łukasz Dvořák",
    "Zoë O’Brien",
    "Дмитрий Иванов",
    "Σοφία Παππά",
    "José Ñúñez",
)
COMPANY = "Café “Synthetic” Homes – Sample €"


def text_of(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def test_the_fonts_are_the_recorded_files_under_their_license():
    # As recorded in THIRD_PARTY_NOTICES.md.
    recorded = {
        "IBMPlexSans-Regular.ttf": "8685116dfbcfa639621631c4af0ad62e"
        "52ddeb9979acbec0007ccef2e4801a27",
        "IBMPlexSans-Bold.ttf": "cf74841e461235ac7205369329b14fd13a126af4c93927f8b287cad9aa6ffaab",
        "OFL.txt": "7e6b2818edbd8f6a01ae80641cc8f16a51080d08fb4e532be3a0b6f74adb07da",
    }
    for name, sha in recorded.items():
        assert hashlib.sha256((pdf_fonts.FONTS / name).read_bytes()).hexdigest() == sha, name
    assert "SIL Open Font License, Version 1.1" in (pdf_fonts.FONTS / "OFL.txt").read_text()


def test_a_letter_the_font_has_prints_and_one_it_lacks_reads_a_question_mark():
    for name in (*NAMES, COMPANY):
        assert pdf_fonts.printable(name) == name
    # Arabic and Chinese aren't in Plex: each letter reads "?", the rest as written.
    assert pdf_fonts.printable("王 Wei مرحبا") == "? Wei ?????"
    # Line breaks and tabs pass through for the layout.
    assert pdf_fonts.printable("a\nb\tc") == "a\nb\tc"


def test_a_report_prints_its_names_as_written():
    table = exports.Table(
        title=f"Tenant directory, {COMPANY}",
        columns=("Tenant", "Rent"),
        rows=tuple((name, Decimal("1200.00")) for name in NAMES),
    )

    text = text_of(exports.to_pdf(table, GENERATED))

    assert COMPANY in text
    for name in NAMES:
        assert name in text, name
    assert "?" not in text


def test_a_notice_prints_its_names_as_written():
    letter = notice_letters.Letter(
        title=f"Notice to {NAMES[0]}",
        letterhead=f"{COMPANY}\n100 Example Way, Sampletown, CA 90000",
        dated_on=date(2026, 11, 3),
        body=f"Dear {NAMES[1]} and {NAMES[2]}, this is a notice about Unit 1.",
    )

    text = text_of(notice_letters.render(letter, GENERATED))

    for words in (COMPANY, f"Notice to {NAMES[0]}", f"Dear {NAMES[1]} and {NAMES[2]},"):
        assert words in text, words


def test_a_deposit_statement_prints_its_names_as_written():
    statement = ds.Statement(
        company=COMPANY,
        street="100 Example Way, Sampletown, CA 90000",
        dated_on=date(2026, 11, 3),
        to_name=NAMES[3],
        to_address=("200 Sample Road", "Testville, OR 97000"),
        lines=(
            ("PMC", COMPANY, None),
            ("property and unit", "Birch Flats / Unit A", None),
            ("deposit held", NAMES[3], Decimal("1200.00")),
            ("returned to", NAMES[3], Decimal("1200.00")),
        ),
        kept=(),
    )

    text = text_of(ds.render(statement, GENERATED))

    for words in (COMPANY, NAMES[3], f"Questions about this statement: write to {COMPANY}"):
        assert words in text, words


def test_a_signing_certificate_prints_its_names_as_written():
    at = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)
    step = certificate.Step(at=at, ip="192.0.2.10", user_agent="Synthetic browser")
    signer = certificate.Signer(
        name=NAMES[4],
        email="sofia@example.com",
        role="tenant",
        first_link_at=at,
        consented=step,
        opened=step,
        paper=None,
        signed=step,
        typed_name=NAMES[4],
        drawing=None,
        disclosure="I agree to sign electronically.",
    )
    made = certificate.Certificate(
        company=COMPANY,
        document_title=f"Lease for {NAMES[4]}",
        home="Unit 1, Aspen Row",
        terms="11/01/2026 – 10/31/2027, $1,200.00 a month",
        document_sha256="0" * 64,
        sent_at=at,
        sent_by=NAMES[5],
        status="signed",
        signers=(signer,),
    )

    text = text_of(certificate.render(made, GENERATED))

    for words in (COMPANY, f"Lease for {NAMES[4]}", NAMES[5], "11/01/2026 – 10/31/2027"):
        assert words in text, words


def test_a_check_prints_its_names_as_written_and_its_amount_in_words_fits_its_line():
    check = checks.Check(
        number="1043",
        on=date(2026, 11, 3),
        payee=NAMES[0],
        amount=Decimal("1200.00"),
        memo=f"Owner draw, {NAMES[1]}",
        address=["200 Sample Road", "Testville, OR 97000"],
        lines=[f"Owner draw, {COMPANY}"],
    )

    text = text_of(checks.write(COMPANY, [check]))

    for words in (COMPANY, NAMES[0], f"Memo: Owner draw, {NAMES[1]}", f"Paid to {NAMES[0]}"):
        assert words in text, words
    # The longest amount in words, padded with stars as printed, fits the check's 185 mm line.
    pdf = pdf_fonts.Pdf(unit="mm", format="Letter")
    pdf.set_font(pdf_fonts.FAMILY, size=10)
    longest = checks.in_words(Decimal("777777.77")).ljust(90, "*")
    assert pdf.get_string_width(longest) <= 185
