"""A vendor's private link through the API (migration 0086; the founder's review, q069;
src/pms/api/vendor_links.py): staff make and take back a vendor's link; with only the link, the
vendor sees their open work orders, schedules a visit and says the work is done. Every name here
is synthetic."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from helpers import add_tenant, add_unit, make_pmc, open_lease
from people import join_as, join_as_owner, join_as_staff

from pms.api import vendor_links

TODAY = datetime.now(UTC).date()


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def make_link(client, vendor):
    url = ok(client.post(f"/api/vendors/{vendor}/link", json={}), 201)["url"]
    assert "/vendor#" in url
    return url.split("#", 1)[1]


def work_order(client, pmc, unit, summary, vendor=None, **details):
    order = ok(
        client.post(
            "/api/work-orders",
            json={
                "property_id": str(pmc.owners[0].property_id),
                "unit_id": str(unit),
                "summary": summary,
                **details,
            },
        ),
        201,
    )["id"]
    if vendor is not None:
        ok(
            client.post(
                f"/api/maintenance/work-orders/{order}/steps",
                json={"step": "assigned", "vendor_id": str(vendor)},
            ),
            201,
        )
    return order


@pytest.fixture
def jobs(client, other_client, conn, staff, pmc):
    """Two work orders assigned to the PMC's vendor (one due, entry call first), one to another
    vendor, one of the vendor's closed."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    other = conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor 2') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    leak = work_order(
        client,
        pmc,
        unit,
        "Leak",
        pmc.vendor_id,
        priority="high",
        due_on=str(TODAY + timedelta(days=3)),
        entry="call first",
        description="Under the sink.",
    )
    gate = work_order(client, pmc, unit, "Gate", pmc.vendor_id)
    theirs = work_order(client, pmc, unit, "Roof", other)
    done = work_order(client, pmc, unit, "Done already", pmc.vendor_id)
    ok(client.post(f"/api/maintenance/work-orders/{done}/steps", json={"step": "completed"}), 201)
    return {"unit": unit, "leak": leak, "gate": gate, "theirs": theirs, "done": done}


def test_a_vendor_sees_only_their_open_work_orders_through_their_link(
    client, other_client, conn, pmc, jobs
):
    token = make_link(client, pmc.vendor_id)
    (company,) = conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()

    seen = ok(other_client.post("/api/vendor-link/work-orders", json={"token": token}))

    assert (seen["vendor"], seen["company"]) == ("Vendor 1", company)
    assert [w["summary"] for w in seen["work_orders"]] == ["Leak", "Gate"]
    leak = seen["work_orders"][0]
    assert (leak["priority"], leak["due_on"], leak["description"], leak["status"]) == (
        "high",
        str(TODAY + timedelta(days=3)),
        "Under the sink.",
        "assigned",
    )
    assert leak["entry"] == f"Call {company} first to arrange entry."
    assert (leak["property"], leak["unit"]) == ("Property 1", "Unit 1")


def test_the_vendor_schedules_a_visit_and_says_the_work_is_done(
    client, other_client, conn, app_conn, pmc, jobs
):
    token = make_link(client, pmc.vendor_id)
    visit = str(TODAY + timedelta(days=2))
    steps = f"/api/vendor-link/work-orders/{jobs['leak']}/steps"

    scheduled = ok(
        other_client.post(
            steps,
            json={
                "token": token,
                "step": "scheduled",
                "scheduled_for": visit,
                "visit_window": " 8-10 am ",
            },
        ),
        201,
    )
    done = ok(
        other_client.post(steps, json={"token": token, "step": "completed", "note": " Fixed. "}),
        201,
    )
    page = ok(client.get(f"/api/work-orders/{jobs['leak']}"))

    assert [
        (w["summary"], w["status"], w["visit_on"], w["visit_window"])
        for w in scheduled["work_orders"]
    ] == [
        ("Leak", "scheduled", visit, "8-10 am"),
        ("Gate", "assigned", None, None),
    ]
    assert [w["summary"] for w in done["work_orders"]] == ["Gate"]
    assert page["status"] == "completed"
    assert [(s["step"], s["by_vendor"], s["note"]) for s in page["steps"]] == [
        ("assigned", False, None),
        ("scheduled", True, None),
        ("completed", True, "Fixed."),
    ]
    # Recorded through the link: no staff member is named.
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_work_order_steps'"
        " AND row_data ->> 'work_order_id' = %s AND staff_id IS NULL",
        (jobs["leak"],),
    ).fetchone() == (2,)


@pytest.mark.parametrize(
    ("body", "status", "detail"),
    [
        ({"step": "scheduled"}, 422, "a schedule names the day you come, and only it does"),
        (
            {"step": "completed", "scheduled_for": str(TODAY)},
            422,
            "a schedule names the day you come, and only it does",
        ),
        (
            {"step": "completed", "visit_window": "8-10 am"},
            422,
            "only a schedule says when in the day you come",
        ),
        ({"step": "cancelled"}, 422, None),
        ({"step": "completed", "note": "x" * 2001}, 422, None),
    ],
)
def test_a_bad_vendor_step_is_refused(client, other_client, pmc, jobs, body, status, detail):
    token = make_link(client, pmc.vendor_id)

    answer = other_client.post(
        f"/api/vendor-link/work-orders/{jobs['leak']}/steps", json={"token": token, **body}
    )

    assert answer.status_code == status, answer.text
    if detail:
        assert answer.json()["detail"] == detail


def test_another_vendors_or_a_closed_work_order_is_not_found(client, other_client, pmc, jobs):
    token = make_link(client, pmc.vendor_id)

    answers = [
        other_client.post(
            f"/api/vendor-link/work-orders/{jobs[name]}/steps",
            json={"token": token, "step": "completed"},
        )
        for name in ("theirs", "done")
    ] + [
        other_client.post(
            f"/api/vendor-link/work-orders/{uuid.uuid4()}/steps",
            json={"token": token, "step": "completed"},
        )
    ]

    assert [(a.status_code, a.json()["detail"]) for a in answers] == [
        (404, "no such work order of yours")
    ] * 3


def test_a_step_the_database_refuses_says_why(client, other_client, conn, pmc, jobs, monkeypatch):
    """Read as the vendor's just before staff gave the job to another vendor, a work order takes
    no step through the link: the database refuses it and the vendor reads why."""
    token = make_link(client, pmc.vendor_id)
    real = vendor_links.open_orders

    def stale(conn_, pmc_id, vendor, company):
        found = real(conn_, pmc_id, vendor, company)
        return found + [SimpleNamespace(id=uuid.UUID(jobs["theirs"]))] if company == "" else found

    monkeypatch.setattr(vendor_links, "open_orders", stale)
    answer = other_client.post(
        f"/api/vendor-link/work-orders/{jobs['theirs']}/steps",
        json={"token": token, "step": "completed"},
    )

    assert (answer.status_code, answer.json()["detail"]) == (
        409,
        "this work order is not assigned to the link's vendor",
    )
    assert conn.execute(
        "SELECT count(*) FROM trust_work_order_steps"
        " WHERE work_order_id = %s AND step = 'completed'",
        (jobs["theirs"],),
    ).fetchone() == (0,)


def test_a_new_link_replaces_the_old_and_a_taken_back_one_opens_nothing(
    client, other_client, pmc, jobs
):
    before = ok(client.get(f"/api/vendors/{pmc.vendor_id}/link"))
    old = make_link(client, pmc.vendor_id)
    new = make_link(client, pmc.vendor_id)
    standing = ok(client.get(f"/api/vendors/{pmc.vendor_id}/link"))
    replaced = other_client.post("/api/vendor-link/work-orders", json={"token": old})
    still = other_client.post("/api/vendor-link/work-orders", json={"token": new})
    ok(client.post(f"/api/vendors/{pmc.vendor_id}/link/revoke", json={}), 204)
    taken = other_client.post("/api/vendor-link/work-orders", json={"token": new})
    again = client.post(f"/api/vendors/{pmc.vendor_id}/link/revoke", json={})
    after = ok(client.get(f"/api/vendors/{pmc.vendor_id}/link"))

    assert before == {"made_at": None}
    assert standing["made_at"] is not None
    assert (replaced.status_code, replaced.json()["detail"]) == (
        404,
        "this link was taken back or doesn't exist",
    )
    assert still.status_code == 200
    assert taken.status_code == 404
    assert (again.status_code, again.json()["detail"]) == (
        409,
        "this vendor has no link to take back",
    )
    assert after == {"made_at": None}


@pytest.mark.parametrize("token", ["x" * 19, "y" * 101, "an-unknown-token-of-enough-length"])
def test_a_bad_or_unknown_token_opens_nothing(other_client, token):
    answer = other_client.post("/api/vendor-link/work-orders", json={"token": token})

    assert answer.status_code == (404 if len(token) in range(20, 101) else 422)


def test_another_companys_vendor_is_not_found(client, conn, staff):
    stranger = make_pmc(conn).vendor_id

    answers = [
        client.get(f"/api/vendors/{stranger}/link"),
        client.post(f"/api/vendors/{stranger}/link", json={}),
        client.post(f"/api/vendors/{stranger}/link/revoke", json={}),
    ]

    assert [(a.status_code, a.json()["detail"]) for a in answers] == [(404, "no such vendor")] * 3


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_make_or_take_back_a_link(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        prop = pmc.owners[0].property_id
        tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 1")
        open_lease(
            conn,
            pmc.pmc_id,
            add_unit(conn, pmc.pmc_id, prop, "Unit 9"),
            TODAY,
            None,
            "900.00",
            [tenant],
        )
        join_as(client, app_conn, pmc.pmc_id, "tenant", tenant)
    expected = 401 if caller == "nobody" else 403
    vendor = pmc.vendor_id

    answers = [
        client.get(f"/api/vendors/{vendor}/link"),
        client.post(f"/api/vendors/{vendor}/link", json={}),
        client.post(f"/api/vendors/{vendor}/link/revoke", json={}),
    ]

    assert [a.status_code for a in answers] == [expected] * 3


def test_a_bad_vendor_id_is_refused(client, staff):
    assert client.get("/api/vendors/not-an-id/link").status_code == 422
    assert client.post("/api/vendors/not-an-id/link", json={}).status_code == 422
