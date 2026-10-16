"""Property-based tests for archiving a property or a unit (migration 20261016000063, ADR 0005).

Hypothesis builds a property with up to three units, each with a lease or none (a first day and
maybe a last day, around today), maybe money in its owner's account, and then archives and brings
back the property and its units in a random order. A model says which of those requests stand;
the database must take exactly those, refuse every other one, and say after each what the model
says (`trust_is_archived`):
  - a unit is archived only while no lease of it is in effect or to come (no last day, or one
    today or later), and not when it already is; it comes back only when it is archived;
  - a property is archived only while none of its units' leases is in effect or to come and its
    owner's account holds nothing, and not when it already is; it comes back only when archived.

A bill not paid holding a property back is an example test (tests/test_archivals.py).
Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import timedelta
from decimal import Decimal

import psycopg
from helpers import add_unit, make_pmc, open_lease, transfer_batch
from hypothesis import event, given
from hypothesis import strategies as st

LEASE = st.none() | st.tuples(
    st.integers(min_value=-90, max_value=30),  # first day, from today
    st.none() | st.integers(min_value=0, max_value=120),  # days after it, to the last day
)
REQUESTS = st.lists(
    st.tuples(st.sampled_from(["property", 0, 1, 2]), st.booleans()), min_size=1, max_size=8
)


@given(
    leases=st.lists(LEASE, min_size=1, max_size=3),
    money=st.sampled_from([Decimal("0.00"), Decimal("0.01"), Decimal("250.00")]),
    requests=REQUESTS,
)
def check(conn, leases, money, requests):
    today = conn.execute("SELECT (now() AT TIME ZONE 'UTC')::date").fetchone()[0]
    pmc = make_pmc(conn)
    owner = pmc.owners[0]
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    units, open_now = [], []
    for n, lease in enumerate(leases):
        unit = add_unit(conn, pmc.pmc_id, owner.property_id, f"Unit {n + 1}")
        units.append(unit)
        if lease is None:
            open_now.append(False)
            continue
        starts = today + timedelta(days=lease[0])
        ends = None if lease[1] is None else starts + timedelta(days=lease[1])
        open_lease(conn, pmc.pmc_id, unit, starts, ends, "1500.00", [tenant])
        open_now.append(ends is None or ends >= today)
    if money:
        transfer_batch(conn, [(pmc.operating_cash, owner.account, money)])

    archived = {"property": False, **{n: False for n in range(len(units))}}
    for record, wanted in requests:
        if record != "property" and record >= len(units):
            continue
        if archived[record] == wanted:
            stands = False
        elif not wanted:
            stands = True
        elif record == "property":
            stands = not any(open_now) and money == 0
        else:
            stands = not open_now[record]
        target = (owner.property_id, None) if record == "property" else (None, units[record])
        try:
            conn.execute(
                "INSERT INTO trust_archivals (pmc_id, property_id, unit_id, archived)"
                " VALUES (%s, %s, %s, %s)",
                (pmc.pmc_id, *target, wanted),
            )
            took = True
        except psycopg.errors.CheckViolation:
            took = False
        event(f"{'archive' if wanted else 'bring back'} {'stands' if stands else 'refused'}")
        assert took == stands, (record, wanted)
        if took:
            archived[record] = wanted
        assert (
            conn.execute("SELECT trust_is_archived(%s, %s)", target).fetchone()[0]
            is archived[record]
        )


def test_a_record_is_archived_only_while_nothing_is_open_on_it(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
