"""Listing syndication through the API (migration 20261023000250, src/pms/api/listing_feeds.py,
src/pms/listing_feeds.py): staff send a listing on the public page to listing sites, keep the
leasing contact the feeds name and read each feed's URL; anyone fetches the feeds without a
login, in Zillow's rental listing format and in MITS's, and finds there each listing sent that
has what the sites ask. Every name, address and number here is synthetic."""

import base64
import uuid
from datetime import date, timedelta
from xml.etree import ElementTree

import pytest
from helpers import PNG, add_tenant, add_unit, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

TODAY = date.today()
HOME = {"line_1": "100 Synthetic Way", "city": "Demo City", "state": "CA", "zip": "00001"}
OFFICE = {
    "address_line_1": "1 Example Plaza",
    "city": "Demo City",
    "state": "CA",
    "postal_code": "00002",
}
CONTACT = {"contact_name": "Leasing office", "email": "leasing@example.com", "phone": "2025550142"}


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def ready(client, pmc):
    """The company's mailing address and leasing contact: what every feed needs."""
    name = client.get("/api/company").json()["name"]
    ok(client.post("/api/company", json={"name": name, "address": OFFICE}))
    return ok(client.post("/api/leasing/feeds", json=CONTACT))


def listed(client, conn, pmc, name="Unit 1", rent="1500.00", prop=None, details=None):
    """A unit with what the sites ask, listed on the public page: (unit, listing)."""
    prop = prop or pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, name)
    if client.get(f"/api/properties/{prop}").json().get("address") is None:
        ok(client.post(f"/api/properties/{prop}/address", json=HOME), 204)
    if details is not False:
        ok(
            client.post(
                f"/api/units/{unit}/details",
                json=details or {"bedrooms": 2, "bathrooms": "1.5", "square_feet": 950},
            )
        )
    made = ok(
        client.post(
            "/api/leasing/links",
            json={"unit_id": str(unit), "monthly_rent": rent, "listing": {}},
        ),
        201,
    )
    return unit, made["listing_id"]


def send(client, listing, in_feed=True):
    return client.post(f"/api/leasing/listings/{listing}/feed", json={"in_feed": in_feed})


def fetched(client, pmc, site):
    answer = client.get(f"/api/listings/{pmc.pmc_id}/feeds/{site}.xml")
    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"] == "application/xml; charset=utf-8"
    assert answer.content.startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n')
    return ElementTree.fromstring(answer.content)


def hexed(value):
    return uuid.UUID(str(value)).hex


def photo(client, unit):
    return ok(
        client.post(
            "/api/documents",
            json={
                "kind": "listing_photo",
                "about_id": str(unit),
                "title": "Kitchen",
                "content_type": "image/png",
                "content_base64": base64.b64encode(PNG).decode(),
            },
        ),
        201,
    )["id"]


def test_a_listing_sent_is_in_both_feeds_with_what_the_sites_ask(
    client, other_client, conn, staff, pmc
):
    ready(client, pmc)
    unit, listing = listed(client, conn, pmc)
    kitchen = photo(client, unit)

    sent = ok(send(client, listing))
    zillow = fetched(other_client, pmc, "zillow")  # nobody signed in
    mits = fetched(other_client, pmc, "mits")

    assert sent == {"in_feed": True, "changed": True}
    assert zillow.tag == "hotPadsItems" and zillow.get("version") == "2.1"
    company = zillow.find("Company")
    assert company.get("id") == hexed(pmc.pmc_id)
    assert (company.findtext("city"), company.findtext("state")) == ("Demo City", "CA")
    (home,) = zillow.findall("listing")
    apply_url = f"http://testserver/apply?listing={listing}"
    photo_url = f"http://testserver/api/listings/{pmc.pmc_id}/photos/{kitchen}"
    assert home.attrib == {
        "id": hexed(unit),
        "type": "RENTAL",
        "companyId": hexed(pmc.pmc_id),
        "propertyType": "HOUSE",
    }
    assert {child.tag: child.text for child in home if child.text and child.text.strip()} == {
        "name": conn.execute(
            "SELECT display_name FROM trust_properties WHERE id = %s",
            (pmc.owners[0].property_id,),
        ).fetchone()[0],
        "street": "100 Synthetic Way",
        "city": "Demo City",
        "state": "CA",
        "zip": "00001",
        "country": "US",
        "lastUpdated": home.findtext("lastUpdated"),
        "contactName": "Leasing office",
        "contactEmail": "leasing@example.com",
        "contactPhone": "2025550142",
        "website": apply_url,
        "price": "1500.00",
        "pricingFrequency": "MONTH",
        "numBedrooms": "2",
        "numFullBaths": "1",
        "numHalfBaths": "1",
        "squareFeet": "950",
        "dateAvailable": TODAY.isoformat(),
        "providerType": "propertyManager",
    }
    assert home.findtext("lastUpdated").endswith("Z")
    assert home.find("street").get("hide") == "false"
    assert home.find("listingTag").get("type") == "AVAILABILITY_URL"
    assert home.find("listingTag").findtext("tag") == apply_url
    assert [p.get("source") for p in home.findall("listingPhoto")] == [photo_url]
    # The photo's address is the public one, served to anyone while the unit is listed.
    assert other_client.get(photo_url.removeprefix("http://testserver")).content == PNG

    (prop,) = mits.findall("Property")
    assert mits.tag == "PhysicalProperty"
    assert mits.find("Management").get("IDValue") == hexed(pmc.pmc_id)
    assert prop.get("IDValue") == hexed(unit)
    assert prop.findtext("PropertyID/WebSite") == apply_url
    assert prop.findtext("PropertyID/Address/AddressLine1") == "100 Synthetic Way"
    assert prop.findtext("PropertyID/Phone/PhoneNumber") == "2025550142"
    assert prop.findtext("Information/Rents/StandardRent") == "1500.00"
    assert prop.findtext("Information/PropertyAvailabilityURL") == apply_url
    assert prop.findtext("ILS_Unit/Units/Unit/UnitBedrooms") == "2"
    assert prop.findtext("ILS_Unit/Units/Unit/UnitBathrooms") == "1.5"
    assert prop.findtext("ILS_Unit/Units/Unit/UnitRent") == "1500.00"
    assert prop.findtext("ILS_Unit/Availability/VacancyClass") == "Unoccupied"
    assert prop.find("ILS_Unit/Availability/MadeReadyDate").attrib == {
        "Month": str(TODAY.month),
        "Day": str(TODAY.day),
        "Year": str(TODAY.year),
    }
    assert [f.findtext("Src") for f in prop.findall("File")] == [photo_url]
    assert prop.find("File/Format").text == "image/png"


def test_a_property_of_several_units_is_one_multifamily_listing(
    client, other_client, conn, staff, pmc
):
    ready(client, pmc)
    small = {"bedrooms": 1, "bathrooms": "1", "square_feet": 600}
    first, a = listed(client, conn, pmc, "Unit 1", "1200.00", details=small)
    second, b = listed(client, conn, pmc, "Unit 2", "1250.00", details=small)
    third, c = listed(client, conn, pmc, "Unit 3", "1800.00")
    for listing in (a, b, c):
        ok(send(client, listing))
    add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 4")  # not listed

    (building,) = fetched(other_client, pmc, "zillow").findall("listing")
    models = building.findall("model")

    assert building.get("id") == hexed(pmc.owners[0].property_id)
    assert building.get("propertyType") == "DIVIDED"
    assert building.findtext("numUnits") == "4"
    assert building.findtext("website") == f"http://testserver/listings?pmc={pmc.pmc_id}"
    plans = {m.get("id"): m for m in models if m.get("type") == "floorplan"}
    assert set(plans) == {"B1H2S600", "B2H3S950"}
    assert plans["B1H2S600"].get("pricingType") == "RANGE"
    assert (plans["B1H2S600"].findtext("lowPrice"), plans["B1H2S600"].findtext("highPrice")) == (
        "1200.00",
        "1250.00",
    )
    assert plans["B2H3S950"].findtext("name") == "2 bd, 1.5 ba, 950 sq ft"
    assert plans["B2H3S950"].find("highPrice") is None
    units = [m for m in models if m.get("type") == "unit"]
    assert [(m.get("id"), m.get("parentModelId"), m.findtext("lowPrice")) for m in units] == [
        (hexed(first), "B1H2S600", "1200.00"),
        (hexed(second), "B1H2S600", "1250.00"),
        (hexed(third), "B2H3S950", "1800.00"),
    ]
    assert units[0].findtext("unitNumber") == "Unit 1"
    assert units[0].find("modelTag/tag").text == f"http://testserver/apply?listing={a}"
    # MITS: each listing a Property of its own, of a property of four units.
    properties = fetched(other_client, pmc, "mits").findall("Property")
    assert [p.get("IDValue") for p in properties] == [hexed(u) for u in (first, second, third)]
    assert {p.findtext("Information/UnitCount") for p in properties} == {"4"}
    assert {p.find("ILS_Identification").get("ILS_IdentificationType") for p in properties} == {
        "Apartment"
    }


def test_a_unit_free_later_shows_the_day_it_comes_free(client, other_client, conn, staff, pmc):
    ready(client, pmc)
    unit, listing = listed(client, conn, pmc)
    tenant = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 1")
    open_lease(
        conn,
        pmc.pmc_id,
        unit,
        TODAY - timedelta(days=300),
        TODAY + timedelta(days=20),
        "1400.00",
        [tenant],
    )
    ok(send(client, listing))

    home = fetched(other_client, pmc, "zillow").find("listing")
    availability = fetched(other_client, pmc, "mits").find("Property/ILS_Unit/Availability")
    free_on = TODAY + timedelta(days=21)

    assert home.findtext("dateAvailable") == free_on.isoformat()
    assert availability.findtext("VacancyClass") == "Occupied"
    assert availability.find("VacateDate").get("Day") == str((free_on - timedelta(days=1)).day)
    assert availability.find("MadeReadyDate").get("Day") == str(free_on.day)


def test_a_listing_leaves_the_feeds_when_staff_stop_or_it_leaves_the_page(
    client, other_client, conn, staff, pmc
):
    ready(client, pmc)
    _, kept = listed(client, conn, pmc, "Unit 1")
    _, removed = listed(client, conn, pmc, "Unit 2")
    ok(send(client, kept))
    ok(send(client, removed))

    stopped = ok(send(client, kept, False))
    again = ok(send(client, kept, False))
    ok(client.post(f"/api/leasing/listings/{removed}/remove", json={}))
    refused = send(client, removed)

    assert stopped == {"in_feed": False, "changed": True}
    assert again == {"in_feed": False, "changed": False}
    assert fetched(other_client, pmc, "zillow").findall("listing") == []
    assert fetched(other_client, pmc, "mits").findall("Property") == []
    assert refused.status_code == 409
    assert refused.json()["detail"] == "this listing is off the public page; list the unit again"
    # Staff may still take a listing off the page out of the feeds.
    assert ok(send(client, removed, False)) == {"in_feed": False, "changed": True}
    assert conn.execute(
        "SELECT count(*) FROM trust_listing_feed_choices WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (4,)


def test_the_feeds_list_nothing_until_the_company_has_its_address_and_a_contact(
    client, other_client, conn, staff, pmc
):
    _, listing = listed(client, conn, pmc)
    ok(send(client, listing))

    before = ok(client.get("/api/leasing/feeds"))
    empty = fetched(other_client, pmc, "zillow")
    after = ready(client, pmc)

    assert before["contact"] is None
    assert before["missing"] == [
        "the company's mailing address (Settings, Company setup)",
        "a leasing contact's email and phone (here)",
    ]
    assert before["listings"] == 0
    assert empty.find("Company") is not None and empty.findall("listing") == []
    assert after["missing"] == [] and after["listings"] == 1
    assert after["contact"] == CONTACT
    assert [(f["site"], f["url"]) for f in after["feeds"]] == [
        ("zillow", f"http://testserver/api/listings/{pmc.pmc_id}/feeds/zillow.xml"),
        ("mits", f"http://testserver/api/listings/{pmc.pmc_id}/feeds/mits.xml"),
    ]
    assert len(fetched(other_client, pmc, "zillow").findall("listing")) == 1


def test_a_listing_without_what_the_sites_ask_is_refused_and_listings_say_why(
    client, other_client, conn, staff, pmc
):
    ready(client, pmc)
    prop = pmc.owners[1].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit 9")
    listing = ok(
        client.post(
            "/api/leasing/links",
            json={"unit_id": str(unit), "monthly_rent": "1100.00", "listing": {}},
        ),
        201,
    )["listing_id"]

    refused = send(client, listing)
    (row,) = [v for v in ok(client.get("/api/leasing/vacancies"))["items"] if v["unit"] == "Unit 9"]
    ok(client.post(f"/api/properties/{prop}/address", json=HOME), 204)
    ok(client.post(f"/api/units/{unit}/details", json={"bedrooms": 0, "bathrooms": "1"}))
    sent = ok(send(client, listing))
    in_feed = len(fetched(other_client, pmc, "zillow").findall("listing"))
    ok(client.post(f"/api/units/{unit}/details", json={}))  # its details cleared
    (later,) = [
        v for v in ok(client.get("/api/leasing/vacancies"))["items"] if v["unit"] == "Unit 9"
    ]

    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "the listing sites ask the property's street address and the unit's bedrooms and the"
        " unit's bathrooms; add them first"
    )
    assert (row["in_feed"], row["feed_needs"]) == (
        False,
        ["the property's street address", "the unit's bedrooms", "the unit's bathrooms"],
    )
    assert sent["in_feed"] is True and in_feed == 1
    # Still chosen, but left out of the feeds until it has them again.
    assert (later["in_feed"], later["feed_needs"]) == (
        True,
        ["the unit's bedrooms", "the unit's bathrooms"],
    )
    assert fetched(other_client, pmc, "zillow").findall("listing") == []


def test_text_xml_cannot_carry_is_left_out_and_the_rest_escaped(
    client, other_client, conn, staff, pmc
):
    ready(client, pmc)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, 'Unit <"A"> & B')
    ok(client.post(f"/api/properties/{pmc.owners[0].property_id}/address", json=HOME), 204)
    ok(client.post(f"/api/units/{unit}/details", json={"bedrooms": 1, "bathrooms": "1"}))
    listing = ok(
        client.post(
            "/api/leasing/links",
            json={
                "unit_id": str(unit),
                "monthly_rent": "999.99",
                "listing": {"description": "Sunny\x01 & <quiet>\x0b room"},
            },
        ),
        201,
    )["listing_id"]
    ok(send(client, listing))

    prop = fetched(other_client, pmc, "mits").find("Property")
    home = fetched(other_client, pmc, "zillow").find("listing")

    assert prop.findtext("Information/LongDescription") == "Sunny & <quiet> room"
    assert prop.findtext("ILS_Unit/Units/Unit/MarketingName") == 'Unit <"A"> & B'
    assert home.findtext("description") == "Sunny & <quiet> room"


def test_the_contact_is_kept_once_per_change(client, conn, staff, pmc):
    first = ok(client.post("/api/leasing/feeds", json={**CONTACT, "phone": "(202) 555-0142"}))
    ok(client.post("/api/leasing/feeds", json=CONTACT))
    changed = ok(
        client.post(
            "/api/leasing/feeds", json={"email": "rent@example.com", "phone": CONTACT["phone"]}
        )
    )

    assert first["contact"] == CONTACT
    assert changed["contact"] == {
        "contact_name": None,
        "email": "rent@example.com",
        "phone": "2025550142",
    }
    assert conn.execute(
        "SELECT count(*) FROM trust_listing_feed_contacts WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (2,)
    assert (
        conn.execute(
            "SELECT s.display_name FROM trust_listing_feed_contacts AS c"
            " JOIN trust_staff AS s ON s.id = c.recorded_by WHERE c.pmc_id = %s LIMIT 1",
            (pmc.pmc_id,),
        ).fetchone()
        is not None
    )


def test_another_pmcs_listings_and_feeds(client, other_client, conn, app_conn, staff, pmc):
    other = make_pmc(conn)
    join_as_staff(other_client, conn, app_conn, other, "Staff 9")
    ready(other_client, other)
    _, theirs = listed(other_client, conn, other)
    ok(send(other_client, theirs))
    ready(client, pmc)

    assert send(client, theirs).status_code == 404
    assert send(client, theirs).json()["detail"] == "no such listing"
    assert fetched(client, pmc, "zillow").findall("listing") == []
    assert len(fetched(client, other, "zillow").findall("listing")) == 1
    assert ok(client.get("/api/leasing/feeds"))["listings"] == 0


@pytest.mark.parametrize("site", ["zillow", "mits"])
def test_an_unknown_pmc_has_no_feed(client, site):
    answer = client.get(f"/api/listings/{uuid.uuid4()}/feeds/{site}.xml")

    assert answer.status_code == 404
    assert answer.json()["detail"] == "no such PMC"


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_send_listings_and_keep_the_contact(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    expected = 401 if caller == "nobody" else 403

    answers = [
        client.get("/api/leasing/feeds"),
        client.post("/api/leasing/feeds", json=CONTACT),
        send(client, uuid.uuid4()),
    ]

    assert [a.status_code for a in answers] == [expected] * 3


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/leasing/feeds", {**CONTACT, "phone": "555-0142"}),
        ("post", "/api/leasing/feeds", {**CONTACT, "phone": "1" * 11}),
        ("post", "/api/leasing/feeds", {**CONTACT, "email": "leasing"}),
        ("post", "/api/leasing/feeds", {**CONTACT, "contact_name": "x" * 101}),
        ("post", "/api/leasing/feeds", {"email": CONTACT["email"]}),
        ("post", "/api/leasing/listings/{listing}/feed", {}),
        ("post", "/api/leasing/listings/{listing}/feed", {"in_feed": "maybe"}),
        ("post", "/api/leasing/listings/not-a-listing/feed", {"in_feed": True}),
        ("get", "/api/listings/not-a-pmc/feeds/zillow.xml", None),
        ("get", "/api/listings/not-a-pmc/feeds/mits.xml", None),
    ],
)
def test_bad_feed_input_is_refused(client, conn, staff, pmc, method, path, body):
    _, listing = listed(client, conn, pmc)
    url = path.format(listing=listing)

    answer = client.get(url) if method == "get" else client.post(url, json=body)

    assert answer.status_code == 422
