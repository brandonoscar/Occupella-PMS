"""Email from staff (src/pms/api/emails.py, docs/adr/0007-email.md): writing to a tenant, an
owner or a vendor, to every tenant living at a property, and each person's list of the emails
the PMS wrote them, with whether each went. Every name and address here is synthetic."""

from datetime import UTC, datetime, timedelta

import pytest
from helpers import add_agreement, add_tenant, add_unit, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of

from pms import email


class Kept:
    name = "fake"

    def __init__(self):
        self.sent: list[email.Message] = []

    def send(self, message):
        self.sent.append(message)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Writer 1")


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def contact(client, kind, person, address):
    ok(client.post(f"/api/{kind}s/{person}/contact", json={"email": address}), 204)


def write(client, about, about_id, subject="Water shutoff", body="Water is off from 9 to 11."):
    return client.post(
        "/api/emails",
        json={"about": about, "about_id": str(about_id), "subject": subject, "body": body},
    )


def listed(client, about, about_id):
    return ok(client.get("/api/emails", params={"about": about, "about_id": str(about_id)}))


def today():
    return datetime.now(UTC).date()


# --- one person -----------------------------------------------------------------------------


def test_staff_email_a_tenant_at_their_contact_email_and_see_it_listed(client, conn, staff, pmc):
    tenant = tenant_of(conn, pmc)
    contact(client, "tenant", tenant, "tenant.one@example.com")
    sent = ok(write(client, "tenant", tenant), 201)
    assert (sent["to"], sent["name"]) == ("tenant.one@example.com", sent["name"])

    (line,) = listed(client, "tenant", tenant)
    assert (line["kind"], line["to"], line["subject"], line["by"]) == (
        "message",
        "tenant.one@example.com",
        "Water shutoff",
        "Writer 1",
    )
    assert line["body"].startswith("Water is off from 9 to 11.\n\nWriter 1\n")
    assert (line["status"], line["error"]) == ("waiting", None)  # this server sends none


def test_an_email_goes_out_when_the_server_sends_email(client, conn, staff, pmc):
    kept = Kept()
    client.app.state.email = kept
    vendor = pmc.vendor_id
    contact(client, "vendor", vendor, "vendor.one@example.com")
    ok(write(client, "vendor", vendor, "Gate code", "The gate code is 0000."), 201)

    (message,) = [m for m in kept.sent if m.to == "vendor.one@example.com"]
    assert message.subject == "Gate code" and message.from_name.startswith("Synthetic PMC")
    assert listed(client, "vendor", vendor)[0]["status"] == "sent"


def test_an_owner_without_a_contact_email_is_written_at_their_login(
    client, other_client, conn, app_conn, staff, pmc
):
    owner = join_as_owner(other_client, app_conn, pmc)
    sent = ok(write(client, "owner", owner.person_id), 201)
    assert sent["to"] == owner.email


def test_someone_with_no_email_on_file_is_not_written(client, conn, staff, pmc):
    answer = write(client, "owner", pmc.owners[1].owner_id)
    assert answer.status_code == 422
    assert "no email on file" in answer.json()["detail"]
    assert listed(client, "owner", pmc.owners[1].owner_id) == []


def test_an_owners_list_has_their_bill_approval_emails_too(
    client, other_client, conn, app_conn, staff, pmc
):
    owner = pmc.owners[0].owner_id
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, today(), approval_limit="100")
    join_as_owner(other_client, app_conn, pmc)  # an owner who can sign in is emailed
    bill = ok(
        client.post(
            "/api/payables/bills",
            json={
                "vendor_id": str(pmc.vendor_id),
                "account": pmc.owners[0].account,
                "reference": "ROOF-9",
                "bill_date": str(today()),
                "due_on": str(today() + timedelta(days=10)),
                "amount": "500.00",
                "memo": "Roof patch",
            },
        ),
        201,
    )
    assert bill
    kinds = [line["kind"] for line in listed(client, "owner", owner)]
    assert kinds == ["bill_approval"]


def test_another_companys_person_is_not_found(client, conn, staff, pmc):
    other = make_pmc(conn)
    assert write(client, "tenant", tenant_of(conn, other)).status_code == 404
    assert listed(client, "tenant", tenant_of(conn, other)) == []


# --- everyone living at a property ----------------------------------------------------------


def lived_in(conn, pmc, names):
    """A unit of the PMC's first property with a lease in effect today for these tenants."""
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, f"Unit {names[0][-1]}")
    tenants = [add_tenant(conn, pmc.pmc_id, prop, name) for name in names]
    open_lease(conn, pmc.pmc_id, unit, today() - timedelta(days=30), None, "1000.00", tenants)
    return tenants


def test_staff_email_every_tenant_living_at_a_property(client, conn, staff, pmc):
    a, b = lived_in(conn, pmc, ["Tenant A", "Tenant B"])
    (c,) = lived_in(conn, pmc, ["Tenant C"])
    contact(client, "tenant", a, "a@example.com")
    contact(client, "tenant", c, "c@example.com")
    sent = ok(
        client.post(
            "/api/emails/property",
            json={
                "property_id": str(pmc.owners[0].property_id),
                "subject": "Water shutoff",
                "body": "Water is off Tuesday from 9 to 11.",
            },
        ),
        201,
    )
    assert [(s["name"], s["to"]) for s in sent["emailed"]] == [
        ("Tenant A", "a@example.com"),
        ("Tenant C", "c@example.com"),
    ]
    assert sent["without_email"] == ["Tenant B"]
    assert [line["subject"] for line in listed(client, "tenant", a)] == ["Water shutoff"]
    assert listed(client, "tenant", b) == []


def test_a_property_where_no_one_lives_or_of_another_company_is_refused(client, conn, staff, pmc):
    body = {"subject": "Hi", "body": "Hi"}
    empty = client.post(
        "/api/emails/property", json={"property_id": str(pmc.owners[1].property_id), **body}
    )
    assert empty.status_code == 422 and "no tenant lives there" in empty.json()["detail"]
    other = make_pmc(conn)
    theirs = client.post(
        "/api/emails/property", json={"property_id": str(other.owners[0].property_id), **body}
    )
    assert theirs.status_code == 404


# --- who may --------------------------------------------------------------------------------


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_read_or_write_email(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    tenant = tenant_of(conn, pmc)
    assert (
        client.get("/api/emails", params={"about": "tenant", "about_id": str(tenant)}).status_code
        == status
    )
    assert write(client, "tenant", tenant).status_code == status
    answer = client.post(
        "/api/emails/property",
        json={"property_id": str(pmc.owners[0].property_id), "subject": "s", "body": "b"},
    )
    assert answer.status_code == status


def test_a_custom_role_without_email_may_not_write(
    client, other_client, conn, app_conn, staff, pmc
):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Books", "areas": ["rent"]}), 201)
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )
    tenant = tenant_of(conn, pmc)
    contact(client, "tenant", tenant, "t@example.com")
    refused = write(other_client, "tenant", tenant)
    assert refused.status_code == 403 and "email to tenants" in refused.json()["detail"]
    assert (
        other_client.get(
            "/api/emails", params={"about": "tenant", "about_id": str(tenant)}
        ).status_code
        == 200
    )  # reads stay open


NOWHERE = "00000000-0000-0000-0000-000000000000"


@pytest.mark.parametrize(
    "body",
    [
        {"about": "tenant", "about_id": NOWHERE, "subject": " ", "body": "x"},
        {"about": "tenant", "about_id": NOWHERE, "subject": "two\nlines", "body": "x"},
        {"about": "tenant", "about_id": NOWHERE, "subject": "x" * 201, "body": "x"},
        {"about": "tenant", "about_id": NOWHERE, "subject": "x", "body": "x" * 8001},
        {"about": "tenant", "about_id": NOWHERE, "subject": "x", "body": "a\u0000b"},
        {"about": "staff", "about_id": NOWHERE, "subject": "x", "body": "x"},
        {"about": "tenant", "about_id": "not-an-id", "subject": "x", "body": "x"},
    ],
)
def test_bad_input_is_refused(client, staff, body):
    assert client.post("/api/emails", json=body).status_code == 422


def test_bad_property_email_is_refused(client, staff, pmc):
    for body in (
        {"property_id": "not-an-id", "subject": "x", "body": "x"},
        {"property_id": str(pmc.owners[0].property_id), "subject": "", "body": "x"},
    ):
        assert client.post("/api/emails/property", json=body).status_code == 422


def test_a_person_is_named_to_read_their_email(client, staff):
    assert client.get("/api/emails").status_code == 422
    assert (
        client.get("/api/emails", params={"about": "bill", "about_id": NOWHERE}).status_code == 422
    )
