"""Filling a notice template (src/pms/notice_letters.py; the founder's review q095): each known
blank is replaced once by its value, whatever spaces sit inside its braces; a value that itself
reads like a blank is never filled again; anything else is left as written; unknown blanks are
named."""

from datetime import UTC, date, datetime

from hypothesis import given
from hypothesis import strategies as st

from pms import notice_letters

FIELDS = sorted(notice_letters.FIELDS)
TEXT = st.text(alphabet=st.characters(blacklist_characters="{}"), max_size=40)


def test_known_blanks_are_filled_and_unknown_ones_named():
    values = {name: f"<{name}>" for name in FIELDS}
    text = "Dear {{tenant_names}}, owe {{ balance_due }} by {{notice_date}}; {{amount}} {{ due }}"

    assert notice_letters.unknown(text) == ["amount", "due"]
    assert notice_letters.unknown("{{amount}} {{amount}}") == ["amount"]
    assert notice_letters.fill("Dear {{tenant_names}}, owe {{ balance_due }}.", values) == (
        "Dear <tenant_names>, owe <balance_due>."
    )
    assert notice_letters.unknown("{ not a blank } {{}}") == [""]
    # A name keeps the spaces inside it: "{{ tenant names }}" is not a blank the PMS fills.
    assert notice_letters.unknown("{{ tenant names }}") == ["tenant names"]


def test_a_long_run_of_spaces_in_an_open_blank_is_read_in_one_pass():
    # The blank's pattern once let a space match two ways, so this took minutes (CodeQL
    # py/polynomial-redos); each space now matches one way only.
    text = "{{" + " " * 50_000 + "x"
    assert notice_letters.unknown(text) == []
    assert notice_letters.fill(text, {}) == text


def test_names_read_as_a_person_would_write_them():
    assert notice_letters.names([]) == ""
    assert notice_letters.names(["A"]) == "A"
    assert notice_letters.names(["A", "B"]) == "A and B"
    assert notice_letters.names(["A", "B", "C"]) == "A, B and C"


@given(
    parts=st.lists(st.tuples(TEXT, st.sampled_from(FIELDS)), max_size=6),
    tail=TEXT,
    values=st.fixed_dictionaries({name: st.text(max_size=30) for name in FIELDS}),
)
def test_each_blank_is_filled_once_with_its_value(parts, tail, values):
    template = "".join(f"{text}{{{{{name}}}}}" for text, name in parts) + tail
    expected = "".join(f"{text}{values[name]}" for text, name in parts) + tail

    assert notice_letters.unknown(template) == []
    # A value that reads like a blank ("{{balance_due}}" as a tenant's name) stays as it is.
    assert notice_letters.fill(template, values) == expected


def test_a_letter_is_a_pdf_whatever_its_letters():
    letter = notice_letters.Letter(
        "Late rent",
        "Synthetic Homes LLC\n100 Example Way",
        date(2026, 10, 21),
        "Dear Zoë 😀,\n\nPay.",
    )
    pdf = notice_letters.render(letter, datetime(2026, 10, 21, tzinfo=UTC))
    assert pdf.startswith(b"%PDF-")
