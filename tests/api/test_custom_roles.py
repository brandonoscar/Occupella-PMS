"""Custom staff roles (src/pms/api/custom_roles.py, the area check in src/pms/api/deps.py): an
admin names a role and the areas it may change and gives it to staff; a staff member with
custom roles changes only those areas, and still sees everything; one with none works
everywhere."""

import uuid
from datetime import date

import pytest
from fastapi.routing import APIRoute
from helpers import make_pmc
from people import join_as_staff

from pms.api.deps import AREA_PATHS, AREAS, area_of, staff_only

# Staff writes that are no area's, and why: the database holds them to the admin role, or they
# are the staff member's own.
NOT_AN_AREA = {
    "/api/staff": "adds a staff member: admin",
    "/api/staff/{staff_id}/roles": "changes a role: admin",
    "/api/logins/{login_id}/disable": "disables a login: admin",
    "/api/custom-roles": "makes a custom role: admin",
    "/api/custom-roles/{role_id}/areas": "changes a custom role: admin",
    "/api/custom-roles/{role_id}/staff": "gives a custom role: admin",
    "/api/signers": "names a trust bank account's broker (admin) or its signers (the broker)",
    "/api/calendar/mine": "makes the staff member's own calendar link: theirs alone",
    "/api/calendar/mine/revoke": "takes the staff member's own calendar link back",
}


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert detail in str(answer.json()["detail"]), answer.text


@pytest.fixture
def admin(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Admin 1")


@pytest.fixture
def clerk(other_client, conn, app_conn, pmc, admin):
    """No role of the database's, signed in on the other browser."""
    return join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=[])


def make_role(client, name, areas):
    return ok(client.post("/api/custom-roles", json={"name": name, "areas": areas}), 201)["id"]


def give(client, role, person, granted=True):
    ok(
        client.post(
            f"/api/custom-roles/{role}/staff",
            json={"staff_id": str(person.person_id), "granted": granted},
        ),
        204,
    )


def contribute(client, pmc):
    return client.post(
        "/api/payables/contributions",
        json={
            "request_id": str(uuid.uuid4()),
            "account": pmc.owners[0].account,
            "amount": "10.00",
            "on": str(date.today()),
        },
    )


def journal(client, pmc):
    return client.post(
        "/api/journal",
        json={
            "event_on": str(date.today()),
            "memo": "Correction",
            "lines": [
                {
                    "from_account": pmc.owners[0].account,
                    "to_account": pmc.owners[1].account,
                    "amount": "1.00",
                }
            ],
        },
    )


def staff_writes(app):
    """Every route that changes something and takes a staff session, by its path."""

    def routes(found):
        for route in found:
            if hasattr(route, "original_router"):  # an included router, as FastAPI keeps it
                yield from routes(route.original_router.routes)
            else:
                yield route

    def staffs(dependant):
        return dependant.call is staff_only or any(staffs(d) for d in dependant.dependencies)

    return {
        route.path
        for route in routes(app.routes)
        if isinstance(route, APIRoute)
        and route.methods - {"GET", "HEAD"}
        and staffs(route.dependant)
    }


def test_every_staff_write_is_in_an_area_or_said_not_to_be(client):
    writes = staff_writes(client.app)

    assert writes  # the walk found them
    unmapped = {path for path in writes if area_of(path) is None}
    assert unmapped == set(NOT_AN_AREA)
    # Every area guards something, and every prefix is some staff write's.
    assert {area_of(path) for path in writes} - {None} == set(AREAS)
    assert all(any(p.startswith(prefix) for p in writes) for prefix, _ in AREA_PATHS)


def test_a_staff_member_with_no_custom_role_works_everywhere(other_client, pmc, clerk):
    assert ok(other_client.get("/api/me"))["areas"] == list(AREAS)
    ok(journal(other_client, pmc), 201)


def test_a_role_limits_what_its_staff_change_and_not_what_they_see(
    client, other_client, conn, pmc, admin, clerk
):
    role = make_role(client, "Bookkeeping", ["journal", "bills"])
    give(client, role, clerk)

    assert ok(other_client.get("/api/me"))["areas"] == ["bills", "journal"]
    ok(journal(other_client, pmc), 201)
    refused(
        contribute(other_client, pmc),
        403,
        "your roles don't let you change owners' money: an admin can add it",
    )
    # They still see the owners' money, and an admin still changes it.
    ok(other_client.get("/api/payables/owners"))
    ok(contribute(client, pmc), 201)
    assert ok(client.get("/api/me"))["areas"] == list(AREAS)

    # Another role adds its areas; taking both away leaves them working everywhere again.
    owners = make_role(client, "Owners", ["owners"])
    give(client, owners, clerk)
    ok(contribute(other_client, pmc), 201)
    give(client, role, clerk, granted=False)
    refused(journal(other_client, pmc), 403, "change the journal")
    give(client, owners, clerk, granted=False)
    ok(journal(other_client, pmc), 201)


def test_an_area_taken_from_a_role_is_taken_from_its_staff(client, other_client, pmc, admin, clerk):
    role = make_role(client, "Bookkeeping", ["journal"])
    give(client, role, clerk)
    ok(journal(other_client, pmc), 201)

    ok(
        client.post(f"/api/custom-roles/{role}/areas", json={"area": "journal", "granted": False}),
        204,
    )

    refused(journal(other_client, pmc), 403, "change the journal")
    assert ok(other_client.get("/api/me"))["areas"] == []
    ok(
        client.post(f"/api/custom-roles/{role}/areas", json={"area": "journal", "granted": True}),
        204,
    )
    ok(journal(other_client, pmc), 201)


def test_staff_see_every_role_its_areas_and_who_holds_it(client, other_client, pmc, admin, clerk):
    role = make_role(client, "Leasing agent", ["leasing", "documents", "leasing"])
    give(client, role, clerk)
    make_role(client, "Auditor", [])

    found = ok(other_client.get("/api/custom-roles"))

    assert [(a["area"], a["label"]) for a in found["areas"]][:3] == [
        ("rent", "rent"),
        ("bills", "bills"),
        ("owners", "owners' money"),
    ]
    assert [(r["name"], r["areas"], [s["name"] for s in r["staff"]]) for r in found["roles"]] == [
        ("Auditor", [], []),
        ("Leasing agent", ["documents", "leasing"], ["Clerk 1"]),
    ]


def test_asking_for_what_already_holds_writes_nothing(client, conn, pmc, admin, clerk):
    role = make_role(client, "Bookkeeping", ["journal"])
    give(client, role, clerk)
    give(client, role, clerk)
    ok(
        client.post(f"/api/custom-roles/{role}/areas", json={"area": "journal", "granted": True}),
        204,
    )
    ok(client.post(f"/api/custom-roles/{role}/areas", json={"area": "rent", "granted": False}), 204)

    counts = conn.execute(
        "SELECT (SELECT count(*) FROM trust_custom_role_areas WHERE role_id = %s),"
        " (SELECT count(*) FROM trust_staff_custom_roles WHERE role_id = %s)",
        (role, role),
    ).fetchone()
    assert counts == (1, 1)
    # Each change names the admin who made it.
    assert conn.execute(
        "SELECT changed_by FROM trust_staff_custom_roles WHERE role_id = %s", (role,)
    ).fetchone() == (admin.person_id,)


def test_only_an_admin_changes_custom_roles(client, other_client, pmc, admin, clerk):
    refused(
        other_client.post("/api/custom-roles", json={"name": "Mine", "areas": []}),
        403,
        "Clerk 1 may not change a custom role (the admin role)",
    )
    role = make_role(client, "Bookkeeping", ["journal"])
    refused(
        other_client.post(
            f"/api/custom-roles/{role}/areas", json={"area": "rent", "granted": True}
        ),
        403,
        "may not change a custom role",
    )
    refused(
        other_client.post(
            f"/api/custom-roles/{role}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        403,
        "may not change a custom role",
    )
    # Nobody signed in: nothing.
    other_client.post("/api/sign-out", json={})
    refused(other_client.get("/api/custom-roles"), 401)


def test_a_role_is_named_once_and_names_known_areas_staff_and_roles(
    client, other_client, conn, app_conn, pmc, admin
):
    role = make_role(client, "Bookkeeping", [])
    refused(
        client.post("/api/custom-roles", json={"name": " Bookkeeping ", "areas": []}),
        409,
        "a role of that name already",
    )
    for body in ({"name": " ", "areas": []}, {"name": "X", "areas": ["payroll"]}, {"name": "X"}):
        refused(client.post("/api/custom-roles", json=body), 422)
    refused(
        client.post(f"/api/custom-roles/{role}/areas", json={"area": "payroll", "granted": True}),
        422,
    )
    refused(
        client.post(
            f"/api/custom-roles/{uuid.uuid4()}/areas", json={"area": "rent", "granted": True}
        ),
        404,
        "no such role",
    )

    other = make_pmc(conn)
    stranger = join_as_staff(other_client, conn, app_conn, other, name="Admin 2")
    refused(
        client.post(
            f"/api/custom-roles/{role}/staff",
            json={"staff_id": str(stranger.person_id), "granted": True},
        ),
        404,
        "no such staff member in this PMC",
    )
    # Another company's admin finds no such role.
    refused(
        other_client.post(
            f"/api/custom-roles/{role}/areas", json={"area": "rent", "granted": True}
        ),
        404,
        "no such role",
    )
    assert ok(other_client.get("/api/custom-roles"))["roles"] == []
