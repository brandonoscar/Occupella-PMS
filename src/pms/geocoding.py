"""Where a property is, through a geocoder behind one interface (migration 0109).

The server's PMS_GEOCODER picks it (pms.api.Settings): "census", the U.S. Census Bureau's free
geocoder, called directly over HTTPS (its Python wrapper is GPL-3.0, so it isn't used); "fake"
for development and CI, which calls nothing; or unset for none, and finding a property is then
refused. Only a property's street address is ever sent, and only when staff ask: never a person's
contact details.
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

SIX = Decimal("0.000001")


@dataclass(frozen=True)
class Address:
    line_1: str
    line_2: str | None
    city: str
    state: str
    zip: str

    def one_line(self) -> str:
        """ "100 Synthetic Way, Unit 2, Demo City, CA 00001"."""
        street = self.line_1 if self.line_2 is None else f"{self.line_1}, {self.line_2}"
        return f"{street}, {self.city}, {self.state} {self.zip}"


@dataclass(frozen=True)
class Match:
    latitude: Decimal
    longitude: Decimal
    # The address as the geocoder matched it.
    matched: str


class GeocoderFailed(Exception):
    """The geocoder could not be reached or answered with something it shouldn't."""


class Geocoder(Protocol):
    name: str

    def locate(self, address: Address) -> Match | None: ...


class FakeGeocoder:
    """A stand-in for development and tests: it calls nothing. Each address gets a point in the
    lower 48 states worked out from its own text, so the same address lands on the same point; a
    first line naming "Nowhere" is not found."""

    name = "fake"

    def locate(self, address: Address) -> Match | None:
        if "nowhere" in address.line_1.lower():
            return None
        digest = hashlib.sha256(address.one_line().upper().encode()).digest()
        north = int.from_bytes(digest[:4], "big") / 2**32
        east = int.from_bytes(digest[4:8], "big") / 2**32
        return Match(
            latitude=(Decimal(25) + Decimal(24) * Decimal(north)).quantize(SIX),
            longitude=(Decimal(-124) + Decimal(57) * Decimal(east)).quantize(SIX),
            matched=address.one_line().upper(),
        )


class CensusGeocoder:
    """The U.S. Census Bureau's geocoder (geocoding.geo.census.gov), one address at a time,
    against its current address ranges. Its answers are public domain."""

    name = "census"
    url = "https://geocoding.geo.census.gov/geocoder/locations/address"

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def locate(self, address: Address) -> Match | None:
        street = address.line_1  # the second line (a unit) is not part of a street range
        query = urllib.parse.urlencode(
            {
                "street": street,
                "city": address.city,
                "state": address.state,
                "zip": address.zip[:5],
                "benchmark": "Public_AR_Current",
                "format": "json",
            }
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 (a fixed https URL)
                f"{self.url}?{query}", timeout=self.timeout
            ) as answer:
                body = json.load(answer)
        except (OSError, ValueError) as failed:
            raise GeocoderFailed(f"the Census geocoder did not answer: {failed}") from None
        return census_match(body)


def census_match(body: Any) -> Match | None:
    """The first match in the Census geocoder's answer, or None when it found none."""
    try:
        matches = body["result"]["addressMatches"]
        if not matches:
            return None
        first = matches[0]
        return Match(
            latitude=Decimal(str(first["coordinates"]["y"])).quantize(SIX),
            longitude=Decimal(str(first["coordinates"]["x"])).quantize(SIX),
            matched=str(first["matchedAddress"])[:300],
        )
    except (KeyError, IndexError, TypeError, ArithmeticError) as failed:
        raise GeocoderFailed(
            f"the Census geocoder answered in a shape it shouldn't: {failed}"
        ) from None


GEOCODERS: dict[str, type[FakeGeocoder] | type[CensusGeocoder]] = {
    "fake": FakeGeocoder,
    "census": CensusGeocoder,
}


class NoGeocoder(Exception):
    """A geocoder was named that isn't one."""


def geocoder_named(name: str) -> Geocoder | None:
    """The geocoder called `name` ("fake" or "census"), or None for an empty name: none."""
    chosen = name.strip()
    if not chosen:
        return None
    if chosen not in GEOCODERS:
        raise NoGeocoder(f"no geocoder called {chosen!r}; known: {', '.join(GEOCODERS)}")
    return GEOCODERS[chosen]()
