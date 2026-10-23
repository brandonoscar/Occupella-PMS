"""Announcements and staff to-dos in the database (migration 20261023000203): an announcement
names its audience, where (a property, a group or the whole company) and who sent it, and each
email of it is that staff member's message to a tenant or an owner, once per address; a to-do is
assigned from the start, its latest assignment holds, and once done it takes no other. All
append-only and audited; the AI role reads none of it. The API's side is in
tests/api/test_announcements_api.py and tests/api/test_todos_api.py. Everything here is
synthetic."""

from datetime import date

import psycopg
import pytest
from helpers import add_staff, add_tenant, make_pmc

DAY = date(2026, 10, 23)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def staff(conn, pmc):
    return add_staff(conn, pmc.pmc_id)


def announce(conn, pmc, staff, **where):
    values = {"pmc_id": pmc.pmc_id, "audience": "tenants", "subject": "Water off",
              "body": "Tuesday 9 to 11.", "sent_by": staff} | where  # fmt: skip
    return conn.execute(
        f"INSERT INTO trust_announcements ({', '.join(values)})"  # noqa: S608
        f" VALUES ({', '.join(['%s'] * len(values))}) RETURNING id",
        tuple(values.values()),
    ).fetchone()[0]


def email_of(conn, pmc, staff, announcement, to="reese@example.com", **who):
    values = {
        "pmc_id": pmc.pmc_id,
        "kind": "message",
        "to_address": to,
        "subject": "Water off",
        "body": "Tuesday.",
        "sent_by": staff,
        "announcement_id": announcement,
    } | who
    conn.execute(
        f"INSERT INTO trust_emails ({', '.join(values)})"  # noqa: S608
        f" VALUES ({', '.join(['%s'] * len(values))})",
        tuple(values.values()),
    )


def test_an_announcement_goes_to_one_place_naming_who_sent_it(conn, pmc, staff):
    prop = pmc.owners[0].property_id
    group = conn.execute(
        "INSERT INTO trust_property_groups (pmc_id, display_name) VALUES (%s, 'North')"
        " RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    announce(conn, pmc, staff)  # the whole company
    announce(conn, pmc, staff, property_id=prop)
    announce(conn, pmc, staff, group_id=group, audience="owners")
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        announce(conn, pmc, staff, property_id=prop, group_id=group)
    for bad in ({"audience": "vendors"}, {"subject": "Two\nlines"}, {"body": ""}):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            announce(conn, pmc, staff, **bad)
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        announce(conn, pmc, add_staff(conn, make_pmc(conn).pmc_id))  # another company's staff
    with pytest.raises(psycopg.errors.NotNullViolation), conn.transaction():
        announce(conn, pmc, None)


def test_each_email_of_it_is_a_message_to_a_tenant_or_owner_once_per_address(conn, pmc, staff):
    tenant = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Reese Sample")
    made = announce(conn, pmc, staff)
    email_of(conn, pmc, staff, made, tenant_id=tenant)
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        email_of(conn, pmc, staff, made, to="REESE@example.com", owner_id=pmc.owners[0].owner_id)
    email_of(conn, pmc, staff, made, to="owner@example.com", owner_id=pmc.owners[0].owner_id)
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        email_of(conn, pmc, staff, made, to="v@example.com", kind="rent_reminder", tenant_id=tenant)
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        email_of(conn, pmc, staff, made, to="x@example.com")  # to no one
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        email_of(conn, pmc, None, made, to="y@example.com", tenant_id=tenant)  # by no one


def todo(conn, pmc, staff, assigned=None, due=DAY):
    with conn.transaction():
        made = conn.execute(
            "INSERT INTO trust_todos (pmc_id, title, recorded_by)"
            " VALUES (%s, 'Call the roofer', %s) RETURNING id",
            (pmc.pmc_id, staff),
        ).fetchone()[0]
        if assigned is not False:
            assign(conn, pmc, made, assigned or staff, due)
    return made


def assign(conn, pmc, made, to, due=DAY):
    conn.execute(
        "INSERT INTO trust_todo_assignments (pmc_id, todo_id, assigned_to, due_on)"
        " VALUES (%s, %s, %s, %s)",
        (pmc.pmc_id, made, to, due),
    )


def finish(conn, pmc, made, by):
    conn.execute(
        "INSERT INTO trust_todo_completions (todo_id, pmc_id, done_by) VALUES (%s, %s, %s)",
        (made, pmc.pmc_id, by),
    )


def test_a_todo_is_assigned_from_the_start(conn, pmc, staff):
    with pytest.raises(psycopg.errors.CheckViolation, match="assigned to someone"):
        todo(conn, pmc, staff, assigned=False)
    made = todo(conn, pmc, staff)
    assert conn.execute(
        "SELECT assigned_to, due_on FROM trust_todo_assignments WHERE todo_id = %s", (made,)
    ).fetchall() == [(staff, DAY)]
    for bad in ("", " padded", "two\nlines"):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO trust_todos (pmc_id, title, recorded_by) VALUES (%s, %s, %s)",
                (pmc.pmc_id, bad, staff),
            )


def test_a_todo_done_takes_no_other_assignment_and_is_done_once(conn, pmc, staff):
    other = add_staff(conn, pmc.pmc_id, "Staff 2")
    made = todo(conn, pmc, staff)
    assign(conn, pmc, made, other, date(2026, 10, 30))  # reassigned
    finish(conn, pmc, made, other)
    with pytest.raises(psycopg.errors.CheckViolation, match="done already"):
        assign(conn, pmc, made, staff)
    with pytest.raises(psycopg.errors.CheckViolation, match="done already"):
        finish(conn, pmc, made, staff)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        assign(conn, pmc, todo(conn, pmc, staff), add_staff(conn, make_pmc(conn).pmc_id))


def test_all_of_it_is_append_only_audited_and_never_the_ais(conn, ai_conn, pmc, staff):
    made = todo(conn, pmc, staff)
    finish(conn, pmc, made, staff)
    announce(conn, pmc, staff)
    tables = ("trust_announcements", "trust_todos", "trust_todo_assignments",
              "trust_todo_completions")  # fmt: skip
    for table in tables:
        for change in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with pytest.raises(psycopg.errors.RestrictViolation), conn.transaction():
                conn.execute(change)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ai_conn.execute(f"SELECT 1 FROM {table}")  # noqa: S608
    logged = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT table_name FROM trust_audit_log WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchall()
    }
    assert set(tables) <= logged


def test_an_assignment_names_who_changed_it(conn, pmc, staff):
    made = todo(conn, pmc, staff)
    conn.execute("SELECT set_config('trust.staff_id', %s, false)", (str(staff),))
    try:
        assign(conn, pmc, made, staff, date(2026, 11, 2))
    finally:
        conn.execute("SELECT set_config('trust.staff_id', '', false)")
    assert conn.execute(
        "SELECT recorded_by FROM trust_todo_assignments WHERE todo_id = %s"
        " ORDER BY created_at DESC LIMIT 1",
        (made,),
    ).fetchone() == (staff,)
