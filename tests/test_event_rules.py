"""Events in the database (ADR 0011, migration 20261105000500): each write that tells of something
writes its event in the same transaction, a kind and ids only, by one function that runs as the
owner; a write undone writes none; a reader that takes events of finished transactions, in the
order they finished, never skips one written by a transaction that began earlier and finished
later. Every name here is synthetic."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_late_fee_policy,
    add_tenant,
    add_unit,
    add_work_order,
    assess_late_fees,
    charge,
    enter_bill,
    file_request,
    make_pmc,
    open_lease,
    post,
    step_work_order,
)

from pms.api.events import events_after

NOW = datetime.now(UTC)
TODAY = NOW.date()


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def events(conn, pmc):
    return conn.execute(
        "SELECT kind, subject FROM trust_events WHERE pmc_id = %s ORDER BY id", (pmc.pmc_id,)
    ).fetchall()


def lease_of(conn, pmc, name="Unit 1"):
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, name)
    tenant = add_tenant(conn, pmc.pmc_id, prop, f"Tenant of {name}")
    return (
        unit,
        tenant,
        open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "900.00", [tenant]),
    )


def test_a_lease_its_payment_and_its_bounce_are_events(conn, pmc):
    unit, _, lease = lease_of(conn, pmc)
    (paid,) = post(
        conn,
        pmc.pmc_id,
        "payment/synthetic-1",
        [(pmc.operating_cash, pmc.owners[0].account, "900.00")],
        metadata={"kind": "tenant payment", "lease_id": str(lease)},
    )
    (back,) = post(
        conn,
        pmc.pmc_id,
        "bounce/payment/synthetic-1",
        [(pmc.owners[0].account, pmc.operating_cash, "900.00")],
        metadata={"kind": "bounced payment", "lease_id": str(lease)},
    )
    # A posting that is neither: no event.
    post(conn, pmc.pmc_id, "other-1", [(pmc.operating_cash, pmc.owners[0].account, "1.00")])

    assert events(conn, pmc) == [
        ("lease.opened", {"lease_id": str(lease), "unit_id": str(unit)}),
        ("payment.recorded", {"lease_id": str(lease), "transfer_id": paid}),
        ("payment.returned", {"lease_id": str(lease), "transfer_id": back}),
    ]


def test_a_lease_end_set_or_moved_is_an_event_and_the_same_end_is_none(conn, app_conn, pmc):
    unit, _, lease = lease_of(conn, pmc)
    app_conn.execute(
        "SELECT trust_end_lease(%s, %s, %s)", (pmc.pmc_id, lease, TODAY + timedelta(60))
    )
    app_conn.execute(
        "SELECT trust_end_lease(%s, %s, %s)", (pmc.pmc_id, lease, TODAY + timedelta(60))
    )
    kinds = [k for k, _ in events(conn, pmc)]
    assert kinds.count("lease.end_changed") == 1


def test_a_late_fee_is_an_event(conn, pmc):
    _, _, lease = lease_of(conn, pmc)
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, date(2026, 1, 1), 0, "50.00")
    rent = charge(conn, pmc.pmc_id, lease, TODAY - timedelta(days=10), Decimal("900.00"))
    assess_late_fees(conn, pmc.pmc_id, TODAY)
    (late,) = [s for k, s in events(conn, pmc) if k == "late_fee.charged"]
    assert late["rent_charge_id"] == str(rent) and late["lease_id"] == str(lease)
    assert set(late) == {"lease_id", "charge_id", "rent_charge_id"}


def test_a_bill_over_its_owners_limit_waits_for_them(conn, pmc):
    # No management agreement: the limit is 0, so any bill waits for its owner.
    bill = enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        pmc.owners[0].account,
        "INV-1",
        TODAY,
        TODAY,
        "120.00",
    )
    assert events(conn, pmc) == [
        ("bill.waiting_for_owner", {"bill_id": str(bill), "vendor_id": str(pmc.vendor_id)})
    ]


def test_a_work_order_its_steps_and_a_request_are_events(conn, pmc):
    unit, tenant, lease = lease_of(conn, pmc)
    order = add_work_order(conn, pmc.pmc_id, pmc.owners[0].property_id, unit, "Leak", NOW)
    step_work_order(conn, pmc.pmc_id, order, "assigned", NOW, vendor_id=pmc.vendor_id)
    step_work_order(conn, pmc.pmc_id, order, "scheduled", NOW, scheduled_for=TODAY)
    step_work_order(conn, pmc.pmc_id, order, "completed", NOW)
    request = file_request(conn, pmc.pmc_id, lease, tenant, "Tap drips")
    assert events(conn, pmc)[1:] == [
        (
            "work_order.opened",
            {
                "work_order_id": str(order),
                "property_id": str(pmc.owners[0].property_id),
                "unit_id": str(unit),
            },
        ),
        ("work_order.assigned", {"work_order_id": str(order)}),
        ("work_order.scheduled", {"work_order_id": str(order)}),
        ("work_order.completed", {"work_order_id": str(order)}),
        ("maintenance.requested", {"request_id": str(request), "lease_id": str(lease)}),
    ]


def test_an_undone_write_writes_no_event(conn, pmc):
    with pytest.raises(RuntimeError), conn.transaction():
        add_work_order(conn, pmc.pmc_id, pmc.owners[0].property_id, None, "Undone", NOW)
        raise RuntimeError("rolled back")
    assert events(conn, pmc) == []


def test_neither_the_app_nor_the_ai_writes_an_event(app_conn, ai_conn, pmc):
    for who in (app_conn, ai_conn):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            who.execute(
                "INSERT INTO trust_events (pmc_id, kind, subject)"
                " VALUES (%s, 'work_order.opened', '{}')",
                (pmc.pmc_id,),
            )


@pytest.mark.parametrize(
    "statement", ["UPDATE trust_events SET kind = kind", "DELETE FROM trust_events"]
)
def test_events_never_change(conn, pmc, statement):
    add_work_order(conn, pmc.pmc_id, pmc.owners[0].property_id, None, "Leak", NOW)
    with pytest.raises(psycopg.errors.RestrictViolation):
        conn.execute(statement)


def test_a_reader_never_skips_an_event_of_a_transaction_that_finished_later(
    database_url, conn, pmc
):
    prop = pmc.owners[0].property_id
    with (
        psycopg.connect(database_url) as slow,
        psycopg.connect(database_url, autocommit=True) as quick,
        psycopg.connect(database_url, autocommit=True) as reader,
    ):
        # The slow transaction begins first and writes its event first (the lower id)...
        add_work_order(slow, pmc.pmc_id, prop, None, "Slow", NOW)
        # ...the quick one writes and finishes while the slow one still runs.
        add_work_order(quick, pmc.pmc_id, prop, None, "Quick", NOW)

        def read(after=None):
            found = events_after(reader, pmc.pmc_id, after, 100)  # GET /api/events's own read
            return [(e.id, e.kind) for e in found]

        before = read()  # what the company had already (made by make_pmc), all finished
        start = before[-1][0] if before else None
        # Neither is read while the slow one runs: the quick one's turn comes after it.
        assert read(start) == [] if start else read() == []
        slow.commit()
        found = read(start) if start else read()
        assert [k for _, k in found] == ["work_order.opened", "work_order.opened"]
        assert read(found[0][0]) == found[1:]
        assert read(found[-1][0]) == []
