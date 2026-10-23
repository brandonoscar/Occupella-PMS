"""Announcements through the API (migration 20261023000203, src/pms/api/announcements.py): who an
announcement would go to before staff write it, writing it to every tenant living at or every
owner of a property, a property group or the whole company (one email per address, the rest
named), each on the person's Email tab as an announcement, and the list and page of what was
sent with whether each email went. Every name and address here is synthetic."""

from datetime import UTC, datetime, timedelta

import pytest
from helpers import add_tenant, add_unit, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

NOWHERE = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Clerk 1")


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def today():
    return datetime.now(UTC).date()


def lived_in(conn, pmc, names, owner=0, since=30, until=None):
    """A unit of one of the PMC's properties with a lease for these tenants, from `since` days ago
    (and until a day, if given)."""
    prop = pmc.owners[owner].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, f"Unit {names[0]}")
    tenants = [add_tenant(conn, pmc.pmc_id, prop, name) for name in names]
    open_lease(
        conn, pmc.pmc_id, unit, today() - timedelta(days=since), until, "1000.00", tenants
    )  # fmt: skip
    return tenants


def contact(client, kind, person, address):
    ok(client.post(f"/api/{kind}s/{person}/contact", json={"email": address}), 204)


def announce(client, **body):
    return client.post(
        "/api/announcements",
        json={"subject": "Water off Tuesday", "body": "From 9 to 11.", **body},
    )


def test_every_tenant_of_the_company_gets_one_email_each(client, conn, staff, pmc):
    a, b = lived_in(conn, pmc, ["Tenant A", "Tenant B"])
    (c,) = lived_in(conn, pmc, ["Tenant C"], owner=1)
    lived_in(conn, pmc, ["Tenant D"], since=400, until=today() - timedelta(days=5))  # moved out
    contact(client, "tenant", a, "a@example.com")
    contact(client, "tenant", c, "c@example.com")

    preview = ok(client.get("/api/announcements/recipients", params={"audience": "tenants"}))
    assert [(r["name"], r["to"]) for r in preview["emailed"]] == [
        ("Tenant A", "a@example.com"),
        ("Tenant C", "c@example.com"),
    ]
    assert preview["without_email"] == ["Tenant B"]
    assert preview["words"] == "every tenant living at the company's properties"

    sent = ok(announce(client, audience="tenants"), 201)
    assert [r["to"] for r in sent["emailed"]] == ["a@example.com", "c@example.com"]
    assert sent["without_email"] == ["Tenant B"]
    # On each tenant's Email tab, as an announcement from the clerk, signed.
    (line,) = ok(client.get("/api/emails", params={"about": "tenant", "about_id": str(a)}))
    assert (line["kind"], line["by"], line["subject"]) == (
        "announcement",
        "Clerk 1",
        "Water off Tuesday",
    )
    assert line["body"].startswith("From 9 to 11.\n\nClerk 1\n")
    assert ok(client.get("/api/emails", params={"about": "tenant", "about_id": str(b)})) == []


def test_one_property_or_one_group_and_one_email_per_address(client, conn, staff, pmc):
    a, b = lived_in(conn, pmc, ["Tenant A", "Tenant B"])
    (c,) = lived_in(conn, pmc, ["Tenant C"], owner=1)
    for person in (a, b):  # two people, one address
        contact(client, "tenant", person, "household@example.com")
    contact(client, "tenant", c, "c@example.com")
    first = pmc.owners[0].property_id
    here = ok(announce(client, audience="tenants", property_id=str(first)), 201)
    assert [(r["name"], r["to"]) for r in here["emailed"]] == [
        ("Tenant A", "household@example.com")
    ]
    assert here["words"].startswith("every tenant living at ")
    # The list says where, the property's name as it is.
    (listed,) = ok(client.get("/api/announcements"))["items"]
    name = conn.execute(
        "SELECT display_name FROM trust_properties WHERE id = %s", (first,)
    ).fetchone()[0]
    assert listed["words"] == f"Every tenant living at {name}"

    group = ok(
        client.post(
            "/api/property-groups",
            json={"name": "South", "property_ids": [str(pmc.owners[1].property_id)]},
        ),
        201,
    )["id"]
    grouped = ok(announce(client, audience="tenants", group_id=group), 201)
    assert [r["to"] for r in grouped["emailed"]] == ["c@example.com"]
    assert grouped["words"] == "every tenant living at the South group's properties"


def test_every_owner_gets_it_and_the_list_says_what_went(client, conn, staff, pmc):
    owner_a, owner_b = (o.owner_id for o in pmc.owners)
    contact(client, "owner", owner_a, "owner.a@example.com")
    sent = ok(announce(client, audience="owners", subject="Year-end statements"), 201)
    assert [r["to"] for r in sent["emailed"]] == ["owner.a@example.com"]
    assert len(sent["without_email"]) == 1
    (line,) = ok(client.get("/api/emails", params={"about": "owner", "about_id": str(owner_a)}))
    assert line["kind"] == "announcement"

    listed = ok(client.get("/api/announcements"))
    assert listed["total"] == 1
    (row,) = listed["items"]
    assert (row["subject"], row["words"], row["by"], row["emails"], row["sent"], row["failed"]) == (
        "Year-end statements",
        "Every owner of the company's properties",
        "Clerk 1",
        1,
        0,
        0,
    )
    page = ok(client.get(f"/api/announcements/{row['id']}"))
    assert page["body"] == "From 9 to 11."
    assert [(e["to"], e["status"]) for e in page["emails"]] == [("owner.a@example.com", "waiting")]
    assert client.get(f"/api/announcements/{NOWHERE}").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"audience": "vendors"},
        {"audience": "tenants", "subject": ""},
        {"audience": "tenants", "subject": "Two\nlines"},
        {"audience": "tenants", "body": ""},
        {"audience": "tenants", "property_id": NOWHERE, "group_id": NOWHERE},
        {"audience": "tenants", "property_id": "x"},
    ],
)
def test_bad_input_is_refused(client, staff, body):
    assert announce(client, **body).status_code == 422


def test_no_one_there_or_another_companys_place_is_refused(client, conn, staff, pmc):
    empty = announce(client, audience="tenants", property_id=str(pmc.owners[1].property_id))
    assert empty.status_code == 422 and "no one is" in empty.json()["detail"]
    theirs = make_pmc(conn).owners[0].property_id
    assert announce(client, audience="tenants", property_id=str(theirs)).status_code == 404
    assert announce(client, audience="owners", group_id=NOWHERE).status_code == 404
    assert (
        client.get(
            "/api/announcements/recipients", params={"audience": "tenants", "property_id": NOWHERE}
        ).status_code
        == 404
    )


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_announce(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    assert announce(client, audience="tenants").status_code == status
    assert client.get("/api/announcements").status_code == status
    assert (
        client.get("/api/announcements/recipients", params={"audience": "owners"}).status_code
        == status
    )
    assert client.get(f"/api/announcements/{NOWHERE}").status_code == status


def test_a_custom_role_without_email_reads_but_may_not_announce(
    client, other_client, conn, app_conn, staff, pmc
):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 2", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Rent", "areas": ["rent"]}), 201)
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )
    refused = announce(other_client, audience="owners")
    assert refused.status_code == 403 and "email" in refused.json()["detail"]
    assert other_client.get("/api/announcements").status_code == 200
