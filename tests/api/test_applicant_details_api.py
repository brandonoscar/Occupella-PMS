"""What an application collects and where it goes (ADR 0010 phase 2, part G; migration 0155;
src/pms/api/applicant_details.py): each applicant's phone, current address, previous landlord,
employer, emergency contact and proof of income, and the household's other occupants, pets and
vehicles; shown to staff on the application's page, and carried to the tenant and the lease once
the application's offer becomes its lease. Synthetic people and phones (555-0100 to 555-0199)."""

import base64
import secrets
import uuid
from datetime import date, timedelta

import psycopg
import pytest
from helpers import (
    add_staff,
    add_tenant,
    add_unit,
    open_lease,
    record_decision,
    submit_application,
)
from people import join_as_owner, join_as_staff
from test_offers import sign, terms

from pms import accounts

PROOF = b"%PDF-1.4 synthetic pay stub"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def details(n: int, **changes):
    return {
        "phone": f"(555) 555-010{n}",
        "address": {
            "line_1": f"{n} Sample Street",
            "city": "Sampleton",
            "state": "ST",
            "postal_code": "00001",
        },
        "previous_landlord": {
            "name": f"Landlord {n}",
            "phone": "555-555-0150",
            "from_on": "2023-01-01",
            "to_on": "2025-12-31",
        },
        "employer": f"Employer {n}",
        "emergency_contact": {"name": f"Contact {n}", "phone": "555-555-0160"},
        "income_proof": {"content_type": "application/pdf", "content_base64": b64(PROOF)},
        **changes,
    }


HOUSEHOLD = {
    "occupants": ["Occupant 1"],
    "pets": [
        {"name": "Pet 1", "kind": "Dog", "breed": "Mixed", "weight_lbs": "40.5"},
        {"name": "Pet 2", "kind": "Dog", "assistance_animal": True},
    ],
    "vehicles": [{"make": "Make 1", "model": "Model 1", "plate": "SAMPLE1", "plate_state": "ST"}],
}


class Link:
    def __init__(self, conn, pmc):
        self.pmc = pmc
        self.unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
        self.staff = add_staff(conn, pmc.pmc_id, "Leasing staff")
        self.token = secrets.token_urlsafe(32)
        self.link = conn.execute(
            "INSERT INTO trust_application_links"
            " (pmc_id, unit_id, token_sha256, monthly_rent, opened_by)"
            " VALUES (%s, %s, %s, 1500.00, %s) RETURNING id",
            (pmc.pmc_id, self.unit, accounts.digest(self.token), self.staff),
        ).fetchone()[0]


@pytest.fixture
def link(conn, pmc):
    return Link(conn, pmc)


def apply(client, link, applicants=None, household=HOUSEHOLD):
    body = {
        "token": link.token,
        "move_in_on": (date.today() + timedelta(days=20)).isoformat(),
        "monthly_income": "4500.00",
        "applicants": applicants
        or [
            {"name": "Applicant A", "email": "a@example.com", "details": details(1)},
            {"name": "Applicant B", "email": "b@example.com", "details": details(2)},
        ],
    }
    if household is not None:
        body["household"] = household
    return client.post("/api/apply", json=body)


def application_id(conn, pmc):
    return conn.execute(
        "SELECT id FROM trust_applications WHERE pmc_id = %s ORDER BY submitted_at DESC LIMIT 1",
        (pmc.pmc_id,),
    ).fetchone()[0]


def test_staff_see_what_each_applicant_and_the_household_gave(
    client, other_client, conn, app_conn, pmc, link
):
    assert apply(other_client, link).status_code == 201
    join_as_staff(client, conn, app_conn, pmc)
    seen = client.get(f"/api/leasing/applications/{application_id(conn, pmc)}").json()

    first, second = seen["applicants"]
    assert (first["name"], first["email"], first["tenant_id"]) == (
        "Applicant A",
        "a@example.com",
        None,
    )
    given = first["details"]
    assert given["phone"] == "(555) 555-0101"
    assert given["address"] == {
        "line_1": "1 Sample Street",
        "line_2": None,
        "city": "Sampleton",
        "state": "ST",
        "postal_code": "00001",
    }
    assert given["previous_landlord"] == {
        "name": "Landlord 1",
        "phone": "555-555-0150",
        "from_on": "2023-01-01",
        "to_on": "2025-12-31",
    }
    assert given["employer"] == "Employer 1"
    assert given["emergency_contact"]["name"] == "Contact 1"
    [proof] = given["income_proofs"]
    downloaded = client.get(f"/api/documents/{proof}/file")
    assert downloaded.status_code == 200 and downloaded.content == PROOF
    assert second["details"]["employer"] == "Employer 2"
    assert seen["household"] == {
        "occupants": ["Occupant 1"],
        "pets": [
            {
                "name": "Pet 1",
                "kind": "Dog",
                "breed": "Mixed",
                "weight_lbs": "40.5",
                "assistance_animal": False,
            },
            {
                "name": "Pet 2",
                "kind": "Dog",
                "breed": None,
                "weight_lbs": None,
                "assistance_animal": True,
            },
        ],
        "vehicles": [
            {
                "make": "Make 1",
                "model": "Model 1",
                "color": None,
                "plate": "SAMPLE1",
                "plate_state": "ST",
            }
        ],
    }
    # A proof of income is on the applicant's page only: no list of files or search shows it.
    assert client.get("/api/documents/files").json()["total"] == 0
    assert client.get("/api/documents").json()["documents"] == []
    assert "income_proof" not in client.get("/api/documents").json()["subjects"]
    assert [
        f for f in client.get("/api/search", params={"q": "Proof"}).json() if f["kind"] == "file"
    ] == []
    # The applicant's file is kept with nobody named as its uploader, its title the PMS's own.
    assert conn.execute(
        "SELECT title, uploaded_by FROM trust_documents WHERE kind = 'income_proof'"
        " AND pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall() == [("Proof of income", None), ("Proof of income", None)]


def test_an_application_without_details_is_taken_as_before(client, conn, app_conn, pmc, link):
    answer = apply(
        client, link, applicants=[{"name": "Applicant A", "email": "a@example.com"}], household=None
    )
    assert answer.status_code == 201
    join_as_staff(client, conn, app_conn, pmc)
    seen = client.get(f"/api/leasing/applications/{application_id(conn, pmc)}").json()
    assert seen["applicants"][0]["details"] == {
        "phone": None,
        "address": None,
        "previous_landlord": None,
        "employer": None,
        "emergency_contact": None,
        "income_proofs": [],
    }
    assert seen["household"] == {"occupants": [], "pets": [], "vehicles": []}


def test_an_applicant_with_only_some_details_gives_what_they_have(
    client, conn, app_conn, pmc, link
):
    only = details(1, previous_landlord=None, employer=None, emergency_contact=None)
    del only["income_proof"]
    assert (
        apply(client, link, [{"name": "A", "email": "a@example.com", "details": only}]).status_code
        == 201
    )
    join_as_staff(client, conn, app_conn, pmc)
    given = client.get(f"/api/leasing/applications/{application_id(conn, pmc)}").json()
    assert given["applicants"][0]["details"]["phone"] == "(555) 555-0101"
    assert given["applicants"][0]["details"]["previous_landlord"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"phone": None},  # an address without a phone
        {"address": None},
        {"phone": "call me"},
        {
            "previous_landlord": {
                "name": "Landlord 1",
                "from_on": "2025-01-01",
                "to_on": "2024-01-01",
            }
        },
        {"previous_landlord": {"name": " "}},
        {"emergency_contact": {"name": "Contact 1"}},
        {"income_proof": {"content_type": "application/pdf", "content_base64": b64(b"not a pdf")}},
        {"income_proof": {"content_type": "text/plain", "content_base64": b64(PROOF)}},
        {"income_proof": {"content_type": "application/pdf", "content_base64": "%%%%"}},
    ],
)
def test_bad_details_refuse_the_whole_application(client, conn, pmc, link, changes):
    answer = apply(
        client, link, [{"name": "A", "email": "a@example.com", "details": details(1, **changes)}]
    )
    assert answer.status_code == 422, answer.text
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_applications WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_documents WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone()[0]
        == 0
    )


def test_a_landlord_or_employer_comes_with_a_phone_and_address(client, link):
    for given in ({"employer": "Employer 1"}, {"previous_landlord": {"name": "Landlord 1"}}):
        answer = apply(client, link, [{"name": "A", "email": "a@example.com", "details": given}])
        assert answer.status_code == 422


@pytest.mark.parametrize(
    "household",
    [
        {"occupants": [f"Occupant {n}" for n in range(11)]},
        {"pets": [{"name": "Pet 1"}]},
        {"pets": [{"name": "Pet 1", "kind": "Dog", "weight_lbs": "0"}]},
        {"vehicles": [{"make": "Make 1"}]},
        {"occupants": [""]},
    ],
)
def test_a_bad_household_refuses_the_application(client, conn, pmc, link, household):
    assert apply(client, link, household=household).status_code == 422
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_applications WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone()[0]
        == 0
    )


def lease_made(client, other_client, conn, pmc, link, signers=None):
    """The application approved, offered to its applicants (or the signers named), signed by
    each, and made the lease."""
    application = application_id(conn, pmc)
    record_decision(conn, pmc.pmc_id, application, link.staff, "approved")
    asked = terms()
    if signers is not None:
        asked["signers"] = signers
    made = client.post(f"/api/leasing/applications/{application}/offer", json=asked)
    assert made.status_code == 201, made.text
    for signer in made.json()["links"]:
        assert sign(other_client, signer, signer["name"]).status_code == 200
    done = client.post(f"/api/leasing/offers/{made.json()['offer']['id']}/complete", json={})
    assert done.status_code == 200, done.text
    return made.json()["offer"]["id"], done.json()["lease_id"]


def test_an_applicant_who_becomes_a_tenant_takes_what_they_gave_with_them(
    client, other_client, conn, app_conn, pmc, link
):
    assert apply(other_client, link).status_code == 201
    join_as_staff(client, conn, app_conn, pmc)
    offer, lease = lease_made(client, other_client, conn, pmc, link)
    tenants = dict(
        conn.execute(
            "SELECT t.display_name, t.id FROM trust_lease_tenants AS lt"
            " JOIN trust_tenants AS t ON t.id = lt.tenant_id WHERE lt.lease_id = %s",
            (lease,),
        ).fetchall()
    )
    a = tenants["Applicant A"]

    # The tenant's contact details: the email and phone they applied with.
    contact = client.get(f"/api/tenants/{a}").json()["contact"]
    assert (contact["email"], contact["phone"]) == ("a@example.com", "(555) 555-0101")
    # Their emergency contact, and their application on their page.
    page = client.get(f"/api/tenants/{a}/application").json()
    assert (page["emergency_contact"]["name"], page["emergency_contact"]["phone"]) == (
        "Contact 1",
        "555-555-0160",
    )
    applied = page["application"]
    assert applied["details"]["employer"] == "Employer 1"
    assert applied["details"]["previous_landlord"]["name"] == "Landlord 1"
    assert applied["details"]["address"]["line_1"] == "1 Sample Street"
    assert len(applied["details"]["income_proofs"]) == 1
    # The household: the lease's occupants, pets (the assistance animal marked) and vehicles.
    household = client.get(f"/api/leases/{lease}/household").json()
    assert [o["name"] for o in household["occupants"]] == ["Occupant 1"]
    assert [(p["name"], p["assistance_animal"]) for p in household["pets"]] == [
        ("Pet 1", False),
        ("Pet 2", True),
    ]
    assert [v["plate"] for v in household["vehicles"]] == ["SAMPLE1"]
    # The application's page names the tenant each applicant became.
    seen = client.get(f"/api/leasing/applications/{application_id(conn, pmc)}").json()
    assert [x["tenant_id"] for x in seen["applicants"]] == [
        str(tenants["Applicant A"]),
        str(tenants["Applicant B"]),
    ]

    # Completing again carries nothing twice.
    again = client.post(f"/api/leasing/offers/{offer}/complete", json={})
    assert again.status_code == 200
    household = client.get(f"/api/leases/{lease}/household").json()
    assert (len(household["occupants"]), len(household["pets"]), len(household["vehicles"])) == (
        1,
        2,
        1,
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_emergency_contacts WHERE tenant_id = ANY(%s)",
            (list(tenants.values()),),
        ).fetchone()[0]
        == 2
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_tenant_applicants WHERE lease_id = %s", (lease,)
        ).fetchone()[0]
        == 2
    )


def test_a_signer_who_was_not_an_applicant_takes_nothing(
    client, other_client, conn, app_conn, pmc, link
):
    assert apply(other_client, link).status_code == 201
    join_as_staff(client, conn, app_conn, pmc)
    _, lease = lease_made(
        client,
        other_client,
        conn,
        pmc,
        link,
        signers=[
            {"name": "Applicant A", "email": "a@example.com"},
            {"name": "Someone Else", "email": "else@example.com"},
        ],
    )
    linked = conn.execute(
        "SELECT t.display_name FROM trust_tenant_applicants AS x"
        " JOIN trust_tenants AS t ON t.id = x.tenant_id WHERE x.lease_id = %s",
        (lease,),
    ).fetchall()
    assert linked == [("Applicant A",)]
    other = conn.execute(
        "SELECT t.id FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id"
        " WHERE lt.lease_id = %s AND t.display_name = 'Someone Else'",
        (lease,),
    ).fetchone()[0]
    assert client.get(f"/api/tenants/{other}/application").json() == {
        "emergency_contact": None,
        "application": None,
    }


def test_the_database_links_a_tenant_only_to_the_applicant_they_were(conn, pmc, link):
    tenant = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 9")
    application = submit_application(conn, pmc.pmc_id, link.link)
    applicant = conn.execute(
        "SELECT id FROM trust_applicants WHERE application_id = %s", (application,)
    ).fetchone()[0]
    lease = open_lease(conn, pmc.pmc_id, link.unit, date.today(), None, "1000", [tenant])
    with pytest.raises(psycopg.errors.CheckViolation, match="is not on the lease"):
        conn.execute(
            "INSERT INTO trust_tenant_applicants (tenant_id, pmc_id, applicant_id, lease_id)"
            " VALUES (%s, %s, %s, %s)",
            (tenant, pmc.pmc_id, applicant, lease),
        )


def test_staff_change_a_tenants_emergency_contact(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc)
    tenant = conn.execute(
        "SELECT id FROM trust_tenants WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]
    path = f"/api/tenants/{tenant}/emergency-contact"
    assert client.get(f"/api/tenants/{tenant}/application").json()["emergency_contact"] is None
    first = client.post(path, json={"name": "Contact 1", "phone": "555-555-0170"})
    assert first.status_code == 200, first.text
    assert first.json()["emergency_contact"]["name"] == "Contact 1"
    # The same again writes nothing; another holds from now on.
    client.post(path, json={"name": "Contact 1", "phone": "555-555-0170"})
    now = client.post(path, json={"name": "Contact 2", "phone": "555-555-0171"}).json()
    assert now["emergency_contact"]["name"] == "Contact 2"
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_emergency_contacts WHERE tenant_id = %s", (tenant,)
        ).fetchone()[0]
        == 2
    )
    assert conn.execute(
        "SELECT recorded_by IS NOT NULL FROM trust_emergency_contacts WHERE tenant_id = %s",
        (tenant,),
    ).fetchall() == [(True,), (True,)]


def test_bad_emergency_contacts_and_strangers_are_refused(client, conn, app_conn, pmc):
    tenant = conn.execute(
        "SELECT id FROM trust_tenants WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]
    path = f"/api/tenants/{tenant}/emergency-contact"
    assert client.get(f"/api/tenants/{tenant}/application").status_code == 401
    assert client.post(path, json={"name": "Contact 1", "phone": "555-555-0170"}).status_code == 401
    join_as_owner(client, app_conn, pmc)
    assert client.get(f"/api/tenants/{tenant}/application").status_code == 403
    join_as_staff(client, conn, app_conn, pmc)
    for body in (
        {"name": "Contact 1"},
        {"name": " ", "phone": "555-555-0170"},
        {"name": "C", "phone": "x"},
    ):
        assert client.post(path, json=body).status_code == 422
    stranger = uuid.uuid4()
    assert client.get(f"/api/tenants/{stranger}/application").status_code == 404
    assert (
        client.post(
            f"/api/tenants/{stranger}/emergency-contact",
            json={"name": "Contact 1", "phone": "555-555-0170"},
        ).status_code
        == 404
    )


def test_staff_never_upload_an_applicants_proof_of_income_as_a_file(
    client, conn, app_conn, pmc, link
):
    assert apply(client, link).status_code == 201
    join_as_staff(client, conn, app_conn, pmc)
    applicant = conn.execute(
        "SELECT id FROM trust_applicants WHERE pmc_id = %s LIMIT 1", (pmc.pmc_id,)
    ).fetchone()[0]
    answer = client.post(
        "/api/documents",
        json={
            "kind": "income_proof",
            "about_id": str(applicant),
            "title": "Pay stub",
            "content_type": "application/pdf",
            "content_base64": b64(PROOF),
        },
    )
    assert answer.status_code == 422
    assert answer.json()["detail"] == "an applicant's proof of income comes with their application"
