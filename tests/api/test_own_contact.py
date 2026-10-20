"""A tenant's own contact details through the portal (src/pms/api/tenant.py, migration 0099;
the founder's review q121): they read and change their email, phones and mailing address, the
company's emails go to the new email, and staff see on the tenant's page that the tenant changed
them. Nobody else changes them this way."""

import psycopg
import pytest
from people import join_as_owner, join_as_staff, join_as_tenant

from pms.api import emails

CONTACT = {
    "email": "tenant.own@example.com",
    "phone": "(555) 555-0123",
    "alternate_phone": None,
    "address_line_1": "200 Example Avenue",
    "address_line_2": "Apt 2",
    "city": "Sampletown",
    "state": "ZZ",
    "postal_code": "00000",
}
EMPTY = dict.fromkeys(CONTACT)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


@pytest.fixture
def tenant(other_client, conn, app_conn, pmc):
    return join_as_tenant(other_client, conn, app_conn, pmc)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def test_a_tenant_changes_their_own_contact_details_and_staff_see_they_did(
    client, other_client, conn, pmc, tenant, staff
):
    before = ok(other_client.get("/api/tenant/contact"))
    ok(other_client.post("/api/tenant/contact", json=CONTACT), 204)
    after = ok(other_client.get("/api/tenant/contact"))
    page = ok(client.get(f"/api/tenants/{tenant.person_id}"))
    row = conn.execute(
        "SELECT by_person, recorded_by FROM trust_contacts WHERE tenant_id = %s",
        (tenant.person_id,),
    ).fetchall()

    assert before == EMPTY and after == CONTACT
    assert page["contact"] == CONTACT and page["contact_by_tenant_at"] is not None
    # Their sign-in email stays as it was; only where the company writes to them changes.
    assert page["login_email"] == tenant.email
    assert emails.address_of(conn, pmc.pmc_id, "tenant", tenant.person_id)[1] == CONTACT["email"]
    assert row == [(True, None)]


def test_a_change_by_staff_after_is_staffs_and_the_same_details_again_write_nothing(
    client, other_client, conn, tenant, staff
):
    ok(other_client.post("/api/tenant/contact", json=CONTACT), 204)
    ok(other_client.post("/api/tenant/contact", json=CONTACT), 204)
    staffs = {**CONTACT, "phone": None}
    ok(client.post(f"/api/tenants/{tenant.person_id}/contact", json=staffs), 204)
    page = ok(client.get(f"/api/tenants/{tenant.person_id}"))
    rows = conn.execute(
        "SELECT by_person, recorded_by IS NOT NULL FROM trust_contacts WHERE tenant_id = %s"
        " ORDER BY created_at, id",
        (tenant.person_id,),
    ).fetchall()
    audited = conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_contacts'"
        " AND row_data->>'tenant_id' = %s",
        (str(tenant.person_id),),
    ).fetchone()[0]

    assert rows == [(True, False), (False, True)]
    assert audited == 2
    assert page["contact"]["phone"] is None and page["contact_by_tenant_at"] is None


def test_a_tenant_clears_a_field_by_leaving_it_out(other_client, tenant):
    ok(other_client.post("/api/tenant/contact", json=CONTACT), 204)
    ok(other_client.post("/api/tenant/contact", json={"phone": "555-555-0199"}), 204)

    assert ok(other_client.get("/api/tenant/contact")) == {**EMPTY, "phone": "555-555-0199"}


@pytest.mark.parametrize(
    "body",
    [
        {"email": "not an email"},
        {"phone": "call me"},
        {"address_line_1": "200 Example Avenue"},  # a mailing address is whole or not there
        {"address_line_2": "Apt 2"},
        {"state": "Z" * 51, "address_line_1": "1 A St", "city": "B", "postal_code": "00000"},
    ],
)
def test_bad_contact_details_are_refused(other_client, conn, tenant, body):
    answer = other_client.post("/api/tenant/contact", json=body)

    assert answer.status_code == 422
    assert ok(other_client.get("/api/tenant/contact")) == EMPTY


@pytest.mark.parametrize("caller", ["nobody", "owner", "staff"])
def test_only_a_tenant_reads_or_changes_their_own_contact(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "staff":
        join_as_staff(client, conn, app_conn, pmc)

    answers = [client.get("/api/tenant/contact"), client.post("/api/tenant/contact", json=CONTACT)]

    assert [a.status_code for a in answers] == [401 if caller == "nobody" else 403] * 2


def test_the_database_keeps_a_persons_own_change_to_a_tenant_naming_no_staff(conn, pmc, staff):
    owner = pmc.owners[0].owner_id
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO trust_contacts (pmc_id, owner_id, by_person, phone)"
            " VALUES (%s, %s, true, '555-555-0100')",
            (pmc.pmc_id, owner),
        )
    tenant = conn.execute(
        "SELECT id FROM trust_tenants WHERE pmc_id = %s LIMIT 1", (pmc.pmc_id,)
    ).fetchone()[0]
    # A row naming a staff member (the request named them) is staff's, never the tenant's own.
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute("SELECT set_config('trust.staff_id', %s, true)", (str(staff.person_id),))
        conn.execute(
            "INSERT INTO trust_contacts (pmc_id, tenant_id, by_person, phone)"
            " VALUES (%s, %s, true, '555-555-0100')",
            (pmc.pmc_id, tenant),
        )
