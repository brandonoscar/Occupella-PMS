"""Property-based tests for renters insurance on file (migration 20261015000062).

Hypothesis records a lease's renters insurance terms (required or not, a minimum, from a day)
and the tenant's policies (each starting some day, running some more, with or without a stated
liability coverage), in random order, and asks the report about a random day. A model picks what
counts; the report must agree:
  - the terms in force are the latest starting on the day or before (then the latest recorded);
  - the policy that counts is, of those started by the day, the one that runs out last (then
    the latest recorded); with none, none on file;
  - its standing is expired, expires today, expires in N day(s) up to 30, else current; and it is
    below the minimum exactly when the terms ask one and its coverage is less, or not stated.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date, timedelta
from decimal import Decimal

import psycopg
from helpers import add_unit, make_pmc, open_lease
from hypothesis import event, given
from hypothesis import strategies as st

START = date(2026, 1, 1)
COVERAGE = st.none() | st.sampled_from(
    [Decimal("50000.00"), Decimal("100000.00"), Decimal("300000.00")]
)
TERMS = st.lists(
    st.tuples(st.booleans(), COVERAGE, st.integers(min_value=0, max_value=120)), max_size=3
)
POLICIES = st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=120), st.integers(min_value=0, max_value=90), COVERAGE
    ),
    max_size=4,
)


def standing(expires, day):
    if expires < day:
        return "expired"
    if expires == day:
        return "expires today"
    days = (expires - day).days
    return f"expires in {days} day{'' if days == 1 else 's'}" if days <= 30 else "current"


@given(terms=TERMS, policies=POLICIES, day=st.integers(min_value=0, max_value=180))
def check(conn, terms, policies, day):
    on = START + timedelta(days=day)
    pmc = make_pmc(conn)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, START, None, "1500.00", [tenant])
    recorded_terms = []
    for n, (required, minimum, starts) in enumerate(terms):
        minimum = minimum if required else None
        conn.execute(
            "INSERT INTO trust_renters_insurance_terms (pmc_id, lease_id, required,"
            " minimum_liability, starts_on) VALUES (%s, %s, %s, %s, %s)",
            (pmc.pmc_id, lease, required, minimum, START + timedelta(days=starts)),
        )
        recorded_terms.append((START + timedelta(days=starts), n, required, minimum))
    recorded = []
    for n, (starts, runs, limit) in enumerate(policies):
        begins = START + timedelta(days=starts)
        conn.execute(
            "INSERT INTO trust_renters_insurance (pmc_id, lease_id, carrier, policy_number,"
            " starts_on, expires_on, liability_limit) VALUES (%s, %s, 'Carrier A', %s, %s, %s, %s)",
            (pmc.pmc_id, lease, f"RI-{n}", begins, begins + timedelta(days=runs), limit),
        )
        recorded.append((begins + timedelta(days=runs), n, f"RI-{n}", limit, begins))

    in_force = max((t for t in recorded_terms if t[0] <= on), default=None)
    required = in_force[2] if in_force else False
    minimum = in_force[3] if in_force else None
    started = [p for p in recorded if p[4] <= on]
    chosen = max(started, default=None)  # the latest to run out, then the latest recorded
    if chosen is None:
        event("none on file")
        expected = (required, minimum, None, "none on file", False)
    else:
        below = minimum is not None and (chosen[3] is None or chosen[3] < minimum)
        event("below the minimum" if below else standing(chosen[0], on).split(" ")[0])
        expected = (required, minimum, chosen[2], standing(chosen[0], on), below)

    got = conn.execute(
        "SELECT required, minimum_liability, policy_number, standing, below_minimum"
        " FROM trust_report_renters_insurance(%s, %s)",
        (pmc.pmc_id, on),
    ).fetchall()
    assert [tuple(row) for row in got] == [expected]


def test_the_policy_running_last_counts_against_the_terms_in_force(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
