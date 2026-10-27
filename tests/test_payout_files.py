"""The payout files the PMS writes (src/pms/payouts) and the sealing of account numbers
(src/pms/sealed.py), without a database: an ACH file's records as NACHA lays them out, a check's
amount in words, the checks PDF, and a sealed number that opens only under its key and row."""

import base64
import io
import os
from datetime import date, datetime
from decimal import Decimal

import pytest
from pypdf import PdfReader

from pms import sealed
from pms.payouts import checks, nacha


def routing(prefix8: str) -> str:
    d = [int(c) for c in prefix8]
    for c in range(10):
        n = [*d, c]
        if (3 * (n[0] + n[3] + n[6]) + 7 * (n[1] + n[4] + n[7]) + n[2] + n[5] + n[8]) % 10 == 0:
            return prefix8 + str(c)
    raise AssertionError


BANK = routing("00123456")
ORIGIN = nacha.Origin(
    BANK, "Synthetic Bank", "1000000000", "Golden PMC", "1000000000", "Golden PMC"
)


def entry(prefix: str, amount: str, name: str = "Owner 1", savings: bool = False) -> nacha.Entry:
    return nacha.Entry(routing(prefix), "12345678", savings, Decimal(amount), name, "OWNER DRAW")


def write(*batches: nacha.Batch) -> list[str]:
    return nacha.write(ORIGIN, list(batches), datetime(2026, 10, 24, 9, 30)).splitlines()


def test_an_ach_file_lays_out_its_records_as_nacha_asks():
    lines = write(
        nacha.Batch(
            "PPD",
            "OWNER DRAW",
            date(2026, 10, 26),
            [entry("00987654", "650.00"), entry("00111111", "12.34", "Owner 2", savings=True)],
        )
    )

    assert [line[0] for line in lines] == ["1", "5", "6", "6", "8", "9", "9", "9", "9", "9"]
    assert all(len(line) == 94 for line in lines)
    header, batch, first, second, control, total = lines[:6]
    assert header[3:13] == " " + BANK and header[23:33] == "2610240930"
    assert batch[1:4] == "220" and batch[50:53] == "PPD" and batch[69:75] == "261026"
    assert batch[79:87] == BANK[:8]
    assert (first[1:3], second[1:3]) == ("22", "32")  # checking, savings credits
    assert first[29:39] == "0000065000" and second[29:39] == "0000001234"
    assert first[54:76] == "OWNER 1".ljust(22)
    assert (first[79:], second[79:]) == (BANK[:8] + "0000001", BANK[:8] + "0000002")
    entry_hash = int(routing("00987654")[:8]) + int(routing("00111111")[:8])
    assert control[4:10] == "000002" and control[10:20] == str(entry_hash).rjust(10, "0")
    assert control[32:44] == "000000066234"
    assert total[1:7] == "000001" and total[7:13] == "000001" and total[13:21] == "00000002"
    assert total[43:55] == "000000066234"
    assert lines[6:] == ["9" * 94] * 4


def test_an_ach_file_writes_names_in_capitals_with_its_own_characters_only():
    lines = write(
        nacha.Batch(
            "CCD", "VENDOR PAY", date(2026, 10, 26), [entry("00987654", "1.00", "Bö & Sons, Ltd.")]
        )
    )
    assert lines[2][54:76] == "B  & SONS, LTD.".ljust(22)


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"amount": Decimal("0")}, "positive amount in cents"),
        ({"amount": Decimal("1.005")}, "positive amount in cents"),
        ({"routing_number": "12345"}, "nine digits"),
        ({"account_number": "1" * 18}, "at most 17 digits"),
        # An entry's amount has ten digits of cents: $99,999,999.99 at most.
        ({"amount": Decimal("100000000.00")}, "doesn't fit in 10 digits"),
    ],
)
def test_an_ach_file_refuses_an_entry_a_bank_would(change, why):
    bad = nacha.Entry(**{**entry("00987654", "1.00").__dict__, **change})
    with pytest.raises(ValueError, match=why):
        write(nacha.Batch("PPD", "OWNER DRAW", date(2026, 10, 26), [bad]))


def test_an_ach_file_has_no_empty_batch():
    with pytest.raises(ValueError, match="a batch credits someone"):
        write(nacha.Batch("PPD", "OWNER DRAW", date(2026, 10, 26), []))


@pytest.mark.parametrize(
    ("amount", "words"),
    [
        ("240.00", "Two hundred forty and 00/100 dollars"),
        ("1.05", "One and 05/100 dollars"),
        ("0.99", "Zero and 99/100 dollars"),
        ("21.21", "Twenty-one and 21/100 dollars"),
        ("110.00", "One hundred ten and 00/100 dollars"),
        ("12345.67", "Twelve thousand three hundred forty-five and 67/100 dollars"),
        ("1000000.00", "One million and 00/100 dollars"),
    ],
)
def test_a_checks_amount_reads_in_words(amount, words):
    assert checks.in_words(Decimal(amount)) == words


@pytest.mark.parametrize("amount", ["0", "-1", "1.001", "1000000000"])
def test_a_check_is_for_a_positive_amount_in_cents(amount):
    with pytest.raises(ValueError):
        checks.in_words(Decimal(amount))


def test_the_checks_print_one_a_page_with_the_payee_amount_and_stubs():
    printed = checks.write(
        "Golden PMC",
        [
            checks.Check(
                "2001",
                date(2026, 10, 24),
                "Vendor 1",
                Decimal("240.00"),
                "Bill PLB-1",
                ["1 Example Way", "Sample City, ST 00000"],
                ["Bill PLB-1: $240.00"],
            ),
            checks.Check("2002", date(2026, 10, 24), "Owner 2", Decimal("50.00"), "Owner draw"),
        ],
    )
    reader = PdfReader(io.BytesIO(printed))
    assert len(reader.pages) == 2
    first = reader.pages[0].extract_text()
    assert "Vendor 1" in first and "**240.00" in first
    assert "Two hundred forty and 00/100 dollars" in first and "Check 2001" in first
    assert "Bill PLB-1: $240.00" in first and "Sample City, ST 00000" in first
    with pytest.raises(ValueError):
        checks.write("Golden PMC", [])


def test_a_sealed_number_opens_only_under_its_key_and_row():
    key = os.urandom(32)
    sealed_number = sealed.seal("12345678", "row-1", key)
    assert b"12345678" not in sealed_number
    assert sealed.unseal(sealed_number, "row-1", key) == "12345678"
    for row, other in (("row-2", key), ("row-1", os.urandom(32))):
        with pytest.raises(sealed.Unreadable):
            sealed.unseal(sealed_number, row, other)
    with pytest.raises(sealed.Unreadable):
        sealed.unseal(b"\x02" + sealed_number[1:], "row-1", key)
    # Sealing the same number twice gives different bytes (a fresh nonce each time).
    assert sealed.seal("12345678", "row-1", key) != sealed_number


@pytest.mark.parametrize(
    ("environ", "why"),
    [
        ({}, "no PMS_DATA_KEY"),
        ({"PMS_DATA_KEY": "not base64!"}, "not base64"),
        ({"PMS_DATA_KEY": base64.b64encode(b"short").decode()}, "not 32 bytes"),
    ],
)
def test_a_server_without_a_proper_key_keeps_no_numbers(environ, why):
    with pytest.raises(sealed.NoKey, match=why):
        sealed.key_from(environ)
    assert len(sealed.key_from({"PMS_DATA_KEY": base64.b64encode(b"k" * 32).decode()})) == 32
