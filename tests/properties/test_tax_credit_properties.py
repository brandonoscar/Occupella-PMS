"""Property-based tests for tax credit units (migration 20261022000123; the founder's review q143).

Hypothesis gives a property income limits at 50% of median from several days, and a unit
designations from several days (a percent of median, bedrooms and a utility allowance, or an end),
with a lease at it from the first day. An exact model (fractions) says, on any day:
  - the unit's designation is the latest from a day on or before it, none after an end;
  - a household's income limit is the latest table's limit for its size times the percent over
    50, to the dollar (half up); over eight people adds 8% of the four-person limit each;
  - the most the gross rent may be is 30% over twelve of the limit for one person with no
    bedroom, else the mean of the limits around one and a half people a bedroom, down to the
    dollar;
  - an income certification of the lease is taken only on a day the unit is a tax credit unit;
  - the compliance report's unit line gives that most, the rent and allowance as the gross rent,
    and says it is within the limit exactly when it is.
Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from math import ceil, floor

import psycopg
from helpers import add_tenant, add_unit, make_pmc, open_lease
from hypothesis import event, given
from hypothesis import strategies as st

FIRST = date(2026, 1, 1)
RENT = Decimal("900.00")
LIMIT = st.integers(100_000, 999_999_999).map(lambda c: Decimal(c) / 100)
TABLES = st.dictionaries(st.integers(0, 400), st.lists(LIMIT, min_size=8, max_size=8), max_size=3)
DESIGNATION = st.one_of(
    st.none(),
    st.tuples(
        st.sampled_from([20, 30, 40, 50, 60, 70, 80]),
        st.integers(0, 6),
        st.integers(0, 40_000).map(lambda c: Decimal(c) / 100),
    ),
)
DESIGNATIONS = st.dictionaries(st.integers(0, 400), DESIGNATION, max_size=4)
DAYS = st.lists(st.integers(0, 450), min_size=1, max_size=6, unique=True)


def latest(rows: dict, day: int):
    starts = [s for s in rows if s <= day]
    return rows[max(starts)] if starts else None


def income_limit(tables, day, percent, size):
    table = latest(tables, day)
    if table is None:
        return None
    base = table[size - 1] if size <= 8 else table[7] + (size - 8) * table[3] * Decimal("0.08")
    return (base * percent / 50).quantize(Decimal(1), rounding=ROUND_HALF_UP)


def rent_limit(tables, day, percent, bedrooms):
    if bedrooms == 0:
        limit = income_limit(tables, day, percent, 1)
        mean = None if limit is None else Fraction(limit)
    else:
        low = income_limit(tables, day, percent, floor(bedrooms * 1.5))
        high = income_limit(tables, day, percent, ceil(bedrooms * 1.5))
        mean = None if low is None else (Fraction(low) + Fraction(high)) / 2
    return None if mean is None else floor(mean * Fraction(3, 10) / 12)


def one(conn, query, *args):
    return conn.execute(query, args).fetchone()[0]


@given(tables=TABLES, designations=DESIGNATIONS, days=DAYS)
def check(conn, tables, designations, days):
    pmc = make_pmc(conn)
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "Unit 1")
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 1")
    lease = open_lease(conn, pmc.pmc_id, unit, FIRST, None, str(RENT), [tenant])
    for starts, limits in tables.items():
        conn.execute(
            "INSERT INTO trust_tax_credit_limits (pmc_id, property_id, starts_on, limits)"
            " VALUES (%s, %s, %s, %s::numeric[])",
            (pmc.pmc_id, prop, FIRST + timedelta(days=starts), limits),
        )
    for starts, designation in designations.items():
        percent, bedrooms, allowance = designation or (None, None, None)
        conn.execute(
            "INSERT INTO trust_tax_credit_units (pmc_id, unit_id, starts_on, income_percent,"
            " bedrooms, utility_allowance) VALUES (%s, %s, %s, %s, %s, %s)",
            (pmc.pmc_id, unit, FIRST + timedelta(days=starts), percent, bedrooms, allowance),
        )
    for offset in days:
        day = FIRST + timedelta(days=offset)
        for percent, size in ((20, 1), (50, 4), (60, 8), (80, 11)):
            assert one(
                conn,
                "SELECT trust_tax_credit_income_limit(%s, %s, %s, %s)",
                prop,
                day,
                percent,
                size,
            ) == income_limit(tables, offset, percent, size)
        designation = latest(designations, offset)
        on = conn.execute(
            "SELECT income_percent, bedrooms, utility_allowance"
            " FROM trust_tax_credit_unit_on(%s, %s)",
            (unit, day),
        ).fetchall()
        assert on == ([] if designation is None else [designation])
        try:
            conn.execute(
                "INSERT INTO trust_income_certifications (pmc_id, lease_id, kind, effective_on,"
                " household_size, annual_income, next_due_on)"
                " VALUES (%s, %s, 'annual', %s, 2, 30000, %s)",
                (pmc.pmc_id, lease, day, day + timedelta(days=365)),
            )
            certified = True
        except psycopg.errors.CheckViolation:
            certified = False
        assert certified is (designation is not None)
        lines = conn.execute(
            "SELECT max_gross_rent, gross_rent, rent_test"
            " FROM trust_report_tax_credit_compliance(%s, %s) WHERE item = 'unit'",
            (pmc.pmc_id, day),
        ).fetchall()
        if designation is None:
            event("day: not a tax credit unit")
            assert lines == []
            continue
        percent, bedrooms, allowance = designation
        most = rent_limit(tables, offset, percent, bedrooms)
        gross = RENT + allowance
        if most is None:
            event("day: no income limits on file")
            words = "no income limits on file"
        elif gross <= most:
            event("day: within the limit")
            words = "within the limit"
        else:
            event("day: over the limit")
            words = f"over by {gross - most:.2f}"
        assert lines == [(None if most is None else Decimal(most), gross, words)]


def test_a_tax_credit_units_limits_and_certifications_follow_the_tables_in_force(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
