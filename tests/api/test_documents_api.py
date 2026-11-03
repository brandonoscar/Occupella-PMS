"""Staff's documents (src/pms/api/documents.py) and what an owner sees of them in the owner
portal (src/pms/api/owner.py): their own documents, their properties' and units' files and their
bills' receipts (q046). Every file here is synthetic."""

import base64
import uuid
from datetime import UTC, date, datetime

import pytest
from helpers import (
    JPEG,
    PDF,
    PNG,
    add_document,
    add_unit,
    add_work_order,
    enter_bill,
    make_pmc,
)
from people import join_as_owner, join_as_staff, join_as_tenant


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Clerk 1", roles=[])


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert detail in answer.json()["detail"]


def upload(client, kind, about, content=PDF, content_type="application/pdf", title="W-9 2026"):
    body = {
        "kind": kind,
        "about_id": str(about),
        "title": title,
        "content_type": content_type,
        "content_base64": base64.b64encode(content).decode(),
    }
    return client.post("/api/documents", json=body)


def test_staff_keep_each_kind_of_document_and_download_it(client, conn, staff, pmc):
    bill = enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        pmc.owners[0].account,
        "INV-7",
        date(2026, 3, 2),
        date(2026, 4, 1),
        "80.00",
    )
    library = ok(client.get("/api/documents"))
    assert library["documents"] == []
    assert library["subjects"]["w9"] == [{"id": str(pmc.vendor_id), "label": "Vendor 1"}]
    assert library["subjects"]["receipt"] == [
        {"id": str(bill), "label": "Vendor 1 INV-7, 03/02/2026"}
    ]
    assert [s["label"] for s in library["subjects"]["owner_document"]] == ["Owner 1", "Owner 2"]

    w9 = ok(upload(client, "w9", pmc.vendor_id, title="  W-9 2026 "), 201)
    receipt = ok(upload(client, "receipt", bill, JPEG, "image/jpeg", "Receipt"), 201)

    listed = ok(client.get("/api/documents"))["documents"]
    assert [
        (d["id"], d["kind"], d["about"], d["title"], d["size"], d["uploaded_by"]) for d in listed
    ] == [
        (receipt["id"], "receipt", "Vendor 1 INV-7, 03/02/2026", "Receipt", len(JPEG), "Clerk 1"),
        (w9["id"], "w9", "Vendor 1", "W-9 2026", len(PDF), "Clerk 1"),
    ]
    got = client.get(f"/api/documents/{w9['id']}/file")
    assert got.status_code == 200
    assert got.content == PDF
    assert got.headers["content-type"] == "application/pdf"
    assert got.headers["content-disposition"] == 'attachment; filename="W-9-2026.pdf"'
    assert got.headers["content-security-policy"] == "default-src 'none'; sandbox"


def test_a_work_orders_photos_are_documents_about_it(client, conn, staff, pmc):
    order = add_work_order(
        conn, pmc.pmc_id, pmc.owners[0].property_id, None, "Leak", datetime(2026, 3, 2, tzinfo=UTC)
    )
    assert ok(client.get("/api/documents"))["subjects"]["work_order_photo"] == [
        {"id": str(order), "label": "Property 1: Leak"}
    ]

    photo = ok(upload(client, "work_order_photo", order, PNG, "image/png", "Under the sink"), 201)

    (listed,) = ok(client.get("/api/documents"))["documents"]
    assert (listed["id"], listed["kind"], listed["about_id"], listed["about"]) == (
        photo["id"],
        "work_order_photo",
        str(order),
        "Property 1: Leak",
    )
    # A work order photo is about a work order of the PMC, not anything else.
    refused(upload(client, "work_order_photo", pmc.vendor_id, PNG, "image/png"), 404)
    other = make_pmc(conn)
    theirs = add_work_order(
        conn, other.pmc_id, other.owners[0].property_id, None, "X", datetime(2026, 3, 2, tzinfo=UTC)
    )
    refused(upload(client, "work_order_photo", theirs, PNG, "image/png"), 404)


def test_a_removed_document_is_no_longer_listed_or_served(client, conn, staff, pmc):
    made = ok(upload(client, "w9", pmc.vendor_id), 201)
    ok(client.post(f"/api/documents/{made['id']}/remove", json={}), 204)

    assert ok(client.get("/api/documents"))["documents"] == []
    refused(client.get(f"/api/documents/{made['id']}/file"), 404, "no such document")
    refused(client.post(f"/api/documents/{made['id']}/remove", json={}), 409, "removed already")


@pytest.mark.parametrize(
    ("content", "content_type", "why"),
    [
        (PNG, "application/pdf", "the file isn't a PDF"),
        (PDF, "image/png", "the file isn't a PNG"),
        (PNG, "image/jpeg", "the file isn't a JPG"),
        # Named, or pytest makes the 5 MB of bytes the test's ID, and `pytest -v` in CI prints it.
        pytest.param(
            b"%PDF-" + b"x" * (5 * 1024 * 1024), "application/pdf", "at most 5 MB", id="over-5-mb"
        ),
    ],
)
def test_a_file_is_what_it_says_and_at_most_5_mb(client, staff, pmc, content, content_type, why):
    refused(upload(client, "w9", pmc.vendor_id, content, content_type), 422, why)


def test_a_document_is_about_something_of_the_pmc(client, conn, staff, pmc):
    other = make_pmc(conn)
    refused(upload(client, "w9", other.vendor_id), 404, "nothing of this PMC")
    # A vendor isn't an owner.
    refused(upload(client, "owner_document", pmc.vendor_id), 404)
    theirs = add_document(conn, other.pmc_id, "w9", other.vendor_id)
    refused(client.get(f"/api/documents/{theirs}/file"), 404)
    refused(client.post(f"/api/documents/{theirs}/remove", json={}), 404)


def test_an_owner_reads_their_own_documents_only(client, other_client, conn, app_conn, pmc):
    join_as_staff(other_client, conn, app_conn, pmc)
    mine = ok(
        upload(other_client, "owner_document", pmc.owners[0].owner_id, title="Lease 2026"), 201
    )
    theirs = ok(upload(other_client, "owner_document", pmc.owners[1].owner_id), 201)
    w9 = ok(upload(other_client, "w9", pmc.vendor_id), 201)
    join_as_owner(client, app_conn, pmc)

    listed = ok(client.get("/api/owner/documents"))
    assert [(d["id"], d["title"], d["content_type"]) for d in listed] == [
        (mine["id"], "Lease 2026", "application/pdf")
    ]
    assert client.get(f"/api/owner/documents/{mine['id']}/file").content == PDF
    for not_theirs in (theirs["id"], w9["id"]):
        refused(client.get(f"/api/owner/documents/{not_theirs}/file"), 404)
    ok(other_client.post(f"/api/documents/{mine['id']}/remove", json={}), 204)
    assert ok(client.get("/api/owner/documents")) == []


def test_an_owner_reads_their_properties_files_and_their_bills_receipts(
    client, other_client, conn, app_conn, pmc
):
    join_as_staff(other_client, conn, app_conn, pmc)
    first, second = pmc.owners
    unit = add_unit(conn, pmc.pmc_id, first.property_id, name="Unit 4")
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    day = date(2026, 3, 2)
    ours = enter_bill(conn, pmc.pmc_id, pmc.vendor_id, first.account, "INV-1", day, day, "80.00")
    theirs = enter_bill(conn, pmc.pmc_id, pmc.vendor_id, second.account, "INV-2", day, day, "9")
    made = {
        title: ok(upload(other_client, kind, about, title=title), 201)["id"]
        for title, kind, about in (
            ("Insurance binder", "property_document", first.property_id),
            ("Water heater manual", "unit_document", unit),
            ("Plumber's receipt", "receipt", ours),
            ("Their binder", "property_document", second.property_id),
            ("Their receipt", "receipt", theirs),
            ("Tenant's ID", "tenant_document", tenant),
            ("W-9", "w9", pmc.vendor_id),
        )
    }
    join_as_owner(client, app_conn, pmc)

    listed = ok(client.get("/api/owner/documents"))
    assert sorted((d["title"], d["about"], d["about_name"]) for d in listed) == [
        ("Insurance binder", "property", "Property 1"),
        ("Plumber's receipt", "bill", "Bill INV-1, Vendor 1"),
        ("Water heater manual", "property", "Property 1, Unit 4"),
    ]
    for title in ("Insurance binder", "Plumber's receipt", "Water heater manual"):
        assert client.get(f"/api/owner/documents/{made[title]}/file").content == PDF
    for title in ("Their binder", "Their receipt", "Tenant's ID", "W-9"):
        refused(client.get(f"/api/owner/documents/{made[title]}/file"), 404)


STAFF_ENDPOINTS = [
    ("get", "/api/documents"),
    ("post", "/api/documents"),
    ("get", f"/api/documents/{uuid.uuid4()}/file"),
    ("post", f"/api/documents/{uuid.uuid4()}/remove"),
]


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
@pytest.mark.parametrize(("method", "path"), STAFF_ENDPOINTS)
def test_only_staff_keep_documents(client, conn, app_conn, pmc, caller, method, path):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    answer = client.get(path) if method == "get" else client.post(path, json={})

    assert answer.status_code == (401 if caller == "nobody" else 403)


@pytest.mark.parametrize("caller", ["nobody", "staff", "tenant"])
@pytest.mark.parametrize(
    "path", ["/api/owner/documents", f"/api/owner/documents/{uuid.uuid4()}/file"]
)
def test_only_an_owner_reads_owner_documents(client, conn, app_conn, pmc, caller, path):
    if caller == "staff":
        join_as_staff(client, conn, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    assert client.get(path).status_code == (401 if caller == "nobody" else 403)


@pytest.mark.parametrize(
    "body",
    [
        {
            "kind": "lease",
            "about_id": str(uuid.uuid4()),
            "title": "x",
            "content_type": "application/pdf",
            "content_base64": "JVBERi0=",
        },
        {
            "kind": "w9",
            "about_id": "x",
            "title": "x",
            "content_type": "application/pdf",
            "content_base64": "JVBERi0=",
        },
        {
            "kind": "w9",
            "about_id": str(uuid.uuid4()),
            "title": " ",
            "content_type": "application/pdf",
            "content_base64": "JVBERi0=",
        },
        {
            "kind": "w9",
            "about_id": str(uuid.uuid4()),
            "title": "x",
            "content_type": "text/html",
            "content_base64": "JVBERi0=",
        },
        {
            "kind": "w9",
            "about_id": str(uuid.uuid4()),
            "title": "x",
            "content_type": "application/pdf",
            "content_base64": "not base64!",
        },
    ],
)
def test_bad_input_is_refused(client, staff, body):
    refused(client.post("/api/documents", json=body), 422)


def test_a_download_is_named_for_its_title(client, staff, pmc):
    made = ok(upload(client, "w9", pmc.vendor_id, PNG, "image/png", "../../Väry Odd name!!"), 201)
    got = client.get(f"/api/documents/{made['id']}/file")
    assert got.headers["content-disposition"] == 'attachment; filename="V-ry-Odd-name.png"'
    made = ok(upload(client, "w9", pmc.vendor_id, PNG, "image/png", "***"), 201)
    got = client.get(f"/api/documents/{made['id']}/file")
    assert got.headers["content-disposition"] == 'attachment; filename="document.png"'


def test_a_document_named_in_a_path_is_a_uuid(client, other_client, conn, app_conn, pmc):
    join_as_staff(other_client, conn, app_conn, pmc)
    join_as_owner(client, app_conn, pmc)
    refused(other_client.get("/api/documents/not-a-uuid/file"), 422)
    refused(other_client.post("/api/documents/not-a-uuid/remove", json={}), 422)
    refused(client.get("/api/owner/documents/not-a-uuid/file"), 422)
