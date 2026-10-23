"""The geocoders (src/pms/geocoding.py): the fake one calls nothing and lands an address on one
point and one place in the law; the Census one sends the street address alone and reads the
bureau's answer, its geographies too (migration 20261023000202). No test reaches the network:
urlopen is replaced. Every address here is synthetic, and the Census answer's shape is as the
bureau documents it (NOT VERIFIED against a live answer: the build container can't reach it)."""

import io
import json
import urllib.parse
from decimal import Decimal

import pytest

from pms import geocoding
from pms.api import Settings

HOME = geocoding.Address(
    line_1="100 Synthetic Way", line_2="Unit 2", city="Demo City", state="CA", zip="00001-0002"
)
CENSUS_ANSWER = {
    "result": {
        "input": {"address": {"street": "100 Synthetic Way"}},
        "addressMatches": [
            {
                "matchedAddress": "100 SYNTHETIC WAY, DEMO CITY, CA, 00001",
                "coordinates": {"x": -120.12345678, "y": 37.1234564},
                "geographies": {
                    "States": [{"STATE": "06", "NAME": "California", "GEOID": "06"}],
                    "Counties": [{"STATE": "06", "COUNTY": "999", "NAME": "Demo County"}],
                    "County Subdivisions": [{"COUSUB": "99999", "NAME": "Demo CCD"}],
                    "Incorporated Places": [{"PLACE": "99990", "NAME": "Demo City city"}],
                    "Census Tracts": [{"TRACT": "999900", "NAME": "Census Tract 9999"}],
                },
            }
        ],
    }
}


def test_an_address_reads_on_one_line():
    assert HOME.one_line() == "100 Synthetic Way, Unit 2, Demo City, CA 00001-0002"
    plain = geocoding.Address("100 Synthetic Way", None, "Demo City", "CA", "00001")
    assert plain.one_line() == "100 Synthetic Way, Demo City, CA 00001"


def test_the_fake_geocoder_lands_an_address_on_one_point_in_the_lower_48():
    fake = geocoding.FakeGeocoder()
    first, again = fake.locate(HOME), fake.locate(HOME)
    other = fake.locate(geocoding.Address("200 Example Street", None, "Demo City", "CA", "00001"))

    assert first == again and first is not None and other is not None
    assert first != other
    assert Decimal(25) <= first.latitude <= Decimal(49)
    assert Decimal(-124) <= first.longitude <= Decimal(-67)
    assert first.latitude == first.latitude.quantize(Decimal("0.000001"))
    assert first.matched == HOME.one_line().upper()
    assert (
        fake.locate(geocoding.Address("1 Nowhere Lane", None, "Demo City", "CA", "00001")) is None
    )


def test_the_fake_geocoder_puts_one_city_in_one_place_in_its_own_state():
    fake = geocoding.FakeGeocoder()
    first = fake.locate(HOME)
    neighbor = fake.locate(
        geocoding.Address("200 Example Street", None, "Demo City", "CA", "00001")
    )
    assert first is not None and neighbor is not None
    assert first.place is not None and neighbor.place is not None
    assert (first.place.state_fips, first.place.state_name) == ("06", "California")
    assert first.place.place_name == "Demo City city"
    assert first.place.county_fips == neighbor.place.county_fips
    assert first.place.place_fips == neighbor.place.place_fips
    elsewhere = fake.locate(
        geocoding.Address("1 Sample Road", None, "Unincorporated Demo", "TX", "00002")
    )
    assert elsewhere is not None and elsewhere.place is not None
    assert elsewhere.place.state_fips == "48"
    assert (elsewhere.place.place_fips, elsewhere.place.place_name) == (None, None)
    assert elsewhere.place.county_fips is not None and elsewhere.place.tract is not None


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def census_answering(monkeypatch, body, seen):
    def urlopen(url, timeout):
        seen.append((url, timeout))
        if isinstance(body, Exception):
            raise body
        return Answer(body if isinstance(body, bytes) else json.dumps(body).encode())

    monkeypatch.setattr(geocoding.urllib.request, "urlopen", urlopen)


def test_the_census_geocoder_sends_the_street_address_alone_and_reads_its_match(monkeypatch):
    seen: list = []
    census_answering(monkeypatch, CENSUS_ANSWER, seen)

    match = geocoding.CensusGeocoder(timeout=3).locate(HOME)

    assert match == geocoding.Match(
        latitude=Decimal("37.123456"),
        longitude=Decimal("-120.123457"),
        matched="100 SYNTHETIC WAY, DEMO CITY, CA, 00001",
        place=geocoding.Place(
            state_fips="06",
            state_name="California",
            county_fips="999",
            county_name="Demo County",
            cousub_fips="99999",
            cousub_name="Demo CCD",
            place_fips="99990",
            place_name="Demo City city",
            tract="999900",
        ),
    )
    ((url, timeout),) = seen
    assert timeout == 3
    assert url.startswith("https://geocoding.geo.census.gov/geocoder/geographies/address?")
    assert dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)) == {
        "street": "100 Synthetic Way",
        "city": "Demo City",
        "state": "CA",
        "zip": "00001",
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "layers": "all",
        "format": "json",
    }


def test_the_census_geographies_give_what_they_hold_in_the_shape_they_should():
    def place_of(geographies):
        return geocoding.census_place(geographies)

    assert place_of(None) is None
    assert place_of({}) is None  # no state: nothing of the law
    assert place_of({"States": [{"STATE": "6", "NAME": "California"}]}) is None
    outside = place_of(
        {
            "States": [{"STATE": "06", "NAME": "California"}],
            "Counties": [{"COUNTY": "999", "NAME": "Demo County"}],
            "Incorporated Places": [],  # outside any city
            "Census Tracts": [{"TRACT": "12"}],  # not six digits: left out
        }
    )
    assert outside == geocoding.Place("06", "California", "999", "Demo County")
    no_county = place_of(
        {
            "States": [{"STATE": "06", "NAME": "California"}],
            "County Subdivisions": [{"COUSUB": "99999", "NAME": "Demo CCD"}],
            "Census Tracts": [{"TRACT": "999900"}],
        }
    )
    assert no_county == geocoding.Place("06", "California")  # a subdivision or tract is a county's


def test_the_census_geocoder_says_when_it_found_nothing_or_failed(monkeypatch):
    census = geocoding.CensusGeocoder()
    census_answering(monkeypatch, {"result": {"addressMatches": []}}, [])
    assert census.locate(HOME) is None

    census_answering(monkeypatch, OSError("timed out"), [])
    with pytest.raises(geocoding.GeocoderFailed, match="did not answer: timed out"):
        census.locate(HOME)
    census_answering(monkeypatch, b"<html>busy</html>", [])
    with pytest.raises(geocoding.GeocoderFailed, match="did not answer"):
        census.locate(HOME)
    census_answering(monkeypatch, {"result": {"addressMatches": [{"coordinates": {}}]}}, [])
    with pytest.raises(geocoding.GeocoderFailed, match="in a shape it shouldn't"):
        census.locate(HOME)


def test_the_server_picks_its_geocoder_from_the_environment(monkeypatch):
    assert geocoding.geocoder_named("") is None
    assert geocoding.geocoder_named("  ") is None
    assert isinstance(geocoding.geocoder_named("fake"), geocoding.FakeGeocoder)
    assert isinstance(geocoding.geocoder_named("census"), geocoding.CensusGeocoder)
    with pytest.raises(geocoding.NoGeocoder, match="no geocoder called 'acme'"):
        geocoding.geocoder_named("acme")

    monkeypatch.setenv("DATABASE_URL", "postgres://example.invalid/pms")
    monkeypatch.setenv("PMS_GEOCODER", "census")
    assert Settings.from_env().geocoder == "census"
    monkeypatch.delenv("PMS_GEOCODER")
    assert Settings.from_env().geocoder == ""
