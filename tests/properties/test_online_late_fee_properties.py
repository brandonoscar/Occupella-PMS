"""Property-based tests for late fees on rent paid online (migration 20261113000100).

A payment online counts for late fees from the day the tenant (or their autopay) started it, not
the day the partner reports it paid (docs/edge-cases.md D-080, D-081). Hypothesis gives a lease a
rent due twelve days ago with a few days' grace, then has the tenant start payments online at
random moments from the day before it was due to the last day of its grace, each reported paid,
failed or expired at a random moment up to three days later, some paid ones coming back a while
after. A late fee run goes every night from the day the rent fell due, each reading the reports
made before it, as the nightly run does. A model follows each payment as the books match it.

Invariants:
  - while a payment the tenant started within grace is still on its way to the books, no late
    fee is charged, however long it takes;
  - once none is, the rent is charged a late fee exactly when what those payments paid of it,
    less what came back by the end of its grace, left part of it open: so a payment started
    within grace that covers the rent never draws a late fee, however late it settles, and one
    that came back after the grace counts as paid then, as a staff payment that bounced does;
  - the fee records what was open, is the terms' flat fee, and is charged once.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import psycopg
from helpers import add_late_fee_policy, add_unit, assess_late_fees, charge, make_pmc, open_lease
from hypothesis import event, given
from hypothesis import strategies as st

from pms import payments
from pms.api import online_payments as op

TODAY = datetime.now(UTC).date()
DUE = TODAY - timedelta(days=12)
RENT = Decimal("1000.00")
FLAT = Decimal("50.00")
HOUR = timedelta(hours=1)

PAYMENT = st.tuples(
    st.integers(min_value=1, max_value=1300).map(Decimal),  # dollars
    st.integers(min_value=0, max_value=10_000),  # when it starts, within the window
    st.sampled_from(["paid", "failed", "expired", "returned"]),
    st.integers(min_value=1, max_value=72),  # hours until the partner reports how it went
    st.integers(min_value=1, max_value=48),  # hours more until a paid one comes back
)


def midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), UTC)


@dataclass
class Paying:
    id: uuid.UUID
    amount: Decimal
    started: datetime
    outcome: str
    settles: datetime  # when the partner reports paid, failed or expired
    returns: datetime | None  # when a paid one comes back
    matched: Decimal = Decimal(0)  # what it paid of the rent


@given(grace=st.integers(min_value=0, max_value=5), started=st.lists(PAYMENT, max_size=4))
def check(conn, grace, started):
    pmc = make_pmc(conn, owners=1)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    add_late_fee_policy(
        conn, pmc.pmc_id, pmc.owners[0].property_id, DUE - timedelta(days=60), grace, str(FLAT)
    )
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, DUE - timedelta(days=30), None, "1000.00", [tenant])
    rent = charge(conn, pmc.pmc_id, lease, DUE, RENT)
    # From the day before the rent was due to the last hour of its grace (the company's day is
    # UTC's here, as make_pmc sets it).
    first = midnight(DUE - timedelta(days=1))
    span = int((midnight(DUE + timedelta(days=grace + 1)) - first) / HOUR)
    paying = []
    for dollars, at, outcome, after, back in started:
        when = first + (at % span) * HOUR
        settles = when + after * HOUR
        paying.append(
            Paying(
                uuid.uuid4(),
                dollars,
                when,
                outcome,
                settles,
                settles + back * HOUR if outcome == "returned" else None,
            )
        )
        conn.execute(
            "INSERT INTO trust_online_payments (id, pmc_id, lease_id, tenant_id, amount,"
            " tenant_fee, fee_payer, method, partner, partner_ref, created_at)"
            " VALUES (%s, %s, %s, %s, %s, 0, 'company', 'ach', 'fake', %s, %s)",
            (paying[-1].id, pmc.pmc_id, lease, tenant, dollars, f"fake_{uuid.uuid4().hex}", when),
        )

    # The partner's reports in the order they come.
    reports = sorted(
        [(p.settles, "paid" if p.outcome == "returned" else p.outcome, p) for p in paying]
        + [(p.returns, "returned", p) for p in paying if p.returns is not None],
        key=lambda r: r[0],
    )
    open_now = RENT
    cutoff = midnight(DUE + timedelta(days=grace + 1))
    day = DUE + timedelta(days=1)
    while day <= TODAY + timedelta(days=1):
        while reports and reports[0][0] < midnight(day):
            happened, kind, p = reports.pop(0)
            # A paid report carries the partner's fee (none here: the fee leaves trust from the
            # company's account and changes nothing of the rent).
            fee = Decimal("0.00") if kind == "paid" else None
            report = payments.Event(p.id, kind, f"evt_{uuid.uuid4().hex}", happened, fee)
            assert op.apply(conn, report, "fake")
            if kind == "paid":
                p.matched = min(p.amount, open_now)
                open_now -= p.matched
            elif kind == "returned":
                open_now += p.matched
        assess_late_fees(conn, pmc.pmc_id, day)
        fee = conn.execute(
            "SELECT unpaid, fee FROM trust_late_fees WHERE rent_charge_id = %s", (rent,)
        ).fetchone()
        on_its_way = [p for p in paying if p.settles >= midnight(day)]
        if on_its_way:
            # A payment started within grace still on its way: whether the rent is late waits.
            assert fee is None, (day, on_its_way)
        day += timedelta(days=1)

    # Every report is in: what the payments paid of the rent counts from the day each was
    # started, less what came back by the end of the grace.
    counted = sum(
        (p.matched for p in paying if p.outcome in ("paid", "returned")), Decimal(0)
    ) - sum((p.matched for p in paying if p.returns is not None and p.returns < cutoff), Decimal(0))
    left = RENT - counted
    fees = conn.execute(
        "SELECT unpaid, fee FROM trust_late_fees WHERE rent_charge_id = %s", (rent,)
    ).fetchall()
    if left > 0:
        assert fees == [(left, FLAT)]
        event("late: the payments started in grace left part of the rent open")
    else:
        assert fees == []
        if any(p.settles >= cutoff for p in paying if p.outcome in ("paid", "returned")):
            event("covered by a payment that settled after the grace: no late fee")
        else:
            event("covered within the grace")
    if any(p.returns is not None and p.returns >= cutoff for p in paying):
        event("a payment came back after the grace")
    assert assess_late_fees(conn, pmc.pmc_id, TODAY + timedelta(days=2)) == 0


def test_a_payment_started_within_grace_draws_no_late_fee_however_late_it_settles(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
