"""Property-based tests for archiving a property, a unit, a tenant or an owner (migrations
20261016000063 and 20261016000064, ADR 0005).

Hypothesis builds a property with up to three units, each with a lease of its one tenant or none
(a first day and maybe a last day, around today), maybe money in its owner's account and in the
tenant's prepaid rent or deposit, and then archives and brings back the property, its units, the
tenant and the owner in a random order. A model says which of those requests stand; the database
must take exactly those, refuse every other one, and say after each what the model says
(`trust_is_archived`):
  - a unit is archived only while no lease of it is in effect or to come (no last day, or one
    today or later);
  - a property only while none of its units' leases is in effect or to come and its owner's
    account holds nothing;
  - a tenant only while on no lease in effect or to come and holding no prepaid rent or deposit;
  - an owner only while their account holds nothing;
  - none of them when it already is, and each comes back only when it is archived.

A bill not paid holding a property or an owner back is an example test (tests/test_archivals.py).
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
MONEY = st.sampled_from([Decimal("0.00"), Decimal("0.01"), Decimal("250.00")])
REQUESTS = st.lists(
    st.tuples(st.sampled_from(["property", "tenant", "owner", 0, 1, 2]), st.booleans()),
    min_size=1,
    max_size=10,
)


@given(
    leases=st.lists(LEASE, min_size=1, max_size=3),
    owner_money=MONEY,
    tenant_money=MONEY,
    held_as=st.sampled_from(["prepaid rent", "deposit"]),
    requests=REQUESTS,
)
def check(conn, leases, owner_money, tenant_money, held_as, requests):
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
    if owner_money:
        transfer_batch(conn, [(pmc.operating_cash, owner.account, owner_money)])
    if tenant_money:
        cash, held = (
            (pmc.operating_cash, pmc.prepaid_rent)
            if held_as == "prepaid rent"
            else (pmc.deposit_cash, pmc.tenant_deposit)
        )
        transfer_batch(conn, [(cash, held, tenant_money)])

    targets = {
        "property": (owner.property_id, None, None, None),
        "tenant": (None, None, tenant, None),
        "owner": (None, None, None, owner.owner_id),
        **{n: (None, unit, None, None) for n, unit in enumerate(units)},
    }
    nothing_open = {
        "property": not any(open_now) and owner_money == 0,
        "tenant": not any(open_now) and tenant_money == 0,
        "owner": owner_money == 0,
        **{n: not open_now[n] for n in range(len(units))},
    }
    archived = {record: False for record in targets}
    for record, wanted in requests:
        if record not in targets:
            continue
        # Never what it already is; brought back whenever; archived only with nothing open.
        stands = archived[record] != wanted and (not wanted or nothing_open[record])
        try:
            conn.execute(
                "INSERT INTO trust_archivals (pmc_id, property_id, unit_id, tenant_id, owner_id,"
                " archived) VALUES (%s, %s, %s, %s, %s, %s)",
                (pmc.pmc_id, *targets[record], wanted),
            )
            took = True
        except psycopg.errors.CheckViolation:
            took = False
        kind = record if isinstance(record, str) else "unit"
        asked = "archive" if wanted else "bring back"
        event(f"{kind}: {asked} {'stands' if stands else 'refused'}")
        assert took == stands, (record, wanted)
        if took:
            archived[record] = wanted
        assert (
            conn.execute("SELECT trust_is_archived(%s, %s, %s, %s)", targets[record]).fetchone()[0]
            is archived[record]
        )


def test_a_record_is_archived_only_while_nothing_is_open_on_it(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
