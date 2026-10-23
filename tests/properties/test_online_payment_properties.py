"""Property-based tests for rent paid online (src/pms/api/online_payments.py, migration
20261022000158).

Hypothesis starts any payments by card or bank debit on a lease that owes rent, and sends any
run of processor events (succeeded, failed, a dispute lost), some of them again:
  - an event is taken once, and only when it follows from where its payment stands;
  - what the lease owes less the prepaid rent it holds is what it owed at first less the money
    that came and stayed (succeeded and not returned): nothing is recorded twice or lost;
  - each payment recorded carries how it was paid.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date
from decimal import Decimal

import psycopg
from helpers import add_unit, charge, make_pmc, open_lease
from hypothesis import event, given
from hypothesis import strategies as st

from pms.api import online_payments, rent
from pms.payments.processor import Event

steps = st.lists(
    st.one_of(
        st.tuples(
            st.just("start"), st.integers(1, 200_000), st.sampled_from(["card", "bank debit"])
        ),
        st.tuples(
            st.just("event"),
            st.integers(0, 20),
            st.sampled_from(["succeeded", "failed", "returned"]),
            st.booleans(),  # the same event again
        ),
    ),
    min_size=1,
    max_size=16,
)


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]


@given(run=steps)
def check_payments(conn, run):
    pmc = make_pmc(conn, owners=1)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    tenant = tenant_of(conn, pmc)
    lease = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "1500.00", [tenant])
    for month in (1, 2):
        charge(conn, pmc.pmc_id, lease, date(2026, month, 1), "1500.00")
    first = rent.owed(conn, lease)
    payments: list[tuple[str, Decimal, str]] = []  # id, amount, method
    state: dict[str, str] = {}  # the model: started, succeeded, failed, returned
    sent: list[Event] = []
    for step in run:
        if step[0] == "start":
            amount = Decimal(step[1]) / 100
            (pid,) = conn.execute(
                "INSERT INTO trust_online_payments (pmc_id, lease_id, tenant_id, amount, method,"
                " processor) VALUES (%s, %s, %s, %s, %s, 'fake') RETURNING id",
                (pmc.pmc_id, lease, tenant, amount, step[2]),
            ).fetchone()
            conn.execute(
                "INSERT INTO trust_online_payment_steps (pmc_id, payment_id, step, reference)"
                " VALUES (%s, %s, 'started', %s)",
                (pmc.pmc_id, pid, f"ref-{pid}"),
            )
            payments.append((str(pid), amount, step[2]))
            state[str(pid)] = "started"
            continue
        if not payments:
            continue
        pid = payments[step[1] % len(payments)][0]
        _, _, outcome, repeat = step
        if repeat and sent:
            said = sent[step[1] % len(sent)]  # an event taken already
            expected = False
        else:
            said = Event(
                id=f"evt-{len(sent)}-{pid}",
                outcome=outcome,
                reference=None if outcome == "returned" else f"ref-{pid}",
                processor_payment=f"pay-{pid}" if outcome != "failed" else None,
                reason="declined" if outcome == "failed" else None,
            )
            now = state[pid]
            expected = (now == "started" and outcome != "returned") or (
                now == "succeeded" and outcome == "returned"
            )
        with conn.transaction():
            taken = online_payments.take(conn, "fake", said)
        assert taken == expected, (said, state[pid])
        if taken:
            event(f"taken: {said.outcome}")
            state[pid] = said.outcome
            sent.append(said)
        else:
            event("not taken")

    came = sum((a for p, a, _ in payments if state[p] == "succeeded"), Decimal(0))
    prepaid = conn.execute(
        "SELECT coalesce(sum(a.balance), 0) FROM pgledger_accounts AS a"
        " JOIN trust_ledger_accounts AS t ON t.ledger_account_id = a.id"
        " WHERE t.pmc_id = %s AND t.kind = 'prepaid_rent'",
        (pmc.pmc_id,),
    ).fetchone()[0]
    assert rent.owed(conn, lease) - prepaid == first - came
    for pid, _, method in payments:
        recorded = conn.execute(
            "SELECT DISTINCT t.metadata ->> 'method' FROM pgledger_transfers AS t"
            " WHERE t.metadata ->> 'key' = %s",
            (f"payment/{pid}",),
        ).fetchall()
        assert recorded == ([] if state[pid] in ("started", "failed") else [(method,)])


def test_a_payment_is_recorded_once_and_nothing_is_lost(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_payments(conn)
