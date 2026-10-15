"""Renters insurance through the API (src/pms/api/rent.py, tenant.py, today.py): staff set
whether a lease requires it and record a policy; a tenant records their own from the portal,
with any insurer; the rent page, the portal and Today show how each lease stands."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from helpers import add_unit, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def lease(conn, pmc):
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, TODAY - 90 * DAY, None, "1500.00", [tenant])


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json()


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def post(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def terms(client, lease, **body):
    return post(
        client,
        f"/api/rent/leases/{lease}/renters-insurance-terms",
        **({"required": True, "minimum_liability": "100000.00", "starts_on": TODAY} | body),
    )


def policy(**change):
    return {
        "carrier": "Carrier A",
        "policy_number": "RI-1",
        "starts_on": TODAY - 30 * DAY,
        "expires_on": TODAY + 335 * DAY,
        "liability_limit": "300000.00",
        "landlord_interested_party": True,
    } | change


def insurance(client, lease):
    (found,) = [x for x in ok(client.get("/api/rent")) if x["id"] == str(lease)]
    return found["renters_insurance"]


def test_staff_require_it_and_the_tenant_records_their_policy(
    client, other_client, conn, app_conn, staff, pmc, lease
):
    ok(terms(client, lease), 201)
    assert insurance(client, lease) == {
        "required": True,
        "minimum_liability": "100000.00",
        "carrier": None,
        "policy_number": None,
        "expires_on": None,
        "liability_limit": None,
        "standing": "none on file",
        "below_minimum": False,
    }
    waiting = ok(client.get("/api/today"))["renters_insurance"]
    assert [(x["unit"], x["standing"]) for x in waiting] == [("Unit 1", "none on file")]

    tenant = join_as_tenant(other_client, conn, app_conn, pmc)
    ok(post(other_client, f"/api/tenant/leases/{lease}/renters-insurance", **policy()), 201)

    shown = insurance(client, lease)
    assert (shown["carrier"], shown["standing"], shown["below_minimum"]) == (
        "Carrier A",
        "current",
        False,
    )
    assert ok(client.get("/api/today"))["renters_insurance"] == []
    (mine,) = ok(other_client.get("/api/tenant/leases"))
    assert mine["renters_insurance"]["standing"] == "current"
    # Recorded as the tenant's own.
    assert conn.execute(
        "SELECT recorded_by, submitted_by FROM trust_renters_insurance WHERE lease_id = %s",
        (lease,),
    ).fetchone() == (None, tenant.person_id)


def test_staff_record_a_renewal_and_today_flags_a_policy_below_the_minimum(
    client, staff, pmc, lease
):
    ok(terms(client, lease, minimum_liability="500000.00"), 201)
    ok(post(client, f"/api/rent/leases/{lease}/renters-insurance", **policy()), 201)
    (flagged,) = ok(client.get("/api/today"))["renters_insurance"]
    assert flagged["standing"] == "below the minimum"

    ok(
        post(
            client,
            f"/api/rent/leases/{lease}/renters-insurance",
            **policy(
                policy_number="RI-2",
                starts_on=TODAY,
                expires_on=TODAY + 20 * DAY,
                liability_limit="500000.00",
            ),
        ),
        201,
    )
    # The first runs out last, so it still counts.
    assert insurance(client, lease)["policy_number"] == "RI-1"


def test_a_tenant_records_a_policy_only_for_their_own_lease(
    client, other_client, conn, app_conn, staff, pmc, lease
):
    other = make_pmc(conn)
    join_as_tenant(other_client, conn, app_conn, other)
    refused(
        post(other_client, f"/api/tenant/leases/{lease}/renters-insurance", **policy()),
        404,
        "no such lease of yours",
    )


def test_only_the_pmcs_staff_set_terms_or_record_for_a_lease(
    client, other_client, conn, app_conn, pmc, lease
):
    refused(terms(client, lease), 401)
    refused(post(client, f"/api/rent/leases/{lease}/renters-insurance", **policy()), 401)
    join_as_owner(client, app_conn, pmc)
    refused(terms(client, lease), 403)
    other = make_pmc(conn)
    join_as_staff(other_client, conn, app_conn, other, name="Staff 2")
    refused(terms(other_client, lease), 404)
    refused(post(other_client, f"/api/rent/leases/{lease}/renters-insurance", **policy()), 404)


@pytest.mark.parametrize(
    "change",
    [
        {"carrier": " "},
        {"policy_number": "x" * 101},
        {"expires_on": str(TODAY - 31 * DAY)},
        {"liability_limit": "0"},
        {"liability_limit": "1.001"},
        {"starts_on": "soon"},
    ],
)
def test_a_bad_policy_is_refused(client, conn, staff, lease, change):
    refused(post(client, f"/api/rent/leases/{lease}/renters-insurance", **policy(**change)), 422)
    assert conn.execute(
        "SELECT count(*) FROM trust_renters_insurance WHERE lease_id = %s", (lease,)
    ).fetchone() == (0,)


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ({"required": False}, "a minimum is asked only of insurance the lease requires"),
        ({"minimum_liability": "0"}, None),
        ({"starts_on": "soon"}, None),
    ],
)
def test_bad_terms_are_refused(client, staff, lease, body, why):
    refused(terms(client, lease, **body), 422, why)
