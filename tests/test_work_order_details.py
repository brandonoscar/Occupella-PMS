"""Work orders as Buildium works them (migration 20261016000072): their details (priority, what
is wrong, the day due, entry), the scheduled step and its day, no assignment to a vendor whose
insurance ran out, a work order's photos, and the work orders report counting a scheduled one with
the assigned."""

from datetime import UTC, date, datetime

import psycopg
import pytest
from helpers import (
    acting_as,
    add_document,
    add_staff,
    add_work_order,
    make_pmc,
    step_work_order,
)

JAN_5 = datetime(2026, 1, 5, 9, tzinfo=UTC)
JAN_6 = datetime(2026, 1, 6, 9, tzinfo=UTC)
JAN_7 = datetime(2026, 1, 7, 9, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def order(conn, pmc):
    """A work order at Property 1, opened Jan 5."""
    return add_work_order(conn, pmc.pmc_id, pmc.owners[0].property_id, None, "Leaking tap", JAN_5)


def add_details(conn, pmc_id, order, priority="normal", description=None, due_on=None, entry=None):
    conn.execute(
        "INSERT INTO trust_work_order_details"
        " (pmc_id, work_order_id, priority, description, due_on, entry)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (pmc_id, order, priority, description, due_on, entry),
    )


def add_policy(conn, pmc_id, vendor, starts, expires, number="GL-1"):
    conn.execute(
        "INSERT INTO trust_vendor_insurance (pmc_id, vendor_id, coverage, carrier, policy_number,"
        " starts_on, expires_on) VALUES (%s, %s, 'general liability', 'Carrier A', %s, %s, %s)",
        (pmc_id, vendor, number, starts, expires),
    )


def test_details_are_kept_row_after_row_naming_who_recorded_them(conn, app_conn, pmc, order):
    staff = add_staff(conn, pmc.pmc_id)
    with acting_as(app_conn, staff):
        add_details(
            app_conn, pmc.pmc_id, order, "high", "Drips at night", date(2026, 1, 9), "allowed"
        )
        add_details(app_conn, pmc.pmc_id, order, "urgent", None, None, "call first")

    assert conn.execute(
        "SELECT priority, description, due_on, entry, recorded_by FROM trust_work_order_details"
        " WHERE work_order_id = %s ORDER BY created_at",
        (order,),
    ).fetchall() == [
        ("high", "Drips at night", date(2026, 1, 9), "allowed", staff),
        ("urgent", None, None, "call first", staff),
    ]


@pytest.mark.parametrize(
    ("priority", "description", "entry"),
    [
        ("whenever", None, None),
        ("normal", "  ", None),
        ("normal", "x" * 5001, None),
        ("normal", None, "anytime"),
    ],
)
def test_details_take_only_known_words_and_some_text(
    conn, pmc, order, priority, description, entry
):
    with pytest.raises(psycopg.errors.CheckViolation):
        add_details(conn, pmc.pmc_id, order, priority, description, None, entry)


def test_details_are_of_a_work_order_of_their_pmc(conn, pmc, order):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        add_details(conn, make_pmc(conn).pmc_id, order)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE trust_work_order_details SET priority = 'low'",
        "DELETE FROM trust_work_order_details",
        "TRUNCATE trust_work_order_details",
    ],
)
def test_details_are_kept_for_good(conn, pmc, order, statement):
    add_details(conn, pmc.pmc_id, order, "high")
    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)


def test_a_visit_is_scheduled_once_a_vendor_is_assigned(conn, app_conn, pmc, order):
    with pytest.raises(psycopg.errors.CheckViolation, match="once a vendor is assigned"):
        step_work_order(app_conn, pmc.pmc_id, order, "scheduled", JAN_6, None, date(2026, 1, 9))

    step_work_order(app_conn, pmc.pmc_id, order, "assigned", JAN_6, pmc.vendor_id)
    step_work_order(app_conn, pmc.pmc_id, order, "scheduled", JAN_7, None, date(2026, 1, 9))
    # A visit is moved with another schedule; then the work is done.
    step_work_order(app_conn, pmc.pmc_id, order, "scheduled", JAN_7, None, date(2026, 1, 12))
    step_work_order(app_conn, pmc.pmc_id, order, "completed", JAN_7)

    assert conn.execute(
        "SELECT step, scheduled_for FROM trust_work_order_steps WHERE work_order_id = %s"
        " ORDER BY taken_at, created_at",
        (order,),
    ).fetchall() == [
        ("assigned", None),
        ("scheduled", date(2026, 1, 9)),
        ("scheduled", date(2026, 1, 12)),
        ("completed", None),
    ]


@pytest.mark.parametrize(
    ("step", "vendor", "scheduled_for"),
    [
        ("scheduled", None, None),
        ("scheduled", "vendor", date(2026, 1, 9)),
        ("assigned", "vendor", date(2026, 1, 9)),
        ("completed", None, date(2026, 1, 9)),
    ],
)
def test_only_a_schedule_names_a_day_and_it_names_no_vendor(
    conn, pmc, order, step, vendor, scheduled_for
):
    step_work_order(conn, pmc.pmc_id, order, "assigned", JAN_5, pmc.vendor_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        step_work_order(
            conn, pmc.pmc_id, order, step, JAN_6, vendor and pmc.vendor_id, scheduled_for
        )


def test_nothing_is_scheduled_after_a_close(conn, pmc, order):
    step_work_order(conn, pmc.pmc_id, order, "assigned", JAN_5, pmc.vendor_id)
    step_work_order(conn, pmc.pmc_id, order, "cancelled", JAN_6)
    with pytest.raises(psycopg.errors.CheckViolation):
        step_work_order(conn, pmc.pmc_id, order, "scheduled", JAN_7, None, date(2026, 1, 9))


def test_a_vendor_whose_insurance_ran_out_is_not_assigned(conn, app_conn, pmc, order):
    add_policy(conn, pmc.pmc_id, pmc.vendor_id, date(2025, 1, 1), date(2026, 1, 5))

    # Covered through its last day, Jan 5 (UTC).
    step_work_order(app_conn, pmc.pmc_id, order, "assigned", JAN_5, pmc.vendor_id)
    with pytest.raises(psycopg.errors.CheckViolation, match="insurance expired on 01/05/2026"):
        step_work_order(app_conn, pmc.pmc_id, order, "assigned", JAN_6, pmc.vendor_id)
    # A renewal starting the next day covers them again.
    add_policy(conn, pmc.pmc_id, pmc.vendor_id, date(2026, 1, 6), date(2027, 1, 5), "GL-2")
    step_work_order(app_conn, pmc.pmc_id, order, "assigned", JAN_6, pmc.vendor_id)


def test_a_policy_not_yet_started_does_not_count_and_no_policy_blocks_nothing(conn, pmc, order):
    vendor_2 = conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor 2') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    # Vendor 1's only running policy ran out; one starting later doesn't cover them yet.
    add_policy(conn, pmc.pmc_id, pmc.vendor_id, date(2025, 1, 1), date(2025, 12, 31))
    add_policy(conn, pmc.pmc_id, pmc.vendor_id, date(2026, 2, 1), date(2027, 1, 31), "GL-2")

    with pytest.raises(psycopg.errors.CheckViolation, match="expired on 12/31/2025"):
        step_work_order(conn, pmc.pmc_id, order, "assigned", JAN_6, pmc.vendor_id)
    # A vendor with no insurance on file is not refused: the vendors page says so.
    step_work_order(conn, pmc.pmc_id, order, "assigned", JAN_6, vendor_2)


def test_a_work_order_photo_is_about_one_work_order_of_its_pmc(conn, pmc, order):
    photo = add_document(conn, pmc.pmc_id, "work_order_photo", order, title="Under the sink")
    assert conn.execute(
        "SELECT kind, work_order_id FROM trust_documents WHERE id = %s", (photo,)
    ).fetchone() == ("work_order_photo", order)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        add_document(conn, make_pmc(conn).pmc_id, "work_order_photo", order)
    with pytest.raises(psycopg.errors.CheckViolation):
        # Another kind names no work order.
        conn.execute(
            "INSERT INTO trust_documents (pmc_id, kind, owner_id, work_order_id, title,"
            " content_type, size, sha256) VALUES (%s, 'owner_document', %s, %s, 'X',"
            " 'application/pdf', 1, sha256('x'))",
            (pmc.pmc_id, pmc.owners[0].owner_id, order),
        )


def test_the_report_counts_a_scheduled_work_order_with_the_assigned(conn, pmc, order):
    step_work_order(conn, pmc.pmc_id, order, "assigned", JAN_5, pmc.vendor_id)
    step_work_order(conn, pmc.pmc_id, order, "scheduled", JAN_6, None, date(2026, 1, 9))

    rows = conn.execute(
        "SELECT item, status, vendor, summary FROM trust_report_work_orders(%s, %s)"
        " WHERE item IN ('open', 'total')",
        (pmc.pmc_id, date(2026, 1, 7)),
    ).fetchall()
    assert rows == [
        ("open", "scheduled", "Vendor 1", "Leaking tap"),
        ("total", None, None, "1 open (1 assigned), 0 completed, 0 cancelled"),
    ]


def test_the_ai_reads_details_and_writes_none(conn, ai_conn, pmc, order):
    add_details(conn, pmc.pmc_id, order, "urgent")
    assert ai_conn.execute(
        "SELECT priority FROM trust_work_order_details WHERE work_order_id = %s", (order,)
    ).fetchall() == [("urgent",)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        add_details(ai_conn, pmc.pmc_id, order)
