"""A lease's people through the API (migration 0080): staff take one tenant off a lease others
stay on (src/pms/api/moveout.py), end any lease with or without its lease-break fee, keep who else
lives there and who guarantees it (src/pms/api/household.py), set a property's lease-break fee
terms (src/pms/api/setup.py), and name an offer's guarantor signers (src/pms/api/offers.py). A
tenant who left reads the lease but files nothing more under it. Every name here is synthetic."""

import base64
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from helpers import (
    LEASE_PDF,
    add_tenant,
    add_unit,
    end_lease,
    make_pmc,
    open_lease,
    return_deposit,
    transfer_batch,
)
from people import join_as_staff, join_as_tenant, tenant_of

TODAY = datetime.now(UTC).date()
START = TODAY - timedelta(days=60)
END = TODAY + timedelta(days=300)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Clerk 1")


@pytest.fixture
def tenants(conn, pmc):
    """Tenant 1, holding a 1500.00 deposit, and Tenant 2."""
    first = tenant_of(conn, pmc)
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "1500.00")])
    return first, add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 2")


@pytest.fixture
def lease(conn, pmc, tenants):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, START, END, "1200.00", list(tenants))


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def depart(client, lease, tenant, left_on=TODAY, deposit_to=None):
    body = {"tenant_id": str(tenant), "left_on": left_on.isoformat()}
    if deposit_to is not None:
        body["deposit_to"] = str(deposit_to)
    return client.post(f"/api/move-out/{lease}/departures", json=body)


def limited_clerk(client, other_client, conn, app_conn, pmc, areas):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 2", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Narrow", "areas": areas}), 201)
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )
    return clerk


# --- a tenant leaving ---------------------------------------------------------------------------


def test_staff_take_one_tenant_off_and_their_deposit_moves_to_who_stays(
    client, conn, staff, pmc, tenants, lease
):
    first, second = tenants
    assert ok(depart(client, lease, first, deposit_to=second), 201) == {"moved": "1500.00"}
    # The same again moves nothing more.
    assert ok(depart(client, lease, first, deposit_to=second), 201) == {"moved": "1500.00"}
    shown = ok(client.get(f"/api/leases/{lease}"))
    assert {p["name"]: (p["left_on"], p["deposit_to"]) for p in shown["people"]} == {
        "Tenant 1": (TODAY.isoformat(), str(second)),
        "Tenant 2": (None, None),
    }
    assert {t["name"]: t["deposit"] for t in shown["tenants"]} == {
        "Tenant 1": "0.00",
        "Tenant 2": "1500.00",
    }
    assert conn.execute(
        "SELECT recorded_by FROM trust_lease_departures WHERE lease_id = %s", (lease,)
    ).fetchall() == [(staff.person_id,)]
    # The last tenant can't leave: the lease ends instead.
    refused = depart(client, lease, second)
    assert refused.status_code == 409
    assert (
        refused.json()["detail"]
        == "Tenant 2 is the last tenant on this lease: end the lease instead"
    )


def test_a_departure_names_a_tenant_on_the_lease_within_its_days(
    client, conn, staff, pmc, tenants, lease
):
    first, _ = tenants
    stranger = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 3")
    for answer in (
        depart(client, lease, stranger),
        depart(client, lease, first, deposit_to=stranger),
    ):
        assert answer.status_code == 422
        assert "one who is staying" in answer.json()["detail"]
    early = depart(client, lease, first, left_on=START - timedelta(days=1))
    assert early.status_code == 409 and "within its days" in early.json()["detail"]
    ok(depart(client, lease, first), 201)
    again = depart(client, lease, first, left_on=TODAY - timedelta(days=1))
    assert again.status_code == 409 and "Tenant 1 left this lease on" in again.json()["detail"]
    other = make_pmc(conn)
    unit = add_unit(conn, other.pmc_id, other.owners[0].property_id)
    theirs = open_lease(conn, other.pmc_id, unit, START, None, "900.00", [tenant_of(conn, other)])
    assert depart(client, theirs, tenant_of(conn, other)).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"left_on": TODAY.isoformat()},
        {"tenant_id": "not-a-uuid", "left_on": TODAY.isoformat()},
        {"tenant_id": "00000000-0000-0000-0000-000000000000", "left_on": "someday"},
    ],
)
def test_a_departure_refuses_bad_input(client, staff, lease, body):
    assert client.post(f"/api/move-out/{lease}/departures", json=body).status_code == 422


def test_a_returned_deposit_keeps_the_leases_tenants(client, conn, staff, pmc, tenants, lease):
    end_lease(conn, pmc.pmc_id, lease, TODAY - timedelta(days=1))
    return_deposit(conn, pmc.pmc_id, lease, "move-out-1", datetime.now(UTC))
    refused = depart(client, lease, tenants[0], left_on=TODAY - timedelta(days=2))
    assert refused.status_code == 409 and "deposit was returned" in refused.json()["detail"]


def test_only_staff_who_may_do_move_outs_take_a_tenant_off(
    client, other_client, conn, app_conn, staff, pmc, tenants, lease
):
    assert depart(other_client, lease, tenants[0]).status_code == 401
    join_as_tenant(other_client, conn, app_conn, pmc)
    assert depart(other_client, lease, tenants[0]).status_code == 403
    other_client.cookies.clear()
    limited_clerk(client, other_client, conn, app_conn, pmc, ["rent"])
    assert depart(other_client, lease, tenants[0]).status_code == 403


def test_a_tenant_who_left_reads_the_lease_and_files_nothing_more(
    client, other_client, conn, app_conn, staff, pmc, tenants, lease
):
    join_as_tenant(other_client, conn, app_conn, pmc)  # Tenant 1
    ok(depart(client, lease, tenants[0]), 201)
    request = other_client.post(
        "/api/tenant/requests",
        json={"lease_id": str(lease), "summary": "Leak", "entry_allowed": False},
    )
    policy = other_client.post(
        f"/api/tenant/leases/{lease}/renters-insurance",
        json={
            "carrier": "Insurer 1",
            "policy_number": "P-1",
            "starts_on": TODAY.isoformat(),
            "expires_on": END.isoformat(),
        },
    )
    for answer in (request, policy):
        assert answer.status_code == 409
        assert answer.json()["detail"] == f"you left this lease on {TODAY:%m/%d/%Y}"
    assert [str(lease)] == [
        found["lease_id"] for found in ok(other_client.get("/api/tenant/leases"))
    ]


def test_a_payment_beyond_what_is_owed_is_held_for_a_tenant_who_stays(
    client, conn, staff, pmc, tenants, lease
):
    first, second = tenants  # Tenant 1 would hold it, being first by name; they leave.
    ok(depart(client, lease, first), 201)
    paid = client.post(
        f"/api/rent/leases/{lease}/payments",
        json={"request_id": str(uuid.uuid4()), "amount": "300.00", "received_on": str(TODAY)},
    )
    ok(paid, 201)
    shown = {t["name"]: t["prepaid"] for t in ok(client.get(f"/api/leases/{lease}"))["tenants"]}
    assert shown == {"Tenant 1": "0.00", "Tenant 2": "300.00"}


# --- ending a lease and its lease-break fee ----------------------------------------------------


def terms(client, pmc, starts_on, flat_fee="250.00", rent_months="1.5"):
    return client.post(
        "/api/setup/lease-break-fees",
        json={
            "property_id": str(pmc.owners[0].property_id),
            "starts_on": starts_on.isoformat(),
            "flat_fee": flat_fee,
            "rent_months": rent_months,
        },
    )


def charges(conn, lease):
    return conn.execute(
        "SELECT due_on, amount, memo FROM trust_charges WHERE lease_id = %s AND kind = 'fee'",
        (lease,),
    ).fetchall()


def test_an_early_end_charges_the_lease_break_fee_in_force_on_its_first_day(
    client, conn, staff, pmc, lease
):
    ok(terms(client, pmc, START - timedelta(days=30)), 201)
    # Terms from after the lease began are not its terms.
    ok(terms(client, pmc, START + timedelta(days=1), flat_fee="999.00", rent_months="0"), 201)
    prop = ok(client.get(f"/api/properties/{pmc.owners[0].property_id}"))
    assert prop["lease_break_fee"] == {
        "starts_on": (START + timedelta(days=1)).isoformat(),
        "flat_fee": "999.00",
        "rent_months": "0",
    }
    last = TODAY + timedelta(days=30)
    assert ok(client.get(f"/api/move-out/{lease}/break-fee", params={"ends_on": str(last)})) == {
        "early": True,
        "fee": "2050.00",  # 250.00 and 1.5 months of 1200.00
        "flat_fee": "250.00",
        "rent_months": "1.5",
    }
    ok(
        client.post(f"/api/move-out/{lease}/end", json={"ends_on": str(last), "break_fee": True}),
        204,
    )
    assert charges(conn, lease) == [(last, 2050, "Lease-break fee")]
    assert conn.execute("SELECT ends_on FROM trust_leases WHERE id = %s", (lease,)).fetchone() == (
        last,
    )


def test_a_lease_breaks_only_when_it_ends_before_its_end_date_under_terms(
    client, conn, staff, pmc, tenants, lease
):
    last = TODAY + timedelta(days=30)
    none = ok(client.get(f"/api/move-out/{lease}/break-fee", params={"ends_on": str(last)}))
    assert none == {"early": True, "fee": None, "flat_fee": None, "rent_months": None}
    refused = client.post(
        f"/api/move-out/{lease}/end", json={"ends_on": str(last), "break_fee": True}
    )
    assert refused.status_code == 422 and "charges no lease-break fee" in refused.json()["detail"]
    # Nothing ended.
    assert conn.execute("SELECT ends_on FROM trust_leases WHERE id = %s", (lease,)).fetchone() == (
        END,
    )
    ok(terms(client, pmc, START - timedelta(days=30)), 201)
    on_time = client.post(
        f"/api/move-out/{lease}/end", json={"ends_on": str(END), "break_fee": True}
    )
    assert on_time.status_code == 422 and "before its end date" in on_time.json()["detail"]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 2")
    monthly = open_lease(conn, pmc.pmc_id, unit, START, None, "900.00", [tenants[1]])
    assert not ok(client.get(f"/api/move-out/{monthly}/break-fee", params={"ends_on": str(last)}))[
        "early"
    ]
    # Ending without the fee charges nothing.
    ok(client.post(f"/api/move-out/{monthly}/end", json={"ends_on": str(last)}), 204)
    assert charges(conn, monthly) == []


def test_lease_break_fee_terms_refuse_bad_input_and_callers(
    client, other_client, conn, app_conn, staff, pmc, lease
):
    for flat, months in (("-1.00", "1"), ("10.00", "13"), ("10.001", "1"), ("10.00", "1.555")):
        assert terms(client, pmc, START, flat, months).status_code == 422
    ok(terms(client, pmc, START), 201)
    assert terms(client, pmc, START).status_code == 409
    assert (
        client.get(f"/api/move-out/{lease}/break-fee", params={"ends_on": "someday"}).status_code
        == 422
    )
    other = make_pmc(conn)
    elsewhere = client.post(
        "/api/setup/lease-break-fees",
        json={"property_id": str(other.owners[0].property_id), "starts_on": str(START)},
    )
    assert elsewhere.status_code == 404
    # Another company's lease reads as not found.
    their_tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (other.prepaid_rent,),
    ).fetchone()[0]
    theirs = open_lease(
        conn,
        other.pmc_id,
        add_unit(conn, other.pmc_id, other.owners[0].property_id),
        START,
        None,
        "900.00",
        [their_tenant],
    )
    gone = client.get(f"/api/move-out/{theirs}/break-fee", params={"ends_on": str(TODAY)})
    assert (gone.status_code, gone.json()["detail"]) == (404, "no such lease")
    join_as_tenant(other_client, conn, app_conn, pmc)
    assert terms(other_client, pmc, START).status_code == 403
    assert (
        other_client.get(
            f"/api/move-out/{lease}/break-fee", params={"ends_on": str(TODAY)}
        ).status_code
        == 403
    )


def test_move_outs_list_an_ending_lease_that_holds_no_deposit(client, conn, staff, pmc, tenants):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 2")
    monthly = open_lease(conn, pmc.pmc_id, unit, START, None, "900.00", [tenants[1]])
    listed = {row["lease_id"] for row in ok(client.get("/api/move-out"))}
    assert str(monthly) not in listed
    ok(
        client.post(
            f"/api/move-out/{monthly}/end", json={"ends_on": str(TODAY + timedelta(days=10))}
        ),
        204,
    )
    listed = {row["lease_id"]: row["held"] for row in ok(client.get("/api/move-out"))}
    assert listed[str(monthly)] == "0.00"


# --- who else lives there, and guarantors -------------------------------------------------------


def test_staff_keep_who_else_lives_there_and_take_one_off(client, conn, staff, pmc, lease):
    base = f"/api/leases/{lease}"
    occupant = ok(
        client.post(f"{base}/occupants", json={"name": "Occupant 1", "relationship": "Child"}), 201
    )
    pet = ok(
        client.post(
            f"{base}/pets",
            json={"name": "Pet 1", "kind": "Dog", "weight_lbs": "42.5", "assistance_animal": True},
        ),
        201,
    )
    vehicle = ok(client.post(f"{base}/vehicles", json={"make": "Make 1", "plate": "SYN-001"}), 201)
    guarantor = ok(
        client.post(
            f"{base}/guarantors",
            json={"name": "Guarantor 1", "email": "Guarantor1@Example.com", "phone": "555-0100"},
        ),
        201,
    )
    assert ok(client.get(f"{base}/household")) == {
        "occupants": [{"id": occupant["id"], "name": "Occupant 1", "relationship": "Child"}],
        "pets": [
            {
                "id": pet["id"],
                "name": "Pet 1",
                "kind": "Dog",
                "breed": None,
                "weight_lbs": "42.5",
                "assistance_animal": True,
            }
        ],
        "vehicles": [
            {
                "id": vehicle["id"],
                "make": "Make 1",
                "model": None,
                "color": None,
                "plate": "SYN-001",
                "plate_state": None,
            }
        ],
        "guarantors": [
            {
                "id": guarantor["id"],
                "name": "Guarantor 1",
                "email": "guarantor1@example.com",
                "phone": "555-0100",
                "signed": False,
            }
        ],
    }
    ok(client.post(f"{base}/pets/{pet['id']}/remove", json={}), 204)
    assert client.post(f"{base}/pets/{pet['id']}/remove", json={}).status_code == 409
    # An occupant is not a pet, and another lease's rows are not this one's.
    assert client.post(f"{base}/pets/{occupant['id']}/remove", json={}).status_code == 404
    shown = ok(client.get(f"{base}/household"))
    assert shown["pets"] == [] and len(shown["occupants"]) == 1
    assert conn.execute(
        "SELECT recorded_by FROM trust_lease_household_removals WHERE pet_id = %s", (pet["id"],)
    ).fetchone() == (staff.person_id,)


@pytest.mark.parametrize(
    "kind, body",
    [
        ("occupants", {"name": " "}),
        ("pets", {"name": "Pet 1", "kind": "Dog", "weight_lbs": "0"}),
        ("pets", {"name": "Pet 1", "kind": "Dog", "weight_lbs": "4.25"}),
        ("pets", {"name": "Pet 1"}),
        ("vehicles", {"make": "Make 1", "plate": "PLATE-TOO-LONG-1"}),
        ("guarantors", {"name": "Guarantor 1", "email": "not an email"}),
        ("guarantors", {"name": "Guarantor 1", "phone": "call me"}),
    ],
)
def test_household_rows_refuse_bad_input(client, staff, lease, kind, body):
    assert client.post(f"/api/leases/{lease}/{kind}", json=body).status_code == 422


def test_household_is_staffs_of_the_company_with_setup(
    client, other_client, conn, app_conn, staff, pmc, lease
):
    other = make_pmc(conn)
    unit = add_unit(conn, other.pmc_id, other.owners[0].property_id)
    theirs = open_lease(conn, other.pmc_id, unit, START, None, "900.00", [tenant_of(conn, other)])
    assert client.get(f"/api/leases/{theirs}/household").status_code == 404
    assert (
        client.post(f"/api/leases/{theirs}/occupants", json={"name": "Occupant 1"}).status_code
        == 404
    )
    assert client.post(f"/api/leases/{lease}/kids/{lease}/remove", json={}).status_code == 422
    assert other_client.get(f"/api/leases/{lease}/household").status_code == 401
    join_as_tenant(other_client, conn, app_conn, pmc)
    assert other_client.get(f"/api/leases/{lease}/household").status_code == 403
    assert (
        other_client.post(f"/api/leases/{lease}/occupants", json={"name": "O"}).status_code == 403
    )
    other_client.cookies.clear()
    limited_clerk(client, other_client, conn, app_conn, pmc, ["rent"])
    assert (
        other_client.post(f"/api/leases/{lease}/occupants", json={"name": "O"}).status_code == 403
    )
    assert other_client.get(f"/api/leases/{lease}/household").status_code == 200  # reads stay open


def renewal_terms(signers):
    return {
        "starts_on": (END + timedelta(days=1)).isoformat(),
        "ends_on": None,
        "monthly_rent": "1250.00",
        "document_title": "Residential lease",
        "document_base64": base64.b64encode(LEASE_PDF).decode(),
        "signers": signers,
        "send": False,
    }


def test_an_offer_names_guarantor_signers_beside_a_tenant(client, conn, staff, pmc, lease):
    made = ok(
        client.post(
            f"/api/leasing/leases/{lease}/renewal",
            json=renewal_terms(
                [
                    {"name": "Tenant 2", "email": "tenant2@example.com"},
                    {"name": "Guarantor 1", "email": "guarantor1@example.com", "role": "guarantor"},
                ]
            ),
        ),
        201,
    )
    assert [(s["name"], s["role"]) for s in made["offer"]["signers"]] == [
        ("Tenant 2", "tenant"),
        ("Guarantor 1", "guarantor"),
    ]
    alone = client.post(
        f"/api/leasing/leases/{lease}/renewal",
        json=renewal_terms(
            [{"name": "Guarantor 1", "email": "guarantor1@example.com", "role": "guarantor"}]
        ),
    )
    assert alone.status_code == 422 and alone.json()["detail"] == "at least one signer is a tenant"
    odd = client.post(
        f"/api/leasing/leases/{lease}/renewal",
        json=renewal_terms([{"name": "Signer 1", "email": "s1@example.com", "role": "landlord"}]),
    )
    assert odd.status_code == 422
