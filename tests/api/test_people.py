"""Staff see who in their PMC can sign in (src/pms/api/people.py)."""

import pytest
from helpers import add_staff, make_pmc
from people import PASSWORD, invite_token, join_as_owner, join_as_staff, join_as_tenant


def test_staff_see_everyone_and_where_their_login_stands(client, other_client, conn, app_conn, pmc):
    staff = join_as_staff(client, conn, app_conn, pmc)
    owner = join_as_owner(other_client, app_conn, pmc, n=0)
    invite_token(app_conn, pmc.pmc_id, "owner", pmc.owners[1].owner_id, "o2@example.com")
    half = add_staff(conn, pmc.pmc_id, "Half Enrolled")
    token = invite_token(app_conn, pmc.pmc_id, "staff", half, "half@example.com")
    other_client.post("/api/invites/accept", json={"token": token, "password": PASSWORD})
    make_pmc(conn)  # another PMC's people never show

    people = client.get("/api/people").json()

    assert [(p["kind"], p["name"], p["email"], p["status"]) for p in people] == [
        ("staff", "Half Enrolled", "half@example.com", "invited"),
        ("staff", "Staff 1", staff.email, "active"),
        ("owner", "Owner 1", owner.email, "active"),
        ("owner", "Owner 2", "o2@example.com", "invited"),
        ("tenant", "Tenant 1", None, "no login"),
    ]


def test_a_disabled_login_says_so(client, other_client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc)
    owner = join_as_owner(other_client, app_conn, pmc)
    client.post(f"/api/logins/{owner.login_id}/disable", json={})

    statuses = {p["name"]: p["status"] for p in client.get("/api/people").json()}

    assert statuses["Owner 1"] == "disabled"


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_see_the_people(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    assert client.get("/api/people").status_code == (401 if caller == "nobody" else 403)
