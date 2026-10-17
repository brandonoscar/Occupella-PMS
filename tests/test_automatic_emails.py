"""The emails the PMS writes by itself (migration 20261017000077, src/pms/notices.py,
docs/adr/0007-email.md): the company's settings, then rent reminders, late notices, owner
statements, vendors' work orders and applicants' adverse action notices, each once, each only
when the company turned it on and the server sends email. Every name and address is synthetic."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    add_late_fee_policy,
    add_staff,
    add_tenant,
    add_unit,
    add_work_order,
    contribute,
    hold_payments,
    make_pmc,
    open_application_link,
    open_lease,
    record_adverse_action,
    record_decision,
    record_screening,
    release_hold,
    submit_application,
)

from pms import accounts, nightly, notices

URL = "https://pms.example"
DAY = timedelta(days=1)
OCT_16 = date(2026, 10, 16)
ALL_ON = {
    "rent_reminder_days": 5,
    "late_notices": True,
    "owner_statement_day": 5,
    "work_order_notices": True,
    "adverse_action_notices": True,
}


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def turn_on(conn, pmc_id, by=None, **wanted):
    settings = {**dict.fromkeys(notices.FIELDS), **wanted}
    for flag in ("late_notices", "work_order_notices", "adverse_action_notices"):
        settings[flag] = bool(settings[flag])
    with acting_as(conn, by):
        return conn.execute(
            f"INSERT INTO trust_email_settings (pmc_id, {', '.join(settings)})"
            f" VALUES (%s, {', '.join(['%s'] * len(settings))}) RETURNING id",
            (pmc_id, *settings.values()),
        ).fetchone()[0]


def contact(conn, pmc_id, column, person, address):
    conn.execute(
        f"INSERT INTO trust_contacts (pmc_id, {column}, email) VALUES (%s, %s, %s)",
        (pmc_id, person, address),
    )


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]


def emails(conn, pmc_id, kind):
    return conn.execute(
        "SELECT to_address, subject, body, tenant_id, owner_id, vendor_id, lease_id, charge_id,"
        " period, work_order_step_id, application_id, applicant_id"
        " FROM trust_emails WHERE pmc_id = %s AND kind = %s"
        ' ORDER BY to_address COLLATE "C", created_at',
        (pmc_id, kind),
    ).fetchall()


def lease_of(conn, pmc, starts=date(2026, 9, 1), ends=None, rent="1000.00", tenants=None):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 4")
    return open_lease(conn, pmc.pmc_id, unit, starts, ends, rent, tenants or [tenant_of(conn, pmc)])


# --- the settings -------------------------------------------------------------------------------


def test_the_latest_settings_hold_and_none_turns_everything_off(conn, app_conn, pmc):
    assert notices.settings_of(app_conn, pmc.pmc_id) == notices.Settings()
    turn_on(conn, pmc.pmc_id, **ALL_ON)
    assert notices.settings_of(app_conn, pmc.pmc_id) == notices.Settings(**ALL_ON)
    turn_on(conn, pmc.pmc_id, late_notices=True)
    assert notices.settings_of(app_conn, pmc.pmc_id) == notices.Settings(late_notices=True)
    # Another company's settings are its own.
    assert notices.settings_of(app_conn, make_pmc(conn).pmc_id) == notices.Settings()


def test_settings_are_append_only_name_who_changed_them_and_stay_in_range(conn, pmc):
    admin = add_staff(conn, pmc.pmc_id, "Admin 1")
    made = turn_on(conn, pmc.pmc_id, by=admin, rent_reminder_days=3)
    assert conn.execute(
        "SELECT recorded_by FROM trust_email_settings WHERE id = %s", (made,)
    ).fetchone() == (admin,)
    assert conn.execute(
        "SELECT staff_id FROM trust_audit_log WHERE table_name = 'trust_email_settings'"
        " AND pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall() == [(admin,)]
    for query in (
        "UPDATE trust_email_settings SET late_notices = true",
        "DELETE FROM trust_email_settings",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(query)
    for wanted in (
        {"rent_reminder_days": 0},
        {"rent_reminder_days": 16},
        {"owner_statement_day": 29},
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            turn_on(conn, pmc.pmc_id, **wanted)


# --- the outbox's rules for each kind ---------------------------------------------------------


def outbox(conn, pmc_id, kind, **about):
    columns = ["pmc_id", "kind", "to_address", "subject", "body", *about]
    conn.execute(
        f"INSERT INTO trust_emails ({', '.join(columns)})"
        f" VALUES ({', '.join(['%s'] * len(columns))})",
        (pmc_id, kind, "someone@example.com", "Subject", "Body", *about.values()),
    )


def test_each_automatic_email_names_what_it_is_about_and_nothing_else(conn, pmc):
    tenant, owner = tenant_of(conn, pmc), pmc.owners[0].owner_id
    lease = lease_of(conn, pmc)
    nov = date(2026, 11, 1)
    for kind, about in (
        ("rent_reminder", {"tenant_id": tenant, "period": nov}),  # no lease
        ("rent_reminder", {"tenant_id": tenant, "lease_id": lease}),  # no due day
        ("rent_reminder", {"owner_id": owner, "lease_id": lease, "period": nov}),
        ("late_notice", {"tenant_id": tenant, "lease_id": lease}),  # no late fee
        ("owner_statement", {"owner_id": owner, "period": date(2026, 9, 2)}),  # not a first
        ("owner_statement", {"tenant_id": tenant, "period": nov}),
        ("work_order", {"vendor_id": pmc.vendor_id}),  # no step
        ("adverse_action", {"tenant_id": tenant}),
        ("message", {"tenant_id": tenant, "period": nov}),  # a message is about no month
    ):
        with conn.transaction(), pytest.raises(psycopg.errors.CheckViolation):
            outbox(conn, pmc.pmc_id, kind, **about)
    outbox(conn, pmc.pmc_id, "owner_statement", owner_id=owner, period=date(2026, 9, 1))
    with pytest.raises(psycopg.errors.UniqueViolation):
        outbox(conn, pmc.pmc_id, "owner_statement", owner_id=owner, period=date(2026, 9, 1))


def test_what_an_email_is_about_is_of_its_company(conn, pmc):
    theirs = make_pmc(conn)
    lease = lease_of(conn, theirs)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        outbox(
            conn,
            pmc.pmc_id,
            "rent_reminder",
            tenant_id=tenant_of(conn, pmc),
            lease_id=lease,
            period=date(2026, 11, 1),
        )


# --- rent reminders -------------------------------------------------------------------------------


def test_a_rent_reminder_goes_to_each_tenant_from_the_days_before_rent_is_due(conn, app_conn, pmc):
    turn_on(conn, pmc.pmc_id, rent_reminder_days=5)
    first = tenant_of(conn, pmc)
    second = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 2")
    lease = lease_of(conn, pmc, tenants=[first, second])
    contact(conn, pmc.pmc_id, "tenant_id", first, "tenant.one@example.com")
    contact(conn, pmc.pmc_id, "tenant_id", second, "tenant.two@example.com")

    # November 1 is 6 days after October 26: too soon to remind.
    assert notices.write_rent_reminders(app_conn, pmc.pmc_id, date(2026, 10, 26), 5, URL) == 0
    assert notices.write_rent_reminders(app_conn, pmc.pmc_id, date(2026, 10, 27), 5, URL) == 2
    # Once per tenant, lease and due day, however many runs.
    assert notices.write_rent_reminders(app_conn, pmc.pmc_id, date(2026, 10, 31), 5, URL) == 0

    sent = emails(conn, pmc.pmc_id, "rent_reminder")
    assert [
        (to, subject, t, lease_id, period)
        for to, subject, _, t, _, _, lease_id, _, period, *_ in sent
    ] == [
        (
            "tenant.one@example.com",
            "Rent reminder: due 11/01/2026",
            first,
            lease,
            date(2026, 11, 1),
        ),
        (
            "tenant.two@example.com",
            "Rent reminder: due 11/01/2026",
            second,
            lease,
            date(2026, 11, 1),
        ),
    ]
    body = sent[0][2]
    assert "Hello Tenant 1," in body
    assert "Your rent for Property 1, Unit 4 is due on 11/01/2026: $1,000.00 a month." in body
    assert "Owed today" not in body  # nothing charged yet
    assert "tenant portal" not in body  # no login to sign in with


def test_a_reminder_says_what_is_owed_the_part_month_and_the_portal(conn, app_conn, pmc):
    tenant = tenant_of(conn, pmc)
    lease = lease_of(conn, pmc, ends=date(2026, 11, 15))
    conn.execute(
        "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount)"
        " VALUES (%s, %s, '2026-10-01', 'rent', 1000.00)",
        (pmc.pmc_id, lease),
    )
    invite = accounts.create_invite(conn, pmc.pmc_id, "tenant", tenant, "t.login@example.com", None)
    accounts.accept_invite(conn, invite.token, "a long synthetic passphrase")

    assert notices.write_rent_reminders(app_conn, pmc.pmc_id, date(2026, 10, 30), 5, URL) == 1
    ((to, _, body, *_),) = emails(conn, pmc.pmc_id, "rent_reminder")
    assert to == "t.login@example.com"  # no contact email: the login's
    assert "Your lease ends on 11/15/2026, so November's rent is for its days to then." in body
    assert "Owed today, before it: $1,000.00." in body
    assert f"in the tenant portal:\n{URL}/" in body


def test_no_reminder_without_an_email_a_lease_on_the_due_day_or_under_a_hold(conn, app_conn, pmc):
    day = date(2026, 10, 30)
    tenant = tenant_of(conn, pmc)
    lease_of(conn, pmc)  # Tenant 1: no email on file
    other = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 2")
    contact(conn, pmc.pmc_id, "tenant_id", other, "tenant.two@example.com")
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 5")
    open_lease(conn, pmc.pmc_id, unit, date(2026, 9, 1), date(2026, 10, 31), "900.00", [other])
    assert notices.write_rent_reminders(app_conn, pmc.pmc_id, day, 5, URL) == 0  # ends first

    held = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 6")
    third = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 3")
    contact(conn, pmc.pmc_id, "tenant_id", third, "tenant.three@example.com")
    lease = open_lease(conn, pmc.pmc_id, held, date(2026, 9, 1), None, "900.00", [third])
    hold = hold_payments(conn, pmc.pmc_id, lease, date(2026, 10, 20))
    assert notices.write_rent_reminders(app_conn, pmc.pmc_id, day, 5, URL) == 0
    release_hold(conn, pmc.pmc_id, hold, date(2026, 10, 30))
    assert notices.write_rent_reminders(app_conn, pmc.pmc_id, day, 5, URL) == 1
    assert tenant != third


def test_a_tenant_who_left_the_lease_hears_no_more_of_its_rent(conn, app_conn, pmc):
    turn_on(conn, pmc.pmc_id, rent_reminder_days=5, late_notices=True)
    first = tenant_of(conn, pmc)
    second = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 2")
    lease = lease_of(conn, pmc, tenants=[first, second])
    contact(conn, pmc.pmc_id, "tenant_id", first, "tenant.one@example.com")
    contact(conn, pmc.pmc_id, "tenant_id", second, "tenant.two@example.com")
    # Tenant 1 leaves on October 15; Tenant 2 stays (migration 0080).
    conn.execute(
        "SELECT trust_remove_lease_tenant(%s, %s, %s, %s, NULL)",
        (pmc.pmc_id, lease, first, date(2026, 10, 15)),
    )
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, date(2026, 9, 1), 5, "50.00")
    ran = nightly.run_pmc(app_conn, pmc.pmc_id, OCT_16, URL, sending=True)
    assert ran.late_fees == 1
    assert notices.write_rent_reminders(app_conn, pmc.pmc_id, date(2026, 10, 27), 5, URL) == 1
    for kind in ("late_notice", "rent_reminder"):
        assert [row[3] for row in emails(conn, pmc.pmc_id, kind)] == [second]


# --- late notices ---------------------------------------------------------------------------------


def test_a_late_notice_goes_to_each_tenant_for_the_late_fee_the_run_charged(conn, app_conn, pmc):
    turn_on(conn, pmc.pmc_id, late_notices=True)
    tenant = tenant_of(conn, pmc)
    contact(conn, pmc.pmc_id, "tenant_id", tenant, "tenant.one@example.com")
    lease = lease_of(conn, pmc)
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, date(2026, 9, 1), 5, "50.00")

    first = nightly.run_pmc(app_conn, pmc.pmc_id, OCT_16, URL, sending=True)
    again = nightly.run_pmc(app_conn, pmc.pmc_id, OCT_16 + DAY, URL, sending=True)

    assert (first.late_fees, first.emails, again.emails) == (1, 1, 0)
    ((to, subject, body, t, _, _, lease_id, charge, *_),) = emails(conn, pmc.pmc_id, "late_notice")
    assert (to, subject, t, lease_id) == (
        "tenant.one@example.com",
        "A late fee of $50.00 was charged",
        tenant,
        lease,
    )
    assert conn.execute(
        "SELECT kind, amount, due_on FROM trust_charges WHERE id = %s", (charge,)
    ).fetchone() == ("fee", Decimal("50.00"), date(2026, 10, 7))
    assert (
        "Rent for Property 1, Unit 4 due on 10/01/2026 still had $1,000.00 unpaid after its grace"
        " days, so a late fee of $50.00 was charged on 10/16/2026.\nOwed today: $1,050.00." in body
    )


def test_late_fees_charged_before_notices_were_on_get_none(conn, app_conn, pmc):
    tenant = tenant_of(conn, pmc)
    contact(conn, pmc.pmc_id, "tenant_id", tenant, "tenant.one@example.com")
    lease_of(conn, pmc)
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, date(2026, 9, 1), 5, "50.00")
    assert nightly.run_pmc(app_conn, pmc.pmc_id, OCT_16, URL, sending=True).late_fees == 1
    turn_on(conn, pmc.pmc_id, late_notices=True)
    assert nightly.run_pmc(app_conn, pmc.pmc_id, OCT_16 + DAY, URL, sending=True).emails == 0
    assert emails(conn, pmc.pmc_id, "late_notice") == []


# --- owner statements -----------------------------------------------------------------------------


def test_each_owner_gets_last_months_statement_from_the_day_the_company_picked(conn, app_conn, pmc):
    turn_on(conn, pmc.pmc_id, owner_statement_day=5)
    first, second = pmc.owners
    contact(conn, pmc.pmc_id, "owner_id", first.owner_id, "owner.one@example.com")
    contribute(conn, pmc.pmc_id, first.account, "c-1", "250.00", datetime(2026, 9, 10, tzinfo=UTC))
    contribute(conn, pmc.pmc_id, first.account, "c-2", "40.00", datetime(2026, 10, 2, tzinfo=UTC))

    assert notices.write_owner_statements(app_conn, pmc.pmc_id, date(2026, 10, 4), 5, URL) == 0
    # Owner 2 has no email on file: none.
    assert notices.write_owner_statements(app_conn, pmc.pmc_id, date(2026, 10, 5), 5, URL) == 1
    assert notices.write_owner_statements(app_conn, pmc.pmc_id, date(2026, 10, 20), 5, URL) == 0

    ((to, subject, body, _, owner, _, _, _, period, *_),) = emails(
        conn, pmc.pmc_id, "owner_statement"
    )
    assert (to, subject, owner, period) == (
        "owner.one@example.com",
        "Your statement for September 2026",
        first.owner_id,
        date(2026, 9, 1),
    )
    assert (
        "  Opening balance  $0.00\n  Money in         $250.00\n  Money out        $0.00\n"
        "  Closing balance  $250.00\n" in body
    )
    assert "Ask Synthetic PMC" in body  # no portal login
    # November's run sends October's, contribution and all.
    contact(conn, pmc.pmc_id, "owner_id", second.owner_id, "owner.two@example.com")
    assert notices.write_owner_statements(app_conn, pmc.pmc_id, date(2026, 11, 5), 5, URL) == 2
    october = [
        row for row in emails(conn, pmc.pmc_id, "owner_statement") if row[8] == date(2026, 10, 1)
    ]
    assert "Closing balance  $290.00" in october[0][2]


def test_an_archived_owner_gets_no_statement(conn, app_conn, pmc):
    owner = pmc.owners[1].owner_id
    contact(conn, pmc.pmc_id, "owner_id", owner, "owner.two@example.com")
    conn.execute(
        "INSERT INTO trust_archivals (pmc_id, owner_id, archived) VALUES (%s, %s, true)",
        (pmc.pmc_id, owner),
    )
    assert notices.write_owner_statements(app_conn, pmc.pmc_id, date(2026, 10, 5), 1, URL) == 0


# --- the nightly run --------------------------------------------------------------------------


def test_the_night_writes_only_what_is_on_and_only_while_the_server_sends_email(
    conn, app_conn, pmc
):
    tenant = tenant_of(conn, pmc)
    contact(conn, pmc.pmc_id, "tenant_id", tenant, "tenant.one@example.com")
    contact(conn, pmc.pmc_id, "owner_id", pmc.owners[0].owner_id, "owner.one@example.com")
    lease_of(conn, pmc)
    day = date(2026, 10, 28)
    assert nightly.run_pmc(app_conn, pmc.pmc_id, day, URL, sending=True).emails == 0  # all off
    turn_on(conn, pmc.pmc_id, **ALL_ON)
    assert nightly.run_pmc(app_conn, pmc.pmc_id, day, URL).emails == 0  # sends no email
    assert conn.execute(
        "SELECT count(*) FROM trust_emails WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)
    assert nightly.run_pmc(app_conn, pmc.pmc_id, day, URL, sending=True).emails == 2
    assert conn.execute(
        'SELECT kind FROM trust_emails WHERE pmc_id = %s ORDER BY kind COLLATE "C"', (pmc.pmc_id,)
    ).fetchall() == [("owner_statement",), ("rent_reminder",)]


# --- a vendor's work order ------------------------------------------------------------------------


def step(conn, pmc_id, work_order, kind, vendor=None, on=None, note=None):
    return conn.execute(
        "INSERT INTO trust_work_order_steps"
        " (pmc_id, work_order_id, step, vendor_id, scheduled_for, taken_at, note)"
        " VALUES (%s, %s, %s, %s, %s, now(), %s) RETURNING id",
        (pmc_id, work_order, kind, vendor, on, note),
    ).fetchone()[0]


def test_a_vendor_hears_of_an_assignment_and_a_schedule_once_each(conn, app_conn, pmc):
    turn_on(conn, pmc.pmc_id, work_order_notices=True)
    contact(conn, pmc.pmc_id, "vendor_id", pmc.vendor_id, "vendor.one@example.com")
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 4")
    work_order = add_work_order(
        conn, pmc.pmc_id, pmc.owners[0].property_id, unit, "Leaking tap", datetime.now(UTC)
    )
    conn.execute(
        "INSERT INTO trust_work_order_details"
        " (pmc_id, work_order_id, priority, description, due_on, entry)"
        " VALUES (%s, %s, 'high', 'Kitchen tap drips.', '2026-10-20', 'call first')",
        (pmc.pmc_id, work_order),
    )
    assigned = step(conn, pmc.pmc_id, work_order, "assigned", vendor=pmc.vendor_id)
    scheduled = step(conn, pmc.pmc_id, work_order, "scheduled", on=date(2026, 10, 19), note="AM")
    completed = step(conn, pmc.pmc_id, work_order, "completed")

    assert notices.write_work_order(app_conn, pmc.pmc_id, assigned) == "written"
    assert notices.write_work_order(app_conn, pmc.pmc_id, scheduled) == "written"
    assert notices.write_work_order(app_conn, pmc.pmc_id, completed) == "not this step"
    assert notices.write_work_order(app_conn, pmc.pmc_id, assigned) == "written"  # nothing new
    sent = emails(conn, pmc.pmc_id, "work_order")
    assert [(subject, vendor, s) for _, subject, _, _, _, vendor, _, _, _, s, *_ in sent] == [
        ("Work order #1 at Property 1, Unit 4: Leaking tap", pmc.vendor_id, assigned),
        ("Work order #1 scheduled for 10/19/2026: Leaking tap", pmc.vendor_id, scheduled),
    ]
    body = sent[1][2]
    assert "scheduled your visit for work order #1 on 10/19/2026." in body
    for line in (
        "Where: Property 1, Unit 4",
        "Details: Kitchen tap drips.",
        "Priority: high",
        "Due by: 10/20/2026",
        "Call Synthetic PMC",
        "Note: AM",
    ):
        assert line in body


def test_no_work_order_email_when_off_or_with_no_email_on_file(conn, app_conn, pmc):
    work_order = add_work_order(
        conn, pmc.pmc_id, pmc.owners[0].property_id, None, "Gate stuck", datetime.now(UTC)
    )
    assigned = step(conn, pmc.pmc_id, work_order, "assigned", vendor=pmc.vendor_id)
    assert notices.write_work_order(app_conn, pmc.pmc_id, assigned) == "off"
    turn_on(conn, pmc.pmc_id, work_order_notices=True)
    assert notices.write_work_order(app_conn, pmc.pmc_id, assigned) == "no email on file"
    # A step of another company's work order is not found in this one.
    other = make_pmc(conn).pmc_id
    turn_on(conn, other, work_order_notices=True)
    with pytest.raises(LookupError):
        notices.write_work_order(app_conn, other, assigned)


# --- an applicant's adverse action notice ----------------------------------------------------


def declined(conn, pmc, staff):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 9")
    link = open_application_link(conn, pmc.pmc_id, unit, staff)
    application = submit_application(
        conn,
        pmc.pmc_id,
        link,
        applicants=(("Applicant 1", "applicant.one@example.com"), ("Applicant 2", None)),
    )
    screening = record_screening(conn, pmc.pmc_id, application, staff, "decline", ["Income"])
    with conn.transaction():
        record_decision(conn, pmc.pmc_id, application, staff, "declined", screening)
        record_adverse_action(conn, pmc.pmc_id, application, staff)
    return application


def test_each_applicant_gets_the_adverse_action_notice_once(conn, app_conn, pmc):
    staff = add_staff(conn, pmc.pmc_id)
    application = declined(conn, pmc, staff)
    assert notices.write_adverse_action(app_conn, pmc.pmc_id, application) == 0  # off
    turn_on(conn, pmc.pmc_id, adverse_action_notices=True)
    assert notices.write_adverse_action(app_conn, pmc.pmc_id, application) == 2
    assert notices.write_adverse_action(app_conn, pmc.pmc_id, application) == 0

    sent = emails(conn, pmc.pmc_id, "adverse_action")
    assert [(to, subject, a) for to, subject, *_, a, _ in sent] == [
        (
            "applicant.one@example.com",
            "About your rental application: Property 1, Unit 9",
            application,
        ),
        (sent[1][0], "About your rental application: Property 1, Unit 9", application),
    ]
    body = sent[0][2]
    for line in (
        "Hello Applicant 1,",
        "Your application was declined.",
        "- Income too low",
        "a report from Test agency (synthetic).",
        "did not make this decision and can't say why it was made.",
        "a free copy of the report within 60 days",
        "dispute with Test agency (synthetic)",
        "Notice given on 10/11/2026.",
    ):
        assert line in body


def test_an_application_with_no_notice_gets_no_email(conn, app_conn, pmc):
    staff = add_staff(conn, pmc.pmc_id)
    turn_on(conn, pmc.pmc_id, adverse_action_notices=True)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name="Unit 9")
    application = submit_application(
        conn, pmc.pmc_id, open_application_link(conn, pmc.pmc_id, unit, staff)
    )
    record_decision(conn, pmc.pmc_id, application, staff, "approved")
    assert notices.write_adverse_action(app_conn, pmc.pmc_id, application) == 0


def test_the_notice_text_says_the_conditions_of_a_conditional_approval():
    text = notices.notice_text(
        "conditional", "A co-signer", ["Short credit history"], "Agency 1", date(2026, 10, 2)
    )
    assert text.startswith("Your application was approved on conditions: A co-signer\n")
    assert "Notice given on 10/02/2026." in text
