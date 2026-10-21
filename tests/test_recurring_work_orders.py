"""Recurring work orders, and a tenant charged for a work order (migration 20261018000085; the
founder's review, q072 and q073). The API's side is in tests/api/test_recurring_work_orders_api.py.
Every name here is synthetic."""

from datetime import UTC, date, datetime

import psycopg
import pytest
from helpers import (
    acting_as,
    add_staff,
    add_tenant,
    add_unit,
    add_work_order,
    make_pmc,
    open_lease,
)

NOW = datetime(2026, 10, 18, 9, tzinfo=UTC)


class Home:
    """Property 1 with Unit 1 (leased to Tenant A) and Unit 2 (leased to Tenant B); a vendor."""

    def __init__(self, conn):
        self.pmc = make_pmc(conn)
        pmc_id, prop = self.pmc.pmc_id, self.pmc.owners[0].property_id
        self.property_id = prop
        self.unit = add_unit(conn, pmc_id, prop, "Unit 1")
        self.other_unit = add_unit(conn, pmc_id, prop, "Unit 2")
        self.tenant = add_tenant(conn, pmc_id, prop, "Tenant A")
        self.other_tenant = add_tenant(conn, pmc_id, prop, "Tenant B")
        self.lease = open_lease(
            conn, pmc_id, self.unit, date(2026, 1, 1), None, "1500", [self.tenant]
        )
        self.other_lease = open_lease(
            conn, pmc_id, self.other_unit, date(2026, 1, 1), None, "1200", [self.other_tenant]
        )


@pytest.fixture
def home(conn):
    return Home(conn)


def recurring(conn, home, starts_on, every_months=3, day=15, unit=True, vendor=None, **more):
    columns = {
        "pmc_id": home.pmc.pmc_id,
        "property_id": home.property_id,
        "unit_id": home.unit if unit else None,
        "summary": "Change the furnace filter",
        "every_months": every_months,
        "day_of_month": day,
        "starts_on": starts_on,
        "vendor_id": vendor,
        **more,
    }
    return conn.execute(
        f"INSERT INTO trust_recurring_work_orders ({', '.join(columns)})"
        f" VALUES ({', '.join(['%s'] * len(columns))}) RETURNING id",
        tuple(columns.values()),
    ).fetchone()[0]


def open_through(conn, home, day):
    return conn.execute(
        "SELECT trust_open_recurring_work_orders(%s, %s)", (home.pmc.pmc_id, day)
    ).fetchone()[0]


def opened(conn, of):
    return conn.execute(
        "SELECT w.recurring_on, w.opened_at, w.summary, w.unit_id, d.priority,"
        " (SELECT s.vendor_id FROM trust_work_order_steps AS s WHERE s.work_order_id = w.id)"
        " FROM trust_work_orders AS w JOIN trust_work_order_details AS d ON d.work_order_id = w.id"
        " WHERE w.recurring_work_order_id = %s ORDER BY w.recurring_on",
        (of,),
    ).fetchall()


# --- recurring work orders -------------------------------------------------------------------


def test_each_one_due_opens_once_on_its_day(conn, home):
    # From January 20th, every 3 months on the 15th: the first is February 15th, the first 15th
    # on or after it starts; then May 15th.
    filters = recurring(conn, home, date(2026, 1, 20), priority="high")

    assert open_through(conn, home, date(2026, 2, 14)) == 0
    assert open_through(conn, home, date(2026, 7, 31)) == 2
    assert open_through(conn, home, date(2026, 7, 31)) == 0  # a run again opens nothing new
    assert opened(conn, filters) == [
        (
            date(2026, 2, 15),
            datetime(2026, 2, 15, tzinfo=UTC),
            "Change the furnace filter",
            home.unit,
            "high",
            None,
        ),
        (
            date(2026, 5, 15),
            datetime(2026, 5, 15, tzinfo=UTC),
            "Change the furnace filter",
            home.unit,
            "high",
            None,
        ),
    ]


def test_it_starts_on_its_first_day_when_that_is_the_day_and_stops_at_its_end(conn, home):
    gutters = recurring(conn, home, date(2026, 1, 15), every_months=1, unit=False)
    conn.execute(
        "INSERT INTO trust_recurring_work_order_ends (recurring_work_order_id, pmc_id, ends_on)"
        " VALUES (%s, %s, %s)",
        (gutters, home.pmc.pmc_id, date(2026, 3, 15)),
    )

    assert open_through(conn, home, date(2026, 6, 30)) == 2
    # January and February; March 15th is its end, so none from then.
    assert [(row[0], row[3]) for row in opened(conn, gutters)] == [
        (date(2026, 1, 15), None),
        (date(2026, 2, 15), None),
    ]
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO trust_recurring_work_order_ends"
            " (recurring_work_order_id, pmc_id, ends_on) VALUES (%s, %s, %s)",
            (gutters, home.pmc.pmc_id, date(2026, 9, 1)),
        )


def test_its_vendor_is_assigned_unless_their_insurance_ran_out(conn, home):
    pmc_id = home.pmc.pmc_id
    insured = recurring(conn, home, date(2026, 1, 1), every_months=6, vendor=home.pmc.vendor_id)
    lapsed_vendor = conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor Lapsed')"
        " RETURNING id",
        (pmc_id,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO trust_vendor_insurance (pmc_id, vendor_id, coverage, carrier, policy_number,"
        " starts_on, expires_on) VALUES (%s, %s, 'general liability', 'Carrier A', 'P-1',"
        " '2025-01-01', '2025-12-31')",
        (pmc_id, lapsed_vendor),
    )
    lapsed = recurring(conn, home, date(2026, 1, 1), every_months=6, vendor=lapsed_vendor)

    assert open_through(conn, home, date(2026, 1, 31)) == 2
    assert [row[5] for row in opened(conn, insured)] == [home.pmc.vendor_id]
    # Opened all the same; it waits for staff to assign someone.
    assert [row[5] for row in opened(conn, lapsed)] == [None]


def test_another_companys_are_left_alone(conn, home):
    other = Home(conn)
    theirs = recurring(conn, other, date(2026, 1, 1))
    assert open_through(conn, home, date(2026, 12, 31)) == 0
    assert opened(conn, theirs) == []
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="through a day"):
        open_through(conn, home, None)


def test_what_a_recurring_work_order_may_be(conn, home):
    refused = [
        ({"every_months": 0}, psycopg.errors.CheckViolation),
        ({"every_months": 13}, psycopg.errors.CheckViolation),
        ({"day": 29}, psycopg.errors.CheckViolation),
        ({"summary": "  "}, psycopg.errors.CheckViolation),
        ({"priority": "soon"}, psycopg.errors.CheckViolation),
        # A unit of another property.
        (
            {"unit_id": add_unit(conn, home.pmc.pmc_id, home.pmc.owners[1].property_id, "Unit 9")},
            psycopg.errors.CheckViolation,
        ),
        ({"vendor_id": make_pmc(conn).vendor_id}, psycopg.errors.ForeignKeyViolation),
    ]
    for change, error in refused:
        with pytest.raises(error), conn.transaction():
            recurring(conn, home, date(2026, 1, 1), **change)


def test_recurring_work_orders_are_append_only_audited_and_name_who(conn, home):
    clerk = add_staff(conn, home.pmc.pmc_id, "Clerk A")
    with acting_as(conn, clerk):
        filters = recurring(conn, home, date(2026, 1, 1))
    assert conn.execute(
        "SELECT recorded_by FROM trust_recurring_work_orders WHERE id = %s", (filters,)
    ).fetchone() == (clerk,)
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_recurring_work_orders'"
        " AND pmc_id = %s",
        (home.pmc.pmc_id,),
    ).fetchone() == (1,)
    for query in (
        "UPDATE trust_recurring_work_orders SET summary = 'x'",
        "DELETE FROM trust_recurring_work_order_ends",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(query)


def test_a_work_order_names_its_recurring_day_with_it_and_once(conn, home):
    filters = recurring(conn, home, date(2026, 1, 1))
    insert = (
        "INSERT INTO trust_work_orders (pmc_id, property_id, summary, opened_at,"
        " recurring_work_order_id, recurring_on) VALUES (%s, %s, 'Filter', %s, %s, %s)"
    )
    for of, on in ((filters, None), (None, date(2026, 1, 15))):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(insert, (home.pmc.pmc_id, home.property_id, NOW, of, on))
    conn.execute(insert, (home.pmc.pmc_id, home.property_id, NOW, filters, date(2026, 1, 15)))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(insert, (home.pmc.pmc_id, home.property_id, NOW, filters, date(2026, 1, 15)))


# --- a tenant charged for a work order -----------------------------------------------------


def charge(conn, home, lease, work_order, kind="fee", pmc_id=None):
    return conn.execute(
        "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo, work_order_id)"
        " VALUES (%s, %s, '2026-10-18', %s, 85.00, 'Broken window', %s) RETURNING id",
        (pmc_id or home.pmc.pmc_id, lease, kind, work_order),
    ).fetchone()[0]


def test_a_work_order_is_charged_only_to_a_lease_at_its_unit_or_property(conn, home):
    pmc_id = home.pmc.pmc_id
    window = add_work_order(conn, pmc_id, home.property_id, home.unit, "Window", NOW)
    hallway = add_work_order(conn, pmc_id, home.property_id, None, "Hallway", NOW)
    elsewhere = add_work_order(
        conn, pmc_id, home.pmc.owners[1].property_id, None, "Another property", NOW
    )

    charge(conn, home, home.lease, window)
    # A work order on the whole property: any lease at one of its units.
    charge(conn, home, home.other_lease, hallway)
    for lease, order, kind in (
        (home.other_lease, window, "fee"),  # another unit
        (home.lease, elsewhere, "fee"),  # another property
        (home.lease, window, "credit"),  # never a credit
    ):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            charge(conn, home, lease, order, kind)
    theirs = Home(conn)
    their_order = add_work_order(
        conn, theirs.pmc.pmc_id, theirs.property_id, theirs.unit, "Theirs", NOW
    )
    # Another company's work order is at none of this lease's properties.
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        charge(conn, home, home.lease, their_order)
    assert conn.execute(
        "SELECT count(*) FROM trust_charges WHERE work_order_id IN (%s, %s)", (window, hallway)
    ).fetchone() == (2,)
