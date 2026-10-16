"""Tenants, owners and contact details through the API (src/pms/api/directory.py, ADR 0005):
each list a page at a time, each person's page, renaming, archiving and contact details, and a
vendor's contact details. Always the session's PMC; the AI role never reads contact details."""

from datetime import UTC, datetime, timedelta

import pytest
from helpers import (
    add_tenant,
    add_unit,
    end_lease,
    enter_bill,
    make_pmc,
    open_lease,
    transfer_batch,
)
from people import join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
NOBODY = "00000000-0000-0000-0000-000000000000"
CONTACT = {
    "email": "tenant.one@example.com",
    "phone": "(555) 555-0100",
    "alternate_phone": None,
    "address_line_1": "100 Example Street",
    "address_line_2": None,
    "city": "Sampletown",
    "state": "ZZ",
    "postal_code": "00000",
}
EMPTY = dict.fromkeys(CONTACT)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def test_tenants_come_a_page_at_a_time_with_their_lease_and_contact(client, conn, staff, pmc):
    prop = pmc.owners[0].property_id
    first = tenant_of(conn, pmc)
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit 1")
    old = open_lease(conn, pmc.pmc_id, unit, TODAY - 400 * DAY, None, "900.00", [first])
    end_lease(conn, pmc.pmc_id, old, TODAY - 40 * DAY)
    now = open_lease(conn, pmc.pmc_id, unit, TODAY - 30 * DAY, None, "1000.00", [first])
    later = add_tenant(conn, pmc.pmc_id, prop, "Tenant 2")
    open_lease(
        conn,
        pmc.pmc_id,
        add_unit(conn, pmc.pmc_id, prop, "Unit 2"),
        TODAY + 5 * DAY,
        None,
        "1000.00",
        [later],
    )
    add_tenant(conn, pmc.pmc_id, pmc.owners[1].property_id, "Tenant 3")
    ok(client.post(f"/api/tenants/{first}/contact", json=CONTACT), 204)

    page = ok(client.get("/api/tenants", params={"per_page": 2}))
    assert (page["total"], page["page"], page["per_page"]) == (3, 1, 2)
    one, two = page["items"]
    assert one == {
        "id": str(first),
        "name": "Tenant 1",
        "property": {"id": str(prop), "name": "Property 1"},
        # The lease in effect, not the one before it.
        "lease": {"id": str(now), "unit": {"id": str(unit), "name": "Unit 1"}, "status": "current"},
        "email": "tenant.one@example.com",
        "phone": "(555) 555-0100",
        "archived": False,
        "owed": "0.00",
        "portal": "no login",
    }
    assert (two["name"], two["lease"]["status"], two["email"]) == ("Tenant 2", "future", None)
    last = ok(client.get("/api/tenants", params={"per_page": 2, "page": 2}))["items"]
    assert [(t["name"], t["lease"]) for t in last] == [("Tenant 3", None)]
    # Found by their name, property or unit.
    for q, names in (
        ("tenant 3", ["Tenant 3"]),
        ("property 2", ["Tenant 3"]),
        ("unit 2", ["Tenant 2"]),
    ):
        assert [
            t["name"] for t in ok(client.get("/api/tenants", params={"q": q}))["items"]
        ] == names


def test_a_tenant_shows_their_leases_money_held_and_contact(client, conn, staff, pmc):
    tenant = tenant_of(conn, pmc)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    old = open_lease(conn, pmc.pmc_id, unit, TODAY - 400 * DAY, None, "900.00", [tenant])
    end_lease(conn, pmc.pmc_id, old, TODAY - 40 * DAY)
    now = open_lease(conn, pmc.pmc_id, unit, TODAY - 30 * DAY, None, "1000.00", [tenant])
    transfer_batch(
        conn,
        [
            (pmc.deposit_cash, pmc.tenant_deposit, "1000.00"),
            (pmc.operating_cash, pmc.prepaid_rent, "25.00"),
        ],
    )

    page = ok(client.get(f"/api/tenants/{tenant}"))

    assert (page["name"], page["archived"], page["prepaid"], page["deposit"]) == (
        "Tenant 1",
        False,
        "25.00",
        "1000.00",
    )
    assert page["property"]["name"] == "Property 1"
    assert page["contact"] == EMPTY
    assert [
        (x["id"], x["status"], x["unit"]["name"], x["property"]["name"]) for x in page["leases"]
    ] == [
        (str(now), "current", "Unit 1", "Property 1"),
        (str(old), "ended", "Unit 1", "Property 1"),
    ]


def test_contact_details_are_kept_whole_and_a_change_is_audited(client, conn, staff, pmc):
    tenant = tenant_of(conn, pmc)
    ok(client.post(f"/api/tenants/{tenant}/contact", json=CONTACT), 204)
    # The same details again write nothing; new ones replace them all.
    ok(client.post(f"/api/tenants/{tenant}/contact", json=CONTACT), 204)
    ok(client.post(f"/api/tenants/{tenant}/contact", json={"phone": " 555-555-0101 "}), 204)

    assert ok(client.get(f"/api/tenants/{tenant}"))["contact"] == {**EMPTY, "phone": "555-555-0101"}
    rows = conn.execute(
        "SELECT a.staff_id, a.row_data ->> 'email' FROM trust_audit_log AS a"
        " WHERE a.table_name = 'trust_contacts' AND a.pmc_id = %s ORDER BY a.id",
        (pmc.pmc_id,),
    ).fetchall()
    me = staff.person_id
    assert rows == [(me, "tenant.one@example.com"), (me, None)]


def test_owners_come_a_page_at_a_time_and_an_owner_shows_their_accounts(client, conn, staff, pmc):
    one, two = pmc.owners
    transfer_batch(conn, [(pmc.operating_cash, one.account, "300.00")])
    ok(
        client.post(
            f"/api/owners/{one.owner_id}/contact", json={**EMPTY, "email": "owner1@example.com"}
        ),
        204,
    )

    page = ok(client.get("/api/owners"))
    assert page["total"] == 2
    assert page["items"][0] == {
        "id": str(one.owner_id),
        "name": "Owner 1",
        "properties": ["Property 1"],
        "held": "300.00",
        "email": "owner1@example.com",
        "phone": None,
        "archived": False,
    }
    # Found by a property's name too.
    found = ok(client.get("/api/owners", params={"q": "property 2"}))["items"]
    assert [o["name"] for o in found] == ["Owner 2"]

    owner = ok(client.get(f"/api/owners/{one.owner_id}"))
    assert (owner["name"], owner["archived"], owner["contact"]["email"]) == (
        "Owner 1",
        False,
        "owner1@example.com",
    )
    (account,) = owner["accounts"]
    assert (
        account["account"],
        account["property_id"],
        account["balance"],
        account["available"],
    ) == (
        one.account,
        str(one.property_id),
        "300.00",
        "300.00",
    )
    assert ok(client.get(f"/api/owners/{two.owner_id}"))["accounts"][0]["property"] == "Property 2"


def test_staff_rename_a_tenant_and_an_owner(client, conn, staff, pmc):
    tenant = tenant_of(conn, pmc)
    ok(client.post(f"/api/tenants/{tenant}/name", json={"name": " Tenant One "}), 204)
    ok(client.post(f"/api/owners/{pmc.owners[0].owner_id}/name", json={"name": "Owner One"}), 204)

    assert ok(client.get(f"/api/tenants/{tenant}"))["name"] == "Tenant One"
    assert ok(client.get(f"/api/owners/{pmc.owners[0].owner_id}"))["name"] == "Owner One"


def test_staff_archive_a_tenant_or_owner_only_when_nothing_is_open(client, conn, staff, pmc):
    tenant = tenant_of(conn, pmc)
    owner = pmc.owners[0]
    unit = add_unit(conn, pmc.pmc_id, owner.property_id, "Unit 1")
    lease = open_lease(conn, pmc.pmc_id, unit, TODAY - 30 * DAY, None, "1000.00", [tenant])
    enter_bill(conn, pmc.pmc_id, pmc.vendor_id, owner.account, "INV-1", TODAY, TODAY, "10.00")

    refused = client.post(f"/api/tenants/{tenant}/archive", json={"archived": True})
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "Tenant 1 is on a lease in effect or to come; end it before archiving"
    )
    refused = client.post(f"/api/owners/{owner.owner_id}/archive", json={"archived": True})
    assert (
        refused.json()["detail"]
        == "Owner 1 has a bill not paid; pay or decline it before archiving"
    )

    end_lease(conn, pmc.pmc_id, lease, TODAY - DAY)
    ok(client.post(f"/api/tenants/{tenant}/archive", json={"archived": True}), 204)
    assert ok(client.get(f"/api/tenants/{tenant}"))["archived"] is True
    assert ok(client.get("/api/tenants"))["items"] == []
    assert [
        t["id"] for t in ok(client.get("/api/tenants", params={"archived": True}))["items"]
    ] == [str(tenant)]
    ok(client.post(f"/api/tenants/{tenant}/archive", json={"archived": False}), 204)
    ok(client.post(f"/api/owners/{pmc.owners[1].owner_id}/archive", json={"archived": True}), 204)
    assert [o["name"] for o in ok(client.get("/api/owners"))["items"]] == ["Owner 1"]
    assert [
        o["name"] for o in ok(client.get("/api/owners", params={"archived": True}))["items"]
    ] == ["Owner 2"]
    assert ok(client.get(f"/api/owners/{pmc.owners[1].owner_id}"))["archived"] is True


def test_a_vendors_contact_details_show_on_the_vendors_list(client, conn, staff, pmc):
    ok(client.post(f"/api/vendors/{pmc.vendor_id}/contact", json={"phone": "555-555-0102"}), 204)

    (vendor,) = ok(client.get("/api/vendors"))
    assert vendor["contact"] == {**EMPTY, "phone": "555-555-0102"}


def test_everything_is_the_sessions_pmc(client, conn, staff, pmc):
    theirs = make_pmc(conn)
    tenant, owner = tenant_of(conn, theirs), theirs.owners[0].owner_id

    for path in (f"/api/tenants/{tenant}", f"/api/owners/{owner}"):
        assert client.get(path).status_code == 404, path
    for kind, who in (("tenants", tenant), ("owners", owner)):
        assert client.post(f"/api/{kind}/{who}/name", json={"name": "Mine"}).status_code == 404
        assert client.post(f"/api/{kind}/{who}/archive", json={"archived": True}).status_code == 404
        assert client.post(f"/api/{kind}/{who}/contact", json=EMPTY).status_code == 404
    assert client.post(f"/api/vendors/{theirs.vendor_id}/contact", json=EMPTY).status_code == 404
    assert [t["name"] for t in ok(client.get("/api/tenants"))["items"]] == ["Tenant 1"]
    assert ok(client.get("/api/owners"))["total"] == 2


GETS = ["/api/tenants", f"/api/tenants/{NOBODY}", "/api/owners", f"/api/owners/{NOBODY}"]
POSTS = [
    *((f"/api/{kind}/{NOBODY}/name", {"name": "x"}) for kind in ("tenants", "owners")),
    *((f"/api/{kind}/{NOBODY}/archive", {"archived": True}) for kind in ("tenants", "owners")),
    *((f"/api/{kind}/{NOBODY}/contact", {}) for kind in ("tenants", "owners", "vendors")),
]


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_see_or_change_people(client, conn, app_conn, pmc, caller):
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
        ("/api/tenants?per_page=201", None),
        ("/api/owners?page=0", None),
        ("/api/tenants/not-an-id", None),
        (f"/api/tenants/{NOBODY}/name", {"name": ""}),
        (f"/api/owners/{NOBODY}/archive", {"archived": "maybe"}),
        (f"/api/tenants/{NOBODY}/contact", {"email": "not an email"}),
        (f"/api/owners/{NOBODY}/contact", {"phone": "call me"}),
        (f"/api/vendors/{NOBODY}/contact", {"address_line_1": "100 Example Street"}),
        (f"/api/tenants/{NOBODY}/contact", {"address_line_2": "Suite 1"}),
        (f"/api/tenants/{NOBODY}/contact", {"city": " "}),
    ],
)
def test_bad_input_is_refused(client, staff, path, body):
    answer = client.get(path) if body is None else client.post(path, json=body)
    assert answer.status_code == 422, answer.text
