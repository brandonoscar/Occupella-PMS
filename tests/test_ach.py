"""The ACH file writer (src/pms/ach.py): NACHA's 94-character records, an owner's draw in a PPD
batch and a vendor's bill in a CCD batch, the controls' counts, hashes and totals, and padding to
blocks of ten. Routing numbers start 00, which no bank holds; account numbers are made up."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from pms import ach

ORIGINATOR = ach.Originator(
    bank_routing="001000009",
    bank_name="Demo Trust Bank",
    origin="1000000001",
    company_name="Demo Property Co",
    company_id="1000000001",
)
MADE = datetime(2026, 10, 23, 14, 5, tzinfo=UTC)
EFFECTIVE = date(2026, 10, 26)


def entry(sec="PPD", routing="002000008", kind="checking", number="000123456789", amount="125.50",
          identification="Elm Court", name="Demo Owner A"):  # fmt: skip
    return ach.Entry(
        sec=sec,
        routing=routing,
        account_type=kind,
        account_number=number,
        amount=Decimal(amount),
        identification=identification,
        name=name,
    )


def test_a_file_reads_as_the_bank_takes_it():
    text = ach.build(
        ORIGINATOR,
        MADE,
        "A",
        EFFECTIVE,
        [
            entry(
                sec="CCD",
                routing="003000007",
                kind="savings",
                number="0000555501",
                amount="1000.00",
                identification="ROOF-17",
                name="Demo Roofing Co",
            ),  # fmt: skip
            entry(),
        ],
    )

    odfi = "00100000"
    company = "DEMO PROPERTY CO" + " " * 20 + "1000000001"
    assert text.endswith("\r\n")
    assert text.split("\r\n")[:-1] == [
        # The file's header: to the bank, from the company, made 10/23/26 14:05, modifier A.
        "101 001000009" + "1000000001" + "2610231405" + "A" + "094" + "10" + "1"
        + "DEMO TRUST BANK".ljust(23) + "DEMO PROPERTY CO".ljust(23) + " " * 8,
        # The owners' draws (PPD), effective 10/26/26, its entry trace 1, then its control.
        "5220" + company + "PPD" + "OWNER DRAW" + " " * 6 + "261026" + " " * 3 + "1" + odfi
        + "0000001",
        "622" + "002000008" + "000123456789".ljust(17) + "0000012550" + "ELM COURT".ljust(15)
        + "DEMO OWNER A".ljust(22) + "  0" + odfi + "0000001",
        "8220" + "000001" + "0000200000" + "0" * 12 + "000000012550" + "1000000001" + " " * 25
        + odfi + "0000001",
        # The vendors' bills (CCD), a savings account, trace 2.
        "5220" + company + "CCD" + "VENDOR PAY" + " " * 6 + "261026" + " " * 3 + "1" + odfi
        + "0000002",
        "632" + "003000007" + "0000555501".ljust(17) + "0000100000" + "ROOF-17".ljust(15)
        + "DEMO ROOFING CO".ljust(22) + "  0" + odfi + "0000002",
        "8220" + "000001" + "0000300000" + "0" * 12 + "000000100000" + "1000000001" + " " * 25
        + odfi + "0000002",
        # The file's control: 2 batches, 1 block, 2 entries, their hash and total.
        "9" + "000002" + "000001" + "00000002" + "0000500000" + "0" * 12 + "000000112550"
        + " " * 39,
        *["9" * 94] * 2,
    ]  # fmt: skip


def test_every_record_is_94_characters_in_blocks_of_ten():
    for count in (1, 3, 4, 5, 13):
        text = ach.build(ORIGINATOR, MADE, "B", EFFECTIVE, [entry() for _ in range(count)])
        lines = text.split("\r\n")[:-1]
        assert all(len(line) == 94 for line in lines)
        assert len(lines) % 10 == 0
        # A header, a batch of the entries with its control, the file's control, then 9s.
        assert len([line for line in lines if line != "9" * 94]) == count + 4


def test_names_become_upper_case_in_the_characters_nacha_allows():
    assert ach.text("Café Ñandú & Sons; Ltd.", 22) == "CAFE NANDU & SONS  LTD."[:22]
    assert ach.text("x" * 30, 5) == "XXXXX"
    assert ach.text("ab", 4) == "AB  "


@pytest.mark.parametrize(
    ("value", "size"), [(12345678901, 10), (-1, 6)]
)  # fmt: skip
def test_a_number_too_big_for_its_field_is_refused_never_cut(value, size):
    with pytest.raises(ValueError, match="doesn't fit"):
        ach.number(value, size)


@pytest.mark.parametrize("amount", ["0.00", "-1.00", "1.005"])
def test_an_amount_is_whole_cents_above_zero(amount):
    with pytest.raises(ValueError, match="whole number of cents"):
        ach.build(ORIGINATOR, MADE, "A", EFFECTIVE, [entry(amount=amount)])


def test_a_file_has_an_entry():
    with pytest.raises(ValueError, match="at least one entry"):
        ach.build(ORIGINATOR, MADE, "A", EFFECTIVE, [])


def test_the_entry_hash_keeps_its_last_ten_digits():
    assert ach.entry_hash(["991000000"] * 20) == (99100000 * 20) % 10**10
