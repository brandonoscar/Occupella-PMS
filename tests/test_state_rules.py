"""State rules as warnings (q142, src/pms/state_rules.py): the checks, on a made-up state's rules
of each shape (so a change to the real table doesn't move these), then the real table's own
shape. A check only warns; the API's side is in tests/api/test_state_rules_api.py."""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pms import state_rules
from pms.state_rules import (
    Bigger,
    EntryRule,
    LateFeeRule,
    RentHistory,
    RentNoticeRule,
    Source,
    StateRules,
    entry_warnings,
    late_fee_warnings,
    rent_change_warnings,
)

SOURCE = Source("Test Code 1-1", "https://example.org/test-code", "excerpt")
SECONDARY = Source("Test Code 2-2", "https://example.org/summary", "secondary")

LESSER = LateFeeRule(
    5,
    "rent is unpaid five days after its due date",
    "the lesser of $50 or 5% of the monthly rent",
    state_rules.lesser_of_rent("50", "5"),
    SOURCE,
)
OF_LATE = LateFeeRule(
    5,
    "rent is unpaid five days after its due date",
    "10% of the rent past due",
    state_rules.of_late("10"),
    SECONDARY,
)
BY_UNITS = LateFeeRule(
    2,
    "rent is unpaid two full days after its due date",
    "12% of the monthly rent at a property of four units or fewer, else 10%",
    state_rules.by_units(4, "12", "10"),
    SOURCE,
)
GREATER = LateFeeRule(
    7,
    "rent is at least seven days late",
    "the greater of $50 or 5% of the rent past due",
    state_rules.greater_of_late("50", "5"),
    SOURCE,
    state_rules.kink("50", "5"),
)
WAIT_ONLY = LateFeeRule(30, "rent is 30 days late", None, None, SOURCE)
NOTICE = RentNoticeRule(
    30, "a rent increase needs 30 days' written notice", SOURCE, bigger=Bigger(Decimal(10), 90)
)
TENANCY = RentNoticeRule(
    0,
    "a rent increase of 5% or more needs 30, 60 or 90 days' notice",
    SOURCE,
    by_tenancy=(Decimal(5), ((2, 90), (1, 60), (0, 30))),
)
LIMITS = RentNoticeRule(
    90,
    "a rent increase needs 90 days' written notice",
    SOURCE,
    not_first_year=SECONDARY,
    once_a_year=SOURCE,
)
TERMS = RentNoticeRule(
    60,
    "a rent increase needs 60 days' written notice",
    SOURCE,
    bigger=Bigger(Decimal(10), 75, at_least=True),
    longer_term=(1, 90),
)
ENTRY = EntryRule(48, "48 hours' notice before entering", SOURCE)
UNLESS_ASKED = EntryRule(
    72, "72 hours' notice before routine maintenance the tenant didn't ask for", SOURCE, True
)


# The real table, kept before the made-up one takes its place.
REAL = state_rules.RULES
ONE = Decimal("1.00")
MADE_UP = {
    "AA": StateRules("Aland", late_fee=LESSER, rent_notice=NOTICE, entry=ENTRY),
    "BB": StateRules("Bland", late_fee=OF_LATE, rent_notice=TENANCY),
    "CC": StateRules("Cland", late_fee=BY_UNITS, rent_notice=LIMITS),
    "DD": StateRules("Dland"),
    "EE": StateRules("Eland", late_fee=GREATER, rent_notice=TERMS, entry=UNLESS_ASKED),
    "FF": StateRules("Fland", late_fee=WAIT_ONLY),
}


@pytest.fixture(autouse=True, scope="module")
def made_up_table():
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(state_rules, "RULES", MADE_UP)
        yield


def money(text):
    return Decimal(text)


def late(state, grace=5, flat="0", percent="0", maximum=None, rents=(), units=1):
    return late_fee_warnings(
        state,
        grace,
        money(flat),
        money(percent),
        None if maximum is None else money(maximum),
        [(f"Unit {i + 1}", money(r)) for i, r in enumerate(rents)],
        units,
    )


def test_late_fees_within_the_rules_warn_of_nothing():
    assert late("AA", grace=5, flat="25", percent="1", rents=["1500.00", "900.00"]) == []
    # 5% of $900.00 is $45.00: the cap there, met by a maximum.
    assert late("AA", grace=7, flat="50", maximum="45.00", rents=["900.00"]) == []


def test_a_wait_shorter_than_the_states_warns_with_its_statute():
    [warning] = late("AA", grace=3)

    assert warning.rule == "late fee wait"
    assert warning.said == (
        "Aland: no late fee until rent is unpaid five days after its due date (Test Code 1-1)."
        " These terms charge one after 3 days of grace; 5 or more meets it."
    )
    assert (warning.state, warning.cite, warning.how) == ("AA", "Test Code 1-1", "excerpt")


def test_a_fee_over_the_cap_at_a_leases_rent_names_the_first_and_counts_the_rest():
    [warning] = late("AA", flat="40", percent="1", rents=["500.00", "2000.00", "600.00"])

    # $40 + 1%: $45.00 on $500.00 (cap $25.00), $60.00 on $2,000.00 (cap $50.00), $46.00 on
    # $600.00 (cap $30.00).
    assert warning.rule == "late fee cap"
    assert warning.said == (
        "Aland: a late fee is at most the lesser of $50 or 5% of the monthly rent (Test Code 1-1)."
        " On Unit 1's rent of $500.00 these terms charge up to $45.00, over $25.00; 2 more leases"
        " too."
    )
    [one] = late("AA", flat="40", rents=["500.00", "2000.00"])
    assert one.said.endswith("over $25.00.")


def test_with_no_lease_in_effect_only_the_wait_is_checked():
    assert late("AA", grace=5, flat="500", rents=[]) == []


def test_a_cap_of_the_rent_late_is_checked_with_a_dollar_of_it_late_too():
    assert late("BB", percent="10", rents=["1500.00"]) == []
    [flat] = late("BB", flat="5", rents=["1500.00"])
    [percent] = late("BB", percent="12.5", rents=["1500.00"])

    assert flat.said == (
        "Bland: a late fee is at most 10% of the rent past due (Test Code 2-2). On Unit 1's rent"
        " of $1,500.00, with $1.00 of it late, these terms charge $5.00, over $0.10."
    )
    assert percent.said.endswith(
        "On Unit 1's rent of $1,500.00 these terms charge up to $187.50, over $150.00."
    )
    assert flat.how == "secondary"


def test_the_greater_of_a_flat_amount_and_a_percent_of_the_rent_late():
    # $50 up to $1,000 late, then 5%: a flat $60 passes it below $1,200 late.
    assert late("EE", grace=7, flat="50", rents=["3000.00"]) == []
    assert late("EE", grace=7, percent="5", rents=["3000.00"]) == []
    [over] = late("EE", grace=7, flat="60", rents=["3000.00"])
    assert "with $1,000.00 of it late, these terms charge $60.00, over $50.00" in over.said
    # Up to $100: 6% of $3,000.00 is $180.00, over its $150.00; it reaches $100 at $1,666.67 late,
    # $83.33 over there; at the whole rent $100.00 is under $150.00.
    [kinked] = late("EE", grace=7, percent="6", maximum="100.00", rents=["3000.00"])
    assert "with $1,666.67 of it late, these terms charge $100.00, over $83.33" in kinked.said
    assert late("FF", grace=30, flat="900", percent="50", rents=["100.00"]) == []


def test_a_cap_by_the_number_of_units():
    assert late("CC", grace=2, percent="12", rents=["1000.00"], units=4) == []
    [warning] = late("CC", grace=2, percent="12", rents=["1000.00"], units=5)
    assert "these terms charge up to $120.00, over $100.00" in warning.said


def test_a_state_without_rules_or_an_address_is_not_checked():
    assert late("DD", grace=0, flat="1000", rents=["100.00"]) == []
    assert late("ZZ", grace=0, flat="1000", rents=["100.00"]) == []
    assert late(None, grace=0, flat="1000", rents=["100.00"]) == []
    day = date(2020, 1, 1)
    history = RentHistory(day, ((day, money("1000")),), day, None)
    assert rise("DD", date(2026, 2, 1), date(2026, 1, 31), "5000", history) == []
    assert rise(None, date(2026, 2, 1), date(2026, 1, 31), "5000", history) == []
    assert entry_warnings("BB", date(2026, 2, 1), date(2026, 2, 1)) == []
    assert entry_warnings(None, date(2026, 2, 1), date(2026, 2, 1)) == []


def rise(state, effective_on, notice_given_on, rent, lived):
    return rent_change_warnings(state, effective_on, notice_given_on, money(rent), lived)


def history(since, *steps, starts=None, ends=None):
    rents = ((since, money(steps[0])),) + tuple((on, money(r)) for on, r in steps[1:])
    return RentHistory(since, rents, starts or since, ends)


def test_rent_increase_notice_and_a_bigger_increase_needing_more():
    lived = history(date(2024, 3, 1), "1000.00")

    assert rise("AA", date(2026, 3, 1), date(2026, 1, 30), "1100.00", lived) == []
    [short] = rise("AA", date(2026, 3, 1), date(2026, 2, 1), "1050.00", lived)
    [big] = rise("AA", date(2026, 3, 1), date(2026, 1, 1), "1100.01", lived)

    assert short.rule == "rent increase notice"
    assert short.said == (
        "Aland: a rent increase needs 30 days' written notice (Test Code 1-1). Notice given"
        " 02/01/2026 for 03/01/2026 is 28 days."
    )
    assert big.said == (
        "Aland: an increase of more than 10% within 12 months (from $1,000.00) needs 90 days'"
        " written notice (Test Code 1-1). Notice given 01/01/2026 for 03/01/2026 is 59 days."
    )
    # A lower rent, or the same, needs no notice here.
    assert rise("AA", date(2026, 3, 1), date(2026, 2, 28), "900.00", lived) == []


def test_a_bigger_increase_counts_from_the_lowest_rent_of_the_year_before():
    # $1,000 to $1,060 in July, then to $1,120 next February: 12% over $1,000 within 12 months.
    rose = history(date(2024, 3, 1), "1000.00", (date(2025, 7, 1), "1060.00"))

    [warning] = rise("AA", date(2026, 2, 1), date(2025, 12, 1), "1120.00", rose)
    assert "(from $1,000.00)" in warning.said
    # A year after the first rise, the lowest of the year before is $1,060.00: 5.7%, 30 days do.
    assert rise("AA", date(2026, 7, 2), date(2026, 6, 1), "1120.00", rose) == []


def test_notice_by_how_long_the_tenant_lived_there_or_the_leases_term():
    new = history(date(2026, 1, 1), "1000.00")
    year = history(date(2025, 1, 1), "1000.00")
    years = history(date(2023, 1, 1), "1000.00")
    two_year_lease = history(date(2026, 1, 1), "1000.00", ends=date(2027, 12, 31))

    def warned(lived, notice, rent="1050.00"):
        return rent_change_warnings("BB", date(2026, 6, 1), notice, money(rent), lived)

    assert warned(new, date(2026, 5, 1)) == []
    assert warned(year, date(2026, 4, 2)) == []
    [sixty] = warned(year, date(2026, 4, 3))
    assert sixty.said == (
        "Bland: an increase of 5% or more to a tenant there 1 year or more, or on a lease that"
        " long, needs 60 days' written notice (Test Code 1-1). Notice given 04/03/2026 for"
        " 06/01/2026 is 59 days."
    )
    [ninety] = warned(years, date(2026, 4, 2))
    assert "there 2 years or more, or on a lease that long, needs 90 days'" in ninety.said
    assert len(warned(two_year_lease, date(2026, 4, 2))) == 1
    [thirty] = warned(new, date(2026, 5, 3))
    assert thirty.said.startswith("Bland: an increase of 5% or more needs 30 days' written notice")
    # Under 5% no notice is asked here.
    assert warned(years, date(2026, 5, 31), rent="1049.99") == []


def test_no_increase_in_a_tenancys_first_year_nor_twice_in_twelve_months():
    young = history(date(2025, 9, 1), "1000.00")
    rose = history(date(2023, 1, 1), "1000.00", (date(2025, 10, 1), "1030.00"))

    [first] = rise("CC", date(2026, 8, 1), date(2026, 5, 1), "1050.00", young)
    [twice] = rise("CC", date(2026, 9, 1), date(2026, 6, 1), "1050.00", rose)

    assert first.rule == "rent increase in the first year"
    assert first.said == (
        "Cland: no rent increase in a tenancy's first year (Test Code 2-2). This tenancy began"
        " 09/01/2025."
    )
    assert (first.cite, first.how) == ("Test Code 2-2", "secondary")
    assert twice.rule == "rent increase once a year"
    assert twice.said.endswith("The rent rose 10/01/2025.")
    assert rise("CC", date(2026, 10, 2), date(2026, 7, 1), "1050.00", rose) == []
    assert rise("CC", date(2026, 9, 1), date(2026, 6, 1), "1050.00", young) == []


def test_a_longer_lease_or_a_bigger_increase_from_at_least_a_percent():
    month_to_month = history(date(2024, 1, 1), "1000.00")
    year_lease = history(date(2026, 1, 1), "1000.00", ends=date(2026, 12, 31))

    assert rise("EE", date(2026, 6, 1), date(2026, 4, 2), "1050.00", month_to_month) == []
    [longer] = rise("EE", date(2026, 6, 1), date(2026, 4, 2), "1050.00", year_lease)
    assert longer.said.startswith(
        "Eland: a lease for a term longer than 1 month needs 90 days' written notice of a rent"
        " increase (Test Code 1-1)."
    )
    # Exactly 10% needs 75 days where the rule says "at least".
    [ten] = rise("EE", date(2026, 6, 1), date(2026, 4, 2), "1100.00", month_to_month)
    assert "an increase of at least 10% within 12 months (from $1,000.00) needs 75" in ten.said
    assert rise("EE", date(2026, 6, 1), date(2026, 3, 18), "1100.00", month_to_month) == []


def test_entry_notice_by_whole_days():
    [warning] = entry_warnings("AA", date(2026, 2, 2), date(2026, 2, 1))

    assert warning.rule == "entry notice"
    assert warning.said == (
        "Aland asks 48 hours' notice before entering (Test Code 1-1). This visit on 02/02/2026 was"
        " set on 02/01/2026."
    )
    assert entry_warnings("AA", date(2026, 2, 3), date(2026, 2, 1)) == []
    # A repair the tenant asked for is not one the rule covers where it says so.
    assert entry_warnings("AA", date(2026, 2, 2), date(2026, 2, 1), requested=True) != []
    assert entry_warnings("EE", date(2026, 2, 2), date(2026, 2, 1), requested=True) == []
    assert entry_warnings("EE", date(2026, 2, 2), date(2026, 2, 1)) != []


# --- properties ----------------------------------------------------------------------------------

cents = st.integers(min_value=0, max_value=50_000).map(lambda c: Decimal(c) / 100)
rent_cents = st.integers(min_value=100, max_value=1_000_000)
rents = st.lists(rent_cents.map(lambda c: Decimal(c) / 100), max_size=5)
percents = st.integers(min_value=0, max_value=2_000).map(lambda p: Decimal(p) / 100)


@given(
    state=st.sampled_from(["AA", "BB", "CC"]),
    grace=st.integers(min_value=0, max_value=30),
    flat=cents,
    percent=percents,
    maximum=st.none() | cents,
    leases=rents,
    units=st.integers(min_value=1, max_value=12),
    more_grace=st.integers(min_value=0, max_value=30),
)
def test_more_grace_or_a_smaller_fee_never_adds_a_warning(
    state, grace, flat, percent, maximum, leases, units, more_grace
):
    """Warnings follow the terms one way: lengthening the grace or lowering a fee's parts (or
    its maximum) takes warnings away and never adds one, and no fee at all never passes a cap."""
    rule = state_rules.RULES[state].late_fee
    assert rule is not None

    def kinds(g, f, p, m):
        found = late_fee_warnings(state, g, f, p, m, [("Unit", r) for r in leases], units)
        assert all(w.state == state and w.cite == rule.source.cite for w in found)
        return {w.rule for w in found}

    before = kinds(grace, flat, percent, maximum)
    assert kinds(grace + more_grace, flat, percent, maximum) <= before
    assert kinds(grace, flat / 2, percent / 2, maximum) <= before
    if maximum is not None:
        assert kinds(grace, flat, percent, maximum / 2) <= before
    assert "late fee cap" not in kinds(grace, Decimal(0), Decimal(0), maximum)
    assert ("late fee wait" in before) == (grace < rule.grace_days)
    # A cap warning names an amount late at which the fee passes the cap.
    for warning in late_fee_warnings(
        state, grace, flat, percent, maximum, [("Unit", r) for r in leases], units
    ):
        if warning.rule == "late fee cap":
            assert ", over $" in warning.said


@given(
    state=st.sampled_from(["AA", "BB", "CC", "EE"]),
    flat=cents,
    percent=percents,
    maximum=st.none() | cents,
    rent=st.integers(min_value=100, max_value=600_000).map(lambda c: Decimal(c) / 100),
    units=st.integers(min_value=1, max_value=12),
    lates=st.lists(st.integers(min_value=100, max_value=600_000), min_size=1, max_size=40),
)
def test_no_cap_warning_means_no_amount_late_passes_the_cap(
    state, flat, percent, maximum, rent, units, lates
):
    """The check looks at a few amounts late, not every one: when it finds none over the cap, no
    amount late from a dollar to the whole rent is over it either (tried at random amounts)."""
    rule = state_rules.RULES[state].late_fee
    assert rule is not None and rule.cap is not None
    found = late_fee_warnings(state, 30, flat, percent, maximum, [("Unit", rent)], units)
    if found:
        return
    for late_cents in lates:
        late = min(rent, Decimal(late_cents) / 100)
        fee = (flat + percent * late / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
        if maximum is not None:
            fee = min(fee, maximum)
        assert fee <= rule.cap(rent, late, units), (late, fee)


@given(
    state=st.sampled_from(["AA", "BB", "CC", "EE"]),
    since=st.dates(min_value=date(2018, 1, 1), max_value=date(2026, 6, 1)),
    rent=st.integers(min_value=50_000, max_value=500_000),
    rises=st.lists(st.integers(min_value=-10_000, max_value=30_000), max_size=4),
    months=st.integers(min_value=1, max_value=60),
    notice=st.integers(min_value=0, max_value=120),
    more=st.integers(min_value=0, max_value=60),
    new=st.integers(min_value=-20_000, max_value=60_000),
)
def test_more_notice_never_adds_a_warning_and_a_lower_rent_needs_none(
    state, since, rent, rises, months, notice, more, new
):
    """However the rent went before: a rent not above the one before warns of nothing; giving
    notice earlier never adds a notice warning; 90 days' notice (the longest any tier asks here)
    meets every notice rule; and the first-year and once-a-year rules don't turn on the notice."""
    steps = [(since, Decimal(rent) / 100)]
    for i, rise in enumerate(rises):
        on = (since + timedelta(days=200 * (i + 1))).replace(day=1)
        steps.append((on, max(Decimal("1.00"), steps[-1][1] + Decimal(rise) / 100)))
    lived = RentHistory(since, tuple(steps), steps[-1][0], None)
    effective = (steps[-1][0] + timedelta(days=31 * months)).replace(day=1)
    before = lived.rent_on(effective - timedelta(days=1))
    new_rent = max(Decimal("1.00"), before + Decimal(new) / 100)

    def found(days):
        given_on = effective - timedelta(days=days)
        return {w.rule for w in rent_change_warnings(state, effective, given_on, new_rent, lived)}

    now = found(notice)
    if new_rent <= before:
        assert now == set()
    assert found(notice + more) <= now
    assert "rent increase notice" not in found(90)
    assert found(notice) - {"rent increase notice"} == found(90)


@given(
    state=st.sampled_from(["AA", "BB", "EE"]),
    told=st.dates(min_value=date(2024, 1, 1), max_value=date(2027, 1, 1)),
    days=st.integers(min_value=0, max_value=10),
)
def test_entry_warns_exactly_when_the_days_between_fall_short(state, told, days):
    rule = state_rules.RULES[state].entry
    warned = entry_warnings(state, told + timedelta(days=days), told) != []
    assert warned == (rule is not None and days * 24 < rule.hours)


# --- the real table ------------------------------------------------------------------------------


def test_the_real_tables_rules_are_whole_and_each_names_its_statute():
    assert REAL, "the table has rules"
    codes = {
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN",
        "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
        "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
        "VT", "VA", "WA", "WV", "WI", "WY",
    }  # fmt: skip
    for code, rules in REAL.items():
        assert code in codes, code
        assert rules.name and rules.name[0].isupper()
        for rule in (rules.late_fee, rules.rent_notice, rules.entry):
            if rule is None:
                continue
            assert rule.source.cite and rule.source.url.startswith("https://"), code
            assert rule.source.how in ("official", "excerpt", "secondary")
        if rules.late_fee is not None:
            fee = rules.late_fee
            assert fee.grace_days >= 0 and (fee.grace_days == 0) == (fee.wait_words is None), code
            assert (fee.cap is None) == (fee.cap_words is None), code
            if fee.cap is not None:
                # A cap is never more than the rent late, and grows with the rent and with what
                # is late.
                for rent in (Decimal("500.00"), Decimal("1000.00"), Decimal("5000.00")):
                    for units in (1, 4, 5, 100):
                        assert Decimal(0) <= fee.cap(rent, rent, units) <= rent, code
                        assert fee.cap(rent, ONE, units) <= fee.cap(rent, rent, units), code
                        assert fee.cap(rent, rent, units) <= fee.cap(rent * 2, rent * 2, units)
        if rules.rent_notice is not None:
            notice = rules.rent_notice
            assert 0 <= notice.days <= 180 and notice.words.startswith("a "), code
            assert notice.days or notice.by_tenancy, code
        if rules.entry is not None:
            assert 0 < rules.entry.hours <= 96, code
    assert "No attorney has reviewed these state rules" in state_rules.NOT_ADVICE
    assert {code for code, rules in REAL.items() if rules.late_fee} == {
        "CO", "CT", "DC", "DE", "HI", "IA", "MA", "MD", "ME", "MN", "NC", "NM", "NV", "NY", "OR",
        "TN", "TX", "VA", "WA",
    }  # fmt: skip
    # Nothing in the table was read on an official site (docs/law.md says why).
    assert {
        rule.source.how
        for rules in REAL.values()
        for rule in (rules.late_fee, rules.rent_notice, rules.entry)
        if rule is not None
    } == {"excerpt", "secondary"}


def test_the_real_table_on_known_cases():
    """A few of the table's numbers as the PMS checks them (docs/law.md): New York's late fee,
    Texas's by the property's units, Colorado's once a year, Florida's entry notice."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(state_rules, "RULES", REAL)
        ny = late("NY", grace=5, flat="75", rents=["1200.00"])
        tx = late("TX", grace=2, percent="11", rents=["2000.00"], units=4)
        tx_big = late("TX", grace=2, percent="11", rents=["2000.00"], units=5)
        rose = history(date(2024, 1, 1), "1000.00", (date(2026, 3, 1), "1050.00"))
        co = rise("CO", date(2026, 12, 1), date(2026, 9, 1), "1100.00", rose)
        fl = entry_warnings("FL", date(2026, 11, 2), date(2026, 11, 2))
    assert [w.said for w in ny] == [
        "New York: a late fee is at most the lesser of $50 or 5% of the monthly rent (N.Y. Real"
        " Prop. Law 238-a(2)). On Unit 1's rent of $1,200.00 these terms charge up to $75.00, over"
        " $50.00."
    ]
    assert tx == [] and [w.rule for w in tx_big] == ["late fee cap"]
    assert [w.rule for w in co] == ["rent increase once a year"]
    assert [w.cite for w in fl] == ["Fla. Stat. 83.53(2)"]
