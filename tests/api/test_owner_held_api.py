"""Deposits the owner holds through the API (src/pms/api/owner_held.py, migration 20261106000241):
staff record from the lease page a deposit the owner keeps outside trust, and at move-out what the
owner returned and kept of it. Nothing moves in trust; the lease page, Move-outs and the tenant
ledger show it."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import add_unit, bank_sums, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
START = TODAY - 60 * DAY


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def first_tenant(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]


@pytest.fixture
def lease(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, START, None, "1000.00", [first_tenant(conn, pmc)])


def ok(answer, status=201):
    assert answer.status_code == status, answer.text
    return None if status == 204 else answer.json()


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def record(client, lease, tenant, **more):
    body = {"tenant_id": str(tenant), "amount": "1500.00", "held_since": str(START)} | more
    return client.post(f"/api/rent/leases/{lease}/owner-held-deposits", json=body)


def settle(client, lease, held, **more):
    body = {"returned": "1500.00", "kept": "0.00", "on": str(TODAY)} | more
    return client.post(f"/api/move-out/{lease}/owner-held-deposits/{held}/settle", json=body)


def books(conn, pmc):
    return (
        conn.execute(
            "SELECT t.ledger_account_id, a.balance FROM trust_ledger_accounts AS t"
            " JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id WHERE t.pmc_id = %s"
            " ORDER BY 1",
            (pmc.pmc_id,),
        ).fetchall(),
        conn.execute("SELECT count(*) FROM pgledger_transfers").fetchone()[0],
    )


def test_staff_record_a_deposit_the_owner_holds_and_the_lease_shows_it_outside_trust(
    client, conn, staff, pmc, lease
):
    tenant = first_tenant(conn, pmc)
    before = books(conn, pmc)

    made = ok(record(client, lease, tenant, memo="The owner keeps it in their own account"))
    found = ok(client.get(f"/api/leases/{lease}"), 200)

    assert made == {
        "id": made["id"],
        "tenant_id": str(tenant),
        "tenant": "Tenant 1",
        "kind": "security",
        "amount": "1500.00",
        "held_since": str(START),
        "memo": "The owner keeps it in their own account",
        "settled": None,
    }
    assert found["owner_held"] == [made]
    # Never in trust: the deposits held there are as they were, and nothing moved.
    assert [t["deposit"] for t in found["tenants"]] == ["0.00"]
    assert books(conn, pmc) == before
    assert conn.execute(
        "SELECT recorded_by FROM trust_owner_held_deposits WHERE id = %s", (made["id"],)
    ).fetchone() == (staff.person_id,)


def test_one_of_each_kind_per_tenant_and_only_a_tenant_on_the_lease(
    client, conn, staff, pmc, lease
):
    tenant = first_tenant(conn, pmc)
    ok(record(client, lease, tenant))

    again = record(client, lease, tenant, amount="10.00")
    pet = record(client, lease, tenant, kind="pet", amount="250.00")
    stranger = record(client, lease, uuid.uuid4())

    refused(again, 409, "the owner's security deposit for this tenant is recorded already")
    assert ok(pet)["kind"] == "pet"
    refused(stranger, 404, "no such tenant on this lease")


@pytest.mark.parametrize(
    "change",
    [
        {"amount": "0.00"},
        {"amount": "-1.00"},
        {"amount": "1.001"},
        {"kind": "cleaning"},
        {"held_since": str(TODAY + DAY)},
        {"memo": ""},
    ],
)
def test_a_bad_deposit_the_owner_holds_is_refused_and_records_nothing(
    client, conn, staff, pmc, lease, change
):
    answer = record(client, lease, first_tenant(conn, pmc), **change)

    assert answer.status_code == 422, answer.text
    assert conn.execute(
        "SELECT count(*) FROM trust_owner_held_deposits WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_at_move_out_staff_record_what_the_owner_returned_and_kept_and_nothing_moves(
    client, conn, staff, pmc, lease
):
    held = ok(record(client, lease, first_tenant(conn, pmc)))["id"]
    early = settle(client, lease, held)
    ok(client.post(f"/api/move-out/{lease}/end", json={"ends_on": str(TODAY - DAY)}), 204)
    before = books(conn, pmc)

    listed = [m for m in ok(client.get("/api/move-out"), 200) if m["lease_id"] == str(lease)]
    short = settle(client, lease, held, returned="1000.00")
    done = ok(settle(client, lease, held, returned="1200.00", kept="300.00", memo="Carpet"))
    twice = settle(client, lease, held)
    after = [m for m in ok(client.get("/api/move-out"), 200) if m["lease_id"] == str(lease)]

    refused(
        early,
        409,
        "what the owner did with a deposit they hold is recorded at move-out, once its last day"
        " is set",
    )
    # Listed at move-out beside what trust holds (nothing here).
    assert listed[0]["held"] == "0.00"
    assert listed[0]["owner_held"][0]["amount"] == "1500.00"
    assert listed[0]["owner_held"][0]["settled"] is None
    refused(short, 422, "what the owner returned and kept come to the $1,500.00 they held")
    assert done["settled"] == {
        "returned": "1200.00",
        "kept": "300.00",
        "on": str(TODAY),
        "memo": "Carpet",
    }
    refused(twice, 409, "what the owner did with this deposit is recorded already")
    assert after[0]["owner_held"] == [done]
    assert books(conn, pmc) == before
    assert set(bank_sums(conn, pmc.pmc_id).values()) == {Decimal(0)}


def test_the_lease_ledger_shows_it_as_memo_lines(client, conn, staff, pmc, lease):
    held = ok(record(client, lease, first_tenant(conn, pmc)))["id"]
    ok(client.post(f"/api/move-out/{lease}/end", json={"ends_on": str(TODAY - DAY)}), 204)
    ok(settle(client, lease, held, returned="1500.00"))

    lines = ok(client.get(f"/api/leases/{lease}/ledger"), 200)["lines"]

    assert [(line["kind"], line["memo"], line["charged"], line["paid"]) for line in lines] == [
        (
            "memo",
            "Tenant 1's security deposit, held by the owner (not in trust): 1,500.00",
            None,
            None,
        ),
        (
            "memo",
            "Tenant 1's security deposit the owner held: 1,500.00 returned, 0.00 kept",
            None,
            None,
        ),
    ]


def test_another_companys_lease_and_deposit_are_not_found(client, conn, staff, pmc, lease):
    theirs = make_pmc(conn)
    unit = add_unit(conn, theirs.pmc_id, theirs.owners[0].property_id)
    other = open_lease(
        conn, theirs.pmc_id, unit, START, None, "900.00", [first_tenant(conn, theirs)]
    )
    held = ok(record(client, lease, first_tenant(conn, pmc)))["id"]

    refused(record(client, other, first_tenant(conn, theirs)), 404)
    refused(settle(client, other, held), 404)
    refused(settle(client, lease, uuid.uuid4()), 404)


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_record_deposits_the_owner_holds(client, conn, app_conn, pmc, lease, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    some = uuid.uuid4()

    answers = [
        record(client, lease, first_tenant(conn, pmc)),
        settle(client, lease, some),
    ]

    assert [a.status_code for a in answers] == [401 if caller == "nobody" else 403] * 2


def test_a_custom_role_records_them_by_its_areas(
    client, other_client, conn, app_conn, staff, pmc, lease
):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 2", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Move-outs", "areas": ["move-out"]}))
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )

    # Recording one is the rent's area; what the owner did at move-out, move-out's.
    refused(record(other_client, lease, first_tenant(conn, pmc)), 403)
    held = ok(record(client, lease, first_tenant(conn, pmc)))["id"]
    ok(client.post(f"/api/move-out/{lease}/end", json={"ends_on": str(TODAY - DAY)}), 204)
    assert ok(settle(other_client, lease, held))["settled"]["returned"] == "1500.00"
