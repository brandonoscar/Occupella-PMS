"""A property's street address and where it is, through the API (src/pms/api/addresses.py,
migration 20261021000109): staff keep the address on the property's page, find it on a map with
the server's geocoder (the fake one here; nothing reaches the network), and find the property by
its address. The database's side is in tests/test_property_addresses.py. Every address here is
synthetic."""

import pytest
from fastapi.testclient import TestClient
from helpers import make_pmc
from people import join_as_owner, join_as_staff

from pms import geocoding
from pms.api import Settings, create_app

HOME = {"line_1": "100 Synthetic Way", "city": "Demo City", "state": "CA", "zip": "00001"}
ONE_LINE = "100 Synthetic Way, Demo City, CA 00001"


@pytest.fixture
def settings(database_url):
    return Settings(
        database_url=database_url,
        public_url="http://testserver",
        cookie_secure=False,
        geocoder="fake",
    )


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def prop(pmc):
    return pmc.owners[0].property_id


def save(client, prop, **address):
    return client.post(f"/api/properties/{prop}/address", json={**HOME, **address})


def page(client, prop):
    answer = client.get(f"/api/properties/{prop}")
    assert answer.status_code == 200, answer.text
    return answer.json()


def rows(conn, prop):
    return conn.execute(
        "SELECT count(*) FROM trust_property_addresses WHERE property_id = %s", (prop,)
    ).fetchone()[0]


def test_staff_keep_a_propertys_address_and_find_it_on_a_map(client, conn, staff, prop):
    assert (page(client, prop)["address"], page(client, prop)["location"]) == (None, None)
    assert page(client, prop)["can_locate"] is True

    saved = save(client, prop, line_2="  ", state="ca", zip=" 00001-0002 ")
    found = client.post(f"/api/properties/{prop}/locate", json={})

    assert saved.status_code == 204, saved.text
    assert found.status_code == 201, found.text
    shown = page(client, prop)
    assert shown["address"] | {"recorded_at": None} == {
        "line_1": "100 Synthetic Way",
        "line_2": None,
        "city": "Demo City",
        "state": "CA",
        "zip": "00001-0002",
        "one_line": "100 Synthetic Way, Demo City, CA 00001-0002",
        "recorded_by": "Staff 1",
        "recorded_at": None,
    }
    # The fake geocoder's point for that very address, and OpenStreetMap at it.
    expected = geocoding.FakeGeocoder().locate(
        geocoding.Address("100 Synthetic Way", None, "Demo City", "CA", "00001-0002")
    )
    assert expected is not None
    location = shown["location"]
    assert location == found.json()
    assert (location["latitude"], location["longitude"]) == (
        str(expected.latitude),
        str(expected.longitude),
    )
    assert location["matched"] == "100 SYNTHETIC WAY, DEMO CITY, CA 00001-0002"
    assert location["geocoder"] == "fake"
    assert location["map_url"] == (
        f"https://www.openstreetmap.org/?mlat={expected.latitude}&mlon={expected.longitude}"
        f"#map=17/{expected.latitude}/{expected.longitude}"
    )


def test_the_same_address_again_writes_nothing_and_a_new_one_is_not_found_yet(
    client, conn, staff, prop
):
    assert save(client, prop).status_code == 204
    assert client.post(f"/api/properties/{prop}/locate", json={}).status_code == 201
    assert save(client, prop, line_1=" 100 Synthetic Way ").status_code == 204
    assert rows(conn, prop) == 1
    assert page(client, prop)["location"] is not None

    assert save(client, prop, line_1="200 Example Street", line_2="Building B").status_code == 204

    shown = page(client, prop)
    assert rows(conn, prop) == 2
    assert shown["address"]["one_line"] == "200 Example Street, Building B, Demo City, CA 00001"
    assert shown["location"] is None


def test_the_list_and_the_search_show_and_find_a_property_by_its_address(client, staff, pmc, prop):
    save(client, prop)

    listed = client.get("/api/properties").json()["items"]
    by_street = client.get("/api/properties", params={"q": "synthetic way"}).json()["items"]
    found = client.get("/api/search", params={"q": "Synthetic Way"}).json()

    assert {row["id"]: row["address"] for row in listed} == {
        str(prop): ONE_LINE,
        str(pmc.owners[1].property_id): None,
    }
    assert [row["id"] for row in by_street] == [str(prop)]
    # The list's address is the page's, written the same way on both sides.
    assert by_street[0]["address"] == page(client, prop)["address"]["one_line"]
    assert [(f["kind"], f["id"], f["detail"]) for f in found] == [("property", str(prop), ONE_LINE)]


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ({"state": "ZZ"}, "ZZ is not a state's USPS code"),
        ({"state": "California"}, None),
        ({"zip": "1234"}, None),
        ({"zip": "00001-12"}, None),
        ({"line_1": "  "}, None),
        ({"city": ""}, None),
        ({"line_1": "x" * 201}, None),
    ],
)
def test_an_address_is_whole_with_a_state_code_and_a_zip_code(client, staff, prop, change, said):
    answer = save(client, prop, **change)

    assert answer.status_code == 422, answer.text
    if said is not None:
        assert answer.json()["detail"] == said
    assert page(client, prop)["address"] is None


def test_finding_a_property_needs_its_address_and_an_address_the_geocoder_knows(
    client, staff, prop
):
    none_yet = client.post(f"/api/properties/{prop}/locate", json={})
    save(client, prop, line_1="1 Nowhere Lane")
    unknown = client.post(f"/api/properties/{prop}/locate", json={})

    assert (none_yet.status_code, none_yet.json()["detail"]) == (
        409,
        "the property has no address yet: add its address first",
    )
    assert (unknown.status_code, unknown.json()["detail"]) == (
        422,
        "the geocoder found no match for 1 Nowhere Lane, Demo City, CA 00001: check the address",
    )
    assert page(client, prop)["location"] is None


def test_a_geocoder_that_fails_is_said_in_words_and_keeps_nothing(client, staff, prop):
    class Failing:
        name = "census"

        def locate(self, address):
            raise geocoding.GeocoderFailed("the Census geocoder did not answer: timed out")

    client.app.state.geocoder = Failing()
    save(client, prop)

    answer = client.post(f"/api/properties/{prop}/locate", json={})

    assert (answer.status_code, answer.json()["detail"]) == (
        502,
        "the Census geocoder did not answer: timed out",
    )
    assert page(client, prop)["location"] is None


def test_a_server_without_a_geocoder_says_so(database_url, conn, app_conn, pmc, prop):
    plain = Settings(database_url=database_url, public_url="http://testserver", cookie_secure=False)
    with TestClient(create_app(plain)) as client:
        join_as_staff(client, conn, app_conn, pmc)
        save(client, prop)
        answer = client.post(f"/api/properties/{prop}/locate", json={})

        assert (answer.status_code, answer.json()["detail"]) == (
            503,
            "this server finds no addresses on a map: whoever runs it sets PMS_GEOCODER",
        )
        assert page(client, prop)["can_locate"] is False


def test_a_property_of_another_company_is_not_found(client, conn, staff):
    elsewhere = make_pmc(conn).owners[0].property_id

    assert save(client, elsewhere).status_code == 404
    assert client.post(f"/api/properties/{elsewhere}/locate", json={}).status_code == 404
    assert rows(conn, elsewhere) == 0


def test_addresses_take_staff_whose_roles_allow_setup(
    client, other_client, conn, app_conn, pmc, prop
):
    assert save(client, prop).status_code == 401
    assert client.post(f"/api/properties/{prop}/locate", json={}).status_code == 401

    join_as_owner(other_client, app_conn, pmc)
    assert save(other_client, prop).status_code == 403
    assert other_client.post(f"/api/properties/{prop}/locate", json={}).status_code == 403

    join_as_staff(client, conn, app_conn, pmc)
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=[])
    role = client.post("/api/custom-roles", json={"name": "Bills", "areas": ["bills"]}).json()["id"]
    client.post(
        f"/api/custom-roles/{role}/staff",
        json={"staff_id": str(clerk.person_id), "granted": True},
    )
    refused = save(other_client, prop)
    assert refused.status_code == 403
    assert "your roles don't let you change" in refused.json()["detail"]
    assert rows(conn, prop) == 0
