"""Work orders, a vendor's page and renewals through the API (src/pms/api/work_orders.py,
vendors.py, renewals.py; ADR 0005). Always the session's PMC; only staff read or change them."""

from datetime import UTC, datetime, timedelta

import pytest
from helpers import (
    add_staff,
    add_tenant,
    add_unit,
    approve_bill,
    enter_bill,
    make_pmc,
    offer_lease,
    open_lease,
    pay_bill,
    set_aside_bill,
    transfer_batch,
)
from people import join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
NOBODY = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def open_one(client, pmc, summary, unit=None, owner=0):
    body = {"property_id": str(pmc.owners[owner].property_id), "summary": summary}
    if unit:
        body["unit_id"] = str(unit)
    return ok(client.post("/api/work-orders", json=body), 201)["id"]


def step(client, work_order, name, vendor=None, note=None, on=None):
    body = {"step": name, "vendor_id": None if vendor is None else str(vendor), "note": note}
    if on is not None:
        body["scheduled_for"] = str(on)
    ok(client.post(f"/api/maintenance/work-orders/{work_order}/steps", json=body), 201)


def test_staff_open_work_orders_and_list_them_by_where_they_stand(client, conn, staff, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    leak = open_one(client, pmc, " Leak under the sink ", unit)
    roof = open_one(client, pmc, "Roof check", owner=1)
    fence = open_one(client, pmc, "Fence")
    step(client, roof, "assigned", pmc.vendor_id)
    step(client, fence, "cancelled", note="Owner will do it")

    still = ok(client.get("/api/work-orders"))
    assert [(w["summary"], w["status"], w["vendor"]) for w in still["items"]] == [
        ("Roof check", "assigned", "Vendor 1"),
        ("Leak under the sink", "open", None),
    ]
    assert still["items"][1]["unit"] == {"id": str(unit), "name": "Unit 1"}
    assert still["items"][0]["unit"] is None
    closed = ok(client.get("/api/work-orders", params={"show": "closed"}))["items"]
    assert [(w["id"], w["status"]) for w in closed] == [(fence, "cancelled")]
    assert ok(client.get("/api/work-orders", params={"show": "all"}))["total"] == 3
    # Found by what they are for, property, unit or vendor.
    for q, found in (
        ("sink", [leak]),
        ("property 2", [roof]),
        ("unit 1", [leak]),
        ("vendor", [roof]),
    ):
        assert [
            w["id"] for w in ok(client.get("/api/work-orders", params={"q": q}))["items"]
        ] == found
    page = ok(client.get("/api/work-orders", params={"show": "all", "per_page": 2, "page": 2}))
    assert (page["total"], [w["id"] for w in page["items"]]) == (3, [leak])


def test_a_work_order_shows_its_steps_bills_and_the_request_it_answers(client, conn, staff, pmc):
    owner = pmc.owners[0]
    unit = add_unit(conn, pmc.pmc_id, owner.property_id, "Unit 1")
    tenant = add_tenant(conn, pmc.pmc_id, owner.property_id, "Tenant 2")
    lease = open_lease(conn, pmc.pmc_id, unit, TODAY - 30 * DAY, None, "1000.00", [tenant])
    request = conn.execute(
        "INSERT INTO trust_maintenance_requests (pmc_id, lease_id, tenant_id, summary,"
        " entry_allowed) VALUES (%s, %s, %s, 'Drip', true) RETURNING id",
        (pmc.pmc_id, lease, tenant),
    ).fetchone()[0]
    work_order = ok(client.post(f"/api/maintenance/requests/{request}/work-order", json={}), 201)[
        "work_order_id"
    ]
    step(client, work_order, "assigned", pmc.vendor_id, "Tuesday")
    step(client, work_order, "completed")
    transfer_batch(conn, [(pmc.operating_cash, owner.account, "500.00")])
    paid = enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        owner.account,
        "INV-1",
        TODAY,
        TODAY,
        "80.00",
        work_order_id=work_order,
    )
    approve_bill(conn, pmc.pmc_id, paid, TODAY, "Owner 1")
    set_aside_bill(conn, pmc.pmc_id, paid, datetime.now(UTC))
    pay_bill(conn, pmc.pmc_id, paid, datetime.now(UTC))
    enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        owner.account,
        "INV-2",
        TODAY,
        TODAY,
        "20.00",
        work_order_id=work_order,
    )

    found = ok(client.get(f"/api/work-orders/{work_order}"))

    assert (found["summary"], found["status"], found["vendor"], found["billed"]) == (
        "Drip",
        "completed",
        "Vendor 1",
        "100.00",
    )
    assert [(s["step"], s["vendor"], s["note"]) for s in found["steps"]] == [
        ("assigned", "Vendor 1", "Tuesday"),
        ("completed", None, None),
    ]
    assert [(b["reference"], b["amount"], b["paid"]) for b in found["bills"]] == [
        ("INV-1", "80.00", True),
        ("INV-2", "20.00", False),
    ]
    assert (found["request"]["id"], found["request"]["tenant"]) == (str(request), "Tenant 2")
    assert ok(client.get(f"/api/work-orders/{open_one(client, pmc, 'Gutter')}"))["request"] is None


def test_a_work_order_opens_with_its_details_and_they_change_row_by_row(client, conn, staff, pmc):
    body = {
        "property_id": str(pmc.owners[0].property_id),
        "summary": "Leak",
        "priority": "high",
        "description": " Drips at night ",
        "due_on": str(TODAY - DAY),
        "entry": "call first",
    }
    leak = ok(client.post("/api/work-orders", json=body), 201)["id"]
    plain = open_one(client, pmc, "Gutter")

    found = ok(client.get(f"/api/work-orders/{leak}"))
    assert {k: found[k] for k in ("priority", "description", "due_on", "entry", "overdue")} == {
        "priority": "high",
        "description": "Drips at night",
        "due_on": str(TODAY - DAY),
        "entry": "call first",
        "overdue": True,
    }
    # A work order opened without details is normal priority, with nothing more said, and
    # writes no details row.
    assert {
        k: ok(client.get(f"/api/work-orders/{plain}"))[k]
        for k in ("priority", "description", "due_on", "entry", "overdue")
    } == {
        "priority": "normal",
        "description": None,
        "due_on": None,
        "entry": None,
        "overdue": False,
    }
    assert conn.execute(
        "SELECT count(*) FROM trust_work_order_details WHERE work_order_id = %s", (plain,)
    ).fetchone() == (0,)

    change = {"priority": "urgent", "due_on": str(TODAY + DAY), "entry": None}
    assert ok(client.post(f"/api/work-orders/{leak}/details", json=change)) == {"changed": True}
    assert ok(client.post(f"/api/work-orders/{leak}/details", json=change)) == {"changed": False}
    assert ok(client.post(f"/api/work-orders/{plain}/details", json={})) == {"changed": False}
    again = ok(client.get(f"/api/work-orders/{leak}"))
    assert (again["priority"], again["description"], again["due_on"], again["overdue"]) == (
        "urgent",
        None,
        str(TODAY + DAY),
        False,
    )
    # Each change is a row of its own, naming the staff member signed in.
    assert conn.execute(
        "SELECT priority, recorded_by FROM trust_work_order_details WHERE work_order_id = %s"
        " ORDER BY created_at",
        (leak,),
    ).fetchall() == [("high", staff.person_id), ("urgent", staff.person_id)]
    # A closed work order is not overdue.
    step(client, leak, "cancelled")
    ok(client.post(f"/api/work-orders/{leak}/details", json={"due_on": str(TODAY - DAY)}))
    assert ok(client.get(f"/api/work-orders/{leak}"))["overdue"] is False


def test_the_list_numbers_them_and_narrows_by_status_vendor_and_property(client, conn, staff, pmc):
    vendor_2 = conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor 2') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    first = open_one(client, pmc, "First")
    second = open_one(client, pmc, "Second", owner=1)
    third = open_one(client, pmc, "Third")
    step(client, second, "assigned", pmc.vendor_id)
    step(client, second, "scheduled", on=TODAY + 3 * DAY)
    step(client, third, "assigned", vendor_2)

    def listed(**params):
        return [
            (w["summary"], w["number"], w["status"])
            for w in ok(client.get("/api/work-orders", params=params))["items"]
        ]

    assert listed(show="all") == [
        ("Third", 3, "assigned"),
        ("Second", 2, "scheduled"),
        ("First", 1, "open"),
    ]
    assert listed(show="all", status="scheduled") == [("Second", 2, "scheduled")]
    assert listed(show="all", status="open") == [("First", 1, "open")]
    assert listed(show="all", vendor=str(vendor_2)) == [("Third", 3, "assigned")]
    assert listed(property=str(pmc.owners[1].property_id)) == [("Second", 2, "scheduled")]
    group = ok(
        client.post(
            "/api/property-groups",
            json={"name": "North", "property_ids": [str(pmc.owners[0].property_id)]},
        ),
        201,
    )["id"]
    assert listed(group=group) == [("Third", 3, "assigned"), ("First", 1, "open")]

    row = ok(client.get("/api/work-orders", params={"status": "scheduled"}))["items"][0]
    assert (row["vendor_id"], row["vendor"], row["scheduled_for"]) == (
        str(pmc.vendor_id),
        "Vendor 1",
        str(TODAY + 3 * DAY),
    )
    assert row["updated_at"] >= row["opened_at"]
    assert ok(client.get(f"/api/work-orders/{first}"))["number"] == 1


def test_a_visit_is_scheduled_after_an_assignment(client, conn, staff, pmc):
    leak = open_one(client, pmc, "Leak")
    path = f"/api/maintenance/work-orders/{leak}/steps"

    early = client.post(path, json={"step": "scheduled", "scheduled_for": str(TODAY)})
    assert (early.status_code, early.json()["detail"]) == (
        409,
        "a work order is scheduled once a vendor is assigned to it",
    )
    for body in (
        {"step": "scheduled"},
        {"step": "completed", "scheduled_for": str(TODAY)},
        {"step": "assigned", "vendor_id": str(pmc.vendor_id), "scheduled_for": str(TODAY)},
    ):
        refused = client.post(path, json=body)
        assert (refused.status_code, refused.json()["detail"]) == (
            422,
            "a schedule names the day the vendor comes, and only it does",
        ), body
    step(client, leak, "assigned", pmc.vendor_id)
    step(client, leak, "scheduled", note="Mornings", on=TODAY + DAY)

    found = ok(client.get(f"/api/work-orders/{leak}"))
    assert (found["status"], found["scheduled_for"]) == ("scheduled", str(TODAY + DAY))
    assert [(s["step"], s["vendor"], s["scheduled_for"], s["note"]) for s in found["steps"]] == [
        ("assigned", "Vendor 1", None, None),
        ("scheduled", None, str(TODAY + DAY), "Mornings"),
    ]


def test_a_vendor_whose_insurance_ran_out_is_not_assigned(client, conn, staff, pmc):
    leak = open_one(client, pmc, "Leak")
    conn.execute(
        "INSERT INTO trust_vendor_insurance (pmc_id, vendor_id, coverage, carrier, policy_number,"
        " starts_on, expires_on) VALUES (%s, %s, 'general liability', 'Carrier A', 'GL-1', %s, %s)",
        (pmc.pmc_id, pmc.vendor_id, TODAY - 400 * DAY, TODAY - DAY),
    )

    refused = client.post(
        f"/api/maintenance/work-orders/{leak}/steps",
        json={"step": "assigned", "vendor_id": str(pmc.vendor_id)},
    )

    ended = (TODAY - DAY).strftime("%m/%d/%Y")
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        f"the vendor's insurance expired on {ended}; record its renewal before assigning them",
    )
    assert ok(client.get(f"/api/work-orders/{leak}"))["steps"] == []


def test_a_work_order_names_its_units_tenants_and_how_to_reach_them(client, conn, staff, pmc):
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit 1")
    tenant_b = add_tenant(conn, pmc.pmc_id, prop, "Tenant B")
    tenant_a = add_tenant(conn, pmc.pmc_id, prop, "Tenant A")
    gone = add_tenant(conn, pmc.pmc_id, prop, "Tenant Gone")
    open_lease(conn, pmc.pmc_id, unit, TODAY - 400 * DAY, TODAY - 40 * DAY, "900.00", [gone])
    open_lease(conn, pmc.pmc_id, unit, TODAY - 30 * DAY, None, "1000.00", [tenant_b, tenant_a])
    ok(
        client.post(
            f"/api/tenants/{tenant_a}/contact",
            json={"email": "tenant.a@example.com", "phone": "555-555-0101"},
        ),
        204,
    )
    leak = open_one(client, pmc, "Leak", unit)

    tenants = ok(client.get(f"/api/work-orders/{leak}"))["tenants"]

    assert [(t["name"], t["contact"]["email"], t["contact"]["phone"]) for t in tenants] == [
        ("Tenant A", "tenant.a@example.com", "555-555-0101"),
        ("Tenant B", None, None),
    ]
    # A work order on the whole property names no one.
    assert ok(client.get(f"/api/work-orders/{open_one(client, pmc, 'Roof')}"))["tenants"] == []


def test_the_list_says_what_the_work_orders_report_says(client, conn, staff, pmc):
    open_one(client, pmc, "Opened")
    step(client, open_one(client, pmc, "Done"), "completed")
    step(client, open_one(client, pmc, "Sent"), "assigned", pmc.vendor_id)

    listed = {
        w["summary"]: w["status"] for w in ok(client.get("/api/work-orders?show=all"))["items"]
    }
    reported = dict(
        conn.execute(
            "SELECT summary, status FROM trust_report_work_orders(%s, %s)"
            " WHERE item IN ('open', 'closed')",
            (pmc.pmc_id, TODAY),
        ).fetchall()
    )
    assert listed == reported


def test_a_unit_must_be_the_propertys(client, conn, staff, pmc):
    elsewhere = add_unit(conn, pmc.pmc_id, pmc.owners[1].property_id, "Unit 9")
    theirs = make_pmc(conn)
    their_unit = add_unit(conn, theirs.pmc_id, theirs.owners[0].property_id, "Unit 8")
    body = {"property_id": str(pmc.owners[0].property_id), "summary": "Leak"}

    for unit in (elsewhere, their_unit):
        refused = client.post("/api/work-orders", json={**body, "unit_id": str(unit)})
        assert refused.status_code == 422
        assert refused.json()["detail"] == "that unit is not one of the property's"
    other = {**body, "property_id": str(theirs.owners[0].property_id)}
    assert client.post("/api/work-orders", json=other).status_code == 404


def test_a_vendors_page_has_their_bills_and_work_orders_and_they_are_renamed(
    client, conn, staff, pmc
):
    owner = pmc.owners[0]
    roof = open_one(client, pmc, "Roof")
    step(client, roof, "assigned", pmc.vendor_id)
    open_one(client, pmc, "Not theirs")
    enter_bill(conn, pmc.pmc_id, pmc.vendor_id, owner.account, "INV-7", TODAY, TODAY, "70.00")

    page = ok(client.get(f"/api/vendors/{pmc.vendor_id}"))
    ok(client.post(f"/api/vendors/{pmc.vendor_id}/name", json={"name": "Vendor One"}), 204)

    assert (page["name"], page["w9_on_file"], page["insurance"]) == ("Vendor 1", False, [])
    assert [
        (b["reference"], b["owner"], b["property"], b["amount"], b["paid"]) for b in page["bills"]
    ] == [("INV-7", "Owner 1", "Property 1", "70.00", False)]
    assert [(w["id"], w["summary"], w["status"]) for w in page["work_orders"]] == [
        (roof, "Roof", "assigned")
    ]
    assert ok(client.get(f"/api/vendors/{pmc.vendor_id}"))["name"] == "Vendor One"


def test_renewals_list_leases_ending_soon_with_their_lease_and_renewal(client, conn, staff, pmc):
    # Today as the API takes it, read now: TODAY is yesterday's for a run that crosses midnight.
    today = datetime.now(UTC).date()
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit 1")
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 2")
    ends = today + 20 * DAY
    lease = open_lease(conn, pmc.pmc_id, unit, today - 300 * DAY, ends, "1000.00", [tenant])
    later = add_unit(conn, pmc.pmc_id, prop, "Unit 2")
    open_lease(conn, pmc.pmc_id, later, today - 300 * DAY, today + 200 * DAY, "900.00", [tenant])
    offer_lease(
        conn, pmc.pmc_id, unit, add_staff(conn, pmc.pmc_id), ends + DAY, renews_lease_id=lease
    )

    (found,) = ok(client.get("/api/leasing/renewals"))
    assert found == {
        "lease_id": str(lease),
        "property": "Property 1",
        "unit": "Unit 1",
        "tenants": "Tenant 2",
        "ends_on": str(ends),
        "days_left": 20,
        "bucket": "0-30",
        "monthly_rent": "1000.00",
        "renewal": "drafted",
    }
    later_on = ok(client.get("/api/leasing/renewals", params={"on": str(today + 150 * DAY)}))
    assert [r["unit"] for r in later_on] == ["Unit 2"]


def test_everything_is_the_sessions_pmc(client, conn, staff, pmc):
    theirs = make_pmc(conn)
    work_order = conn.execute(
        "INSERT INTO trust_work_orders (pmc_id, property_id, summary, opened_at)"
        " VALUES (%s, %s, 'Theirs', now()) RETURNING id",
        (theirs.pmc_id, theirs.owners[0].property_id),
    ).fetchone()[0]

    assert client.get(f"/api/work-orders/{work_order}").status_code == 404
    assert client.post(f"/api/work-orders/{work_order}/details", json={}).status_code == 404
    assert conn.execute(
        "SELECT count(*) FROM trust_work_order_details WHERE work_order_id = %s", (work_order,)
    ).fetchone() == (0,)
    assert ok(client.get("/api/work-orders?show=all"))["total"] == 0
    assert client.get(f"/api/vendors/{theirs.vendor_id}").status_code == 404
    assert (
        client.post(f"/api/vendors/{theirs.vendor_id}/name", json={"name": "x"}).status_code == 404
    )
    assert ok(client.get("/api/leasing/renewals")) == []


GETS = [
    "/api/work-orders",
    f"/api/work-orders/{NOBODY}",
    f"/api/vendors/{NOBODY}",
    "/api/leasing/renewals",
]
POSTS = [
    ("/api/work-orders", {"property_id": NOBODY, "summary": "x"}),
    (f"/api/work-orders/{NOBODY}/details", {}),
    (f"/api/vendors/{NOBODY}/name", {"name": "x"}),
]


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_see_or_change_them(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    for path in GETS:
        assert client.get(path).status_code == status, path
    for path, body in POSTS:
        assert client.post(path, json=body).status_code == status, path


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/work-orders?show=late", None),
        ("/api/work-orders?status=late", None),
        ("/api/work-orders?vendor=someone", None),
        ("/api/work-orders?per_page=0", None),
        ("/api/work-orders/not-an-id", None),
        ("/api/leasing/renewals?on=soon", None),
        ("/api/work-orders", {"property_id": NOBODY, "summary": " "}),
        ("/api/work-orders", {"property_id": "x", "summary": "Leak"}),
        ("/api/work-orders", {"property_id": NOBODY, "summary": "x" * 501}),
        (f"/api/vendors/{NOBODY}/name", {"name": ""}),
        ("/api/work-orders", {"property_id": NOBODY, "summary": "x", "priority": "asap"}),
        (f"/api/work-orders/{NOBODY}/details", {"priority": "asap"}),
        (f"/api/work-orders/{NOBODY}/details", {"description": " "}),
        (f"/api/work-orders/{NOBODY}/details", {"description": "x" * 5001}),
        (f"/api/work-orders/{NOBODY}/details", {"due_on": "soon"}),
        (f"/api/work-orders/{NOBODY}/details", {"entry": "anytime"}),
        (
            f"/api/maintenance/work-orders/{NOBODY}/steps",
            {"step": "scheduled", "scheduled_for": "x"},
        ),
        (f"/api/maintenance/work-orders/{NOBODY}/steps", {"step": "reopened"}),
    ],
)
def test_bad_input_is_refused(client, staff, path, body):
    answer = client.get(path) if body is None else client.post(path, json=body)
    assert answer.status_code == 422, answer.text
