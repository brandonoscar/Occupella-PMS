"""Property-based tests for rent and monthly charges once a month (migration 20261105000400;
docs/edge-cases.md M-034, M-035, D-025).

Hypothesis builds a company with random leases due on the first (some starting or ending inside
the two months, some with a monthly charge) and runs the rent run (`trust_run_rent`) for random
days of those months, in any order, mid-month ones included, as Run rent now lets an admin pick.

Invariants (ci/registry.toml maps them to this test):
  - a lease is charged at most one rent in a month, and a monthly charge at most once in a month,
    whatever days the run is run for;
  - no month is missed: a lease in effect on a day the run was run for has its rent in that month;
  - running every day again charges nothing new.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

import psycopg
from helpers import add_recurring_charge, add_tenant, add_unit, make_pmc, open_lease
from hypothesis import event, given
from hypothesis import strategies as st

START = date(2026, 10, 1)  # the first of two months, October and November
RENT = st.integers(min_value=10_000, max_value=400_000).map(lambda c: Decimal(c) / 100)
LEASES = st.lists(
    st.fixed_dictionaries(
        {
            "starts": st.integers(min_value=-40, max_value=55),  # days from October 1
            "days": st.none() | st.integers(min_value=5, max_value=90),
            "rent": RENT,
            "monthly": st.booleans(),
        }
    ),
    min_size=1,
    max_size=4,
)
DAYS = st.lists(st.integers(min_value=0, max_value=60), min_size=1, max_size=6)


def month_of(day):
    return (day.year, day.month)


@given(leases=LEASES, days=DAYS)
def check(conn, app_conn, leases, days):
    pmc = make_pmc(conn)
    prop = pmc.owners[0].property_id
    made = []
    for n, lease in enumerate(leases):
        unit = add_unit(conn, pmc.pmc_id, prop, f"Unit {n}")
        tenant = add_tenant(conn, pmc.pmc_id, prop, f"Tenant {n}")
        starts = START + timedelta(days=lease["starts"])
        ends = None if lease["days"] is None else starts + timedelta(days=lease["days"])
        lease_id = open_lease(conn, pmc.pmc_id, unit, starts, ends, str(lease["rent"]), [tenant])
        if lease["monthly"]:
            add_recurring_charge(conn, pmc.pmc_id, lease_id, "25.00", starts)
        made.append((lease_id, starts, ends))

    ran = [START + timedelta(days=d) for d in days]
    for day in ran:
        event("a mid-month run" if day.day != 1 else "a run on the first")
        app_conn.execute("SELECT trust_run_rent(%s, %s, %s)", (pmc.pmc_id, day, day))

    def charges():
        return conn.execute(
            "SELECT lease_id, due_on, kind, recurring_charge_id FROM trust_charges"
            " WHERE pmc_id = %s",
            (pmc.pmc_id,),
        ).fetchall()

    found = charges()
    rents = Counter((lease, month_of(due)) for lease, due, kind, rc in found if kind == "rent")
    monthly = Counter((rc, month_of(due)) for _, due, _, rc in found if rc is not None)
    assert all(n == 1 for n in rents.values()), rents
    assert all(n == 1 for n in monthly.values()), monthly
    for lease_id, starts, ends in made:
        for day in ran:
            if starts <= day and (ends is None or day <= ends):
                assert rents[(lease_id, month_of(day))] == 1

    for day in ran:
        app_conn.execute("SELECT trust_run_rent(%s, %s, %s)", (pmc.pmc_id, day, day))
    assert sorted(charges(), key=str) == sorted(found, key=str)


def test_rent_and_monthly_charges_are_charged_once_a_month_whatever_day_the_run_is_for(
    database_url,
):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        check(conn, app_conn)
