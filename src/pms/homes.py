"""A home's name as tenants, owners, vendors and applicants read it (migration 20261104000500;
the founder's review of 11/03, item 5): the property, then the unit only when it says something.
`trust_unit_shown(unit_id)` gives the unit's name to show, or null for a generic one (House, Home,
Main...) or a property's only unit, so "Cedar House, House" reads "Cedar House". Staff's own pages
keep every unit's name."""

from __future__ import annotations


def home(prop: str, unit: str | None) -> str:
    """ "Elm Court, Unit 4", or "Cedar House" when the unit's name adds nothing."""
    return prop if not unit else f"{prop}, {unit}"
