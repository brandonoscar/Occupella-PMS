"""Tenant screening through a partner, behind one interface.

The founder picks the real partner at deploy time; its keys and terms come then (ADR 0002). Until
then the only partner is FakeScreening, which pulls no report: it answers from the application's
own numbers, so every result it gives is synthetic and says so. The partner collects anything
it needs beyond a name and an email (an SSN, a date of birth) on its own side; the PMS never
holds them.

The server's PMS_SCREENING_PARTNER picks the partner (pms.api.Settings): "fake", or unset for none,
and screening is then refused.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, Protocol

Recommendation = Literal["approve", "conditional", "decline"]


@dataclass(frozen=True)
class Applicant:
    name: str
    email: str


@dataclass(frozen=True)
class Application:
    """What a partner is told about an application."""

    applicants: tuple[Applicant, ...]
    move_in_on: date
    monthly_income: Decimal
    monthly_rent: Decimal


@dataclass(frozen=True)
class Result:
    partner: str
    reference: str
    # Who the applicant asks for a copy of the report, or disputes it with.
    agency: str
    recommendation: Recommendation
    reasons: tuple[str, ...]


class Partner(Protocol):
    name: str

    def screen(self, application: Application) -> Result: ...


class FakeScreening:
    """A stand-in partner for development and tests. It pulls no report: income of three times
    the rent or more is approved, twice to three times is approved on conditions, and less is
    declined."""

    name = "Fake Screening Partner"
    agency = (
        "Fake Screening Partner (synthetic, for testing only). No consumer report was pulled;"
        " a real partner gives its address and phone here."
    )

    def screen(self, application: Application) -> Result:
        ratio = application.monthly_income / application.monthly_rent
        recommendation: Recommendation
        reasons: tuple[str, ...]
        if ratio >= 3:
            recommendation, reasons = "approve", ()
        elif ratio >= 2:
            recommendation = "conditional"
            reasons = ("Household income is less than three times the rent",)
        else:
            recommendation = "decline"
            reasons = ("Household income is less than twice the rent",)
        return Result(
            partner=self.name,
            reference=f"fake-{uuid.uuid4().hex}",
            agency=self.agency,
            recommendation=recommendation,
            reasons=reasons,
        )


PARTNERS: dict[str, type[FakeScreening]] = {"fake": FakeScreening}


class NoPartner(Exception):
    """Screening was asked for, but no partner is set up."""


def partner_named(name: str) -> Partner | None:
    """The partner called `name` ("fake"), or None for an empty name: no partner."""
    chosen = name.strip()
    if not chosen:
        return None
    if chosen not in PARTNERS:
        raise NoPartner(f"no screening partner called {chosen!r}; known: {', '.join(PARTNERS)}")
    return PARTNERS[chosen]()
