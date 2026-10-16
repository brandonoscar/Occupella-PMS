"""The Dashboard's figures through the API (src/pms/api/dashboard.py): what tenants owe, open
work orders, leases ending within 30, 60 and 90 days, vacant units and applications waiting,
each as its own report counts it, for the session's PMC only."""

from datetime import UTC, datetime, timedelta

import pytest
from helpers import (
    add_tenant,
    add_unit,
    add_work_order,
    charge,
    end_lease,
    make_pmc,
    open_application_link,
    open_lease,
    record_decision,
    step_work_order,
    submit_application,
)
from people import join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
START = TODAY - timedelta(days=90)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def lease_of(conn, pmc, name, ends_in=None):
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, name)
    tenant = add_tenant(conn, pmc.pmc_id, prop, f"Tenant of {name}")
    lease = open_lease(conn, pmc.pmc_id, unit, START, None, "1500.00", [tenant])
    if ends_in is not None:
        end_lease(conn, pmc.pmc_id, lease, TODAY + ends_in * DAY)
    return unit, lease


def test_the_dashboard_counts_each_part_as_its_report_does(client, conn, staff, pmc):
    # Two leases owe rent due by today (one also owes tomorrow's, not counted yet).
    unit, owing = lease_of(conn, pmc, "Unit 1")
    charge(conn, pmc.pmc_id, owing, TODAY - 10 * DAY, "1500.00")
    charge(conn, pmc.pmc_id, owing, TODAY + DAY, "1500.00")
    _, also = lease_of(conn, pmc, "Unit 2", ends_in=45)
    charge(conn, pmc.pmc_id, also, TODAY, "250.50")
    # Leases ending in 10 and 75 days, and one in 200 (not counted).
    lease_of(conn, pmc, "Unit 3", ends_in=10)
    lease_of(conn, pmc, "Unit 4", ends_in=75)
    lease_of(conn, pmc, "Unit 5", ends_in=200)
    # Two units with no lease.
    for name in ("Unit 6", "Unit 7"):
        add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name)
    # Work orders: one open, one assigned, one completed, one cancelled.
    prop = pmc.owners[0].property_id
    orders = [
        add_work_order(conn, pmc.pmc_id, prop, unit, f"Job {n}", datetime.now(UTC) - 5 * DAY)
        for n in range(4)
    ]
    step_work_order(conn, pmc.pmc_id, orders[1], "assigned", datetime.now(UTC), pmc.vendor_id)
    step_work_order(conn, pmc.pmc_id, orders[2], "completed", datetime.now(UTC))
    step_work_order(conn, pmc.pmc_id, orders[3], "cancelled", datetime.now(UTC))
    # Applications: one waiting, one decided.
    link = open_application_link(conn, pmc.pmc_id, unit, staff.person_id)
    submit_application(conn, pmc.pmc_id, link)
    decided = submit_application(conn, pmc.pmc_id, link)
    record_decision(conn, pmc.pmc_id, decided, staff.person_id, "declined")

    answer = client.get("/api/dashboard")

    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "on": str(TODAY),
        "outstanding": "1750.50",
        "leases_owing": 2,
        "open_work_orders": 2,
        "leases_ending": {"within_30": 1, "within_60": 1, "within_90": 1},
        "vacant_units": 2,
        "applications_waiting": 1,
    }


def test_a_quiet_company_shows_zeros(client, staff, pmc):
    answer = client.get("/api/dashboard", params={"on": str(TODAY)}).json()

    assert (answer["outstanding"], answer["leases_owing"], answer["open_work_orders"]) == (
        "0.00",
        0,
        0,
    )
    assert answer["leases_ending"] == {"within_30": 0, "within_60": 0, "within_90": 0}
    assert (answer["vacant_units"], answer["applications_waiting"]) == (0, 0)


def test_the_dashboard_is_the_sessions_pmc(client, conn, staff, pmc):
    other = make_pmc(conn)
    prop = other.owners[0].property_id
    unit = add_unit(conn, other.pmc_id, prop, "Unit 1")
    tenant = add_tenant(conn, other.pmc_id, prop, "Tenant A")
    theirs = open_lease(conn, other.pmc_id, unit, START, None, "1500.00", [tenant])
    charge(conn, other.pmc_id, theirs, TODAY - DAY, "1500.00")
    add_unit(conn, other.pmc_id, prop, "Unit 2")

    answer = client.get("/api/dashboard").json()

    assert (answer["outstanding"], answer["vacant_units"]) == ("0.00", 0)


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_see_the_dashboard(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    assert client.get("/api/dashboard").status_code == (401 if caller == "nobody" else 403)


@pytest.mark.parametrize("on", ["not a day", "2026-02-30"])
def test_a_day_that_is_not_one_is_refused(client, staff, on):
    assert client.get("/api/dashboard", params={"on": on}).status_code == 422
