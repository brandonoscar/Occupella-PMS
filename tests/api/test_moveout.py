"""Staff's move-outs (src/pms/api/moveout.py): end a lease, charge what the tenants owe at
move-out, and return their deposits, keeping what the lease owes. Every lease here is
synthetic."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import add_tenant, add_unit, make_pmc, open_account, open_lease, transfer_batch
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of

TODAY = datetime.now(UTC).date()
ENDED = TODAY - timedelta(days=5)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Manager 1")


@pytest.fixture
def lease(conn, pmc):
    """Tenant 1's month-to-month lease of Unit 1 at Property 1, with a 1200.00 deposit."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease_id = open_lease(
        conn, pmc.pmc_id, unit, TODAY - timedelta(days=200), None, "1000.00", [tenant_of(conn, pmc)]
    )
    transfer_batch(
        conn,
        [(pmc.deposit_cash, pmc.tenant_deposit, "1200.00")],
        datetime.combine(TODAY - timedelta(days=200), datetime.min.time(), UTC),
    )
    return lease_id


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert detail in answer.json()["detail"]


def listed(client):
    return {m["lease_id"]: m for m in ok(client.get("/api/move-out"))}


def give_back(client, lease, on=TODAY, request_id=None, **more):
    body = {"request_id": str(request_id or uuid.uuid4()), "on": str(on)} | more
    return client.post(f"/api/move-out/{lease}/return", json=body)


def test_staff_see_the_leases_that_hold_a_deposit(client, conn, staff, pmc, lease):
    # A lease with no deposit held has nothing to return and isn't listed.
    other = add_unit(conn, pmc.pmc_id, pmc.owners[1].property_id, "Unit 2")
    tenant = add_tenant(conn, pmc.pmc_id, pmc.owners[1].property_id, "Tenant 2")
    open_lease(conn, pmc.pmc_id, other, TODAY, None, "900.00", [tenant])

    shown = listed(client)

    assert list(shown) == [str(lease)]
    assert {k: shown[str(lease)][k] for k in ("property", "unit", "tenants", "owed", "held")} == {
        "property": "Property 1",
        "unit": "Unit 1",
        "tenants": ["Tenant 1"],
        "owed": "0.00",
        "held": "1200.00",
    }
    assert shown[str(lease)]["owner_accounts"] == [
        {"id": pmc.owners[0].account, "owner": "Owner 1"}
    ]
    assert shown[str(lease)]["settled"] is None


def test_a_move_out_ends_the_lease_charges_damage_and_returns_the_rest(
    client, conn, staff, pmc, lease
):
    ok(client.post(f"/api/move-out/{lease}/end", json={"ends_on": str(ENDED)}), 204)
    made = ok(
        client.post(
            f"/api/move-out/{lease}/deductions",
            json={"amount": "350.00", "due_on": str(ENDED), "memo": "  Damage: broken window "},
        ),
        201,
    )
    shown = listed(client)[str(lease)]
    assert shown["ends_on"] == str(ENDED)
    assert shown["owed"] == "350.00"
    assert shown["open_charges"] == [
        {
            "id": made["id"],
            "due_on": str(ENDED),
            "kind": "fee",
            "memo": "Damage: broken window",
            "open": "350.00",
            "home": None,  # the lease's own
        }
    ]
    assert shown["carried"] == "0.00"  # no lease moved into it

    request = uuid.uuid4()
    assert ok(give_back(client, lease, request_id=request), 201) == {"returned": "850.00"}
    assert ok(give_back(client, lease, request_id=request), 201) == {"returned": "850.00"}
    refused(give_back(client, lease), 409, "was returned already")

    shown = listed(client)[str(lease)]
    assert shown["settled"] == {
        "held": "1200.00",
        "kept": "350.00",
        "returned": "850.00",
        "returned_on": str(TODAY),
    }
    assert (shown["held"], shown["owed"], shown["open_charges"]) == ("0.00", "0.00", [])
    letter = client.get(f"/api/reports/trust_report_deposit_return?arg={lease}&format=pdf")
    assert letter.status_code == 200
    assert letter.headers["content-type"] == "application/pdf"
    # Once returned, the lease's days and charges at move-out stay as they were.
    refused(client.post(f"/api/move-out/{lease}/end", json={"ends_on": str(TODAY)}), 409)
    refused(
        client.post(
            f"/api/move-out/{lease}/deductions",
            json={"amount": "5.00", "due_on": str(TODAY), "memo": "Late"},
        ),
        409,
        "returned already",
    )


def test_a_deposit_goes_back_once_the_lease_has_ended_and_not_ahead_of_time(
    client, staff, pmc, lease
):
    refused(give_back(client, lease), 409, "has not ended")
    refused(give_back(client, lease, on=TODAY + timedelta(days=1)), 422, "not a later one")
    refused(
        client.post(
            f"/api/move-out/{lease}/end", json={"ends_on": str(TODAY - timedelta(days=400))}
        ),
        422,
    )


def test_kept_money_goes_to_the_owners_account_staff_choose(client, conn, staff, pmc, lease):
    second = open_account(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        "owner_property",
        pmc.owners[1].owner_id,
        pmc.owners[0].property_id,
    )
    ok(client.post(f"/api/move-out/{lease}/end", json={"ends_on": str(ENDED)}), 204)
    assert len(listed(client)[str(lease)]["owner_accounts"]) == 2

    refused(give_back(client, lease), 422, "is not the one owner's account")
    refused(give_back(client, lease, owner_account=pmc.owners[1].account), 422)
    ok(give_back(client, lease, owner_account=second), 201)


def test_returning_a_deposit_takes_the_disburse_role(
    client, other_client, conn, app_conn, staff, pmc, lease
):
    join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=[])
    ok(other_client.post(f"/api/move-out/{lease}/end", json={"ends_on": str(ENDED)}), 204)

    refused(give_back(other_client, lease), 403, "Clerk 1 may not pay out money")
    assert listed(client)[str(lease)]["settled"] is None


def test_another_pmcs_lease_is_not_found(client, conn, staff, pmc):
    theirs = make_pmc(conn)
    lease = open_lease(
        conn,
        theirs.pmc_id,
        add_unit(conn, theirs.pmc_id, theirs.owners[0].property_id),
        TODAY - timedelta(days=30),
        None,
        "1000.00",
        [tenant_of(conn, theirs)],
    )
    for answer in (
        client.post(f"/api/move-out/{lease}/end", json={"ends_on": str(ENDED)}),
        client.post(
            f"/api/move-out/{lease}/deductions",
            json={"amount": "5.00", "due_on": str(TODAY), "memo": "Cleaning"},
        ),
        give_back(client, lease),
    ):
        refused(answer, 404, "no such lease")


ENDPOINTS = [
    ("get", "/api/move-out"),
    ("post", f"/api/move-out/{uuid.uuid4()}/end"),
    ("post", f"/api/move-out/{uuid.uuid4()}/deductions"),
    ("post", f"/api/move-out/{uuid.uuid4()}/return"),
]


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_only_staff_handle_move_outs(client, conn, app_conn, pmc, caller, method, path):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    answer = client.get(path) if method == "get" else client.post(path, json={})

    assert answer.status_code == (401 if caller == "nobody" else 403)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("end", {"ends_on": "soon"}),
        ("deductions", {"amount": "0", "due_on": str(TODAY), "memo": "x"}),
        ("deductions", {"amount": "1.001", "due_on": str(TODAY), "memo": "x"}),
        ("deductions", {"amount": "5.00", "due_on": str(TODAY), "memo": "  "}),
        ("return", {"request_id": "x", "on": str(TODAY)}),
        ("return", {"request_id": str(uuid.uuid4())}),
    ],
)
def test_bad_input_is_refused(client, staff, path, body):
    refused(client.post(f"/api/move-out/{uuid.uuid4()}/{path}", json=body), 422)


def test_amounts_stay_to_the_cent(client, conn, staff, pmc, lease):
    transfer_batch(
        conn,
        [(pmc.deposit_cash, pmc.tenant_deposit, Decimal("0.05"))],
        datetime.combine(TODAY, datetime.min.time(), UTC),
    )
    assert listed(client)[str(lease)]["held"] == "1200.05"
