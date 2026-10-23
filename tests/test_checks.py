"""The check writer (src/pms/checks.py): the amount in words as a check writes it, the guarded
figures, and a voucher page for each check with the check in its third and the stubs in the
others. Names and amounts are made up."""

import io
from datetime import date
from decimal import Decimal

import pytest
from pypdf import PdfReader

from pms import checks


@pytest.mark.parametrize(
    ("dollars", "words"),
    [
        (0, "zero"),
        (7, "seven"),
        (13, "thirteen"),
        (20, "twenty"),
        (42, "forty-two"),
        (100, "one hundred"),
        (105, "one hundred five"),
        (1125, "one thousand one hundred twenty-five"),
        (20000, "twenty thousand"),
        (1_000_001, "one million one"),
        (987_654_321, "nine hundred eighty-seven million six hundred fifty-four thousand"
                      " three hundred twenty-one"),
        (12_000_000_000, "twelve billion"),
    ],
)  # fmt: skip
def test_dollars_read_as_a_check_writes_them(dollars, words):
    assert checks.dollars_in_words(dollars) == words


def test_the_legal_amount_and_the_figures():
    assert (
        checks.amount_in_words(Decimal("1125.50"))
        == "One thousand one hundred twenty-five and 50/100"
    )
    assert checks.amount_in_words(Decimal("0.07")) == "Zero and 07/100"
    assert checks.figures(Decimal("1125.5")) == "**1,125.50"


@pytest.mark.parametrize("amount", ["0", "-1.00", "1.005"])
def test_an_amount_is_whole_cents_above_zero(amount):
    with pytest.raises(ValueError, match="whole number of cents"):
        checks.amount_in_words(Decimal(amount))


@pytest.mark.parametrize("dollars", [-1, 10**12])
def test_dollars_out_of_reach_are_refused(dollars):
    with pytest.raises(ValueError, match="not a check's dollars"):
        checks.dollars_in_words(dollars)


CHECK = checks.Check(
    number=1001,
    dated_on=date(2026, 10, 23),
    payee="Demo Roofing Co",
    address=("100 Example Way", "Suite 2", "Springfield, ZZ 00000"),
    amount=Decimal("1125.50"),
    memo="Bill ROOF-17",
    stub=(("Bill ROOF-17: Roof repair", Decimal("1125.50")),),
)


def text_of(pdf: bytes) -> list[str]:
    return [page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages]


@pytest.mark.parametrize("position", ["top", "middle", "bottom"])
def test_a_page_for_each_check_with_two_stubs(position):
    second = checks.Check(
        number=1002, dated_on=date(2026, 10, 23), payee="Demo Owner A", address=(),
        amount=Decimal("40.00"), memo="Owner draw, Elm Court",
        stub=(("Owner draw, Elm Court", Decimal("40.00")),),
    )  # fmt: skip
    pages = text_of(
        checks.render([CHECK, second], checks.Layout(position, 3, -2), "Demo Property Co")
    )

    assert len(pages) == 2
    first = pages[0]
    for part in ("**1,125.50", "One thousand one hundred twenty-five and 50/100",
                 "100 Example Way", "Springfield, ZZ 00000", "Memo: Bill ROOF-17"):  # fmt: skip
        assert first.count(part) == 1, part
    # The stubs: the company, the payee, the day, the check's number and what it pays, twice.
    assert first.count("10/23/2026") == 3
    assert first.count("Check 1001") == 2
    assert first.count("Bill ROOF-17: Roof repair") == 2
    assert first.count("Demo Property Co") == 2
    assert pages[1].count("Check 1002") == 2 and "Forty and 00/100" in pages[1]


def test_the_check_sits_in_its_third_of_the_page():
    def payee_height(position: str) -> float:
        seen: list[float] = []
        pdf = checks.render([CHECK], checks.Layout(position), "Demo Property Co")
        page = PdfReader(io.BytesIO(pdf)).pages[0]
        page.extract_text(
            visitor_text=lambda text, _cm, tm, *_: (
                seen.append(tm[5]) if "**1,125.50" in text else None
            )
        )
        return seen[0]

    # PDF heights run from the bottom of the page: the top third's check is highest.
    assert payee_height("top") > payee_height("middle") > payee_height("bottom")


def test_a_run_has_a_check():
    with pytest.raises(ValueError, match="at least one check"):
        checks.render([], checks.Layout(), "Demo Property Co")
