"""Property-based tests for what staff record under a request id (src/pms/api/rent.py made_before;
docs/edge-cases.md C-025, C-026).

Hypothesis sends staff's Enter charge and Add recurring charge a random run of requests to two
leases: fees and credits of a few amounts and days, some a retry of an earlier request id with the
same charge, some an earlier request id with another.

Invariants (ci/registry.toml maps them to this test):
  - the same request id with the same charge returns what it made the first time, and makes
    nothing more;
  - the same request id with anything else (another amount, day, kind or lease) is refused,
    making nothing;
  - each request id accepted made exactly one charge, or one recurring charge, as it was asked
    (each panel's ids are its own: Enter charge's and Add recurring charge's never meet).

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import psycopg
from fastapi import HTTPException
from helpers import add_unit, make_pmc, open_lease
from hypothesis import event, given
from hypothesis import strategies as st

from pms.api import rent

DAYS = [date(2026, 3, 2), date(2026, 3, 3)]
SENT = st.lists(
    st.fixed_dictionaries(
        {
            "what": st.sampled_from(["charge", "recurring"]),
            "lease": st.integers(min_value=0, max_value=1),
            "kind": st.sampled_from(["fee", "credit"]),
            "amount": st.sampled_from(["25.00", "40.50"]),
            "day": st.integers(min_value=0, max_value=1),
            # None: a new request id; otherwise the request id of an earlier request sent.
            "retry": st.none() | st.integers(min_value=0, max_value=9),
        }
    ),
    min_size=1,
    max_size=10,
)


@given(sent=SENT)
def check(conn, app_conn, sent):
    pmc = make_pmc(conn)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    leases = []
    for n in range(2):
        unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, f"Unit {n}")
        leases.append(
            open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "1200.00", [tenant])
        )
    session = SimpleNamespace(pmc_id=pmc.pmc_id)

    # The model: each request id accepted, by what it made, with what it asked and the id made.
    made: dict[tuple[str, uuid.UUID], tuple] = {}
    ids: list[uuid.UUID] = []
    for each in sent:
        asked = (each["what"], each["lease"], each["kind"], each["amount"], each["day"])
        if each["retry"] is not None and each["retry"] < len(ids):
            request = ids[each["retry"]]
        else:
            request = uuid.uuid4()
        ids.append(request)
        lease = leases[each["lease"]]
        try:
            with app_conn.transaction():
                if each["what"] == "charge":
                    created = rent.add_charge(
                        session,
                        app_conn,
                        lease,
                        rent.NewCharge(
                            request_id=request,
                            kind=each["kind"],
                            amount=Decimal(each["amount"]),
                            due_on=DAYS[each["day"]],
                            memo="Key copy",
                        ),
                    )
                else:
                    created = rent.add_recurring_charge(
                        session,
                        app_conn,
                        lease,
                        rent.NewRecurringCharge(
                            request_id=request,
                            kind=each["kind"],
                            amount=Decimal(each["amount"]),
                            starts_on=DAYS[each["day"]],
                            memo="Parking",
                        ),
                    )
        except HTTPException as refused:
            # The same id asked for something else.
            assert refused.status_code == 409 and (each["what"], request) in made
            assert made[each["what"], request][0] != asked, (each, made)
            event("another request under an id")
            continue
        if (each["what"], request) in made:
            assert made[each["what"], request] == (asked, created.id), (each, made)
            event("a retry")
        else:
            made[each["what"], request] = (asked, created.id)
            event("a request")

    charges = conn.execute(
        "SELECT request_id, id, lease_id, kind, amount, due_on FROM trust_charges"
        " WHERE pmc_id = %s AND request_id IS NOT NULL",
        (pmc.pmc_id,),
    ).fetchall()
    recurring = conn.execute(
        "SELECT request_id, id, lease_id, kind, amount, starts_on FROM trust_recurring_charges"
        " WHERE pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall()
    found = {
        (what, request): (
            (what, leases.index(lease), kind, f"{amount:.2f}", DAYS.index(day)),
            made_id,
        )
        for what, rows in (("charge", charges), ("recurring", recurring))
        for request, made_id, lease, kind, amount, day in rows
    }
    assert len(charges) + len(recurring) == len(found)
    assert found == made


def test_a_request_id_sent_again_makes_one_charge_and_another_under_it_is_refused(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        check(conn, app_conn)
