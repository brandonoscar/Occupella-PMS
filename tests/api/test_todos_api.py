"""Staff to-dos through the API (migration 20261023000203, src/pms/api/todos.py): writing one
assigned to someone on staff with a day it is due, maybe about a property; reassigning it or
moving its day; marking it done once; the list (open, done or all; theirs or everyone's); and the
Dashboard's part, each staff member's own due within a week or overdue. Any staff member keeps
them, whatever their custom roles. Every name here is synthetic."""

from datetime import UTC, datetime, timedelta

import pytest
from helpers import make_pmc
from people import join_as_owner, join_as_staff, join_as_tenant

NOWHERE = "00000000-0000-0000-0000-000000000000"


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def day(offset=0):
    return (datetime.now(UTC).date() + timedelta(days=offset)).isoformat()


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Clerk 1")


@pytest.fixture
def clerk(other_client, conn, app_conn, pmc):
    """A second staff member, with no custom role's areas but rent."""
    return join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 2", roles=[])


def add(client, to, due, **more):
    return client.post(
        "/api/todos",
        json={"title": "Call the roofer", "assigned_to": str(to), "due_on": due, **more},
    )


def test_staff_write_assign_move_and_finish_a_todo(client, other_client, conn, staff, clerk, pmc):
    prop = pmc.owners[0].property_id
    made = ok(
        add(client, clerk.person_id, day(3), notes="About the leak.", property_id=str(prop)), 201
    )
    assert (made["assigned_to"], made["due_on"], made["written_by"], made["overdue"]) == (
        "Clerk 2",
        day(3),
        "Clerk 1",
        False,
    )
    assert made["property"] is not None and made["notes"] == "About the leak."
    # The clerk sees it as theirs; the writer doesn't, but everyone's list has it.
    assert [
        t["id"] for t in ok(other_client.get("/api/todos", params={"mine": True}))["items"]
    ] == [made["id"]]
    assert ok(client.get("/api/todos", params={"mine": True}))["items"] == []
    assert ok(client.get("/api/todos"))["total"] == 1

    moved = ok(
        client.post(
            f"/api/todos/{made['id']}/assign",
            json={"assigned_to": str(staff.person_id), "due_on": day(-1)},
        )
    )
    assert (moved["assigned_to"], moved["overdue"]) == ("Clerk 1", True)
    same = client.post(
        f"/api/todos/{made['id']}/assign",
        json={"assigned_to": str(staff.person_id), "due_on": day(-1)},
    )
    assert ok(same) == moved
    rows = conn.execute(
        "SELECT count(*) FROM trust_todo_assignments WHERE todo_id = %s", (made["id"],)
    ).fetchone()[0]
    assert rows == 2  # the same again wrote nothing

    finished = ok(other_client.post(f"/api/todos/{made['id']}/done", json={}))
    assert (finished["done_by"], finished["overdue"]) == ("Clerk 2", False)
    assert ok(client.post(f"/api/todos/{made['id']}/done", json={}))["done_by"] == "Clerk 2"
    refused = client.post(
        f"/api/todos/{made['id']}/assign",
        json={"assigned_to": str(staff.person_id), "due_on": day(9)},
    )
    assert (refused.status_code, refused.json()["detail"]) == (409, "that to-do is done already")
    assert ok(client.get("/api/todos"))["items"] == []
    assert [t["id"] for t in ok(client.get("/api/todos", params={"status": "done"}))["items"]] == [
        made["id"]
    ]


def test_the_list_puts_the_soonest_first_and_the_dashboard_shows_mine_within_a_week(
    client, conn, staff, clerk
):
    later = ok(add(client, staff.person_id, day(20), title="Order filters"), 201)
    soon = ok(add(client, staff.person_id, day(2), title="Inspect the roof"), 201)
    late = ok(add(client, staff.person_id, day(-3), title="Renew the permit"), 201)
    ok(add(client, clerk.person_id, day(1), title="Someone else's"), 201)
    mine = ok(client.get("/api/todos", params={"mine": True}))["items"]
    assert [t["id"] for t in mine] == [late["id"], soon["id"], later["id"]]
    dashboard = ok(client.get("/api/today"))["todos"]
    assert [(t["title"], t["overdue"]) for t in dashboard] == [
        ("Renew the permit", True),
        ("Inspect the roof", False),
    ]


@pytest.mark.parametrize(
    "body",
    [
        {"title": "", "assigned_to": NOWHERE, "due_on": "2026-10-23"},
        {"title": "Two\nlines", "assigned_to": NOWHERE, "due_on": "2026-10-23"},
        {"title": "Call", "assigned_to": "x", "due_on": "2026-10-23"},
        {"title": "Call", "assigned_to": NOWHERE, "due_on": "soon"},
        {"title": "Call", "due_on": "2026-10-23"},
    ],
)
def test_bad_input_is_refused(client, staff, body):
    assert client.post("/api/todos", json=body).status_code == 422


def test_someone_or_somewhere_of_another_company_is_not_found(client, conn, app_conn, staff):
    other = make_pmc(conn)
    assert add(client, NOWHERE, day()).status_code == 404
    elsewhere = add(client, staff.person_id, day(), property_id=str(other.owners[0].property_id))
    assert elsewhere.status_code == 404
    assert client.post(f"/api/todos/{NOWHERE}/done", json={}).status_code == 404
    made = ok(add(client, staff.person_id, day()), 201)
    assert (
        client.post(
            f"/api/todos/{made['id']}/assign", json={"assigned_to": NOWHERE, "due_on": day()}
        ).status_code
        == 404
    )


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_keep_todos(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    assert client.get("/api/todos").status_code == status
    assert add(client, NOWHERE, day()).status_code == status
    assert client.post(f"/api/todos/{NOWHERE}/done", json={}).status_code == status
    assert (
        client.post(
            f"/api/todos/{NOWHERE}/assign", json={"assigned_to": NOWHERE, "due_on": day()}
        ).status_code
        == status
    )


def test_any_staff_member_keeps_them_whatever_their_roles(
    client, other_client, conn, app_conn, staff, clerk
):
    role = ok(client.post("/api/custom-roles", json={"name": "Rent", "areas": ["rent"]}), 201)
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )
    made = ok(add(other_client, staff.person_id, day(1)), 201)
    ok(other_client.post(f"/api/todos/{made['id']}/done", json={}))
