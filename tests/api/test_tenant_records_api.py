"""A tenant renting at two properties at once (migration 0105; the founder's review q009): staff
make the tenant a record at the other property from their page, or say an existing record there is
the same person, and the tenant signs in once and sees and acts on every lease of theirs."""

from datetime import date

import pytest
from helpers import add_tenant, add_unit, make_pmc, open_lease
from people import join_as_staff, join_as_tenant

from pms import notices

CONTACT = {
    "email": "tenant.home@example.com",
    "phone": "(555) 555-0142",
    "alternate_phone": None,
    "address_line_1": None,
    "address_line_2": None,
    "city": None,
    "state": None,
    "postal_code": None,
}


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def tenant(other_client, conn, app_conn, pmc):
    return join_as_tenant(other_client, conn, app_conn, pmc)


def lease_at(conn, pmc, n, tenant_id, rent="1000.00"):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[n].property_id, f"Unit {n + 1}")
    return open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, rent, [tenant_id])


def test_staff_rent_a_tenant_at_another_property_and_they_see_both_leases(
    client, other_client, conn, pmc, staff, tenant
):
    home = lease_at(conn, pmc, 0, tenant.person_id)
    ok(client.post(f"/api/tenants/{tenant.person_id}/contact", json=CONTACT), 204)

    there = ok(
        client.post(
            f"/api/tenants/{tenant.person_id}/elsewhere",
            json={"property_id": str(pmc.owners[1].property_id)},
        ),
        201,
    )["id"]
    away = lease_at(conn, pmc, 1, there, "900.00")
    first_page = ok(client.get(f"/api/tenants/{tenant.person_id}"))
    their_page = ok(client.get(f"/api/tenants/{there}"))
    leases = ok(other_client.get("/api/tenant/leases"))

    assert their_page["name"] == "Tenant 1" and their_page["property"]["name"] == "Property 2"
    assert their_page["contact"] == CONTACT
    # They sign in once, as their first record, which both pages say.
    assert (their_page["portal"], their_page["login_email"]) == ("active", tenant.email)
    assert their_page["same_person"] == [
        {
            "id": str(tenant.person_id),
            "property": {"id": str(pmc.owners[0].property_id), "name": "Property 1"},
            "first": True,
        }
    ]
    assert first_page["same_person"] == [
        {
            "id": there,
            "property": {"id": str(pmc.owners[1].property_id), "name": "Property 2"},
            "first": False,
        }
    ]
    assert {(lease["lease_id"], lease["property"]) for lease in leases} == {
        (str(home), "Property 1"),
        (str(away), "Property 2"),
    }
    # Each record keeps its own money: the new one holds nothing.
    assert (their_page["prepaid"], their_page["deposit"]) == ("0.00", "0.00")
    assert their_page["leases"][0]["id"] == str(away)


def test_the_tenant_acts_on_the_other_propertys_lease_as_their_record_there(
    client, other_client, conn, pmc, staff, tenant
):
    lease_at(conn, pmc, 0, tenant.person_id)
    there = ok(
        client.post(
            f"/api/tenants/{tenant.person_id}/elsewhere",
            json={"property_id": str(pmc.owners[1].property_id)},
        ),
        201,
    )["id"]
    away = lease_at(conn, pmc, 1, there, "900.00")

    request = ok(
        other_client.post(
            "/api/tenant/requests",
            json={"lease_id": str(away), "summary": "The sink drips", "entry_allowed": True},
        ),
        201,
    )
    policy = {
        "carrier": "Synthetic Mutual",
        "policy_number": "SYN-0001",
        "starts_on": "2026-01-01",
        "expires_on": "2027-01-01",
    }
    ok(other_client.post(f"/api/tenant/leases/{away}/renters-insurance", json=policy), 201)
    ledger = other_client.get(f"/api/tenant/leases/{away}/ledger")
    ok(other_client.post("/api/tenant/contact", json={**CONTACT, "phone": None}), 204)

    assert conn.execute(
        "SELECT tenant_id::text FROM trust_maintenance_requests WHERE id = %s", (request["id"],)
    ).fetchone() == (there,)
    assert request["id"] in {r["id"] for r in ok(other_client.get("/api/tenant/requests"))}
    assert conn.execute(
        "SELECT submitted_by::text FROM trust_renters_insurance WHERE lease_id = %s", (away,)
    ).fetchone() == (there,)
    assert ledger.status_code == 200
    # Their own contact details are theirs at both properties.
    for record in (tenant.person_id, there):
        assert ok(client.get(f"/api/tenants/{record}"))["contact"]["phone"] is None


def test_the_company_writes_to_a_record_without_an_email_at_the_persons_login(
    client, conn, pmc, staff, tenant
):
    there = ok(
        client.post(
            f"/api/tenants/{tenant.person_id}/elsewhere",
            json={"property_id": str(pmc.owners[1].property_id)},
        ),
        201,
    )["id"]

    assert notices.address(conn, pmc.pmc_id, "tenant", there) == tenant.email
    assert notices.can_sign_in(conn, pmc.pmc_id, "tenant", there)


def test_staff_say_an_applicants_record_is_a_tenant_who_rents_already(
    client, conn, app_conn, pmc, staff, tenant
):
    applicant = add_tenant(conn, pmc.pmc_id, pmc.owners[1].property_id, "Tenant 1")
    third = add_tenant(conn, pmc.pmc_id, pmc.owners[1].property_id, "Tenant 3")

    ok(
        client.post(
            f"/api/tenants/{applicant}/same-person", json={"tenant_id": str(tenant.person_id)}
        ),
        204,
    )
    page = ok(client.get(f"/api/tenants/{applicant}"))
    # The first record signs in: it never joins another.
    refused = client.post(
        f"/api/tenants/{tenant.person_id}/same-person", json={"tenant_id": str(third)}
    )
    itself = client.post(f"/api/tenants/{third}/same-person", json={"tenant_id": str(third)})
    # A second record at Property 2 for the same person is one too many.
    twice = client.post(f"/api/tenants/{third}/same-person", json={"tenant_id": str(applicant)})

    assert [r["id"] for r in page["same_person"]] == [str(tenant.person_id)]
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "Tenant 1 has records at other properties linked to them: link Tenant 3 to them instead"
    )
    assert itself.status_code == 422
    assert twice.status_code == 409
    assert twice.json()["detail"] == (
        "Tenant 1 is a tenant at Property 2 already: one record a property"
    )


def test_a_record_linked_to_another_takes_no_invite_of_its_own(
    client, conn, app_conn, pmc, staff, tenant
):
    there = ok(
        client.post(
            f"/api/tenants/{tenant.person_id}/elsewhere",
            json={"property_id": str(pmc.owners[1].property_id)},
        ),
        201,
    )["id"]

    invited = client.post(
        "/api/invites",
        json={"kind": "tenant", "person_id": there, "email": "tenant.two@example.com"},
    )

    assert invited.status_code == 409
    assert invited.json()["detail"] == (
        "this tenant signs in as Tenant 1 at their first property: invite them there"
    )


def test_only_staff_link_records(client, other_client, conn, app_conn, pmc, tenant):
    other = add_tenant(conn, pmc.pmc_id, pmc.owners[1].property_id, "Tenant 2")
    body = {"property_id": str(pmc.owners[1].property_id)}

    assert client.post(f"/api/tenants/{tenant.person_id}/elsewhere", json=body).status_code == 401
    assert (
        other_client.post(f"/api/tenants/{tenant.person_id}/elsewhere", json=body).status_code
        == 403
    )
    assert (
        other_client.post(
            f"/api/tenants/{other}/same-person", json={"tenant_id": str(tenant.person_id)}
        ).status_code
        == 403
    )


def test_bad_or_unknown_records_and_properties_are_refused(client, conn, pmc, staff, tenant):
    theirs = make_pmc(conn)
    elsewhere = f"/api/tenants/{tenant.person_id}/elsewhere"

    assert client.post(elsewhere, json={"property_id": "nope"}).status_code == 422
    assert client.post(elsewhere, json={}).status_code == 422
    assert (
        client.post(elsewhere, json={"property_id": str(theirs.owners[0].property_id)}).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/tenants/{theirs.owners[0].owner_id}/elsewhere",
            json={"property_id": str(pmc.owners[1].property_id)},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/tenants/{tenant.person_id}/same-person",
            json={"tenant_id": str(theirs.owners[0].owner_id)},
        ).status_code
        == 404
    )
    # The same property as the first record: a person has one record a property.
    same = client.post(elsewhere, json={"property_id": str(pmc.owners[0].property_id)})
    assert same.status_code == 409
    assert conn.execute(
        "SELECT count(*) FROM trust_tenants WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (1,)


def test_the_portal_answers_another_persons_lease_as_not_found(other_client, conn, pmc, tenant):
    someone = add_tenant(conn, pmc.pmc_id, pmc.owners[1].property_id, "Tenant 2")
    theirs = lease_at(conn, pmc, 1, someone)

    assert other_client.get(f"/api/tenant/leases/{theirs}/ledger").status_code == 404
    assert str(theirs) not in {
        lease["lease_id"] for lease in ok(other_client.get("/api/tenant/leases"))
    }
