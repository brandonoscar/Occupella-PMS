"""Vacancy listings through the API (src/pms/api/listings.py, src/pms/api/leasing.py): staff list
an open application link on the PMC's public page and take it off again; anyone opens the page
without a login and applies through a listing as through its link."""

import uuid
from datetime import date, timedelta

import pytest
from helpers import add_unit, make_pmc
from people import join_as_owner, join_as_staff, join_as_tenant


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def open_link(client, conn, pmc, name="Unit 1", rent="1500.00"):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name)
    answer = client.post("/api/leasing/links", json={"unit_id": str(unit), "monthly_rent": rent})
    assert answer.status_code == 201, answer.text
    return answer.json()


def list_link(client, link, description=None):
    return client.post(
        f"/api/leasing/links/{link['id']}/listing", json={"description": description}
    )


def application(**via):
    return {
        **via,
        "move_in_on": (date.today() + timedelta(days=30)).isoformat(),
        "monthly_income": "4500.00",
        "applicants": [{"name": "Applicant 1", "email": "applicant.1@example.com"}],
    }


def test_staff_list_open_links_and_anyone_sees_the_page(client, other_client, conn, staff, pmc):
    first = open_link(client, conn, pmc, "Unit 2", "1500.00")
    second = open_link(client, conn, pmc, "Unit 1", "1250.50")
    open_link(client, conn, pmc, "Unit 3")  # open, not listed
    listed = list_link(client, first, "  Two bedrooms, sunny.  ")
    list_link(client, second)

    page = other_client.get(f"/api/listings/{pmc.pmc_id}")  # nobody signed in
    links = client.get("/api/leasing/links").json()

    assert listed.status_code == 201
    assert page.status_code == 200
    body = page.json()
    assert (
        body["pmc"]
        == conn.execute(
            "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone()[0]
    )
    assert [(x["unit"], x["monthly_rent"], x["description"]) for x in body["listings"]] == [
        ("Unit 1", "1250.50", None),
        ("Unit 2", "1500.00", "Two bedrooms, sunny."),
    ]
    assert {x["id"]: x["listing_id"] for x in links}[first["id"]] == listed.json()["id"]
    assert sum(x["listing_id"] is None for x in links) == 1


def test_an_applicant_applies_through_a_listing(client, other_client, conn, staff, pmc):
    link = open_link(client, conn, pmc)
    listing = list_link(client, link).json()["id"]

    seen = other_client.post("/api/apply/view", json={"listing": listing})
    applied = other_client.post("/api/apply", json=application(listing=listing))

    assert seen.status_code == 200
    assert (seen.json()["unit"], seen.json()["monthly_rent"]) == ("Unit 1", "1500.00")
    assert applied.status_code == 201, applied.text
    assert applied.json()["status_url"].startswith("http://testserver/application#")
    assert client.get("/api/leasing/links").json()[0]["applications"] == 1


def test_a_removed_listing_or_a_closed_link_leaves_the_page_and_takes_no_application(
    client, other_client, conn, staff, pmc
):
    removed, closed = open_link(client, conn, pmc, "Unit 1"), open_link(client, conn, pmc, "Unit 2")
    removed_listing = list_link(client, removed).json()["id"]
    closed_listing = list_link(client, closed).json()["id"]

    gone = client.post(f"/api/leasing/listings/{removed_listing}/remove", json={})
    again = client.post(f"/api/leasing/listings/{removed_listing}/remove", json={})
    client.post(f"/api/leasing/links/{closed['id']}/close", json={})

    assert gone.status_code == 204
    assert again.status_code == 409
    assert again.json()["detail"] == "this listing is removed already"
    assert other_client.get(f"/api/listings/{pmc.pmc_id}").json()["listings"] == []
    for listing in (removed_listing, closed_listing):
        for path, body in (
            ("/api/apply/view", {"listing": listing}),
            ("/api/apply", application(listing=listing)),
        ):
            answer = other_client.post(path, json=body)
            assert answer.status_code == 404
            assert answer.json()["detail"] == "this application link is closed or doesn't exist"
    # The removed listing's link still takes applications through its own token.
    token = removed["url"].split("#", 1)[1]
    assert other_client.post("/api/apply", json=application(token=token)).status_code == 201


def test_a_link_is_listed_once_and_only_while_open(client, conn, staff, pmc):
    link = open_link(client, conn, pmc)
    first = list_link(client, link)
    listing = first.json()["id"]
    client.post(f"/api/leasing/listings/{listing}/remove", json={})
    again = list_link(client, link)
    closed = open_link(client, conn, pmc, "Unit 2")
    client.post(f"/api/leasing/links/{closed['id']}/close", json={})

    assert first.status_code == 201
    assert again.status_code == 409
    assert again.json()["detail"] == (
        "this link was listed already; open a new link to list the unit again"
    )
    assert list_link(client, closed).status_code == 409
    assert list_link(client, closed).json()["detail"] == "this link is closed"


def test_another_pmcs_links_listings_and_page(client, other_client, conn, app_conn, staff, pmc):
    other = make_pmc(conn)
    with_theirs = join_as_staff(other_client, conn, app_conn, other, "Staff 9")
    assert with_theirs
    theirs = open_link(other_client, conn, other)
    their_listing = list_link(other_client, theirs).json()["id"]

    assert list_link(client, theirs).status_code == 404
    assert client.post(f"/api/leasing/listings/{their_listing}/remove", json={}).status_code == 404
    assert client.get(f"/api/listings/{pmc.pmc_id}").json()["listings"] == []
    assert [x["id"] for x in client.get(f"/api/listings/{other.pmc_id}").json()["listings"]] == [
        their_listing
    ]


def test_an_unknown_pmc_has_no_page(client):
    answer = client.get(f"/api/listings/{uuid.uuid4()}")

    assert answer.status_code == 404
    assert answer.json()["detail"] == "no such PMC"


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_list_and_remove(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    expected = 401 if caller == "nobody" else 403

    answers = [
        client.post(f"/api/leasing/links/{uuid.uuid4()}/listing", json={}),
        client.post(f"/api/leasing/listings/{uuid.uuid4()}/remove", json={}),
    ]

    assert [a.status_code for a in answers] == [expected] * 2


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/leasing/links/{link}/listing", {"description": "   "}),
        ("post", "/api/leasing/links/{link}/listing", {"description": "x" * 2001}),
        ("post", "/api/leasing/links/not-a-link/listing", {}),
        ("post", "/api/leasing/listings/not-a-listing/remove", {}),
        ("get", "/api/listings/not-a-pmc", None),
        ("post", "/api/apply/view", {}),
        ("post", "/api/apply/view", {"listing": "not-a-listing"}),
        ("post", "/api/apply/view", {"listing": str(uuid.uuid4()), "token": "t" * 30}),
    ],
)
def test_bad_listing_input_is_refused(client, conn, staff, pmc, method, path, body):
    link = open_link(client, conn, pmc)
    url = path.format(link=link["id"])

    answer = client.get(url) if method == "get" else client.post(url, json=body)

    assert answer.status_code == 422
