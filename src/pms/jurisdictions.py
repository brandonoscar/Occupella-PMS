"""State and local rules from a property's address (migration 20261023000202; the founder,
2026-10-23; ADR 0009; q142; docs/law.md, State and local rules).

A rule is a row of trust_jurisdiction_rules: a jurisdiction's number for a kind of term, cited,
dated and checked by someone who read the statute's text. trust_jurisdiction_rule resolves the one
in force for a property on a day; none reads as unknown, never as "no rule". The checks here put a
rule beside the field it limits and warn when what staff typed goes past it. They only warn: the
company answers for the rules it follows (ADR 0009) and saves either way, and a law's exceptions
the PMS can't see are in the rule's own words. No AI decides a legal value: everything here is
arithmetic on the rule's numbers.

The PMS's own rules come from its catalog (jurisdiction_rules.toml beside this file), each read in
the statute's text and written up in docs/law.md; `seed` copies the ones a company lacks into its
rules each night. The catalog holds only rules confirmed against a statute's text.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import psycopg

CENT = Decimal("0.01")
ONE_DOLLAR = Decimal("1.00")
CATALOG = Path(__file__).with_name("jurisdiction_rules.toml")

Kind = Literal[
    "deposit_cap",
    "deposit_return_days",
    "late_fee_cap",
    "late_fee_grace_days",
    "nsf_fee_cap",
    "rent_increase_notice_days",
    "rent_cap",
]

# Each kind of rule: what it is called on screen, and whether it is the most a term may be (a cap)
# or the least (a wait, a notice).
KINDS: dict[Kind, tuple[str, Literal["most", "least"]]] = {
    "deposit_cap": ("Security deposit", "most"),
    "deposit_return_days": ("Deposit returned within", "most"),
    "late_fee_grace_days": ("Days before a late fee", "least"),
    "late_fee_cap": ("Late fee", "most"),
    "nsf_fee_cap": ("Returned payment (NSF) fee", "most"),
    "rent_increase_notice_days": ("Notice of a rent increase", "least"),
    "rent_cap": ("Rent increase in a year", "most"),
}

# A fact a rule may turn on, in words.
FACTS = {
    "year_built": "the year built",
    "units_in_building": "the units in the building",
    "owner_occupied": "whether an owner lives there",
    "owner_kind": "whether the owner is a person or a company",
}

# Shown wherever a rule is.
NOTE = (
    "Each rule is cited information, read in the statute on the day shown, never a guarantee or"
    " legal advice: your company answers for the rules it follows and confirms each one. The PMS"
    " only warns; a law's exceptions it can't see are in the rule's own words."
)


@dataclass(frozen=True)
class Rule:
    """A row of trust_jurisdiction_rules."""

    id: UUID
    level: str
    jurisdiction: str
    jurisdiction_name: str
    rule: Kind
    value_kind: Literal["number", "none", "words"]
    value_number: Decimal | None
    value_unit: str | None
    combine: Literal["lesser", "greater"] | None
    also_number: Decimal | None
    also_unit: str | None
    value_words: str | None
    effective_from: date
    effective_to: date | None
    citation: str
    url: str
    checked_on: date
    reviewed_by: str
    exceptions: str | None
    source: Literal["pms", "staff"]


RULE_COLUMNS = (
    "id, level, jurisdiction, jurisdiction_name, rule, value_kind, value_number, value_unit,"
    " combine, also_number, also_unit, value_words, effective_from, effective_to, citation, url,"
    " checked_on, reviewed_by, exceptions, source"
)


def rule_of(row: Sequence[Any]) -> Rule:
    return Rule(*row)


@dataclass(frozen=True)
class Found:
    """What the PMS knows of a kind of rule for a property on a day: the rule, or why it is
    unknown."""

    kind: Kind
    rule: Rule | None
    # The facts to put on file before it can be known.
    needs: tuple[str, ...]
    # Why it is unknown, in words; None when known.
    unknown: str | None


def place_words(conn: psycopg.Connection[Any], property_id: UUID) -> str | None:
    """Where the property is in the law, in words ("Demo City city, County 101, California"), or
    None when that isn't on file."""
    row = conn.execute(
        "SELECT place_name, county_name, state_name FROM trust_property_jurisdiction(%s)",
        (property_id,),
    ).fetchone()
    if row is None:
        return None
    return ", ".join(part for part in row if part)


def resolve(conn: psycopg.Connection[Any], property_id: UUID, kind: Kind, day: date) -> Found:
    """The rule of a kind in force for a property on a day, or why it is unknown."""
    if conn.execute("SELECT 1 FROM trust_property_address(%s)", (property_id,)).fetchone() is None:
        return Found(kind, None, (), "unknown: the property has no address on file")
    where = place_words(conn, property_id)
    if where is None:
        return Found(
            kind,
            None,
            (),
            "unknown: where the property's address is in the law isn't on file yet (find it on"
            " the map, or say where it is)",
        )
    row = conn.execute(
        f"SELECT {RULE_COLUMNS} FROM trust_jurisdiction_rule(%s, %s, %s)",  # noqa: S608
        (property_id, kind, day),
    ).fetchone()
    if row is not None:
        return Found(kind, rule_of(row), (), None)
    needed = conn.execute(
        "SELECT trust_jurisdiction_rule_needs(%s, %s, %s)", (property_id, kind, day)
    ).fetchone()
    needs = tuple(needed[0]) if needed is not None else ()
    if needs:
        facts = " and ".join(FACTS[f] for f in needs)
        verb = "is" if len(needs) == 1 else "are"
        return Found(kind, None, needs, f"unknown until {facts} {verb} on file for the property")
    return Found(kind, None, (), f"unknown: no rule of this kind on file for {where}")


def money(amount: Decimal) -> str:
    return f"${amount:,.2f}"


def number(value: Decimal) -> str:
    """A rule's number as people write it: 1, 1.5, 9.683."""
    text = format(value.normalize(), "f")
    return text


def days(value: Decimal) -> str:
    return f"{number(value)} day" if value == 1 else f"{number(value)} days"


def part_words(value: Decimal, unit: str) -> str:
    if unit == "dollars":
        return money(value)
    if unit == "months_of_rent":
        return f"{number(value)} month's rent" if value == 1 else f"{number(value)} months' rent"
    if unit == "percent_of_rent":
        return f"{number(value)}% of the monthly rent"
    if unit == "percent_of_unpaid":
        return f"{number(value)}% of the rent unpaid"
    if unit == "percent_a_year":
        return f"{number(value)}% a year"
    return days(value)


def value_words(rule: Rule) -> str:
    """The rule's value in words: "at most the lesser of $50.00 or 5% of the monthly rent"."""
    if rule.value_kind == "none":
        return "no limit"
    if rule.value_kind == "words":
        assert rule.value_words is not None
        return rule.value_words
    assert rule.value_number is not None and rule.value_unit is not None
    first = part_words(rule.value_number, rule.value_unit)
    if rule.combine is not None:
        assert rule.also_number is not None and rule.also_unit is not None
        first = f"the {rule.combine} of {first} or {part_words(rule.also_number, rule.also_unit)}"
    most = KINDS[rule.rule][1] == "most"
    if rule.rule == "deposit_return_days":
        return f"within {first} after the tenancy ends"
    return f"at most {first}" if most else f"at least {first}"


def percent_of(amount: Decimal, percent: Decimal) -> Decimal:
    return (amount * percent / 100).quantize(CENT, ROUND_HALF_UP)


def part(value: Decimal, unit: str, rent: Decimal, unpaid: Decimal) -> Decimal:
    if unit == "dollars":
        return value
    if unit == "months_of_rent":
        return (value * rent).quantize(CENT, ROUND_HALF_UP)
    if unit == "percent_of_rent":
        return percent_of(rent, value)
    if unit == "percent_of_unpaid":
        return percent_of(unpaid, value)
    raise ValueError(f"{unit} is no amount of money")


def most(rule: Rule, rent: Decimal, unpaid: Decimal | None = None) -> Decimal | None:
    """The most a money rule allows on a monthly rent, with `unpaid` of it unpaid (the whole rent
    by default); None when it sets no number."""
    if rule.value_kind != "number":
        return None
    assert rule.value_number is not None and rule.value_unit is not None
    late = rent if unpaid is None else unpaid
    first = part(rule.value_number, rule.value_unit, rent, late)
    if rule.combine is None:
        return first
    assert rule.also_number is not None and rule.also_unit is not None
    second = part(rule.also_number, rule.also_unit, rent, late)
    return min(first, second) if rule.combine == "lesser" else max(first, second)


def turns(rule: Rule, rent: Decimal) -> list[Decimal]:
    """The amounts unpaid of a monthly rent at which a lesser or greater of a part fixed for the
    month (dollars, or a percent of the rent) and a percent of what is unpaid changes which one
    counts."""
    if rule.combine is None or rule.value_number is None or rule.also_number is None:
        return []
    pairs = [(rule.value_number, rule.value_unit), (rule.also_number, rule.also_unit)]
    fixed = [part(v, str(u), rent, rent) for v, u in pairs if u != "percent_of_unpaid"]
    share = [v for v, u in pairs if u == "percent_of_unpaid" and v > 0]
    if not fixed or not share:
        return []
    turn = fixed[0] * 100 / share[0]
    return [turn.quantize(CENT), (turn + CENT).quantize(CENT)]


def whole_days(rule: Rule) -> int | None:
    if rule.value_kind != "number" or rule.value_number is None:
        return None
    return int(rule.value_number)


@dataclass(frozen=True)
class Warning:
    rule: Kind
    said: str


def cite(rule: Rule) -> str:
    return f"{rule.jurisdiction_name}, {rule.citation}"


def late_fee_warnings(
    grace: Found,
    cap: Found,
    grace_days: int,
    flat_fee: Decimal,
    percent: Decimal,
    maximum: Decimal | None,
    rents: Sequence[tuple[str, Decimal]],
) -> list[Warning]:
    """Late fee terms (grace days, a flat fee, a percent of the rent left unpaid, at most a
    maximum) against the rules in force: the days before a late fee, and the most one may be at
    each rent of `rents` ((which lease, monthly rent)), with all of it unpaid, a cent or a dollar
    of it, and where the fee or the rule changes how it grows (between those both grow in straight
    lines, to the cent)."""
    warnings = []
    wait = None if grace.rule is None else whole_days(grace.rule)
    if grace.rule is not None and wait is not None and grace_days < wait:
        warnings.append(
            Warning(
                "late_fee_grace_days",
                f"{cite(grace.rule)}: no late fee until rent is unpaid {days(Decimal(wait))}"
                f" after its due date. These terms charge one after {days(Decimal(grace_days))}"
                f" of grace; {wait} or more meets it.",
            )
        )
    if cap.rule is None or cap.rule.value_kind != "number":
        return warnings
    rule = cap.rule

    def fee(unpaid: Decimal) -> Decimal:
        charged = (flat_fee + percent * unpaid / 100).quantize(CENT, ROUND_HALF_UP)
        return charged if maximum is None else min(charged, maximum)

    over = []
    for what, rent in rents:
        unpaids = {rent, CENT, min(rent, ONE_DOLLAR), *turns(rule, rent)}
        if maximum is not None and percent > 0:
            reached = (maximum - flat_fee) * 100 / percent
            unpaids |= {reached.quantize(CENT), (reached + CENT).quantize(CENT)}
        for unpaid in sorted((u for u in unpaids if 0 < u <= rent), reverse=True):
            allowed = most(rule, rent, unpaid)
            assert allowed is not None
            if fee(unpaid) > allowed:
                over.append((what, rent, unpaid, fee(unpaid), allowed))
                break
    if not rents:
        # No lease to work it out on: a flat fee alone over a cap in dollars is past it anyway.
        allowed = most(rule, Decimal(0), Decimal(0)) if rule.value_unit == "dollars" else None
        if allowed is not None and rule.combine is None and fee(Decimal(0)) > allowed:
            over.append(("any lease", Decimal(0), Decimal(0), fee(Decimal(0)), allowed))
    if over:
        what, rent, unpaid, charged, allowed = over[0]
        others = len(over) - 1
        more = f"; {others} more {'lease' if others == 1 else 'leases'} too" if others else ""
        if rent == 0:
            when = f"These terms charge {money(charged)}"
        elif unpaid == rent:
            when = f"On {what}'s rent of {money(rent)} these terms charge up to {money(charged)}"
        else:
            when = (
                f"On {what}'s rent of {money(rent)}, with {money(unpaid)} of it unpaid, these"
                f" terms charge {money(charged)}"
            )
        warnings.append(
            Warning(
                "late_fee_cap",
                f"{cite(rule)}: a late fee is {value_words(rule)}. {when}, over"
                f" {money(allowed)}{more}.",
            )
        )
    return warnings


def nsf_fee_warnings(cap: Found, fee: Decimal) -> list[Warning]:
    if cap.rule is None:
        return []
    allowed = most(cap.rule, Decimal(0))
    if allowed is None or fee <= allowed:
        return []
    return [
        Warning(
            "nsf_fee_cap",
            f"{cite(cap.rule)}: a returned payment fee is {value_words(cap.rule)}. This fee is"
            f" {money(fee)}.",
        )
    ]


def deposit_warnings(
    cap: Found, held: Decimal, amount: Decimal, rent: Decimal | None
) -> list[Warning]:
    """A deposit of `amount` beside `held` already held for the lease, every kind together,
    against the most the rule allows at the lease's monthly rent."""
    if cap.rule is None:
        return []
    if rent is None:
        allowed = most(cap.rule, Decimal(0)) if cap.rule.value_unit == "dollars" else None
        if cap.rule.combine is not None:
            allowed = None
    else:
        allowed = most(cap.rule, rent)
    total = held + amount
    if allowed is None or total <= allowed:
        return []
    with_held = f", with {money(held)} held already," if held else ""
    return [
        Warning(
            "deposit_cap",
            f"{cite(cap.rule)}: a security deposit is {value_words(cap.rule)}"
            + (f" (on this lease's rent of {money(rent)}, {money(allowed)})" if rent else "")
            + f". This deposit{with_held} comes to {money(total)} for the lease, every kind"
            " together.",
        )
    ]


def rent_change_warnings(
    notice: Found,
    cap: Found,
    effective_on: date,
    notice_given_on: date,
    monthly_rent: Decimal,
    lowest: Decimal | None,
) -> list[Warning]:
    """A rent change from `effective_on` to `monthly_rent`, written notice given on
    `notice_given_on`, against the notice it needs and the most rent may rise in a year: over
    `lowest`, the lowest rent of the lease in the 12 months before."""
    warnings = []
    need = None if notice.rule is None else whole_days(notice.rule)
    given = (effective_on - notice_given_on).days
    if notice.rule is not None and need is not None and given < need:
        warnings.append(
            Warning(
                "rent_increase_notice_days",
                f"{cite(notice.rule)}: a rent increase needs {value_words(notice.rule)} of written"
                f" notice. Notice given {notice_given_on:%m/%d/%Y} is {days(Decimal(given))}"
                f" before {effective_on:%m/%d/%Y}.",
            )
        )
    if (
        cap.rule is not None
        and cap.rule.value_kind == "number"
        and cap.rule.value_number is not None
        and lowest is not None
        and lowest > 0
        and monthly_rent > lowest
    ):
        rise = ((monthly_rent - lowest) * 100 / lowest).quantize(Decimal("0.01"), ROUND_HALF_UP)
        if rise > cap.rule.value_number:
            warnings.append(
                Warning(
                    "rent_cap",
                    f"{cite(cap.rule)}: rent may rise {value_words(cap.rule)}. From the lowest"
                    f" rent of the 12 months before, {money(lowest)}, {money(monthly_rent)} is"
                    f" {rise}% more.",
                )
            )
    return warnings


# --- the PMS's catalog ---------------------------------------------------------------------------

CATALOG_FIELDS = (
    "level",
    "jurisdiction",
    "jurisdiction_name",
    "rule",
    "value_kind",
    "value_number",
    "value_unit",
    "combine",
    "also_number",
    "also_unit",
    "value_words",
    "built_before",
    "built_on_or_after",
    "units_at_least",
    "units_at_most",
    "owner_occupied",
    "owner_kind",
    "precedence",
    "effective_from",
    "effective_to",
    "citation",
    "url",
    "checked_on",
    "reviewed_by",
    "exceptions",
)


def catalog(path: Path = CATALOG) -> list[dict[str, Any]]:
    """The PMS's rules: each a table of `[[rule]]` with its key and the columns of
    trust_jurisdiction_rules, maybe `supersedes` naming an earlier key."""
    with path.open("rb") as file:
        found = tomllib.load(file)
    rules = found.get("rule", [])
    assert isinstance(rules, list)
    return rules


def seed(
    conn: psycopg.Connection[Any], pmc_id: UUID, rules: Iterable[dict[str, Any]] | None = None
) -> int:
    """Adds the catalog's rules a company lacks to its rules, in order, each under its key, and
    returns how many. A rule correcting an earlier key supersedes the company's row of it."""
    added = 0
    for entry in catalog() if rules is None else rules:
        key = entry["key"]
        if conn.execute(
            "SELECT 1 FROM trust_jurisdiction_rules WHERE pmc_id = %s AND catalog_key = %s",
            (pmc_id, key),
        ).fetchone():
            continue
        supersedes = None
        if entry.get("supersedes"):
            row = conn.execute(
                "SELECT id FROM trust_jurisdiction_rules WHERE pmc_id = %s AND catalog_key = %s",
                (pmc_id, entry["supersedes"]),
            ).fetchone()
            supersedes = None if row is None else row[0]
        values = [entry.get(field) for field in CATALOG_FIELDS]
        values[CATALOG_FIELDS.index("precedence")] = entry.get("precedence", 0)
        conn.execute(
            f"INSERT INTO trust_jurisdiction_rules (pmc_id, {', '.join(CATALOG_FIELDS)},"  # noqa: S608
            " supersedes, source, catalog_key)"
            f" VALUES (%s, {', '.join(['%s'] * len(CATALOG_FIELDS))}, %s, 'pms', %s)",
            (pmc_id, *values, supersedes, key),
        )
        added += 1
    return added
