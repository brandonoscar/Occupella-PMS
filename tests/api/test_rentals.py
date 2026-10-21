"""Rentals through the API (src/pms/api/rentals.py, ADR 0005): the properties a page at a time, a
property with its owners and units, a unit with its leases, the rent roll a page at a time, one
lease whole, renaming and archiving a property or unit, and search. Always the session's PMC."""

from datetime import UTC, datetime, timedelta

import pytest
from helpers import (
    add_tenant,
    add_unit,
    charge,
    end_lease,
    make_pmc,
    open_lease,
    transfer_batch,
)
from people import join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def lease_at(conn, pmc, prop, unit_name, tenant_name, starts, rent="1500.00"):
    unit = add_unit(conn, pmc.pmc_id, prop, unit_name)
    tenant = add_tenant(conn, pmc.pmc_id, prop, tenant_name)
    return unit, open_lease(conn, pmc.pmc_id, unit, starts, None, rent, [tenant])


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def test_properties_come_a_page_at_a_time_with_owners_units_and_occupancy(client, conn, staff, pmc):
    first = pmc.owners[0].property_id
    lease_at(conn, pmc, first, "Unit 1", "Tenant A", TODAY - 30 * DAY)
    add_unit(conn, pmc.pmc_id, first, "Unit 2")

    listed = ok(client.get("/api/properties"))
    assert (listed["total"], listed["page"], listed["per_page"]) == (2, 1, 50)
    assert [(p["name"], p["owners"], p["units"], p["occupied"]) for p in listed["items"]] == [
        ("Property 1", ["Owner 1"], 2, 1),
        ("Property 2", ["Owner 2"], 0, 0),
    ]
    second_page = ok(client.get("/api/properties", params={"per_page": 1, "page": 2}))
    assert ([p["name"] for p in second_page["items"]], second_page["total"]) == (["Property 2"], 2)
    assert [
        p["name"] for p in ok(client.get("/api/properties", params={"q": "ty 2"}))["items"]
    ] == ["Property 2"]
    # A search's own % matches only a %.
    assert ok(client.get("/api/properties", params={"q": "%"}))["items"] == []


def test_a_property_shows_its_owners_money_and_each_units_lease(client, conn, staff, pmc):
    owner = pmc.owners[0]
    transfer_batch(conn, [(pmc.operating_cash, owner.account, "250.00")])
    now, _ = lease_at(conn, pmc, owner.property_id, "Unit 1", "Tenant A", TODAY - 30 * DAY)
    later, coming = lease_at(conn, pmc, owner.property_id, "Unit 2", "Tenant B", TODAY + 9 * DAY)
    add_unit(conn, pmc.pmc_id, owner.property_id, "Unit 3")

    found = ok(client.get(f"/api/properties/{owner.property_id}"))

    assert (found["name"], found["archived"]) == ("Property 1", False)
    assert [(o["owner"], o["balance"], o["bank_account"]) for o in found["owners"]] == [
        ("Owner 1", "250.00", "Synthetic operating trust account")
    ]
    units = {u["name"]: u for u in found["units"]}
    assert units["Unit 1"]["lease"]["status"] == "current"
    assert units["Unit 1"]["lease"]["tenants"] == ["Tenant A"]
    assert (units["Unit 2"]["lease"]["id"], units["Unit 2"]["lease"]["status"]) == (
        str(coming),
        "future",
    )
    assert units["Unit 3"]["lease"] is None


def test_a_property_shows_the_terms_in_force_today(client, conn, staff, pmc):
    owner = pmc.owners[0]
    bare = ok(client.get(f"/api/properties/{owner.property_id}"))
    assert (bare["late_fees"], bare["nsf_fee"], bare["owners"][0]["agreement"]) == (
        None,
        None,
        None,
    )

    for path, body in (
        (
            "agreements",
            {
                "account": owner.account,
                "starts_on": str(TODAY - 9 * DAY),
                "fee_percent": "8",
                "reserve": "200.00",
            },
        ),
        (
            "agreements",
            {"account": owner.account, "starts_on": str(TODAY + 5 * DAY), "fee_percent": "9"},
        ),
        (
            "late-fees",
            {
                "property_id": str(owner.property_id),
                "starts_on": str(TODAY),
                "grace_days": 3,
                "flat_fee": "25.00",
            },
        ),
        (
            "nsf-fees",
            {"property_id": str(owner.property_id), "starts_on": str(TODAY - DAY), "fee": "35.00"},
        ),
    ):
        ok(client.post(f"/api/setup/{path}", json=body), 201)

    found = ok(client.get(f"/api/properties/{owner.property_id}"))
    terms = found["owners"][0]["agreement"]
    assert (terms["starts_on"], terms["fee_percent"], terms["reserve"]) == (
        str(TODAY - 9 * DAY),
        "8",
        "200.00",
    )
    assert (found["late_fees"]["grace_days"], found["late_fees"]["flat_fee"]) == (3, "25.00")
    assert found["late_fees"]["maximum"] is None
    assert found["nsf_fee"] == {"starts_on": str(TODAY - DAY), "fee": "35.00"}
    # Another property's terms are its own.
    other = ok(client.get(f"/api/properties/{pmc.owners[1].property_id}"))
    assert (other["late_fees"], other["nsf_fee"]) == (None, None)


def test_a_unit_shows_every_lease_it_has_had_newest_first(client, conn, staff, pmc):
    prop = pmc.owners[0].property_id
    unit, old = lease_at(conn, pmc, prop, "Unit 1", "Tenant A", TODAY - 400 * DAY)
    end_lease(conn, pmc.pmc_id, old, TODAY - 31 * DAY)
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant B")
    new = open_lease(conn, pmc.pmc_id, unit, TODAY - 30 * DAY, None, "1600.00", [tenant])

    found = ok(client.get(f"/api/units/{unit}"))

    assert (found["name"], found["property"]["name"]) == ("Unit 1", "Property 1")
    assert [(lease["id"], lease["status"], lease["monthly_rent"]) for lease in found["leases"]] == [
        (str(new), "current", "1600.00"),
        (str(old), "ended", "1500.00"),
    ]


def test_the_rent_roll_shows_leases_in_effect_those_that_owe_or_all(client, conn, staff, pmc):
    first, second = (o.property_id for o in pmc.owners)
    _, a = lease_at(conn, pmc, first, "Unit 1", "Tenant A", TODAY - 30 * DAY)
    _, b = lease_at(conn, pmc, second, "Unit 9", "Tenant B", TODAY - 30 * DAY, "900.00")
    _, ended = lease_at(conn, pmc, first, "Unit 2", "Tenant C", TODAY - 90 * DAY)
    end_lease(conn, pmc.pmc_id, ended, TODAY - 10 * DAY)
    charge(conn, pmc.pmc_id, ended, TODAY - 20 * DAY, "75.00")

    roll = ok(client.get("/api/leases"))
    assert [
        (r["property"]["name"], r["unit"]["name"], r["tenants"], r["monthly_rent"])
        for r in roll["items"]
    ] == [
        ("Property 1", "Unit 1", ["Tenant A"], "1500.00"),
        ("Property 2", "Unit 9", ["Tenant B"], "900.00"),
    ]
    owing = ok(client.get("/api/leases", params={"show": "owing"}))["items"]
    assert [(r["id"], r["owed"]) for r in owing] == [(str(ended), "75.00")]
    assert ok(client.get("/api/leases", params={"show": "all"}))["total"] == 3
    one = ok(client.get("/api/leases", params={"property": str(second)}))["items"]
    assert [r["id"] for r in one] == [str(b)]
    by_tenant = ok(client.get("/api/leases", params={"q": "tenant a"}))["items"]
    assert [r["id"] for r in by_tenant] == [str(a)]
    paged = ok(client.get("/api/leases", params={"per_page": 1, "page": 2}))
    assert ([r["id"] for r in paged["items"]], paged["total"]) == ([str(b)], 2)


def test_a_lease_comes_whole_ended_or_not(client, conn, staff, pmc):
    prop = pmc.owners[0].property_id
    unit, lease = lease_at(conn, pmc, prop, "Unit 1", "Tenant A", TODAY - 90 * DAY)
    end_lease(conn, pmc.pmc_id, lease, TODAY - 10 * DAY)
    charge(conn, pmc.pmc_id, lease, TODAY - 20 * DAY, "75.00")

    found = ok(client.get(f"/api/leases/{lease}"))

    assert (found["id"], found["unit_id"], found["property_id"]) == (
        str(lease),
        str(unit),
        str(prop),
    )
    assert (found["owed"], found["ends_on"]) == ("75.00", str(TODAY - 10 * DAY))
    assert [c["open"] for c in found["open_charges"]] == ["75.00"]


def test_staff_rename_a_property_and_a_unit_and_the_audit_log_keeps_the_old_name(
    client, conn, staff, pmc
):
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit 1")

    ok(client.post(f"/api/properties/{prop}/name", json={"name": "  Elm Court  "}), 204)
    ok(client.post(f"/api/units/{unit}/name", json={"name": "Unit 1A"}), 204)

    assert ok(client.get(f"/api/properties/{prop}"))["name"] == "Elm Court"
    assert ok(client.get(f"/api/units/{unit}"))["name"] == "Unit 1A"
    assert conn.execute(
        "SELECT before ->> 'display_name', row_data ->> 'display_name', staff_id"
        " FROM trust_audit_log WHERE table_name = 'trust_properties' AND action = 'update'"
        " AND pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall() == [("Property 1", "Elm Court", staff.person_id)]


def test_staff_archive_and_bring_back_a_unit_and_a_property_only_when_nothing_is_open(
    client, conn, staff, pmc
):
    owner = pmc.owners[0]
    unit, lease = lease_at(conn, pmc, owner.property_id, "Unit 1", "Tenant A", TODAY - 30 * DAY)

    refused = client.post(f"/api/units/{unit}/archive", json={"archived": True})
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        "Unit 1 has a lease in effect or to come; end it before archiving",
    )
    end_lease(conn, pmc.pmc_id, lease, TODAY - DAY)
    ok(client.post(f"/api/units/{unit}/archive", json={"archived": True}), 204)
    assert ok(client.get(f"/api/units/{unit}"))["archived"] is True
    # An archived unit leaves the property's count; archived properties leave the list.
    assert ok(client.get("/api/properties"))["items"][0]["units"] == 0
    ok(client.post(f"/api/properties/{owner.property_id}/archive", json={"archived": True}), 204)
    assert [p["name"] for p in ok(client.get("/api/properties"))["items"]] == ["Property 2"]
    archived = ok(client.get("/api/properties", params={"archived": True}))["items"]
    assert [(p["name"], p["archived"]) for p in archived] == [("Property 1", True)]
    assert (
        client.post(f"/api/properties/{owner.property_id}/archive", json={"archived": True}).json()[
            "detail"
        ]
        == "it is archived already"
    )
    ok(client.post(f"/api/properties/{owner.property_id}/archive", json={"archived": False}), 204)
    assert len(ok(client.get("/api/properties"))["items"]) == 2


def test_search_finds_properties_units_tenants_and_owners_with_the_page_each_opens(
    client, conn, staff, pmc
):
    prop = pmc.owners[0].property_id
    unit, _ = lease_at(conn, pmc, prop, "Maple 1", "Jordan Maple", TODAY - 30 * DAY)
    (tenant,) = conn.execute(
        "SELECT id FROM trust_tenants WHERE display_name = 'Jordan Maple'"
    ).fetchone()
    conn.execute("UPDATE trust_properties SET display_name = 'Maple Court' WHERE id = %s", (prop,))
    conn.execute(
        "UPDATE trust_owners SET display_name = 'Maple Holdings' WHERE id = %s",
        (pmc.owners[0].owner_id,),
    )
    # An archived owner is found no more.
    conn.execute(
        "UPDATE trust_owners SET display_name = 'Maple Gone' WHERE id = %s",
        (pmc.owners[1].owner_id,),
    )
    conn.execute(
        "INSERT INTO trust_archivals (pmc_id, owner_id, archived) VALUES (%s, %s, true)",
        (pmc.pmc_id, pmc.owners[1].owner_id),
    )
    theirs = make_pmc(conn)
    add_unit(conn, theirs.pmc_id, theirs.owners[0].property_id, "Maple 9")

    found = ok(client.get("/api/search", params={"q": "maple"}))

    assert [(f["kind"], f["label"], f["detail"], f["href"]) for f in found] == [
        ("property", "Maple Court", "", f"/properties/{prop}"),
        ("unit", "Maple 1", "Maple Court", f"/units/{unit}"),
        ("tenant", "Jordan Maple", "Maple 1, Maple Court", f"/tenants/{tenant}"),
        ("owner", "Maple Holdings", "", f"/owners/{pmc.owners[0].owner_id}"),
    ]


def test_everything_is_the_sessions_pmc(client, conn, staff, pmc):
    theirs = make_pmc(conn)
    unit, lease = lease_at(conn, theirs, theirs.owners[0].property_id, "Unit 1", "Tenant Z", TODAY)
    prop = theirs.owners[0].property_id

    for path in (f"/api/properties/{prop}", f"/api/units/{unit}", f"/api/leases/{lease}"):
        assert client.get(path).status_code == 404
    for path in (f"/api/properties/{prop}/name", f"/api/units/{unit}/name"):
        assert client.post(path, json={"name": "Mine now"}).status_code == 404
    for path in (f"/api/properties/{prop}/archive", f"/api/units/{unit}/archive"):
        assert client.post(path, json={"archived": True}).status_code == 404
    assert [p["name"] for p in ok(client.get("/api/properties"))["items"]] == [
        "Property 1",
        "Property 2",
    ]
    assert ok(client.get("/api/leases", params={"show": "all"}))["total"] == 0


GETS = [
    "/api/properties",
    "/api/properties/00000000-0000-0000-0000-000000000000",
    "/api/units/00000000-0000-0000-0000-000000000000",
    "/api/leases",
    "/api/leases/00000000-0000-0000-0000-000000000000",
    "/api/search?q=unit",
]
POSTS = [
    ("/api/properties/00000000-0000-0000-0000-000000000000/name", {"name": "x"}),
    ("/api/properties/00000000-0000-0000-0000-000000000000/archive", {"archived": True}),
    ("/api/units/00000000-0000-0000-0000-000000000000/name", {"name": "x"}),
    ("/api/units/00000000-0000-0000-0000-000000000000/archive", {"archived": True}),
]


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_see_or_change_rentals(client, conn, app_conn, pmc, caller):
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
    ("method", "path", "body"),
    [
        ("get", "/api/properties?per_page=0", None),
        ("get", "/api/properties?per_page=201", None),
        ("get", "/api/properties?page=0", None),
        ("get", "/api/leases?show=late", None),
        ("get", "/api/leases?property=not-an-id", None),
        ("get", "/api/search?q=a", None),
        ("get", "/api/search", None),
        ("get", "/api/properties/not-an-id", None),
        ("post", "/api/properties/00000000-0000-0000-0000-000000000000/name", {"name": " "}),
        ("post", "/api/units/00000000-0000-0000-0000-000000000000/name", {"name": "x" * 201}),
        ("post", "/api/units/00000000-0000-0000-0000-000000000000/archive", {"archived": "maybe"}),
    ],
)
def test_bad_input_is_refused(client, staff, method, path, body):
    answer = client.get(path) if method == "get" else client.post(path, json=body)
    assert answer.status_code == 422, answer.text
