"""Staff's side of leasing basics (src/pms/api/leasing.py): application links, screening through
the server's partner (the fake one here), decisions, and adverse action notices."""

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from helpers import add_unit, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

from pms.api import Settings, create_app


@pytest.fixture
def settings(database_url):
    return Settings(
        database_url=database_url,
        public_url="http://testserver",
        cookie_secure=False,
        screening_partner="fake",
    )


@pytest.fixture
def unit(conn, pmc):
    return add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def open_link(client, unit, rent="1500.00"):
    answer = client.post("/api/leasing/links", json={"unit_id": str(unit), "monthly_rent": rent})
    assert answer.status_code == 201, answer.text
    return answer.json()


def apply(other_client, link, income="4500.00", emails=("applicant.a@example.com",)):
    answer = other_client.post(
        "/api/apply",
        json={
            "token": link["url"].split("#", 1)[1],
            "move_in_on": (date.today() + timedelta(days=30)).isoformat(),
            "monthly_income": income,
            "applicants": [
                {"name": f"Applicant {n}", "email": email} for n, email in enumerate(emails, 1)
            ],
        },
    )
    assert answer.status_code == 201, answer.text
    return answer.json()


def only_application(client):
    (found,) = client.get("/api/leasing/applications").json()
    return found


def notice(reasons=("Household income is less than twice the rent",)):
    return {"reasons": list(reasons), "notice_given_on": date.today().isoformat()}


def test_staff_open_a_link_that_takes_applications_and_close_it(client, other_client, staff, unit):
    link = open_link(client, unit)
    listed = client.get("/api/leasing/links").json()
    apply(other_client, link)
    counted = client.get("/api/leasing/links").json()[0]["applications"]
    closed = client.post(f"/api/leasing/links/{link['id']}/close", json={})
    again = client.post(f"/api/leasing/links/{link['id']}/close", json={})

    assert link["url"].startswith("http://testserver/apply#")
    assert [
        (item["id"], item["unit"], item["monthly_rent"], item["applications"]) for item in listed
    ] == [(link["id"], "Unit 1", "1500.00", 0)]
    assert counted == 1
    assert closed.status_code == 204
    assert client.get("/api/leasing/links").json() == []
    assert (again.status_code, again.json()["detail"]) == (409, "this link is closed already")


def test_staff_see_their_units_and_which_are_leased(client, conn, staff, pmc, unit):
    leased = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2")
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    open_lease(conn, pmc.pmc_id, leased, date(2026, 1, 1), None, "1200", [tenant])

    units = client.get("/api/leasing/units").json()

    assert [(u["unit"], u["leased_today"]) for u in units] == [("Unit 1", False), ("Unit 2", True)]


@pytest.mark.parametrize(
    ("income", "recommendation", "reasons"),
    [
        ("4500.00", "approve", []),
        ("4499.99", "conditional", ["Household income is less than three times the rent"]),
        ("3000.00", "conditional", ["Household income is less than three times the rent"]),
        ("2999.99", "decline", ["Household income is less than twice the rent"]),
    ],
)
def test_the_fake_partner_screens_on_income_against_rent(
    client, other_client, staff, unit, income, recommendation, reasons
):
    apply(other_client, open_link(client, unit), income)
    application = only_application(client)

    screened = client.post(f"/api/leasing/applications/{application['id']}/screen", json={})

    assert screened.status_code == 201, screened.text
    (screening,) = screened.json()["screenings"]
    assert (screening["partner"], screening["recommendation"], screening["reasons"]) == (
        "Fake Screening Partner",
        recommendation,
        reasons,
    )
    assert screening["reference"].startswith("fake-")


def test_without_a_partner_screening_is_refused(conn, app_conn, database_url, pmc, unit):
    settings = Settings(
        database_url=database_url, public_url="http://testserver", cookie_secure=False
    )
    with TestClient(create_app(settings)) as bare:
        join_as_staff(bare, conn, app_conn, pmc)
        apply(bare, open_link(bare, unit))
        application = only_application(bare)

        answer = bare.post(f"/api/leasing/applications/{application['id']}/screen", json={})

    assert (answer.status_code, answer.json()["detail"]) == (
        503,
        "no screening partner is set up on this server",
    )


def test_a_decline_on_a_screening_records_its_notice_and_the_notice_downloads(
    client, conn, other_client, staff, unit
):
    apply(other_client, open_link(client, unit), "1000.00")
    application = only_application(client)
    screened = client.post(f"/api/leasing/applications/{application['id']}/screen", json={})
    screening = screened.json()["screenings"][0]["id"]
    decide = f"/api/leasing/applications/{application['id']}/decide"

    without = client.post(decide, json={"decision": "declined", "screening_id": screening})
    decided = client.post(
        decide, json={"decision": "declined", "screening_id": screening, "adverse_action": notice()}
    )
    pdf = client.get(f"/api/leasing/applications/{application['id']}/notice")

    assert without.status_code == 422
    assert decided.status_code == 201, decided.text
    assert decided.json()["decision"]["adverse_action"] == notice()
    assert client.get("/api/leasing/applications").json() == []
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    assert pdf.headers["content-disposition"] == 'attachment; filename="adverse-action-notice.pdf"'
    staff_named = conn.execute(
        "SELECT staff_id FROM trust_audit_log WHERE table_name = 'trust_adverse_actions'"
        " AND row_data ->> 'application_id' = %s",
        (application["id"],),
    ).fetchone()[0]
    assert str(staff_named) == str(staff.person_id)


def test_a_conditional_approval_says_its_conditions(client, other_client, staff, unit):
    apply(other_client, open_link(client, unit), "3500.00")
    application = only_application(client)
    screening = client.post(
        f"/api/leasing/applications/{application['id']}/screen", json={}
    ).json()["screenings"][0]["id"]
    decide = f"/api/leasing/applications/{application['id']}/decide"
    base = {"decision": "conditional", "screening_id": screening, "adverse_action": notice()}

    answers = [
        client.post(decide, json=base),
        client.post(decide, json=base | {"conditions": "   "}),
        client.post(decide, json=base | {"conditions": "One more month's deposit"}),
    ]

    assert [a.status_code for a in answers] == [422, 422, 201]
    assert answers[2].json()["decision"]["conditions"] == "One more month's deposit"


def test_an_approval_needs_no_notice_and_takes_none(client, other_client, staff, unit):
    apply(other_client, open_link(client, unit))
    application = only_application(client)
    decide = f"/api/leasing/applications/{application['id']}/decide"

    with_notice = client.post(decide, json={"decision": "approved", "adverse_action": notice()})
    approved = client.post(decide, json={"decision": "approved"})
    twice = client.post(decide, json={"decision": "declined"})
    screened_after = client.post(f"/api/leasing/applications/{application['id']}/screen", json={})
    no_notice = client.get(f"/api/leasing/applications/{application['id']}/notice")

    assert with_notice.status_code == 422
    assert approved.status_code == 201
    assert (twice.status_code, twice.json()["detail"]) == (
        409,
        "this application is decided already",
    )
    assert screened_after.status_code == 409
    assert no_notice.status_code == 404


def test_a_decision_rests_only_on_its_own_applications_screening(client, other_client, staff, unit):
    link = open_link(client, unit)
    apply(other_client, link, emails=("first@example.com",))
    apply(other_client, link, emails=("second@example.com",))
    second, first = client.get("/api/leasing/applications").json()  # newest first
    screening = client.post(f"/api/leasing/applications/{first['id']}/screen", json={}).json()[
        "screenings"
    ][0]["id"]

    answer = client.post(
        f"/api/leasing/applications/{second['id']}/decide",
        json={"decision": "approved", "screening_id": screening},
    )

    assert (answer.status_code, answer.json()["detail"]) == (
        422,
        "that screening isn't one of this application's",
    )


@pytest.mark.parametrize(
    ("action", "detail"),
    [
        (notice(reasons=["   "]), "every reason says something"),
        (
            {"reasons": ["x"], "notice_given_on": (date.today() + timedelta(days=1)).isoformat()},
            "a notice is given on a day from 30 days ago to today",
        ),
        (
            {"reasons": ["x"], "notice_given_on": (date.today() - timedelta(days=31)).isoformat()},
            "a notice is given on a day from 30 days ago to today",
        ),
        ({"reasons": [], "notice_given_on": date.today().isoformat()}, None),
    ],
)
def test_a_bad_notice_is_refused(client, other_client, staff, unit, action, detail):
    apply(other_client, open_link(client, unit), "1000.00")
    application = only_application(client)
    screening = client.post(
        f"/api/leasing/applications/{application['id']}/screen", json={}
    ).json()["screenings"][0]["id"]

    answer = client.post(
        f"/api/leasing/applications/{application['id']}/decide",
        json={"decision": "declined", "screening_id": screening, "adverse_action": action},
    )

    assert answer.status_code == 422
    if detail:
        assert answer.json()["detail"] == detail
    assert only_application(client)["decision"] is None


def test_another_pmcs_units_links_and_applications_are_not_found(
    client, other_client, conn, app_conn, staff, unit
):
    elsewhere = make_pmc(conn)
    their_unit = add_unit(conn, elsewhere.pmc_id, elsewhere.owners[0].property_id)
    join_as_staff(other_client, conn, app_conn, elsewhere, "Staff 2")
    their_link = open_link(other_client, their_unit)
    apply(other_client, their_link)
    (theirs,) = other_client.get("/api/leasing/applications").json()

    answers = [
        client.post("/api/leasing/links", json={"unit_id": str(their_unit), "monthly_rent": "1"}),
        client.post(f"/api/leasing/links/{their_link['id']}/close", json={}),
        client.post(f"/api/leasing/applications/{theirs['id']}/screen", json={}),
        client.post(
            f"/api/leasing/applications/{theirs['id']}/decide", json={"decision": "approved"}
        ),
        client.get(f"/api/leasing/applications/{theirs['id']}/notice"),
    ]

    assert [a.status_code for a in answers] == [404] * 5
    assert client.get("/api/leasing/applications", params={"show": "all"}).json() == []
    assert client.get("/api/leasing/links").json() == []


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_reach_the_leasing_endpoints(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    expected = 401 if caller == "nobody" else 403
    some = uuid.uuid4()

    answers = [
        client.get("/api/leasing/units"),
        client.get("/api/leasing/links"),
        client.post("/api/leasing/links", json={"unit_id": str(some), "monthly_rent": "1"}),
        client.post(f"/api/leasing/links/{some}/close", json={}),
        client.get("/api/leasing/applications"),
        client.post(f"/api/leasing/applications/{some}/screen", json={}),
        client.post(f"/api/leasing/applications/{some}/decide", json={"decision": "approved"}),
        client.get(f"/api/leasing/applications/{some}/notice"),
    ]

    assert [a.status_code for a in answers] == [expected] * 8


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/leasing/links", {"unit_id": "not-a-unit", "monthly_rent": "1500"}),
        ("post", "/api/leasing/links", {"unit_id": str(uuid.uuid4()), "monthly_rent": "0"}),
        ("post", "/api/leasing/links", {"unit_id": str(uuid.uuid4()), "monthly_rent": "1.001"}),
        ("get", "/api/leasing/applications?show=some", None),
        ("post", "/api/leasing/applications/not-one/decide", {"decision": "approved"}),
        ("post", f"/api/leasing/applications/{uuid.uuid4()}/decide", {"decision": "maybe"}),
    ],
)
def test_bad_leasing_input_is_refused(client, staff, method, path, body):
    answer = client.get(path) if method == "get" else client.post(path, json=body)

    assert answer.status_code == 422


def test_an_unknown_application_or_link_is_not_found(client, staff):
    some = uuid.uuid4()

    answers = [
        client.post(f"/api/leasing/links/{some}/close", json={}),
        client.post(f"/api/leasing/applications/{some}/screen", json={}),
        client.post(f"/api/leasing/applications/{some}/decide", json={"decision": "approved"}),
        client.get(f"/api/leasing/applications/{some}/notice"),
    ]

    assert [a.status_code for a in answers] == [404] * 4
