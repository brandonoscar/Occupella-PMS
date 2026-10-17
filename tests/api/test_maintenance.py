"""Staff's side of maintenance requests (src/pms/api/maintenance.py): see the PMC's requests,
open a work order for one at its lease's unit, and record what happens to it."""

import threading
import time
import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from helpers import add_work_order, link_work_order, make_pmc
from people import join_as_owner, join_as_staff, join_as_tenant
from test_tenant import Home, new_request

from pms.api import maintenance


@pytest.fixture
def home(conn, pmc):
    return Home(conn, pmc)


@pytest.fixture
def filed(client, other_client, conn, app_conn, home):
    """Tenant 1's request, filed on `other_client`; staff are signed in on `client`."""
    join_as_tenant(other_client, conn, app_conn, home.pmc)
    request = other_client.post("/api/tenant/requests", json=new_request(home)).json()
    join_as_staff(client, conn, app_conn, home.pmc)
    return request


def open_order(client, request):
    # Every change is JSON from the app's own origin, even one with nothing to say.
    return client.post(f"/api/maintenance/requests/{request['id']}/work-order", json={})


def test_staff_see_open_requests_and_open_a_work_order(client, conn, filed, home):
    listed = client.get("/api/maintenance/requests").json()
    opened = open_order(client, filed)

    assert [(r["id"], r["status"], r["work_order_id"]) for r in listed] == [
        (filed["id"], "received", None)
    ]
    assert opened.status_code == 201, opened.text
    order = opened.json()
    assert (order["status"], order["filed_by"], order["unit"]) == ("opened", "Tenant 1", "Unit 1")
    assert conn.execute(
        "SELECT property_id, unit_id, summary FROM trust_work_orders WHERE id = %s",
        (order["work_order_id"],),
    ).fetchone() == (home.pmc.owners[0].property_id, home.unit, "Kitchen tap drips")


def test_a_work_order_names_the_staff_member_in_the_audit_log(client, conn, filed):
    order = open_order(client, filed).json()
    staff = conn.execute(
        "SELECT staff_id FROM trust_audit_log WHERE table_name = 'trust_work_orders'"
        " AND row_data ->> 'id' = %s",
        (order["work_order_id"],),
    ).fetchone()[0]

    assert staff is not None


def test_a_request_gets_one_work_order(client, filed):
    open_order(client, filed)

    again = open_order(client, filed)

    assert again.status_code == 409
    assert again.json()["detail"] == "this request already has a work order"


@pytest.mark.timing
def test_of_two_staff_opening_a_work_order_at_once_one_is_refused(
    client, conn, database_url, filed, home
):
    pmc_id = home.pmc.pmc_id
    with psycopg.connect(database_url) as first:
        theirs = add_work_order(
            first, pmc_id, home.pmc.owners[0].property_id, home.unit, "Theirs", datetime.now(UTC)
        )
        link_work_order(first, pmc_id, filed["id"], theirs)  # not committed yet
        answer: dict = {}
        thread = threading.Thread(target=lambda: answer.update(it=open_order(client, filed)))
        thread.start()
        for _ in range(1000):  # until the API's insert waits for this transaction
            if conn.execute(
                "SELECT 1 FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
                " AND query LIKE 'INSERT INTO trust_maintenance_request_work_orders%%'"
            ).fetchone():
                break
            time.sleep(0.01)
        else:
            raise AssertionError("the API never waited for the first work order")
        first.commit()
    thread.join(timeout=30)

    assert answer["it"].status_code == 409
    assert answer["it"].json()["detail"] == "this request already has a work order"
    assert client.get("/api/maintenance/requests").json()[0]["work_order_id"] == str(theirs)


def test_requests_are_narrowed_only_by_the_fixed_conditions(conn, home):
    with pytest.raises(ValueError, match="fixed conditions only"):
        maintenance.requests(conn, "true OR true", pmc=home.pmc.pmc_id)


def test_staff_assign_and_complete_and_the_request_closes(client, filed, home):
    order = open_order(client, filed).json()["work_order_id"]
    vendors = client.get("/api/maintenance/vendors").json()
    steps = f"/api/maintenance/work-orders/{order}/steps"

    assigned = client.post(steps, json={"step": "assigned", "vendor_id": vendors[0]["id"]})
    seen = client.get("/api/maintenance/requests").json()[0]
    scheduled = client.post(steps, json={"step": "scheduled", "scheduled_for": "2026-01-09"})
    visit = client.get("/api/maintenance/requests").json()[0]
    completed = client.post(steps, json={"step": "completed", "note": "Washer replaced"})

    assert vendors == [{"id": str(home.pmc.vendor_id), "name": vendors[0]["name"]}]
    assert (assigned.status_code, scheduled.status_code, completed.status_code) == (201,) * 3
    assert (seen["status"], seen["vendor"]) == ("assigned", vendors[0]["name"])
    # A scheduled visit is still the vendor's: the request names them.
    assert (visit["status"], visit["vendor"]) == ("scheduled", vendors[0]["name"])
    assert client.get("/api/maintenance/requests").json() == []
    everything = client.get("/api/maintenance/requests", params={"show": "all"}).json()
    assert [(r["status"], r["vendor"]) for r in everything] == [("completed", vendors[0]["name"])]


def test_nothing_follows_a_closed_work_order(client, filed):
    order = open_order(client, filed).json()["work_order_id"]
    steps = f"/api/maintenance/work-orders/{order}/steps"
    client.post(steps, json={"step": "cancelled", "note": "Tenant fixed it"})

    again = client.post(steps, json={"step": "completed"})

    assert again.status_code == 409
    assert "nothing follows that" in again.json()["detail"]


@pytest.mark.parametrize(
    ("step", "detail"),
    [
        ({"step": "assigned"}, "an assignment names a vendor, and only an assignment does"),
        ({"step": "scheduled"}, "a schedule names the day the vendor comes, and only it does"),
        ({"step": "completed", "vendor_id": None, "extra": 1}, None),
        ({"step": "painted"}, None),
        ({"step": "completed", "note": "x" * 2001}, None),
    ],
)
def test_a_bad_step_is_refused(client, filed, step, detail):
    order = open_order(client, filed).json()["work_order_id"]

    answer = client.post(f"/api/maintenance/work-orders/{order}/steps", json=step)

    if step.get("extra"):
        # Unknown fields are ignored; the step itself is fine.
        assert answer.status_code == 201
        return
    assert answer.status_code == 422
    if detail:
        assert answer.json()["detail"] == detail


def test_a_vendor_of_another_pmc_is_refused(client, conn, filed):
    order = open_order(client, filed).json()["work_order_id"]
    stranger = make_pmc(conn).vendor_id

    answer = client.post(
        f"/api/maintenance/work-orders/{order}/steps",
        json={"step": "assigned", "vendor_id": str(stranger)},
    )

    assert answer.status_code == 422
    assert answer.json()["detail"] == "no such vendor in this PMC"


def test_another_pmcs_requests_and_work_orders_are_not_found(
    client, other_client, conn, app_conn, filed
):
    elsewhere = Home(conn, make_pmc(conn))
    join_as_tenant(other_client, conn, app_conn, elsewhere.pmc)
    theirs = other_client.post("/api/tenant/requests", json=new_request(elsewhere)).json()
    join_as_staff(other_client, conn, app_conn, elsewhere.pmc, "Staff 2")
    their_order = open_order(other_client, theirs).json()["work_order_id"]

    answers = [
        open_order(client, theirs),
        open_order(client, {"id": str(uuid.uuid4())}),
        client.post(
            f"/api/maintenance/work-orders/{their_order}/steps", json={"step": "completed"}
        ),
    ]

    assert [a.status_code for a in answers] == [404, 404, 404]
    assert [r["id"] for r in client.get("/api/maintenance/requests").json()] == [filed["id"]]


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_reach_the_maintenance_endpoints(client, conn, app_conn, home, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, home.pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, home.pmc)
    expected = 401 if caller == "nobody" else 403
    some = uuid.uuid4()

    answers = [
        client.get("/api/maintenance/requests"),
        client.get("/api/maintenance/vendors"),
        client.post(f"/api/maintenance/requests/{some}/work-order", json={}),
        client.post(f"/api/maintenance/work-orders/{some}/steps", json={"step": "completed"}),
    ]

    assert [a.status_code for a in answers] == [expected] * 4


@pytest.mark.parametrize(
    ("method", "path", "params"),
    [
        ("get", "/api/maintenance/requests", {"show": "some"}),
        ("post", "/api/maintenance/requests/not-a-request/work-order", {}),
        ("post", "/api/maintenance/work-orders/not-an-order/steps", {}),
    ],
)
def test_bad_maintenance_input_is_refused(client, conn, app_conn, home, method, path, params):
    join_as_staff(client, conn, app_conn, home.pmc)

    if method == "get":
        answer = client.get(path, params=params)
    else:
        answer = client.post(path, json={"step": "completed"})

    assert answer.status_code == 422
