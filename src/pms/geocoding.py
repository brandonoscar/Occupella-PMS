"""Where a property is, through a geocoder behind one interface (migration 0109), and where it is
in the law: its state, county, county subdivision, city and census tract (migration
20261023000202).

The server's PMS_GEOCODER picks it (pms.api.Settings): "census", the U.S. Census Bureau's free
geocoder, called directly over HTTPS (its Python wrapper is GPL-3.0, so it isn't used); "fake"
for development and CI, which calls nothing; or unset for none, and finding a property is then
refused. Only a property's street address is ever sent, and only when staff ask: never a person's
contact details.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

SIX = Decimal("0.000001")

# Each USPS state code's FIPS code and name, as the Census Bureau writes them.
STATES: dict[str, tuple[str, str]] = {
    "AL": ("01", "Alabama"), "AK": ("02", "Alaska"), "AZ": ("04", "Arizona"),
    "AR": ("05", "Arkansas"), "CA": ("06", "California"), "CO": ("08", "Colorado"),
    "CT": ("09", "Connecticut"), "DE": ("10", "Delaware"), "DC": ("11", "District of Columbia"),
    "FL": ("12", "Florida"), "GA": ("13", "Georgia"), "HI": ("15", "Hawaii"),
    "ID": ("16", "Idaho"), "IL": ("17", "Illinois"), "IN": ("18", "Indiana"),
    "IA": ("19", "Iowa"), "KS": ("20", "Kansas"), "KY": ("21", "Kentucky"),
    "LA": ("22", "Louisiana"), "ME": ("23", "Maine"), "MD": ("24", "Maryland"),
    "MA": ("25", "Massachusetts"), "MI": ("26", "Michigan"), "MN": ("27", "Minnesota"),
    "MS": ("28", "Mississippi"), "MO": ("29", "Missouri"), "MT": ("30", "Montana"),
    "NE": ("31", "Nebraska"), "NV": ("32", "Nevada"), "NH": ("33", "New Hampshire"),
    "NJ": ("34", "New Jersey"), "NM": ("35", "New Mexico"), "NY": ("36", "New York"),
    "NC": ("37", "North Carolina"), "ND": ("38", "North Dakota"), "OH": ("39", "Ohio"),
    "OK": ("40", "Oklahoma"), "OR": ("41", "Oregon"), "PA": ("42", "Pennsylvania"),
    "RI": ("44", "Rhode Island"), "SC": ("45", "South Carolina"), "SD": ("46", "South Dakota"),
    "TN": ("47", "Tennessee"), "TX": ("48", "Texas"), "UT": ("49", "Utah"),
    "VT": ("50", "Vermont"), "VA": ("51", "Virginia"), "WA": ("53", "Washington"),
    "WV": ("54", "West Virginia"), "WI": ("55", "Wisconsin"), "WY": ("56", "Wyoming"),
    "AS": ("60", "American Samoa"), "GU": ("66", "Guam"),
    "MP": ("69", "Northern Mariana Islands"), "PR": ("72", "Puerto Rico"),
    "VI": ("78", "U.S. Virgin Islands"),
}  # fmt: skip


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
class Place:
    """Where an address is in the law: FIPS codes, each with its name. A county subdivision, a
    place (a city, town or village) and a tract only where the geocoder found one; an address
    outside any incorporated place has none."""

    state_fips: str
    state_name: str
    county_fips: str | None = None
    county_name: str | None = None
    cousub_fips: str | None = None
    cousub_name: str | None = None
    place_fips: str | None = None
    place_name: str | None = None
    tract: str | None = None


@dataclass(frozen=True)
class Match:
    latitude: Decimal
    longitude: Decimal
    # The address as the geocoder matched it.
    matched: str
    # Where it is in the law; None when the geocoder said nothing of it.
    place: Place | None = None


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
            place=fake_place(address, digest),
        )


def fake_place(address: Address, digest: bytes) -> Place:
    """The fake geocoder's place: the address's own state, and a county, county subdivision and
    city worked out from its city's name (so one city is one place), a tract from the address. A
    city named "Unincorporated ..." is in no place."""
    state_fips, state_name = STATES[address.state]
    town = hashlib.sha256(f"{address.state}/{address.city.upper()}".encode()).digest()
    county = f"{int.from_bytes(town[:2], 'big') % 999 + 1:03d}"
    unincorporated = address.city.lower().startswith("unincorporated")
    return Place(
        state_fips=state_fips,
        state_name=state_name,
        county_fips=county,
        county_name=f"County {county}",
        cousub_fips=f"{int.from_bytes(town[2:5], 'big') % 99999 + 1:05d}",
        cousub_name=f"{address.city} division"[:100],
        place_fips=None
        if unincorporated
        else f"{int.from_bytes(town[5:8], 'big') % 99999 + 1:05d}",
        place_name=None if unincorporated else f"{address.city} city"[:100],
        tract=f"{int.from_bytes(digest[8:12], 'big') % 999999 + 1:06d}",
    )


class CensusGeocoder:
    """The U.S. Census Bureau's geocoder (geocoding.geo.census.gov), one address at a time,
    against its current address ranges, with the geographies the address is in (its current
    vintage, every layer). Its answers are public domain."""

    name = "census"
    url = "https://geocoding.geo.census.gov/geocoder/geographies/address"

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
                "vintage": "Current_Current",
                "layers": "all",
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
            place=census_place(first.get("geographies")),
        )
    except (KeyError, IndexError, TypeError, ArithmeticError, AttributeError) as failed:
        raise GeocoderFailed(
            f"the Census geocoder answered in a shape it shouldn't: {failed}"
        ) from None


def layer(geographies: dict[str, Any], name: str, code: str, digits: int) -> tuple[str, str] | None:
    """A layer's first geography's code and name, when it has one in the shape it should."""
    found = geographies.get(name) or []
    if not found:
        return None
    value, title = str(found[0].get(code, "")), str(found[0].get("NAME", "")).strip()
    if not re.fullmatch(rf"[0-9]{{{digits}}}", value):
        return None
    return value, (title or value)[:100]


def census_place(geographies: Any) -> Place | None:
    """Where the Census geocoder's geographies put an address: its state, county, county
    subdivision, incorporated place and tract. None without a state."""
    if not isinstance(geographies, dict):
        return None
    state = layer(geographies, "States", "STATE", 2)
    if state is None:
        return None
    county = layer(geographies, "Counties", "COUNTY", 3)
    cousub = layer(geographies, "County Subdivisions", "COUSUB", 5) if county else None
    place = layer(geographies, "Incorporated Places", "PLACE", 5)
    tract = layer(geographies, "Census Tracts", "TRACT", 6) if county else None
    return Place(
        state_fips=state[0],
        state_name=state[1],
        county_fips=county[0] if county else None,
        county_name=county[1] if county else None,
        cousub_fips=cousub[0] if cousub else None,
        cousub_name=cousub[1] if cousub else None,
        place_fips=place[0] if place else None,
        place_name=place[1] if place else None,
        tract=tract[0] if tract else None,
    )


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
