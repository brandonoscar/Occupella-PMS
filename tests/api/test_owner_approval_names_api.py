"""An owner's approval of a big bill is signed (migration 20261104000100; the founder's review of
11/03, item 1): over the company's amount (with the bill's markup; $5,000.00 unless staff set
another), the owner types their full name to approve in the portal, the server checks it is
theirs, and the approval keeps it. Who approves always comes from the session."""

from datetime import UTC, date, datetime

import pytest
from helpers import add_agreement, enter_bill, transfer_batch
from people import join_as_owner, join_as_staff, join_as_tenant

JAN_1, JAN_10 = date(2026, 1, 1), date(2026, 1, 10)


@pytest.fixture
def bills(conn, pmc):
    """Owner 1's account holds 9,000.00 over a 500.00 approval limit: a 6,000.00 roof bill over
    $5,000.00 and a 700.00 gutter bill under it."""
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, JAN_1, approval_limit="500")
    transfer_batch(
        conn,
        [(pmc.operating_cash, pmc.owners[0].account, "9000.00")],
        datetime(2026, 1, 3, 12, tzinfo=UTC),
    )

    def bill(reference, amount):
        return enter_bill(
            conn,
            pmc.pmc_id,
            pmc.vendor_id,
            pmc.owners[0].account,
            reference,
            JAN_10,
            JAN_10,
            amount,
        )

    return {"roof": bill("ROOF-9", "6000.00"), "gutter": bill("GUTTER-9", "700.00")}


def approval(conn, bill):
    return conn.execute(
        "SELECT approved_by, owner_id, signed_name FROM trust_bill_approvals WHERE bill_id = %s",
        (bill,),
    ).fetchone()


def test_a_bill_over_the_amount_takes_the_owners_own_name(client, conn, app_conn, pmc, bills):
    join_as_owner(client, app_conn, pmc)
    listed = {b["reference"]: b for b in client.get("/api/owner/bills").json()}
    assert (listed["ROOF-9"]["name_needed"], listed["ROOF-9"]["balance"]) == (True, "9000.00")
    assert listed["GUTTER-9"]["name_needed"] is False

    roof = f"/api/owner/bills/{bills['roof']}/approval"
    none = client.post(roof, json={})
    wrong = client.post(roof, json={"typed_name": "Somebody Else"})
    assert (none.status_code, none.json()["detail"]) == (
        422,
        "Type your full name to approve a bill over $5,000.00.",
    )
    assert (wrong.status_code, wrong.json()["detail"]) == (
        422,
        "That name doesn't match the name on your account.",
    )
    assert approval(conn, bills["roof"]) is None

    # Its case and spacing don't matter; what they typed is kept.
    assert client.post(roof, json={"typed_name": "  owner   1 "}).status_code == 201
    assert approval(conn, bills["roof"]) == (
        "Owner 1, in the owner portal",
        pmc.owners[0].owner_id,
        "owner   1",
    )


def test_a_bill_under_the_amount_takes_no_name(client, conn, app_conn, pmc, bills):
    join_as_owner(client, app_conn, pmc)
    assert client.post(f"/api/owner/bills/{bills['gutter']}/approval", json={}).status_code == 201
    assert approval(conn, bills["gutter"]) == (
        "Owner 1, in the owner portal",
        pmc.owners[0].owner_id,
        None,
    )


def test_who_approves_comes_from_the_session_never_the_request(client, conn, app_conn, pmc, bills):
    join_as_owner(client, app_conn, pmc)
    answer = client.post(
        f"/api/owner/bills/{bills['gutter']}/approval", json={"approved_by": "Owner 2"}
    )
    assert answer.status_code == 422
    assert approval(conn, bills["gutter"]) is None


def test_staff_set_the_amount_and_it_holds_from_then_on(
    client, other_client, conn, app_conn, pmc, bills
):
    join_as_staff(client, conn, app_conn, pmc)
    assert client.get("/api/company/owner-approvals").json() == {"sign_over": "5000.00"}
    assert client.post("/api/company/owner-approvals", json={"sign_over": "500.00"}).json() == {
        "sign_over": "500.00"
    }
    client.post("/api/company/owner-approvals", json={"sign_over": "500.00"})  # the same: nothing
    assert conn.execute(
        "SELECT count(*), max(recorded_by::text) IS NOT NULL FROM trust_owner_approval_settings"
        " WHERE pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchone() == (1, True)

    join_as_owner(other_client, app_conn, pmc)
    gutter = f"/api/owner/bills/{bills['gutter']}/approval"
    assert other_client.post(gutter, json={}).status_code == 422
    assert other_client.post(gutter, json={"typed_name": "Owner 1"}).status_code == 201


@pytest.mark.parametrize("body", [{"sign_over": "-1.00"}, {"sign_over": "1.001"}, {}])
def test_a_bad_amount_is_refused(client, conn, app_conn, pmc, body):
    join_as_staff(client, conn, app_conn, pmc)
    assert client.post("/api/company/owner-approvals", json=body).status_code == 422


def test_only_staff_read_or_set_the_amount(client, other_client, conn, app_conn, pmc):
    join_as_owner(client, app_conn, pmc)
    join_as_tenant(other_client, conn, app_conn, pmc)
    for caller in (client, other_client):
        assert caller.get("/api/company/owner-approvals").status_code in (401, 403)
        assert caller.post(
            "/api/company/owner-approvals", json={"sign_over": "1.00"}
        ).status_code in (401, 403)
