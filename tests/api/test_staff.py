"""The PMC's staff and their roles (src/pms/api/staff.py), and the role each staff action takes:
paying out money (disburse), approving (approve), inviting or disabling a staff login (admin)."""

import uuid
from datetime import UTC, datetime, time
from decimal import Decimal

import pytest
from helpers import add_staff, enter_bill, make_pmc, set_aside_bill, transfer_batch
from people import join_as_owner, join_as_staff, join_as_tenant, login_of


@pytest.fixture
def admin(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Admin 1")


@pytest.fixture
def clerk(other_client, conn, app_conn, pmc, admin):
    """A staff member with no role, signed in on the other browser."""
    return join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=[])


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert detail in answer.json()["detail"]


def members(client):
    return {m["name"]: m["roles"] for m in ok(client.get("/api/staff"))}


def test_staff_see_who_holds_what_and_their_own_roles(client, other_client, admin, clerk):
    assert members(other_client) == {
        "Admin 1": ["admin", "approve", "disburse"],
        "Clerk 1": [],
    }
    assert ok(client.get("/api/me"))["roles"] == ["admin", "approve", "disburse"]
    assert ok(other_client.get("/api/me"))["roles"] == []


def test_an_owner_or_tenant_holds_no_role(client, conn, app_conn, pmc):
    join_as_owner(client, app_conn, pmc)
    assert ok(client.get("/api/me"))["roles"] == []


def test_an_admin_adds_a_staff_member_and_changes_roles(client, admin, clerk):
    added = ok(client.post("/api/staff", json={"name": "  Clerk 2 "}), 201)
    ok(
        client.post(f"/api/staff/{added['id']}/roles", json={"role": "disburse", "granted": True}),
        204,
    )
    ok(
        client.post(
            f"/api/staff/{clerk.person_id}/roles", json={"role": "approve", "granted": True}
        ),
        204,
    )

    assert members(client) == {
        "Admin 1": ["admin", "approve", "disburse"],
        "Clerk 1": ["approve"],
        "Clerk 2": ["disburse"],
    }
    refused(
        client.post(
            f"/api/staff/{clerk.person_id}/roles", json={"role": "approve", "granted": True}
        ),
        409,
        "holds the approve role already",
    )
    refused(
        client.post(
            f"/api/staff/{admin.person_id}/roles", json={"role": "admin", "granted": False}
        ),
        409,
        "is the PMC's last admin",
    )


def test_only_an_admin_adds_staff_or_changes_roles(other_client, admin, clerk):
    refused(other_client.post("/api/staff", json={"name": "Clerk 2"}), 403, "only an admin")
    refused(
        other_client.post(
            f"/api/staff/{clerk.person_id}/roles", json={"role": "admin", "granted": True}
        ),
        403,
        "Clerk 1 may not change a role (the admin role)",
    )
    assert members(other_client)["Clerk 1"] == []


def test_another_pmcs_staff_member_is_not_found(client, conn, admin):
    theirs = add_staff(conn, make_pmc(conn).pmc_id)
    refused(
        client.post(f"/api/staff/{theirs}/roles", json={"role": "approve", "granted": True}),
        404,
        "no such staff member",
    )
    assert "Staff 1" not in members(client)


def test_paying_out_money_takes_the_disburse_role(client, other_client, conn, pmc, admin, clerk):
    owner = pmc.owners[0].account
    # The page records money at the start of its day (UTC).
    today = datetime.combine(datetime.now(UTC).date(), time(0), UTC)
    transfer_batch(conn, [(pmc.operating_cash, owner, "1000.00")], today)
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, owner, "INV-1", today.date(), today.date(), Decimal("50")
    )
    conn.execute(
        "INSERT INTO trust_bill_approvals (pmc_id, bill_id, approved_on, approved_by)"
        " VALUES (%s, %s, %s, 'Owner 1')",
        (pmc.pmc_id, bill, today.date()),
    )
    set_aside_bill(conn, pmc.pmc_id, bill, today)

    refused(
        other_client.post(f"/api/payables/bills/{bill}/pay", json={"on": str(today.date())}),
        403,
        "Clerk 1 may not pay out money (the disburse role)",
    )
    refused(
        other_client.post(
            "/api/payables/draws",
            json={
                "request_id": str(uuid.uuid4()),
                "account": owner,
                "amount": "10.00",
                "on": str(today.date()),
            },
        ),
        403,
        "Clerk 1 may not pay out money",
    )
    ok(client.post(f"/api/payables/bills/{bill}/pay", json={"on": str(today.date())}), 201)


def test_only_an_admin_invites_or_disables_a_staff_login(
    client, other_client, conn, app_conn, pmc, admin, clerk
):
    newcomer = add_staff(conn, pmc.pmc_id, "Staff 3")
    staff_invite = {"kind": "staff", "person_id": str(newcomer), "email": "staff3@example.com"}
    refused(
        other_client.post("/api/invites", json=staff_invite),
        403,
        "Clerk 1 may not invite a staff member",
    )
    # Owners and tenants are any staff member's to invite.
    ok(
        other_client.post(
            "/api/invites",
            json={
                "kind": "owner",
                "person_id": str(pmc.owners[0].owner_id),
                "email": "owner1@example.com",
            },
        ),
        201,
    )
    ok(client.post("/api/invites", json=staff_invite), 201)
    login = login_of(app_conn, "staff", newcomer)

    refused(
        other_client.post(f"/api/logins/{login}/disable", json={}),
        403,
        "Clerk 1 may not disable a staff login",
    )
    ok(client.post(f"/api/logins/{login}/disable", json={}), 204)


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/staff"),
        ("post", "/api/staff"),
        ("post", f"/api/staff/{uuid.uuid4()}/roles"),
    ],
)
def test_only_staff_see_and_change_roles(client, conn, app_conn, pmc, caller, method, path):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    answer = client.get(path) if method == "get" else client.post(path, json={})

    assert answer.status_code == (401 if caller == "nobody" else 403)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("", {"name": "   "}),
        ("", {"name": "x" * 201}),
        (f"/{uuid.uuid4()}/roles", {"role": "owner", "granted": True}),
        (f"/{uuid.uuid4()}/roles", {"role": "approve"}),
        ("/x/roles", {"role": "approve", "granted": True}),
    ],
)
def test_bad_input_is_refused(client, admin, path, body):
    refused(client.post(f"/api/staff{path}", json=body), 422)
