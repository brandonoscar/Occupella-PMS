"""The written notice a state asks before a rent increase (src/pms/rent_notice.py; ADR 0010 phase
3, part L): only the rules docs/law.md records, nothing for a state without one."""

from datetime import date
from decimal import Decimal

import pytest

from pms import rent_notice
from pms.rent_notice import Needed, needed, warning


def d(amount: str) -> Decimal:
    return Decimal(amount)


def test_no_increase_asks_no_notice():
    assert needed("CA", d("1500"), d("1500"), d("1500"), 3) is None
    assert needed("OR", d("1500"), d("1500"), d("1400"), 3) is None


def test_a_state_without_a_recorded_rule_asks_none():
    for state in ("TX", "CO", "WV", "FL"):
        assert needed(state, d("1000"), d("1000"), d("2000"), 5) is None


def test_california_asks_30_days_up_to_ten_percent_and_90_over_it():
    assert needed("CA", d("1000"), d("1000"), d("1100"), 1) == Needed(
        "CA", 30, "California Civil Code §827", d("10.0")
    )
    assert needed("CA", d("1000"), d("1000"), d("1100.01"), 1) == Needed(
        "CA", 90, "California Civil Code §827", d("10.0")
    )
    assert needed("CA", d("1000"), d("1000"), d("1150"), 1).days == 90


def test_california_counts_from_the_lowest_rent_in_the_twelve_months():
    # 1,050 to 1,100 is under 5%, but the rent was 980 earlier in the year: 12.2% over it.
    rule = needed("CA", d("1050"), d("980"), d("1100"), 1)
    assert (rule.days, rule.increase_percent) == (90, d("12.2"))


@pytest.mark.parametrize(
    ("state", "days", "law"),
    [("OR", 90, "ORS 90.323"), ("WA", 90, "RCW 59.18.140"), ("NV", 60, "NRS 118A.300")],
)
def test_oregon_washington_and_nevada_ask_the_same_days_for_any_increase(state, days, law):
    assert needed(state, d("1000"), d("1000"), d("1001"), 0) == Needed(state, days, law, d("0.1"))


def test_new_york_asks_nothing_under_five_percent():
    assert needed("NY", d("1000"), d("1000"), d("1049.99"), 3) is None


@pytest.mark.parametrize(("years", "days"), [(0, 30), (1, 60), (2, 90), (7, 90)])
def test_new_york_asks_more_the_longer_the_tenant_lived_there(years, days):
    rule = needed("NY", d("1000"), d("1000"), d("1050"), years)
    assert (rule.days, rule.law) == (days, "New York Real Property Law §226-c")


def test_the_warning_says_the_rule_and_the_first_day_that_gives_it():
    rule = Needed("CA", 90, "California Civil Code §827", d("12.0"))
    sent = date(2026, 10, 25)

    said = warning(rule, sent, date(2026, 11, 1))

    assert said == (
        "California asks 90 days' written notice before a rent increase of 12.0% (California"
        " Civil Code §827). This new rent starts in 7 days. To give that notice, start it on or"
        " after 01/23/2027."
    )
    assert rent_notice.earliest(sent, rule) == date(2027, 1, 23)


def test_no_warning_once_the_new_rent_starts_late_enough():
    rule = Needed("OR", 90, "ORS 90.323", d("5.0"))
    sent = date(2026, 10, 25)

    assert warning(rule, sent, date(2027, 1, 23)) is None
    assert warning(rule, sent, date(2027, 1, 22)) is not None
    nevada = Needed("NV", 60, "NRS 118A.300", d("1.0"))
    assert "in 1 day." in warning(nevada, sent, date(2026, 10, 26))
