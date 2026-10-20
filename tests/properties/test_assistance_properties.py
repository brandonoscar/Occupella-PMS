"""Property-based tests for a housing authority's part of the rent (migration 20261020000095, the
founder's review q034).

Hypothesis gives a lease random rent from a random day of January and a housing authority random
assistance (0 to all of the rent) from random months, and the rent run charges January to June.
Then, in a random order, the authority pays some of what it owes (sometimes more than it owes),
the tenant pays, and the lease's end is set, moved or cleared. A model works out each month's rent
for the days in effect, the authority's part of it (its share of the month's rent to the cent,
never more than the amount) and the tenant's rest. After every step:
  - each month's tenant part and authority part come to the month's rent for its days, each as
    the model says, through every end set, moved or cleared;
  - an authority's payment pays its charges oldest first into the owner's account, never more
    than it owes (a payment over it moves nothing), and leaves what the tenant owes alone;
  - a tenant's payment pays the tenant's part alone and leaves what the authority owes alone;
  - the income statement counts both as rent collected;
  - every trust bank account ties out.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace

import psycopg
import pytest
from fastapi import HTTPException
from helpers import add_authority, add_unit, assist, balance, bank_sums, make_pmc, open_lease
from hypothesis import event, given
from hypothesis import strategies as st

from pms.api import assistance, rent

MONTHS = [date(2026, m, 1) for m in range(1, 7)]
PAID_ON = date(2026, 6, 25)
CENT = Decimal("0.01")
ZERO = Decimal(0)


@st.composite
def lease_terms(draw):
    cents = draw(st.integers(min_value=10_000, max_value=500_000))
    start = draw(st.integers(min_value=1, max_value=28))
    months = draw(st.lists(st.integers(0, 5), max_size=4, unique=True))
    aid = sorted(
        (MONTHS[m], Decimal(draw(st.integers(min_value=0, max_value=cents))) / 100) for m in months
    )
    return Decimal(cents) / 100, date(2026, 1, start), aid


STEP = st.one_of(
    st.tuples(st.just("authority pays"), st.integers(min_value=1, max_value=120)),
    st.tuples(
        st.just("tenant pays"), st.integers(min_value=1, max_value=600_000).map(lambda c: c * CENT)
    ),
    st.tuples(
        st.just("end"),
        st.one_of(st.none(), st.dates(min_value=date(2026, 2, 1), max_value=date(2026, 6, 30))),
    ),
)


def part(amount, monthly_rent, assistance_amount):
    """The model: the authority's part of an amount of a month's rent."""
    if amount <= 0 or assistance_amount <= 0:
        return ZERO
    share = (amount * assistance_amount / monthly_rent).quantize(CENT, rounding=ROUND_HALF_UP)
    return min(amount, share)


@given(terms=lease_terms(), steps=st.lists(STEP, max_size=6))
def check(conn, app, terms, steps):
    monthly, starts, aid = terms
    pmc = make_pmc(conn)
    owner = pmc.owners[0].account
    authority = add_authority(conn, pmc.pmc_id)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, starts, None, monthly, [tenant])
    for first, amount in aid:
        assist(conn, pmc.pmc_id, lease, authority, first, amount)
    for first in MONTHS:
        app.execute("SELECT trust_charge_rent_due(%s, %s)", (pmc.pmc_id, first))
    event("assisted" if any(a for _, a in aid) else "no assistance")
    session = SimpleNamespace(pmc_id=pmc.pmc_id)

    def assistance_on(day):
        return ([ZERO] + [a for first, a in aid if first <= day])[-1]

    def due(first):
        return starts if first.month == starts.month else first

    def days_rent(first, ends):
        return conn.execute(
            "SELECT greatest(r.amount, 0) FROM trust_rent_for_month(%s, %s, %s, %s) AS r",
            (starts, ends, monthly, due(first)),
        ).fetchone()[0]

    def model(ends):
        """Each month's (tenant's part, authority's part) under the lease's end."""
        months = {}
        for first in MONTHS:
            amount = days_rent(first, ends)
            authoritys = part(amount, monthly, assistance_on(due(first)))
            months[first] = (amount - authoritys, authoritys)
        return months

    def books():
        """Each month's (tenant's part, authority's part) as charged and adjusted."""
        rows = conn.execute(
            "SELECT date_trunc('month', c.due_on::timestamp)::date, c.kind, c.amount"
            " FROM trust_charges AS c WHERE c.lease_id = %s",
            (lease,),
        ).fetchall()
        aid_rows = conn.execute(
            "SELECT date_trunc('month', a.due_on::timestamp)::date, a.kind, a.amount"
            " FROM trust_assistance_charges AS a WHERE a.lease_id = %s",
            (lease,),
        ).fetchall()
        months = {first: [ZERO, ZERO] for first in MONTHS}
        for side, kinds in ((0, rows), (1, aid_rows)):
            for first, kind, amount in kinds:
                months[first][side] += -amount if kind == "credit" else amount
        return {first: tuple(both) for first, both in months.items()}

    def authority_open():
        return conn.execute(
            "SELECT id, due_on, trust_assistance_open(id) FROM trust_assistance_charges"
            " WHERE lease_id = %s AND adjusts_id IS NULL ORDER BY due_on, created_at, id",
            (lease,),
        ).fetchall()

    def tenant_owed():
        return conn.execute("SELECT trust_lease_owed(%s)", (lease,)).fetchone()[0]

    ends = None
    assert books() == model(ends)
    paid_by_authority = ZERO
    paid_by_tenant = ZERO
    for step, value in steps:
        before = balance(conn, owner)
        if step == "authority pays":
            opens = authority_open()
            owing = sum((left for _, _, left in opens if left > 0), ZERO)
            amount = (owing * value / 100).quantize(CENT, rounding=ROUND_HALF_UP)
            if amount <= 0:
                continue
            owed_before = tenant_owed()
            new = assistance.NewAuthorityPayment(
                request_id=uuid.uuid4(),
                housing_authority_id=authority,
                received_on=PAID_ON,
                lines=[assistance.PaymentLine(lease_id=lease, amount=amount)],
            )
            if amount > owing:
                # As the API runs it: one transaction, rolled back when refused.
                with pytest.raises(HTTPException) as refused, app.transaction():
                    assistance.record_payment(session, app, new)
                assert refused.value.status_code == 422
                assert authority_open() == opens
                assert balance(conn, owner) == before
                event("authority paid more than it owed: refused")
                continue
            with app.transaction():
                assistance.record_payment(session, app, new)
            # The model: oldest first, each charge up to what is open of it.
            left, want = amount, []
            for charge, day, still in opens:
                piece = min(left, max(still, ZERO))
                left -= piece
                want.append((charge, day, still - piece))
            assert authority_open() == want
            assert balance(conn, owner) == before + amount
            assert tenant_owed() == owed_before
            paid_by_authority += amount
            event("authority paid all it owed" if amount == owing else "authority paid some")
        elif step == "tenant pays":
            opens, owed_before = authority_open(), tenant_owed()
            paid = rent.record_payment(app, pmc.pmc_id, lease, uuid.uuid4(), value, PAID_ON)
            assert paid.to_owner == min(value, owed_before)
            assert balance(conn, owner) == before + paid.to_owner
            assert authority_open() == opens
            paid_by_tenant += paid.to_owner
            event("tenant paid")
        else:
            app.execute("SELECT trust_end_lease(%s, %s, %s)", (pmc.pmc_id, lease, value))
            ends = value
            event("end cleared" if value is None else "end set")
        assert books() == model(ends)
        assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}

    # Both kinds of payment are rent collected.
    statement = dict(
        conn.execute(
            "SELECT item, amount FROM trust_report_income_statement(%s, %s, %s, %s)"
            " WHERE property IS NOT NULL",
            (pmc.pmc_id, pmc.owners[0].owner_id, date(2026, 1, 1), date(2026, 12, 31)),
        ).fetchall()
    )
    assert statement.get("income: rent", ZERO) == paid_by_authority + paid_by_tenant


def test_the_rent_splits_between_tenant_and_authority_to_the_cent(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app,
    ):
        app.execute("SET ROLE trust_app")
        check(conn, app)
