"""Archiving a vendor (migration 20261021000110; the founder's review q131): a row of
trust_archivals, the latest holding, refused while anything is open with them (a bill neither
paid nor declined, money set aside for them, a work order last assigned to them and still open,
a recurring bill or job of theirs still running), and kept for good."""

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from helpers import (
    acting_as,
    add_recurring_bill,
    add_work_order,
    approve_bill,
    decline_bill,
    end_recurring_bill,
    enter_bill,
    make_pmc,
    pay_bill,
    set_aside_bill,
    staff_named,
    step_work_order,
    transfer_batch,
)

NOW = datetime.now(UTC)
TODAY = NOW.date()
DAY = timedelta(days=1)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def archive(conn, pmc, vendor_id=None, archived=True, **other):
    columns = {"vendor_id": vendor_id or pmc.vendor_id, **other}
    conn.execute(
        f"INSERT INTO trust_archivals (pmc_id, archived, {', '.join(columns)})"  # noqa: S608
        f" VALUES (%s, %s, {', '.join(['%s'] * len(columns))})",
        (pmc.pmc_id, archived, *columns.values()),
    )


def archived(conn, vendor_id):
    return conn.execute(
        "SELECT trust_is_archived(NULL, NULL, NULL, NULL, %s)", (vendor_id,)
    ).fetchone()[0]


def refused(match):
    return pytest.raises(psycopg.errors.CheckViolation, match=match)


def other_vendor(conn, pmc):
    return conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor 2') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]


def test_a_vendor_with_nothing_open_is_archived_and_brought_back(conn, pmc):
    assert archived(conn, pmc.vendor_id) is False

    archive(conn, pmc)
    assert archived(conn, pmc.vendor_id) is True
    with refused("archived already"):
        archive(conn, pmc)
    archive(conn, pmc, archived=False)
    assert archived(conn, pmc.vendor_id) is False
    with refused("not archived already"):
        archive(conn, pmc, archived=False)


def test_a_vendor_stays_while_a_bill_of_theirs_is_not_paid(conn, pmc):
    owner = pmc.owners[0]
    bill = enter_bill(conn, pmc.pmc_id, pmc.vendor_id, owner.account, "INV-1", TODAY, TODAY, "50")
    with refused("Vendor 1 has a bill not paid; pay or decline it before archiving"):
        archive(conn, pmc)
    transfer_batch(conn, [(pmc.operating_cash, owner.account, "50.00")])
    approve_bill(conn, pmc.pmc_id, bill, TODAY, "Owner 1")
    set_aside_bill(conn, pmc.pmc_id, bill, NOW)
    with refused("has a bill not paid"):
        archive(conn, pmc)
    pay_bill(conn, pmc.pmc_id, bill, NOW)
    archive(conn, pmc)

    # A declined bill holds nothing back either.
    other = other_vendor(conn, pmc)
    declined = enter_bill(conn, pmc.pmc_id, other, owner.account, "INV-2", TODAY, TODAY, "9")
    with refused("Vendor 2 has a bill not paid"):
        archive(conn, pmc, vendor_id=other)
    decline_bill(conn, pmc.pmc_id, declined, TODAY, "Owner 1")
    archive(conn, pmc, vendor_id=other)


def test_a_vendor_stays_while_money_is_set_aside_for_them(conn, pmc):
    transfer_batch(conn, [(pmc.operating_cash, pmc.vendor_payable, "10.00")])
    with refused("Vendor 1 has money set aside for them; pay it before archiving"):
        archive(conn, pmc)
    transfer_batch(conn, [(pmc.vendor_payable, pmc.operating_cash, "10.00")])
    archive(conn, pmc)


def test_a_vendor_stays_while_a_work_order_last_assigned_to_them_is_open(conn, pmc):
    prop = pmc.owners[0].property_id
    order = add_work_order(conn, pmc.pmc_id, prop, None, "Leak", NOW - DAY)
    step_work_order(conn, pmc.pmc_id, order, "assigned", NOW - DAY, vendor_id=pmc.vendor_id)
    with refused("Vendor 1 has a work order open; complete, cancel or reassign it"):
        archive(conn, pmc)
    # Reassigned: the work order is the other vendor's now.
    other = other_vendor(conn, pmc)
    step_work_order(conn, pmc.pmc_id, order, "assigned", NOW, vendor_id=other)
    archive(conn, pmc)
    with refused("Vendor 2 has a work order open"):
        archive(conn, pmc, vendor_id=other)
    step_work_order(conn, pmc.pmc_id, order, "completed", NOW)
    archive(conn, pmc, vendor_id=other)


def test_a_vendor_stays_while_a_recurring_bill_or_job_of_theirs_runs(conn, pmc):
    owner = pmc.owners[0]
    mowing = add_recurring_bill(
        conn, pmc.pmc_id, pmc.vendor_id, owner.account, "MOW", "40.00", 1, TODAY - 60 * DAY
    )
    with refused("Vendor 1 has a recurring bill or job still running; stop it"):
        archive(conn, pmc)
    end_recurring_bill(conn, pmc.pmc_id, mowing, TODAY)  # its last day is today: still running
    with refused("recurring bill or job still running"):
        archive(conn, pmc)

    other = other_vendor(conn, pmc)
    job = conn.execute(
        "INSERT INTO trust_recurring_work_orders (pmc_id, property_id, summary, every_months,"
        " day_of_month, starts_on, vendor_id) VALUES (%s, %s, 'Filters', 3, 15, %s, %s)"
        " RETURNING id",
        (pmc.pmc_id, owner.property_id, TODAY, other),
    ).fetchone()[0]
    with refused("Vendor 2 has a recurring bill or job still running"):
        archive(conn, pmc, vendor_id=other)
    conn.execute(
        "INSERT INTO trust_recurring_work_order_ends (recurring_work_order_id, pmc_id, ends_on)"
        " VALUES (%s, %s, %s)",
        (job, pmc.pmc_id, TODAY - DAY),
    )
    archive(conn, pmc, vendor_id=other)


def test_a_vendors_archival_is_of_one_record_of_the_pmc_and_names_who_made_it(conn, pmc):
    with pytest.raises(psycopg.errors.CheckViolation):
        archive(conn, pmc, owner_id=pmc.owners[0].owner_id)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        archive(conn, make_pmc(conn), vendor_id=pmc.vendor_id)

    manager = staff_named(conn, pmc.pmc_id, "Manager 1")
    with acting_as(conn, manager):
        archive(conn, pmc)
    assert conn.execute(
        "SELECT recorded_by FROM trust_archivals WHERE vendor_id = %s", (pmc.vendor_id,)
    ).fetchall() == [(manager,)]
