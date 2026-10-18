"""Maintenance through the API (migration 0084; the founder's review, q066, q070, q071, q074 and
q075): a tenant files a request as an emergency and with photos; its work order opens urgent with
the tenant's word on entry; each staff member on call hears of an emergency by email while the
server sends email; a visit's window reaches the vendor and the tenant; and each work order says
how many days it was open. Every name and address here is synthetic."""

import base64
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from helpers import JPEG, PDF, PNG, add_staff, add_work_order, make_pmc, step_work_order
from people import join_as, join_as_owner, join_as_staff, join_as_tenant
from test_tenant import Home, new_request

from pms import email, notices


@pytest.fixture
def home(conn, pmc):
    return Home(conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def photo(title, content=PNG, content_type="image/png"):
    return {
        "title": title,
        "content_type": content_type,
        "content_base64": base64.b64encode(content).decode(),
    }


def open_order(client, request_id):
    return ok(client.post(f"/api/maintenance/requests/{request_id}/work-order", json={}), 201)


def details(conn, work_order):
    return conn.execute(
        "SELECT priority, entry FROM trust_work_order_details WHERE work_order_id = %s",
        (work_order,),
    ).fetchall()


def emergencies(conn, pmc_id):
    return conn.execute(
        "SELECT staff_id, request_id, to_address, subject, body FROM trust_emails"
        " WHERE pmc_id = %s AND kind = 'emergency_request' ORDER BY to_address",
        (pmc_id,),
    ).fetchall()


# --- photos and an emergency ---------------------------------------------------------------------


def test_a_tenant_files_an_emergency_with_photos_and_its_work_order_opens_urgent(
    client, other_client, conn, app_conn, home
):
    join_as_tenant(other_client, conn, app_conn, home.pmc)
    filed = ok(
        other_client.post(
            "/api/tenant/requests",
            json=new_request(
                home,
                summary="Water through the ceiling",
                entry_allowed=False,
                emergency=True,
                photos=[photo("Ceiling"), photo("Floor", JPEG, "image/jpeg")],
            ),
        ),
        201,
    )
    join_as_staff(client, conn, app_conn, home.pmc)
    listed = ok(client.get("/api/maintenance/requests"))
    order = open_order(client, filed["id"])["work_order_id"]
    page = ok(client.get(f"/api/work-orders/{order}"))

    assert (filed["emergency"], filed["photos"]) == (True, 2)
    assert [(r["emergency"], r["photos"]) for r in listed] == [(True, 2)]
    # Urgent, and the tenant said to call first.
    assert details(conn, order) == [("urgent", "call first")]
    assert (page["priority"], page["entry"]) == ("urgent", "call first")
    assert (page["request"]["emergency"], page["request"]["entry_allowed"]) == (True, False)
    assert [p["title"] for p in page["request"]["photos"]] == ["Ceiling", "Floor"]
    # Each photo is a document about the request; staff download it as any document.
    kept = conn.execute(
        "SELECT kind, request_id, title FROM trust_documents WHERE request_id = %s"
        ' ORDER BY title COLLATE "C"',
        (filed["id"],),
    ).fetchall()
    assert kept == [
        ("request_photo", uuid.UUID(filed["id"]), "Ceiling"),
        ("request_photo", uuid.UUID(filed["id"]), "Floor"),
    ]
    first = page["request"]["photos"][0]["id"]
    assert client.get(f"/api/documents/{first}/file").content == PNG


def test_a_plain_request_opens_a_normal_work_order_the_vendor_may_enter(
    client, other_client, conn, app_conn, home
):
    join_as_tenant(other_client, conn, app_conn, home.pmc)
    filed = ok(other_client.post("/api/tenant/requests", json=new_request(home)), 201)
    join_as_staff(client, conn, app_conn, home.pmc)
    order = open_order(client, filed["id"])["work_order_id"]

    assert (filed["emergency"], filed["photos"]) == (False, 0)
    assert details(conn, order) == [("normal", "allowed")]
    assert ok(client.get(f"/api/work-orders/{order}"))["request"]["photos"] == []


def test_a_removed_photo_is_no_longer_counted(client, other_client, conn, app_conn, home):
    join_as_tenant(other_client, conn, app_conn, home.pmc)
    filed = ok(
        other_client.post(
            "/api/tenant/requests", json=new_request(home, photos=[photo("Tap"), photo("Sink")])
        ),
        201,
    )
    join_as_staff(client, conn, app_conn, home.pmc)
    order = open_order(client, filed["id"])["work_order_id"]
    # Filed together, they are listed by title.
    sink = ok(client.get(f"/api/work-orders/{order}"))["request"]["photos"][0]["id"]
    ok(client.post(f"/api/documents/{sink}/remove", json={}), 204)

    assert [
        p["title"] for p in ok(client.get(f"/api/work-orders/{order}"))["request"]["photos"]
    ] == ["Tap"]
    assert ok(other_client.get("/api/tenant/requests"))[0]["photos"] == 1


@pytest.mark.parametrize(
    ("photos", "status", "detail"),
    [
        ([photo("Tap", PDF)], 422, "the file isn't a PNG"),
        ([photo("Tap", PNG, "image/gif")], 422, None),
        ([photo("Tap")] * 4, 422, None),
        ([{**photo("Tap"), "content_base64": "not base64!"}], 422, "the file isn't base64"),
        ([photo("   ")], 422, None),
    ],
)
def test_a_bad_photo_files_nothing(client, conn, app_conn, home, photos, status, detail):
    join_as_tenant(client, conn, app_conn, home.pmc)

    answer = client.post("/api/tenant/requests", json=new_request(home, photos=photos))

    assert answer.status_code == status, answer.text
    if detail:
        assert answer.json()["detail"] == detail
    assert conn.execute(
        "SELECT count(*) FROM trust_maintenance_requests WHERE lease_id = %s", (home.lease,)
    ).fetchone() == (0,)


# --- whoever is on call hears of an emergency -------------------------------------------------


def test_each_staff_member_on_call_is_emailed_of_an_emergency_once(
    client, other_client, conn, app_conn, home
):
    pmc_id = home.pmc.pmc_id
    staff = join_as_staff(client, conn, app_conn, home.pmc, name="Manager 1")
    resting = join_as_staff(other_client, conn, app_conn, home.pmc, name="Manager 2")
    no_login = add_staff(conn, pmc_id, "Manager 3")
    for who in (staff.person_id, resting.person_id, no_login):
        ok(client.post("/api/maintenance/on-call", json={"staff_id": str(who), "on_call": True}))
    ok(
        client.post(
            "/api/maintenance/on-call",
            json={"staff_id": str(resting.person_id), "on_call": False},
        )
    )
    join_as_tenant(other_client, conn, app_conn, home.pmc)
    other_client.app.state.email = email.FakeSender()

    plain = ok(other_client.post("/api/tenant/requests", json=new_request(home)), 201)
    urgent = ok(
        other_client.post(
            "/api/tenant/requests",
            json=new_request(home, summary="No heat", emergency=True, entry_allowed=True),
        ),
        201,
    )

    staff_page = ok(
        client.get(f"/api/work-orders/{open_order(client, urgent['id'])['work_order_id']}")
    )
    sent = emergencies(conn, pmc_id)
    assert [(s, r, to, subject) for s, r, to, subject, _ in sent] == [
        (
            staff.person_id,
            uuid.UUID(urgent["id"]),
            staff.email,
            "Emergency at Property 1, Unit 1: No heat",
        )
    ]
    body = sent[0][4]
    for line in (
        "Hello Manager 1,",
        "Tenant 1 filed an emergency maintenance request for Property 1, Unit 1:",
        "No heat",
        "They allow entry.",
        "http://testserver/work-orders",
    ):
        assert line in body, body
    assert plain["emergency"] is False
    # Staff see on the work order whom it went to.
    assert staff_page["request"]["emailed"] == ["Manager 1"]
    # Written once per request and staff member.
    assert notices.write_emergency(app_conn, pmc_id, uuid.UUID(urgent["id"]), "x") == 0
    assert notices.write_emergency(app_conn, pmc_id, uuid.UUID(plain["id"]), "x") == 0


def test_no_emergency_email_while_the_server_sends_none(client, other_client, conn, app_conn, home):
    staff = join_as_staff(client, conn, app_conn, home.pmc)
    ok(
        client.post(
            "/api/maintenance/on-call", json={"staff_id": str(staff.person_id), "on_call": True}
        )
    )
    join_as_tenant(other_client, conn, app_conn, home.pmc)

    ok(other_client.post("/api/tenant/requests", json=new_request(home, emergency=True)), 201)

    assert emergencies(conn, home.pmc.pmc_id) == []


def test_staff_put_each_other_on_call_and_the_same_again_writes_nothing(
    client, conn, app_conn, home
):
    staff = join_as_staff(client, conn, app_conn, home.pmc, name="Manager 1")
    other = add_staff(conn, home.pmc.pmc_id, "Manager 2")
    rows = "SELECT count(*) FROM trust_on_call_staff WHERE pmc_id = %s"

    before = ok(client.get("/api/maintenance/on-call"))
    on = {"staff_id": str(other), "on_call": True}
    after = ok(client.post("/api/maintenance/on-call", json=on))
    ok(client.post("/api/maintenance/on-call", json=on))

    assert [(s["name"], s["on_call"], s["emailed"]) for s in before] == [
        ("Manager 1", False, True),
        ("Manager 2", False, False),
    ]
    assert [(s["name"], s["on_call"]) for s in after] == [("Manager 1", False), ("Manager 2", True)]
    assert conn.execute(rows, (home.pmc.pmc_id,)).fetchone() == (1,)
    assert conn.execute(
        "SELECT recorded_by FROM trust_on_call_staff WHERE staff_id = %s", (other,)
    ).fetchone() == (staff.person_id,)


def test_staff_of_another_company_are_not_found(client, conn, app_conn, home):
    join_as_staff(client, conn, app_conn, home.pmc)
    stranger = add_staff(conn, make_pmc(conn).pmc_id, "Stranger 1")

    for who in (stranger, uuid.uuid4()):
        answer = client.post(
            "/api/maintenance/on-call", json={"staff_id": str(who), "on_call": True}
        )
        assert answer.status_code == 404
        assert answer.json()["detail"] == "no such staff member"


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_see_or_change_who_is_on_call(client, conn, app_conn, home, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, home.pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, home.pmc)
    expected = 401 if caller == "nobody" else 403

    answers = [
        client.get("/api/maintenance/on-call"),
        client.post(
            "/api/maintenance/on-call", json={"staff_id": str(uuid.uuid4()), "on_call": True}
        ),
    ]

    assert [a.status_code for a in answers] == [expected] * 2


@pytest.mark.parametrize(
    "body", [{}, {"staff_id": "not-an-id", "on_call": True}, {"staff_id": str(uuid.uuid4())}]
)
def test_bad_on_call_input_is_refused(client, conn, app_conn, home, body):
    join_as_staff(client, conn, app_conn, home.pmc)

    assert client.post("/api/maintenance/on-call", json=body).status_code == 422


# --- a visit's window, and days open -----------------------------------------------------------


def test_a_visit_window_reaches_the_tenant_the_vendor_and_the_work_order(
    client, other_client, conn, app_conn, home
):
    join_as_tenant(other_client, conn, app_conn, home.pmc)
    filed = ok(other_client.post("/api/tenant/requests", json=new_request(home)), 201)
    join_as_staff(client, conn, app_conn, home.pmc)
    # The vendor hears of their visit by email.
    client.app.state.email = email.FakeSender()
    ok(client.post("/api/emails/settings", json={"work_order_notices": True}))
    conn.execute(
        "INSERT INTO trust_contacts (pmc_id, vendor_id, email) VALUES (%s, %s, %s)",
        (home.pmc.pmc_id, home.pmc.vendor_id, "vendor.one@example.com"),
    )
    order = open_order(client, filed["id"])["work_order_id"]
    steps = f"/api/maintenance/work-orders/{order}/steps"
    ok(client.post(steps, json={"step": "assigned", "vendor_id": str(home.pmc.vendor_id)}), 201)
    ok(
        client.post(
            steps,
            json={"step": "scheduled", "scheduled_for": "2026-11-03", "visit_window": " 8-10 am "},
        ),
        201,
    )

    seen = ok(other_client.get("/api/tenant/requests"))[0]
    page = ok(client.get(f"/api/work-orders/{order}"))
    listed = ok(client.get("/api/work-orders"))["items"][0]

    assert (seen["status"], seen["visit_on"], seen["visit_window"]) == (
        "scheduled",
        "2026-11-03",
        "8-10 am",
    )
    assert [(s["step"], s["visit_window"]) for s in page["steps"]] == [
        ("assigned", None),
        ("scheduled", "8-10 am"),
    ]
    assert (listed["scheduled_for"], listed["visit_window"]) == ("2026-11-03", "8-10 am")
    (body,) = conn.execute(
        "SELECT body FROM trust_emails WHERE kind = 'work_order'"
        " AND subject LIKE 'Work order #1 scheduled for 11/03/2026%%'"
    ).fetchone()
    # The tenant's word on entry, copied to the work order, reaches the vendor too.
    assert "Visit window: 8-10 am" in body and "You may let yourself in." in body


@pytest.mark.parametrize(
    ("step", "detail"),
    [
        (
            {"step": "completed", "visit_window": "8-10 am"},
            "only a schedule says when in the day the vendor comes",
        ),
        ({"step": "scheduled", "scheduled_for": "2026-11-03", "visit_window": "  "}, None),
        ({"step": "scheduled", "scheduled_for": "2026-11-03", "visit_window": "x" * 61}, None),
    ],
)
def test_a_bad_visit_window_is_refused(client, other_client, conn, app_conn, home, step, detail):
    join_as_tenant(other_client, conn, app_conn, home.pmc)
    filed = ok(other_client.post("/api/tenant/requests", json=new_request(home)), 201)
    join_as_staff(client, conn, app_conn, home.pmc)
    order = open_order(client, filed["id"])["work_order_id"]
    steps = f"/api/maintenance/work-orders/{order}/steps"
    ok(client.post(steps, json={"step": "assigned", "vendor_id": str(home.pmc.vendor_id)}), 201)

    answer = client.post(steps, json=step)

    assert answer.status_code == 422
    if detail:
        assert answer.json()["detail"] == detail


def test_a_work_order_counts_its_days_open_until_it_closes(client, conn, app_conn, home):
    join_as_staff(client, conn, app_conn, home.pmc)
    pmc_id, prop = home.pmc.pmc_id, home.pmc.owners[0].property_id
    now = datetime.now(UTC)
    still = add_work_order(conn, pmc_id, prop, home.unit, "Still open", now - timedelta(days=12))
    done = add_work_order(conn, pmc_id, prop, home.unit, "Done", now - timedelta(days=30))
    step_work_order(conn, pmc_id, done, "completed", now - timedelta(days=26))
    fresh = add_work_order(conn, pmc_id, prop, home.unit, "Fresh", now)

    found = {w["id"]: w["days_open"] for w in ok(client.get("/api/work-orders?show=all"))["items"]}

    assert (found[str(still)], found[str(done)], found[str(fresh)]) == (12, 4, 0)
    assert ok(client.get(f"/api/work-orders/{done}"))["days_open"] == 4


def test_a_co_tenant_sees_the_visit_too(client, other_client, conn, app_conn, home):
    join_as_tenant(client, conn, app_conn, home.pmc)
    join_as(other_client, app_conn, home.pmc.pmc_id, "tenant", home.co_tenant)
    filed = ok(client.post("/api/tenant/requests", json=new_request(home)), 201)
    pmc_id = home.pmc.pmc_id
    order = add_work_order(
        conn, pmc_id, home.pmc.owners[0].property_id, home.unit, "Tap", datetime.now(UTC)
    )
    conn.execute(
        "INSERT INTO trust_maintenance_request_work_orders (pmc_id, request_id, work_order_id)"
        " VALUES (%s, %s, %s)",
        (pmc_id, filed["id"], order),
    )
    now = datetime.now(UTC)
    step_work_order(conn, pmc_id, order, "assigned", now, home.pmc.vendor_id)
    conn.execute(
        "INSERT INTO trust_work_order_steps"
        " (pmc_id, work_order_id, step, scheduled_for, visit_window, taken_at)"
        " VALUES (%s, %s, 'scheduled', '2026-11-04', 'Noon-2 pm', %s)",
        (pmc_id, order, now),
    )

    seen = ok(other_client.get("/api/tenant/requests"))[0]

    assert (seen["visit_on"], seen["visit_window"]) == ("2026-11-04", "Noon-2 pm")
    assert "vendor" not in seen
