"""A company's own lease, filled in (migration 0121; brought in scope by the founder on
2026-10-22): a template is for notices or for leases from when it is made, each with the blanks
the PMS fills for it; a draft lease takes the company's PDF or one of its lease templates, filled
in from its terms into the PDF it sends for signing, and keeps which version it came from. Every
name is synthetic."""

import base64
import io
import uuid
from datetime import date

import pytest
from helpers import (
    LEASE_PDF,
    add_staff,
    add_tenant,
    add_unit,
    make_pmc,
    open_application_link,
    open_lease,
    record_decision,
    submit_application,
)
from people import join_as_owner, join_as_staff, join_as_tenant
from pypdf import PdfReader

from pms.api import Settings

LEASE_TEXT = (
    "This lease is between {{company_name}} ({{company_address}}) and {{tenant_names}}, guaranteed"
    " by {{guarantor_names}}, for {{property}}, {{unit}}, from {{lease_start}} to {{lease_end}},"
    " at {{monthly_rent}} a month, with a security deposit of {{security_deposit}}. Also each"
    " month: {{monthly_charges}}. Made {{lease_date}}."
)


@pytest.fixture
def settings(database_url):
    return Settings(database_url=database_url, public_url="http://testserver", cookie_secure=False)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def application(conn, pmc):
    """Unit 1's application from two applicants, approved."""
    pmc_id = pmc.pmc_id
    unit = add_unit(conn, pmc_id, pmc.owners[0].property_id, "Unit 1")
    staff = add_staff(conn, pmc_id, "Leasing staff")
    link = open_application_link(conn, pmc_id, unit, staff)
    made = submit_application(
        conn,
        pmc_id,
        link,
        applicants=[("Applicant A", "a@example.com"), ("Applicant B", "b@example.com")],
    )
    record_decision(conn, pmc_id, made, staff, "approved")
    return made


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert detail in str(answer.json()["detail"]), answer.text


def add(client, name="Residential lease", body=LEASE_TEXT, used_for="lease"):
    return client.post(
        "/api/notices/templates", json={"name": name, "body": body, "used_for": used_for}
    )


def terms(**change):
    return {
        "starts_on": "2027-01-01",
        "ends_on": None,
        "monthly_rent": "1500.00",
        "send": False,
        **change,
    }


def text(data):
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def test_a_template_is_for_notices_or_leases_each_with_its_own_blanks(client, staff):
    lease = ok(add(client), 201)
    assert (lease["name"], lease["used_for"]) == ("Residential lease", "lease")
    notice = ok(add(client, "Late rent", "Dear {{tenant_names}}", "notice"), 201)
    assert notice["used_for"] == "notice"
    # A notice's blank isn't a lease's, and a lease's isn't a notice's.
    refused(add(client, "Bad lease", "{{balance_due}}"), 422, "{{balance_due}} is not a blank")
    refused(add(client, "Bad notice", "{{security_deposit}}", "notice"), 422, "is not a blank")
    refused(add(client, "Odd", "x", "flyer"), 422)

    listed = ok(client.get("/api/notices/templates"))
    assert [(t["name"], t["used_for"]) for t in listed["templates"]] == [
        ("Late rent", "notice"),
        ("Residential lease", "lease"),
    ]
    assert "security_deposit" in {f["name"] for f in listed["lease_fields"]}
    assert "balance_due" in {f["name"] for f in listed["fields"]}
    # It stays what it was made for.
    change = {"name": "Residential lease", "body": "x", "used_for": "notice"}
    refused(
        client.post(f"/api/notices/templates/{lease['id']}", json=change),
        422,
        "this template is for a lease",
    )


def test_a_draft_lease_is_the_company_lease_template_filled_from_its_terms(
    client, conn, staff, pmc, application
):
    template = ok(add(client), 201)
    body = terms(
        template_id=template["id"],
        security_deposit="1500.00",
        recurring_charges=[
            {"kind": "fee", "memo": "Pet rent", "amount": "25.00"},
            {"kind": "credit", "memo": "Autopay", "amount": "10.00"},
        ],
        signers=[
            {"name": "Applicant A", "email": "a@example.com"},
            {"name": "Applicant B", "email": "b@example.com"},
            {"name": "Guarantor G", "email": "g@example.com", "role": "guarantor"},
        ],
    )

    made = ok(client.post(f"/api/leasing/applications/{application}/offer", json=body), 201)
    offer = made["offer"]
    document = client.get(f"/api/leasing/offers/{offer['id']}/document")

    assert offer["document_title"] == "Residential lease"
    assert document.status_code == 200
    read = " ".join(text(document.content).split())
    for words in (
        "Applicant A and Applicant B, guaranteed by Guarantor G",
        "for Property 1, Unit 1",
        "from 01/01/2027 to month to month",
        "at $1,500.00 a month, with a security deposit of $1,500.00",
        "Pet rent, $25.00 a month; Autopay, $10.00 a month (a credit)",
    ):
        assert words in read, words
    assert conn.execute(
        "SELECT template_version_id FROM trust_lease_offer_templates WHERE offer_id = %s",
        (offer["id"],),
    ).fetchone() == (uuid.UUID(template["version_id"]),)


def test_a_renewal_fills_none_where_there_is_nothing(client, conn, staff, pmc):
    template = ok(add(client), 201)
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit 2")
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 1")
    lease = open_lease(
        conn, pmc.pmc_id, unit, date(2026, 1, 1), date(2026, 12, 31), "1200", [tenant]
    )
    body = terms(
        template_id=template["id"],
        ends_on="2027-12-31",
        signers=[{"name": "Tenant 1", "email": "t1@example.com"}],
    )

    made = ok(client.post(f"/api/leasing/leases/{lease}/renewal", json=body), 201)
    read = " ".join(
        text(client.get(f"/api/leasing/offers/{made['offer']['id']}/document").content).split()
    )

    assert "Tenant 1, guaranteed by none" in read
    assert "to 12/31/2027" in read
    assert "security deposit of none" in read
    assert "Also each month: none" in read


def test_a_draft_lease_takes_the_pdf_or_a_lease_template(client, conn, staff, pmc, application):
    notice = ok(add(client, "Late rent", "Dear {{tenant_names}}", "notice"), 201)
    other = make_pmc(conn)
    theirs = conn.execute(
        "INSERT INTO trust_notice_templates (pmc_id, used_for) VALUES (%s, 'lease') RETURNING id",
        (other.pmc_id,),
    ).fetchone()[0]
    path = f"/api/leasing/applications/{application}/offer"
    pdf = {"document_title": "Lease", "document_base64": base64.b64encode(LEASE_PDF).decode()}
    lease = ok(add(client), 201)

    refused(client.post(path, json=terms()), 422, "or name a lease template")
    refused(client.post(path, json=terms(template_id=lease["id"], **pdf)), 422, "lease template")
    refused(client.post(path, json=terms(template_id=notice["id"])), 404, "no such lease template")
    refused(client.post(path, json=terms(template_id=str(theirs))), 404, "no such lease template")
    refused(client.post(path, json=terms(document_base64=pdf["document_base64"])), 422, "title")
    # A notice comes from a notice template only.
    notice_from_lease = {"lease_id": str(uuid.uuid4()), "template_id": lease["id"]}
    notice_from_lease["dated_on"] = "2026-10-22"
    refused(client.post("/api/notices/preview", json=notice_from_lease), 404, "no such notice")
    ok(client.post(path, json=terms(**pdf)), 201)  # the company's PDF, as before


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_keep_lease_templates(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    assert add(client).status_code == (401 if caller == "nobody" else 403)
