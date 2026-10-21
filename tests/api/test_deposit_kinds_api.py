"""A tenant's pet or key deposit through the API (migration 20261018000092; the founder's review,
q043): staff record each kind apart from the security deposit, the lease shows each kind held,
and an assistance animal takes no pet deposit (Fair Housing Act; HUD FHEO-2020-01). The
database's side is in tests/test_deposit_kinds.py. Every name here is synthetic."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import add_unit, balance, open_lease
from people import join_as_staff

TODAY = datetime.now(UTC).date()
START = TODAY - timedelta(days=30)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def tenant(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]


@pytest.fixture
def lease(conn, pmc, tenant):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, START, None, "1500.00", [tenant])


def deposit(client, tenant, amount, kind=None, request_id=None):
    body = {
        "request_id": request_id or uuid.uuid4(),
        "tenant_id": tenant,
        "amount": amount,
        "received_on": TODAY,
    } | ({} if kind is None else {"kind": kind})
    return client.post("/api/rent/deposits", json=json.loads(json.dumps(body, default=str)))


def ok(answer):
    assert answer.status_code == 201, answer.text
    return answer.json()


def held(conn, tenant, kind):
    account = conn.execute(
        "SELECT ledger_account_id FROM trust_ledger_accounts WHERE tenant_id = %s"
        " AND kind = 'tenant_deposit' AND deposit_kind IS NOT DISTINCT FROM %s",
        (tenant, kind),
    ).fetchone()
    return None if account is None else balance(conn, account[0])


def add_pet(conn, pmc, lease, name, assistance):
    return conn.execute(
        "INSERT INTO trust_lease_pets (pmc_id, lease_id, display_name, kind, assistance_animal)"
        " VALUES (%s, %s, %s, 'dog', %s) RETURNING id",
        (pmc.pmc_id, lease, name, assistance),
    ).fetchone()[0]


def test_each_kind_of_deposit_is_held_apart_and_the_lease_shows_each(
    client, conn, staff, pmc, tenant, lease
):
    ok(deposit(client, tenant, "1000.00"))
    pet = uuid.uuid4()
    first = ok(deposit(client, tenant, "300.00", "pet", pet))
    ok(deposit(client, tenant, "50.00", "key"))
    again = ok(deposit(client, tenant, "300.00", "pet", pet))
    other = deposit(client, tenant, "300.00", "key", pet)

    # The same request again records nothing new; the same request as another kind is refused.
    assert again == first
    assert other.status_code == 409, other.text
    assert other.json()["detail"] == "this request id was used for a different deposit"
    assert (held(conn, tenant, None), held(conn, tenant, "pet"), held(conn, tenant, "key")) == (
        Decimal("1000.00"),
        Decimal("300.00"),
        Decimal("50.00"),
    )
    row = client.get(f"/api/leases/{lease}").json()["tenants"][0]
    assert row["deposit"] == "1350.00"
    assert row["deposits"] == [
        {"kind": "security", "amount": "1000.00"},
        {"kind": "key", "amount": "50.00"},
        {"kind": "pet", "amount": "300.00"},
    ]
    memo = conn.execute(
        "SELECT metadata->>'memo', metadata->>'kind' FROM pgledger_transfers WHERE id = %s",
        (first["transfers"][0],),
    ).fetchone()
    assert memo[0].startswith("Pet deposit – ") and memo[1] == "pet deposit"


def test_a_tenant_without_deposits_shows_none(client, staff, tenant, lease):
    row = client.get(f"/api/leases/{lease}").json()["tenants"][0]

    assert (row["deposit"], row["deposits"]) == ("0.00", [])


def test_an_assistance_animal_takes_no_pet_deposit(client, conn, staff, pmc, tenant, lease):
    refusal = (
        "the tenant's only animal is an assistance animal, which takes no pet deposit"
        " (Fair Housing Act)"
    )
    # No animal on file yet: staff may take one as the lease says.
    ok(deposit(client, tenant, "100.00", "pet"))
    add_pet(conn, pmc, lease, "Rex", True)

    only_assistance = deposit(client, tenant, "100.00", "pet")
    security = deposit(client, tenant, "100.00")
    dog = add_pet(conn, pmc, lease, "Fido", False)
    with_a_pet = deposit(client, tenant, "100.00", "pet")
    conn.execute(
        "INSERT INTO trust_lease_household_removals (pmc_id, pet_id) VALUES (%s, %s)",
        (pmc.pmc_id, dog),
    )
    pet_gone = deposit(client, tenant, "100.00", "pet")

    assert (only_assistance.status_code, only_assistance.json()["detail"]) == (409, refusal)
    ok(security)
    ok(with_a_pet)
    assert (pet_gone.status_code, pet_gone.json()["detail"]) == (409, refusal)
    assert held(conn, tenant, "pet") == Decimal("200.00")


def test_an_ended_leases_assistance_animal_does_not_count(client, conn, staff, pmc, tenant):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    ended = open_lease(
        conn,
        pmc.pmc_id,
        unit,
        START - timedelta(days=400),
        START - timedelta(days=40),
        "900.00",
        [tenant],
    )
    add_pet(conn, pmc, ended, "Rex", True)

    ok(deposit(client, tenant, "100.00", "pet"))


@pytest.mark.parametrize("kind", ["cleaning", "Pet", "", None])
def test_a_deposit_is_of_a_known_kind(client, staff, tenant, kind):
    body = {
        "request_id": str(uuid.uuid4()),
        "tenant_id": str(tenant),
        "amount": "10.00",
        "received_on": str(TODAY),
        "kind": kind,
    }

    assert client.post("/api/rent/deposits", json=body).status_code == 422


def test_the_journal_names_each_kind_of_deposit(client, conn, staff, pmc, tenant):
    pet = conn.execute(
        "SELECT trust_open_deposit_account(%s, %s, %s, 'pet')",
        (pmc.pmc_id, pmc.deposit_bank_id, tenant),
    ).fetchone()[0]

    labels = {a["id"]: a["label"] for a in client.get("/api/journal").json()["accounts"]}

    deposits = "Synthetic security_deposit trust account"
    assert labels[pet] == f"{deposits}: pet deposit: Tenant 1"
    assert labels[pmc.tenant_deposit] == f"{deposits}: deposit: Tenant 1"
