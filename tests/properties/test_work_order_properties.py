"""Property-based tests for a work order's steps (migration 20261016000072: the scheduled step and
no vendor whose insurance ran out).

Hypothesis gives one vendor a few policies over a few months (another has none), then tries a
work order's steps in random order and on random days: assigned to either vendor, scheduled for
a day, completed, cancelled. A model says which the database takes:
  - nothing follows a completed or cancelled work order, and no step is dated before the one it
    follows;
  - a work order is scheduled only once a vendor was assigned to it;
  - a vendor is not assigned once their insurance ran out: of their policies started by the
    step's day (UTC), the one ending last ended before it. A vendor with no policy started is not
    refused.
Every refused step writes nothing. Then the work orders report, as of the last day, shows the
model's status and the vendor last assigned, and counts a scheduled work order with the
assigned ones.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from helpers import add_work_order, make_pmc, step_work_order
from hypothesis import event, given
from hypothesis import strategies as st

START = date(2026, 1, 1)
OPENED = datetime(2026, 1, 1, tzinfo=UTC)
POLICIES = st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=60),  # starts this many days after Jan 1
        st.integers(min_value=0, max_value=40),  # and runs this many more
    ),
    max_size=3,
)
STEPS = st.lists(
    st.tuples(
        st.sampled_from(["assigned", "scheduled", "completed", "cancelled"]),
        st.integers(min_value=0, max_value=120),  # days after Jan 1
        st.booleans(),  # assigned: the insured vendor (else the one with no policy)
    ),
    max_size=6,
)


@given(policies=POLICIES, steps=STEPS)
def check_steps(conn, policies, steps):
    pmc = make_pmc(conn)
    insured = pmc.vendor_id
    uninsured = conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor B') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    names = {
        insured: conn.execute(
            "SELECT display_name FROM trust_vendors WHERE id = %s", (insured,)
        ).fetchone()[0],
        uninsured: "Vendor B",
    }
    ends = []  # (starts, expires) of the insured vendor's policies
    for n, (starts, runs) in enumerate(policies):
        begins = START + timedelta(days=starts)
        ends.append((begins, begins + timedelta(days=runs)))
        conn.execute(
            "INSERT INTO trust_vendor_insurance (pmc_id, vendor_id, coverage, carrier,"
            " policy_number, starts_on, expires_on)"
            " VALUES (%s, %s, 'general liability', 'Carrier A', %s, %s, %s)",
            (pmc.pmc_id, insured, f"P-{n}", *ends[-1]),
        )
    order = add_work_order(
        conn, pmc.pmc_id, pmc.owners[0].property_id, None, "Leaking faucet", OPENED
    )

    taken = []  # (step, taken_at, vendor) of the steps the model accepts
    for step, days, to_insured in steps:
        at = OPENED + timedelta(days=days, hours=12)
        day = at.date()
        vendor = (insured if to_insured else uninsured) if step == "assigned" else None
        last = max((t for _, t, _ in taken), default=OPENED)
        started = [expires for begins, expires in ends if begins <= day]
        if any(s in ("completed", "cancelled") for s, _, _ in taken):
            why = "closed"
        elif at < last:
            why = "dated before the step it follows"
        elif step == "scheduled" and not any(s == "assigned" for s, _, _ in taken):
            why = "scheduled before an assignment"
        elif vendor == insured and started and max(started) < day:
            why = "insurance ran out"
        else:
            why = None
        scheduled_for = day + timedelta(days=3) if step == "scheduled" else None
        if why is None:
            step_work_order(conn, pmc.pmc_id, order, step, at, vendor, scheduled_for)
            taken.append((step, at, vendor))
            event(f"took {step}")
        else:
            event(f"refused: {why}")
            with pytest.raises(psycopg.errors.CheckViolation):
                step_work_order(conn, pmc.pmc_id, order, step, at, vendor, scheduled_for)

    # Every refused step wrote nothing: the work order's steps are the model's.
    got = conn.execute(
        "SELECT step, taken_at, vendor_id FROM trust_work_order_steps WHERE work_order_id = %s"
        " ORDER BY taken_at, created_at, id",
        (order,),
    ).fetchall()
    assert [tuple(row) for row in got] == taken

    as_of = START + timedelta(days=121)
    status = taken[-1][0] if taken else "open"
    assigned = [vendor for step, _, vendor in taken if step == "assigned"]
    report = conn.execute(
        "SELECT item, status, vendor, summary FROM trust_report_work_orders(%s, %s)"
        " WHERE item IN ('open', 'closed', 'total') ORDER BY line",
        (pmc.pmc_id, as_of),
    ).fetchall()
    closed = status in ("completed", "cancelled")
    assert report[0][:3] == (
        "closed" if closed else "open",
        status,
        names[assigned[-1]] if assigned else None,
    )
    counted = f"{0 if closed else 1} open ({1 if status in ('assigned', 'scheduled') else 0}"
    assert report[1][0] == "total"
    assert report[1][3] == (
        f"{counted} assigned), {int(status == 'completed')} completed,"
        f" {int(status == 'cancelled')} cancelled"
    )


def test_a_work_orders_steps_follow_in_order_and_no_lapsed_vendor_is_assigned(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_steps(conn)
