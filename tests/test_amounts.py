"""Amounts read from people's files (src/pms/statements.py read_amount: bank statements, payment
files, everything a switch brings in; docs/edge-cases.md F-064). A comma is only the thousands
separator of US amounts ("1,234.56"); a decimal comma ("1234,56", as a European spreadsheet
writes it) is refused saying so, never read as 100 times the amount."""

from decimal import Decimal

import pytest

from pms import statements


@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("1234.56", Decimal("1234.56")),
        ("1,234.56", Decimal("1234.56")),
        ("$1,234", Decimal("1234")),
        ("1,000,000.00", Decimal("1000000.00")),
        ("(1,234.00)", Decimal("-1234.00")),
        ("-1,200.50", Decimal("-1200.50")),
        (" 25 ", Decimal("25")),
        ("0.5", Decimal("0.5")),
    ],
)
def test_an_amount_with_us_thousands_separators_reads(text, amount):
    assert statements.read_amount(text, "line 2") == amount


@pytest.mark.parametrize(
    "text", ["1234,56", "1200,50", "12,5", "1.234,56", "1,2345", "1,23,456.00", "1,2,3", "12,50,0"]
)
def test_a_decimal_comma_or_a_misplaced_comma_is_refused_saying_so(text):
    with pytest.raises(statements.StatementError) as refused:
        statements.read_amount(text, "line 2")
    assert str(refused.value) == (
        f"line 2: {text!r} is not an amount: a comma only separates thousands (1,234.56);"
        " write a decimal comma as a point (1234.56)"
    )
