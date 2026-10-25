"""Property-based tests for interest on security deposits (migration 20261022000122).

Hypothesis makes a lease of one to three tenants from 2024-01-01 (a leap year), deposits paid in
and partly back out on any days, a property's rates changing on any days, and interest paid for
runs of days in a row, each as a rent credit or added to the deposits. A model works each day's
interest out exactly; the database must agree:
  - each deposit's interest for the days paid is the sum over them of what it held at each day's
    end times that day's rate over the days in its year, rounded to the cent;
  - a payment is from the day after the last one's through day, and one with nothing due is
    refused and leaves the next one where it was;
  - added to a deposit, it comes out of the owner's account with its cash and then earns interest
    itself; as a credit, it is the lease's credit and moves no money;
  - every trust bank account ties out, and the same request again pays nothing more;
  - the cash and accrual income statements count what was added to the deposits as an expense of
    its own (migration 20261022000125), on Schedule E's line 19, and nothing else for it; a
    credit is less rent on the accrual basis and no line at all on the cash basis.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

import psycopg
from helpers import (
    add_tenant,
    add_unit,
    balance,
    bank_sums,
    deposit_interest_terms,
    make_pmc,
    open_account,
    open_lease,
    pay_deposit_interest,
    transfer_batch,
)
from hypothesis import event, given
from hypothesis import strategies as st

START = date(2024, 1, 1)
CENT = Decimal("0.01")
MONEY = st.integers(min_value=1, max_value=500_000).map(lambda c: Decimal(c) / 100)
RATE = st.integers(min_value=0, max_value=50_000).map(lambda r: Decimal(r) / 10_000)  # 0 to 5%


def noon(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 12, tzinfo=UTC)


def days_in_year(day: date) -> int:
    return (date(day.year + 1, 1, 1) - date(day.year, 1, 1)).days


@given(
    tenants=st.integers(min_value=1, max_value=3),
    terms=st.dictionaries(st.integers(0, 900), RATE, min_size=1, max_size=4),
    # (tenant, day, amount, out): money into a deposit, or back out of it as far as it holds.
    moves=st.lists(
        st.tuples(st.integers(0, 2), st.integers(0, 900), MONEY, st.booleans()), max_size=6
    ),
    # (days in the run, added to the deposit rather than credited)
    runs=st.lists(st.tuples(st.integers(1, 400), st.booleans()), min_size=1, max_size=5),
)
def check(conn, app, tenants, terms, moves, runs):
    pmc = make_pmc(conn)
    prop = pmc.owners[0].property_id
    owner = pmc.owners[0].account
    transfer_batch(conn, [(pmc.operating_cash, owner, Decimal(100_000))], noon(START))
    people, accounts = [], []
    for n in range(tenants):
        tenant = add_tenant(conn, pmc.pmc_id, prop, f"Tenant {n}")
        people.append(tenant)
        accounts.append(
            open_account(conn, pmc.pmc_id, pmc.deposit_bank_id, "tenant_deposit", tenant_id=tenant)
        )
    lease = open_lease(
        conn, pmc.pmc_id, add_unit(conn, pmc.pmc_id, prop), START, None, "1000.00", people
    )
    for offset, percent in terms.items():
        deposit_interest_terms(conn, pmc.pmc_id, prop, START + timedelta(days=offset), percent)

    # What each deposit holds at each day's end, as the model keeps it: changes by day.
    changes: dict[tuple[int, date], Decimal] = {}

    def held(n: int, day: date) -> Decimal:
        return sum((a for (m, d), a in changes.items() if m == n and d <= day), Decimal(0))

    for who, offset, amount, out in sorted(moves, key=lambda m: m[1]):
        n, day = who % tenants, START + timedelta(days=offset)
        if out:
            # Back out of the deposit, no more than it holds by then and on every later day.
            amount = min(amount, min(held(n, d) for d in (day, START + timedelta(days=2000))))
            if amount <= 0:
                continue
            transfer_batch(conn, [(accounts[n], pmc.deposit_cash, amount)], noon(day))
            changes[(n, day)] = changes.get((n, day), Decimal(0)) - amount
        else:
            transfer_batch(conn, [(pmc.deposit_cash, accounts[n], amount)], noon(day))
            changes[(n, day)] = changes.get((n, day), Decimal(0)) + amount

    def rate(day: date) -> Decimal:
        started = [o for o in terms if START + timedelta(days=o) <= day]
        return terms[max(started)] if started else Decimal(0)

    def interest(n: int, start: date, through: date) -> tuple[Decimal, bool]:
        """A deposit's interest over the days, to the cent, and whether it sits within a hair
        of a half cent (where the database's own division may round the other way)."""
        exact, day = Fraction(0), start
        while day <= through:
            exact += Fraction(held(n, day)) * Fraction(rate(day)) / 100 / days_in_year(day)
            day += timedelta(days=1)
        cents = exact * 100
        near = abs(cents - int(cents) - Fraction(1, 2)) < Fraction(1, 10**9)
        rounded = Decimal(cents.numerator) / Decimal(cents.denominator)
        return (rounded / 100).quantize(CENT, ROUND_HALF_UP), near

    start, credited, added = START, Decimal(0), Decimal(0)
    for k, (length, into_deposit) in enumerate(runs):
        through = start + timedelta(days=length - 1)
        paid_on = through + timedelta(days=1)
        parts = [interest(n, start, through) for n in range(tenants)]
        if any(near for _, near in parts):
            event("a deposit's interest within a hair of a half cent")
            return
        expected = [amount for amount, _ in parts]
        how = "deposit" if into_deposit else "rent_credit"
        key = f"run-{k}"
        before = balance(conn, owner)
        if sum(expected) == 0:
            event("nothing due")
            try:
                pay_deposit_interest(app, pmc.pmc_id, lease, through, how, key, noon(paid_on))
            except psycopg.errors.CheckViolation as refused:
                assert "no interest is due" in str(refused)
            else:
                raise AssertionError("a payment of nothing went through")
            continue

        paid = pay_deposit_interest(app, pmc.pmc_id, lease, through, how, key, noon(paid_on))

        assert paid == sum(expected)
        assert conn.execute(
            "SELECT from_on, through_on, amount FROM trust_deposit_interest_payments"
            " WHERE pmc_id = %s AND request_key = %s",
            (pmc.pmc_id, key),
        ).fetchone() == (start, through, paid)
        lines = conn.execute(
            "SELECT x.deposit_account, sum(x.amount) FROM trust_deposit_interest_lines AS x"
            " JOIN trust_deposit_interest_payments AS p ON p.id = x.payment_id"
            " WHERE p.request_key = %s AND p.pmc_id = %s GROUP BY x.deposit_account",
            (key, pmc.pmc_id),
        ).fetchall()
        assert dict(lines) == {accounts[n]: a for n, a in enumerate(expected) if a > 0}
        if into_deposit:
            event("added to the deposit")
            for n, amount in enumerate(expected):
                if amount:
                    changes[(n, paid_on)] = changes.get((n, paid_on), Decimal(0)) + amount
            added += paid
            assert balance(conn, owner) == before - paid
        else:
            event("credited against rent")
            credited += paid
            assert balance(conn, owner) == before
        assert [balance(conn, a) for a in accounts] == [
            held(n, START + timedelta(days=5000)) for n in range(tenants)
        ]
        assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}
        # The same request again pays nothing more.
        assert (
            pay_deposit_interest(app, pmc.pmc_id, lease, through, how, key, noon(paid_on)) == paid
        )
        start = paid_on

    assert conn.execute(
        "SELECT coalesce(sum(c.amount), 0) FROM trust_deposit_interest_payments AS p"
        " JOIN trust_charges AS c ON c.id = p.charge_id WHERE p.lease_id = %s",
        (lease,),
    ).fetchone() == (credited,)
    assert balance(conn, owner) == Decimal(100_000) - added

    # Nothing but interest moved the owner's account, so it is all either statement holds: added
    # to deposits, an expense of its own; credited, less rent charged (accrual) and nothing
    # collected (cash).
    for report, rent in (
        ("trust_report_income_statement", Decimal(0)),
        ("trust_report_income_statement_accrual", -credited),
    ):
        lines = {
            (item, schedule): amount
            for _, place, item, schedule, amount in conn.execute(
                f"SELECT * FROM {report}(%s, %s, %s, %s)",
                (pmc.pmc_id, pmc.owners[0].owner_id, START, START + timedelta(days=5000)),
            ).fetchall()
            if place is not None and not item.startswith("all properties") and amount is not None
        }
        interest_lines = {k: v for k, v in lines.items() if "interest" in k[0] or k[1] == 19}
        if added:
            assert interest_lines == {
                ("expense: security deposit interest", 19): added,
                ("Schedule E line 19: Other", 19): added,
            }
        else:
            assert interest_lines == {}
        assert lines[("income: rent", 3)] == rent
        assert lines[("total expenses", None)] == added
        assert lines[("net income", None)] == rent - added

    # The year-end package (migration 20261025000200): each year's interest added, by the day it
    # moved, is its own part, never "other".
    by_year = dict(
        conn.execute(
            "SELECT extract(year FROM tr.event_at AT TIME ZONE 'UTC')::int, sum(l.amount)"
            " FROM trust_deposit_interest_lines AS l"
            " JOIN pgledger_transfers AS tr ON tr.id = l.transfer_id"
            " WHERE l.pmc_id = %s GROUP BY 1",
            (pmc.pmc_id,),
        ).fetchall()
    )
    for year in range(START.year, max(by_year, default=START.year) + 1):
        parts = {
            item: amount
            for _, place, item, amount in conn.execute(
                "SELECT * FROM trust_report_owner_year_end(%s, %s, %s)",
                (pmc.pmc_id, pmc.owners[0].owner_id, year),
            ).fetchall()
            if place is None
        }
        assert parts["all properties: security deposit interest"] == -by_year.get(year, 0)
        if year > START.year:  # only interest moved the account after its first year
            assert parts["all properties: other"] == 0


def test_deposit_interest_is_each_days_deposit_at_its_rate_paid_once(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app,
    ):
        app.execute("SET ROLE trust_app")
        check(conn, app)
