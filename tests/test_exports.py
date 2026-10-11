"""Exports (src/pms/exports.py): every report as CSV, Excel and PDF, and a PMC's whole ledger.

Every golden case is exported in all three formats and read back, so each report's real columns
and types are covered; the property test covers arbitrary values.
"""

import csv
import io
import uuid
import zipfile
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from helpers import make_pmc, post, transfer_batch
from openpyxl import load_workbook
from pypdf import PdfReader

from pms import exports
from tools import golden

CASES = Path(__file__).parent / "golden" / "cases"
NOON = datetime(2026, 3, 1, 12, tzinfo=UTC)
GENERATED = datetime(2026, 3, 2, 8, 30, tzinfo=UTC)


def read_csv(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode(), newline="")))


def read_xlsx(data: bytes) -> list[list]:
    sheet = load_workbook(io.BytesIO(data)).active
    return [[cell.value for cell in row] for row in sheet.iter_rows()]


def read_pdf(data: bytes) -> list[str]:
    return [page.extract_text() for page in PdfReader(io.BytesIO(data)).pages]


def same_in_excel(value, read):
    """What a report value reads back as from Excel."""
    if value is None or isinstance(value, str):
        return read == value
    if isinstance(value, Decimal):
        return Decimal(str(read)) == value
    if isinstance(value, datetime):
        return read == value.astimezone(UTC).replace(tzinfo=None, microsecond=0)
    if isinstance(value, date):
        return read == datetime.combine(value, datetime.min.time())
    return read == value


# --- every report, in every format --------------------------------------------------------


@pytest.mark.parametrize(
    "case", golden.cases(CASES), ids=lambda case: f"{case.parent.name}/{case.stem}"
)
def test_every_report_exports_in_every_format(conn, case):
    *setup, query = golden.statements(case.read_text())
    with conn.transaction(force_rollback=True), conn.cursor() as cursor:
        for statement in setup:
            cursor.execute(statement.encode())
        cursor.execute(query.encode())
        columns = tuple(column.name for column in cursor.description)
        table = exports.Table(case.parent.name, columns, tuple(cursor.fetchall()))

    assert table.rows, "a golden case with no rows exports nothing worth checking"
    assert read_csv(exports.export(table, "csv")) == [
        list(columns),
        *([exports._csv_cell(value) for value in row] for row in table.rows),
    ]
    sheet = read_xlsx(exports.export(table, "xlsx", GENERATED))
    assert sheet[0] == list(columns)
    assert len(sheet) == len(table.rows) + 1
    for row, read in zip(table.rows, sheet[1:], strict=True):
        assert all(same_in_excel(v, r) for v, r in zip(row, read, strict=True)), (row, read)
    pages = read_pdf(exports.export(table, "pdf", GENERATED))
    assert case.parent.name in pages[0]
    assert "Generated 2026-03-02 08:30:00 UTC" in pages[0]


# --- running a report by name -------------------------------------------------------------


def test_a_report_runs_by_name_with_text_arguments(conn, app_conn):
    pmc = make_pmc(conn)
    post(
        app_conn, pmc.pmc_id, "in-1", [(pmc.operating_cash, pmc.owners[0].account, "125.50")], NOON
    )
    direct = conn.execute(
        "SELECT * FROM trust_report_general_ledger(%s, %s, %s)",
        (pmc.pmc_id, date(2026, 3, 1), date(2026, 3, 31)),
    ).fetchall()

    table = exports.run_report(
        app_conn, "trust_report_general_ledger", [str(pmc.pmc_id), "2026-03-01", "2026-03-31"]
    )

    assert table.rows == tuple(direct)
    assert table.columns[:3] == ("line", "item", "bank")
    assert table.title == f"general ledger ({pmc.pmc_id}, 2026-03-01, 2026-03-31)"


def test_the_ai_role_exports_reports_too(conn, ai_conn):
    pmc = make_pmc(conn)

    table = exports.run_report(ai_conn, "trust_report_vendor_payments", [pmc.pmc_id, 2026])

    assert table.rows[-1][1] == "total"


@pytest.mark.parametrize(
    "name",
    [
        "trust_post_transfers",
        "pg_sleep",
        "trust_report_rent_roll; DROP TABLE trust_pmcs",
        "trust_report_Rent_Roll",
        "trust_report_nothing_by_this_name",
    ],
)
def test_only_a_report_can_be_named(conn, name):
    with pytest.raises(exports.ExportError, match="not a report|no report named"):
        exports.run_report(conn, name, [])


def test_a_report_needs_its_own_arguments(conn):
    with pytest.raises(exports.ExportError, match=r"takes 2 arguments \(uuid, integer\), not 1"):
        exports.run_report(conn, "trust_report_vendor_payments", [uuid.uuid4()])


def test_a_role_without_the_report_is_refused(conn):
    role = f"pms_test_nobody_{uuid.uuid4().hex[:8]}"
    conn.execute(f"CREATE ROLE {role} NOLOGIN")
    try:
        with conn.transaction(force_rollback=True):
            conn.execute(f"SET LOCAL ROLE {role}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                exports.run_report(conn, "trust_report_vendor_payments", [uuid.uuid4(), 2026])
    finally:
        conn.execute(f"DROP ROLE {role}")


def test_an_unknown_format_is_refused():
    with pytest.raises(exports.ExportError, match="no export format 'docx'"):
        exports.export(exports.Table("t", ("a",), ()), "docx")


# --- what each format keeps ---------------------------------------------------------------


TRICKY = exports.Table(
    "tricky",
    ("name", "amount", "due", "at", "flag", "count", "extra"),
    (
        (
            '=HYPERLINK("http://x.invalid")',
            Decimal("-12.50"),
            date(2026, 1, 31),
            NOON,
            True,
            3,
            None,
        ),
        (
            "+1 and @2",
            Decimal("0.00"),
            None,
            NOON.astimezone(timezone(timedelta(hours=-8))),
            False,
            0,
            {"k": 1},
        ),
        ('Zoë 東京, "quoted"\nnext line', Decimal("1234567.89"), None, None, None, None, [1, 2]),
        ("bell\x07 inside", None, None, None, None, None, None),
    ),
)


def test_csv_neutralizes_formulas_and_keeps_everything_else():
    rows = read_csv(exports.to_csv(TRICKY))

    assert rows[1] == [
        '\'=HYPERLINK("http://x.invalid")',
        "-12.50",
        "2026-01-31",
        "2026-03-01T12:00:00+00:00",
        "true",
        "3",
        "",
    ]
    assert rows[2] == [
        "'+1 and @2",
        "0.00",
        "",
        "2026-03-01T12:00:00+00:00",
        "false",
        "0",
        '{"k": 1}',
    ]
    assert rows[3] == ['Zoë 東京, "quoted"\nnext line', "1234567.89", "", "", "", "", "[1, 2]"]
    assert rows[4] == ["bell\x07 inside", "", "", "", "", "", ""]


def test_excel_keeps_types_and_never_holds_a_formula():
    book = load_workbook(io.BytesIO(exports.to_xlsx(TRICKY, GENERATED)))
    sheet = book.active
    cells = [list(row) for row in sheet.iter_rows(min_row=2)]

    assert all(cell.data_type != "f" for row in cells for cell in row)
    assert cells[0][0].value == '=HYPERLINK("http://x.invalid")'
    assert [cell.value for cell in cells[0][1:6]] == [
        -12.5,
        datetime(2026, 1, 31),
        datetime(2026, 3, 1, 12),
        True,
        3,
    ]
    assert cells[1][3].value == datetime(2026, 3, 1, 12)  # the same moment, read in UTC
    assert cells[2][0].value == 'Zoë 東京, "quoted"\nnext line'
    assert cells[3][0].value == "bell? inside"  # a control character XML can't hold
    assert book.properties.title == "tricky"
    assert book.properties.created == datetime(2026, 3, 2, 8, 30)


def test_the_pdf_prints_latin1_and_marks_the_rest():
    text = read_pdf(exports.to_pdf(TRICKY, GENERATED))[0]

    assert "Zoë ??" in text
    assert "1234567.89" in text
    assert "Page 1 of 1" in text


def test_a_long_report_repeats_its_header_on_every_page():
    table = exports.Table(
        "long", ("line", "description"), tuple((n, f"posting {n}") for n in range(1, 301))
    )

    pages = read_pdf(exports.to_pdf(table, GENERATED))

    assert len(pages) > 1
    assert all("line" in page and "description" in page for page in pages)
    assert f"Page {len(pages)} of {len(pages)}" in pages[-1]
    assert "posting 300" in pages[-1]


# --- the whole ledger ---------------------------------------------------------------------


def test_the_ledger_export_holds_every_account_transfer_and_entry_of_the_pmc(conn, app_conn):
    pmc = make_pmc(conn)
    other = make_pmc(conn)
    paid = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "500.00")], NOON)
    post(app_conn, pmc.pmc_id, "fee-1", [(pmc.owners[0].account, pmc.pmc_income, "50.00")], NOON)
    transfer_batch(conn, [(other.operating_cash, other.owners[0].account, "9.00")], NOON)

    archive = zipfile.ZipFile(io.BytesIO(exports.ledger_zip(app_conn, pmc.pmc_id)))
    files = {name: read_csv(archive.read(name)) for name in archive.namelist()}

    assert sorted(files) == ["accounts.csv", "entries.csv", "transfers.csv"]
    accounts = files["accounts.csv"]
    assert accounts[0] == ["ledger_account_id", "bank", "kind", "holder", "balance", "opened_at"]
    assert {row[0] for row in accounts[1:]} == set(pmc.accounts())
    owner = next(row for row in accounts if row[0] == pmc.owners[0].account)
    assert owner[2:5] == ["owner_property", "Owner 1 / Property 1", "450.00"]

    transfers = files["transfers.csv"][1:]
    assert [row[0] for row in transfers][0] == paid[0]
    assert len(transfers) == 2
    assert transfers[0][1] == "2026-03-01T12:00:00+00:00"
    entries = files["entries.csv"][1:]
    assert len(entries) == 4
    for transfer_id in (row[0] for row in transfers):
        assert sum(Decimal(e[3]) for e in entries if e[1] == transfer_id) == 0


def test_the_ledger_export_is_the_same_bytes_each_time(conn, ai_conn):
    pmc = make_pmc(conn)
    transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "1.00")], NOON)

    assert exports.ledger_zip(ai_conn, pmc.pmc_id) == exports.ledger_zip(ai_conn, pmc.pmc_id)


def test_the_ledger_export_refuses_an_unknown_pmc(conn):
    with pytest.raises(exports.ExportError, match="no PMC"):
        exports.ledger_zip(conn, uuid.uuid4())


# --- the command line ---------------------------------------------------------------------


def test_the_command_line_writes_a_report_file(conn, database_url, monkeypatch, tmp_path):
    pmc = make_pmc(conn)
    monkeypatch.setenv("DATABASE_URL", database_url)
    out = tmp_path / "payments.xlsx"

    code = exports.main(
        [
            "--role",
            "trust_app",
            "--out",
            str(out),
            "report",
            "trust_report_vendor_payments",
            str(pmc.pmc_id),
            "2026",
            "--format",
            "xlsx",
        ]
    )

    assert code == 0
    assert read_xlsx(out.read_bytes())[0] == ["line", "item", "vendor", "payments", "paid"]


def test_the_command_line_writes_the_ledger_to_standard_output(
    conn, database_url, monkeypatch, capsysbinary
):
    pmc = make_pmc(conn)
    monkeypatch.setenv("DATABASE_URL", database_url)

    assert exports.main(["ledger", str(pmc.pmc_id)]) == 0

    archive = zipfile.ZipFile(io.BytesIO(capsysbinary.readouterr().out))
    assert len(read_csv(archive.read("accounts.csv"))) == len(pmc.accounts()) + 1


def test_the_command_line_says_why_it_refused(database_url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", database_url)

    assert exports.main(["report", "trust_post_transfers"]) == 2
    assert "refused: 'trust_post_transfers' is not a report" in capsys.readouterr().err
