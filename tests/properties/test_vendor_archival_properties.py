"""Property-based tests for archiving a vendor (migration 20261021000110; the founder's review
q131).

Hypothesis gives a vendor a random state: a bill of theirs (none, open, set aside, paid or
declined), money set aside for them with no bill, a work order (none, open and last assigned to
them, open and reassigned to another vendor, or completed), a recurring bill and a recurring work
order of theirs (none, running, ending today, or ended yesterday), and then archives and brings
them back in a random order. A model says which of those requests stand: archiving only while
nothing is open with them (a bill neither paid nor declined, money set aside, an open work order
last assigned to them, a recurring bill or job running through today), never when already
archived, and bringing back only when archived. The database must take exactly those and say
after each what the model says. Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, datetime, timedelta

import psycopg
from helpers import (
    add_recurring_bill,
    add_work_order,
    approve_bill,
    decline_bill,
    end_recurring_bill,
    enter_bill,
    make_pmc,
    pay_bill,
    set_aside_bill,
    step_work_order,
    transfer_batch,
)
from hypothesis import event, example, given
from hypothesis import strategies as st

DAY = timedelta(days=1)
STATE = st.fixed_dictionaries(
    {
        "bill": st.sampled_from(["none", "open", "set aside", "paid", "declined"]),
        "money": st.booleans(),
        "work_order": st.sampled_from(["none", "theirs", "reassigned", "completed"]),
        "recurring_bill": st.sampled_from(["none", "running", "ends today", "ended"]),
        "recurring_job": st.sampled_from(["none", "running", "ends today", "ended"]),
    }
)


def build(conn, pmc, state, now):
    today = now.date()
    vendor, owner = pmc.vendor_id, pmc.owners[0]
    other = conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor 2') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    if state["bill"] != "none":
        bill = enter_bill(conn, pmc.pmc_id, vendor, owner.account, "INV-1", today, today, "50")
        if state["bill"] == "declined":
            decline_bill(conn, pmc.pmc_id, bill, today, "Owner 1")
        elif state["bill"] in ("set aside", "paid"):
            transfer_batch(conn, [(pmc.operating_cash, owner.account, "50.00")])
            approve_bill(conn, pmc.pmc_id, bill, today, "Owner 1")
            set_aside_bill(conn, pmc.pmc_id, bill, now)
            if state["bill"] == "paid":
                pay_bill(conn, pmc.pmc_id, bill, now)
    if state["money"]:
        transfer_batch(conn, [(pmc.operating_cash, pmc.vendor_payable, "10.00")])
    if state["work_order"] != "none":
        order = add_work_order(conn, pmc.pmc_id, owner.property_id, None, "Leak", now - DAY)
        step_work_order(conn, pmc.pmc_id, order, "assigned", now - DAY, vendor_id=vendor)
        if state["work_order"] == "reassigned":
            step_work_order(conn, pmc.pmc_id, order, "assigned", now, vendor_id=other)
        elif state["work_order"] == "completed":
            step_work_order(conn, pmc.pmc_id, order, "completed", now)
    ends = {"ends today": today, "ended": today - DAY}
    if state["recurring_bill"] != "none":
        mowing = add_recurring_bill(
            conn, pmc.pmc_id, vendor, owner.account, "MOW", "40.00", 1, today - 60 * DAY
        )
        if state["recurring_bill"] in ends:
            end_recurring_bill(conn, pmc.pmc_id, mowing, ends[state["recurring_bill"]])
    if state["recurring_job"] != "none":
        job = conn.execute(
            "INSERT INTO trust_recurring_work_orders (pmc_id, property_id, summary, every_months,"
            " day_of_month, starts_on, vendor_id) VALUES (%s, %s, 'Filters', 3, 15, %s, %s)"
            " RETURNING id",
            (pmc.pmc_id, owner.property_id, today - 60 * DAY, vendor),
        ).fetchone()[0]
        if state["recurring_job"] in ends:
            conn.execute(
                "INSERT INTO trust_recurring_work_order_ends"
                " (recurring_work_order_id, pmc_id, ends_on) VALUES (%s, %s, %s)",
                (job, pmc.pmc_id, ends[state["recurring_job"]]),
            )


def held_back(state) -> bool:
    """What the model says keeps the vendor from being archived."""
    return (
        state["bill"] in ("open", "set aside")
        or state["money"]
        or state["work_order"] == "theirs"
        or state["recurring_bill"] in ("running", "ends today")
        or state["recurring_job"] in ("running", "ends today")
    )


NOTHING = {
    "bill": "none",
    "money": False,
    "work_order": "none",
    "recurring_bill": "none",
    "recurring_job": "none",
}
# One thing at a time, each kind that holds the vendor back and each that doesn't.
ONE_AT_A_TIME = [
    {**NOTHING, field: value}
    for field, values in [
        ("bill", ["open", "set aside", "paid", "declined"]),
        ("money", [True]),
        ("work_order", ["theirs", "reassigned", "completed"]),
        ("recurring_bill", ["running", "ends today", "ended"]),
        ("recurring_job", ["running", "ends today", "ended"]),
    ]
    for value in values
]


def one_at_a_time(test):
    for state in ONE_AT_A_TIME:
        test = example(state=state, requests=[True, False, True])(test)
    return test


@one_at_a_time
@given(state=STATE, requests=st.lists(st.booleans(), min_size=1, max_size=6))
def check(conn, state, requests):
    pmc = make_pmc(conn)
    now = datetime.now(UTC)
    build(conn, pmc, state, now)
    archived = False
    for wanted in requests:
        stands = wanted != archived and not (wanted and held_back(state))
        event("archive stands" if stands and wanted else "refused" if not stands else "back")
        try:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO trust_archivals (pmc_id, vendor_id, archived) VALUES (%s, %s, %s)",
                    (pmc.pmc_id, pmc.vendor_id, wanted),
                )
        except psycopg.errors.CheckViolation:
            assert not stands, (state, wanted)
        else:
            assert stands, (state, wanted)
            archived = wanted
        assert (
            conn.execute(
                "SELECT trust_is_archived(NULL, NULL, NULL, NULL, %s)", (pmc.vendor_id,)
            ).fetchone()[0]
            is archived
        )


def test_a_vendor_is_archived_exactly_while_nothing_is_open_with_them(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
