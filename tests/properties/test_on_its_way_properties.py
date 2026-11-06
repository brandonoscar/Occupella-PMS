"""Property-based tests for what a balance autopay counts as on its way (src/pms/autopay.py
ON_ITS_WAY; docs/edge-cases.md P-067, P-068, H-145).

Hypothesis starts random payments online for a lease, each some days before the due date, some
reported paid (and waiting for staff, or in the books), failed, come back or expired, and some
with no word at all; then asks what a balance autopay pays for the due date.

Invariants (ci/registry.toml maps them to this test):
  - a balance autopay pays what the lease owes by the due date less what is on its way, up to its
    amount, never below zero;
  - on its way is a payment not in the books, not failed, come back or expired, that was reported
    paid or started within ON_ITS_WAY_DAYS before the due date: one whose report never came no
    longer lowers what the autopay pays.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
from helpers import add_unit, charge, make_pmc, open_lease, post
from hypothesis import event, given
from hypothesis import strategies as st

from pms import autopay

DUE = date(2026, 6, 1)
STARTED = st.lists(
    st.fixed_dictionaries(
        {
            "amount": st.integers(min_value=1, max_value=900).map(Decimal),
            "days_before": st.integers(min_value=-2, max_value=60),
            "word": st.sampled_from(
                [None, None, "paid", "paid in the books", "failed", "returned", "expired"]
            ),
        }
    ),
    max_size=5,
)


@given(started=STARTED, cap=st.integers(min_value=100, max_value=3000).map(Decimal))
def check(conn, app_conn, started, cap):
    pmc = make_pmc(conn, owners=1)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "1200.00", [tenant])
    charge(conn, pmc.pmc_id, lease, DUE, "1200.00")
    due_at = datetime.combine(DUE, datetime.min.time(), UTC)
    coming = Decimal(0)
    for each in started:
        payment = conn.execute(
            "INSERT INTO trust_online_payments (pmc_id, lease_id, tenant_id, amount, fee_payer,"
            " method, partner, partner_ref, created_at)"
            " VALUES (%s, %s, %s, %s, 'company', 'ach', 'fake', %s, %s) RETURNING id",
            (
                pmc.pmc_id,
                lease,
                tenant,
                each["amount"],
                f"fake_{uuid.uuid4().hex}",
                due_at - timedelta(days=each["days_before"]),
            ),
        ).fetchone()[0]
        word = each["word"]
        if word is not None:
            # A payment comes back only once it was paid.
            kind = "paid" if word.startswith("paid") else word
            kinds = ["paid", "returned"] if kind == "returned" else [kind]
            for kind in kinds:
                conn.execute(
                    "INSERT INTO trust_online_payment_events (pmc_id, payment_id, kind,"
                    " partner_event, partner_fee, happened_at) VALUES (%s, %s, %s, %s, %s, now())",
                    (
                        pmc.pmc_id,
                        payment,
                        kind,
                        f"evt_{kind}_{payment.hex}",
                        0 if kind == "paid" else None,
                    ),
                )
        if word == "paid in the books":
            # In the books under the payment's own key: no longer on its way (a deposit of the
            # tenant's prepaid rent stands in for the recording; only its key counts here).
            key = f"payment/{payment}"
            post(
                conn,
                pmc.pmc_id,
                key,
                [(pmc.operating_cash, pmc.prepaid_rent, each["amount"])],
                metadata={"key": key},
            )
        fresh = each["days_before"] <= autopay.ON_ITS_WAY_DAYS
        if word == "paid" or (word is None and fresh):
            coming += each["amount"]
        event(f"word: {word}, {'fresh' if fresh else 'old'}")

    balance = autopay.Active(
        None, lease, tenant, "balance", cap, "fake", due_at, DUE, None, "c", "m", None, "l", DUE
    )
    paid = autopay.to_pay(app_conn, pmc.pmc_id, balance, DUE)
    assert paid == max(min(Decimal("1200.00") - coming, cap), Decimal(0)), (started, coming)


def test_a_balance_autopay_counts_on_its_way_only_what_may_still_come(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        check(conn, app_conn)
