"""Work orders: a job at a property (maybe one of its units), assigned to vendors, then completed
or cancelled; and the bills sent for them (migration 20261010000018).
"""

from datetime import UTC, date, datetime

import psycopg
import pytest
from helpers import (
    add_unit,
    add_work_order,
    enter_bill,
    in_background,
    make_pmc,
    step_work_order,
    wait_until_blocked,
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


def steps(conn, work_order):
    return conn.execute(
        "SELECT step, vendor_id, taken_at FROM trust_work_order_steps WHERE work_order_id = %s"
        " ORDER BY taken_at, created_at",
        (work_order,),
    ).fetchall()


def test_the_app_opens_assigns_and_completes_a_work_order(conn, app_conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    order = add_work_order(
        app_conn, pmc.pmc_id, pmc.owners[0].property_id, unit, "Leaking tap", JAN_5
    )

    step_work_order(app_conn, pmc.pmc_id, order, "assigned", JAN_5, pmc.vendor_id)
    step_work_order(app_conn, pmc.pmc_id, order, "completed", JAN_7)

    assert steps(conn, order) == [("assigned", pmc.vendor_id, JAN_5), ("completed", None, JAN_7)]


def test_a_work_orders_unit_is_one_of_its_propertys(conn, pmc):
    other_property_unit = add_unit(conn, pmc.pmc_id, pmc.owners[1].property_id)

    with pytest.raises(psycopg.errors.CheckViolation, match="not one of property"):
        add_work_order(
            conn, pmc.pmc_id, pmc.owners[0].property_id, other_property_unit, "Paint", JAN_5
        )


def test_a_work_order_is_for_a_property_of_its_pmc(conn, pmc):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        add_work_order(conn, pmc.pmc_id, make_pmc(conn).owners[0].property_id, None, "X", JAN_5)


@pytest.mark.parametrize(
    ("step", "vendor"),
    [("assigned", None), ("completed", "vendor"), ("cancelled", "vendor"), ("reopened", None)],
)
def test_only_an_assignment_names_a_vendor(conn, pmc, order, step, vendor):
    with pytest.raises(psycopg.errors.CheckViolation):
        step_work_order(conn, pmc.pmc_id, order, step, JAN_6, vendor and pmc.vendor_id)


def test_a_work_order_is_reassigned_with_a_new_step(conn, pmc, order):
    vendor_2 = conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor 2') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    step_work_order(conn, pmc.pmc_id, order, "assigned", JAN_5, pmc.vendor_id)
    step_work_order(conn, pmc.pmc_id, order, "assigned", JAN_6, vendor_2)

    assert [vendor for _, vendor, _ in steps(conn, order)] == [pmc.vendor_id, vendor_2]
    with pytest.raises(psycopg.errors.CheckViolation, match="dated before"):  # the latest step
        step_work_order(conn, pmc.pmc_id, order, "completed", JAN_5)


@pytest.mark.parametrize("closing", ["completed", "cancelled"])
def test_nothing_follows_a_closed_work_order(conn, pmc, order, closing):
    step_work_order(conn, pmc.pmc_id, order, closing, JAN_6)

    for step, vendor in [("assigned", pmc.vendor_id), ("completed", None), ("cancelled", None)]:
        with pytest.raises(psycopg.errors.CheckViolation, match="nothing follows"):
            step_work_order(conn, pmc.pmc_id, order, step, JAN_7, vendor)


def test_steps_are_not_dated_before_the_work_order_or_the_step_before(conn, pmc, order):
    with pytest.raises(psycopg.errors.CheckViolation, match="dated before"):
        step_work_order(
            conn, pmc.pmc_id, order, "assigned", datetime(2026, 1, 4, tzinfo=UTC), pmc.vendor_id
        )
    step_work_order(conn, pmc.pmc_id, order, "assigned", JAN_6, pmc.vendor_id)
    with pytest.raises(psycopg.errors.CheckViolation, match="dated before"):
        step_work_order(conn, pmc.pmc_id, order, "completed", JAN_5)
    step_work_order(conn, pmc.pmc_id, order, "completed", JAN_6)  # the same moment is fine


def test_a_close_is_final_even_beside_steps_of_the_same_moment_and_transaction(
    database_url, pmc, order
):
    # Steps written in one transaction share created_at, so "the last step" can't be told
    # by time alone; the close must still be seen.
    with psycopg.connect(database_url) as tx:
        step_work_order(tx, pmc.pmc_id, order, "assigned", JAN_6, pmc.vendor_id)
        step_work_order(tx, pmc.pmc_id, order, "completed", JAN_6)
        with pytest.raises(psycopg.errors.CheckViolation, match="nothing follows"):
            step_work_order(tx, pmc.pmc_id, order, "assigned", JAN_6, pmc.vendor_id)


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_steps_are_recorded_at_read_committed(database_url, pmc, order, isolation):
    with psycopg.connect(database_url) as tx:
        tx.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
        with pytest.raises(psycopg.errors.InvalidTransactionState, match="READ COMMITTED"):
            step_work_order(tx, pmc.pmc_id, order, "completed", JAN_6)


@pytest.mark.timing
def test_a_step_waits_for_a_close_in_progress_and_is_then_refused(conn, database_url, pmc, order):
    with psycopg.connect(database_url) as first:
        step_work_order(first, pmc.pmc_id, order, "cancelled", JAN_6)  # not committed yet
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: step_work_order(
                worker, pmc.pmc_id, order, "assigned", JAN_7, pmc.vendor_id
            ),
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert [step for step, _, _ in steps(conn, order)] == ["cancelled"]


@pytest.mark.parametrize(
    "statement",
    ["UPDATE trust_work_orders SET summary = 'x'", "DELETE FROM trust_work_order_steps"],
)
def test_work_orders_and_their_steps_are_kept_for_good(conn, pmc, order, statement):
    step_work_order(conn, pmc.pmc_id, order, "assigned", JAN_5, pmc.vendor_id)

    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(statement)


# --- bills for work orders ------------------------------------------------------------------


def test_a_bill_is_for_a_work_order_of_its_property(conn, pmc, order):
    enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        pmc.owners[0].account,
        "INV-1",
        date(2026, 1, 8),
        date(2026, 1, 8),
        "80.00",
        work_order_id=order,
    )

    with pytest.raises(psycopg.errors.CheckViolation, match="not for the property"):
        enter_bill(
            conn,
            pmc.pmc_id,
            pmc.vendor_id,
            pmc.owners[1].account,
            "INV-2",
            date(2026, 1, 8),
            date(2026, 1, 8),
            "80.00",
            work_order_id=order,
        )


def test_a_cancelled_work_order_takes_no_bills(conn, pmc, order):
    step_work_order(conn, pmc.pmc_id, order, "cancelled", JAN_6)

    with pytest.raises(psycopg.errors.CheckViolation, match="was cancelled"):
        enter_bill(
            conn,
            pmc.pmc_id,
            pmc.vendor_id,
            pmc.owners[0].account,
            "INV-1",
            date(2026, 1, 8),
            date(2026, 1, 8),
            "80.00",
            work_order_id=order,
        )
