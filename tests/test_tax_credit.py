"""Tax credit (LIHTC) compliance as the database keeps it (migration 20261022000123; the founder's
review q143): a property's 50% income limits from a day, a unit's designation from a day, the
income and rent limits worked out from them, an income certification allowed for a tax credit
unit's lease, nothing rewritten, and what the AI role reads. Every name and figure is synthetic."""

from datetime import date
from decimal import Decimal

import psycopg
import pytest
from helpers import add_tenant, add_unit, make_pmc, open_lease

LIMITS = [30000, 34300, 38600, 42850, 46300, 49750, 53150, 56600]


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def limits(conn, pmc_id, property_id, starts_on=date(2026, 1, 1), values=LIMITS):
    conn.execute(
        "INSERT INTO trust_tax_credit_limits (pmc_id, property_id, starts_on, limits)"
        " VALUES (%s, %s, %s, %s::numeric[])",
        (pmc_id, property_id, starts_on, [None if v is None else Decimal(v) for v in values]),
    )


def designate(conn, pmc_id, unit, starts_on, percent=60, bedrooms=1, allowance="50.00"):
    conn.execute(
        "INSERT INTO trust_tax_credit_units"
        " (pmc_id, unit_id, starts_on, income_percent, bedrooms, utility_allowance)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (
            pmc_id,
            unit,
            starts_on,
            percent,
            bedrooms,
            None if allowance is None else Decimal(allowance),
        ),
    )


def certify(conn, pmc_id, lease, effective_on, size=2, income="30000.00", kind="move-in"):
    conn.execute(
        "INSERT INTO trust_income_certifications (pmc_id, lease_id, kind, effective_on,"
        " household_size, annual_income, next_due_on) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (pmc_id, lease, kind, effective_on, size, Decimal(income), date(2027, 1, 1)),
    )


def one(conn, query, *args):
    return conn.execute(query, args).fetchone()[0]


def test_limits_are_the_50_percent_table_in_proportion_and_rent_30_percent_of_a_bedrooms_limit(
    conn, pmc
):
    prop = pmc.owners[0].property_id
    limits(conn, pmc.pmc_id, prop)

    def income(percent, size, day=date(2026, 6, 30)):
        return one(
            conn, "SELECT trust_tax_credit_income_limit(%s, %s, %s, %s)", prop, day, percent, size
        )

    def rent(percent, bedrooms):
        return one(
            conn,
            "SELECT trust_tax_credit_rent_limit(%s, '2026-06-30', %s, %s)",
            prop,
            percent,
            bedrooms,
        )

    assert income(50, 1) == 30000
    assert income(60, 4) == 51420  # 42850 x 60/50
    assert income(20, 2) == 13720  # 34300 x 20/50
    # Nine people: the eight-person limit and 8% of the four-person one (3428).
    assert income(50, 9) == 60028
    # No limits on file yet, or no household: none.
    assert income(50, 1, date(2025, 12, 31)) is None
    assert income(50, 0) is None
    # No bedroom: one person; one bedroom: the mean of one and two; two: three people.
    assert rent(60, 0) == 900  # 36000 x 30% / 12
    assert rent(60, 1) == 964  # (36000 + 41160) / 2 x 30% / 12 = 964.50, down to the dollar
    assert rent(50, 2) == 965  # 38600 x 30% / 12
    # Three bedrooms: four and a half people, (51420 + 55560) / 2 x 30% / 12 = 1337.25.
    assert rent(60, 3) == 1337


def test_a_designation_names_its_bedrooms_and_allowance_and_an_end_names_neither(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "1A")
    designate(conn, pmc.pmc_id, unit, date(2026, 1, 1))
    designate(conn, pmc.pmc_id, unit, date(2026, 6, 1), None, None, None)
    on = "SELECT income_percent, bedrooms, utility_allowance FROM trust_tax_credit_unit_on(%s, %s)"
    assert conn.execute(on, (unit, date(2026, 5, 31))).fetchall() == [(60, 1, Decimal("50.00"))]
    assert conn.execute(on, (unit, date(2026, 6, 1))).fetchall() == []
    assert conn.execute(on, (unit, date(2025, 12, 31))).fetchall() == []
    refused = psycopg.errors.CheckViolation
    for percent, bedrooms, allowance in (
        (60, None, "0"),
        (None, 1, None),
        (65, 1, "0"),
        (60, 7, "0"),
    ):
        with pytest.raises(refused):
            designate(conn, pmc.pmc_id, unit, date(2026, 7, 1), percent, bedrooms, allowance)
    with pytest.raises(psycopg.errors.UniqueViolation):
        designate(conn, pmc.pmc_id, unit, date(2026, 1, 1))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        designate(conn, make_pmc(conn).pmc_id, unit, date(2026, 8, 1))
    for bad in ([1, 2, 3], [0] * 8, [1, 2, 3, 4, 5, 6, 7, None], [10_000_000] * 8):
        with pytest.raises(refused):
            limits(conn, pmc.pmc_id, pmc.owners[0].property_id, date(2026, 1, 1), bad)


def test_a_tax_credit_units_lease_takes_certifications_and_no_inspection(conn, pmc):
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "1A")
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 9")
    lease = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "900.00", [tenant])

    with pytest.raises(psycopg.errors.CheckViolation, match="not a tax credit unit on 01/01/2026"):
        certify(conn, pmc.pmc_id, lease, date(2026, 1, 1))
    designate(conn, pmc.pmc_id, unit, date(2025, 1, 1))
    certify(conn, pmc.pmc_id, lease, date(2026, 1, 1))
    with pytest.raises(psycopg.errors.CheckViolation, match="outside the lease"):
        certify(conn, pmc.pmc_id, lease, date(2025, 12, 1), kind="interim")
    # An inspection is for a housing authority: none without assistance on file.
    with pytest.raises(psycopg.errors.CheckViolation, match="no housing assistance on file"):
        conn.execute(
            "INSERT INTO trust_assistance_inspections (pmc_id, lease_id, housing_authority_id,"
            " inspected_on, result, due_on) VALUES (%s, %s, gen_random_uuid(), '2026-01-02',"
            " 'passed', '2028-01-02')",
            (pmc.pmc_id, lease),
        )


def test_nothing_is_rewritten_and_the_ai_reads_the_terms_and_the_report_only(
    conn, app_conn, ai_conn, pmc
):
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, "1A")
    limits(app_conn, pmc.pmc_id, prop)
    designate(app_conn, pmc.pmc_id, unit, date(2026, 1, 1))
    for table in ("trust_tax_credit_limits", "trust_tax_credit_units"):
        for change in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
                conn.execute(change)
        assert ai_conn.execute(
            f"SELECT count(*) FROM {table} WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone() == (1,)
    assert ai_conn.execute(
        "SELECT item, unit, max_gross_rent FROM trust_report_tax_credit_compliance(%s, %s)",
        (pmc.pmc_id, date(2026, 6, 30)),
    ).fetchall() == [("unit", "1A", Decimal(964)), ("qualified units", None, None)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ai_conn.execute("SELECT trust_tax_credit_rent_limit(%s, '2026-06-30', 60, 1)", (prop,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ai_conn.execute("SELECT count(*) FROM trust_income_certifications")
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="as of a day"):
        app_conn.execute(
            "SELECT * FROM trust_report_tax_credit_compliance(%s, NULL)", (pmc.pmc_id,)
        )
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no PMC"):
        app_conn.execute(
            "SELECT * FROM trust_report_tax_credit_compliance(gen_random_uuid(), '2026-06-30')"
        )
