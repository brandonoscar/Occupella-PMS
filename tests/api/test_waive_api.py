"""Waiving a charge through the API (src/pms/api/rent.py, migration 0079): staff take a late fee
or any charge off a lease, all or part, with why; it comes off that charge first, never past what
is left of it. Every name here is synthetic."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from helpers import add_unit, charge, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of

TODAY = datetime.now(UTC).date()
START = TODAY - timedelta(days=40)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Clerk 1")


@pytest.fixture
def lease(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    made = open_lease(conn, pmc.pmc_id, unit, START, None, "1000.00", [tenant_of(conn, pmc)])
    charge(conn, pmc.pmc_id, made, START, "1000.00", memo="Rent")
    return made


@pytest.fixture
def fee(conn, pmc, lease):
    return charge(conn, pmc.pmc_id, lease, START + timedelta(days=6), "50.00", "fee", "Late fee")


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def waive(client, charge_id, amount="50.00", reason="First time late"):
    return client.post(
        f"/api/rent/charges/{charge_id}/waive", json={"amount": amount, "reason": reason}
    )


def open_charges(client, lease):
    return {c["memo"]: c["open"] for c in ok(client.get(f"/api/leases/{lease}"))["open_charges"]}


def test_staff_waive_a_late_fee_and_it_comes_off_that_fee(client, conn, staff, pmc, lease, fee):
    assert open_charges(client, lease) == {"Rent": "1000.00", "Late fee": "50.00"}
    done = ok(waive(client, fee, "20.00"), 201)
    assert (done["charge_id"], done["amount"], done["open"]) == (str(fee), "20.00", "30.00")
    assert open_charges(client, lease) == {"Rent": "1000.00", "Late fee": "30.00"}
    ok(waive(client, fee, "30.00"), 201)
    shown = ok(client.get(f"/api/leases/{lease}"))
    assert shown["owed"] == "1000.00"
    assert [c["memo"] for c in shown["open_charges"]] == ["Rent"]
    # The ledger shows the credit; the waiver names who waived it and why.
    lines = ok(client.get(f"/api/leases/{lease}/ledger"))["lines"]
    assert [ln["memo"] for ln in lines if ln["kind"] == "credit"] == [
        f"Waived: Late fee, due {START + timedelta(days=6):%m/%d/%Y}"
    ] * 2
    assert (
        conn.execute(
            "SELECT reason, recorded_by FROM trust_charge_waivers WHERE waived_charge_id = %s",
            (fee,),
        ).fetchall()
        == [("First time late", staff.person_id)] * 2
    )


def test_no_more_than_is_left_of_a_charge_is_waived(client, conn, staff, pmc, lease, fee):
    ok(
        client.post(
            f"/api/rent/leases/{lease}/payments",
            json=json.loads(
                json.dumps(
                    {"request_id": uuid.uuid4(), "amount": "1010.00", "received_on": TODAY},
                    default=str,
                )
            ),
        ),
        201,
    )  # pays the rent, and 10.00 of the fee
    refused = waive(client, fee, "45.00")
    assert refused.status_code == 409 and "40.00 is left" in refused.json()["detail"]
    assert conn.execute(
        "SELECT count(*) FROM trust_charges WHERE lease_id = %s AND kind = 'credit'", (lease,)
    ).fetchone() == (0,)  # the refused credit was not kept
    ok(waive(client, fee, "40.00"), 201)


def test_a_credit_or_another_companys_charge_is_not_waived(client, conn, staff, pmc, lease):
    credit = charge(conn, pmc.pmc_id, lease, START, "10.00", "credit", "Concession")
    refused = waive(client, credit, "10.00")
    assert refused.status_code == 422 and "credit" in refused.json()["detail"]
    other = make_pmc(conn)
    unit = add_unit(conn, other.pmc_id, other.owners[0].property_id)
    theirs = open_lease(conn, other.pmc_id, unit, START, None, "900.00", [tenant_of(conn, other)])
    their_rent = charge(conn, other.pmc_id, theirs, START, "900.00")
    assert waive(client, their_rent, "10.00").status_code == 404
    assert waive(client, uuid.uuid4(), "10.00").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"amount": "0", "reason": "x"},
        {"amount": "-5.00", "reason": "x"},
        {"amount": "1.005", "reason": "x"},
        {"amount": "5.00", "reason": " "},
        {"amount": "5.00"},
        {"reason": "x"},
    ],
)
def test_bad_waivers_are_refused(client, staff, fee, body):
    assert client.post(f"/api/rent/charges/{fee}/waive", json=body).status_code == 422


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_waive(client, conn, app_conn, pmc, fee, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    assert waive(client, fee).status_code == (401 if caller == "nobody" else 403)


def test_a_custom_role_without_rent_may_not_waive(
    client, other_client, conn, app_conn, staff, pmc, fee
):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 2", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Books", "areas": ["bills"]}), 201)
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )
    assert waive(other_client, fee).status_code == 403
