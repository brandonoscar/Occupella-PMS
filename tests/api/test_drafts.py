"""Drafts through the API (ADR 0011, migration 20261105000300, src/pms/api/drafts.py): software with
an API key proposes an email, a notice, a work order opened, assigned or scheduled, or a note, by
the ids of the company's records; staff see what it would do and to whom, then approve it (maybe
edited first) or decline it saying why; the PMS does an approved one through the write path staff
use by hand, as the staff member who approved it and with their roles. Nothing moves money. Every
name here is synthetic."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from helpers import add_tenant, add_unit, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of

from pms.api import create_app, drafts

TODAY = datetime.now(UTC).date()
NOT_VALID = "this key was taken back or doesn't exist"


@pytest.fixture
def admin(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Admin 1")


@pytest.fixture
def software(settings):
    """Software calling the API: no browser, no cookie, only the key it is given."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def key(client, admin):
    made = ok(client.post("/api/ai/keys", json={"name": "Assistant"}), 201)
    return {"Authorization": f"Bearer {made['secret']}"}


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, says=None):
    assert answer.status_code == status, answer.text
    if says is not None:
        assert says in str(answer.json()["detail"]), answer.text


def propose(software, key, proposal, why=None, status=201):
    body = {"proposal": proposal, **({"why": why} if why else {})}
    answer = software.post("/api/ai/drafts", json=body, headers=key)
    return ok(answer, status) if status < 300 else answer


def email_to(*people, subject="Water shutoff", body="Water is off from 9 to 11 tomorrow."):
    return {
        "kind": "email",
        "to": [{"about": about, "id": str(i)} for about, i in people],
        "subject": subject,
        "body": body,
    }


def contact(client, kind, person, address):
    ok(client.post(f"/api/{kind}s/{person}/contact", json={"email": address}), 204)


def rows(conn, sql, *args):
    return conn.execute(sql, args).fetchall()


def ledger(conn):
    """Everything that moves or owes money, counted: none of it changes for a draft."""
    return conn.execute(
        "SELECT (SELECT count(*) FROM pgledger_transfers), (SELECT count(*) FROM trust_charges),"
        " (SELECT count(*) FROM trust_bills), (SELECT count(*) FROM trust_owner_draws)"
    ).fetchone()


# --- an email -----------------------------------------------------------------------------------


def test_staff_see_who_a_proposed_email_goes_to_and_approving_it_writes_it_as_them(
    client, software, conn, pmc, admin, key
):
    tenant = tenant_of(conn, pmc)
    owner = pmc.owners[0].owner_id
    contact(client, "tenant", tenant, "tenant.one@example.com")
    made = propose(
        software,
        key,
        email_to(("tenant", tenant), ("owner", owner)),
        why="The water company posted a shutoff for tomorrow.",
    )

    # The software reads what became of it: waiting.
    (mine,) = ok(software.get("/api/ai/drafts", headers=key))["items"]
    assert (mine["id"], mine["kind"], mine["status"]) == (made["id"], "email", "waiting")

    # Staff see it in words, from the key that proposed it, and to whom it goes today.
    tenant_name = conn.execute(
        "SELECT display_name FROM trust_tenants WHERE id = %s", (tenant,)
    ).fetchone()[0]
    (row,) = ok(client.get("/api/drafts"))["items"]
    assert row["what"] == f"Email to {tenant_name} and 1 other: Water shutoff"
    assert (row["key"], row["why"], row["status"]) == (
        "Assistant",
        "The water company posted a shutoff for tomorrow.",
        "waiting",
    )
    shown = ok(client.get(f"/api/drafts/{made['id']}"))
    assert [(n["about"], n["to"]) for n in shown["names"]] == [
        ("tenant", "tenant.one@example.com"),
        ("owner", None),  # no email on file: they get none
    ]
    assert [d["id"] for d in ok(client.get("/api/today"))["drafts_waiting"]] == [made["id"]]

    before = ledger(conn)
    done = ok(client.post(f"/api/drafts/{made['id']}/approve", json={}))
    assert (done["status"], done["edited"], done["decided_by"]) == ("approved", False, "Admin 1")
    assert done["result"]["without_email"] == [{"about": "owner", "id": str(owner)}]
    (sent,) = rows(
        conn,
        "SELECT to_address, subject, sent_by, tenant_id FROM trust_emails WHERE id = %s",
        done["result"]["emails"][0],
    )
    assert sent == ("tenant.one@example.com", "Water shutoff", admin.person_id, tenant)
    assert ledger(conn) == before
    assert ok(client.get("/api/drafts"))["items"] == []
    assert ok(client.get("/api/today"))["drafts_waiting"] == []

    (mine,) = ok(software.get("/api/ai/drafts", headers=key))["items"]
    assert (mine["status"], mine["edited"], mine["reason"]) == ("approved", False, None)
    # Decided once.
    refused(client.post(f"/api/drafts/{made['id']}/approve", json={}), 409, "approved already")
    refused(
        client.post(f"/api/drafts/{made['id']}/decline", json={"reason": "No"}),
        409,
        "approved already",
    )


def test_an_email_no_one_named_can_get_is_refused_and_the_draft_waits(
    client, software, conn, pmc, admin, key
):
    made = propose(software, key, email_to(("owner", pmc.owners[0].owner_id)))
    refused(
        client.post(f"/api/drafts/{made['id']}/approve", json={}),
        422,
        "nobody it names has an email on file",
    )
    assert ok(client.get(f"/api/drafts/{made['id']}"))["status"] == "waiting"
    assert rows(conn, "SELECT 1 FROM trust_draft_decisions WHERE draft_id = %s", made["id"]) == []


# --- edited, declined -----------------------------------------------------------------------------


def test_staff_change_a_draft_before_approving_it_and_it_is_done_as_changed(
    client, software, conn, pmc, admin, key
):
    tenant = tenant_of(conn, pmc)
    note = {
        "kind": "note",
        "about": "tenant",
        "about_id": str(tenant),
        "body": "Called about rent.",
    }
    made = propose(software, key, note)

    changed = {**note, "body": "Called about rent; pays Friday."}
    done = ok(client.post(f"/api/drafts/{made['id']}/approve", json={"proposal": changed}))
    assert (done["status"], done["edited"]) == ("approved", True)
    assert done["proposal"]["body"] == "Called about rent."  # what the software proposed
    assert done["acted"]["body"] == "Called about rent; pays Friday."  # what was done
    (body, by) = rows(
        conn, "SELECT body, recorded_by FROM trust_notes WHERE id = %s", done["result"]["note"]
    )[0]
    assert (body, by) == ("Called about rent; pays Friday.", admin.person_id)
    # The software learns it was changed, never what to.
    (mine,) = ok(software.get("/api/ai/drafts", headers=key))["items"]
    assert (mine["status"], mine["edited"]) == ("approved", True)
    assert "acted" not in mine and "Friday" not in str(mine)


def test_an_edit_keeps_the_drafts_kind_and_names_only_the_companys_records(
    client, software, conn, pmc, admin, key
):
    tenant = tenant_of(conn, pmc)
    made = propose(
        software, key, {"kind": "note", "about": "tenant", "about_id": str(tenant), "body": "Hi"}
    )
    refused(
        client.post(
            f"/api/drafts/{made['id']}/approve",
            json={"proposal": email_to(("tenant", tenant))},
        ),
        422,
        "an edit keeps its kind",
    )
    elsewhere = make_pmc(conn)
    theirs = add_tenant(conn, elsewhere.pmc_id, elsewhere.owners[0].property_id, "Other Tenant 1")
    refused(
        client.post(
            f"/api/drafts/{made['id']}/approve",
            json={
                "proposal": {
                    "kind": "note",
                    "about": "tenant",
                    "about_id": str(theirs),
                    "body": "Hi",
                }
            },
        ),
        422,
        "no such tenant in this company",
    )
    assert ok(client.get(f"/api/drafts/{made['id']}"))["status"] == "waiting"


def test_a_declined_draft_does_nothing_and_the_software_reads_why(
    client, software, conn, pmc, admin, key
):
    opened = {
        "kind": "work_order_open",
        "property_id": str(pmc.owners[0].property_id),
        "summary": "Gutters need cleaning",
    }
    made = propose(software, key, opened)
    refused(client.post(f"/api/drafts/{made['id']}/decline", json={"reason": "  "}), 422)

    done = ok(client.post(f"/api/drafts/{made['id']}/decline", json={"reason": "Done last week."}))
    assert (done["status"], done["reason"], done["decided_by"]) == (
        "declined",
        "Done last week.",
        "Admin 1",
    )
    assert rows(conn, "SELECT 1 FROM trust_work_orders WHERE pmc_id = %s", pmc.pmc_id) == []
    (mine,) = ok(software.get("/api/ai/drafts", headers=key))["items"]
    assert (mine["status"], mine["reason"], mine["edited"]) == (
        "declined",
        "Done last week.",
        None,
    )
    refused(client.post(f"/api/drafts/{made['id']}/approve", json={}), 409, "declined already")
    decided = ok(client.get("/api/drafts", params={"show": "decided"}))["items"]
    assert [d["id"] for d in decided] == [made["id"]]


# --- work orders, notices -----------------------------------------------------------------------


def test_work_order_drafts_open_assign_and_schedule_by_the_work_orders_own_rules(
    client, software, conn, pmc, admin, key
):
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit 4")
    opened = propose(
        software,
        key,
        {
            "kind": "work_order_open",
            "property_id": str(prop),
            "unit_id": str(unit),
            "summary": "Leaking faucet",
            "priority": "high",
            "description": "Kitchen faucet drips.",
        },
    )
    assert ok(client.get(f"/api/drafts/{opened['id']}"))["what"].endswith(
        ", Unit 4: Leaking faucet"
    )
    work_order = ok(client.post(f"/api/drafts/{opened['id']}/approve", json={}))["result"][
        "work_order"
    ]
    shown = ok(client.get(f"/api/work-orders/{work_order}"))
    assert (shown["summary"], shown["priority"]) == ("Leaking faucet", "high")

    # A visit before a vendor is refused by the work order's own rules, and the draft waits.
    visit = {
        "kind": "work_order_schedule",
        "work_order_id": work_order,
        "scheduled_for": str(TODAY + timedelta(days=3)),
        "visit_window": "8-10 am",
    }
    early = propose(software, key, visit)
    refused(client.post(f"/api/drafts/{early['id']}/approve", json={}), 409)
    assert ok(client.get(f"/api/drafts/{early['id']}"))["status"] == "waiting"

    assigned = propose(
        software,
        key,
        {"kind": "work_order_assign", "work_order_id": work_order, "vendor_id": str(pmc.vendor_id)},
    )
    ok(client.post(f"/api/drafts/{assigned['id']}/approve", json={}))
    later = str(TODAY + timedelta(days=4))
    done = ok(
        client.post(
            f"/api/drafts/{early['id']}/approve",
            json={"proposal": {**visit, "scheduled_for": later}},
        )
    )
    assert done["edited"] is True
    steps = rows(
        conn,
        "SELECT step, vendor_id, scheduled_for, visit_window FROM trust_work_order_steps"
        " WHERE work_order_id = %s ORDER BY taken_at, id",
        work_order,
    )
    assert steps == [
        ("assigned", pmc.vendor_id, None, None),
        ("scheduled", None, date.fromisoformat(later), "8-10 am"),
    ]


def test_a_notice_draft_makes_the_notice_for_each_lease_from_the_companys_template(
    client, software, conn, pmc, admin, key
):
    prop = pmc.owners[0].property_id
    leases = []
    for n in (1, 2):
        unit = add_unit(conn, pmc.pmc_id, prop, f"Unit {n}")
        tenant = add_tenant(conn, pmc.pmc_id, prop, f"Notice Tenant {n}")
        leases.append(
            open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "900.00", [tenant])
        )
    template = ok(
        client.post(
            "/api/notices/templates",
            json={"name": "Entry notice", "body": "Dear {{tenant_names}}, we enter on Monday."},
        ),
        201,
    )
    made = propose(
        software,
        key,
        {
            "kind": "notice",
            "template_id": template["id"],
            "lease_ids": [str(lease) for lease in leases],
            "dated_on": str(TODAY),
        },
    )
    shown = ok(client.get(f"/api/drafts/{made['id']}"))
    assert shown["what"].startswith("Notice from Entry notice for ") and shown["what"].endswith(
        "and 1 other"
    )
    done = ok(client.post(f"/api/drafts/{made['id']}/approve", json={}))
    made_notices = rows(
        conn,
        "SELECT lease_id, body FROM trust_notices WHERE id = ANY(%s::uuid[]) ORDER BY body",
        done["result"]["notices"],
    )
    assert [(lease, body) for lease, body in made_notices] == [
        (leases[0], "Dear Notice Tenant 1, we enter on Monday."),
        (leases[1], "Dear Notice Tenant 2, we enter on Monday."),
    ]


# --- who decides ----------------------------------------------------------------------------------


def test_approving_takes_the_area_of_what_the_draft_does_and_declining_takes_none(
    client, other_client, software, conn, app_conn, pmc, admin, key
):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1")
    role = ok(
        client.post("/api/custom-roles", json={"name": "Repairs", "areas": ["maintenance"]}), 201
    )
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )
    tenant = tenant_of(conn, pmc)
    contact(client, "tenant", tenant, "tenant.one@example.com")
    emailed = propose(software, key, email_to(("tenant", tenant)))
    noted = propose(
        software, key, {"kind": "note", "about": "tenant", "about_id": str(tenant), "body": "Hi"}
    )
    opened = propose(
        software,
        key,
        {
            "kind": "work_order_open",
            "property_id": str(pmc.owners[0].property_id),
            "summary": "Smoke detector chirps",
        },
    )

    refused(
        other_client.post(f"/api/drafts/{emailed['id']}/approve", json={}),
        403,
        "your roles don't let you change email to tenants, owners and vendors",
    )
    refused(
        other_client.post(f"/api/drafts/{noted['id']}/approve", json={}),
        403,
        "your roles don't let you change documents",
    )
    assert rows(conn, "SELECT 1 FROM trust_emails WHERE tenant_id = %s", tenant) == []
    done = ok(other_client.post(f"/api/drafts/{opened['id']}/approve", json={}))
    assert done["decided_by"] == "Clerk 1"
    ok(other_client.post(f"/api/drafts/{noted['id']}/decline", json={"reason": "Not needed"}))


def test_owners_tenants_and_another_companys_staff_neither_see_nor_decide_a_draft(
    client, other_client, software, conn, app_conn, pmc, admin, key
):
    made = propose(software, key, email_to(("tenant", tenant_of(conn, pmc))))
    elsewhere = make_pmc(conn)
    join_as_staff(other_client, conn, app_conn, elsewhere, name="Other Staff 1")
    refused(other_client.get(f"/api/drafts/{made['id']}"), 404, "no such draft")
    refused(other_client.post(f"/api/drafts/{made['id']}/approve", json={}), 404)
    refused(other_client.post(f"/api/drafts/{made['id']}/decline", json={"reason": "x"}), 404)
    assert ok(other_client.get("/api/drafts"))["items"] == []

    other_client.post("/api/sign-out")
    join_as_tenant(other_client, conn, app_conn, pmc)
    refused(other_client.get("/api/drafts"), 403)
    other_client.post("/api/sign-out")
    join_as_owner(other_client, app_conn, pmc)
    refused(other_client.post(f"/api/drafts/{made['id']}/approve", json={}), 403)
    assert ok(client.get(f"/api/drafts/{made['id']}"))["status"] == "waiting"


# --- the key --------------------------------------------------------------------------------------


def test_only_a_standing_key_proposes_and_a_key_opens_no_staff_page(
    client, software, conn, pmc, admin, key
):
    note = {"kind": "note", "about": "owner", "about_id": str(pmc.owners[0].owner_id), "body": "Hi"}
    refused(software.post("/api/ai/drafts", json={"proposal": note}), 401, NOT_VALID)
    # A staff session is no key.
    refused(client.post("/api/ai/drafts", json={"proposal": note}), 401, NOT_VALID)
    refused(software.get("/api/drafts", headers=key), 401)

    made = propose(software, key, note)
    (listed,) = ok(client.get("/api/ai/keys"))
    ok(client.post(f"/api/ai/keys/{listed['id']}/revoke", json={}), 204)
    refused(software.post("/api/ai/drafts", json={"proposal": note}, headers=key), 401, NOT_VALID)
    refused(software.get("/api/ai/drafts", headers=key), 401, NOT_VALID)
    # What it proposed while it stood still waits for staff.
    assert ok(client.get(f"/api/drafts/{made['id']}"))["status"] == "waiting"


@pytest.mark.parametrize(
    ("proposal", "says"),
    [
        ({"kind": "payment", "lease_id": "x", "amount": "100.00"}, None),  # no kind moves money
        ({"kind": "email", "to": [], "subject": "Hi", "body": "Hi"}, None),
        (
            {
                "kind": "email",
                "to": [{"about": "tenant", "id": "00000000-0000-4000-8000-000000000001"}],
                "subject": "Hi",
                "body": "Hi",
                # A contact detail never rides along in a draft.
                "address": "someone@example.com",
            },
            None,
        ),
        (
            {"kind": "email", "to": [{"about": "staff", "id": "x"}], "subject": "Hi", "body": "Hi"},
            None,
        ),
        ({"kind": "note", "about": "lease", "about_id": "not-a-uuid", "body": "Hi"}, None),
        ({"kind": "note", "about": "tenant", "about_id": None, "body": "  "}, None),
        (
            {
                "kind": "work_order_schedule",
                "work_order_id": "00000000-0000-4000-8000-000000000002",
                "scheduled_for": "2026-13-40",
            },
            None,
        ),
    ],
)
def test_a_malformed_proposal_is_refused(software, key, proposal, says):
    refused(software.post("/api/ai/drafts", json={"proposal": proposal}, headers=key), 422, says)


def test_a_draft_names_only_the_companys_own_records_each_once(software, conn, pmc, key):
    tenant = tenant_of(conn, pmc)
    elsewhere = make_pmc(conn)
    theirs = add_tenant(conn, elsewhere.pmc_id, elsewhere.owners[0].property_id, "Other Tenant 1")
    refused(
        propose(software, key, email_to(("tenant", theirs)), status=422),
        422,
        "no such tenant in this company",
    )
    refused(
        propose(software, key, email_to(("tenant", tenant), ("tenant", tenant)), status=422),
        422,
        "an email names each person once",
    )
    other_unit = add_unit(conn, pmc.pmc_id, pmc.owners[1].property_id, "Unit 9")
    refused(
        propose(
            software,
            key,
            {
                "kind": "work_order_open",
                "property_id": str(pmc.owners[0].property_id),
                "unit_id": str(other_unit),
                "summary": "Leak",
            },
            status=422,
        ),
        422,
        "that unit is not one of the property's",
    )
    refused(
        propose(
            software,
            key,
            {"kind": "note", "about": "tenant", "about_id": str(tenant), "body": "Hi"},
            why="x" * 2001,
            status=422,
        ),
        422,
    )
    assert rows(conn, "SELECT 1 FROM trust_drafts WHERE pmc_id = %s", pmc.pmc_id) == []


def test_drafts_waiting_have_a_limit_so_a_loop_cant_bury_the_inbox(
    client, software, conn, pmc, admin, key, monkeypatch
):
    monkeypatch.setattr(drafts, "MAX_WAITING", 2)
    note = {"kind": "note", "about": "owner", "about_id": str(pmc.owners[0].owner_id), "body": "Hi"}
    first = propose(software, key, note)
    propose(software, key, note)
    refused(
        software.post("/api/ai/drafts", json={"proposal": note}, headers=key),
        429,
        "2 drafts already wait for staff",
    )
    ok(client.post(f"/api/drafts/{first['id']}/decline", json={"reason": "Twice"}))
    propose(software, key, note)


def test_a_drafts_proposal_and_decision_are_in_the_audit_log_with_who(
    client, software, conn, pmc, admin, key
):
    tenant = tenant_of(conn, pmc)
    made = propose(
        software, key, {"kind": "note", "about": "tenant", "about_id": str(tenant), "body": "Hi"}
    )
    ok(client.post(f"/api/drafts/{made['id']}/approve", json={}))
    logged = rows(
        conn,
        "SELECT table_name, db_role, staff_id FROM trust_audit_log"
        " WHERE coalesce(row_data ->> 'draft_id', row_data ->> 'id') = %s"
        " AND table_name LIKE 'trust_draft%%' ORDER BY id",
        made["id"],
    )
    assert logged == [
        ("trust_drafts", "trust_ai_agent", None),  # a key names no staff member
        ("trust_draft_decisions", "trust_app", admin.person_id),
        ("trust_draft_actions", "trust_app", admin.person_id),
    ]
