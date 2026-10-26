"""Golden files for what the PMS hands people (ADR 0010 phase 4): a tenant's payment receipt, an
owner's statement as the PDF they download, the ACH file the company uploads to its bank and the
checks it prints. Each is made from a fixed synthetic fixture and its text must equal
tests/golden/files/<name>.expected.txt exactly: a PDF by the text it shows, the ACH file as it is.
Regenerate deliberately with `pytest tests/golden --update-goldens` (see tools/golden.py)."""

import io
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

from pms import exports
from pms.api import receipts, rent
from pms.payouts import checks, nacha
from tools import golden

FILES = Path(__file__).parent / "files"
CASES = Path(__file__).parent / "cases"
GENERATED = datetime(2026, 2, 1, 9, 30, tzinfo=UTC)
PMC = uuid.UUID("00000000-0000-4000-8000-000000000001")


def pdf_text(data: bytes) -> str:
    pages = PdfReader(io.BytesIO(data)).pages
    return "\n".join(page.extract_text() for page in pages).rstrip() + "\n"


def matches(request, name: str, actual: str) -> None:
    update = request.config.getoption("--update-goldens")
    diff = golden.compare(FILES / f"{name}.sql", golden.normalize(actual), update)
    assert diff is None, diff


def setup(conn, sql_file: Path) -> None:
    *statements, last = golden.statements(sql_file.read_text())
    for statement in [*statements, last]:
        conn.execute(statement.encode())


def test_a_payment_receipt(conn, request):
    with conn.transaction(force_rollback=True):
        setup(conn, FILES / "receipt.sql")
        lease = conn.execute(
            "SELECT id FROM trust_leases WHERE unit_id = '00000000-0000-4000-8000-000000000041'"
        ).fetchone()[0]
        recorded = rent.record_payment(
            conn,
            PMC,
            lease,
            uuid.UUID("00000000-0000-4000-8000-000000000901"),
            Decimal("1600.00"),
            date(2026, 1, 3),
            reference="101",
            method="check",
        )
        found = receipts.receipt_of(conn, PMC, recorded.transfers[0])
        actual = pdf_text(exports.to_pdf(found.table, GENERATED))
    matches(request, "receipt", actual)


def test_an_owners_statement_as_they_download_it(conn, request):
    case = CASES / "trust_report_owner_statement" / "january.sql"
    *statements, _ = golden.statements(case.read_text())
    with conn.transaction(force_rollback=True):
        for statement in statements:
            conn.execute(statement.encode())
        table = exports.run_report(
            conn,
            "trust_report_owner_statement",
            [
                PMC,
                uuid.UUID("00000000-0000-4000-8000-000000000021"),
                "2026-01-01 00:00:00+00",
                "2026-02-01 00:00:00+00",
            ],
        )
        actual = pdf_text(exports.to_pdf(table, GENERATED))
    matches(request, "owner_statement", actual)


def routing(prefix8: str) -> str:
    """A routing number with its check digit, so the file is one a bank would read."""
    digits = [int(c) for c in prefix8]
    for last in range(10):
        n = [*digits, last]
        if (3 * (n[0] + n[3] + n[6]) + 7 * (n[1] + n[4] + n[7]) + n[2] + n[5] + n[8]) % 10 == 0:
            return prefix8 + str(last)
    raise AssertionError(prefix8)


def entry(prefix: str, account: str, savings: bool, amount: str, name: str, what: str):
    return nacha.Entry(routing(prefix), account, savings, Decimal(amount), name, what)


def test_an_ach_file_of_owner_draws_and_a_vendor_payment(request):
    bank = routing("00123456")
    origin = nacha.Origin(
        bank, "Synthetic Bank", "1000000000", "Golden PMC", "1000000000", "Golden PMC"
    )
    day = date(2026, 2, 2)
    owners = nacha.Batch(
        "PPD",
        "OWNER DRAW",
        day,
        [
            entry("00987654", "12345678", False, "2010.00", "Owner 1", "OWNER DRAW"),
            entry("00111111", "87654321", True, "725.00", "Owner 2", "OWNER DRAW"),
        ],
    )
    vendors = nacha.Batch(
        "CCD",
        "VENDOR PAY",
        day,
        [entry("00222222", "55555555", False, "240.00", "Vendor 1", "VENDOR PAY")],
    )
    actual = nacha.write(origin, [owners, vendors], datetime(2026, 2, 1, 9, 30))
    matches(request, "ach_file", actual)


def test_printed_checks(request):
    printed = checks.write(
        "Golden PMC",
        [
            checks.Check(
                "1001",
                date(2026, 2, 2),
                "Owner 1",
                Decimal("2010.00"),
                "Owner draw, January",
                address=["1 Example Road", "Suite 2", "Sampletown, ZZ 00000"],
                lines=["Owner draw, Property 1, $2,010.00"],
            ),
            checks.Check(
                "1002",
                date(2026, 2, 2),
                "Vendor 1",
                Decimal("240.00"),
                "Bill PLB-1001",
                lines=["Bill PLB-1001, $240.00"],
            ),
        ],
    )
    matches(request, "checks", pdf_text(printed))
