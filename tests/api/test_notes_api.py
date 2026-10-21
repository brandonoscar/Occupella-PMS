"""Notes through the API (src/pms/api/notes.py, migration 0066): staff write a note about a
property, unit, lease, tenant, owner or vendor of their company, and read a record's notes newest
first with who wrote each. Always the session's PMC."""

from datetime import UTC, datetime

import pytest
from helpers import add_tenant, add_unit, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def records(conn, pmc):
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit 1")
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant A")
    lease = open_lease(conn, pmc.pmc_id, unit, TODAY, None, "1500.00", [tenant])
    return {
        "property": prop,
        "unit": unit,
        "lease": lease,
        "tenant": tenant,
        "owner": pmc.owners[0].owner_id,
        "vendor": pmc.vendor_id,
    }


def test_staff_write_notes_on_any_record_and_read_them_newest_first(client, conn, staff, pmc):
    for about, about_id in records(conn, pmc).items():
        for body in (f"First about the {about}.", f"  Then the {about} again.  "):
            ok(
                client.post(
                    "/api/notes", json={"about": about, "about_id": str(about_id), "body": body}
                ),
                201,
            )
        found = ok(client.get("/api/notes", params={"about": about, "about_id": str(about_id)}))
        assert [(n["body"], n["by"]) for n in found] == [
            (f"Then the {about} again.", "Staff 1"),
            (f"First about the {about}.", "Staff 1"),
        ]
    # Each record has only its own notes.
    prop = pmc.owners[1].property_id
    assert ok(client.get("/api/notes", params={"about": "property", "about_id": str(prop)})) == []


def test_another_companys_record_takes_no_note(client, conn, staff, pmc):
    other = make_pmc(conn)
    theirs = other.owners[0].property_id
    conn.execute(
        "INSERT INTO trust_notes (pmc_id, property_id, body) VALUES (%s, %s, 'Theirs')",
        (other.pmc_id, theirs),
    )
    answer = client.post(
        "/api/notes", json={"about": "property", "about_id": str(theirs), "body": "Mine"}
    )
    assert (answer.status_code, answer.json()["detail"]) == (404, "no such property")
    # Nor are its notes read.
    assert ok(client.get("/api/notes", params={"about": "property", "about_id": str(theirs)})) == []


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_read_or_write_notes(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    prop = str(pmc.owners[0].property_id)
    assert client.get("/api/notes", params={"about": "property", "about_id": prop}).status_code == (
        status
    )
    answer = client.post("/api/notes", json={"about": "property", "about_id": prop, "body": "x"})
    assert answer.status_code == status


NOWHERE = "00000000-0000-0000-0000-000000000000"


@pytest.mark.parametrize(
    "body",
    [
        {"about": "property", "about_id": NOWHERE, "body": " "},
        {"about": "property", "about_id": NOWHERE, "body": "x" * 5001},
        {"about": "property", "about_id": NOWHERE, "body": "gate code\u0000 changed"},
        {"about": "bill", "about_id": NOWHERE, "body": "x"},
        {"about": "property", "about_id": "not-an-id", "body": "x"},
    ],
)
def test_bad_input_is_refused(client, staff, body):
    assert client.post("/api/notes", json=body).status_code == 422


def test_a_lone_surrogate_is_refused_not_a_server_error(client, staff):
    raw = '{"about": "property", "about_id": "' + NOWHERE + '", "body": "\\ud800"}'
    answer = client.post("/api/notes", content=raw, headers={"Content-Type": "application/json"})
    assert answer.status_code == 422


def test_a_record_is_named_to_read_its_notes(client, staff):
    assert client.get("/api/notes").status_code == 422
    assert client.get("/api/notes", params={"about": "bill", "about_id": "x"}).status_code == 422
