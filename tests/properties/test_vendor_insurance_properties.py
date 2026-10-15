"""Property-based tests for vendor insurance and move-in/move-out inspections (migration
20261015000058).

Hypothesis records a vendor's policies of two coverages over a few months, and a lease's move-in
and move-out inspections with rooms spelled in either case, in random order. A model picks what
counts; the reports must agree:
  - a vendor's coverage on a day is the policy of that coverage running then that runs longest,
    else the one that ran out latest; one starting later isn't theirs yet, and a vendor with
    none started is listed with no insurance on file. Its standing is expired, expires today,
    expires in N day(s) up to 30, else current.
  - an inspection report lays the latest inspection of each kind (by its day, then as recorded)
    side by side: each room once whatever its case, named as the move-in names it, in the
    move-in's order then the move-out's; changed only when both name it, and then exactly when
    the conditions differ.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date, timedelta

import psycopg
from helpers import add_unit, make_pmc, open_lease
from hypothesis import event, given
from hypothesis import strategies as st

START = date(2026, 1, 1)
COVERAGES = ("general liability", "workers compensation")
POLICIES = st.lists(
    st.tuples(
        st.sampled_from(COVERAGES),
        st.integers(min_value=0, max_value=90),  # starts this many days after Jan 1
        st.integers(min_value=0, max_value=60),  # and runs this many more
    ),
    max_size=6,
)


def standing(expires: date, day: date) -> str:
    if expires < day:
        return "expired"
    if expires == day:
        return "expires today"
    days = (expires - day).days
    return f"expires in {days} day{'' if days == 1 else 's'}" if days <= 30 else "current"


@given(policies=POLICIES, day=st.integers(min_value=0, max_value=150))
def check_insurance(conn, policies, day):
    on = START + timedelta(days=day)
    pmc = make_pmc(conn)
    recorded = []  # (coverage, number, starts, expires, created_at, id)
    for n, (coverage, starts, runs) in enumerate(policies):
        begins = START + timedelta(days=starts)
        ends = begins + timedelta(days=runs)
        made, at = conn.execute(
            "INSERT INTO trust_vendor_insurance (pmc_id, vendor_id, coverage, carrier,"
            " policy_number, starts_on, expires_on) VALUES (%s, %s, %s, 'Carrier A', %s, %s, %s)"
            " RETURNING id, created_at",
            (pmc.pmc_id, pmc.vendor_id, coverage, f"P-{n}", begins, ends),
        ).fetchone()
        recorded.append((coverage, f"P-{n}", begins, ends, at, made))

    expected = []
    for coverage in COVERAGES:
        started = [p for p in recorded if p[0] == coverage and p[2] <= on]
        if not started:
            continue
        # Running on the day first, then the latest to expire, then the latest recorded.
        started.sort(key=lambda p: p[5].hex)
        started.sort(key=lambda p: (p[3] >= on, p[3], p[4]), reverse=True)
        chosen = started[0]
        event("running" if chosen[3] >= on else "ran out")
        expected.append(
            ("policy", coverage, chosen[1], chosen[2], chosen[3], standing(chosen[3], on))
        )
    if not expected:
        event("no insurance on file")
        expected.append(("no insurance on file", None, None, None, None, None))

    got = conn.execute(
        "SELECT item, coverage, policy_number, starts_on, expires_on, standing"
        " FROM trust_report_vendor_insurance(%s, %s) WHERE vendor_id = %s ORDER BY line",
        (pmc.pmc_id, on, pmc.vendor_id),
    ).fetchall()
    assert [tuple(row) for row in got] == expected


def test_a_vendors_coverage_is_the_policy_running_on_the_day_else_the_latest_to_run_out(
    database_url,
):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_insurance(conn)


ROOMS = ("Kitchen", "Bath", "Hall", "Den")
CONDITIONS = ("good", "fair", "poor", "damaged", "missing")
INSPECTIONS = st.lists(
    st.tuples(
        st.sampled_from(["move-in", "move-out"]),
        st.integers(min_value=0, max_value=3),  # days after Jan 1, so some share a day
        st.lists(
            st.tuples(st.sampled_from(ROOMS), st.booleans(), st.sampled_from(CONDITIONS)),
            min_size=1,
            max_size=4,
            unique_by=lambda room: room[0],
        ),
    ),
    max_size=5,
)


@given(inspections=INSPECTIONS)
def check_inspections(conn, inspections):
    pmc = make_pmc(conn)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    lease = open_lease(conn, pmc.pmc_id, unit, START, date(2026, 12, 31), "1500.00", [tenant])
    recorded = []  # (kind, day, created_at, id, [(room, condition)])
    for kind, days, rooms in inspections:
        on = START + timedelta(days=days)
        made, at = conn.execute(
            "INSERT INTO trust_inspections (pmc_id, lease_id, kind, inspected_on)"
            " VALUES (%s, %s, %s, %s) RETURNING id, created_at",
            (pmc.pmc_id, lease, kind, on),
        ).fetchone()
        items = [(room.lower() if low else room, condition) for room, low, condition in rooms]
        for line, (room, condition) in enumerate(items, start=1):
            conn.execute(
                "INSERT INTO trust_inspection_items (inspection_id, pmc_id, line, room, condition)"
                " VALUES (%s, %s, %s, %s, %s)",
                (made, pmc.pmc_id, line, room, condition),
            )
        recorded.append((kind, on, at, made, items))

    def latest(kind):
        of_kind = sorted((r for r in recorded if r[0] == kind), key=lambda r: r[3].hex)
        of_kind.sort(key=lambda r: (r[1], r[2]), reverse=True)
        return of_kind[0][4] if of_kind else []

    moved_in, moved_out = latest("move-in"), latest("move-out")
    at_out = {room.lower(): (room, condition) for room, condition in moved_out}
    expected = []
    for room, condition in moved_in:
        out = at_out.pop(room.lower(), (None, None))[1]
        expected.append((room, condition, out, None if out is None else out != condition))
    expected += [(room, None, condition, None) for room, condition in at_out.values()]
    event(f"{len(expected)} rooms")
    if any(changed for *_, changed in expected):
        event("a room changed")

    got = conn.execute(
        "SELECT room, move_in, move_out, changed FROM trust_report_inspection(%s, %s)"
        " WHERE item = 'room' ORDER BY line",
        (pmc.pmc_id, lease),
    ).fetchall()
    assert [tuple(row) for row in got] == expected


def test_an_inspection_report_lays_the_latest_of_each_kind_side_by_side(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_inspections(conn)
