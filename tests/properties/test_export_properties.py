"""Property-based tests for exports (src/pms/exports.py): whatever a report holds, the export
holds the same, cell for cell.

Hypothesis builds tables of random shape whose cells are any text (quotes, commas, line breaks,
formula starts, control characters, any script), money amounts, days, times in random zones,
whole numbers, flags and NULLs. For every table:
  - CSV reads back with the same rows and cells: nothing splits or merges, every text comes
    back exactly (after the one apostrophe put before a formula start), and NULL is empty;
  - Excel reads back the same values, types kept (times to the second), and no cell is ever a
    formula (control
    characters XML can't hold read as "?", line breaks as "\n", and rows of NULLs at the end
    as nothing: Excel stores no empty rows);
  - the PDF builds and carries the title.
"""

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st
from openpyxl import load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from pypdf import PdfReader

from pms import exports

GENERATED = datetime(2026, 3, 2, 8, 30, tzinfo=UTC)
MONEY = st.decimals(
    min_value=Decimal("-1000000000000"), max_value=Decimal("1000000000000"), places=2
)
VALUES = st.one_of(
    st.none(),
    st.text(max_size=40),
    st.sampled_from(["=1+1", "+44", "-x", "@sum", "\tx", "\rx", "'quoted", "a,b", 'say "hi"']),
    MONEY,
    st.dates(),
    st.datetimes(timezones=st.timezones()),
    st.integers(min_value=-(10**15), max_value=10**15),
    st.booleans(),
)


@st.composite
def tables(draw):
    width = draw(st.integers(min_value=1, max_value=5))
    columns = tuple(
        draw(
            st.lists(st.from_regex(r"[a-z_]{1,12}", fullmatch=True), min_size=width, max_size=width)
        )
    )
    rows = draw(st.lists(st.tuples(*[VALUES] * width), max_size=15))
    return exports.Table(draw(st.text(max_size=30)), columns, tuple(rows))


def unescaped(cell: str) -> str:
    return cell[1:] if cell.startswith("'") and cell[1:].startswith(exports.FORMULA_START) else cell


@given(tables())
def test_csv_holds_every_cell(table):
    read = list(csv.reader(io.StringIO(exports.to_csv(table).decode(), newline="")))

    assert read[0] == list(table.columns)
    assert len(read) == len(table.rows) + 1
    for row, cells in zip(table.rows, read[1:], strict=True):
        assert len(cells) == len(row)
        for value, cell in zip(row, cells, strict=True):
            if isinstance(value, str):
                assert unescaped(cell) == value
                assert not cell.startswith(exports.FORMULA_START)
            else:
                assert cell == exports.text(value)


@given(tables())
def test_excel_holds_every_value_and_no_formula(table):
    sheet = load_workbook(io.BytesIO(exports.to_xlsx(table, GENERATED))).active
    read = list(sheet.iter_rows())

    assert [cell.value for cell in read[0]] == list(table.columns)
    # Excel stores no empty rows: rows of NULLs at the end read as nothing.
    trailing_nulls = len(table.rows) - len(read) + 1
    assert all(
        all(v is None for v in row) for row in table.rows[len(table.rows) - trailing_nulls :]
    )
    for row, cells in zip(table.rows, read[1:], strict=False):
        for value, cell in zip(row, cells, strict=True):
            assert cell.data_type != "f"
            if isinstance(value, str):
                sheet_text = ILLEGAL_CHARACTERS_RE.sub("?", value)
                sheet_text = sheet_text.replace("\r\n", "\n").replace("\r", "\n")
                assert cell.value == (sheet_text or None)
            elif isinstance(value, Decimal):
                assert Decimal(str(cell.value)) == value
            elif isinstance(value, datetime):
                assert cell.value == value.astimezone(UTC).replace(tzinfo=None, microsecond=0)
            elif value is None or isinstance(value, bool | int):
                assert cell.value == value
            else:  # a day
                assert cell.value == datetime.combine(value, datetime.min.time())


@given(tables())
def test_the_pdf_always_builds(table):
    pages = PdfReader(io.BytesIO(exports.to_pdf(table, GENERATED))).pages

    assert len(pages) >= 1
    assert "Generated 2026-03-02 08:30:00 UTC" in pages[0].extract_text()
