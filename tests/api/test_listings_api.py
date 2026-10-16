"""Vacancy listings through the API (src/pms/api/listings.py, src/pms/api/leasing.py): staff list
an open application link on the PMC's public page and take it off again; anyone opens the page
without a login and applies through a listing as through its link."""

import uuid
from datetime import date, timedelta

import pytest
from helpers import add_tenant, add_unit, make_pmc, open_lease
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
    links = client.get("/api/leasing/vacancies").json()["items"]

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
    assert {x["link_id"]: x["listing_id"] for x in links}[first["id"]] == listed.json()["id"]
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
    assert client.get("/api/leasing/vacancies").json()["items"][0]["applications"] == 1


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


# --- Leasing, Listings: the units coming free (ADR 0006) --------------------------------------

TODAY = date.today()


def vacancies(client, **params):
    answer = client.get("/api/leasing/vacancies", params=params)
    assert answer.status_code == 200, answer.text
    return answer.json()


def free(page):
    return [(v["unit"], v["available_on"], v["last_rent"]) for v in page["items"]]


def test_listings_show_each_unit_coming_free_within_90_days(client, conn, staff, pmc):
    prop = pmc.owners[0].property_id
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 9")
    vacant = add_unit(conn, pmc.pmc_id, prop, "Unit A")
    ending = add_unit(conn, pmc.pmc_id, prop, "Unit B")
    open_lease(
        conn,
        pmc.pmc_id,
        ending,
        TODAY - timedelta(days=300),
        TODAY + timedelta(days=29),
        "1400.00",
        [tenant],
    )
    renewed = add_unit(conn, pmc.pmc_id, prop, "Unit C")
    open_lease(
        conn,
        pmc.pmc_id,
        renewed,
        TODAY - timedelta(days=300),
        TODAY + timedelta(days=9),
        "1300.00",
        [tenant],
    )
    open_lease(
        conn,
        pmc.pmc_id,
        renewed,
        TODAY + timedelta(days=10),
        TODAY + timedelta(days=60),
        "1350.00",
        [tenant],
    )
    later = add_unit(conn, pmc.pmc_id, prop, "Unit D")
    open_lease(
        conn,
        pmc.pmc_id,
        later,
        TODAY - timedelta(days=30),
        TODAY + timedelta(days=120),
        "1200.00",
        [tenant],
    )
    month_to_month = add_unit(conn, pmc.pmc_id, prop, "Unit E")
    open_lease(
        conn, pmc.pmc_id, month_to_month, TODAY - timedelta(days=30), None, "1100.00", [tenant]
    )
    archived = add_unit(conn, pmc.pmc_id, prop, "Unit F")
    conn.execute(
        "INSERT INTO trust_archivals (pmc_id, unit_id, archived) VALUES (%s, %s, true)",
        (pmc.pmc_id, archived),
    )

    page = vacancies(client)

    assert free(page) == [
        ("Unit A", str(TODAY), None),
        ("Unit B", str(TODAY + timedelta(days=30)), "1400.00"),
        # Its next lease runs on from the day after the first, so it is free after that one.
        ("Unit C", str(TODAY + timedelta(days=61)), "1350.00"),
    ]
    assert page["total"] == 3
    assert [v["link_id"] for v in page["items"]] == [None] * 3
    assert vacancies(client, per_page=2, page=2)["items"][0]["unit"] == "Unit C"
    assert vacancies(client, property=str(pmc.owners[1].property_id))["total"] == 0
    assert vacant and later and month_to_month  # leased beyond 90 days, or with no end


def test_staff_open_a_link_and_list_it_at_once_and_the_unit_shows_it(
    client, other_client, conn, staff, pmc
):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 7")

    made = client.post(
        "/api/leasing/links",
        json={
            "unit_id": str(unit),
            "monthly_rent": "1625.00",
            "listing": {"description": "  Corner unit.  "},
        },
    )
    newer = open_link_for(client, unit, "1700.00")  # open, not listed

    assert made.status_code == 201, made.text
    (shown,) = vacancies(client)["items"]
    # The listed link shows, not the newer one.
    assert (shown["link_id"], shown["link_rent"], shown["listing_id"], shown["description"]) == (
        made.json()["id"],
        "1625.00",
        made.json()["listing_id"],
        "Corner unit.",
    )
    assert shown["applications"] == 0
    public = other_client.get(f"/api/listings/{pmc.pmc_id}").json()["listings"]
    assert [(x["unit"], x["monthly_rent"]) for x in public] == [("Unit 7", "1625.00")]
    # Taken off the page, the unit shows its newest open link.
    assert client.post(
        f"/api/leasing/listings/{made.json()['listing_id']}/remove", json={}
    ).is_success
    (after,) = vacancies(client)["items"]
    assert (after["link_id"], after["listing_id"]) == (newer["id"], None)


def test_a_leased_unit_with_an_open_link_still_shows(client, conn, staff, pmc):
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit 8")
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 8")
    open_lease(conn, pmc.pmc_id, unit, TODAY - timedelta(days=10), None, "1500.00", [tenant])
    link = open_link_for(client, unit, "1550.00")

    (shown,) = vacancies(client)["items"]

    assert (shown["unit"], shown["available_on"], shown["link_id"], shown["last_rent"]) == (
        "Unit 8",
        None,
        link["id"],
        "1500.00",
    )


def test_another_pmcs_units_are_not_on_listings(client, other_client, conn, app_conn, staff, pmc):
    elsewhere = make_pmc(conn)
    add_unit(conn, elsewhere.pmc_id, elsewhere.owners[0].property_id, "Theirs")
    join_as_staff(other_client, conn, app_conn, elsewhere, "Staff 2")

    assert vacancies(client)["total"] == 0
    assert vacancies(other_client)["total"] == 1


def test_a_listing_at_once_with_a_bad_description_opens_no_link(client, conn, staff, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)

    answer = client.post(
        "/api/leasing/links",
        json={"unit_id": str(unit), "monthly_rent": "1500.00", "listing": {"description": " "}},
    )

    assert answer.status_code == 422
    assert vacancies(client)["items"][0]["link_id"] is None


def open_link_for(client, unit, rent):
    answer = client.post("/api/leasing/links", json={"unit_id": str(unit), "monthly_rent": rent})
    assert answer.status_code == 201, answer.text
    return answer.json()
