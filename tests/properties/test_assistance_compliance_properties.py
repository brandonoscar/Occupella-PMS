"""Property-based tests for keeping housing-assistance leases in compliance (migration
20261021000108; the founder's review q143).

Hypothesis writes a few leases, each with assistance in force, assistance ended or none, and
random income certifications and inspections (kinds, results, days and due days), then picks a day.
A model says what trust_report_assistance_compliance must print as of that day: one line for each
lease with assistance in force then, its latest certification and inspection in effect by that day
(the later recorded on the same day), where each stands (current, due within 120 days, overdue,
none on file; failed, reinspection due or overdue) and the totals. The report never prints income
or household size. Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date, timedelta
from decimal import Decimal

import psycopg
from helpers import add_authority, add_tenant, add_unit, assist, make_pmc, open_lease
from hypothesis import example, given
from hypothesis import strategies as st

FIRST = date(2024, 1, 1)
# Assistance that ended is 0 from this day.
ENDED = date(2025, 1, 1)
DAY = st.integers(0, 900)  # days after FIRST: all of them past
CERTIFICATION = st.tuples(
    st.sampled_from(["move-in", "annual", "interim"]), DAY, st.integers(1, 800)
)
INSPECTION = st.tuples(st.sampled_from(["passed", "failed"]), DAY, st.integers(1, 800))
LEASE = st.tuples(
    st.sampled_from(["in force", "ended", "never"]),
    st.lists(CERTIFICATION, max_size=4),
    st.lists(INSPECTION, max_size=4),
)


def standing(due: date | None, as_of: date) -> str:
    if due is None:
        return "none on file"
    if as_of > due:
        return "overdue"
    return "due within 120 days" if (due - as_of).days <= 120 else "current"


def inspected(result: str | None, due: date | None, as_of: date) -> str:
    if result == "failed":
        return "failed, reinspection overdue" if as_of > due else "failed, reinspection due"
    return standing(due, as_of)


def latest(records: list[tuple[str, date, date]], as_of: date) -> tuple[str, date, date] | None:
    """The record in effect by the day: the latest day, and of one day the last recorded."""
    by_day = [(record[1], n, record) for n, record in enumerate(records) if record[1] <= as_of]
    return max(by_day)[2] if by_day else None


@given(leases=st.lists(LEASE, min_size=1, max_size=4), as_of=DAY)
# Each boundary of a standing: due the day itself, 120 and 121 days on, and a day past it.
@example(leases=[("in force", [("annual", 0, 500)], [("passed", 0, 500)])], as_of=500)
@example(leases=[("in force", [("annual", 0, 500)], [("failed", 0, 500)])], as_of=501)
@example(leases=[("in force", [], [("failed", 0, 500)])], as_of=500)
@example(leases=[("in force", [("move-in", 0, 500)], [("passed", 0, 620)])], as_of=500)
@example(leases=[("in force", [("move-in", 0, 621)], [("passed", 0, 621)])], as_of=500)
# Of two certifications on one day, the later recorded holds; one to come doesn't count yet.
@example(
    leases=[("in force", [("annual", 10, 400), ("interim", 10, 100), ("annual", 200, 300)], [])],
    as_of=50,
)
# Assistance ended before the day: left out; none at all: refused.
@example(
    leases=[("ended", [("annual", 0, 100)], []), ("never", [("annual", 0, 100)], [])], as_of=600
)
def check(conn, leases, as_of):
    pmc = make_pmc(conn)
    authority = add_authority(conn, pmc.pmc_id)
    prop = pmc.owners[0].property_id
    day = FIRST + timedelta(days=as_of)
    expected = []
    for n, (assisted, certifications, inspections) in enumerate(leases, start=1):
        lease = open_lease(
            conn, pmc.pmc_id, add_unit(conn, pmc.pmc_id, prop, f"Unit {n}"), FIRST, None,
            "1500.00", [add_tenant(conn, pmc.pmc_id, prop, f"Tenant {n}")],
        )  # fmt: skip
        if assisted == "never":
            for kind, effective, after in certifications[:1]:
                try:
                    with conn.transaction():
                        certify(conn, pmc.pmc_id, lease, kind, effective, after)
                except psycopg.errors.CheckViolation as refused:
                    assert "no housing assistance on file" in str(refused)
                else:
                    raise AssertionError("a lease without assistance took a certification")
            continue
        assist(conn, pmc.pmc_id, lease, authority, FIRST, "700.00")
        if assisted == "ended":
            assist(conn, pmc.pmc_id, lease, authority, ENDED, "0.00")
        certified: list[tuple[str, date, date]] = []
        for kind, effective, after in certifications:
            on = FIRST + timedelta(days=effective)
            if any(c[0] == kind and c[1] == on for c in certified):
                continue  # once per lease, day and kind
            certify(conn, pmc.pmc_id, lease, kind, effective, after)
            certified.append((kind, on, on + timedelta(days=after)))
        seen: list[tuple[str, date, date]] = []
        for result, when, after in inspections:
            on = FIRST + timedelta(days=when)
            if any(i[0] == result and i[1] == on for i in seen):
                continue  # once per lease, day and result
            conn.execute(
                "INSERT INTO trust_assistance_inspections (pmc_id, lease_id,"
                " housing_authority_id, inspected_on, result, due_on)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (pmc.pmc_id, lease, authority, on, result, on + timedelta(days=after)),
            )
            seen.append((result, on, on + timedelta(days=after)))
        if assisted == "ended" and day >= ENDED:
            continue
        c, i = latest(certified, day), latest(seen, day)
        expected.append(
            (
                f"Unit {n}",
                f"Tenant {n}",
                None if c is None else f"{c[0]} {c[1]:%m/%d/%Y}",
                None if c is None else c[2],
                standing(None if c is None else c[2], day),
                None if i is None else f"{i[0]} {i[1]:%m/%d/%Y}",
                None if i is None else i[2],
                inspected(None if i is None else i[0], None if i is None else i[2], day),
            )
        )

    rows = conn.execute(
        "SELECT line, item, unit, tenants, certified, recertify_by, recertification, inspected,"
        " inspect_by, inspection FROM trust_report_assistance_compliance(%s, %s) ORDER BY line",
        (pmc.pmc_id, day),
    ).fetchall()

    assert [row[0] for row in rows] == list(range(1, len(expected) + 4))
    assert [row[2:] for row in rows if row[1] == "assisted lease"] == expected
    certify_now = sum(e[4] in ("overdue", "none on file") for e in expected)
    certify_soon = sum(e[4] == "due within 120 days" for e in expected)
    inspect_now = sum(
        e[7] in ("overdue", "none on file", "failed, reinspection overdue") for e in expected
    )
    inspect_soon = sum(
        e[7] in ("due within 120 days", "failed, reinspection due") for e in expected
    )
    assert rows[-1][1:] == (
        "total", None, None, None, None,
        f"{certify_now} to certify now, {certify_soon} within 120 days",
        None, None, f"{inspect_now} to inspect now, {inspect_soon} within 120 days or failed",
    )  # fmt: skip
    # Nothing of a household's income or size reaches the report.
    printed = " ".join(str(value) for row in rows for value in row if value is not None)
    assert "18000" not in printed and "Household" not in printed


def certify(conn, pmc_id, lease, kind, effective, after):
    on = FIRST + timedelta(days=effective)
    conn.execute(
        "INSERT INTO trust_income_certifications (pmc_id, lease_id, kind, effective_on,"
        " household_size, annual_income, next_due_on) VALUES (%s, %s, %s, %s, 3, %s, %s)",
        (pmc_id, lease, kind, on, Decimal("18000.00"), on + timedelta(days=after)),
    )


def test_the_compliance_report_says_where_each_assisted_lease_stands_on_any_day(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
