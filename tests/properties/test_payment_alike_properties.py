"""Property-based tests for a payment like one recorded (src/pms/api/rent.py refuse_alike;
docs/edge-cases.md H-008, U-138).

Hypothesis sends staff's Receive payment a random run of tenant payments to two leases: amounts,
days, ways and check numbers drawn from a few, some a retry of an earlier request id, some saying
they are another payment though one alike is recorded.

Invariants (ci/registry.toml maps them to this test):
  - a payment like one recorded to its lease (the same amount received the same day, by the same
    way and check number where both say one) under another request id is refused, recording
    nothing, unless staff said it is another payment;
  - the same request id again returns the payment as it was recorded, never a second one;
  - a payment alike to one on another lease is its own;
  - every trust bank account ties out.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from datetime import date
from decimal import Decimal

import psycopg
from fastapi import HTTPException
from helpers import add_unit, bank_sums, make_pmc, open_lease
from hypothesis import event, given
from hypothesis import strategies as st

from pms.api import rent

DAYS = [date(2026, 3, 2), date(2026, 3, 3)]
SENT = st.lists(
    st.fixed_dictionaries(
        {
            "lease": st.integers(min_value=0, max_value=1),
            "amount": st.sampled_from(["100.00", "250.50", "900.00"]),
            "day": st.integers(min_value=0, max_value=1),
            # None: a new request id; otherwise the request id of an earlier payment sent.
            "method": st.sampled_from([None, "check", "cash"]),
            "reference": st.sampled_from([None, "2101", "2102"]),
            "retry": st.none() | st.integers(min_value=0, max_value=9),
            "another": st.booleans(),
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

    def same(one, other):
        return one is None or other is None or one == other

    # The model: each request id recorded, with what it was (lease, amount, day, way, check).
    recorded: dict[uuid.UUID, tuple] = {}
    ids: list[uuid.UUID] = []
    for each in sent:
        what = (each["lease"], each["amount"], each["day"], each["method"], each["reference"])
        if each["retry"] is not None and each["retry"] < len(ids):
            request = ids[each["retry"]]
        else:
            request = uuid.uuid4()
        ids.append(request)
        alike = any(
            w[:3] == what[:3] and same(w[3], what[3]) and same(w[4], what[4])
            for r, w in recorded.items()
            if r != request
        )
        try:
            with app_conn.transaction():
                rent.record_payment(
                    app_conn,
                    pmc.pmc_id,
                    leases[each["lease"]],
                    request,
                    Decimal(each["amount"]),
                    DAYS[each["day"]],
                    reference=each["reference"],
                    method=each["method"],
                    alike_refused=not each["another"],
                )
        except HTTPException as refused:
            event("refused")
            if request in recorded:  # the same id with another payment: refused as before
                assert refused.status_code == 409 and recorded[request][:3] != what[:3]
                continue
            assert alike and not each["another"], (each, recorded)
            assert refused.status_code == 409
            continue
        except psycopg.errors.UniqueViolation:
            # this request id was used for a different payment
            assert request in recorded and recorded[request][:3] != what[:3]
            event("a request id used for another payment")
            continue
        if request in recorded:
            # A retry; how it came and its check number describe it and may differ.
            assert recorded[request][:3] == what[:3]
            event("a retry")
        else:
            assert not alike or each["another"], (each, recorded)
            event("another payment alike" if alike else "a payment")
            recorded[request] = what

    found = conn.execute(
        "SELECT count(DISTINCT metadata ->> 'key') FROM pgledger_transfers"
        " WHERE metadata ->> 'kind' = 'tenant payment' AND metadata ->> 'lease_id' = ANY(%s)",
        ([str(lease) for lease in leases],),
    ).fetchone()[0]
    assert found == len(recorded)
    assert all(total == 0 for total in bank_sums(conn, pmc.pmc_id).values())


def test_a_payment_like_one_recorded_is_recorded_only_when_staff_say_it_is_another(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        check(conn, app_conn)
