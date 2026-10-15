"""Vendors (src/pms/api/vendors.py): each vendor's W-9 and insurance as of a day, as
trust_report_vendor_insurance has it, and recording a policy."""

from datetime import date, timedelta

import pytest
from helpers import add_document, make_pmc
from people import join_as_owner, join_as_staff

TODAY = date.today()


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert detail in str(answer.json()["detail"]), answer.text


def policy(**change):
    return {
        "coverage": "general liability",
        "carrier": "Carrier A",
        "policy_number": "GL-1",
        "starts_on": str(TODAY - timedelta(days=300)),
        "expires_on": str(TODAY + timedelta(days=65)),
        "limit_amount": "1000000",
    } | change


def test_staff_record_a_policy_and_see_how_it_stands(client, conn, pmc, staff):
    vendor = pmc.vendor_id
    ok(client.post(f"/api/vendors/{vendor}/insurance", json=policy()), 201)
    ok(
        client.post(
            f"/api/vendors/{vendor}/insurance",
            json=policy(
                coverage="commercial auto",
                policy_number="AU-1",
                carrier=" Carrier C ",
                expires_on=str(TODAY + timedelta(days=10)),
                limit_amount=None,
            ),
        ),
        201,
    )
    add_document(conn, pmc.pmc_id, "w9", vendor)

    (found,) = ok(client.get("/api/vendors"))

    assert (found["name"], found["w9_on_file"]) == ("Vendor 1", True)
    assert [
        (p["coverage"], p["carrier"], p["limit_amount"], p["standing"]) for p in found["insurance"]
    ] == [
        ("commercial auto", "Carrier C", None, "expires in 10 days"),
        ("general liability", "Carrier A", "1000000.00", "current"),
    ]
    # As of a later day it has run out.
    later = ok(client.get("/api/vendors", params={"on": str(TODAY + timedelta(days=11))}))
    assert [p["standing"] for p in later[0]["insurance"]] == ["expired", "current"]
    # The audit log names who recorded it.
    assert conn.execute(
        "SELECT count(*) FROM trust_vendor_insurance WHERE recorded_by = %s", (staff.person_id,)
    ).fetchone() == (2,)


def test_a_vendor_with_no_policy_says_so(client, conn, pmc, staff):
    (found,) = ok(client.get("/api/vendors"))
    assert (found["w9_on_file"], found["insurance"]) == (False, [])
    # A W-9 kept and then removed is not on file.
    w9 = add_document(conn, pmc.pmc_id, "w9", pmc.vendor_id)
    assert ok(client.get("/api/vendors"))[0]["w9_on_file"] is True
    conn.execute(
        "INSERT INTO trust_document_removals (document_id, pmc_id) VALUES (%s, %s)",
        (w9, pmc.pmc_id),
    )
    assert ok(client.get("/api/vendors"))[0]["w9_on_file"] is False


def test_expiring_insurance_waits_on_today(client, pmc, staff):
    """Today lists every coverage that isn't current: expiring and expired, not current."""
    for coverage, expires in (
        ("general liability", TODAY),
        ("commercial auto", TODAY - timedelta(days=1)),
        ("workers compensation", TODAY + timedelta(days=31)),
    ):
        ok(
            client.post(
                f"/api/vendors/{pmc.vendor_id}/insurance",
                json=policy(coverage=coverage, expires_on=str(expires)),
            ),
            201,
        )
    day = ok(client.get("/api/today"))
    assert [(x["vendor"], x["coverage"], x["standing"]) for x in day["vendor_insurance"]] == [
        ("Vendor 1", "commercial auto", "expired"),
        ("Vendor 1", "general liability", "expires today"),
    ]


def test_only_the_pmcs_staff_record_or_see_insurance(
    client, other_client, conn, app_conn, pmc, staff
):
    path = f"/api/vendors/{pmc.vendor_id}/insurance"
    refused(other_client.get("/api/vendors"), 401)
    refused(other_client.post(path, json=policy()), 401)
    join_as_owner(other_client, app_conn, pmc)
    refused(other_client.post(path, json=policy()), 403, "only the PMC's staff")

    other = make_pmc(conn)
    other_client.post("/api/sign-out", json={})
    join_as_staff(other_client, conn, app_conn, other, name="Staff 2")
    refused(other_client.post(path, json=policy()), 404, "no such vendor")
    assert [v["name"] for v in ok(other_client.get("/api/vendors"))] == ["Vendor 1"]
    assert ok(other_client.get("/api/vendors"))[0]["insurance"] == []


@pytest.mark.parametrize(
    "change",
    [
        {"coverage": "flood"},
        {"carrier": " "},
        {"policy_number": "x" * 101},
        {"expires_on": str(TODAY - timedelta(days=301))},
        {"limit_amount": "0"},
        {"limit_amount": "1.001"},
        {"starts_on": "soon"},
    ],
)
def test_a_policy_names_a_coverage_carrier_number_and_days_that_run_forward(
    client, conn, pmc, staff, change
):
    refused(client.post(f"/api/vendors/{pmc.vendor_id}/insurance", json=policy(**change)), 422)
    assert conn.execute(
        "SELECT count(*) FROM trust_vendor_insurance WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)
