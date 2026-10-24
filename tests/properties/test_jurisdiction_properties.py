"""Property-based tests for state and local rules (migration 20261023000202,
src/pms/jurisdictions.py).

Hypothesis gives a property a place in the law (a state, maybe a county and a city), facts that
may each be unknown, and any rules of any level, in force on any days, with any conditions and
precedence, some corrected, some of another county, city or company. A model in Python says which
rule governs on a day, and the database must agree:
  - of the rules in force for the property's jurisdictions and not corrected, the most specific
    level's comes first (city, county, state, federal), then the highest precedence, then the
    newest;
  - the first whose facts don't fail governs if they match; if a fact it turns on isn't on file it
    is unknown (none), never the next one, and its facts are the ones to put on file;
  - and the checks' arithmetic never warns on a value within a rule's number, and always on one
    past it.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import re
from datetime import date, timedelta
from decimal import Decimal

import psycopg
from helpers import make_pmc
from hypothesis import event, given
from hypothesis import strategies as st

from pms import jurisdictions

DAY = date(2026, 10, 23)
LEVELS = {
    "federal": ["US"],
    "state": ["06", "48"],  # the property's state, and another
    "county": ["06037", "06001"],
    "city": ["0644000", "0699999"],
}
RANK = {"city": 0, "county": 1, "state": 2, "federal": 3}
MINE = {"US", "06", "06037", "0644000"}

maybe_year = st.one_of(st.none(), st.integers(1900, 2020))
maybe_units = st.one_of(st.none(), st.integers(1, 40))
maybe_bool = st.one_of(st.none(), st.booleans())
maybe_kind = st.one_of(st.none(), st.sampled_from(["person", "company"]))

rules = st.lists(
    st.fixed_dictionaries(
        {
            "level": st.sampled_from(list(LEVELS)),
            "other": st.booleans(),  # another state's, county's or city's
            "precedence": st.integers(0, 3),
            "starts": st.integers(-400, 30),  # days from DAY
            "lasts": st.one_of(st.none(), st.integers(1, 800)),
            "built_before": maybe_year,
            "units_at_most": st.one_of(st.none(), st.integers(1, 20)),
            "owner_occupied": maybe_bool,
            "owner_kind": maybe_kind,
            "corrected": st.booleans(),
            "elsewhere": st.booleans(),  # another company's
        }
    ),
    max_size=8,
)
facts = st.fixed_dictionaries(
    {
        "year_built": maybe_year,
        "units_in_building": maybe_units,
        "owner_occupied": maybe_bool,
        "owner_kind": maybe_kind,
    }
)


def matches(rule, known):
    """The rule's facts against the property's: True, False, or None when unknown."""
    tests = []
    if rule["built_before"] is not None:
        y = known["year_built"]
        tests.append(None if y is None else y < rule["built_before"])
    if rule["units_at_most"] is not None:
        u = known["units_in_building"]
        tests.append(None if u is None else u <= rule["units_at_most"])
    for fact in ("owner_occupied", "owner_kind"):
        if rule[fact] is not None:
            k = known[fact]
            tests.append(None if k is None else k == rule[fact])
    if False in tests:
        return False
    return None if None in tests else True


def needs_of(rule, known):
    named = []
    if rule["built_before"] is not None and known["year_built"] is None:
        named.append("year_built")
    if rule["units_at_most"] is not None and known["units_in_building"] is None:
        named.append("units_in_building")
    for fact in ("owner_occupied", "owner_kind"):
        if rule[fact] is not None and known[fact] is None:
            named.append(fact)
    return named


def insert(conn, pmc_id, plan, supersedes=None):
    code = LEVELS[plan["level"]][1 if plan["other"] and plan["level"] != "federal" else 0]
    starts = DAY + timedelta(days=plan["starts"])
    ends = None if plan["lasts"] is None else starts + timedelta(days=plan["lasts"])
    return (
        code,
        conn.execute(
            "INSERT INTO trust_jurisdiction_rules (pmc_id, level, jurisdiction, jurisdiction_name,"
            " rule, value_kind, value_number, value_unit, built_before, units_at_most,"
            " owner_occupied, owner_kind, precedence, effective_from, effective_to, citation,"
            " url, checked_on, reviewed_by, supersedes, source)"
            " VALUES (%s, %s, %s, 'Synthetic', 'deposit_cap', 'number', 1, 'months_of_rent',"
            " %s, %s, %s, %s, %s, %s, %s, 'Synthetic Code 1', 'https://example.org/code',"
            " '2026-10-23', 'Synthetic Reviewer', %s, 'staff') RETURNING id",
            (
                pmc_id,
                plan["level"],
                code,
                plan["built_before"],
                plan["units_at_most"],
                plan["owner_occupied"],
                plan["owner_kind"],
                plan["precedence"],
                starts,
                ends,
                supersedes,
            ),
        ).fetchone()[0],
        starts,
        ends,
    )


@given(plan=rules, known=facts)
def check_resolver(conn, plan, known):
    pmc = make_pmc(conn)
    other = make_pmc(conn, owners=1)
    prop = pmc.owners[0].property_id
    address = conn.execute(
        "INSERT INTO trust_property_addresses (pmc_id, property_id, line_1, city, state, zip)"
        " VALUES (%s, %s, '100 Synthetic Way', 'Demo City', 'CA', '00001') RETURNING id",
        (pmc.pmc_id, prop),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO trust_property_jurisdictions (pmc_id, address_id, state_fips, state_name,"
        " county_fips, county_name, place_fips, place_name, source)"
        " VALUES (%s, %s, '06', 'California', '037', 'Demo County', '44000', 'Demo City city',"
        " 'fake')",
        (pmc.pmc_id, address),
    )
    conn.execute(
        "INSERT INTO trust_property_facts (pmc_id, property_id, year_built, units_in_building,"
        " owner_occupied, owner_kind) VALUES (%s, %s, %s, %s, %s, %s)",
        (
            pmc.pmc_id,
            prop,
            known["year_built"],
            known["units_in_building"],
            known["owner_occupied"],
            known["owner_kind"],
        ),
    )
    held = []  # (order inserted, plan, id) of the company's rows that count
    for order, rule in enumerate(plan):
        owner = other.pmc_id if rule["elsewhere"] else pmc.pmc_id
        code, made, starts, ends = insert(conn, owner, rule)
        counts = (
            not rule["elsewhere"]
            and code in MINE
            and starts <= DAY
            and (ends is None or ends > DAY)
        )
        if rule["corrected"]:
            # Corrected by a row with no conditions, which counts in its place.
            fixed = replace_plan(rule)
            _, made, starts, ends = insert(conn, owner, fixed, supersedes=made)
            rule = fixed
            order += 0.5
        if counts:
            held.append((order, rule, made))

    held.sort(key=lambda h: (RANK[h[1]["level"]], -h[1]["precedence"], -h[0]))
    expected, expected_needs = None, []
    for _, rule, made in held:
        found = matches(rule, known)
        if found is False:
            continue
        if found:
            expected = made
        else:
            expected_needs = needs_of(rule, known)
        break
    got = conn.execute(
        "SELECT id FROM trust_jurisdiction_rule(%s, 'deposit_cap', %s)", (prop, DAY)
    ).fetchone()
    needs = conn.execute(
        "SELECT trust_jurisdiction_rule_needs(%s, 'deposit_cap', %s)", (prop, DAY)
    ).fetchone()[0]
    assert (None if got is None else got[0]) == expected
    assert needs == expected_needs
    event("known" if expected else ("needs facts" if expected_needs else "unknown"))


def replace_plan(rule):
    return rule | {
        "built_before": None,
        "units_at_most": None,
        "owner_occupied": None,
        "owner_kind": None,
        "precedence": 0,
    }


def test_the_rule_in_force_is_the_models(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_resolver(conn)


def rule_with(kind, number, unit, combine=None, also=None, also_unit=None):
    return jurisdictions.Rule(
        id=None,  # type: ignore[arg-type]
        level="state",
        jurisdiction="06",
        jurisdiction_name="Synthetic State",
        rule=kind,
        value_kind="number",
        value_number=Decimal(number),
        value_unit=unit,
        combine=combine,
        also_number=None if also is None else Decimal(also),
        also_unit=also_unit,
        value_words=None,
        effective_from=date(2020, 1, 1),
        effective_to=None,
        citation="Synthetic Code 1",
        url="https://example.org/code",
        checked_on=DAY,
        reviewed_by="Synthetic Reviewer",
        exceptions=None,
        source="staff",
    )


def known(rule):
    return jurisdictions.Found(rule.rule, rule, (), None)


cents = st.integers(0, 500_000).map(lambda c: Decimal(c) / 100)
caps = st.one_of(
    st.tuples(st.integers(1, 20_000).map(lambda c: Decimal(c) / 100), st.just("dollars")),
    st.tuples(st.integers(1, 2000).map(lambda c: Decimal(c) / 100), st.just("percent_of_rent")),
    st.tuples(st.integers(1, 2000).map(lambda c: Decimal(c) / 100), st.just("percent_of_unpaid")),
)


@given(
    cap=caps,
    second=st.one_of(st.none(), st.tuples(caps, st.sampled_from(["lesser", "greater"]))),
    flat=cents,
    percent=st.integers(0, 2000).map(lambda c: Decimal(c) / 100),
    maximum=st.one_of(st.none(), cents),
    rent=st.integers(100, 1_000_000).map(lambda c: Decimal(c) / 100),
)
def test_a_late_fee_warns_exactly_when_some_unpaid_amount_goes_past_the_cap(
    cap, second, flat, percent, maximum, rent
):
    if second is None:
        rule = rule_with("late_fee_cap", cap[0], cap[1])
    else:
        (also, also_unit), combine = second
        rule = rule_with("late_fee_cap", cap[0], cap[1], combine, also, also_unit)
    warned = jurisdictions.late_fee_warnings(
        jurisdictions.Found("late_fee_grace_days", None, (), "unknown"),
        known(rule),
        0,
        flat,
        percent,
        maximum,
        [("Unit 1", rent)],
    )

    def fee(unpaid):
        charged = (flat + percent * unpaid / 100).quantize(Decimal("0.01"))
        return charged if maximum is None else min(charged, maximum)

    # Every amount unpaid to the cent on a small rent; on a large one, a spread of them.
    step = max(Decimal("0.01"), (rent / 400).quantize(Decimal("0.01")))
    unpaids = {rent, Decimal("0.01")}
    u = step
    while u <= rent:
        unpaids.add(u)
        u += step
    over = max(fee(u) - jurisdictions.most(rule, rent, u) for u in unpaids if 0 < u <= rent)
    if over > Decimal("0.01"):
        # Past the cap by more than a rounded cent somewhere: between the check's corners both
        # grow in straight lines, so the check found it too.
        assert warned, (rule, flat, percent, maximum, rent)
        event("past the cap")
    if warned:
        # What the warning says is so: at the amount unpaid it names, the terms charge what it
        # says, and that is over what the rule allows there.
        said = warned[0].said
        unpaid = REDUCED.search(said)
        u = rent if unpaid is None else money_of(unpaid.group(1))
        charged, allowed = (money_of(m) for m in CHARGED.search(said).groups())
        assert charged == fee(u) and allowed == jurisdictions.most(rule, rent, u)
        assert charged > allowed


REDUCED = re.compile(r"with \$([\d,]+\.\d\d) of it unpaid")
CHARGED = re.compile(r"charge (?:up to )?\$([\d,]+\.\d\d), over \$([\d,]+\.\d\d)")


def money_of(text):
    return Decimal(text.replace(",", ""))


@given(
    number=st.integers(0, 200),
    given_days=st.integers(0, 400),
)
def test_notice_warns_exactly_when_it_is_shorter_than_the_rule(number, given_days):
    rule = rule_with("rent_increase_notice_days", number, "days")
    effective = date(2027, 3, 1)
    warned = jurisdictions.rent_change_warnings(
        known(rule),
        jurisdictions.Found("rent_cap", None, (), "unknown"),
        effective,
        effective - timedelta(days=given_days),
        Decimal("1000.00"),
        None,
    )
    assert bool(warned) == (given_days < number)


@given(
    months=st.integers(1, 30).map(lambda m: Decimal(m) / 10),
    rent=st.integers(10_000, 500_000).map(lambda c: Decimal(c) / 100),
    held=cents,
    amount=cents,
)
def test_a_deposit_warns_exactly_when_the_lease_would_hold_more_than_the_cap(
    months, rent, held, amount
):
    rule = rule_with("deposit_cap", months, "months_of_rent")
    warned = jurisdictions.deposit_warnings(known(rule), held, amount, rent)
    allowed = (months * rent).quantize(Decimal("0.01"))
    assert bool(warned) == (held + amount > allowed)
