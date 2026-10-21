"""Files on any record (src/pms/api/documents.py, migration 0074, ADR 0006 step 12): a
property's, unit's, tenant's, lease's or vendor's documents, and every file of the company a page
at a time, by category and property group. Always the session's PMC; only staff."""

import base64
from datetime import UTC, date, datetime

import pytest
from helpers import (
    PDF,
    PNG,
    add_document,
    add_tenant,
    add_unit,
    add_work_order,
    enter_bill,
    make_pmc,
    open_lease,
)
from people import join_as_owner, join_as_staff


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Clerk 1", roles=[])


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def upload(client, kind, about, title, content=PDF, content_type="application/pdf"):
    body = {
        "kind": kind,
        "about_id": str(about),
        "title": title,
        "content_type": content_type,
        "content_base64": base64.b64encode(content).decode(),
    }
    return client.post("/api/documents", json=body)


@pytest.fixture
def records(conn, pmc):
    """Unit A at Property 1 leased to Tenant A; a work order at Property 2."""
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit A")
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant A")
    lease = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "1000.00", [tenant])
    order = add_work_order(
        conn,
        pmc.pmc_id,
        pmc.owners[1].property_id,
        None,
        "Gutter",
        datetime(2026, 3, 2, tzinfo=UTC),
    )
    return {"unit": unit, "tenant": tenant, "lease": lease, "order": order}


def test_any_record_takes_a_document_of_its_own(client, conn, staff, pmc, records):
    made = {
        kind: ok(upload(client, kind, about, title), 201)["id"]
        for kind, about, title in (
            ("property_document", pmc.owners[0].property_id, "Deed"),
            ("unit_document", records["unit"], "Floor plan"),
            ("tenant_document", records["tenant"], "Pet agreement"),
            ("lease_document", records["lease"], "Signed lease"),
            ("vendor_document", pmc.vendor_id, "Contract"),
        )
    }

    library = ok(client.get("/api/documents"))
    about = {d["id"]: (d["kind"], d["about"]) for d in library["documents"]}
    assert about == {
        made["property_document"]: ("property_document", "Property 1"),
        made["unit_document"]: ("unit_document", "Property 1 / Unit A"),
        made["tenant_document"]: ("tenant_document", "Tenant A (Property 1)"),
        made["lease_document"]: ("lease_document", "Property 1 / Unit A: Tenant A"),
        made["vendor_document"]: ("vendor_document", "Vendor 1"),
    }
    assert [s["label"] for s in library["subjects"]["unit_document"]] == ["Property 1 / Unit A"]
    # A kind is about its own kind of record only.
    assert upload(client, "unit_document", records["tenant"], "x").status_code == 404
    assert upload(client, "property_document", pmc.vendor_id, "x").status_code == 404


def test_the_files_list_names_each_files_record_and_its_page(client, conn, staff, pmc, records):
    deed = ok(upload(client, "property_document", pmc.owners[0].property_id, "Deed"), 201)["id"]
    w9 = add_document(conn, pmc.pmc_id, "w9", pmc.vendor_id)
    photo = add_document(
        conn, pmc.pmc_id, "work_order_photo", records["order"], PNG, "image/png", "Gutter photo"
    )

    page = ok(client.get("/api/documents/files"))

    assert page["total"] == 3
    rows = {r["id"]: r for r in page["items"]}
    assert (rows[deed]["title"], rows[deed]["about_path"], rows[deed]["uploaded_by"]) == (
        "Deed",
        f"/properties/{pmc.owners[0].property_id}",
        "Clerk 1",
    )
    assert rows[str(w9)]["about_path"] == f"/vendors/{pmc.vendor_id}"
    assert rows[str(photo)]["about"] == "Property 2: Gutter"
    # Newest first, a page at a time, and by category.
    assert [r["id"] for r in page["items"]][0] == str(photo)
    second = ok(client.get("/api/documents/files", params={"per_page": 2, "page": 2}))
    assert [r["id"] for r in second["items"]] == [deed]
    only = ok(client.get("/api/documents/files", params={"kind": "w9"}))
    assert [r["id"] for r in only["items"]] == [str(w9)]


def test_the_files_list_narrows_to_where_each_record_is(client, conn, staff, pmc, records):
    one, two = pmc.owners[0].property_id, pmc.owners[1].property_id
    by_lease = ok(upload(client, "lease_document", records["lease"], "Signed lease"), 201)["id"]
    by_owner = add_document(conn, pmc.pmc_id, "owner_document", pmc.owners[0].owner_id)
    by_bill = add_document(
        conn,
        pmc.pmc_id,
        "receipt",
        enter_bill(
            conn,
            pmc.pmc_id,
            pmc.vendor_id,
            pmc.owners[1].account,
            "INV-1",
            date(2026, 3, 2),
            date(2026, 3, 9),
            "50.00",
        ),
        title="Receipt",
    )
    by_order = add_document(
        conn, pmc.pmc_id, "work_order_photo", records["order"], PNG, "image/png"
    )
    w9 = add_document(conn, pmc.pmc_id, "w9", pmc.vendor_id)

    def ids(**params):
        return {r["id"] for r in ok(client.get("/api/documents/files", params=params))["items"]}

    assert ids(property=str(one)) == {by_lease, str(by_owner)}
    assert ids(property=str(two)) == {str(by_bill), str(by_order)}
    group = ok(
        client.post(
            "/api/property-groups", json={"name": "All", "property_ids": [str(one), str(two)]}
        ),
        201,
    )["id"]
    # A vendor's file is at no property: a scope leaves it out; nothing chosen, it is there.
    assert str(w9) not in ids(group=group)
    assert str(w9) in ids()


def test_a_removed_file_and_another_pmcs_are_not_listed(client, conn, staff, pmc):
    gone = add_document(conn, pmc.pmc_id, "w9", pmc.vendor_id)
    ok(client.post(f"/api/documents/{gone}/remove", json={}), 204)
    theirs = make_pmc(conn)
    add_document(conn, theirs.pmc_id, "w9", theirs.vendor_id)

    assert ok(client.get("/api/documents/files")) == {
        "items": [],
        "total": 0,
        "page": 1,
        "per_page": 50,
    }


@pytest.mark.parametrize("caller", ["nobody", "owner"])
def test_only_staff_list_the_files(client, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    assert client.get("/api/documents/files").status_code == (401 if caller == "nobody" else 403)


@pytest.mark.parametrize(
    "params",
    [{"kind": "deed"}, {"group": "north"}, {"property": "1"}, {"per_page": 0}, {"page": 0}],
)
def test_a_bad_files_query_is_refused(client, staff, params):
    assert client.get("/api/documents/files", params=params).status_code == 422
