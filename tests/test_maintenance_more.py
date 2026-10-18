"""Maintenance (migration 20261018000084; the founder's review, q066, q071 and q075): photos a
tenant files with a request, who is on call for emergencies, the email each of them gets when a
tenant files one, and a scheduled visit's window. The API's side is in
tests/api/test_maintenance_more_api.py. Every name and address here is synthetic."""

from datetime import UTC, date, datetime

import psycopg
import pytest
from helpers import (
    PNG,
    acting_as,
    add_document,
    add_staff,
    add_tenant,
    add_unit,
    add_work_order,
    file_request,
    make_pmc,
    open_lease,
    step_work_order,
)

from pms import notices

NOW = datetime(2026, 10, 18, 9, tzinfo=UTC)


class Home:
    """Unit 1 of Property 1, leased to Tenant A, who files an emergency."""

    def __init__(self, conn):
        self.pmc = make_pmc(conn)
        pmc_id, prop = self.pmc.pmc_id, self.pmc.owners[0].property_id
        self.property_id = prop
        self.unit = add_unit(conn, pmc_id, prop, "Unit 1")
        self.tenant = add_tenant(conn, pmc_id, prop, "Tenant A")
        self.lease = open_lease(
            conn, pmc_id, self.unit, date(2026, 1, 1), None, "1500", [self.tenant]
        )
        self.request = conn.execute(
            "INSERT INTO trust_maintenance_requests"
            " (pmc_id, lease_id, tenant_id, summary, entry_allowed, emergency)"
            " VALUES (%s, %s, %s, 'Pipe burst', false, true) RETURNING id",
            (pmc_id, self.lease, self.tenant),
        ).fetchone()[0]


@pytest.fixture
def home(conn):
    return Home(conn)


def login(conn, pmc_id, staff, address, accepted=True):
    made = conn.execute(
        "INSERT INTO trust_logins (pmc_id, kind, staff_id, email) VALUES (%s, 'staff', %s, %s)"
        " RETURNING id",
        (pmc_id, staff, address),
    ).fetchone()[0]
    if accepted:
        conn.execute(
            "INSERT INTO trust_login_secrets (login_id, pmc_id, password_hash)"
            " VALUES (%s, %s, '$argon2id$synthetic')",
            (made, pmc_id),
        )
    return made


def on_call(conn, pmc_id, staff, on=True):
    conn.execute(
        "INSERT INTO trust_on_call_staff (pmc_id, staff_id, on_call) VALUES (%s, %s, %s)",
        (pmc_id, staff, on),
    )


def test_an_emergency_is_emailed_to_each_staff_member_on_call_with_a_login(conn, app_conn, home):
    pmc_id = home.pmc.pmc_id
    first = add_staff(conn, pmc_id, "Manager A")
    gone = add_staff(conn, pmc_id, "Manager B")
    invited = add_staff(conn, pmc_id, "Manager C")
    resting = add_staff(conn, pmc_id, "Manager D")
    login(conn, pmc_id, first, "manager.a@example.com")
    conn.execute(
        "UPDATE trust_logins SET disabled_at = now() WHERE id = %s",
        (login(conn, pmc_id, gone, "manager.b@example.com"),),
    )
    login(conn, pmc_id, invited, "manager.c@example.com", accepted=False)
    login(conn, pmc_id, resting, "manager.d@example.com")
    for staff in (first, gone, invited, resting):
        on_call(conn, pmc_id, staff)
    on_call(conn, pmc_id, resting, on=False)
    # Someone on call at another company hears nothing of this one.
    other = make_pmc(conn).pmc_id
    elsewhere = add_staff(conn, other, "Manager E")
    login(conn, other, elsewhere, "manager.e@example.com")
    on_call(conn, other, elsewhere)

    # On call: the latest row of each says. Only one of them has a login to email.
    assert set(notices.on_call(app_conn, pmc_id)) == {first, gone, invited}
    assert (
        notices.write_emergency(app_conn, home.pmc.pmc_id, home.request, "https://pms.example") == 1
    )
    assert (
        notices.write_emergency(app_conn, home.pmc.pmc_id, home.request, "https://pms.example") == 0
    )
    (sent,) = conn.execute(
        "SELECT staff_id, request_id, to_address, subject, body FROM trust_emails"
        " WHERE kind = 'emergency_request'"
    ).fetchall()
    assert sent[:4] == (
        first,
        home.request,
        "manager.a@example.com",
        "Emergency at Property 1, Unit 1: Pipe burst",
    )
    assert "Call them before going in." in sent[4]
    assert "https://pms.example/work-orders" in sent[4]


def test_a_request_that_is_no_emergency_is_emailed_to_nobody(conn, app_conn, home):
    pmc_id = home.pmc.pmc_id
    staff = add_staff(conn, pmc_id, "Manager A")
    login(conn, pmc_id, staff, "manager.a@example.com")
    on_call(conn, pmc_id, staff)
    plain = file_request(conn, pmc_id, home.lease, home.tenant, "Squeaky door", True)

    assert notices.write_emergency(app_conn, pmc_id, plain, "https://pms.example") == 0
    # Nor is one of another company's.
    assert notices.write_emergency(app_conn, make_pmc(conn).pmc_id, home.request, "x") == 0


def test_an_emergency_email_names_one_staff_member_and_its_request(conn, home):
    pmc_id = home.pmc.pmc_id
    staff = add_staff(conn, pmc_id, "Manager A")
    columns = "(pmc_id, kind, to_address, subject, body, staff_id, request_id, tenant_id)"
    insert = (
        f"INSERT INTO trust_emails {columns} VALUES (%s, %s, 'a@example.com', 'S', 'B', %s, %s, %s)"
    )
    for kind, who, request, tenant in (
        ("emergency_request", staff, None, None),
        ("emergency_request", None, home.request, None),
        ("emergency_request", staff, home.request, home.tenant),
        ("invite", staff, home.request, None),
    ):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(insert, (pmc_id, kind, who, request, tenant))
    conn.execute(insert, (pmc_id, "emergency_request", staff, home.request, None))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(insert, (pmc_id, "emergency_request", staff, home.request, None))


def test_a_request_photo_is_about_a_request_of_its_company(conn, home):
    pmc_id = home.pmc.pmc_id
    add_document(conn, pmc_id, "request_photo", home.request, PNG, "image/png", "Ceiling")
    insert = (
        "INSERT INTO trust_documents (pmc_id, kind, {}, title, content_type, size, sha256)"
        " VALUES (%s, %s, %s, 'Photo', 'image/png', 1, sha256('x'))"
    )
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(insert.format("lease_id"), (pmc_id, "request_photo", home.lease))
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(insert.format("request_id"), (pmc_id, "move_out_photo", home.request))
    other = make_pmc(conn).pmc_id
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute(insert.format("request_id"), (other, "request_photo", home.request))


def test_who_is_on_call_is_append_only_audited_and_names_who(conn, home):
    pmc_id = home.pmc.pmc_id
    clerk = add_staff(conn, pmc_id, "Clerk A")
    staff = add_staff(conn, pmc_id, "Manager A")
    with acting_as(conn, clerk):
        on_call(conn, pmc_id, staff)
    assert conn.execute(
        "SELECT recorded_by, on_call FROM trust_on_call_staff WHERE staff_id = %s", (staff,)
    ).fetchone() == (clerk, True)
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_on_call_staff'"
        " AND pmc_id = %s",
        (pmc_id,),
    ).fetchone() == (1,)
    for query in (
        "UPDATE trust_on_call_staff SET on_call = false",
        "DELETE FROM trust_on_call_staff",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(query)
    stranger = add_staff(conn, make_pmc(conn).pmc_id, "Manager B")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        on_call(conn, pmc_id, stranger)


def test_a_visit_window_comes_only_with_a_schedule(conn, home):
    pmc_id = home.pmc.pmc_id
    order = add_work_order(conn, pmc_id, home.property_id, home.unit, "Pipe burst", NOW)
    step_work_order(conn, pmc_id, order, "assigned", NOW, home.pmc.vendor_id)
    insert = (
        "INSERT INTO trust_work_order_steps"
        " (pmc_id, work_order_id, step, scheduled_for, visit_window, taken_at)"
        " VALUES (%s, %s, %s, %s, %s, %s)"
    )
    for step, day, window in (
        ("completed", None, "8-10 am"),
        ("scheduled", date(2026, 10, 20), "  "),
        ("scheduled", date(2026, 10, 20), "x" * 61),
    ):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(insert, (pmc_id, order, step, day, window, NOW))
    conn.execute(insert, (pmc_id, order, "scheduled", date(2026, 10, 20), "8-10 am", NOW))
    assert conn.execute(
        "SELECT visit_window FROM trust_work_order_steps WHERE work_order_id = %s"
        " AND step = 'scheduled'",
        (order,),
    ).fetchone() == ("8-10 am",)


def test_a_request_is_no_emergency_unless_the_tenant_says_so(conn, home):
    plain = file_request(conn, home.pmc.pmc_id, home.lease, home.tenant, "Squeaky door")
    assert conn.execute(
        "SELECT emergency FROM trust_maintenance_requests WHERE id = %s", (plain,)
    ).fetchone() == (False,)
