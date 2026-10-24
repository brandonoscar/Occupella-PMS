"""Property-based tests for the listing feeds (migration 20261023000250, src/pms/listing_feeds.py).

Hypothesis builds a company's feed: properties of one unit or many, each with listed units of any
size and rent, in any order, with names and descriptions in any script, quotes, angle brackets,
ampersands and control characters, photos and days free (today, later, or none known). Then
for both formats it checks:
- the file is UTF-8 XML that reads back, and every text reads back as written but for what XML
  1.0 can't carry, which is left out;
- every ID is letters and digits only, as the sites ask;
- each listed unit is in it exactly once, at its own rent to the cent, with its bedrooms and
  bathrooms (Zillow's full and half ones adding back to them) and its photos in order;
- Zillow's: a property of one unit is one listing; one of more is one multifamily listing whose
  every unit model names a floorplan of its listing, of its own size, whose prices bound its
  units' rents;
- MITS's: each listing is a Property of its own.

And a run of staff's choices through the database: a listing is in the feed exactly when its
latest choice put it in, each choice changing it (a choice that changes nothing is refused).
Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import string
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from xml.etree import ElementTree

import psycopg
from helpers import add_staff, add_unit, make_pmc, open_application_link
from hypothesis import event, given
from hypothesis import strategies as st

from pms import listing_feeds

TODAY = date(2026, 10, 23)
ALPHANUMERIC = set(string.ascii_letters + string.digits)
WORDS = st.text(min_size=1, max_size=40)

UNITS = st.builds(
    listing_feeds.Unit,
    listing_id=st.uuids(),
    unit_id=st.uuids(),
    name=WORDS,
    rent=st.integers(1, 10**9).map(lambda cents: Decimal(cents) / 100),
    available_on=st.one_of(
        st.none(), st.integers(-10, 400).map(lambda days: TODAY + timedelta(days=days))
    ),
    bedrooms=st.integers(0, 20),
    bathrooms=st.integers(0, 40).map(lambda halves: Decimal(halves) / 2),
    square_feet=st.one_of(st.none(), st.integers(1, 100_000)),
    description=st.one_of(st.none(), st.text(max_size=200)),
    photos=st.lists(
        st.builds(
            listing_feeds.Photo,
            id=st.uuids(),
            url=st.uuids().map(lambda i: f"https://pms.example.com/api/listings/p/photos/{i}"),
            content_type=st.sampled_from(["image/png", "image/jpeg"]),
        ),
        max_size=3,
    ).map(tuple),
    apply_url=st.uuids().map(lambda i: f"https://pms.example.com/apply?listing={i}"),
    updated_at=st.datetimes(
        min_value=datetime(2026, 1, 1), max_value=datetime(2026, 10, 23), timezones=st.just(UTC)
    ),
)


@st.composite
def properties(draw):
    units = tuple(draw(st.lists(UNITS, min_size=1, max_size=5, unique_by=lambda u: u.unit_id)))
    return listing_feeds.Property(
        id=draw(st.uuids()),
        name=draw(WORDS),
        address=listing_feeds.Address(
            draw(WORDS),
            draw(st.one_of(st.none(), WORDS)),
            draw(WORDS),
            "CA",
            "00001",
            draw(st.one_of(st.none(), st.just(Decimal("37.000001")))),
            draw(st.one_of(st.none(), st.just(Decimal("-122.000001")))),
        ),
        # A property has at least the units it lists, sometimes more.
        unit_count=len(units) + draw(st.integers(0, 60)),
        units=units,
    )


FEEDS = st.builds(
    listing_feeds.Feed,
    company=st.builds(
        listing_feeds.Company,
        id=st.uuids(),
        name=WORDS,
        line_1=WORDS,
        line_2=st.one_of(st.none(), WORDS),
        city=WORDS,
        state=st.just("CA"),
        zip=st.just("00002"),
        website=st.just("https://pms.example.com/listings?pmc=p"),
    ),
    contact=st.builds(
        listing_feeds.Contact,
        name=WORDS,
        email=st.just("leasing@example.com"),
        phone=st.just("2025550142"),
    ),
    properties=st.lists(properties(), max_size=4, unique_by=lambda p: p.id).map(tuple),
    today=st.just(TODAY),
)


def read(written: bytes) -> ElementTree.Element:
    assert written.startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n')
    return ElementTree.fromstring(written.decode("utf-8"))


def ids(root: ElementTree.Element) -> list[str]:
    attributes = ("id", "companyId", "parentModelId", "IDValue", "FileID")
    return [e.get(a) for e in root.iter() for a in attributes if e.get(a) is not None]


def bathrooms(element: ElementTree.Element) -> Decimal:
    full = int(element.findtext("numFullBaths"))
    half = int(element.findtext("numHalfBaths") or 0)
    return Decimal(full) + Decimal(half) / 2


@given(feed=FEEDS)
def test_the_zillow_feed_carries_each_listed_unit_once_as_written(feed):
    root = read(listing_feeds.zillow(feed))

    assert root.find("Company").findtext("name") == listing_feeds.text(feed.company.name)
    assert all(set(value) <= ALPHANUMERIC for value in ids(root))
    listings = root.findall("listing")
    assert len(listings) == len(feed.properties)
    for prop, listing in zip(feed.properties, listings, strict=True):
        assert listing.findtext("name") == listing_feeds.text(prop.name)
        assert listing.findtext("street") == listing_feeds.text(prop.address.line_1)
        if prop.unit_count <= 1:
            event("one unit: a single listing")
            (unit,) = prop.units
            assert listing.get("id") == unit.unit_id.hex
            assert listing.get("propertyType") == "HOUSE"
            assert listing.findtext("price") == listing_feeds.money(unit.rent)
            assert Decimal(listing.findtext("price")) == unit.rent
            assert int(listing.findtext("numBedrooms")) == unit.bedrooms
            assert bathrooms(listing) == unit.bathrooms
            assert listing.findtext("description") == (
                listing_feeds.text(unit.description) if unit.description else None
            )
            assert [p.get("source") for p in listing.findall("listingPhoto")] == [
                p.url for p in unit.photos
            ]
            continue
        event("several units: a multifamily listing")
        assert listing.get("id") == prop.id.hex
        assert listing.get("propertyType") in {"DIVIDED", "MEDIUM", "LARGE"}
        models = listing.findall("model")
        plans = {m.get("id"): m for m in models if m.get("type") == "floorplan"}
        units = [m for m in models if m.get("type") == "unit"]
        assert [m.get("id") for m in units] == [u.unit_id.hex for u in prop.units]
        for unit, model in zip(prop.units, units, strict=True):
            plan = plans[model.get("parentModelId")]
            assert model.findtext("lowPrice") == listing_feeds.money(unit.rent)
            assert model.findtext("unitNumber") == listing_feeds.text(unit.name)
            assert [p.get("source") for p in model.findall("modelPhoto")] == [
                p.url for p in unit.photos
            ]
            for sized in (model, plan):
                assert int(sized.findtext("numBedrooms")) == unit.bedrooms
                assert bathrooms(sized) == unit.bathrooms
                assert sized.findtext("squareFeet") == (
                    str(unit.square_feet) if unit.square_feet else None
                )
            low = Decimal(plan.findtext("lowPrice"))
            high = Decimal(plan.findtext("highPrice") or low)
            assert low <= unit.rent <= high
        # Every floorplan is some listed unit's.
        assert set(plans) == {m.get("parentModelId") for m in units}


@given(feed=FEEDS)
def test_the_mits_feed_carries_each_listed_unit_once_as_written(feed):
    root = read(listing_feeds.mits(feed))

    assert all(set(value) <= ALPHANUMERIC for value in ids(root))
    units = [unit for prop in feed.properties for unit in prop.units]
    found = root.findall("Property")
    assert [p.get("IDValue") for p in found] == [u.unit_id.hex for u in units]
    assert listing_feeds.listing_ids(feed.properties) == [u.listing_id for u in units]
    for unit, prop in zip(units, found, strict=True):
        one = prop.find("ILS_Unit/Units/Unit")
        assert one.findtext("MarketingName") == listing_feeds.text(unit.name)
        assert Decimal(one.findtext("UnitRent")) == unit.rent
        assert int(one.findtext("UnitBedrooms")) == unit.bedrooms
        assert Decimal(one.findtext("UnitBathrooms")) == unit.bathrooms
        assert prop.findtext("Information/LongDescription") == (
            listing_feeds.text(unit.description) if unit.description else None
        )
        assert [f.findtext("Src") for f in prop.findall("File")] == [p.url for p in unit.photos]
        vacancy = prop.findtext("ILS_Unit/Availability/VacancyClass")
        free_now = unit.available_on is not None and unit.available_on <= TODAY
        event("free now" if free_now else "free later or not known")
        assert vacancy == ("Unoccupied" if free_now else "Occupied")


@given(text=st.text())
def test_any_text_reads_back_but_what_xml_cannot_carry(text):
    root = ElementTree.Element("t")
    listing_feeds.add(root, "v", text, a=text)
    back = read(listing_feeds.written(root)).find("v")

    kept = listing_feeds.text(text)
    assert back.text == (kept or None)
    assert back.get("a") == kept
    assert not {ord(c) for c in kept} & listing_feeds.NOT_XML
    event("left something out" if len(kept) < len(text) else "kept it all")


@given(choices=st.lists(st.booleans(), max_size=8))
def check_choices(conn, choices):
    pmc = make_pmc(conn)
    staff = add_staff(conn, pmc.pmc_id)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    link = open_application_link(conn, pmc.pmc_id, unit, staff)
    listing = conn.execute(
        "INSERT INTO trust_listings (pmc_id, link_id, listed_by) VALUES (%s, %s, %s) RETURNING id",
        (pmc.pmc_id, link, staff),
    ).fetchone()[0]
    in_feed = False
    rows = 0
    for choice in choices:
        try:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO trust_listing_feed_choices (pmc_id, listing_id, in_feed)"
                    " VALUES (%s, %s, %s)",
                    (pmc.pmc_id, listing, choice),
                )
        except psycopg.errors.CheckViolation:
            event("a choice that changes nothing: refused")
            assert choice == in_feed
        else:
            assert choice != in_feed
            in_feed = choice
            rows += 1
        assert conn.execute("SELECT trust_listing_in_feed(%s)", (listing,)).fetchone() == (in_feed,)
    assert conn.execute(
        "SELECT count(*) FROM trust_listing_feed_choices WHERE listing_id = %s", (listing,)
    ).fetchone() == (rows,)


def test_a_listing_is_in_the_feed_exactly_by_its_latest_choice(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_choices(conn)


def test_a_feed_of_no_listings_is_still_a_feed():
    feed = listing_feeds.Feed(
        company=listing_feeds.Company(
            uuid4(), "Synthetic PMC", "1 Example Plaza", None, "Demo City", "CA", "00002", "x"
        ),
        contact=listing_feeds.Contact("Leasing", "leasing@example.com", "2025550142"),
        properties=(),
        today=TODAY,
    )

    assert read(listing_feeds.zillow(feed)).findall("listing") == []
    assert read(listing_feeds.mits(feed)).findall("Property") == []
    assert UUID(read(listing_feeds.zillow(feed)).find("Company").get("id")) == feed.company.id
