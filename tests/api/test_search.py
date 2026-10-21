"""The search at the top of every staff page (src/pms/api/rentals.py; the founder's review
q135): besides properties, units, tenants and owners by name, vendors, bills, work orders,
applicants, leads and files, and people by the email, phone or street on file. Always the
session's PMC."""

from datetime import UTC, datetime

import pytest
from helpers import (
    add_document,
    add_tenant,
    add_work_order,
    enter_bill,
    make_pmc,
    open_application_link,
    submit_application,
)
from people import join_as_owner, join_as_staff

TODAY = datetime.now(UTC).date()


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def found(client, q):
    answer = client.get("/api/search", params={"q": q})
    assert answer.status_code == 200, answer.text
    return [(f["kind"], f["label"], f["detail"], f["href"]) for f in answer.json()]


def contact(conn, pmc, column, person, **details):
    conn.execute(
        f"INSERT INTO trust_contacts (pmc_id, {column}, {', '.join(details)})"  # noqa: S608
        f" VALUES (%s, %s, {', '.join(['%s'] * len(details))})",
        (pmc.pmc_id, person, *details.values()),
    )


def test_vendors_bills_and_work_orders_are_found_with_the_page_each_opens(client, conn, staff, pmc):
    prop = pmc.owners[0].property_id
    conn.execute(
        "UPDATE trust_vendors SET display_name = 'Juniper Plumbing' WHERE id = %s", (pmc.vendor_id,)
    )
    bill = enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        pmc.owners[0].account,
        "JUN-77",
        TODAY,
        TODAY,
        "80.00",
        memo="Juniper hedge trim",
    )
    order = add_work_order(conn, pmc.pmc_id, prop, None, "Juniper roots in the drain", TODAY)

    assert found(client, "juniper") == [
        ("vendor", "Juniper Plumbing", "", f"/vendors/{pmc.vendor_id}"),
        (
            "bill",
            "Juniper Plumbing JUN-77",
            f"Property 1 · Juniper hedge trim · due {TODAY:%m/%d/%Y}",
            f"/bills/{bill}",
        ),
        ("work order", "Juniper roots in the drain", "Property 1", f"/work-orders/{order}"),
    ]
    # A bill by its reference alone.
    assert [k for k, *_ in found(client, "JUN-77")] == ["bill"]


def test_people_are_found_by_the_email_phone_or_street_on_file(client, conn, staff, pmc):
    prop = pmc.owners[0].property_id
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant Q")
    contact(conn, pmc, "tenant_id", tenant, email="quill.q@example.com", phone="(555) 555-0187")
    contact(
        conn,
        pmc,
        "owner_id",
        pmc.owners[0].owner_id,
        address_line_1="9 Quillfeather Lane",
        city="Sampletown",
        state="ZZ",
        postal_code="00000",
    )
    contact(conn, pmc, "vendor_id", pmc.vendor_id, alternate_phone="555.555.0187")

    by_email = found(client, "quill.q@")
    by_phone = found(client, "555-0187")
    by_street = found(client, "quillfeather")

    assert [(k, label) for k, label, *_ in by_email] == [("tenant", "Tenant Q")]
    assert [(k, label) for k, label, *_ in by_phone] == [
        ("tenant", "Tenant Q"),
        ("vendor", "Vendor 1"),
    ]
    assert [(k, label) for k, label, *_ in by_street] == [("owner", "Owner 1")]
    # Too few digits to be part of a phone number find nothing by phone.
    assert found(client, "01") == []


def test_applicants_leads_and_files_are_found(client, conn, staff, pmc):
    prop = pmc.owners[0].property_id
    unit = conn.execute(
        "INSERT INTO trust_units (pmc_id, property_id, display_name) VALUES (%s, %s, 'Unit 4')"
        " RETURNING id",
        (pmc.pmc_id, prop),
    ).fetchone()[0]
    link = open_application_link(conn, pmc.pmc_id, unit, staff.person_id)
    application = submit_application(
        conn, pmc.pmc_id, link, applicants=(("Rowan Zephyr", "rowan.z@example.com"),)
    )
    lead = conn.execute(
        "INSERT INTO trust_leads (pmc_id, display_name, phone, property_id)"
        " VALUES (%s, 'Ash Zephyr', '555-555-0199', %s) RETURNING id",
        (pmc.pmc_id, prop),
    ).fetchone()[0]
    kept = add_document(conn, pmc.pmc_id, "w9", pmc.vendor_id, title="Zephyr W-9")
    removed = add_document(conn, pmc.pmc_id, "w9", pmc.vendor_id, title="Zephyr old W-9")
    conn.execute(
        "INSERT INTO trust_document_removals (document_id, pmc_id) VALUES (%s, %s)",
        (removed, pmc.pmc_id),
    )

    assert found(client, "zephyr") == [
        ("applicant", "Rowan Zephyr", "Unit 4, Property 1", f"/applicants/{application}"),
        ("lead", "Ash Zephyr", "Property 1", f"/leads/{lead}"),
        ("file", "Zephyr W-9", f"{TODAY:%m/%d/%Y}", f"/vendors/{pmc.vendor_id}"),
    ]
    assert [k for k, *_ in found(client, "rowan.z@example")] == ["applicant"]
    assert [k for k, *_ in found(client, "5550199")] == ["lead"]
    assert kept != removed


def test_another_companys_records_are_never_found(client, conn, staff, pmc):
    theirs = make_pmc(conn)
    conn.execute(
        "UPDATE trust_vendors SET display_name = 'Juniper Elsewhere' WHERE id = %s",
        (theirs.vendor_id,),
    )
    enter_bill(
        conn,
        theirs.pmc_id,
        theirs.vendor_id,
        theirs.owners[0].account,
        "JUN-99",
        TODAY,
        TODAY,
        "10.00",
        memo="Juniper",
    )
    add_work_order(conn, theirs.pmc_id, theirs.owners[0].property_id, None, "Juniper", TODAY)
    contact(conn, theirs, "vendor_id", theirs.vendor_id, email="juniper@example.com")

    assert found(client, "juniper") == []


@pytest.mark.parametrize("q", ["", "j", "x" * 101])
def test_a_search_of_too_few_or_too_many_letters_is_refused(client, staff, q):
    assert client.get("/api/search", params={"q": q}).status_code == 422


def test_only_staff_search(client, app_conn, pmc):
    assert client.get("/api/search", params={"q": "juniper"}).status_code == 401
    join_as_owner(client, app_conn, pmc)
    assert client.get("/api/search", params={"q": "juniper"}).status_code == 403
