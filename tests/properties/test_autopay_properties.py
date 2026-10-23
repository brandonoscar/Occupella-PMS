"""Property-based tests for autopay (src/pms/api/autopay.py, migration 20261022000159).

Hypothesis charges a lease any fees in any months, stops and sets autopay up again, and runs the
autopay charge on any days, again and again:
  - an autopay charges a due date once, and only while it is on, its rent is due and the lease
    owes something, for exactly what the lease owes then;
  - every charge is recorded as a payment (the fake processor pays at once), so what was charged
    is what was paid and the lease owes what the model says.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date
from decimal import Decimal

import psycopg
from helpers import add_unit, charge, make_pmc, open_lease
from hypothesis import event, given
from hypothesis import strategies as st

from pms.api import autopay, rent
from pms.payments.processor import FakeProcessor

MONTHS = [date(2026, m, 1) for m in (1, 2, 3)]

steps = st.lists(
    st.one_of(
        st.tuples(st.just("fee"), st.sampled_from(MONTHS), st.integers(1, 90_000)),
        st.tuples(st.just("run"), st.sampled_from(MONTHS), st.integers(0, 27)),
        st.tuples(st.just("stop")),
        st.tuples(st.just("start")),
    ),
    min_size=1,
    max_size=14,
)


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]


def set_up(conn, pmc, lease, tenant):
    (made,) = conn.execute(
        "INSERT INTO trust_autopay (pmc_id, lease_id, tenant_id, method, processor)"
        " VALUES (%s, %s, %s, 'card', 'fake') RETURNING id",
        (pmc.pmc_id, lease, tenant),
    ).fetchone()
    for kind, reference, label in (
        ("started", f"fake_setup_{made}", None),
        ("saved", f"fake_saved_{made}", "Card ••••4242"),
    ):
        conn.execute(
            "INSERT INTO trust_autopay_steps (pmc_id, autopay_id, step, reference, label)"
            " VALUES (%s, %s, %s, %s, %s)",
            (pmc.pmc_id, made, kind, reference, label),
        )
    return made


@given(run=steps)
def check_autopay(conn, run):
    pmc = make_pmc(conn, owners=1)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    tenant = tenant_of(conn, pmc)
    lease = open_lease(conn, pmc.pmc_id, unit, MONTHS[0], None, "1500.00", [tenant])
    for month in MONTHS:
        charge(conn, pmc.pmc_id, lease, month, "1500.00")
    owed = Decimal("4500.00")  # the model
    on: str | None = set_up(conn, pmc, lease, tenant)
    charged: set[tuple[str, date]] = set()
    paid = Decimal(0)
    for step in run:
        if step[0] == "fee":
            amount = Decimal(step[2]) / 100
            charge(conn, pmc.pmc_id, lease, step[1], amount, "fee")
            owed += amount
        elif step[0] == "stop" and on is not None:
            conn.execute(
                "INSERT INTO trust_autopay_steps (pmc_id, autopay_id, step, reason)"
                " VALUES (%s, %s, 'stopped', 'the tenant stopped it')",
                (pmc.pmc_id, on),
            )
            on = None
        elif step[0] == "start" and on is None:
            on = set_up(conn, pmc, lease, tenant)
        elif step[0] == "run":
            day = step[1].replace(day=1 + step[2])
            expected = on is not None and (on, step[1]) not in charged and owed > 0
            started = autopay.charge_due(conn, pmc.pmc_id, day, FakeProcessor())
            assert started == (1 if expected else 0), (day, owed, on)
            if expected:
                event("charged")
                charged.add((on, step[1]))
                paid += owed
                owed = Decimal(0)
            else:
                event("nothing to charge")
        assert rent.owed(conn, lease) == owed

    recorded = conn.execute(
        "SELECT coalesce(sum(p.amount), 0), count(*) FROM trust_online_payments AS p"
        " JOIN trust_online_payment_steps AS s ON s.payment_id = p.id AND s.step = 'succeeded'"
        " WHERE p.lease_id = %s",
        (lease,),
    ).fetchone()
    assert recorded == (paid, len(charged))


def test_autopay_charges_a_due_date_once_for_what_the_lease_owes(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_autopay(conn)
