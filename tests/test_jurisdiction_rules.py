"""State and local rules in the database (migration 20261023000202): where a property's address is
in the law, the facts rules turn on, the rules themselves and the company's confirmations, and the
one function that resolves the rule in force for a property on a day. Unknown is never "no rule":
a fact a rule turns on that isn't on file leaves the rule unknown. The API's side is in
tests/api/test_jurisdictions_api.py; a model of the resolver in
tests/properties/test_jurisdiction_properties.py. Every rule here is synthetic: none is the law."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from helpers import add_staff, make_pmc

from pms import jurisdictions

DAY = date(2026, 10, 23)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def prop(pmc):
    return pmc.owners[0].property_id


def address(conn, pmc, prop, line="100 Synthetic Way", state="CA"):
    return conn.execute(
        "INSERT INTO trust_property_addresses (pmc_id, property_id, line_1, city, state, zip)"
        " VALUES (%s, %s, %s, 'Demo City', %s, '00001') RETURNING id",
        (pmc.pmc_id, prop, line, state),
    ).fetchone()[0]


def place(conn, pmc, address_id, county="037", city="44000", source="staff"):
    conn.execute(
        "INSERT INTO trust_property_jurisdictions (pmc_id, address_id, state_fips, state_name,"
        " county_fips, county_name, place_fips, place_name, source)"
        " VALUES (%s, %s, '06', 'California', %s, %s, %s, %s, %s)",
        (
            pmc.pmc_id,
            address_id,
            county,
            None if county is None else "Demo County",
            city,
            None if city is None else "Demo City city",
            source,
        ),
    )


def rule(conn, pmc, kind="deposit_cap", level="state", code="06", number="2", **extra):
    values = {
        "pmc_id": pmc.pmc_id,
        "level": level,
        "jurisdiction": code,
        "jurisdiction_name": f"Synthetic {level}",
        "rule": kind,
        "value_kind": "number",
        "value_number": number,
        "value_unit": "days" if kind.endswith("_days") else "months_of_rent",
        "effective_from": date(2020, 1, 1),
        "citation": "Synthetic Code 1",
        "url": "https://example.org/synthetic-code-1",
        "checked_on": date(2026, 10, 23),
        "reviewed_by": "Synthetic Reviewer",
        "source": "staff",
    } | extra
    columns = ", ".join(values)
    return conn.execute(
        f"INSERT INTO trust_jurisdiction_rules ({columns})"  # noqa: S608
        f" VALUES ({', '.join(['%s'] * len(values))}) RETURNING id",
        tuple(values.values()),
    ).fetchone()[0]


def resolved(conn, prop, kind="deposit_cap", day=DAY):
    row = conn.execute(
        "SELECT id FROM trust_jurisdiction_rule(%s, %s, %s)", (prop, kind, day)
    ).fetchone()
    return None if row is None else row[0]


def needs(conn, prop, kind="deposit_cap", day=DAY):
    return conn.execute(
        "SELECT trust_jurisdiction_rule_needs(%s, %s, %s)", (prop, kind, day)
    ).fetchone()[0]


def facts(conn, pmc, prop, **known):
    conn.execute(
        "INSERT INTO trust_property_facts (pmc_id, property_id, year_built, units_in_building,"
        " owner_occupied, owner_kind) VALUES (%s, %s, %s, %s, %s, %s)",
        (
            pmc.pmc_id,
            prop,
            known.get("year_built"),
            known.get("units_in_building"),
            known.get("owner_occupied"),
            known.get("owner_kind"),
        ),
    )


def test_where_a_property_is_counts_for_its_address_now(conn, pmc, prop):
    assert conn.execute("SELECT * FROM trust_property_jurisdiction(%s)", (prop,)).fetchone() is None
    first = address(conn, pmc, prop)
    place(conn, pmc, first, source="fake")
    place(conn, pmc, first, county="001", city=None)  # staff correct it: no city
    now = conn.execute(
        "SELECT county_fips, place_fips, source FROM trust_property_jurisdiction(%s)", (prop,)
    ).fetchone()
    assert now == ("001", None, "staff")
    address(conn, pmc, prop, "200 Example Street")  # moved: none until found again
    assert conn.execute("SELECT * FROM trust_property_jurisdiction(%s)", (prop,)).fetchone() is None


def test_a_place_is_whole_codes_and_names(conn, pmc, prop):
    at = address(conn, pmc, prop)
    for county, name in (("037", None), ("37", "Demo County")):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO trust_property_jurisdictions (pmc_id, address_id, state_fips,"
                " state_name, county_fips, county_name, source)"
                " VALUES (%s, %s, '06', 'California', %s, %s, 'staff')",
                (pmc.pmc_id, at, county, name),
            )
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO trust_property_jurisdictions (pmc_id, address_id, state_fips,"
            " state_name, tract, source) VALUES (%s, %s, '06', 'California', '000100', 'staff')",
            (pmc.pmc_id, at),
        )  # a tract outside any county


def test_the_most_specific_level_governs(conn, pmc, prop):
    place(conn, pmc, address(conn, pmc, prop))
    assert resolved(conn, prop) is None  # nothing on file: unknown
    federal = rule(conn, pmc, level="federal", code="US")
    assert resolved(conn, prop) == federal
    state = rule(conn, pmc)
    assert resolved(conn, prop) == state
    county = rule(conn, pmc, level="county", code="06037")
    assert resolved(conn, prop) == county
    rule(conn, pmc, level="county", code="06001")  # another county's
    rule(conn, pmc, level="city", code="0699999")  # another city's
    assert resolved(conn, prop) == county
    city = rule(conn, pmc, level="city", code="0644000")
    assert resolved(conn, prop) == city
    assert resolved(conn, prop, "nsf_fee_cap") is None  # another kind


def test_a_rule_holds_from_its_first_day_until_its_last(conn, pmc, prop):
    place(conn, pmc, address(conn, pmc, prop))
    old = rule(conn, pmc, effective_from=date(2020, 1, 1), effective_to=date(2026, 7, 1))
    new = rule(conn, pmc, effective_from=date(2026, 7, 1))
    assert resolved(conn, prop, day=date(2026, 6, 30)) == old
    assert resolved(conn, prop, day=date(2026, 7, 1)) == new
    assert resolved(conn, prop, day=date(2019, 12, 31)) is None


def test_a_correction_takes_the_place_of_what_it_corrects(conn, pmc, prop):
    place(conn, pmc, address(conn, pmc, prop))
    wrong = rule(conn, pmc, number="3")
    right = rule(conn, pmc, number="2", supersedes=wrong)
    assert resolved(conn, prop) == right
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        rule(conn, pmc, supersedes=wrong)  # corrected once: correct the newer one
    with pytest.raises(psycopg.errors.CheckViolation, match="same jurisdiction and kind"):
        rule(conn, pmc, kind="nsf_fee_cap", value_unit="dollars", supersedes=right)
    with pytest.raises(psycopg.errors.CheckViolation, match="same jurisdiction and kind"):
        rule(conn, make_pmc(conn), supersedes=right)  # another company's


def test_a_narrower_rule_wins_where_its_facts_match_and_unknown_facts_leave_it_unknown(
    conn, pmc, prop
):
    place(conn, pmc, address(conn, pmc, prop))
    general = rule(conn, pmc, number="1")
    small = rule(
        conn, pmc, number="2", precedence=10, owner_kind="person", units_at_most=4
    )  # a small landlord's exception
    # Neither fact on file: the exception may apply, so the rule is unknown, never the general one.
    assert resolved(conn, prop) is None
    assert needs(conn, prop) == ["units_in_building", "owner_kind"]
    facts(conn, pmc, prop, units_in_building=2)
    assert resolved(conn, prop) is None and needs(conn, prop) == ["owner_kind"]
    facts(conn, pmc, prop, units_in_building=2, owner_kind="person")
    assert resolved(conn, prop) == small and needs(conn, prop) == []
    facts(conn, pmc, prop, units_in_building=12)  # one fact fails: the exception can't apply
    assert resolved(conn, prop) == general and needs(conn, prop) == []
    facts(conn, pmc, prop, units_in_building=2, owner_kind="company")
    assert resolved(conn, prop) == general


def test_buildings_by_year_built(conn, pmc, prop):
    place(conn, pmc, address(conn, pmc, prop))
    older = rule(conn, pmc, kind="rent_cap", value_unit="percent_a_year", built_before=1979)
    assert needs(conn, prop, "rent_cap") == ["year_built"]
    facts(conn, pmc, prop, year_built=1978)
    assert resolved(conn, prop, "rent_cap") == older
    facts(conn, pmc, prop, year_built=1979)
    assert resolved(conn, prop, "rent_cap") is None and needs(conn, prop, "rent_cap") == []


def test_another_companys_rules_never_count(conn, pmc, prop):
    place(conn, pmc, address(conn, pmc, prop))
    rule(conn, make_pmc(conn))
    assert resolved(conn, prop) is None


def test_a_rule_says_its_value_in_the_shape_its_kind_takes(conn, pmc):
    bad = [
        {"value_kind": "number", "value_number": None},
        {"value_kind": "none"},  # a number with "no limit"
        {"value_kind": "words", "value_words": "reasonable"},  # words with a number
        {"value_unit": "days"},  # a deposit cap in days
        {"kind": "nsf_fee_cap", "value_unit": "percent_of_rent"},
        {"combine": "lesser"},  # a second number missing
        {"level": "county", "code": "06"},  # a county's code is five digits
        {"level": "federal", "code": "06"},
        {"url": "http://example.org/not-https"},
        {"effective_to": date(2019, 1, 1)},
        {"built_before": 1950, "built_on_or_after": 1960},
        {"source": "pms"},  # the PMS's catalog names its key
    ]
    for extra in bad:
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            rule(conn, pmc, **extra)
    rule(conn, pmc, value_kind="none", value_number=None, value_unit=None)
    rule(conn, pmc, value_kind="words", value_number=None, value_unit=None, value_words="fair")
    rule(
        conn, pmc, kind="late_fee_cap", number="50", value_unit="dollars", combine="lesser",
        also_number="5", also_unit="percent_of_rent",
    )  # fmt: skip


def test_rules_name_who_added_them_and_are_confirmed_once(conn, pmc):
    staff = add_staff(conn, pmc.pmc_id)
    conn.execute("SELECT set_config('trust.staff_id', %s, false)", (str(staff),))
    try:
        added = rule(conn, pmc)
        seeded = rule(conn, pmc, source="pms", catalog_key="synthetic-1")
    finally:
        conn.execute("SELECT set_config('trust.staff_id', '', false)")
    by = dict(
        conn.execute(
            "SELECT id, recorded_by FROM trust_jurisdiction_rules WHERE id IN (%s, %s)",
            (added, seeded),
        ).fetchall()
    )
    assert by == {added: staff, seeded: None}
    conn.execute(
        "INSERT INTO trust_jurisdiction_rule_confirmations (rule_id, pmc_id, confirmed_by)"
        " VALUES (%s, %s, %s)",
        (seeded, pmc.pmc_id, staff),
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO trust_jurisdiction_rule_confirmations (rule_id, pmc_id, confirmed_by)"
            " VALUES (%s, %s, %s)",
            (seeded, pmc.pmc_id, staff),
        )


def test_all_of_it_is_append_only_and_audited(conn, pmc, prop):
    staff = add_staff(conn, pmc.pmc_id)
    place(conn, pmc, address(conn, pmc, prop))
    facts(conn, pmc, prop, year_built=1990)
    made = rule(conn, pmc)
    conn.execute(
        "INSERT INTO trust_jurisdiction_rule_confirmations (rule_id, pmc_id, confirmed_by)"
        " VALUES (%s, %s, %s)",
        (made, pmc.pmc_id, staff),
    )
    tables = (
        "trust_property_jurisdictions",
        "trust_property_facts",
        "trust_jurisdiction_rules",
        "trust_jurisdiction_rule_confirmations",
    )
    for table in tables:
        for change in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with pytest.raises(psycopg.errors.RestrictViolation), conn.transaction():
                conn.execute(change)
    logged = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT table_name FROM trust_audit_log WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchall()
    }
    assert set(tables) <= logged


def test_the_ai_role_reads_the_rules_and_writes_none(conn, ai_conn, app_conn, pmc, prop):
    place(conn, pmc, address(conn, pmc, prop))
    made = rule(conn, pmc)
    for table in (
        "trust_property_jurisdictions",
        "trust_property_facts",
        "trust_jurisdiction_rules",
        "trust_jurisdiction_rule_confirmations",
    ):
        ai_conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        rule(ai_conn, pmc)
    assert resolved(app_conn, prop) == made  # the app resolves it


def test_the_catalog_adds_what_a_company_lacks_once(conn, pmc, prop):
    place(conn, pmc, address(conn, pmc, prop))
    entry = {
        "key": "synthetic-deposit-cap",
        "level": "state",
        "jurisdiction": "06",
        "jurisdiction_name": "Synthetic State",
        "rule": "deposit_cap",
        "value_kind": "number",
        "value_number": 2,
        "value_unit": "months_of_rent",
        "effective_from": date(2020, 1, 1),
        "citation": "Synthetic Code 2",
        "url": "https://example.org/synthetic-code-2",
        "checked_on": date(2026, 10, 23),
        "reviewed_by": "Synthetic Reviewer",
    }
    assert jurisdictions.seed(conn, pmc.pmc_id, [entry]) == 1
    assert jurisdictions.seed(conn, pmc.pmc_id, [entry]) == 0  # once
    first = resolved(conn, prop)
    assert first is not None
    fixed = entry | {"key": "synthetic-deposit-cap-fixed", "value_number": 1}
    fixed["supersedes"] = "synthetic-deposit-cap"
    assert jurisdictions.seed(conn, pmc.pmc_id, [entry, fixed]) == 1
    now = conn.execute(
        "SELECT value_number, source, catalog_key, supersedes, recorded_by"
        " FROM trust_jurisdiction_rule(%s, 'deposit_cap', %s)",
        (prop, DAY),
    ).fetchone()
    assert now == (1, "pms", "synthetic-deposit-cap-fixed", first, None)
    assert jurisdictions.catalog() == []  # the PMS's own: nothing confirmed against a statute yet


def test_the_nightly_run_copies_the_catalog_in_once(app_conn, pmc, monkeypatch):
    from pms import nightly

    entry = {
        "key": "synthetic-grace",
        "level": "federal",
        "jurisdiction": "US",
        "jurisdiction_name": "Synthetic Nation",
        "rule": "late_fee_grace_days",
        "value_kind": "number",
        "value_number": 3,
        "value_unit": "days",
        "effective_from": date(2020, 1, 1),
        "citation": "Synthetic Code 3",
        "url": "https://example.org/synthetic-code-3",
        "checked_on": date(2026, 10, 23),
        "reviewed_by": "Synthetic Reviewer",
    }
    monkeypatch.setattr(jurisdictions, "catalog", lambda: [entry])
    # As the app's role, as the nightly run is.
    assert nightly.run_pmc(app_conn, pmc.pmc_id, DAY, "http://testserver").rules == 1
    assert nightly.run_pmc(app_conn, pmc.pmc_id, DAY, "http://testserver").rules == 0


def test_unknown_rules_never_warn():
    """A rule not known (no row, or a fact not on file) never warns: unknown is not "no limit"."""
    unknown = jurisdictions.Found("deposit_cap", None, (), "unknown")
    assert jurisdictions.deposit_warnings(unknown, Decimal(0), Decimal("99999"), None) == []
    assert (
        jurisdictions.nsf_fee_warnings(replace(unknown, kind="nsf_fee_cap"), Decimal("999")) == []
    )
