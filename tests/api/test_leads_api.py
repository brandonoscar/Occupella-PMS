"""Leads and showings through the API (migration 0088; the founder's review, q083;
src/pms/api/leads.py): staff add a lead, set up showings, record how each went and close the lead.
Every name here is synthetic."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from helpers import add_staff, add_unit, make_pmc
from people import join_as_owner, join_as_staff, join_as_tenant

SOON = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def add(client, **fields):
    return client.post(
        "/api/leasing/leads",
        json={"name": "Prospect 1", "email": "prospect.1@example.com", **fields},
    )


def leads(client, **params):
    return ok(client.get("/api/leasing/leads", params=params))


def test_staff_add_a_lead_and_show_it_a_unit(client, conn, staff, pmc):
    place = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, place, "Unit 1")
    made = ok(
        add(
            client,
            name="  Prospect 1 ",
            phone="555-555-0100",
            property_id=str(place),
            unit_id=str(unit),
            source=" Website ",
            move_in_on=str(date.today() + timedelta(days=30)),
            note="Asked about parking.",
        ),
        201,
    )["id"]

    shown = ok(
        client.post(
            f"/api/leasing/leads/{made}/showings",
            json={
                "unit_id": str(unit),
                "starts_at": SOON.isoformat(),
                "shown_by": str(staff.person_id),
            },
        ),
        201,
    )
    # One already past is listed first and is never the next.
    past = ok(
        client.post(
            f"/api/leasing/leads/{made}/showings",
            json={"unit_id": str(unit), "starts_at": (SOON - timedelta(days=4)).isoformat()},
        ),
        201,
    )
    (row,) = leads(client)["items"]

    assert (shown["name"], shown["email"], shown["phone"], shown["source"]) == (
        "Prospect 1",
        "prospect.1@example.com",
        "555-555-0100",
        "Website",
    )
    assert (shown["property"], shown["unit"], shown["note"], shown["status"]) == (
        "Property 1",
        "Unit 1",
        "Asked about parking.",
        "open",
    )
    assert shown["added_by"] is not None and shown["closing"] is None
    (showing,) = shown["showing_list"]
    assert (showing["unit"], showing["shown_by"], showing["outcome"]) == (
        "Unit 1",
        shown["added_by"],
        None,
    )
    assert datetime.fromisoformat(showing["starts_at"]) == SOON
    assert [datetime.fromisoformat(s["starts_at"]) for s in past["showing_list"]] == [
        SOON - timedelta(days=4),
        SOON,
    ]
    assert (row["id"], row["showings"], row["status"]) == (made, 2, "open")
    assert datetime.fromisoformat(row["next_showing"]) == SOON


def test_how_a_showing_went_and_a_lead_closed_once(client, conn, staff, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    made = ok(add(client), 201)["id"]
    lead = ok(
        client.post(
            f"/api/leasing/leads/{made}/showings",
            json={"unit_id": str(unit), "starts_at": SOON.isoformat()},
        ),
        201,
    )
    showing = lead["showing_list"][0]["id"]

    went = ok(
        client.post(
            f"/api/leasing/showings/{showing}/outcome",
            json={"outcome": "no_show", "note": " Called twice. "},
        ),
        201,
    )
    again = client.post(f"/api/leasing/showings/{showing}/outcome", json={"outcome": "shown"})
    closed = ok(
        client.post(
            f"/api/leasing/leads/{made}/close", json={"outcome": "lost", "reason": " Moved away "}
        ),
        201,
    )
    closed_again = client.post(f"/api/leasing/leads/{made}/close", json={"outcome": "applied"})
    after = client.post(
        f"/api/leasing/leads/{made}/showings",
        json={"unit_id": str(unit), "starts_at": SOON.isoformat()},
    )

    assert (went["showing_list"][0]["outcome"], went["showing_list"][0]["note"]) == (
        "no_show",
        "Called twice.",
    )
    assert went["next_showing"] is None
    assert (again.status_code, again.json()["detail"]) == (
        409,
        "how this showing went is recorded already",
    )
    assert closed["status"] == "lost"
    assert (closed["closing"]["outcome"], closed["closing"]["reason"]) == ("lost", "Moved away")
    assert closed["closing"]["closed_by"] is not None
    assert (closed_again.status_code, closed_again.json()["detail"]) == (
        409,
        "this lead is closed already",
    )
    assert (after.status_code, after.json()["detail"]) == (409, "this lead is closed")
    assert leads(client)["items"] == []
    assert [r["status"] for r in leads(client, status="closed")["items"]] == ["lost"]
    assert leads(client, status="all")["total"] == 1


def test_leads_come_newest_first_a_page_at_a_time_and_by_property(client, conn, staff, pmc):
    first, second = pmc.owners[0].property_id, pmc.owners[1].property_id
    made = [
        ok(add(client, name=f"Prospect {n}", property_id=str(place)), 201)["id"]
        for n, place in ((1, first), (2, second), (3, first))
    ]
    ok(add(client, name="Prospect 4"), 201)  # asked about no property

    page = leads(client, per_page=2, page=2)
    of_first = leads(client, property=str(first))

    assert [r["name"] for r in leads(client)["items"]] == [
        "Prospect 4",
        "Prospect 3",
        "Prospect 2",
        "Prospect 1",
    ]
    assert (page["total"], [r["name"] for r in page["items"]]) == (4, ["Prospect 2", "Prospect 1"])
    assert [r["id"] for r in of_first["items"]] == [made[2], made[0]]


def test_a_lead_needs_a_way_to_reach_them_and_a_unit_of_its_property(client, conn, staff, pmc):
    first, second = pmc.owners[0].property_id, pmc.owners[1].property_id
    unit = add_unit(conn, pmc.pmc_id, first, "Unit 1")

    answers = [
        add(client, email=None),
        add(client, unit_id=str(unit)),
        add(client, property_id=str(second), unit_id=str(unit)),
    ]

    assert [(a.status_code, a.json()["detail"]) for a in answers] == [
        (422, "a lead needs an email or a phone"),
        (422, "a unit asked about is one of a property's; name the property"),
        (422, "that unit is not one of the property's"),
    ]


def test_another_companys_things_are_not_found(client, conn, staff, pmc):
    theirs = make_pmc(conn)
    their_unit = add_unit(conn, theirs.pmc_id, theirs.owners[0].property_id, "Unit 9")
    their_clerk = add_staff(conn, theirs.pmc_id, "Clerk 9")
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    made = ok(add(client), 201)["id"]

    def show(**fields):
        body = {"unit_id": str(unit), "starts_at": SOON.isoformat(), **fields}
        return client.post(f"/api/leasing/leads/{made}/showings", json=body)

    answers = [
        add(client, property_id=str(theirs.owners[0].property_id)),
        add(client, property_id=str(pmc.owners[0].property_id), unit_id=str(their_unit)),
        show(unit_id=str(their_unit)),
        show(shown_by=str(their_clerk)),
        client.get(f"/api/leasing/leads/{uuid.uuid4()}"),
        client.post(
            f"/api/leasing/leads/{uuid.uuid4()}/showings",
            json={"unit_id": str(unit), "starts_at": SOON.isoformat()},
        ),
        client.post(f"/api/leasing/showings/{uuid.uuid4()}/outcome", json={"outcome": "shown"}),
        client.post(f"/api/leasing/leads/{uuid.uuid4()}/close", json={"outcome": "lost"}),
    ]

    assert [(a.status_code, a.json()["detail"]) for a in answers] == [
        (404, "no such property"),
        (404, "no such unit"),
        (404, "no such unit"),
        (404, "no such staff member"),
        (404, "no such lead"),
        (404, "no such lead"),
        (404, "no such showing"),
        (404, "no such lead"),
    ]


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_see_or_change_leads(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    some = uuid.uuid4()

    answers = [
        client.get("/api/leasing/leads"),
        add(client),
        client.get(f"/api/leasing/leads/{some}"),
        client.post(f"/api/leasing/leads/{some}/showings", json={}),
        client.post(f"/api/leasing/showings/{some}/outcome", json={}),
        client.post(f"/api/leasing/leads/{some}/close", json={}),
    ]

    assert [a.status_code for a in answers] == [401 if caller == "nobody" else 403] * 6


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/leasing/leads", {"name": " ", "email": "prospect@example.com"}),
        ("/api/leasing/leads", {"name": "P", "email": "not an email"}),
        ("/api/leasing/leads", {"name": "P", "phone": "call me"}),
        ("/api/leasing/leads", {"name": "P", "email": "p@example.com", "source": "x" * 61}),
        ("/api/leasing/leads", {"name": "P", "email": "p@example.com", "note": "x" * 2001}),
        ("/api/leasing/leads", {"name": "P", "email": "p@example.com", "move_in_on": "soon"}),
        ("/api/leasing/leads/{lead}/showings", {"starts_at": "2026-10-20T15:00:00Z"}),
        ("/api/leasing/leads/{lead}/showings", {"unit_id": "{unit}", "starts_at": "tomorrow"}),
        ("/api/leasing/leads/{lead}/close", {"outcome": "won"}),
        ("/api/leasing/leads/{lead}/close", {"outcome": "lost", "reason": "x" * 501}),
        ("/api/leasing/showings/not-a-showing/outcome", {"outcome": "shown"}),
        ("/api/leasing/leads/not-a-lead/close", {"outcome": "lost"}),
    ],
)
def test_bad_lead_input_is_refused(client, conn, staff, pmc, path, body):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    made = ok(add(client), 201)["id"]
    filled = {k: v.format(unit=unit) if isinstance(v, str) else v for k, v in body.items()}

    answer = client.post(path.format(lead=made), json=filled)

    assert answer.status_code == 422


def test_bad_list_input_is_refused(client, staff):
    for params in ({"status": "won"}, {"per_page": 201}, {"page": 0}, {"property": "x"}):
        assert client.get("/api/leasing/leads", params=params).status_code == 422
    assert client.get("/api/leasing/leads/not-a-lead").status_code == 422
