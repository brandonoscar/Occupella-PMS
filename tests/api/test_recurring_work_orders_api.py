"""Recurring work orders and a tenant charged for a work order, through the API (migration 0085;
the founder's review, q072 and q073; src/pms/api/recurring_work_orders.py, rent.py and
work_orders.py). Always the session's PMC; only staff. Every name here is synthetic."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from helpers import add_tenant, add_unit, make_pmc, open_lease
from people import join_as, join_as_owner, join_as_staff

from pms import nightly
from pms.api import recurring_work_orders

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


class Home:
    """Unit 1 of Property 1 leased to Tenant 1 since January; Unit 2 empty."""

    def __init__(self, conn, pmc):
        prop = pmc.owners[0].property_id
        self.pmc, self.property_id = pmc, prop
        self.unit = add_unit(conn, pmc.pmc_id, prop, "Unit 1")
        self.empty = add_unit(conn, pmc.pmc_id, prop, "Unit 2")
        self.tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 1")
        self.lease = open_lease(
            conn, pmc.pmc_id, self.unit, date(2026, 1, 1), None, "1500.00", [self.tenant]
        )


@pytest.fixture
def home(conn, pmc):
    return Home(conn, pmc)


def open_one(client, home, unit=True):
    body = {"property_id": str(home.property_id), "summary": "Broken window"}
    if unit:
        body["unit_id"] = str(home.unit)
    return ok(client.post("/api/work-orders", json=body), 201)["id"]


# --- a tenant charged for a work order ---------------------------------------------------------


def test_staff_charge_the_tenant_for_a_work_order_and_see_it_on_the_work_order(
    client, other_client, conn, app_conn, staff, home
):
    order = open_one(client, home)
    # A fee of the lease that names no work order: not the work order's.
    ok(
        client.post(
            f"/api/rent/leases/{home.lease}/charges",
            json={"kind": "fee", "amount": "15.00", "due_on": str(TODAY), "memo": "Parking"},
        ),
        201,
    )
    page = ok(client.get(f"/api/work-orders/{order}"))
    charged = ok(
        client.post(
            f"/api/rent/leases/{home.lease}/charges",
            json={
                "kind": "fee",
                "amount": "85.00",
                "due_on": str(TODAY),
                "memo": "Broken window, tenant's guest",
                "work_order_id": order,
            },
        ),
        201,
    )
    after = ok(client.get(f"/api/work-orders/{order}"))

    assert page["leases"] == [{"id": str(home.lease), "unit": "Unit 1", "tenants": "Tenant 1"}]
    assert page["charges"] == []
    assert after["charges"] == [
        {
            "id": charged["id"],
            "lease_id": str(home.lease),
            "unit": "Unit 1",
            "tenants": "Tenant 1",
            "due_on": str(TODAY),
            "amount": "85.00",
            "memo": "Broken window, tenant's guest",
            "open": "85.00",
        }
    ]
    # The tenant owes it like any fee.
    join_as(other_client, app_conn, home.pmc.pmc_id, "tenant", home.tenant)
    (lease,) = ok(other_client.get("/api/tenant/leases"))
    assert lease["balance_due"] == "100.00"


def test_a_work_order_on_the_whole_property_offers_every_lease_there(client, conn, staff, home):
    other_tenant = add_tenant(conn, home.pmc.pmc_id, home.property_id, "Tenant 2")
    # Unit 2's lease of last year has ended: it is not offered.
    gone = add_tenant(conn, home.pmc.pmc_id, home.property_id, "Tenant 0")
    open_lease(
        conn, home.pmc.pmc_id, home.empty, date(2025, 1, 1), date(2025, 12, 31), "800.00", [gone]
    )
    other = open_lease(
        conn, home.pmc.pmc_id, home.empty, TODAY - DAY, None, "900.00", [other_tenant]
    )
    order = open_one(client, home, unit=False)

    page = ok(client.get(f"/api/work-orders/{order}"))

    assert [(lease["unit"], lease["tenants"]) for lease in page["leases"]] == [
        ("Unit 1", "Tenant 1"),
        ("Unit 2", "Tenant 2"),
    ]
    ok(
        client.post(
            f"/api/rent/leases/{other}/charges",
            json={
                "kind": "fee",
                "amount": "40.00",
                "due_on": str(TODAY),
                "memo": "Hallway",
                "work_order_id": order,
            },
        ),
        201,
    )


@pytest.mark.parametrize(
    ("change", "status", "detail"),
    [
        ({"kind": "credit"}, 422, "a work order is charged to a tenant as a fee"),
        ({"work_order_id": str(uuid.uuid4())}, 404, "no such work order"),
        ({"work_order_id": "not-an-id"}, 422, None),
    ],
)
def test_a_bad_work_order_charge_is_refused(client, staff, home, change, status, detail):
    order = open_one(client, home)
    body = {
        "kind": "fee",
        "amount": "85.00",
        "due_on": str(TODAY),
        "memo": "Window",
        "work_order_id": order,
        **change,
    }

    answer = client.post(f"/api/rent/leases/{home.lease}/charges", json=body)

    assert answer.status_code == status, answer.text
    if detail:
        assert answer.json()["detail"] == detail


def test_a_work_order_is_not_charged_to_a_lease_at_another_unit_or_company(
    client, conn, staff, home
):
    other_tenant = add_tenant(conn, home.pmc.pmc_id, home.property_id, "Tenant 2")
    next_door = open_lease(
        conn, home.pmc.pmc_id, home.empty, TODAY - DAY, None, "900.00", [other_tenant]
    )
    order = open_one(client, home)
    body = {
        "kind": "fee",
        "amount": "85.00",
        "due_on": str(TODAY),
        "memo": "Window",
        "work_order_id": order,
    }

    # Only the lease at its own unit is offered.
    offered = ok(client.get(f"/api/work-orders/{order}"))["leases"]
    refused = client.post(f"/api/rent/leases/{next_door}/charges", json=body)
    theirs = Home(conn, make_pmc(conn))
    elsewhere = client.post(f"/api/rent/leases/{theirs.lease}/charges", json=body)

    assert [lease["id"] for lease in offered] == [str(home.lease)]
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "a work order is charged only to a lease at its unit or property"
    )
    assert elsewhere.status_code == 404


# --- recurring work orders ---------------------------------------------------------------------


def new_recurring(home, **change):
    return {
        "property_id": str(home.property_id),
        "unit_id": str(home.unit),
        "summary": " Change the furnace filter ",
        "every_months": 3,
        "day_of_month": 15,
        "starts_on": str(TODAY),
        "priority": "high",
        "vendor_id": str(home.pmc.vendor_id),
        **change,
    }


def test_staff_add_a_recurring_work_order_and_the_due_ones_open_at_once(client, conn, staff, home):
    # From a year ago, every 3 months on the 15th: four or five due by today.
    year_ago = TODAY - timedelta(days=365)
    added = ok(
        client.post(
            "/api/work-orders/recurring", json=new_recurring(home, starts_on=str(year_ago))
        ),
        201,
    )
    listed = ok(client.get("/api/work-orders/recurring"))
    orders = ok(client.get("/api/work-orders?show=all"))["items"]
    first = ok(client.get(f"/api/work-orders/{orders[-1]['id']}"))

    assert added["opened"] in (4, 5)
    assert len(orders) == added["opened"]
    assert [
        (r["summary"], r["schedule"], r["priority"], r["vendor"], r["ends_on"]) for r in listed
    ] == [("Change the furnace filter", "every 3 months on the 15th", "high", "Vendor 1", None)]
    assert (
        listed[0]["last_opened_on"]
        == max(
            conn.execute(
                "SELECT recurring_on FROM trust_work_orders WHERE recurring_work_order_id = %s",
                (added["id"],),
            ).fetchall()
        )[0].isoformat()
    )
    assert (first["recurring"], first["priority"], first["vendor"], first["status"]) == (
        "every 3 months",
        "high",
        "Vendor 1",
        "assigned",
    )
    assert conn.execute(
        "SELECT recorded_by FROM trust_recurring_work_orders WHERE id = %s", (added["id"],)
    ).fetchone() == (staff.person_id,)


def test_the_nightly_run_opens_what_comes_due_and_an_end_stops_it(
    client, conn, app_conn, staff, home
):
    added = ok(
        client.post(
            "/api/work-orders/recurring",
            json=new_recurring(
                home,
                every_months=1,
                day_of_month=TODAY.day if TODAY.day <= 28 else 28,
                starts_on=str(TODAY + DAY),
                vendor_id=None,
            ),
        ),
        201,
    )
    assert added["opened"] == 0
    later = TODAY + timedelta(days=40)
    (ran,) = nightly.run_all(app_conn, later, "http://localhost", [home.pmc.pmc_id])
    assert ran.work_orders == 1
    assert ok(client.get("/api/work-orders/recurring"))[0]["schedule"].startswith(
        "every month on the "
    )

    ok(
        client.post(f"/api/work-orders/recurring/{added['id']}/end", json={"ends_on": str(later)}),
        204,
    )
    again = client.post(
        f"/api/work-orders/recurring/{added['id']}/end", json={"ends_on": str(later)}
    )
    (ran,) = nightly.run_all(
        app_conn, later + timedelta(days=90), "http://localhost", [home.pmc.pmc_id]
    )

    assert again.status_code == 409
    assert again.json()["detail"] == "this recurring work order has ended already"
    assert ran.work_orders == 0
    assert ok(client.get("/api/work-orders/recurring"))[0]["ends_on"] == str(later)


@pytest.mark.parametrize(
    ("change", "status", "detail"),
    [
        ({"every_months": 13}, 422, None),
        ({"day_of_month": 29}, 422, None),
        ({"summary": "  "}, 422, None),
        ({"priority": "soon"}, 422, None),
        ({"vendor_id": str(uuid.uuid4())}, 404, "no such property, unit or vendor in this company"),
        ({"property_id": "nope"}, 422, None),
    ],
)
def test_a_bad_recurring_work_order_is_refused(client, staff, home, change, status, detail):
    answer = client.post("/api/work-orders/recurring", json=new_recurring(home, **change))

    assert answer.status_code == status, answer.text
    if detail:
        assert answer.json()["detail"] == detail


def test_a_unit_of_another_property_is_refused_in_words(client, conn, staff, home):
    elsewhere = add_unit(conn, home.pmc.pmc_id, home.pmc.owners[1].property_id, "Unit 9")

    answer = client.post(
        "/api/work-orders/recurring", json=new_recurring(home, unit_id=str(elsewhere))
    )

    assert answer.status_code == 422
    assert answer.json()["detail"] == "that unit is not one of the property's"


def test_another_companys_recurring_work_order_is_not_found(client, conn, staff, home):
    unknown = client.post(
        f"/api/work-orders/recurring/{uuid.uuid4()}/end", json={"ends_on": str(TODAY)}
    )
    bad = client.post("/api/work-orders/recurring/not-an-id/end", json={"ends_on": str(TODAY)})

    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "no such recurring work order"
    assert bad.status_code == 422


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_reach_recurring_work_orders(client, conn, app_conn, home, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, home.pmc)
    elif caller == "tenant":
        join_as(client, app_conn, home.pmc.pmc_id, "tenant", home.tenant)
    expected = 401 if caller == "nobody" else 403

    answers = [
        client.get("/api/work-orders/recurring"),
        client.post("/api/work-orders/recurring", json=new_recurring(home)),
        client.post(f"/api/work-orders/recurring/{uuid.uuid4()}/end", json={"ends_on": str(TODAY)}),
    ]

    assert [a.status_code for a in answers] == [expected] * 3


@pytest.mark.parametrize(
    ("day", "word"),
    [
        (1, "1st"),
        (2, "2nd"),
        (3, "3rd"),
        (4, "4th"),
        (11, "11th"),
        (12, "12th"),
        (13, "13th"),
        (21, "21st"),
        (22, "22nd"),
        (23, "23rd"),
        (28, "28th"),
    ],
)
def test_a_day_of_the_month_reads_as_an_ordinal(day, word):
    assert recurring_work_orders.ordinal(day) == word
