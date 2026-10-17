"""The emails the PMS writes by itself, through the API (src/pms/api/emails.py, src/pms/notices.py,
docs/adr/0007-email.md): the company's settings, and the emails a rent run by hand, a work order's
step and an application's decision write while the server sends email. Every name and address
here is synthetic."""

from datetime import UTC, datetime, timedelta

import pytest
from helpers import (
    add_unit,
    add_work_order,
    make_pmc,
    open_application_link,
    open_lease,
    record_screening,
    submit_application,
)
from people import join_as_owner, join_as_staff, join_as_tenant, tenant_of

from pms import email

TODAY = datetime.now(UTC).date()
OFF = {
    "rent_reminder_days": None,
    "late_notices": False,
    "owner_statement_day": None,
    "work_order_notices": False,
    "adverse_action_notices": False,
}
ON = {
    "rent_reminder_days": 15,
    "late_notices": True,
    "owner_statement_day": 1,
    "work_order_notices": True,
    "adverse_action_notices": True,
}


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Writer 1")


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def kinds(conn, pmc_id):
    return conn.execute(
        "SELECT kind, count(*) FROM trust_emails WHERE pmc_id = %s GROUP BY kind ORDER BY kind",
        (pmc_id,),
    ).fetchall()


# --- the settings -------------------------------------------------------------------------------


def test_staff_turn_emails_on_and_saving_the_same_writes_nothing(client, conn, staff, pmc):
    assert ok(client.get("/api/emails/settings")) == {**OFF, "sending": False}
    client.app.state.email = email.FakeSender()
    assert ok(client.post("/api/emails/settings", json=ON)) == {**ON, "sending": True}
    assert ok(client.post("/api/emails/settings", json=ON)) == {**ON, "sending": True}
    assert ok(client.get("/api/emails/settings")) == {**ON, "sending": True}
    assert conn.execute(
        "SELECT count(*), min(recorded_by::text) FROM trust_email_settings WHERE pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchone() == (1, str(staff.person_id))
    # Turned off again: a row of its own.
    assert ok(client.post("/api/emails/settings", json={})) == {**OFF, "sending": True}
    # Another company's settings are its own.
    assert conn.execute(
        "SELECT count(*) FROM trust_email_settings WHERE pmc_id <> %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_read_or_change_the_settings(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    assert client.get("/api/emails/settings").status_code == status
    assert client.post("/api/emails/settings", json=ON).status_code == status


def test_a_custom_role_without_email_may_not_change_the_settings(
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
    refused = other_client.post("/api/emails/settings", json=ON)
    assert refused.status_code == 403 and "email to tenants" in refused.json()["detail"]
    assert other_client.get("/api/emails/settings").status_code == 200  # reads stay open


@pytest.mark.parametrize(
    "body",
    [
        {"rent_reminder_days": 0},
        {"rent_reminder_days": 16},
        {"owner_statement_day": 29},
        {"owner_statement_day": "the fifth"},
        {"late_notices": "maybe"},
    ],
)
def test_bad_settings_are_refused(client, staff, body):
    assert client.post("/api/emails/settings", json=body).status_code == 422


# --- what a run by hand writes ------------------------------------------------------------------


def test_a_rent_run_by_hand_writes_the_emails_due_today_while_the_server_sends_email(
    client, conn, staff, pmc
):
    tenant = tenant_of(conn, pmc)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    open_lease(conn, pmc.pmc_id, unit, TODAY.replace(day=1), None, "1000.00", [tenant])
    ok(client.post(f"/api/tenants/{tenant}/contact", json={"email": "t@example.com"}), 204)
    ok(
        client.post(
            f"/api/owners/{pmc.owners[0].owner_id}/contact", json={"email": "o@example.com"}
        ),
        204,
    )
    ok(client.post("/api/emails/settings", json=ON))
    run = {"due_on": str(TODAY.replace(day=1)), "late_fees_as_of": str(TODAY)}

    assert ok(client.post("/api/rent/run", json=run))["emails"] == 0  # the server sends none
    client.app.state.email = email.FakeSender()
    done = ok(client.post("/api/rent/run", json=run))
    # Last month's statement (from the first, as set) and, within 15 days of the next first,
    # the rent reminder.
    reminded = (TODAY.replace(day=28) + timedelta(days=4)).replace(day=1) - TODAY <= timedelta(15)
    assert done["emails"] == 1 + reminded
    assert ok(client.post("/api/rent/run", json=run))["emails"] == 0  # each once
    tab = ok(
        client.get(
            "/api/emails", params={"about": "owner", "about_id": str(pmc.owners[0].owner_id)}
        )
    )
    assert [(line["kind"], line["to"], line["by"]) for line in tab] == [
        ("owner_statement", "o@example.com", None)
    ]


# --- a work order's step ------------------------------------------------------------------------


def step(client, work_order, **body):
    return client.post(f"/api/maintenance/work-orders/{work_order}/steps", json=body)


def test_assigning_and_scheduling_a_work_order_emails_its_vendor(client, conn, staff, pmc):
    vendor = str(pmc.vendor_id)
    ok(client.post(f"/api/vendors/{vendor}/contact", json={"email": "v@example.com"}), 204)
    work_order = add_work_order(
        conn, pmc.pmc_id, pmc.owners[0].property_id, None, "Gate stuck", datetime.now(UTC)
    )
    client.app.state.email = email.FakeSender()
    ok(step(client, work_order, step="assigned", vendor_id=vendor), 201)
    assert kinds(conn, pmc.pmc_id) == []  # not turned on
    ok(client.post("/api/emails/settings", json=ON))
    ok(step(client, work_order, step="scheduled", scheduled_for=str(TODAY + timedelta(2))), 201)
    ok(step(client, work_order, step="completed"), 201)

    tab = ok(client.get("/api/emails", params={"about": "vendor", "about_id": vendor}))
    assert [(line["kind"], line["to"], line["status"]) for line in tab] == [
        ("work_order", "v@example.com", "sent")
    ]
    assert tab[0]["subject"].startswith("Work order #1 scheduled for ")


# --- an application's decision ------------------------------------------------------------------


def screened(conn, pmc, staff):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 9")
    application = submit_application(
        conn,
        pmc.pmc_id,
        open_application_link(conn, pmc.pmc_id, unit, staff.person_id),
        applicants=(("Applicant 1", "applicant.one@example.com"),),
    )
    screening = record_screening(
        conn, pmc.pmc_id, application, staff.person_id, "decline", ["Income"]
    )
    return application, screening


def decline(client, application, screening):
    return client.post(
        f"/api/leasing/applications/{application}/decide",
        json={
            "decision": "declined",
            "screening_id": str(screening),
            "adverse_action": {"reasons": ["Income too low"], "notice_given_on": str(TODAY)},
        },
    )


def test_a_decline_on_a_screening_emails_each_applicant_the_notice(client, conn, staff, pmc):
    client.app.state.email = email.FakeSender()
    ok(client.post("/api/emails/settings", json=ON))
    application, screening = screened(conn, pmc, staff)
    ok(decline(client, application, screening), 201)

    tab = ok(
        client.get("/api/emails", params={"about": "application", "about_id": str(application)})
    )
    assert [(line["kind"], line["to"], line["status"]) for line in tab] == [
        ("adverse_action", "applicant.one@example.com", "sent")
    ]
    assert "Your application was declined." in tab[0]["body"]
    # An application's list is its own: another application's or a person's shows none of it.
    other, _ = screened(conn, pmc, staff)
    assert (
        ok(client.get("/api/emails", params={"about": "application", "about_id": str(other)})) == []
    )


def test_no_notice_is_emailed_while_the_server_sends_no_email(client, conn, staff, pmc):
    ok(client.post("/api/emails/settings", json=ON))
    application, screening = screened(conn, pmc, staff)
    ok(decline(client, application, screening), 201)
    assert kinds(conn, pmc.pmc_id) == []


def test_another_companys_application_lists_nothing(
    client, other_client, conn, app_conn, staff, pmc
):
    other = make_pmc(conn)
    theirs = join_as_staff(other_client, conn, app_conn, other, name="Other 1")
    application, _ = screened(conn, other, theirs)
    conn.execute(
        "INSERT INTO trust_emails (pmc_id, kind, application_id, applicant_id, to_address,"
        " subject, body) SELECT pmc_id, 'adverse_action', application_id, id, email, 's', 'b'"
        " FROM trust_applicants WHERE application_id = %s",
        (application,),
    )
    assert (
        ok(client.get("/api/emails", params={"about": "application", "about_id": str(application)}))
        == []
    )
