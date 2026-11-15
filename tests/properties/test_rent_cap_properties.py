"""Property-based tests for the arithmetic of a rent cap (src/pms/rent_cap.py; docs/law.md, "A
year's rent increase capped by inflation").

Hypothesis gives a state of the three (California, Oregon, Washington), a day the new rent starts,
the rent now and the lowest in the 12 months before, how long the tenants have lived there, a
new rent, and the index figures the rule compares (or none on file). Then (ci/registry.toml,
a-rent-cap-never-reads-as-no-cap):

  - the most is never above the state's 10% nor below nothing, and while the index didn't fall it
    is at least the state's fixed percent (5% or 7%): the floor without inflation;
  - worked out, it is the fixed percent plus the index's change, rounded half up to the rule's
    places, as a model works it out here, never past 10%;
  - the rent it allows is the base rent that much higher, rounded down to the cent: never past
    the percent, and less than a cent under it;
  - a higher index never lowers the most;
  - the new rent is over the cap exactly when it is above the rent allowed;
  - with no figures on file it says it couldn't, and the most is the 10% the cap never exceeds.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from hypothesis import event, given
from hypothesis import strategies as st

from pms import rent_cap

D = Decimal
ZIPS = {"CA": "90000", "OR": "97000", "WA": "98000"}
STATES = st.sampled_from(sorted(ZIPS))
DAYS = st.dates(min_value=date(2021, 1, 1), max_value=date(2029, 12, 1))
RENTS = st.decimals(min_value=D("100.00"), max_value=D("20000.00"), places=2)
INDEX = st.decimals(min_value=D("50.000"), max_value=D("600.000"), places=3)


def months_for(state, starts_on):
    """The months the rule compares, as (before, after) lists, and its index."""
    if state == "CA":
        first, last = rent_cap.california_months(rent_cap.LOS_ANGELES, starts_on)
        return [first], [last], rent_cap.LOS_ANGELES
    if state == "OR":
        prior, latest = rent_cap.oregon_months(starts_on.year)
        return prior, latest, rent_cap.WEST
    first, last = rent_cap.washington_months(starts_on.year)
    return [first], [last], rent_cap.SEATTLE


def figures_with(before_months, after_months, series, before, after):
    held = {m: before for m in before_months} | {m: after for m in after_months}

    def figures(asked_series, months):
        if asked_series != series:
            return {}
        return {m: held[m] for m in months if m in held}

    return figures


def model_most(rule, before, after):
    """The fixed percent plus the change, rounded half up to the rule's places, within 0 and 10."""
    change = ((after / before - 1) * 100).quantize(rule.places, rounding=ROUND_HALF_UP)
    return max(D(0), min(rule.base + change, rule.ceiling))


@given(
    state=STATES,
    starts_on=DAYS,
    now=RENTS,
    lower_by=st.decimals(min_value=D("0.00"), max_value=D("90.00"), places=2),
    lived=st.integers(min_value=0, max_value=10),
    before=INDEX,
    after=INDEX,
    new=RENTS,
)
def test_the_most_is_the_fixed_percent_plus_the_change_within_ten(
    state, starts_on, now, lower_by, lived, before, after, new
):
    rule = rent_cap.RULES[state]
    lowest = now - lower_by
    before_months, after_months, index = months_for(state, starts_on)
    figures = figures_with(before_months, after_months, index.series, before, after)

    found = rent_cap.cap(state, ZIPS[state], starts_on, now, lowest, lived, figures, new)

    assert found is not None
    assert D(0) <= found.max_percent <= rule.ceiling
    ended = rule.ends is not None and starts_on >= rule.ends
    assert found.checked is not ended
    if found.checked:
        event("worked out")
        assert found.max_percent == model_most(rule, before, after)
        if after >= before:
            assert found.max_percent >= min(rule.base, rule.ceiling)
    else:
        event("after the law was set to end")
        assert found.max_percent == rule.ceiling
    base = lowest if rule.from_lowest else now
    assert found.base_rent == base
    exact = base * (100 + found.max_percent) / 100
    assert found.max_rent <= exact < found.max_rent + D("0.01")
    assert found.max_rent == found.max_rent.quantize(D("0.01"))
    assert found.over is (new > found.max_rent)
    assert "A warning, not legal advice." in found.words


@given(
    state=STATES,
    starts_on=st.dates(min_value=date(2021, 1, 1), max_value=date(2029, 6, 1)),
    now=RENTS,
    before=INDEX,
    after=INDEX,
    more=st.decimals(min_value=D("0.000"), max_value=D("50.000"), places=3),
)
def test_a_higher_index_never_lowers_the_most(state, starts_on, now, before, after, more):
    before_months, after_months, index = months_for(state, starts_on)

    def most(latest):
        figures = figures_with(before_months, after_months, index.series, before, latest)
        found = rent_cap.cap(state, ZIPS[state], starts_on, now, now, 3, figures)
        assert found is not None
        return found.max_percent, found.max_rent

    assert most(after) <= most(after + more)


@given(state=STATES, starts_on=DAYS, now=RENTS, new=RENTS)
def test_no_figures_on_file_reads_as_couldnt_check_and_the_ten_percent(state, starts_on, now, new):
    found = rent_cap.cap(state, ZIPS[state], starts_on, now, now, 3, lambda s, m: {}, new)

    assert found is not None
    assert (found.checked, found.max_percent) == (False, rent_cap.RULES[state].ceiling)
    assert "The PMS couldn't work out this year's figure" in found.words
    assert found.max_rent == (now * D("1.1")).quantize(D("0.01"), rounding=ROUND_DOWN)
