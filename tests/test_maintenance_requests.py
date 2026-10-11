"""Maintenance requests: what a tenant asks to have fixed, under one of their leases, and the
work order staff open for it (migration 20261011000029). The API's side is tested in
tests/api/test_tenant.py and tests/api/test_maintenance.py.
"""

from datetime import UTC, date, datetime

import psycopg
import pytest
from helpers import (
    add_tenant,
    add_unit,
    add_work_order,
    file_request,
    link_work_order,
    make_pmc,
    open_lease,
)

JAN_5 = datetime(2026, 1, 5, 9, tzinfo=UTC)


class Home:
    """Unit 1 of Property 1, leased to Tenant A and Tenant B; Tenant C rents Unit 2."""

    def __init__(self, conn):
        self.pmc = make_pmc(conn)
        pmc_id, prop = self.pmc.pmc_id, self.pmc.owners[0].property_id
        self.property_id = prop
        self.unit = add_unit(conn, pmc_id, prop, "Unit 1")
        self.other_unit = add_unit(conn, pmc_id, prop, "Unit 2")
        self.tenant_a = add_tenant(conn, pmc_id, prop, "Tenant A")
        self.tenant_b = add_tenant(conn, pmc_id, prop, "Tenant B")
        self.tenant_c = add_tenant(conn, pmc_id, prop, "Tenant C")
        jan_1 = date(2026, 1, 1)
        self.lease = open_lease(
            conn, pmc_id, self.unit, jan_1, None, "1500", [self.tenant_a, self.tenant_b]
        )
        self.other_lease = open_lease(
            conn, pmc_id, self.other_unit, jan_1, None, "1200", [self.tenant_c]
        )


@pytest.fixture
def home(conn):
    return Home(conn)


def test_a_tenant_on_the_lease_files_a_request_and_staff_open_a_work_order(conn, app_conn, home):
    pmc_id = home.pmc.pmc_id
    request = file_request(app_conn, pmc_id, home.lease, home.tenant_b, "Tap drips", True)
    order = add_work_order(app_conn, pmc_id, home.property_id, home.unit, "Tap drips", JAN_5)
    link_work_order(app_conn, pmc_id, request, order)
    assert conn.execute(
        "SELECT r.tenant_id, r.summary, r.entry_allowed, w.work_order_id"
        " FROM trust_maintenance_requests AS r"
        " JOIN trust_maintenance_request_work_orders AS w ON w.request_id = r.id"
        " WHERE r.id = %s",
        (request,),
    ).fetchone() == (home.tenant_b, "Tap drips", True, order)


def test_only_a_tenant_on_the_lease_files_under_it(conn, home):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        file_request(conn, home.pmc.pmc_id, home.lease, home.tenant_c, "Not my unit")


def test_a_request_stays_in_its_pmc(conn, home):
    other = Home(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        file_request(conn, other.pmc.pmc_id, home.lease, home.tenant_a, "Wrong PMC")


@pytest.mark.parametrize("summary", ["", "   ", "\n\t", "x" * 2001])
def test_a_request_says_what_needs_fixing_in_at_most_2000_characters(conn, home, summary):
    with pytest.raises(psycopg.errors.CheckViolation):
        file_request(conn, home.pmc.pmc_id, home.lease, home.tenant_a, summary)


def test_a_request_of_2000_characters_is_filed(conn, home):
    file_request(conn, home.pmc.pmc_id, home.lease, home.tenant_a, "x" * 2000)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE trust_maintenance_requests SET summary = 'changed'",
        "DELETE FROM trust_maintenance_requests",
        "TRUNCATE trust_maintenance_requests CASCADE",
        "UPDATE trust_maintenance_request_work_orders SET created_at = now()",
        "DELETE FROM trust_maintenance_request_work_orders",
    ],
)
def test_requests_and_their_work_orders_are_never_rewritten(conn, home, statement):
    pmc_id = home.pmc.pmc_id
    request = file_request(conn, pmc_id, home.lease, home.tenant_a, "Door sticks")
    order = add_work_order(conn, pmc_id, home.property_id, home.unit, "Door sticks", JAN_5)
    link_work_order(conn, pmc_id, request, order)
    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)


@pytest.mark.parametrize("where", ["another unit", "the property only"])
def test_a_requests_work_order_is_for_the_leases_unit(conn, home, where):
    pmc_id = home.pmc.pmc_id
    request = file_request(conn, pmc_id, home.lease, home.tenant_a, "Heater out")
    unit = home.other_unit if where == "another unit" else None
    order = add_work_order(conn, pmc_id, home.property_id, unit, "Heater out", JAN_5)
    with pytest.raises(psycopg.errors.CheckViolation, match="not for the unit"):
        link_work_order(conn, pmc_id, request, order)


def test_a_request_has_one_work_order_and_a_work_order_one_request(conn, home):
    pmc_id = home.pmc.pmc_id
    first = file_request(conn, pmc_id, home.lease, home.tenant_a, "Window cracked")
    second = file_request(conn, pmc_id, home.lease, home.tenant_b, "Window cracked too")
    order = add_work_order(conn, pmc_id, home.property_id, home.unit, "Window", JAN_5)
    another = add_work_order(conn, pmc_id, home.property_id, home.unit, "Window again", JAN_5)
    link_work_order(conn, pmc_id, first, order)
    with pytest.raises(psycopg.errors.UniqueViolation):
        link_work_order(conn, pmc_id, first, another)
    with pytest.raises(psycopg.errors.UniqueViolation):
        link_work_order(conn, pmc_id, second, order)


def test_a_work_order_of_another_pmc_is_never_linked(conn, home):
    other = Home(conn)
    request = file_request(conn, home.pmc.pmc_id, home.lease, home.tenant_a, "Lock broken")
    order = add_work_order(
        conn, other.pmc.pmc_id, other.property_id, other.unit, "Lock broken", JAN_5
    )
    # Refused by the unit check, which runs before the keys do.
    with pytest.raises(psycopg.errors.CheckViolation, match="not for the unit"):
        link_work_order(conn, home.pmc.pmc_id, request, order)


def test_every_request_and_link_is_in_the_audit_log(conn, app_conn, home):
    pmc_id = home.pmc.pmc_id
    request = file_request(app_conn, pmc_id, home.lease, home.tenant_a, "Fan noisy")
    order = add_work_order(app_conn, pmc_id, home.property_id, home.unit, "Fan noisy", JAN_5)
    link_work_order(app_conn, pmc_id, request, order)
    logged = conn.execute(
        "SELECT table_name, db_role FROM trust_audit_log"
        " WHERE pmc_id = %s AND table_name LIKE 'trust_maintenance%%' ORDER BY id",
        (pmc_id,),
    ).fetchall()
    assert logged == [
        ("trust_maintenance_requests", "trust_app"),
        ("trust_maintenance_request_work_orders", "trust_app"),
    ]
