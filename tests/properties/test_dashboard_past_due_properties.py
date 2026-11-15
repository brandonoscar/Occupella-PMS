"""Property-based test of the Dashboard's past due figures (src/pms/api/dashboard.py, issue #231).

The Dashboard reads what is past due, and how many tenants are on the leases that owe it, for the
day and the same day a month before in one query (`dashboard.past_due_on`) instead of running
the delinquency report once a day. Hypothesis gives a company leases of one or two tenants,
charges them rent and monthly fees (of no type, a type the owner keeps and one the company
keeps) for a month or more, maybe ends a lease in a month already charged (so its lease end's
credits come off its rent and fees, a company-kept fee's off that fee: migration
20261107000100), adds fees and credits of its own, then pays charges in full or in parts at
random times (at midnight too), waives parts of them and bounces payments, in any order. The
days asked are random ones, the same day a month before, and each day money moved and the day
before it (a payment at midnight counts from its own day).

Invariant (ci/registry.toml maps it to this test): for any days asked, together or one at a
time, each day's figures are exactly trust_report_delinquency's for that day: its total, and the
tenants named on its lease lines.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import psycopg
from helpers import (
    add_tenant,
    add_unit,
    apply_payment,
    charge,
    end_lease,
    make_pmc,
    open_lease,
    reverse_payment,
    transfer_batch,
)
from hypothesis import event, given
from hypothesis import strategies as st

from pms.api.dashboard import month_before, past_due_on

BASE = date(2025, 1, 1)
DAY = timedelta(days=1)
CENTS = st.integers(min_value=1, max_value=200_000).map(lambda c: Decimal(c) / 100)
TYPES = st.sampled_from([None, "owner type", "company type"])
# A day, as days after BASE, and seconds into it (the first and last seconds often).
DAYS = st.integers(min_value=-3, max_value=130)
SECONDS = st.one_of(st.sampled_from([0, 86_399]), st.integers(min_value=0, max_value=86_399))
LEASES = st.lists(
    st.fixed_dictionaries(
        {
            "tenants": st.integers(min_value=1, max_value=2),
            "rent": CENTS,
            "monthly_fee": st.none() | st.tuples(CENTS, TYPES),
        }
    ),
    min_size=1,
    max_size=3,
)
ACTIONS = st.lists(
    st.one_of(
        st.tuples(st.just("fee"), st.integers(0, 2), DAYS, CENTS, TYPES),
        st.tuples(st.just("credit"), st.integers(0, 2), DAYS, CENTS),
        # A part of a charge, or (None) all that is left of it.
        st.tuples(st.just("pay"), st.integers(0, 40), st.none() | CENTS, DAYS, SECONDS),
        st.tuples(st.just("waive"), st.integers(0, 40), CENTS, DAYS),
        st.tuples(st.just("bounce"), st.integers(0, 40), DAYS, SECONDS),
    ),
    max_size=25,
)


def at(day: int, seconds: int) -> datetime:
    return datetime.combine(BASE + day * DAY, time(0), tzinfo=UTC) + timedelta(seconds=seconds)


def reported(conn, pmc_id, as_of: date) -> tuple[Decimal, int]:
    """The delinquency report's total, and the tenants named on its lease lines."""
    rows = conn.execute(
        "SELECT item, tenants, total FROM trust_report_delinquency(%s, %s)"
        " WHERE item IN ('lease', 'total')",
        (pmc_id, as_of),
    ).fetchall()
    total = sum((t for item, _, t in rows if item == "total"), Decimal(0))
    tenants = sum(len(names.split(", ")) for item, names, _ in rows if item == "lease" and names)
    return total, tenants


def pay_into(conn, pmc, charge_id, transfer_source, amount, when):
    """A payment of a charge: into the owner's account, or the company's fee account for a fee
    the company keeps (whichever the books take). The transfer, or None if neither took it."""
    for target in (pmc.owners[0].account, pmc.pmc_income):
        try:
            with conn.transaction():
                (moved,) = transfer_batch(conn, [(transfer_source, target, amount)], when)
                apply_payment(conn, pmc.pmc_id, charge_id, moved, amount)
        except psycopg.errors.RaiseException:  # not this charge's payee
            continue
        return moved, target
    return None


@given(
    leases=LEASES,
    months=st.integers(min_value=1, max_value=3),
    ended=st.none() | st.tuples(st.integers(0, 2), st.integers(0, 27)),
    actions=ACTIONS,
    days=st.lists(DAYS, min_size=1, max_size=3),
)
def check(conn, app_conn, leases, months, ended, actions, days):
    pmc = make_pmc(conn)
    prop = pmc.owners[0].property_id
    types = {
        name: conn.execute(
            "INSERT INTO trust_charge_types (pmc_id, name) VALUES (%s, %s) RETURNING id",
            (pmc.pmc_id, name),
        ).fetchone()[0]
        for name in ("owner type", "company type")
    }
    conn.execute(
        "INSERT INTO trust_charge_type_payees (pmc_id, charge_type_id, payee)"
        " VALUES (%s, %s, 'company')",
        (pmc.pmc_id, types["company type"]),
    )
    lease_ids = []
    for n, terms in enumerate(leases):
        unit = add_unit(conn, pmc.pmc_id, prop, f"Unit {n + 1}")
        tenants = [
            add_tenant(conn, pmc.pmc_id, prop, f"Tenant {k + 1} of Unit {n + 1}")
            for k in range(terms["tenants"])
        ]
        lease = open_lease(conn, pmc.pmc_id, unit, BASE, None, terms["rent"], tenants)
        lease_ids.append(lease)
        if terms["monthly_fee"] is not None:
            amount, kind = terms["monthly_fee"]
            conn.execute(
                "INSERT INTO trust_recurring_charges (pmc_id, lease_id, kind, amount, memo,"
                " starts_on, charge_type_id) VALUES (%s, %s, 'fee', %s, 'Monthly fee', %s, %s)",
                (pmc.pmc_id, lease, amount, BASE, types.get(kind)),
            )
    first = BASE
    for _ in range(months):
        conn.execute("SELECT trust_charge_rent_due(%s, %s)", (pmc.pmc_id, first))
        conn.execute("SELECT trust_charge_recurring_due(%s, %s)", (pmc.pmc_id, first))
        last_month, first = first, (first + 32 * DAY).replace(day=1)
    if ended is not None:
        # Ended on a day of the last month charged: its lease end's credits.
        end_lease(
            conn, pmc.pmc_id, lease_ids[ended[0] % len(lease_ids)], last_month + ended[1] * DAY
        )
        event("a lease ended in a month charged")

    def charges():
        return conn.execute(
            "SELECT id, amount FROM trust_charges WHERE pmc_id = %s AND kind <> 'credit'"
            " ORDER BY created_at, id",
            (pmc.pmc_id,),
        ).fetchall()

    matches = []  # (charge, transfer, account paid into, amount), not bounced yet
    # The days money moved and the days before them: a payment or a bounce at midnight counts
    # from the day it is on, never the day before.
    moved_on: set[date] = set()
    for action in actions:
        if action[0] == "fee":
            _, lease, day, amount, kind = action
            conn.execute(
                "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo,"
                " charge_type_id) VALUES (%s, %s, %s, 'fee', %s, 'Fee', %s)",
                (
                    pmc.pmc_id,
                    lease_ids[lease % len(lease_ids)],
                    BASE + day * DAY,
                    amount,
                    types.get(kind),
                ),
            )
        elif action[0] == "credit":
            _, lease, day, amount = action
            charge(
                conn,
                pmc.pmc_id,
                lease_ids[lease % len(lease_ids)],
                BASE + day * DAY,
                amount,
                "credit",
                "Concession",
            )
        elif action[0] == "pay":
            _, pick, amount, day, seconds = action
            found = charges()
            charge_id, whole = found[pick % len(found)]
            left = (
                whole
                - conn.execute(
                    "SELECT coalesce((SELECT sum(amount) FROM trust_charge_payments"
                    " WHERE charge_id = %(c)s), 0) - coalesce((SELECT sum(amount)"
                    " FROM trust_payment_reversals WHERE charge_id = %(c)s), 0)"
                    " + coalesce((SELECT sum(k.amount) FROM trust_charge_waivers AS w"
                    " JOIN trust_charges AS k ON k.id = w.credit_charge_id"
                    " WHERE w.waived_charge_id = %(c)s), 0)",
                    {"c": charge_id},
                ).fetchone()[0]
            )
            part = left if amount is None else min(amount, left)
            if part <= 0:
                continue
            paid = pay_into(conn, pmc, charge_id, pmc.operating_cash, part, at(day, seconds))
            if paid is None:
                event("payment refused")
                continue
            matches.append((charge_id, paid[0], paid[1], part))
            moved_on.add(at(day, seconds).date())
            event("paid")
        elif action[0] == "waive":
            _, pick, amount, day = action
            found = charges()
            charge_id, _ = found[pick % len(found)]
            lease = conn.execute(
                "SELECT lease_id FROM trust_charges WHERE id = %s", (charge_id,)
            ).fetchone()[0]
            try:
                with conn.transaction():
                    credit = charge(
                        conn, pmc.pmc_id, lease, BASE + day * DAY, amount, "credit", "Waived"
                    )
                    conn.execute(
                        "INSERT INTO trust_charge_waivers (credit_charge_id, pmc_id,"
                        " waived_charge_id, reason) VALUES (%s, %s, %s, 'Courtesy')",
                        (credit, pmc.pmc_id, charge_id),
                    )
            except psycopg.errors.CheckViolation:
                event("waiver refused")
                continue
            event("waived")
        elif matches:
            _, pick, day, seconds = action
            charge_id, moved, account, part = matches.pop(pick % len(matches))
            when = max(
                at(day, seconds),
                conn.execute(
                    "SELECT event_at FROM pgledger_transfers WHERE id = %s", (moved,)
                ).fetchone()[0],
            )
            (back,) = transfer_batch(conn, [(account, pmc.operating_cash, part)], when)
            reverse_payment(conn, pmc.pmc_id, charge_id, moved, back, part)
            moved_on.add(when.date())
            event("bounced")

    picked = [BASE + d * DAY for d in days]
    picked.append(month_before(picked[0]))  # the Dashboard's own pair, too
    asked = sorted({*picked, *moved_on, *(d - DAY for d in moved_on)})
    together = past_due_on(app_conn, pmc.pmc_id, asked)
    assert set(together) == set(asked)
    for as_of in asked:
        expected = reported(app_conn, pmc.pmc_id, as_of)
        assert together[as_of] == expected
        if expected[0] > 0:
            event("something past due")
    for as_of in picked:  # and each day alone
        assert past_due_on(app_conn, pmc.pmc_id, [as_of]) == {as_of: together[as_of]}


def test_the_dashboards_past_due_is_the_delinquency_reports_for_every_day_asked(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        check(conn, app_conn)
