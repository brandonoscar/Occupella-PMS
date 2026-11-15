"""A year's rent increase capped by inflation (docs/law.md, "A year's rent increase capped by
inflation"): where a state caps a year's increase at a fixed percent plus the change in a
Consumer Price Index, or 10%, whichever is lower, the warning says the most this increase may be
and how it was worked out. A warning, never a refusal (D-065), as every state rule here is.

Only the rules docs/law.md records are here, as it records them (each NOT VERIFIED against the
statute's own text; the session that wrote them could reach no statute site):

- California, Civil Code §1947.12 (AB 1482): 5% plus the April-to-April change in the BLS
  regional CPI-U of the property's metro area, rounded to 0.1%, or 10%, over the lowest rent
  charged in the 12 months before the increase. An increase taking effect before August 1 of a
  year counts the April two years before to the April before; on or after it, the April before
  to that April. San Diego's index has no April, so March. Ends January 1, 2030.
- Oregon, ORS 90.323 and 90.324: 7% plus the September 12-month average change in the West
  Region CPI-U (Oregon's DAS publishes the figure for each calendar year by September 30 of the
  year before), or 10%, over the rent now; once in 12 months, none in a tenancy's first year.
- Washington, HB 1217 (2025), RCW 59.18: 7% plus the June 12-month change in the
  Seattle-Tacoma-Bellevue CPI-U (Commerce publishes each calendar year's), or 10%, over the
  lowest rent in the 12 months before; none in a tenancy's first 12 months. Ends July 1, 2040.

The index figures come from the company's books (src/pms/cpi.py, migration 20261115000100), all
of one fetch so a series rebased between fetches never mixes bases. Without them, or without an
index for the property's ZIP code, the warning says it couldn't check and still gives the 10% it
never exceeds: unknown is never "no cap". Some homes are exempt (new construction, some homes
owned by a person, affordable housing...) and a city's own rent control may allow less; the PMS
can't tell which, and the warning says so.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

# A month, as (year, month).
Month = tuple[int, int]
# The company's figures of a series for the months asked, all from one fetch (cpi.on_file): only
# the months it has.
Figures = Callable[[str, Sequence[Month]], Mapping[Month, Decimal]]

CENT = Decimal("0.01")
HUNDRED = Decimal(100)
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


@dataclass(frozen=True)
class Index:
    """A BLS Consumer Price Index for All Urban Consumers, all items, not seasonally adjusted."""

    series: str
    name: str


LOS_ANGELES = Index("CUURS49ASA0", "Los Angeles-Long Beach-Anaheim")
SAN_FRANCISCO = Index("CUURS49BSA0", "San Francisco-Oakland-Hayward")
RIVERSIDE = Index("CUURS49CSA0", "Riverside-San Bernardino-Ontario")
SAN_DIEGO = Index("CUURS49ESA0", "San Diego-Carlsbad")
SEATTLE = Index("CUURS49DSA0", "Seattle-Tacoma-Bellevue")
WEST = Index("CUUR0400SA0", "West Region")
INDEXES = (LOS_ANGELES, SAN_FRANCISCO, RIVERSIDE, SAN_DIEGO, SEATTLE, WEST)
# Every series the nightly run fetches, in one request (src/pms/cpi.py).
SERIES = tuple(index.series for index in INDEXES)

# California's ZIP code prefixes every ZIP code of which lies in one BLS metro area: the PMS's
# own reading of USPS's prefixes (NOT VERIFIED; docs/law.md). Los Angeles and Orange counties
# are both the Los Angeles area. Any other California ZIP code: the PMS can't tell which index.
CALIFORNIA_AREAS: dict[str, Index] = {
    **{
        prefix: LOS_ANGELES
        for prefix in (
            "900",
            "901",
            "902",
            "903",
            "904",
            "905",
            "906",
            "907",
            "908",
            "910",
            "911",
            "912",
            "914",
            "915",
            "916",
            "918",
            "926",
            "927",
            "928",
        )
    },
    **{prefix: SAN_DIEGO for prefix in ("919", "920", "921")},
    "925": RIVERSIDE,
    **{prefix: SAN_FRANCISCO for prefix in ("941", "944", "946", "947", "948")},
}
# The month California counts April to April in, but for an index BLS publishes no April of.
CALIFORNIA_MONTH = {SAN_DIEGO: 3}


@dataclass(frozen=True)
class Rule:
    state: str
    name: str
    law: str
    # The fixed percent the change in the index is added to, and the most it ever comes to.
    base: Decimal
    ceiling: Decimal
    # How the change in the index is rounded.
    places: Decimal
    # What the cap counts from: the lowest rent charged in the 12 months before, or the rent now.
    from_lowest: bool
    # The day the law was set to end, if any.
    ends: date | None
    # Who is exempt, in words, as docs/law.md records it.
    exempt: str
    says: str


RULES = {
    "CA": Rule(
        "CA",
        "California",
        "California Civil Code §1947.12",
        Decimal(5),
        Decimal(10),
        Decimal("0.1"),
        True,
        date(2030, 1, 1),
        "a home first occupied in the last 15 years, a single-family home or condo owned by a"
        " person who gave the tenants the required notice, an owner-occupied duplex, or"
        " deed-restricted affordable housing, and a city's own rent control may allow less",
        "5% plus the change in the cost of living, or 10%, whichever is lower, over the lowest"
        " rent charged in the 12 months before",
    ),
    "OR": Rule(
        "OR",
        "Oregon",
        "ORS 90.323 and 90.324",
        Decimal(7),
        Decimal(10),
        Decimal("0.1"),
        False,
        None,
        "a home first occupied in the last 15 years, or some regulated affordable housing",
        "7% plus inflation, or 10%, whichever is lower, once in 12 months",
    ),
    "WA": Rule(
        "WA",
        "Washington",
        "RCW 59.18, as HB 1217 of 2025 amended it",
        Decimal(7),
        Decimal(10),
        Decimal("0.001"),
        True,
        date(2040, 7, 1),
        "a home first occupied in the last 12 years, public, nonprofit or tax credit housing, or"
        " some homes the owner lives in",
        "7% plus inflation, or 10%, whichever is lower, in any 12 months",
    ),
}
STATES = frozenset(RULES)
# The names of the states a rent rule here names (src/pms/rent_notice.py's too).
STATE_NAMES = {
    "CA": "California",
    "OR": "Oregon",
    "WA": "Washington",
    "NV": "Nevada",
    "NY": "New York",
}


@dataclass(frozen=True)
class Worked:
    """This year's change in the index, and how it was worked out, in words."""

    index: Index
    change: Decimal
    how: str


@dataclass(frozen=True)
class Cap:
    state: str
    law: str
    # Whether this year's figure was worked out; False says why in `words`, and the most is then
    # the ceiling, which the cap never exceeds.
    checked: bool
    max_percent: Decimal
    max_rent: Decimal
    # The rent the cap counts from (the lowest in the 12 months before, or the rent now).
    base_rent: Decimal
    # The index used, when the PMS could tell which.
    index: Index | None
    # The new rent over the base, in percent to the cap's places, rounded down; None when no new
    # rent was asked about.
    increase_percent: Decimal | None
    # The new rent is more than the most.
    over: bool
    words: str


def month_name(month: Month) -> str:
    return f"{MONTHS[month[1] - 1]} {month[0]}"


def dollars(amount: Decimal) -> str:
    return f"${amount:,.2f}"


def percent(value: Decimal) -> str:
    """A percent as said: no trailing zeros past its own places ("8.0", "9.683", "10")."""
    return format(value, "f")


def change(new: Decimal, old: Decimal, places: Decimal) -> Decimal:
    """The percent change from `old` to `new`, rounded half up to `places`."""
    return ((new / old - 1) * HUNDRED).quantize(places, rounding=ROUND_HALF_UP)


def most(rule: Rule, worked: Decimal) -> Decimal:
    """The most a year's increase may be: the base plus the change, never past the ceiling, and
    never below nothing (an index that fell further than the base allows no increase)."""
    return max(Decimal(0), min(rule.base + worked, rule.ceiling))


def allowed_rent(base_rent: Decimal, at_most: Decimal) -> Decimal:
    """The rent `at_most` percent over `base_rent`, rounded down to the cent: never past it."""
    return (base_rent * (HUNDRED + at_most) / HUNDRED).quantize(CENT, rounding=ROUND_DOWN)


def california_index(zip_code: str) -> Index | None:
    return CALIFORNIA_AREAS.get(zip_code.strip()[:3])


def california_months(index: Index, starts_on: date) -> tuple[Month, Month]:
    """The two months California compares for an increase from `starts_on`."""
    month = CALIFORNIA_MONTH.get(index, 4)
    to_year = starts_on.year if starts_on >= date(starts_on.year, 8, 1) else starts_on.year - 1
    return (to_year - 1, month), (to_year, month)


def oregon_months(year: int) -> tuple[list[Month], list[Month]]:
    """The 12 months through September of the year before `year`, and the 12 before them."""
    latest = [(year - 2, m) for m in range(10, 13)] + [(year - 1, m) for m in range(1, 10)]
    prior = [(y - 1, m) for y, m in latest]
    return prior, latest


def washington_months(year: int) -> tuple[Month, Month]:
    """June two years before `year`, and June of the year before."""
    return (year - 2, 6), (year - 1, 6)


def missing(index: Index, first: Month, last: Month, every_month: bool = False) -> str:
    named = (
        f"each month from {month_name(first)} through {month_name(last)}"
        if every_month
        else f"{month_name(first)} and {month_name(last)}"
    )
    return (
        f"it has no {index.name} CPI-U for {named} on file (the server fetches the figures each"
        " night when whoever runs it sets PMS_CPI)"
    )


def work_out(
    rule: Rule, zip_code: str, starts_on: date, figures: Figures
) -> tuple[Worked | None, Index | None, str]:
    """This year's change in the rule's index, or None and why not, in words."""
    if rule.state == "CA":
        index = california_index(zip_code)
        if index is None:
            return (
                None,
                None,
                f"it can't tell which BLS index applies at ZIP code {zip_code.strip()}"
                " (California counts its metro area's, else the state's own index, which the PMS"
                " doesn't fetch)",
            )
        first, last = california_months(index, starts_on)
        got = figures(index.series, (first, last))
        if first not in got or last not in got:
            return None, index, missing(index, first, last)
        worked = change(got[last], got[first], rule.places)
        how = (
            f"the {index.name} CPI-U went from {got[first]} in {month_name(first)} to"
            f" {got[last]} in {month_name(last)}, a change of {percent(worked)}%"
        )
        return Worked(index, worked, how), index, ""
    if rule.state == "OR":
        index = WEST
        prior, latest = oregon_months(starts_on.year)
        got = figures(index.series, (*prior, *latest))
        if any(m not in got for m in (*prior, *latest)):
            return None, index, missing(index, prior[0], latest[-1], every_month=True)
        before = sum((got[m] for m in prior), Decimal(0)) / 12
        after = sum((got[m] for m in latest), Decimal(0)) / 12
        worked = change(after, before, rule.places)
        how = (
            f"the West Region CPI-U averaged {after.quantize(Decimal('0.001'))} over the 12 months"
            f" through {month_name(latest[-1])}, against {before.quantize(Decimal('0.001'))} over"
            f" the 12 before, a change of {percent(worked)}%; Oregon's DAS publishes the figure"
            f" for {starts_on.year}, which governs, so compare it"
        )
        return Worked(index, worked, how), index, ""
    index = SEATTLE
    first, last = washington_months(starts_on.year)
    got = figures(index.series, (first, last))
    if first not in got or last not in got:
        return None, index, missing(index, first, last)
    worked = change(got[last], got[first], rule.places)
    how = (
        f"the {index.name} CPI-U went from {got[first]} in {month_name(first)} to {got[last]} in"
        f" {month_name(last)}, a change of {percent(worked)}%; Commerce publishes the figure for"
        f" {starts_on.year}, which governs, so compare it"
    )
    return Worked(index, worked, how), index, ""


# The reason a cap couldn't be worked out without any figures to work from (the chat's own tool,
# before it reads a company's books).
NO_FIGURES = "it has no Consumer Price Index figures to work from"


def figure_for(
    rule: Rule, zip_code: str, starts_on: date, figures: Figures | None
) -> tuple[Worked | None, Index | None, str]:
    """This year's change, or None and why not: after the day the law was set to end, without
    figures, or as `work_out` finds it."""
    if rule.ends is not None and starts_on >= rule.ends:
        return (
            None,
            None,
            f"{rule.name}'s cap was set to end on {rule.ends:%m/%d/%Y}, and the PMS can't tell"
            " whether it was extended",
        )
    if figures is None:
        index = california_index(zip_code) if rule.state == "CA" else None
        if rule.state == "CA" and index is None:
            return work_out(rule, zip_code, starts_on, lambda series, months: {})
        return None, index or (WEST if rule.state == "OR" else SEATTLE), NO_FIGURES
    return work_out(rule, zip_code, starts_on, figures)


def increase_over(rule: Rule, new_rent: Decimal, base_rent: Decimal) -> Decimal:
    """The new rent over the base, in percent to the cap's places, rounded down."""
    return ((new_rent - base_rent) * HUNDRED / base_rent).quantize(rule.places, rounding=ROUND_DOWN)


def say(
    rule: Rule,
    starts_on: date,
    worked: Worked | None,
    why: str,
    base_rent: Decimal | None,
    new_rent: Decimal | None,
    years_lived: int | None,
    changed_in_the_year: bool,
) -> str:
    """The warning's words. Without a base rent the most is said in percent alone; without how
    long the tenants have lived there, a first-year rule is said as the rule it is."""
    at_most = rule.ceiling if worked is None else most(rule, worked.change)
    if base_rent is None:
        counted_from = (
            "the lowest rent charged in the 12 months before"
            if rule.from_lowest
            else "the rent now"
        )
        allowed = "."
    else:
        counted_from = (
            f"{dollars(base_rent)}, the lowest rent charged in the 12 months before"
            if rule.from_lowest
            else f"the rent now, {dollars(base_rent)}"
        )
        allowed = f": {dollars(allowed_rent(base_rent, at_most))} a month."
    words = [f"{rule.name} caps a year's rent increase at {rule.says} ({rule.law})."]
    if worked is None:
        words.append(
            f"The PMS couldn't work out this year's figure: {why}. Whatever it is, the most is"
            f" {percent(rule.ceiling)}% over {counted_from}{allowed}"
        )
    else:
        words.append(
            f"For a new rent from {starts_on:%m/%d/%Y}: {worked.how}. So the most is"
            f" {percent(at_most)}% over {counted_from}{allowed}"
        )
    if new_rent is not None and base_rent is not None and base_rent > 0:
        over = new_rent > allowed_rent(base_rent, at_most)
        increase = percent(increase_over(rule, new_rent, base_rent))
        words.append(
            f"This new rent, {dollars(new_rent)}, is {increase}% over it:"
            f" {'more than that' if over else 'within it'}."
        )
    first_year = years_lived is None or years_lived < 1
    if rule.state == "OR" and first_year:
        words.append(
            "Oregon allows no increase in a tenancy's first year (ORS 90.323)."
            if years_lived is not None
            else "Oregon allows no increase in a tenancy's first year (ORS 90.323), and one in"
            " 12 months after it."
        )
    if rule.state == "OR" and changed_in_the_year:
        words.append(
            "Oregon allows one increase in 12 months, and this rent changed in the 12 months"
            " before."
        )
    if rule.state == "WA" and first_year:
        words.append("Washington allows no increase in a tenancy's first 12 months.")
    words.append(
        f"Some homes are exempt ({rule.exempt}); the PMS can't tell which. A warning, not legal"
        " advice."
    )
    return " ".join(words)


def no_cap_words(state: str) -> str:
    """What is said in a state with no inflation cap recorded."""
    named = STATE_NAMES.get(state, state)
    return (
        f"The PMS records no cap on a year's rent increase in {named}: it checks only"
        " California's, Oregon's and Washington's, and no city's own rules. Not legal advice."
    )


def limit_words(
    state: str,
    zip_code: str,
    starts_on: date,
    lowest: Decimal | None,
    new_rent: Decimal | None,
    figures: Figures | None = None,
    rent_now: Decimal | None = None,
    years_lived: int | None = None,
) -> str:
    """The words the PMS's warning gives for a rent increase from `starts_on`, pure: no database.

    For any state (one with no cap recorded says so). `lowest` is the lowest rent charged in the
    12 months before (California and Washington count from it); Oregon counts from the rent now,
    `rent_now`, else `lowest` stands for it. Either None: the most is said in percent alone.
    `figures` None: the PMS couldn't work out this year's figure, and says so, the statute, the 10%
    the cap never exceeds, the exemptions it can't tell and that it is no legal advice."""
    rule = RULES.get(state)
    if rule is None:
        return no_cap_words(state)
    worked, _, why = figure_for(rule, zip_code, starts_on, figures)
    known = [r for r in (lowest, rent_now) if r is not None]
    if rule.from_lowest:
        base = min(known) if known else None
    else:
        base = rent_now if rent_now is not None else lowest
    changed = lowest is not None and rent_now is not None and lowest < rent_now
    return say(rule, starts_on, worked, why, base, new_rent, years_lived, changed)


def cap(
    state: str,
    zip_code: str,
    starts_on: date,
    rent_now: Decimal,
    lowest: Decimal,
    years_lived: int,
    figures: Figures,
    new_rent: Decimal | None = None,
) -> Cap | None:
    """The most a year's rent increase from `starts_on` may be where the property's state caps
    it by inflation, and how it was worked out, in words (`limit_words`'s); None in a state with
    no cap recorded.

    `lowest` is the lowest rent charged in the 12 months before `starts_on`; `years_lived` how
    long the tenants have lived there, in whole years; `new_rent` the rent asked about, if any."""
    rule = RULES.get(state)
    if rule is None:
        return None
    base_rent = min(lowest, rent_now) if rule.from_lowest else rent_now
    worked, index, why = figure_for(rule, zip_code, starts_on, figures)
    at_most = rule.ceiling if worked is None else most(rule, worked.change)
    max_rent = allowed_rent(base_rent, at_most)
    increase = None
    over = False
    if new_rent is not None and base_rent > 0:
        increase = increase_over(rule, new_rent, base_rent)
        over = new_rent > max_rent
    return Cap(
        state=rule.state,
        law=rule.law,
        checked=worked is not None,
        max_percent=at_most,
        max_rent=max_rent,
        base_rent=base_rent,
        index=index,
        increase_percent=increase,
        over=over,
        words=say(
            rule, starts_on, worked, why, base_rent, new_rent, years_lived, lowest < rent_now
        ),
    )
