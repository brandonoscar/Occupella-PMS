"""State rules as warnings (q142, `pms.state_rules`): the table on Settings, State rules, and the
checks the pages ask for where staff set the terms. Each check takes the terms as typed (to warn
before a save) or, without them, checks what is on file. Nothing here writes; a check only warns.
A property's state is the one on its address; with no address on file, nothing is checked.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from pms import state_rules
from pms.api.deps import Conn, Staff
from pms.api.rent import lease_of, today
from pms.api.setup import Money, Percent, found

router = APIRouter(prefix="/api/state-rules")


class Rule(BaseModel):
    words: str
    cite: str
    url: str
    how: state_rules.How


class StateRow(BaseModel):
    state: str
    name: str
    # "Rent unpaid five days after its due date; at most the lesser of $50 or 5% of the rent."
    late_fee: Rule | None
    rent_increase: Rule | None
    entry: Rule | None


class Table(BaseModel):
    checked_on: date
    note: str
    states: list[StateRow]


def late_fee_words(rule: state_rules.LateFeeRule) -> str:
    parts = []
    if rule.wait_words:
        parts.append(f"no late fee until {rule.wait_words}")
    if rule.cap_words:
        parts.append(f"at most {rule.cap_words}")
    return "; ".join(parts)


@router.get("")
def table(session: Staff) -> Table:
    """Every state the table has rules for, A to Z by its code, with each rule's statute and how
    it was checked."""
    rows = []
    for code in sorted(state_rules.RULES):
        rules = state_rules.RULES[code]
        late, rent, entry = rules.late_fee, rules.rent_notice, rules.entry
        rows.append(
            StateRow(
                state=code,
                name=rules.name,
                late_fee=None
                if late is None
                else Rule(words=late_fee_words(late), **source(late.source)),
                rent_increase=None
                if rent is None
                else Rule(words=state_rules.rent_notice_words(rent), **source(rent.source)),
                entry=None if entry is None else Rule(words=entry.words, **source(entry.source)),
            )
        )
    return Table(checked_on=state_rules.CHECKED_ON, note=state_rules.NOT_ADVICE, states=rows)


def source(of: state_rules.Source) -> dict[str, Any]:
    return {"cite": of.cite, "url": of.url, "how": of.how}


class Warning(BaseModel):
    rule: state_rules.Rule
    said: str
    cite: str
    how: state_rules.How


class Checked(BaseModel):
    # The property's state, from its address; None with no address on file (nothing checked).
    state: str | None
    # Whether the table has a rule of this kind for the state.
    rules: bool
    warnings: list[Warning]
    note: str


def checked(state: str | None, has: bool, warnings: list[state_rules.StateWarning]) -> Checked:
    return Checked(
        state=state,
        rules=has,
        warnings=[Warning(rule=w.rule, said=w.said, cite=w.cite, how=w.how) for w in warnings],
        note=state_rules.NOT_ADVICE,
    )


def state_of(conn: psycopg.Connection[Any], property_id: UUID) -> str | None:
    row = conn.execute(
        "SELECT a.state FROM trust_property_address(%s) AS a", (property_id,)
    ).fetchone()
    return None if row is None else str(row[0])


@router.get("/late-fees")
def late_fees(
    session: Staff,
    conn: Conn,
    property_id: Annotated[UUID, Query()],
    starts_on: Annotated[date | None, Query()] = None,
    grace_days: Annotated[int | None, Query(ge=0, le=365)] = None,
    flat_fee: Annotated[Money | None, Query()] = None,
    percent: Annotated[Percent | None, Query()] = None,
    maximum: Annotated[Money | None, Query()] = None,
) -> Checked:
    """Late fee terms against the property's state: the terms given (grace days at least), or
    else those in force today. The cap is checked at the rent of each lease in effect on the day
    the terms start (today at the earliest)."""
    found(conn, "property", session.pmc_id, property_id)
    on = max(starts_on or today(), today())
    state = state_of(conn, property_id)
    rule = state_rules.rules_of(state)
    has = rule is not None and rule.late_fee is not None
    if grace_days is None:
        if any(v is not None for v in (flat_fee, percent, maximum)):
            raise HTTPException(422, "terms to check give their grace days")
        row = conn.execute(
            "SELECT grace_days, flat_fee, percent, maximum FROM trust_late_fee_policies"
            " WHERE pmc_id = %s AND property_id = %s AND starts_on <= %s"
            " ORDER BY starts_on DESC LIMIT 1",
            (session.pmc_id, property_id, on),
        ).fetchone()
        if row is None:
            return checked(state, has, [])
        grace_days, flat_fee, percent, maximum = row
    rents = conn.execute(
        "SELECT u.display_name, trust_rent_on(l.id, %(on)s) FROM trust_leases AS l"
        " JOIN trust_units AS u ON u.id = l.unit_id"
        " WHERE u.property_id = %(property)s AND l.pmc_id = %(pmc)s AND l.starts_on <= %(on)s"
        "   AND (l.ends_on IS NULL OR l.ends_on >= %(on)s)"
        ' ORDER BY u.display_name COLLATE "C", l.id',
        {"on": on, "property": property_id, "pmc": session.pmc_id},
    ).fetchall()
    units = conn.execute(
        "SELECT count(*) FROM trust_units WHERE pmc_id = %s AND property_id = %s",
        (session.pmc_id, property_id),
    ).fetchone()
    assert units is not None
    return checked(
        state,
        has,
        state_rules.late_fee_warnings(
            state,
            grace_days,
            flat_fee or Decimal(0),
            percent or Decimal(0),
            maximum,
            [(str(unit), Decimal(rent)) for unit, rent in rents],
            int(units[0]),
        ),
    )


HISTORY = """
WITH RECURSIVE chain (lease_id, depth) AS (
    SELECT %(lease)s::uuid, 0
    UNION ALL
    SELECT o.renews_lease_id, c.depth + 1
    FROM chain AS c
    JOIN trust_lease_offer_completions AS oc ON oc.lease_id = c.lease_id
    JOIN trust_lease_offers AS o ON o.id = oc.offer_id
    WHERE o.renews_lease_id IS NOT NULL AND c.depth < 100
)
SELECT l.starts_on, l.monthly_rent FROM chain JOIN trust_leases AS l ON l.id = chain.lease_id
UNION ALL
SELECT r.effective_on, r.monthly_rent FROM chain
JOIN trust_rent_changes AS r ON r.lease_id = chain.lease_id
ORDER BY 1
"""


def history_of(conn: psycopg.Connection[Any], lease_id: UUID) -> state_rules.RentHistory:
    """The lease's rent over its tenancy at the unit: back through the leases it renewed."""
    steps = [(on, Decimal(rent)) for on, rent in conn.execute(HISTORY, {"lease": lease_id})]
    lease = conn.execute(
        "SELECT starts_on, ends_on FROM trust_leases WHERE id = %s", (lease_id,)
    ).fetchone()
    assert lease is not None and steps
    return state_rules.RentHistory(
        since=steps[0][0], steps=tuple(steps), lease_starts=lease[0], lease_ends=lease[1]
    )


@router.get("/rent-change")
def rent_change(
    session: Staff,
    conn: Conn,
    lease_id: Annotated[UUID, Query()],
    effective_on: Annotated[date | None, Query()] = None,
    notice_given_on: Annotated[date | None, Query()] = None,
    monthly_rent: Annotated[Money | None, Query()] = None,
) -> Checked:
    """A rent change against the state of the lease's property: the one given (all three), or
    else each change on file from today on."""
    lease = lease_of(conn, session.pmc_id, lease_id)
    given = (effective_on, notice_given_on, monthly_rent)
    if any(v is None for v in given) and any(v is not None for v in given):
        raise HTTPException(422, "a rent change to check gives its day, notice and rent")
    state = state_of(conn, lease.property_id)
    rule = state_rules.rules_of(state)
    has = rule is not None and rule.rent_notice is not None
    if not has:
        return checked(state, False, [])
    history = history_of(conn, lease_id)
    if effective_on is not None and notice_given_on is not None and monthly_rent is not None:
        changes = [(effective_on, notice_given_on, monthly_rent)]
    else:
        changes = [
            (on, notice, Decimal(rent))
            for on, notice, rent in conn.execute(
                "SELECT effective_on, notice_given_on, monthly_rent FROM trust_rent_changes"
                " WHERE pmc_id = %s AND lease_id = %s AND effective_on >= %s"
                " ORDER BY effective_on",
                (session.pmc_id, lease_id, today()),
            )
        ]
    warnings = [
        warning
        for on, notice, rent in changes
        for warning in state_rules.rent_change_warnings(state, on, notice, rent, history)
    ]
    return checked(state, True, warnings)


@router.get("/entry")
def entry(
    session: Staff,
    conn: Conn,
    work_order_id: Annotated[UUID, Query()],
    day: Annotated[date | None, Query()] = None,
) -> Checked:
    """A visit for a work order at a unit against its state's notice before entering a home: a
    visit set today for `day`, or else the visit on file (from today on), as of the day it was
    set. A job at no unit, or a unit with no lease in effect that day, enters no one's home; a
    work order opened for a tenant's request is a repair they asked for."""
    row = conn.execute(
        "SELECT w.property_id, w.unit_id, EXISTS (SELECT 1 FROM"
        "  trust_maintenance_request_work_orders AS r WHERE r.work_order_id = w.id)"
        " FROM trust_work_orders AS w WHERE w.pmc_id = %s AND w.id = %s",
        (session.pmc_id, work_order_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such work order")
    property_id, unit_id, requested = row
    state = state_of(conn, property_id)
    rule = state_rules.rules_of(state)
    has = rule is not None and rule.entry is not None
    if day is not None:
        visit: tuple[date, date] | None = (day, today())
    else:
        visit = conn.execute(
            "SELECT s.scheduled_for, (s.taken_at AT TIME ZONE 'UTC')::date"
            " FROM trust_work_order_steps AS s WHERE s.work_order_id = %s"
            " ORDER BY s.taken_at DESC, s.id DESC LIMIT 1",
            (work_order_id,),
        ).fetchone()
        if visit is not None and (visit[0] is None or visit[0] < today()):
            visit = None
    if not has or visit is None or unit_id is None:
        return checked(state, has, [])
    occupied = conn.execute(
        "SELECT 1 FROM trust_leases WHERE unit_id = %(unit)s AND starts_on <= %(on)s"
        " AND (ends_on IS NULL OR ends_on >= %(on)s)",
        {"unit": unit_id, "on": visit[0]},
    ).fetchone()
    if occupied is None:
        return checked(state, has, [])
    return checked(state, has, state_rules.entry_warnings(state, visit[0], visit[1], requested))
