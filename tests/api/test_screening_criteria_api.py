"""A company's screening criteria and decisions against a screening through the API (migration
0089; the founder's review, q085; src/pms/api/screening_criteria.py, src/pms/api/leasing.py), with
the fake screening partner. Every name here is synthetic."""

from datetime import date, timedelta

import pytest
from helpers import add_unit
from people import join_as_owner, join_as_staff, join_as_tenant

from pms.api import Settings

CRITERIA = "/api/leasing/screening-criteria"


@pytest.fixture
def settings(database_url):
    return Settings(
        database_url=database_url,
        public_url="http://testserver",
        cookie_secure=False,
        screening_partner="fake",
    )


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def screened(client, other_client, conn, pmc, income):
    """An application at $1,000.00 a month with this household income, screened: its id and its
    one screening."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    link = ok(
        client.post("/api/leasing/links", json={"unit_id": str(unit), "monthly_rent": "1000.00"}),
        201,
    )
    ok(
        other_client.post(
            "/api/apply",
            json={
                "token": link["url"].split("#", 1)[1],
                "move_in_on": (date.today() + timedelta(days=30)).isoformat(),
                "monthly_income": income,
                "applicants": [{"name": "Applicant 1", "email": "applicant.1@example.com"}],
            },
        ),
        201,
    )
    (found,) = [
        a
        for a in ok(client.get("/api/leasing/applications"))["items"]
        if a["monthly_income"] == income
    ]
    done = ok(client.post(f"/api/leasing/applications/{found['id']}/screen", json={}), 201)
    return found["id"], done["screenings"][-1], link


def test_without_its_own_the_company_screens_by_the_partners_criteria(client, staff):
    assert ok(client.get(CRITERIA)) == {
        "id": None,
        "approve_multiple": "3.00",
        "conditional_multiple": "2.00",
        "written": None,
        "recorded_at": None,
        "recorded_by": None,
    }


def test_the_partner_applies_the_companys_criteria_and_the_apply_page_shows_them(
    client, other_client, conn, staff, pmc
):
    written = "Household income of 2.5 times the rent; 1.5 times with a co-signer."
    made = ok(
        client.post(
            CRITERIA,
            json={"approve_multiple": "2.5", "conditional_multiple": "1.5", "written": written},
        ),
        201,
    )
    again = ok(
        client.post(
            CRITERIA,
            json={"approve_multiple": "2.50", "conditional_multiple": "1.50", "written": written},
        ),
        201,
    )

    results = {
        income: screened(client, other_client, conn, pmc, income)
        for income in ("2500.00", "2499.99", "1499.99")
    }
    view = ok(
        other_client.post(
            "/api/apply/view", json={"token": results["2500.00"][2]["url"].split("#")[1]}
        )
    )

    assert (made["approve_multiple"], made["conditional_multiple"], made["written"]) == (
        "2.50",
        "1.50",
        written,
    )
    assert made["recorded_by"] == "Staff 1"
    assert again == made
    assert conn.execute(
        "SELECT count(*) FROM trust_screening_criteria WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (1,)
    assert [(s["recommendation"], s["reasons"]) for _, s, _ in results.values()] == [
        ("approve", []),
        ("conditional", ["Household income is less than 2.5 times the rent"]),
        ("decline", ["Household income is less than 1.5 times the rent"]),
    ]
    assert conn.execute(
        "SELECT count(*) FROM trust_screenings WHERE pmc_id = %s AND criteria_id = %s",
        (pmc.pmc_id, made["id"]),
    ).fetchone() == (3,)
    assert view["criteria"] == written


def test_the_latest_criteria_hold(client, other_client, conn, staff, pmc):
    ok(client.post(CRITERIA, json={"approve_multiple": "2", "conditional_multiple": "1"}), 201)
    latest = ok(
        client.post(CRITERIA, json={"approve_multiple": "4", "conditional_multiple": "3"}), 201
    )

    _, screening, _ = screened(client, other_client, conn, pmc, "3500.00")

    assert ok(client.get(CRITERIA)) == latest
    assert (latest["approve_multiple"], latest["conditional_multiple"]) == ("4.00", "3.00")
    assert screening["recommendation"] == "conditional"


def test_a_decision_against_the_recommendation_says_why(client, other_client, conn, staff, pmc):
    application, screening, _ = screened(client, other_client, conn, pmc, "1500.00")  # decline
    path = f"/api/leasing/applications/{application}/decide"

    silent = client.post(path, json={"decision": "approved"})
    decided = ok(
        client.post(
            path, json={"decision": "approved", "override_reason": " A co-signer earns enough "}
        ),
        201,
    )

    assert screening["recommendation"] == "decline"
    assert (silent.status_code, silent.json()["detail"]) == (
        422,
        "a decision against the latest screening's recommendation says why,"
        " and no other decision does",
    )
    assert decided["decision"]["override_reason"] == "A co-signer earns enough"


def test_a_decision_follows_the_latest_screening(client, other_client, conn, staff, pmc):
    application, first, _ = screened(client, other_client, conn, pmc, "2500.00")  # 3 and 2 times
    ok(client.post(CRITERIA, json={"approve_multiple": "2", "conditional_multiple": "1"}), 201)
    again = ok(client.post(f"/api/leasing/applications/{application}/screen", json={}), 201)

    decided = ok(
        client.post(
            f"/api/leasing/applications/{application}/decide", json={"decision": "approved"}
        ),
        201,
    )

    assert [s["recommendation"] for s in again["screenings"]] == ["conditional", "approve"]
    assert first["recommendation"] == "conditional"
    assert decided["decision"]["override_reason"] is None


def test_a_decision_with_the_recommendation_takes_no_reason(client, other_client, conn, staff, pmc):
    application, _, _ = screened(client, other_client, conn, pmc, "4500.00")  # approve
    path = f"/api/leasing/applications/{application}/decide"

    told = client.post(path, json={"decision": "approved", "override_reason": "Because"})
    decided = ok(client.post(path, json={"decision": "approved"}), 201)

    assert told.status_code == 422
    assert decided["decision"]["override_reason"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"approve_multiple": "0.99", "conditional_multiple": "0.50"},
        {"approve_multiple": "3.00", "conditional_multiple": "3.01"},
        {"approve_multiple": "10.01", "conditional_multiple": "2.00"},
        {"approve_multiple": "3.001", "conditional_multiple": "2.00"},
        {"approve_multiple": "3.00", "conditional_multiple": "-1"},
        {"approve_multiple": "3.00", "conditional_multiple": "2.00", "written": " "},
        {"approve_multiple": "3.00", "conditional_multiple": "2.00", "written": "x" * 4001},
        {"approve_multiple": "3.00"},
    ],
)
def test_bad_criteria_are_refused(client, staff, body):
    assert client.post(CRITERIA, json=body).status_code == 422


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_see_or_set_criteria(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    answers = [
        client.get(CRITERIA),
        client.post(CRITERIA, json={"approve_multiple": "3", "conditional_multiple": "2"}),
    ]

    assert [a.status_code for a in answers] == [401 if caller == "nobody" else 403] * 2
