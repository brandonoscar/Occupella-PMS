"""Reading bank statement files (src/pms/statements.py): CSV in the shapes banks export, and OFX
1.x (SGML) and 2.x (XML). Every file here is synthetic."""

import re
from datetime import date
from decimal import Decimal

import pytest

from pms import statements
from pms.statements import Line, StatementError

OFX_SGML = """OFXHEADER:100
DATA:OFXSGML
VERSION:102

<OFX>
<BANKMSGSRSV1><STMTTRNRS><STMTRS>
<CURDEF>USD
<BANKTRANLIST>
<DTSTART>20260101
<DTEND>20260131120000[-5:EST]
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20260105
<TRNAMT>2750.00
<FITID>D-0105
<NAME>Deposit
</STMTTRN>
<STMTTRN>
<TRNTYPE>CHECK
<DTPOSTED>20260123120000
<TRNAMT>-240.00
<CHECKNUM>101
<NAME>Check
<MEMO>Vendor 1
</STMTTRN>
<STMTTRN>
<TRNTYPE>OTHER
<DTPOSTED>20260124
<TRNAMT>0.00
<NAME>Notice
</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL>
<BALAMT>2510.00
<DTASOF>20260131
</LEDGERBAL>
<AVAILBAL><BALAMT>9.99<DTASOF>20260131</AVAILBAL>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
"""

OFX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKTRANLIST>
<DTSTART>20260201</DTSTART><DTEND>20260228</DTEND>
<STMTTRN><DTPOSTED>20260203</DTPOSTED><TRNAMT>-15.00</TRNAMT><FITID>F1</FITID>
<NAME>Service fee</NAME></STMTTRN>
</BANKTRANLIST>
<LEDGERBAL><BALAMT>2495.00</BALAMT><DTASOF>20260228</DTASOF></LEDGERBAL>
</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""


def test_a_csv_with_an_amount_column_is_read():
    read = statements.read(
        "\ufeffDate,Description,Amount,Reference\n"
        '2026-01-05,Deposit,"$2,750.00",D-0105\n'
        "01/23/2026,Check 101,(240.00),101\n"
        "2026-01-24,Notice,0.00,\n"
        "\n"
        "2026-01-28,Service fee,-15.00\n",
        "csv",
    )
    assert read.lines == [
        Line(date(2026, 1, 5), Decimal("2750.00"), "Deposit", "D-0105"),
        Line(date(2026, 1, 23), Decimal("-240.00"), "Check 101", "101"),
        Line(date(2026, 1, 28), Decimal("-15.00"), "Service fee", None),
    ]
    assert (read.first_day, read.last_day, read.closing_balance) == (None, None, None)


def test_a_csv_with_columns_for_money_out_and_in_is_read():
    read = statements.read(
        "Posted On,Payee,Withdrawals,Deposits,Check Number\n"
        "2026-01-05,Deposit,,1500.00,\n"
        "2026-01-23,Vendor 1,240.00,,101\n"
        "2026-01-24,Vendor 2,-60.00,,102\n",
        "csv",
    )
    assert [(line.amount, line.reference) for line in read.lines] == [
        (Decimal("1500.00"), None),
        (Decimal("-240.00"), "101"),
        (Decimal("-60.00"), "102"),
    ]


def test_a_csv_keeps_descriptions_and_references_short():
    read = statements.read(
        f"Date,Amount,Memo,Ref\n2026-01-05,1.00,{'x' * 600},{'r' * 300}\n", "csv"
    )
    assert (len(read.lines[0].description), len(read.lines[0].reference or "")) == (500, 255)


def test_a_csv_whose_lines_end_in_a_bare_carriage_return_is_read():
    read = statements.read("Date,Amount,Description\r2026-01-02,5.00,Rent\r", "csv")
    assert [(line.posted_on, line.amount) for line in read.lines] == [
        (date(2026, 1, 2), Decimal("5.00"))
    ]


def test_a_field_longer_than_csv_reads_is_refused_in_words():
    with pytest.raises(StatementError, match=r"line 2: this isn't CSV the PMS can read \(field"):
        statements.read("Date,Amount\n" + "x" * 140_000, "csv")


@pytest.mark.parametrize(
    "content, why",
    [
        ("", "the file is empty"),
        ("Description,Amount\nRent,5.00\n", "line 1: no date column"),
        ("Date,Description\n2026-01-01,Rent\n", "line 1: no amount column"),
        ("Date,Debit\n2026-01-01,5.00\n", "line 1: no amount column"),
        ("Date,Amount\n2026-13-01,5.00\n", "line 2: '2026-13-01' is not a date"),
        ("Date,Amount\n2026-01-01,five\n", "line 2: 'five' is not an amount"),
        ("Date,Amount\n2026-01-01,5.001\n", "line 2: '5.001' is not an amount in cents"),
        ("Date,Amount\n2026-01-01,NaN\n", "is not an amount in cents"),
        ("Date,Amount\n2026-01-01,1000000000.00\n", "is not an amount in cents"),
    ],
)
def test_a_csv_that_is_not_a_statement_says_where_and_why(content, why):
    with pytest.raises(StatementError, match=why):
        statements.read(content, "csv")


def test_a_csv_has_at_most_so_many_lines(monkeypatch):
    monkeypatch.setattr(statements, "MAX_LINES", 2)
    with pytest.raises(StatementError, match="at most 2 lines"):
        statements.read("Date,Amount\n" + "2026-01-01,1.00\n" * 3, "csv")


def test_an_sgml_ofx_gives_its_days_closing_balance_and_lines():
    read = statements.read(OFX_SGML, "ofx")
    assert read.lines == [
        Line(date(2026, 1, 5), Decimal("2750.00"), "Deposit", "D-0105"),
        Line(date(2026, 1, 23), Decimal("-240.00"), "Check Vendor 1", "101"),
    ]
    assert (read.first_day, read.last_day, read.closing_balance) == (
        date(2026, 1, 1),
        date(2026, 1, 31),
        Decimal("2510.00"),
    )


def test_an_xml_ofx_is_read_the_same_way():
    read = statements.read(OFX_XML, "ofx")
    assert read.lines == [Line(date(2026, 2, 3), Decimal("-15.00"), "Service fee", "F1")]
    assert (read.first_day, read.last_day, read.closing_balance) == (
        date(2026, 2, 1),
        date(2026, 2, 28),
        Decimal("2495.00"),
    )


def test_an_ofx_without_its_days_or_balance_leaves_them_to_staff():
    read = statements.read("<OFX><STMTTRN><DTPOSTED>20260105<TRNAMT>5.00</STMTTRN></OFX>", "ofx")
    assert read == statements.Statement([Line(date(2026, 1, 5), Decimal("5.00"), "", None)])


@pytest.mark.parametrize(
    "content, why",
    [
        ("Date,Amount\n", "no <OFX> in the file"),
        ("<OFX><STMTTRN><TRNAMT>5.00</STMTTRN></OFX>", "transaction 1: no DTPOSTED or TRNAMT"),
        ("<OFX><STMTTRN><DTPOSTED>2026<TRNAMT>5.00</STMTTRN></OFX>", "'2026' is not an OFX date"),
        ("<OFX><STMTTRN><DTPOSTED>20260105<TRNAMT>x</STMTTRN></OFX>", "'x' is not an amount"),
        ("<OFX><DTSTART>junk</OFX>", "DTSTART: 'junk' is not an OFX date"),
    ],
)
def test_an_ofx_that_is_not_a_statement_says_where_and_why(content, why):
    with pytest.raises(StatementError, match=why):
        statements.read(content, "ofx")


def test_an_ofx_has_at_most_so_many_lines(monkeypatch):
    monkeypatch.setattr(statements, "MAX_LINES", 1)
    block = "<STMTTRN><DTPOSTED>20260105<TRNAMT>5.00</STMTTRN>"
    with pytest.raises(StatementError, match="at most 1 lines"):
        statements.read(f"<OFX>{block}{block}</OFX>", "ofx")


def test_an_ofx_entity_or_doctype_is_never_expanded():
    hostile = (
        '<!DOCTYPE ofx [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        "<OFX><STMTTRN><DTPOSTED>20260105<TRNAMT>5.00<NAME>&x;</STMTTRN></OFX>"
    )
    assert statements.read(hostile, "ofx").lines[0].description == "&x;"


def test_an_ofx_reads_tags_left_open_and_never_slows_on_them():
    # SGML may leave LEDGERBAL open: its balance runs to AVAILBAL, or to the end of the file.
    cut_off = "<OFX><LEDGERBAL><BALAMT>12.00<AVAILBAL><BALAMT>9.99</AVAILBAL></OFX>"
    assert statements.read(cut_off, "ofx").closing_balance == Decimal("12.00")
    to_the_end = "<OFX><LEDGERBAL><BALAMT>7.50"
    assert statements.read(to_the_end, "ofx").closing_balance == Decimal("7.50")
    # A transaction that never closes is no line, and a file of nothing else reads in linear
    # time (a lazy regular expression took quadratic time over it).
    assert statements.read("<OFX>" + "<stmttrn>" * 200_000, "ofx").lines == []


# --- what mutation testing found untested (mutmut, mutation.yml) ----------------------------


@pytest.mark.parametrize(
    "text, amount",
    [
        ("(12.05)", Decimal("-12.05")),
        ("$ 1 234.56", Decimal("1234.56")),
        (" -0.10 ", Decimal("-0.10")),
    ],
)
def test_an_amount_is_read_to_the_cent(text, amount):
    assert statements.read_amount(text, "line 2") == amount


@pytest.mark.parametrize("text", ["(12.05", "12.05)", "()"])
def test_parentheses_mark_money_out_only_when_they_close(text):
    with pytest.raises(StatementError, match=f"^line 2: {re.escape(repr(text))} is not an amount$"):
        statements.read_amount(text, "line 2")


@pytest.mark.parametrize(
    "content, why",
    [
        ("\n\n", "the file is empty"),
        ("Amount\n5.00\n", "line 1: no date column (Date, Posted, Transaction date, ...)"),
        (
            "Date,Credit\n2026-01-01,5.00\n",
            "line 1: no amount column (Amount), or columns for money out and in (Debit, Credit)",
        ),
        ("Date,Debit,Credit\n2026-01-01,x,\n", "line 2: 'x' is not an amount"),
        ("Date,Debit,Credit\n2026-01-01,,y\n", "line 2: 'y' is not an amount"),
    ],
)
def test_a_csv_says_why_in_full(content, why):
    with pytest.raises(StatementError, match=f"^{re.escape(why)}$"):
        statements.read(content, "csv")


def test_a_line_with_no_amount_is_a_note_and_skipped():
    read = statements.read("Date,Amount,Memo\n2026-01-01,,Note\n2026-01-02,3.00,Rent\n", "csv")
    assert read.lines == [Line(date(2026, 1, 2), Decimal("3.00"), "Rent", None)]


def test_a_csv_of_exactly_the_most_lines_is_read(monkeypatch):
    monkeypatch.setattr(statements, "MAX_LINES", 2)
    assert len(statements.read("Date,Amount\n" + "2026-01-01,1.00\n" * 2, "csv").lines) == 2


def test_an_ofx_reads_its_tags_in_any_case():
    lower = re.sub(r"<(/?)([A-Z]+)>", lambda tag: f"<{tag[1]}{tag[2].lower()}>", OFX_SGML)
    assert "<stmttrn>" in lower and "Deposit" in lower
    assert statements.read(lower, "ofx") == statements.read(OFX_SGML, "ofx")


def test_an_ofx_reads_on_past_a_line_with_no_money():
    def line(day, amount):
        return f"<STMTTRN><DTPOSTED>2026010{day}<TRNAMT>{amount}<FITID>F{day}</STMTTRN>"

    read = statements.read(
        f"<OFX>{line(1, '1.00')}{line(2, '0.00')}{line(3, '-2.00')}</OFX>", "ofx"
    )
    assert [(lin.amount, lin.reference) for lin in read.lines] == [
        (Decimal("1.00"), "F1"),
        (Decimal("-2.00"), "F3"),
    ]


def test_an_ofx_keeps_descriptions_and_references_short():
    block = f"<STMTTRN><DTPOSTED>20260105<TRNAMT>5.00<NAME>{'n' * 600}<FITID>{'f' * 300}</STMTTRN>"
    (read,) = statements.read(f"<OFX>{block}</OFX>", "ofx").lines
    assert (len(read.description), len(read.reference or "")) == (500, 255)


@pytest.mark.parametrize(
    "content, why",
    [
        ("<ofx>", None),
        ("<OFX><DTEND>junk</OFX>", "DTEND: 'junk' is not an OFX date"),
        (
            "<OFX><LEDGERBAL><BALAMT>1.001</LEDGERBAL></OFX>",
            "LEDGERBAL: '1.001' is not an amount in cents",
        ),
        (
            "<OFX><STMTTRN><DTPOSTED>2026<TRNAMT>5.00</STMTTRN></OFX>",
            "transaction 1: '2026' is not an OFX date",
        ),
        (
            "<OFX><STMTTRN><DTPOSTED>20260105<TRNAMT>x</STMTTRN></OFX>",
            "transaction 1: 'x' is not an amount",
        ),
        ("Date,Amount\n", "no <OFX> in the file: is it an OFX or QFX statement?"),
    ],
)
def test_an_ofx_says_where_in_full(content, why):
    if why is None:
        assert statements.read(content, "ofx") == statements.Statement([])
        return
    with pytest.raises(StatementError, match=f"^{re.escape(why)}$"):
        statements.read(content, "ofx")
