"""The portals' second version (migration 20261103000100; the founder's choice of 11/03, "C"):
what an owner sees of their units, tenants and repairs, and changes of their own contact details
(src/pms/api/owner_portal.py); conversations between the company and its tenants and owners
(src/pms/api/messages.py); a tenant's notice to move out and their own lease documents
(src/pms/api/tenant.py, src/pms/api/leaving.py); and staff's announcements to tenants
(src/pms/api/announcements.py). Each for the session's own person and company only."""

import base64
from datetime import UTC, datetime, timedelta

import pytest
from helpers import PDF, add_tenant, add_unit, add_work_order, make_pmc, open_lease, step_work_order
from people import join_as, join_as_owner, join_as_staff, join_as_tenant, tenant_of

from pms.api import messages


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def today():
    return datetime.now(UTC).date()


@pytest.fixture
def home(conn, pmc):
    """Tenant 1's lease at owner 1's property, in effect since a month ago, and a vacant unit."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit A")
    add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit B")
    tenant = tenant_of(conn, pmc)
    lease = open_lease(
        conn, pmc.pmc_id, unit, today() - timedelta(days=30), None, "1250.00", [tenant]
    )
    return {"unit": unit, "tenant": tenant, "lease": lease}


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


# --- the owner's units, tenants and repairs ---------------------------------------------------


def test_an_owner_sees_their_units_who_rents_each_and_what_is_vacant(
    other_client, conn, app_conn, pmc, home
):
    join_as_owner(other_client, app_conn, pmc)
    ok(other_client.post("/api/owner/messages", json={"body": "warm up"}), 201)  # unrelated

    page = ok(other_client.get(f"/api/owner/properties/{pmc.owners[0].property_id}"))

    assert page["name"] == "Property 1"
    assert page["occupied"] == 1 and page["vacant"] == 1
    a, b = page["units"]
    assert a["name"] == "Unit A" and b["name"] == "Unit B"
    assert a["lease"]["tenants"] == ["Tenant 1"]
    assert a["lease"]["monthly_rent"] == "1250.00" and a["lease"]["status"] == "current"
    assert a["lease"]["leaving_on"] is None and a["vacant_since"] is None
    assert b["lease"] is None and b["vacant_since"] is None  # never leased in the PMS
    # Tenants' names, never their contact details.
    assert "email" not in str(page) and "phone" not in str(page)


def test_another_owners_property_reads_as_not_found(other_client, app_conn, pmc, home):
    join_as_owner(other_client, app_conn, pmc, n=1)

    answer = other_client.get(f"/api/owner/properties/{pmc.owners[0].property_id}")

    assert answer.status_code == 404
    assert answer.json()["detail"] == "no such property of yours"


def test_an_owner_sees_the_work_orders_at_their_properties_only(
    other_client, conn, app_conn, pmc, home
):
    opened = datetime.now(UTC) - timedelta(days=2)
    mine = add_work_order(conn, pmc.pmc_id, pmc.owners[0].property_id, home["unit"], "Leak", opened)
    add_work_order(conn, pmc.pmc_id, pmc.owners[1].property_id, None, "Owner 2's roof", opened)
    step_work_order(conn, pmc.pmc_id, mine, "assigned", opened, vendor_id=pmc.vendor_id)
    join_as_owner(other_client, app_conn, pmc)

    found = ok(other_client.get("/api/owner/work-orders"))

    assert [(w["summary"], w["status"], w["unit"]["name"]) for w in found] == [
        ("Leak", "assigned", "Unit A")
    ]


def test_an_owner_changes_their_own_contact_details_and_the_record_says_they_did(
    client, other_client, conn, app_conn, pmc, staff
):
    owner = join_as_owner(other_client, app_conn, pmc)
    assert ok(client.get(f"/api/owners/{owner.person_id}"))["contact_by_owner_at"] is None
    contact = {
        "email": "owner.own@example.com",
        "phone": "(555) 555-0199",
        "alternate_phone": None,
        "address_line_1": None,
        "address_line_2": None,
        "city": None,
        "state": None,
        "postal_code": None,
    }

    ok(other_client.post("/api/owner/contact", json=contact), 204)
    ok(other_client.post("/api/owner/contact", json=contact), 204)  # the same again: nothing

    assert ok(other_client.get("/api/owner/contact")) == contact
    assert conn.execute(
        "SELECT by_person, recorded_by FROM trust_contacts WHERE owner_id = %s",
        (owner.person_id,),
    ).fetchall() == [(True, None)]
    # Staff see the new details on the owner's page, and that the owner gave them.
    page = ok(client.get(f"/api/owners/{owner.person_id}"))
    assert page["contact"]["email"] == "owner.own@example.com"
    assert page["contact_by_owner_at"] is not None


def test_an_owners_bad_contact_details_are_refused(other_client, app_conn, pmc):
    join_as_owner(other_client, app_conn, pmc)

    answer = other_client.post("/api/owner/contact", json={"email": "not an email"})

    assert answer.status_code == 422


@pytest.mark.parametrize(
    "path",
    ["/api/owner/properties/00000000-0000-0000-0000-000000000000", "/api/owner/work-orders"],
)
def test_only_an_owner_reaches_their_portal(client, other_client, conn, app_conn, pmc, path):
    assert other_client.get(path).status_code == 401
    join_as_tenant(other_client, conn, app_conn, pmc)
    assert other_client.get(path).status_code == 403
    join_as_staff(client, conn, app_conn, pmc)
    assert client.get(path).status_code == 403


# --- messages -------------------------------------------------------------------------------


def test_a_tenant_writes_staff_see_it_waiting_and_answer_and_the_tenant_reads_it(
    client, other_client, conn, app_conn, pmc, staff
):
    tenant = join_as_tenant(other_client, conn, app_conn, pmc)

    mine = ok(other_client.post("/api/tenant/messages", json={"body": " The gate sticks. "}), 201)
    inbox = ok(client.get("/api/messages"))
    waiting = ok(client.get("/api/today"))["messages_waiting"]
    answered = ok(
        client.post(f"/api/messages/tenant/{tenant.person_id}", json={"body": "On it today."}),
        201,
    )
    thread = ok(other_client.get("/api/tenant/messages"))
    after = ok(client.get("/api/messages"))
    emailed = conn.execute(
        "SELECT kind, to_address, body FROM trust_emails WHERE tenant_id = %s",
        (tenant.person_id,),
    ).fetchall()

    assert mine["by"] == "person" and mine["body"] == "The gate sticks."
    assert [(t["name"], t["waiting"], t["preview"]) for t in inbox] == [
        ("Tenant 1", True, "The gate sticks.")
    ]
    assert [t["person_id"] for t in waiting] == [str(tenant.person_id)]
    assert [(m["by"], m["body"]) for m in thread] == [
        ("person", "The gate sticks."),
        ("company", "On it today."),
    ]
    assert answered["emailed_to"] == tenant.email
    assert after[0]["waiting"] is False and after[0]["count"] == 2
    # A copy of the answer went by email, with the way back to the portal.
    (kind, to, body), *_ = emailed
    assert kind == "message" and to == tenant.email
    assert "On it today." in body and "/messages" in body


def test_an_owner_has_their_own_conversation_and_never_reads_a_tenants(
    client, other_client, conn, app_conn, pmc, staff
):
    tenant_id = tenant_of(conn, pmc)
    ok(client.post(f"/api/messages/tenant/{tenant_id}", json={"body": "For the tenant"}), 201)
    owner = join_as_owner(other_client, app_conn, pmc)
    ok(other_client.post("/api/owner/messages", json={"body": "When is the roof done?"}), 201)

    mine = ok(other_client.get("/api/owner/messages"))
    staff_view = ok(client.get(f"/api/messages/owner/{owner.person_id}"))

    assert [m["body"] for m in mine] == ["When is the roof done?"]
    assert staff_view["name"] == "Owner 1"
    assert [m["body"] for m in staff_view["messages"]] == ["When is the roof done?"]


def test_staff_never_reach_another_companys_person(client, conn, app_conn, pmc, staff):
    other = make_pmc(conn)
    stranger = tenant_of(conn, other)

    assert client.get(f"/api/messages/tenant/{stranger}").status_code == 404
    answer = client.post(f"/api/messages/tenant/{stranger}", json={"body": "hello"})

    assert answer.status_code == 404
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_messages WHERE tenant_id = %s", (stranger,)
        ).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize("body", ["", "   ", "x" * 4001, "nul \x00 here"])
def test_a_message_that_isnt_one_is_refused(other_client, conn, app_conn, pmc, body):
    join_as_tenant(other_client, conn, app_conn, pmc)

    assert other_client.post("/api/tenant/messages", json={"body": body}).status_code == 422


def test_a_person_writes_at_most_so_many_messages_a_day(other_client, conn, app_conn, pmc):
    join_as_tenant(other_client, conn, app_conn, pmc)
    for n in range(messages.MESSAGES_PER_DAY):
        ok(other_client.post("/api/tenant/messages", json={"body": f"note {n}"}), 201)

    answer = other_client.post("/api/tenant/messages", json={"body": "one more"})

    assert answer.status_code == 429


def test_only_staff_read_the_inbox_and_only_people_their_own_thread(
    client, other_client, conn, app_conn, pmc
):
    assert other_client.get("/api/tenant/messages").status_code == 401
    assert other_client.get("/api/messages").status_code == 401
    join_as_owner(other_client, app_conn, pmc)
    assert other_client.get("/api/messages").status_code == 403
    assert other_client.get("/api/tenant/messages").status_code == 403
    join_as_staff(client, conn, app_conn, pmc)
    assert client.post("/api/owner/messages", json={"body": "x"}).status_code == 403


# --- notice to move out ---------------------------------------------------------------------


def test_a_tenant_gives_notice_and_staff_and_the_owner_see_it(
    client, other_client, conn, app_conn, pmc, home, staff
):
    join_as_tenant(other_client, conn, app_conn, pmc)
    leaving = today() + timedelta(days=45)

    given = ok(
        other_client.post(
            f"/api/tenant/leases/{home['lease']}/move-out-notice",
            json={"leaving_on": leaving.isoformat(), "note": " Moving for work. "},
        ),
        201,
    )
    seen = ok(other_client.get("/api/tenant/leases"))[0]["move_out_notice"]
    lease_page = ok(client.get(f"/api/leases/{home['lease']}"))["move_out_notice"]
    listed = ok(client.get("/api/today"))["move_out_notices"]

    assert given["leaving_on"] == leaving.isoformat() and given["note"] == "Moving for work."
    assert given["given_by"] == "Tenant 1"
    assert seen == given and lease_page == given
    assert [(n["lease_id"], n["leaving_on"]) for n in listed] == [
        (str(home["lease"]), leaving.isoformat())
    ]
    join_as_owner(other_client, app_conn, pmc)
    unit = ok(other_client.get(f"/api/owner/properties/{pmc.owners[0].property_id}"))["units"][0]
    assert unit["lease"]["leaving_on"] == leaving.isoformat()


def test_a_later_notice_replaces_the_earlier(other_client, conn, app_conn, pmc, home):
    join_as_tenant(other_client, conn, app_conn, pmc)
    path = f"/api/tenant/leases/{home['lease']}/move-out-notice"
    first, second = today() + timedelta(days=60), today() + timedelta(days=30)
    ok(other_client.post(path, json={"leaving_on": first.isoformat()}), 201)
    ok(other_client.post(path, json={"leaving_on": second.isoformat()}), 201)

    seen = ok(other_client.get("/api/tenant/leases"))[0]["move_out_notice"]

    assert seen["leaving_on"] == second.isoformat() and seen["note"] is None


def test_a_notice_for_a_past_day_is_refused_in_words(other_client, conn, app_conn, pmc, home):
    join_as_tenant(other_client, conn, app_conn, pmc)

    answer = other_client.post(
        f"/api/tenant/leases/{home['lease']}/move-out-notice",
        json={"leaving_on": (today() - timedelta(days=1)).isoformat()},
    )

    assert answer.status_code == 422
    assert "today or later" in answer.json()["detail"]
    assert "trust:" not in answer.json()["detail"]


def test_a_notice_far_ahead_or_for_a_lease_not_theirs_is_refused(
    other_client, conn, app_conn, pmc, home
):
    other_unit = add_unit(conn, pmc.pmc_id, pmc.owners[1].property_id, "Elsewhere")
    neighbour = add_tenant(conn, pmc.pmc_id, pmc.owners[1].property_id, "Neighbour")
    theirs = open_lease(conn, pmc.pmc_id, other_unit, today(), None, "900.00", [neighbour])
    join_as_tenant(other_client, conn, app_conn, pmc)

    too_far = other_client.post(
        f"/api/tenant/leases/{home['lease']}/move-out-notice",
        json={"leaving_on": (today() + timedelta(days=800)).isoformat()},
    )
    not_mine = other_client.post(
        f"/api/tenant/leases/{theirs}/move-out-notice",
        json={"leaving_on": (today() + timedelta(days=30)).isoformat()},
    )
    bad = other_client.post(
        f"/api/tenant/leases/{home['lease']}/move-out-notice", json={"leaving_on": "soon"}
    )

    assert too_far.status_code == 422
    assert not_mine.status_code == 404
    assert bad.status_code == 422


# --- announcements --------------------------------------------------------------------------


def test_staff_announce_and_tenants_see_theirs_until_taken_down(
    client, other_client, conn, app_conn, pmc, home, staff
):
    here = ok(
        client.post(
            "/api/announcements",
            json={
                "property_id": str(pmc.owners[0].property_id),
                "title": "Water off Tuesday",
                "body": "From 9 to noon.",
            },
        ),
        201,
    )
    ok(
        client.post(
            "/api/announcements", json={"title": "Office closed Friday", "body": "Holiday."}
        ),
        201,
    )
    ok(
        client.post(
            "/api/announcements",
            json={"property_id": str(pmc.owners[1].property_id), "title": "Elsewhere", "body": "-"},
        ),
        201,
    )
    join_as_tenant(other_client, conn, app_conn, pmc)

    before = [a["title"] for a in ok(other_client.get("/api/tenant/announcements"))]
    removed = ok(client.post(f"/api/announcements/{here['id']}/remove", json={}))
    again = client.post(f"/api/announcements/{here['id']}/remove", json={})
    after = [a["title"] for a in ok(other_client.get("/api/tenant/announcements"))]

    assert here["by"] == "Staff 1" and here["property"] == "Property 1"
    assert before == ["Office closed Friday", "Water off Tuesday"]
    assert removed["removed_by"] == "Staff 1"
    assert again.status_code == 409
    assert after == ["Office closed Friday"]


@pytest.mark.parametrize(
    "body",
    [
        {"title": "", "body": "x"},
        {"title": "two\nlines", "body": "x"},
        {"title": "t", "body": ""},
        {"title": "t" * 201, "body": "x"},
    ],
)
def test_an_announcement_that_isnt_one_is_refused(client, staff, body):
    assert client.post("/api/announcements", json=body).status_code == 422


def test_an_announcement_names_a_property_of_the_company(client, conn, staff):
    stranger = make_pmc(conn).owners[0].property_id

    answer = client.post(
        "/api/announcements", json={"property_id": str(stranger), "title": "t", "body": "b"}
    )

    assert answer.status_code == 404


def test_only_staff_announce(client, other_client, conn, app_conn, pmc):
    join_as_tenant(other_client, conn, app_conn, pmc)

    assert (
        other_client.post("/api/announcements", json={"title": "t", "body": "b"}).status_code == 403
    )
    assert other_client.get("/api/announcements").status_code == 403
    assert client.get("/api/tenant/announcements").status_code == 401


# --- a tenant's own lease documents ---------------------------------------------------------


def document(content=PDF, content_type="application/pdf", title="Insurance certificate"):
    return {
        "title": title,
        "content_type": content_type,
        "content_base64": base64.b64encode(content).decode(),
    }


def test_a_tenant_files_a_document_of_their_lease_and_staff_see_it_as_theirs(
    client, other_client, conn, app_conn, pmc, home, staff
):
    join_as_tenant(other_client, conn, app_conn, pmc)

    filed = ok(
        other_client.post(f"/api/tenant/leases/{home['lease']}/documents", json=document()), 201
    )
    files = ok(other_client.get("/api/tenant/leases"))[0]["files"]
    downloaded = other_client.get(filed["url"])
    library = ok(client.get("/api/documents"))["documents"]

    assert filed["from_tenant"] is True
    assert [(f["title"], f["from_tenant"]) for f in files] == [("Insurance certificate", True)]
    assert downloaded.status_code == 200 and downloaded.content == PDF
    assert [(d["title"], d["uploaded_by"]) for d in library] == [
        ("Insurance certificate", "Tenant 1 (the tenant)")
    ]


def test_a_tenants_file_must_be_what_it_says_and_of_their_lease(
    other_client, conn, app_conn, pmc, home
):
    other_unit = add_unit(conn, pmc.pmc_id, pmc.owners[1].property_id, "Elsewhere")
    neighbour = add_tenant(conn, pmc.pmc_id, pmc.owners[1].property_id, "Neighbour")
    theirs = open_lease(conn, pmc.pmc_id, other_unit, today(), None, "900.00", [neighbour])
    join_as_tenant(other_client, conn, app_conn, pmc)

    not_a_pdf = other_client.post(
        f"/api/tenant/leases/{home['lease']}/documents", json=document(content=b"hello")
    )
    not_mine = other_client.post(f"/api/tenant/leases/{theirs}/documents", json=document())
    no_title = other_client.post(
        f"/api/tenant/leases/{home['lease']}/documents", json=document(title=" ")
    )

    assert not_a_pdf.status_code == 422
    assert not_mine.status_code == 404
    assert no_title.status_code == 422


def test_the_database_holds_a_tenants_document_to_a_tenant_on_that_lease(conn, pmc, home):
    stranger = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Stranger")
    import psycopg

    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute(
            "INSERT INTO trust_documents (pmc_id, kind, lease_id, title, content_type, size,"
            " sha256, uploaded_by_tenant)"
            " VALUES (%s, 'lease_document', %s, 'x', 'application/pdf', 1, %s, %s)",
            (pmc.pmc_id, home["lease"], b"\x00" * 32, stranger),
        )


def test_only_a_tenant_files_their_own(client, conn, app_conn, pmc, home):
    join_as(client, app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id)

    answer = client.post(f"/api/tenant/leases/{home['lease']}/documents", json=document())

    assert answer.status_code == 403
