"""State and local rules from a property's address (migration 20261023000202, `pms.jurisdictions`;
ADR 0009; q142). A property's page shows where its address is in the law (found by the geocoder,
or as staff confirm or correct it), the facts rules turn on, and each kind of rule in force today,
cited and dated, or why it is unknown. Staff add a rule they read in a statute's text, correct
one, and confirm the ones the company follows. The checks the forms ask for put the rule beside
the field it limits and warn when what staff typed goes past it; they write nothing and refuse
nothing.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints, model_validator

from pms import jurisdictions
from pms.api.addresses import property_named
from pms.api.deps import Conn, Staff
from pms.api.rent import lease_of, today
from pms.api.setup import Money, Percent
from pms.geocoding import STATES

router = APIRouter()

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class Jurisdiction(BaseModel):
    state_fips: str
    state_name: str
    county_fips: str | None
    county_name: str | None
    cousub_fips: str | None
    cousub_name: str | None
    place_fips: str | None
    place_name: str | None
    tract: str | None
    # "census" or "fake": found by the geocoder; "staff": confirmed or corrected by staff.
    source: str
    recorded_by: str | None
    recorded_at: datetime


class Facts(BaseModel):
    # Each None: not known.
    year_built: int | None
    units_in_building: int | None
    owner_occupied: bool | None
    owner_kind: Literal["person", "company"] | None
    recorded_by: str | None
    recorded_at: datetime | None


class Level(BaseModel):
    """A jurisdiction the property is in, which a rule may be of."""

    level: Literal["federal", "state", "county", "city"]
    jurisdiction: str
    name: str


class Shown(BaseModel):
    """A kind of rule as a page shows it beside the field it limits."""

    rule: jurisdictions.Kind
    label: str
    known: bool
    # "At most 1 month's rent", or why it is unknown ("Unknown: no rule of this kind on file...").
    words: str
    # The facts to put on file before it can be known.
    needs: list[str]
    # What it means here: "For this lease, by 11/21/2026."
    here: str | None = None
    rule_id: UUID | None = None
    level: str | None = None
    jurisdiction_name: str | None = None
    citation: str | None = None
    url: str | None = None
    checked_on: date | None = None
    reviewed_by: str | None = None
    exceptions: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    # The PMS's catalog or the company's staff.
    source: str | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None


class Law(BaseModel):
    day: date
    jurisdiction: Jurisdiction | None
    # True when the property has an address (a jurisdiction can then be found or said).
    has_address: bool
    facts: Facts
    levels: list[Level]
    rules: list[Shown]
    note: str


def staff_name(conn: psycopg.Connection[Any], staff_id: UUID | None) -> str | None:
    if staff_id is None:
        return None
    row = conn.execute("SELECT display_name FROM trust_staff WHERE id = %s", (staff_id,)).fetchone()
    return None if row is None else str(row[0])


def jurisdiction_of(conn: psycopg.Connection[Any], property_id: UUID) -> Jurisdiction | None:
    row = conn.execute(
        "SELECT j.state_fips, j.state_name, j.county_fips, j.county_name, j.cousub_fips,"
        " j.cousub_name, j.place_fips, j.place_name, j.tract, j.source, s.display_name,"
        " j.created_at FROM trust_property_jurisdiction(%s) AS j"
        " LEFT JOIN trust_staff AS s ON s.id = j.recorded_by",
        (property_id,),
    ).fetchone()
    if row is None:
        return None
    return Jurisdiction(
        state_fips=row[0],
        state_name=row[1],
        county_fips=row[2],
        county_name=row[3],
        cousub_fips=row[4],
        cousub_name=row[5],
        place_fips=row[6],
        place_name=row[7],
        tract=row[8],
        source=row[9],
        recorded_by=row[10],
        recorded_at=row[11],
    )


def facts_of(conn: psycopg.Connection[Any], property_id: UUID) -> Facts:
    row = conn.execute(
        "SELECT f.year_built, f.units_in_building, f.owner_occupied, f.owner_kind,"
        " s.display_name, f.created_at FROM trust_property_facts AS f"
        " LEFT JOIN trust_staff AS s ON s.id = f.recorded_by"
        " WHERE f.property_id = %s ORDER BY f.created_at DESC, f.id DESC LIMIT 1",
        (property_id,),
    ).fetchone()
    if row is None:
        return Facts(
            year_built=None,
            units_in_building=None,
            owner_occupied=None,
            owner_kind=None,
            recorded_by=None,
            recorded_at=None,
        )
    return Facts(
        year_built=row[0],
        units_in_building=row[1],
        owner_occupied=row[2],
        owner_kind=row[3],
        recorded_by=row[4],
        recorded_at=row[5],
    )


def levels_of(where: Jurisdiction | None) -> list[Level]:
    found = [Level(level="federal", jurisdiction="US", name="United States")]
    if where is None:
        return found
    found.append(Level(level="state", jurisdiction=where.state_fips, name=where.state_name))
    if where.county_fips and where.county_name:
        found.append(
            Level(
                level="county",
                jurisdiction=where.state_fips + where.county_fips,
                name=where.county_name,
            )
        )
    if where.place_fips and where.place_name:
        found.append(
            Level(
                level="city",
                jurisdiction=where.state_fips + where.place_fips,
                name=where.place_name,
            )
        )
    return found


def shown(
    conn: psycopg.Connection[Any], found: jurisdictions.Found, here: str | None = None
) -> Shown:
    label = jurisdictions.KINDS[found.kind][0]
    if found.rule is None:
        assert found.unknown is not None
        words = found.unknown[0].upper() + found.unknown[1:]
        return Shown(
            rule=found.kind, label=label, known=False, words=words, needs=list(found.needs)
        )
    rule = found.rule
    confirmed = conn.execute(
        "SELECT s.display_name, c.created_at FROM trust_jurisdiction_rule_confirmations AS c"
        " JOIN trust_staff AS s ON s.id = c.confirmed_by WHERE c.rule_id = %s",
        (rule.id,),
    ).fetchone()
    words = jurisdictions.value_words(rule)
    return Shown(
        rule=found.kind,
        label=label,
        known=True,
        words=words[0].upper() + words[1:],
        needs=[],
        here=here,
        rule_id=rule.id,
        level=rule.level,
        jurisdiction_name=rule.jurisdiction_name,
        citation=rule.citation,
        url=rule.url,
        checked_on=rule.checked_on,
        reviewed_by=rule.reviewed_by,
        exceptions=rule.exceptions,
        effective_from=rule.effective_from,
        effective_to=rule.effective_to,
        source=rule.source,
        confirmed_by=None if confirmed is None else confirmed[0],
        confirmed_at=None if confirmed is None else confirmed[1],
    )


@router.get("/api/properties/{property_id}/law")
def law(
    session: Staff, conn: Conn, property_id: UUID, day: Annotated[date | None, Query()] = None
) -> Law:
    """Where the property is in the law, the facts rules turn on, and each kind of rule in force
    on a day (today by default), cited, or why it is unknown."""
    property_named(conn, session.pmc_id, property_id)
    on = day or today()
    where = jurisdiction_of(conn, property_id)
    has_address = (
        conn.execute("SELECT 1 FROM trust_property_address(%s)", (property_id,)).fetchone()
        is not None
    )
    return Law(
        day=on,
        jurisdiction=where,
        has_address=has_address,
        facts=facts_of(conn, property_id),
        levels=levels_of(where),
        rules=[
            shown(conn, jurisdictions.resolve(conn, property_id, kind, on))
            for kind in jurisdictions.KINDS
        ],
        note=jurisdictions.NOTE,
    )


class Place(BaseModel):
    """Where a property's address is in the law, as staff confirm or correct it: its county, county
    subdivision, place (none outside a city, town or village) and tract, by FIPS code and name.
    The state is the address's."""

    county_fips: Annotated[str | None, StringConstraints(pattern=r"^[0-9]{3}$")] = None
    county_name: Name | None = None
    cousub_fips: Annotated[str | None, StringConstraints(pattern=r"^[0-9]{5}$")] = None
    cousub_name: Name | None = None
    place_fips: Annotated[str | None, StringConstraints(pattern=r"^[0-9]{5}$")] = None
    place_name: Name | None = None
    tract: Annotated[str | None, StringConstraints(pattern=r"^[0-9]{6}$")] = None

    @model_validator(mode="after")
    def whole(self) -> Place:
        for code, name, what in (
            (self.county_fips, self.county_name, "county"),
            (self.cousub_fips, self.cousub_name, "county subdivision"),
            (self.place_fips, self.place_name, "city"),
        ):
            if (code is None) != (name is None):
                raise ValueError(f"a {what} is its code and its name, both or neither")
        if (self.cousub_fips or self.tract) and not self.county_fips:
            raise ValueError("a county subdivision or tract is in a county: name it too")
        return self


@router.post("/api/properties/{property_id}/jurisdiction", status_code=201)
def set_jurisdiction(session: Staff, conn: Conn, property_id: UUID, place: Place) -> Jurisdiction:
    """Staff confirm or correct where the property's address is in the law: in force from now
    for that address, naming who."""
    property_named(conn, session.pmc_id, property_id)
    row = conn.execute(
        "SELECT id, state FROM trust_property_address(%s)", (property_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(409, "the property has no address yet: add its address first")
    address_id, state = row
    state_fips, state_name = STATES[state]
    conn.execute(
        "INSERT INTO trust_property_jurisdictions (pmc_id, address_id, state_fips, state_name,"
        " county_fips, county_name, cousub_fips, cousub_name, place_fips, place_name, tract,"
        " source) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'staff')",
        (
            session.pmc_id,
            address_id,
            state_fips,
            state_name,
            place.county_fips,
            place.county_name,
            place.cousub_fips,
            place.cousub_name,
            place.place_fips,
            place.place_name,
            place.tract,
        ),
    )
    found = jurisdiction_of(conn, property_id)
    assert found is not None
    return found


class NewFacts(BaseModel):
    year_built: Annotated[int | None, Field(ge=1600, le=2200)] = None
    units_in_building: Annotated[int | None, Field(ge=1, le=100_000)] = None
    owner_occupied: bool | None = None
    owner_kind: Literal["person", "company"] | None = None


@router.post("/api/properties/{property_id}/facts", status_code=204)
def set_facts(session: Staff, conn: Conn, property_id: UUID, new: NewFacts) -> None:
    """The facts rules turn on, from now (each None: not known). The same again writes
    nothing."""
    property_named(conn, session.pmc_id, property_id)
    now = facts_of(conn, property_id)
    if now.recorded_at is not None and (
        now.year_built,
        now.units_in_building,
        now.owner_occupied,
        now.owner_kind,
    ) == (new.year_built, new.units_in_building, new.owner_occupied, new.owner_kind):
        return
    conn.execute(
        "INSERT INTO trust_property_facts (pmc_id, property_id, year_built, units_in_building,"
        " owner_occupied, owner_kind) VALUES (%s, %s, %s, %s, %s, %s)",
        (
            session.pmc_id,
            property_id,
            new.year_built,
            new.units_in_building,
            new.owner_occupied,
            new.owner_kind,
        ),
    )


Unit = Literal[
    "days", "months_of_rent", "percent_of_rent", "percent_of_unpaid", "dollars", "percent_a_year"
]
Words = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class NewRule(BaseModel):
    """A rule staff read in a statute's text, for one of the property's jurisdictions."""

    property_id: UUID
    level: Literal["federal", "state", "county", "city"]
    rule: jurisdictions.Kind
    value_kind: Literal["number", "none", "words"]
    value_number: Annotated[Decimal | None, Field(ge=0, le=1_000_000, decimal_places=4)] = None
    value_unit: Unit | None = None
    combine: Literal["lesser", "greater"] | None = None
    also_number: Annotated[Decimal | None, Field(ge=0, le=1_000_000, decimal_places=4)] = None
    also_unit: Unit | None = None
    value_words: Words | None = None
    built_before: Annotated[int | None, Field(ge=1600, le=2200)] = None
    built_on_or_after: Annotated[int | None, Field(ge=1600, le=2200)] = None
    units_at_least: Annotated[int | None, Field(ge=1, le=100_000)] = None
    units_at_most: Annotated[int | None, Field(ge=1, le=100_000)] = None
    owner_occupied: bool | None = None
    owner_kind: Literal["person", "company"] | None = None
    precedence: Annotated[int, Field(ge=0, le=100)] = 0
    effective_from: date
    effective_to: date | None = None
    citation: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    url: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern=r"^https://\S+$", max_length=500)
    ]
    checked_on: date
    reviewed_by: Name
    exceptions: Words | None = None
    # The rule this one corrects.
    supersedes: UUID | None = None


# What a refused rule's constraint means, in words.
RULE_REFUSALS = {
    "trust_jurisdiction_rules_value": "a number needs its unit, and words need their text",
    "trust_jurisdiction_rules_second": "a second number needs its unit and lesser or greater",
    "trust_jurisdiction_rules_second_of_number": "lesser or greater goes with a number",
    "trust_jurisdiction_rules_units": "that unit doesn't fit that kind of rule",
    "trust_jurisdiction_rules_days": "a rule stops holding after the day it starts",
    "trust_jurisdiction_rules_built": "built before must be a later year than built on or after",
    "trust_jurisdiction_rules_unit_count": "at most so many units must be at least so many",
    "trust_jurisdiction_rules_checked_on_check": "the day it was checked is 2020 or later",
}


class Created(BaseModel):
    id: UUID


@router.post("/api/jurisdiction-rules", status_code=201)
def add_rule(session: Staff, conn: Conn, new: NewRule) -> Created:
    """A rule of one of the property's jurisdictions, applying to every property of the company in
    it, confirmed by whoever adds it. Naming a rule it corrects, that one no longer counts."""
    property_named(conn, session.pmc_id, new.property_id)
    if new.checked_on > today():
        raise HTTPException(422, "the day the statute was read can't be after today")
    level = next(
        (lv for lv in levels_of(jurisdiction_of(conn, new.property_id)) if lv.level == new.level),
        None,
    )
    if level is None:
        raise HTTPException(
            409,
            f"where the property is in the law has no {new.level}: find it on the map, or say"
            " where it is",
        )
    if new.supersedes is not None and (
        conn.execute(
            "SELECT 1 FROM trust_jurisdiction_rules WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, new.supersedes),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such rule to correct")
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO trust_jurisdiction_rules (pmc_id, level, jurisdiction,"
                " jurisdiction_name, rule, value_kind, value_number, value_unit, combine,"
                " also_number, also_unit, value_words, built_before, built_on_or_after,"
                " units_at_least, units_at_most, owner_occupied, owner_kind, precedence,"
                " effective_from, effective_to, citation, url, checked_on, reviewed_by,"
                " exceptions, supersedes, source) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " 'staff') RETURNING id",
                (
                    session.pmc_id,
                    new.level,
                    level.jurisdiction,
                    level.name,
                    new.rule,
                    new.value_kind,
                    new.value_number,
                    new.value_unit,
                    new.combine,
                    new.also_number,
                    new.also_unit,
                    new.value_words,
                    new.built_before,
                    new.built_on_or_after,
                    new.units_at_least,
                    new.units_at_most,
                    new.owner_occupied,
                    new.owner_kind,
                    new.precedence,
                    new.effective_from,
                    new.effective_to,
                    new.citation,
                    new.url,
                    new.checked_on,
                    new.reviewed_by,
                    new.exceptions,
                    new.supersedes,
                ),
            ).fetchone()
            assert row is not None
            conn.execute(
                "INSERT INTO trust_jurisdiction_rule_confirmations (rule_id, pmc_id, confirmed_by)"
                " VALUES (%s, %s, %s)",
                (row[0], session.pmc_id, session.person_id),
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "that rule was corrected already: correct the newer one") from None
    except psycopg.errors.CheckViolation as refused:
        name = refused.diag.constraint_name or ""
        words = RULE_REFUSALS.get(name) or (refused.diag.message_primary or "").removeprefix(
            "trust: "
        )
        raise HTTPException(422, words) from None
    return Created(id=row[0])


@router.post("/api/jurisdiction-rules/{rule_id}/confirm", status_code=204)
def confirm_rule(session: Staff, conn: Conn, rule_id: UUID) -> None:
    """The company follows this rule, as this staff member confirms; once per rule (again
    changes nothing)."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_jurisdiction_rules WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, rule_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such rule")
    conn.execute(
        "INSERT INTO trust_jurisdiction_rule_confirmations (rule_id, pmc_id, confirmed_by)"
        " VALUES (%s, %s, %s) ON CONFLICT (rule_id) DO NOTHING",
        (rule_id, session.pmc_id, session.person_id),
    )


# --- the checks the forms ask for ----------------------------------------------------------------


class Warning(BaseModel):
    rule: jurisdictions.Kind
    said: str


class Checked(BaseModel):
    # The rules beside the fields, known or not.
    rules: list[Shown]
    # What goes past a known rule, in words: a warning, never a refusal.
    warnings: list[Warning]
    note: str


def checked(
    conn: psycopg.Connection[Any],
    found: list[jurisdictions.Found],
    warnings: list[jurisdictions.Warning],
    here: dict[str, str] | None = None,
) -> Checked:
    return Checked(
        rules=[shown(conn, f, (here or {}).get(f.kind)) for f in found],
        warnings=[Warning(rule=w.rule, said=w.said) for w in warnings],
        note=jurisdictions.NOTE,
    )


RENTS = """
SELECT u.display_name, trust_rent_on(l.id, %(on)s) FROM trust_leases AS l
JOIN trust_units AS u ON u.id = l.unit_id
WHERE u.property_id = %(property)s AND l.pmc_id = %(pmc)s AND l.starts_on <= %(on)s
  AND (l.ends_on IS NULL OR l.ends_on >= %(on)s)
ORDER BY u.display_name COLLATE "C", l.id
"""


@router.get("/api/law/late-fees")
def check_late_fees(
    session: Staff,
    conn: Conn,
    property_id: Annotated[UUID, Query()],
    starts_on: Annotated[date | None, Query()] = None,
    grace_days: Annotated[int | None, Query(ge=0, le=365)] = None,
    flat_fee: Annotated[Money | None, Query()] = None,
    percent: Annotated[Percent | None, Query()] = None,
    maximum: Annotated[Money | None, Query()] = None,
) -> Checked:
    """Late fee terms as typed against the days before a late fee and the most one may be, in
    force on the day they start (today at the earliest), at the rent of each lease in effect
    then. Without grace days, the rules alone."""
    property_named(conn, session.pmc_id, property_id)
    on = max(starts_on or today(), today())
    grace = jurisdictions.resolve(conn, property_id, "late_fee_grace_days", on)
    cap = jurisdictions.resolve(conn, property_id, "late_fee_cap", on)
    warnings: list[jurisdictions.Warning] = []
    if grace_days is not None:
        rents = conn.execute(
            RENTS, {"on": on, "property": property_id, "pmc": session.pmc_id}
        ).fetchall()
        warnings = jurisdictions.late_fee_warnings(
            grace,
            cap,
            grace_days,
            flat_fee or Decimal(0),
            percent or Decimal(0),
            maximum,
            [(str(unit), Decimal(rent)) for unit, rent in rents],
        )
    return checked(conn, [grace, cap], warnings)


@router.get("/api/law/nsf-fee")
def check_nsf_fee(
    session: Staff,
    conn: Conn,
    property_id: Annotated[UUID, Query()],
    starts_on: Annotated[date | None, Query()] = None,
    fee: Annotated[Money | None, Query()] = None,
) -> Checked:
    """An NSF fee as typed against the most one may be, in force on the day it starts."""
    property_named(conn, session.pmc_id, property_id)
    on = max(starts_on or today(), today())
    cap = jurisdictions.resolve(conn, property_id, "nsf_fee_cap", on)
    warnings = [] if fee is None else jurisdictions.nsf_fee_warnings(cap, fee)
    return checked(conn, [cap], warnings)


# The lease a tenant's deposit is for: theirs in effect on the day, else the next to start.
TENANT_LEASE = """
SELECT l.id, trust_rent_on(l.id, greatest(l.starts_on, %(on)s)) FROM trust_lease_tenants AS lt
JOIN trust_leases AS l ON l.id = lt.lease_id
WHERE lt.tenant_id = %(tenant)s AND (l.ends_on IS NULL OR l.ends_on >= %(on)s)
ORDER BY l.starts_on > %(on)s, l.starts_on, l.id
LIMIT 1
"""

# Every kind of deposit held for a lease's tenants.
HELD = """
SELECT coalesce(sum(b.balance), 0) FROM trust_lease_tenants AS lt
JOIN trust_ledger_accounts AS a ON a.tenant_id = lt.tenant_id AND a.kind = 'tenant_deposit'
JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
WHERE lt.lease_id = %s
"""


@router.get("/api/law/deposit")
def check_deposit(
    session: Staff,
    conn: Conn,
    tenant_id: Annotated[UUID, Query()],
    amount: Annotated[Money | None, Query()] = None,
    received_on: Annotated[date | None, Query()] = None,
) -> Checked:
    """A deposit as typed for a tenant against the most a security deposit may be, in force on the
    day it is received: with what is held for their lease already, every kind together, at the
    lease's monthly rent."""
    row = conn.execute(
        "SELECT property_id FROM trust_tenants WHERE pmc_id = %s AND id = %s",
        (session.pmc_id, tenant_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such tenant")
    on = received_on or today()
    cap = jurisdictions.resolve(conn, row[0], "deposit_cap", on)
    warnings: list[jurisdictions.Warning] = []
    if amount is not None and cap.rule is not None:
        lease = conn.execute(TENANT_LEASE, {"tenant": tenant_id, "on": on}).fetchone()
        held = Decimal(0)
        rent = None
        if lease is not None:
            rent = Decimal(lease[1])
            total = conn.execute(HELD, (lease[0],)).fetchone()
            held = Decimal(total[0]) if total is not None else Decimal(0)
        warnings = jurisdictions.deposit_warnings(cap, held, amount, rent)
    return checked(conn, [cap], warnings)


# The lowest rent of a lease in the 12 months before a day, back through the leases it renewed.
LOWEST = """
WITH RECURSIVE chain (lease_id, depth) AS (
    SELECT %(lease)s::uuid, 0
    UNION ALL
    SELECT o.renews_lease_id, c.depth + 1
    FROM chain AS c
    JOIN trust_lease_offer_completions AS oc ON oc.lease_id = c.lease_id
    JOIN trust_lease_offers AS o ON o.id = oc.offer_id
    WHERE o.renews_lease_id IS NOT NULL AND c.depth < 100
)
SELECT min(trust_rent_on(l.id, d.day))
FROM chain JOIN trust_leases AS l ON l.id = chain.lease_id
CROSS JOIN LATERAL (
    SELECT greatest(l.starts_on, %(on)s::date - 365) AS day
    UNION
    SELECT r.effective_on FROM trust_rent_changes AS r
    WHERE r.lease_id = l.id AND r.effective_on < %(on)s
      AND r.effective_on >= %(on)s::date - 365
) AS d
WHERE d.day < %(on)s AND d.day >= l.starts_on AND (l.ends_on IS NULL OR d.day <= l.ends_on)
"""


@router.get("/api/law/rent-change")
def check_rent_change(
    session: Staff,
    conn: Conn,
    lease_id: Annotated[UUID, Query()],
    effective_on: Annotated[date | None, Query()] = None,
    notice_given_on: Annotated[date | None, Query()] = None,
    monthly_rent: Annotated[Money | None, Query()] = None,
) -> Checked:
    """A rent change as typed against the notice it needs and the most rent may rise in a year,
    in force on the day it takes effect: over the lowest rent of the lease (and the leases it
    renewed) in the 12 months before."""
    lease = lease_of(conn, session.pmc_id, lease_id)
    on = effective_on or today()
    notice = jurisdictions.resolve(conn, lease.property_id, "rent_increase_notice_days", on)
    cap = jurisdictions.resolve(conn, lease.property_id, "rent_cap", on)
    warnings: list[jurisdictions.Warning] = []
    if effective_on is not None and notice_given_on is not None and monthly_rent is not None:
        row = conn.execute(LOWEST, {"lease": lease_id, "on": effective_on}).fetchone()
        lowest = None if row is None or row[0] is None else Decimal(row[0])
        warnings = jurisdictions.rent_change_warnings(
            notice, cap, effective_on, notice_given_on, monthly_rent, lowest
        )
    return checked(conn, [notice, cap], warnings)


@router.get("/api/law/deposit-return")
def check_deposit_return(session: Staff, conn: Conn, lease_id: Annotated[UUID, Query()]) -> Checked:
    """The days a lease's deposits must be returned within, in force on its last day, and the day
    that comes to for it."""
    lease = lease_of(conn, session.pmc_id, lease_id)
    ends = conn.execute("SELECT ends_on FROM trust_leases WHERE id = %s", (lease_id,)).fetchone()
    last = ends[0] if ends is not None and ends[0] is not None else None
    found = jurisdictions.resolve(conn, lease.property_id, "deposit_return_days", last or today())
    here = None
    within = None if found.rule is None else jurisdictions.whole_days(found.rule)
    if last is not None and within is not None:
        here = (
            f"For this lease, by {last + timedelta(days=within):%m/%d/%Y}, counted from its last"
            " day; check the rule for when its days start."
        )
    return checked(conn, [found], [], {"deposit_return_days": here} if here else None)
