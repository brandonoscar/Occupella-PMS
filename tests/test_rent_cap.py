"""A year's rent increase capped by inflation (src/pms/rent_cap.py; docs/law.md): the most an
increase may be in California, Oregon and Washington, worked out from the index each names, and
what the warning says when it can't be (never "no cap"). The index figures here are made up, and
every ZIP code is one USPS never issued, of a prefix the PMS reads."""

from datetime import date
from decimal import Decimal

import pytest

from pms import rent_cap
from pms.rent_cap import LOS_ANGELES, SAN_DIEGO, SEATTLE, WEST

LA_ZIP, SD_ZIP, SF_ZIP, OTHER_CA_ZIP = "90000", "92100", "94100", "95800"
D = Decimal


def figures_of(table):
    """A company's figures as rent_cap reads them, from {series: {month: value}}."""

    def figures(series, months):
        held = table.get(series, {})
        return {m: D(held[m]) for m in months if m in held}

    return figures


NONE = figures_of({})


def ca(starts_on, table, zip_code=LA_ZIP, now="1000.00", lowest="1000.00", new=None, lived=3):
    return rent_cap.cap(
        "CA",
        zip_code,
        starts_on,
        D(now),
        D(lowest),
        lived,
        figures_of(table),
        None if new is None else D(new),
    )


def test_california_counts_april_to_april_of_the_metro_areas_index():
    table = {LOS_ANGELES.series: {(2025, 4): "300.000", (2026, 4): "309.000"}}

    found = ca(date(2027, 3, 1), table, new="1100.00")

    assert (found.checked, found.max_percent, found.max_rent) == (True, D("8.0"), D("1080.00"))
    assert (found.index, found.increase_percent, found.over) == (LOS_ANGELES, D("10.0"), True)
    assert found.words == (
        "California caps a year's rent increase at 5% plus the change in the cost of living, or"
        " 10%, whichever is lower, over the lowest rent charged in the 12 months before"
        " (California Civil Code §1947.12). For a new rent from 03/01/2027: the Los Angeles-Long"
        " Beach-Anaheim CPI-U went from 300.000 in April 2025 to 309.000 in April 2026, a change"
        " of 3.0%. So the most is 8.0% over $1,000.00, the lowest rent charged in the 12 months"
        " before: $1,080.00 a month. This new rent, $1,100.00, is 10.0% over it: more than that."
        " Some homes are exempt (a home first occupied in the last 15 years, a single-family home"
        " or condo owned by a person who gave the tenants the required notice, an owner-occupied"
        " duplex, or deed-restricted affordable housing, and a city's own rent control may allow"
        " less); the PMS can't tell which. A warning, not legal advice."
    )
    within = ca(date(2027, 3, 1), table, new="1080.00")
    assert (within.over, within.words.count("within it")) == (False, 1)


def test_california_moves_to_this_aprils_index_from_august_first():
    table = {
        LOS_ANGELES.series: {
            (2024, 4): "290.000",
            (2025, 4): "300.000",
            (2026, 4): "306.000",
        }
    }

    # Before August 1 of 2026: April 2024 to April 2025, 3.4%; from it, April 2025 to 2026, 2.0%.
    assert ca(date(2026, 7, 1), table).max_percent == D("8.4")
    assert ca(date(2026, 8, 1), table).max_percent == D("7.0")
    assert ca(date(2027, 7, 31), table).max_percent == D("7.0")


def test_san_diegos_index_has_no_april_so_california_counts_march():
    table = {SAN_DIEGO.series: {(2025, 3): "300.000", (2026, 3): "312.000"}}

    found = ca(date(2027, 1, 1), table, zip_code=SD_ZIP)

    assert (found.index, found.max_percent) == (SAN_DIEGO, D("9.0"))
    assert "in March 2025 to 312.000 in March 2026, a change of 4.0%" in found.words


def test_the_change_rounds_half_up_to_a_tenth_and_the_cap_never_passes_ten():
    half = {LOS_ANGELES.series: {(2025, 4): "100.000", (2026, 4): "102.950"}}
    high = {LOS_ANGELES.series: {(2025, 4): "100.000", (2026, 4): "106.000"}}
    fell = {LOS_ANGELES.series: {(2025, 4): "100.000", (2026, 4): "93.000"}}

    assert ca(date(2027, 1, 1), half).max_percent == D("8.0")  # 2.95% reads 3.0%
    assert ca(date(2027, 1, 1), high).max_percent == D("10")
    # An index that fell further than the 5% allows no increase, never less than none.
    assert ca(date(2027, 1, 1), fell).max_percent == D("0")
    assert ca(date(2027, 1, 1), fell).max_rent == D("1000.00")


def test_california_counts_from_the_lowest_rent_in_the_twelve_months_before():
    table = {LOS_ANGELES.series: {(2025, 4): "300.000", (2026, 4): "309.000"}}

    found = ca(date(2027, 3, 1), table, now="1000.00", lowest="950.00", new="1030.00")

    assert (found.base_rent, found.max_rent) == (D("950.00"), D("1026.00"))
    assert (found.increase_percent, found.over) == (D("8.4"), True)


def test_a_zip_code_the_pms_cant_place_says_so_and_gives_the_ten_percent():
    found = ca(date(2027, 3, 1), {}, zip_code=OTHER_CA_ZIP, new="1050.00")

    assert (found.checked, found.index, found.max_percent, found.max_rent) == (
        False,
        None,
        D("10"),
        D("1100.00"),
    )
    assert (
        "The PMS couldn't work out this year's figure: it can't tell which BLS index applies"
        " at ZIP code 95800 (California counts its metro area's, else the state's own index, which"
        " the PMS doesn't fetch). Whatever it is, the most is 10% over $1,000.00"
    ) in found.words
    assert "no cap" not in found.words


def test_no_figures_on_file_says_it_couldnt_check_never_no_cap():
    found = ca(date(2027, 3, 1), {}, zip_code=SF_ZIP, new="1200.00")

    assert (found.checked, found.max_percent, found.over) == (False, D("10"), True)
    assert (
        "it has no San Francisco-Oakland-Hayward CPI-U for April 2025 and April 2026 on file"
        " (the server fetches the figures each night when whoever runs it sets PMS_CPI)"
    ) in found.words


def test_one_month_missing_is_as_good_as_none():
    table = {LOS_ANGELES.series: {(2026, 4): "309.000"}}

    assert ca(date(2027, 3, 1), table).checked is False


def test_after_californias_cap_was_set_to_end_it_cant_check():
    table = {LOS_ANGELES.series: {(2028, 4): "300.000", (2029, 4): "309.000"}}

    found = ca(date(2030, 1, 1), table)

    assert (found.checked, found.max_percent) == (False, D("10"))
    assert "California's cap was set to end on 01/01/2030" in found.words
    assert ca(date(2029, 12, 1), table).checked is True


def oregon(table, starts_on=date(2027, 5, 1), now="1000.00", lowest="1000.00", lived=3, new=None):
    return rent_cap.cap(
        "OR",
        "97000",
        starts_on,
        D(now),
        D(lowest),
        lived,
        figures_of(table),
        None if new is None else D(new),
    )


def west(prior: str, latest: str):
    """The West Region index, one level through September 2025 and another from October 2025."""
    months = {(2024, m): prior for m in range(10, 13)} | {(2025, m): prior for m in range(1, 10)}
    months |= {(2025, m): latest for m in range(10, 13)} | {(2026, m): latest for m in range(1, 10)}
    return {WEST.series: months}


def test_oregon_counts_the_september_twelve_month_average_of_the_west_region():
    found = oregon(west("300.000", "306.000"), new="1095.00")

    assert (found.checked, found.index, found.max_percent) == (True, WEST, D("9.0"))
    assert (found.base_rent, found.max_rent, found.over) == (D("1000.00"), D("1090.00"), True)
    assert (
        "the West Region CPI-U averaged 306.000 over the 12 months through September 2026,"
        " against 300.000 over the 12 before, a change of 2.0%; Oregon's DAS publishes the figure"
        " for 2027, which governs, so compare it"
    ) in found.words
    assert "the rent now, $1,000.00" in found.words
    assert oregon(west("300.000", "312.000")).max_percent == D("10")


def test_oregon_says_no_increase_in_a_tenancys_first_year_or_twice_in_twelve_months():
    table = west("300.000", "306.000")

    assert "Oregon allows no increase in a tenancy's first year" in oregon(table, lived=0).words
    assert "first year" not in oregon(table, lived=1).words
    twice = oregon(table, now="1050.00", lowest="1000.00")
    assert "Oregon allows one increase in 12 months" in twice.words
    # Oregon counts from the rent now, not the lowest.
    assert twice.base_rent == D("1050.00")


def test_oregon_without_a_month_of_its_two_years_cant_check():
    table = west("300.000", "306.000")
    del table[WEST.series][(2025, 3)]

    found = oregon(table)

    assert (found.checked, found.max_percent) == (False, D("10"))
    assert "each month from October 2024 through September 2026" in found.words


def washington(table, lived=3, now="1000.00", lowest="1000.00", starts_on=date(2027, 1, 1)):
    return rent_cap.cap(
        "WA", "98000", starts_on, D(now), D(lowest), lived, figures_of(table), D("1100.00")
    )


def test_washington_counts_junes_change_in_seattles_index_to_three_places():
    table = {SEATTLE.series: {(2025, 6): "300.000", (2026, 6): "308.049"}}

    found = washington(table, lowest="990.00")

    assert (found.checked, found.index, found.max_percent) == (True, SEATTLE, D("9.683"))
    assert (found.base_rent, found.max_rent) == (D("990.00"), D("1085.86"))
    assert "a change of 2.683%; Commerce publishes the figure for 2027" in found.words
    assert "first 12 months" not in found.words
    assert "Washington allows no increase in a tenancy's first 12 months" in (
        washington(table, lived=0).words
    )


def test_washingtons_cap_was_set_to_end_in_2040():
    table = {SEATTLE.series: {(2039, 6): "300.000", (2040, 6): "306.000"}}

    found = washington(table, starts_on=date(2041, 1, 1))

    assert found.checked is False
    assert "Washington's cap was set to end on 07/01/2040" in found.words


@pytest.mark.parametrize("state", ["NV", "NY", "TX", "WV"])
def test_a_state_with_no_inflation_cap_recorded_gets_none(state):
    assert rent_cap.cap(state, "00001", date(2027, 1, 1), D(1000), D(1000), 3, NONE) is None


def test_no_new_rent_asked_says_only_the_most():
    found = ca(date(2027, 3, 1), {LOS_ANGELES.series: {(2025, 4): "300", (2026, 4): "309"}})

    assert (found.increase_percent, found.over) == (None, False)
    assert "This new rent" not in found.words


def test_every_california_prefix_names_one_of_its_four_metro_areas():
    assert set(rent_cap.CALIFORNIA_AREAS.values()) == {
        rent_cap.LOS_ANGELES,
        rent_cap.SAN_FRANCISCO,
        rent_cap.RIVERSIDE,
        rent_cap.SAN_DIEGO,
    }
    assert all(len(p) == 3 and p.isdigit() for p in rent_cap.CALIFORNIA_AREAS)
    assert rent_cap.california_index(" 90000-0000") == LOS_ANGELES
    assert rent_cap.california_index("95800") is None
    assert set(rent_cap.SERIES) == {index.series for index in rent_cap.INDEXES}


# --- the words alone, for software with no books to read (rent_cap.limit_words) ------------------


def test_the_words_alone_with_no_figures_still_name_the_law_the_ceiling_and_the_exemptions():
    said = rent_cap.limit_words("CA", LA_ZIP, date(2027, 3, 1), D("1000.00"), D("1150.00"))

    assert said == (
        "California caps a year's rent increase at 5% plus the change in the cost of living, or"
        " 10%, whichever is lower, over the lowest rent charged in the 12 months before"
        " (California Civil Code §1947.12). The PMS couldn't work out this year's figure: it has"
        " no Consumer Price Index figures to work from. Whatever it is, the most is 10% over"
        " $1,000.00, the lowest rent charged in the 12 months before: $1,100.00 a month. This new"
        " rent, $1,150.00, is 15.0% over it: more than that. Some homes are exempt (a home first"
        " occupied in the last 15 years, a single-family home or condo owned by a person who gave"
        " the tenants the required notice, an owner-occupied duplex, or deed-restricted"
        " affordable housing, and a city's own rent control may allow less); the PMS can't tell"
        " which. A warning, not legal advice."
    )


def test_the_words_alone_without_a_rent_say_the_most_in_percent():
    said = rent_cap.limit_words("WA", "98000", date(2027, 3, 1), None, None)

    assert (
        "Whatever it is, the most is 10% over the lowest rent charged in the 12 months before."
        in said
    )
    assert "$" not in said and "This new rent" not in said
    # Not knowing how long the tenants lived there, the first-year rule is said as a rule.
    assert "Washington allows no increase in a tenancy's first 12 months." in said
    oregon_said = rent_cap.limit_words("OR", "97000", date(2027, 3, 1), D("1000.00"), None)
    assert "the most is 10% over the rent now, $1,000.00: $1,100.00 a month." in oregon_said
    assert "a tenancy's first year (ORS 90.323), and one in 12 months after it." in oregon_said


def test_the_words_alone_say_when_a_state_has_no_cap_or_a_zip_code_no_index():
    assert rent_cap.limit_words("TX", "75000", date(2027, 3, 1), D(1000), D(1200)) == (
        "The PMS records no cap on a year's rent increase in TX: it checks only California's,"
        " Oregon's and Washington's, and no city's own rules. Not legal advice."
    )
    assert rent_cap.limit_words("NY", "10000", date(2027, 3, 1), None, None).startswith(
        "The PMS records no cap on a year's rent increase in New York"
    )
    said = rent_cap.limit_words("CA", OTHER_CA_ZIP, date(2027, 3, 1), D(1000), None)
    assert "it can't tell which BLS index applies at ZIP code 95800" in said


def test_the_words_alone_are_the_warnings_given_the_same_figures():
    table = {LOS_ANGELES.series: {(2025, 4): "300.000", (2026, 4): "309.000"}}
    found = ca(date(2027, 3, 1), table, now="1000.00", lowest="950.00", new="1030.00", lived=0)

    assert found.words == rent_cap.limit_words(
        "CA",
        LA_ZIP,
        date(2027, 3, 1),
        D("950.00"),
        D("1030.00"),
        figures_of(table),
        rent_now=D("1000.00"),
        years_lived=0,
    )
