"""Property groups through the API (src/pms/api/groups.py, migration 0065): staff make a group,
rename it and choose its properties, and every list page narrows to a group or one property
(`?group=`, `?property=`; src/pms/api/scope.py). Always the session's PMC."""

from datetime import UTC, datetime, timedelta

import pytest
from helpers import add_tenant, add_unit, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
NOWHERE = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail):
    assert answer.status_code == status, answer.text
    assert detail in str(answer.json()["detail"]), answer.text


def make_group(client, name, property_ids):
    return ok(
        client.post(
            "/api/property-groups",
            json={"name": name, "property_ids": [str(p) for p in property_ids]},
        ),
        201,
    )["id"]


def test_staff_make_rename_and_choose_a_groups_properties(client, conn, staff, pmc):
    first, second = (o.property_id for o in pmc.owners)
    north = make_group(client, "North portfolio", [first])
    make_group(client, "East", [])

    listed = ok(client.get("/api/property-groups"))
    assert [(g["name"], [p["name"] for p in g["properties"]]) for g in listed["groups"]] == [
        ("East", []),
        ("North portfolio", ["Property 1"]),
    ]
    assert [p["name"] for p in listed["properties"]] == ["Property 1", "Property 2"]

    ok(client.post(f"/api/property-groups/{north}/name", json={"name": "North"}), 204)
    choose = {"property_ids": [str(second)]}
    ok(client.post(f"/api/property-groups/{north}/properties", json=choose), 204)
    # The same choice again changes nothing, and writes nothing.
    ok(client.post(f"/api/property-groups/{north}/properties", json=choose), 204)

    group = next(g for g in ok(client.get("/api/property-groups"))["groups"] if g["id"] == north)
    assert (group["name"], [p["name"] for p in group["properties"]]) == ("North", ["Property 2"])
    # Property 1 left and Property 2 joined, each a row naming who did it.
    rows = conn.execute(
        "SELECT property_id, member, recorded_by FROM trust_property_group_members"
        " WHERE group_id = %s ORDER BY created_at, id",
        (north,),
    ).fetchall()
    who = staff.person_id
    assert rows == [(first, True, who), (second, True, who), (first, False, who)]


def test_an_archived_property_is_not_offered(client, conn, staff, pmc):
    second = pmc.owners[1].property_id
    ok(client.post(f"/api/properties/{second}/archive", json={"archived": True}), 204)
    assert [p["name"] for p in ok(client.get("/api/property-groups"))["properties"]] == [
        "Property 1"
    ]


def test_a_group_name_is_once_per_company_whatever_its_case(client, conn, staff, pmc):
    north = make_group(client, "North", [])
    again = client.post("/api/property-groups", json={"name": "north"})
    refused(again, 409, "called north already")
    south = make_group(client, "South", [])
    refused(
        client.post(f"/api/property-groups/{south}/name", json={"name": "NORTH"}),
        409,
        "called NORTH already",
    )
    # Another company may use the name.
    other = make_pmc(conn)
    conn.execute(
        "INSERT INTO trust_property_groups (pmc_id, display_name) VALUES (%s, 'North')",
        (other.pmc_id,),
    )
    assert north


def test_another_companys_group_or_property_is_not_found(client, conn, staff, pmc):
    other = make_pmc(conn)
    theirs = conn.execute(
        "INSERT INTO trust_property_groups (pmc_id, display_name) VALUES (%s, 'Theirs')"
        " RETURNING id",
        (other.pmc_id,),
    ).fetchone()[0]
    refused(client.post(f"/api/property-groups/{theirs}/name", json={"name": "x"}), 404, "no such")
    refused(
        client.post(f"/api/property-groups/{theirs}/properties", json={"property_ids": []}),
        404,
        "no such property group",
    )
    refused(
        client.post(
            "/api/property-groups",
            json={"name": "Mixed", "property_ids": [str(other.owners[0].property_id)]},
        ),
        404,
        "no such property",
    )
    # Nothing was made.
    assert [g["name"] for g in ok(client.get("/api/property-groups"))["groups"]] == []
    # A list narrowed to another company's group or property shows nothing.
    conn.execute(
        "INSERT INTO trust_property_group_members (pmc_id, group_id, property_id, member)"
        " VALUES (%s, %s, %s, true)",
        (other.pmc_id, theirs, other.owners[0].property_id),
    )
    assert ok(client.get("/api/properties", params={"group": str(theirs)}))["items"] == []
    assert (
        ok(client.get("/api/properties", params={"property": str(other.owners[0].property_id)}))[
            "items"
        ]
        == []
    )


def test_every_list_narrows_to_a_group_or_one_property(client, conn, staff, pmc):
    first, second = (o.property_id for o in pmc.owners)
    for prop, unit_name, who in ((first, "Unit 1", "Tenant A"), (second, "Unit 9", "Tenant B")):
        unit = add_unit(conn, pmc.pmc_id, prop, unit_name)
        tenant = add_tenant(conn, pmc.pmc_id, prop, who)
        open_lease(conn, pmc.pmc_id, unit, TODAY - timedelta(days=30), None, "1500.00", [tenant])
    north = make_group(client, "North", [second])
    lists = {
        "/api/properties": lambda row: row["name"],
        "/api/leases": lambda row: row["unit"]["name"],
        "/api/tenants": lambda row: row["name"],
        "/api/owners": lambda row: row["name"],
    }
    for path, name in lists.items():
        everyone = [name(r) for r in ok(client.get(path))["items"]]
        in_north = [name(r) for r in ok(client.get(path, params={"group": north}))["items"]]
        one = [name(r) for r in ok(client.get(path, params={"property": str(first)}))["items"]]
        assert set(in_north) | set(one) == set(everyone), path
        assert in_north and one and not set(in_north) & set(one), path

    def names(path):
        return [r["name"] for r in ok(client.get(path, params={"group": north}))["items"]]

    assert (names("/api/tenants"), names("/api/owners")) == (["Tenant B"], ["Owner 2"])
    # Both at once: a property of the group.
    both = ok(client.get("/api/properties", params={"group": north, "property": str(first)}))
    assert both["items"] == []


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_see_or_change_property_groups(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    assert client.get("/api/property-groups").status_code == status
    for path, body in (
        ("/api/property-groups", {"name": "North"}),
        (f"/api/property-groups/{NOWHERE}/name", {"name": "North"}),
        (f"/api/property-groups/{NOWHERE}/properties", {"property_ids": []}),
    ):
        assert client.post(path, json=body).status_code == status, path


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/property-groups", {"name": " "}),
        ("/api/property-groups", {"name": "x" * 101}),
        ("/api/property-groups", {"name": "North", "property_ids": ["not-an-id"]}),
        (f"/api/property-groups/{NOWHERE}/name", {}),
        (f"/api/property-groups/{NOWHERE}/properties", {"property_ids": "all"}),
        ("/api/property-groups/not-an-id/name", {"name": "North"}),
    ],
)
def test_bad_input_is_refused(client, staff, path, body):
    assert client.post(path, json=body).status_code == 422


@pytest.mark.parametrize("path", ["/api/properties", "/api/leases", "/api/tenants", "/api/owners"])
def test_a_scope_must_be_an_id(client, staff, path):
    assert client.get(path, params={"group": "north"}).status_code == 422
    assert client.get(path, params={"property": "one"}).status_code == 422
