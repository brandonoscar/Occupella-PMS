"""The written notice a state asks before a rent increase takes effect (ADR 0010 phase 3, part L):
the renewal panel warns before it sends a renewal whose new rent starts sooner than that.

Only the rules docs/law.md records ("A lease's rent changed from a month") are here, as it
records them; a state with none recorded gets no warning, and nothing is guessed:

- California, Civil Code 827: 30 days; 90 when the new rent is more than 10% over the lowest
  rent charged in the 12 months before it starts (that increase alone or with others).
- Oregon, ORS 90.323: 90 days.
- Washington, RCW 59.18.140: 90 days. Its 30 days for subsidized housing is not applied (the
  PMS can't tell such a tenancy), so a subsidized tenancy may be warned at 90.
- Nevada, NRS 118A.300: 60 days.
- New York, Real Property Law 226-c: for an increase of 5% or more, 30 days when the tenant has
  lived there (or holds a lease) less than a year, 60 for one to two years, 90 for two or more.

Left out, as docs/law.md records them: Colorado's 60 days (C.R.S. 38-12-701) applies only
without a written lease, and a renewal is one; West Virginia has no statute on increases. Rent
control and caps (California 1947.12, Oregon 90.324, Washington's) are not checked, nor Oregon's
no increase in a tenancy's first year.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

STATES = {
    "CA": "California",
    "OR": "Oregon",
    "WA": "Washington",
    "NV": "Nevada",
    "NY": "New York",
}


@dataclass(frozen=True)
class Needed:
    state: str
    days: int
    law: str
    # The increase the rule measures, in percent to one place, rounded down.
    increase_percent: Decimal


def percent_over(new: Decimal, old: Decimal) -> Decimal:
    """The increase in percent, exact: a rule compares its threshold with this, never a rounding."""
    return (new - old) * 100 / old


def shown(percent: Decimal) -> Decimal:
    """The increase as said, to one place, never rounded up past a threshold (4.99% reads 4.9%)."""
    return percent.quantize(Decimal("0.1"), rounding=ROUND_DOWN)


def needed(state: str, now: Decimal, lowest: Decimal, new: Decimal, years: int) -> Needed | None:
    """The notice a state's recorded rule asks before a new rent, or None: no increase over the
    rent now, no rule recorded for the state, or an increase the rule asks nothing of.

    `lowest` is the lowest rent charged in the 12 months before the new rent starts; `years` how
    long the tenants have lived there (or the lease's term, if longer), in whole years."""
    if new <= now or now <= 0:
        return None
    over_now = percent_over(new, now)
    if state == "CA":
        over_lowest = percent_over(new, min(lowest, now))
        days = 90 if over_lowest > 10 else 30
        return Needed(state, days, "California Civil Code §827", shown(over_lowest))
    if state == "OR":
        return Needed(state, 90, "ORS 90.323", shown(over_now))
    if state == "WA":
        return Needed(state, 90, "RCW 59.18.140", shown(over_now))
    if state == "NV":
        return Needed(state, 60, "NRS 118A.300", shown(over_now))
    if state == "NY":
        if over_now < 5:
            return None
        days = 30 if years < 1 else 60 if years < 2 else 90
        return Needed(state, days, "New York Real Property Law §226-c", shown(over_now))
    return None


def earliest(sent_on: date, rule: Needed) -> date:
    """The first day the new rent may start when the notice goes out on `sent_on`."""
    return sent_on + timedelta(days=rule.days)


def warning(rule: Needed, sent_on: date, starts_on: date) -> str | None:
    """What the renewal panel says before sending one whose new rent starts too soon, or None."""
    given = (starts_on - sent_on).days
    if given >= rule.days:
        return None
    first = earliest(sent_on, rule)
    return (
        f"{STATES[rule.state]} asks {rule.days} days' written notice before a rent increase of"
        f" {rule.increase_percent}% ({rule.law}). This new rent starts in {given} day"
        f"{'' if given == 1 else 's'}. To give that notice, start it on or after"
        f" {first:%m/%d/%Y}."
    )
