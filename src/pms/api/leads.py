"""Leads and showings (migration 0088; the founder's review, q083): a prospect who asked about
renting before any application (a guest card), and the showings staff set up for them.

Staff add a lead on Leasing, Leads with a name, an email or a phone, what they asked about (a
property, maybe one of its units), where they came from and when they would move in. On the lead's
page they set up showings of a unit at a time, maybe by a staff member, record how each went
(shown, a no-show, cancelled), and close the lead once: they applied, or the company lost them
(why). A closed lead takes no new showing. Every lead is recorded the same way, with nothing a
fair housing law protects; the AI role reads none of it. Nothing here moves money.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StringConstraints

from pms.api import scope
from pms.api.deps import Conn, Staff, refusal
from pms.api.directory import Email, Phone
from pms.api.rentals import Page, Paged, PerPage

router = APIRouter(prefix="/api/leasing")

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Source = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Status = Literal["open", "applied", "lost"]
Outcome = Literal["shown", "no_show", "cancelled"]


class NewLead(BaseModel):
    name: Name
    email: Email | None = None
    phone: Phone | None = None
    property_id: UUID | None = None
    unit_id: UUID | None = None
    source: Source | None = None
    move_in_on: date | None = None
    note: Note | None = None


class Created(BaseModel):
    id: UUID


class LeadRow(BaseModel):
    id: UUID
    name: str
    email: str | None
    phone: str | None
    # What they asked about: a property, maybe one of its units.
    property: str | None
    unit: str | None
    source: str | None
    move_in_on: date | None
    added_at: datetime
    showings: int
    # The next showing not yet gone through (no outcome), if any.
    next_showing: datetime | None
    status: Status


class ShowingOut(BaseModel):
    id: UUID
    unit_id: UUID
    property: str
    unit: str
    starts_at: datetime
    shown_by: str | None
    outcome: Outcome | None
    note: str | None


class Closing(BaseModel):
    outcome: Literal["applied", "lost"]
    reason: str | None
    closed_at: datetime
    closed_by: str | None


class Lead(LeadRow):
    property_id: UUID | None
    unit_id: UUID | None
    note: str | None
    added_by: str | None
    showing_list: list[ShowingOut]
    closing: Closing | None


class NewShowing(BaseModel):
    unit_id: UUID
    starts_at: datetime
    shown_by: UUID | None = None


class NewOutcome(BaseModel):
    outcome: Outcome
    note: Note | None = None


class NewClosing(BaseModel):
    outcome: Literal["applied", "lost"]
    reason: Reason | None = None


LEADS = """
SELECT l.id, l.display_name, l.email, l.phone, p.display_name, u.display_name, l.source,
       l.move_in_on, l.created_at,
       (SELECT count(*) FROM trust_showings AS s WHERE s.lead_id = l.id),
       (SELECT min(s.starts_at) FROM trust_showings AS s
        WHERE s.lead_id = l.id AND s.starts_at >= now() AND NOT EXISTS (
            SELECT 1 FROM trust_showing_outcomes AS o WHERE o.showing_id = s.id)),
       coalesce(c.outcome, 'open'),
       l.property_id, l.unit_id, l.note, st.display_name, c.reason, c.created_at, cs.display_name,
       count(*) OVER ()
FROM trust_leads AS l
LEFT JOIN trust_properties AS p ON p.id = l.property_id
LEFT JOIN trust_units AS u ON u.id = l.unit_id
LEFT JOIN trust_lead_closings AS c ON c.lead_id = l.id
LEFT JOIN trust_staff AS st ON st.id = l.recorded_by
LEFT JOIN trust_staff AS cs ON cs.id = c.recorded_by
WHERE l.pmc_id = %(pmc)s AND {where}
ORDER BY l.created_at DESC, l.id
LIMIT %(limit)s OFFSET %(offset)s
"""

SHOWINGS = """
SELECT s.id, u.id, p.display_name, u.display_name, s.starts_at, st.display_name, o.outcome, o.note
FROM trust_showings AS s
JOIN trust_units AS u ON u.id = s.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
LEFT JOIN trust_staff AS st ON st.id = s.shown_by
LEFT JOIN trust_showing_outcomes AS o ON o.showing_id = s.id
WHERE s.pmc_id = %s AND s.lead_id = %s
ORDER BY s.starts_at, s.id
"""

# Which leads each tab of Leads shows.
STATUSES: dict[str, str] = {
    "open": "c.lead_id IS NULL",
    "closed": "c.lead_id IS NOT NULL",
    "all": "true",
}


def row_of(row: Any) -> LeadRow:
    return LeadRow(
        id=row[0],
        name=row[1],
        email=row[2],
        phone=row[3],
        property=row[4],
        unit=row[5],
        source=row[6],
        move_in_on=row[7],
        added_at=row[8],
        showings=row[9],
        next_showing=row[10],
        status=row[11],
    )


@router.get("/leads")
def leads(
    session: Staff,
    conn: Conn,
    status: Literal["open", "closed", "all"] = "open",
    group: scope.Group = None,
    property_id: scope.Property = None,
    page: Page = 1,
    per_page: PerPage = 50,
) -> Paged[LeadRow]:
    """The company's leads, newest first, a page at a time: open, closed or all, maybe of a
    property group or property (by what they asked about)."""
    where = STATUSES[status]
    if group is not None or property_id is not None:
        where += " AND " + scope.within("l.property_id")
    rows = conn.execute(
        LEADS.format(where=where),
        {
            "pmc": session.pmc_id,
            "limit": per_page,
            "offset": (page - 1) * per_page,
            **scope.params(group, property_id),
        },
    ).fetchall()
    return Paged(
        items=[row_of(row) for row in rows],
        total=rows[0][-1] if rows else 0,
        page=page,
        per_page=per_page,
    )


@router.post("/leads", status_code=201)
def add_lead(session: Staff, conn: Conn, new: NewLead) -> Created:
    """A prospect who asked about renting: a name and a way to reach them."""
    if new.email is None and new.phone is None:
        raise HTTPException(422, "a lead needs an email or a phone")
    if new.unit_id is not None and new.property_id is None:
        raise HTTPException(422, "a unit asked about is one of a property's; name the property")
    for table, about, missing in (
        ("trust_properties", new.property_id, "no such property"),
        ("trust_units", new.unit_id, "no such unit"),
    ):
        if about is not None and (
            conn.execute(
                f"SELECT 1 FROM {table} WHERE pmc_id = %s AND id = %s", (session.pmc_id, about)
            ).fetchone()
            is None
        ):
            raise HTTPException(404, missing)
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO trust_leads (pmc_id, display_name, email, phone, property_id,"
                " unit_id, source, move_in_on, note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " RETURNING id",
                (
                    session.pmc_id,
                    new.name,
                    new.email,
                    new.phone,
                    new.property_id,
                    new.unit_id,
                    new.source,
                    new.move_in_on,
                    new.note,
                ),
            ).fetchone()
    except psycopg.errors.CheckViolation:
        raise HTTPException(422, "that unit is not one of the property's") from None
    assert row is not None
    return Created(id=row[0])


def lead_of(conn: psycopg.Connection[Any], pmc_id: UUID, lead_id: UUID) -> Lead:
    row = conn.execute(
        LEADS.format(where="l.id = %(lead)s"),
        {"pmc": pmc_id, "lead": lead_id, "limit": 1, "offset": 0},
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such lead")
    closing = (
        None
        if row[11] == "open"
        else Closing(outcome=row[11], reason=row[16], closed_at=row[17], closed_by=row[18])
    )
    return Lead(
        **row_of(row).model_dump(),
        property_id=row[12],
        unit_id=row[13],
        note=row[14],
        added_by=row[15],
        showing_list=[
            ShowingOut(
                id=s[0],
                unit_id=s[1],
                property=s[2],
                unit=s[3],
                starts_at=s[4],
                shown_by=s[5],
                outcome=s[6],
                note=s[7],
            )
            for s in conn.execute(SHOWINGS, (pmc_id, lead_id)).fetchall()
        ],
        closing=closing,
    )


@router.get("/leads/{lead_id}")
def lead(session: Staff, conn: Conn, lead_id: UUID) -> Lead:
    """A lead: what they asked about, their showings and whether they are closed."""
    return lead_of(conn, session.pmc_id, lead_id)


@router.post("/leads/{lead_id}/showings", status_code=201)
def add_showing(session: Staff, conn: Conn, lead_id: UUID, new: NewShowing) -> Lead:
    """Sets up a showing of one of the company's units to the lead, maybe by a staff member."""
    lead_of(conn, session.pmc_id, lead_id)
    for table, about, missing in (
        ("trust_units", new.unit_id, "no such unit"),
        ("trust_staff", new.shown_by, "no such staff member"),
    ):
        if about is not None and (
            conn.execute(
                f"SELECT 1 FROM {table} WHERE pmc_id = %s AND id = %s", (session.pmc_id, about)
            ).fetchone()
            is None
        ):
            raise HTTPException(404, missing)
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_showings (pmc_id, lead_id, unit_id, starts_at, shown_by)"
                " VALUES (%s, %s, %s, %s, %s)",
                (session.pmc_id, lead_id, new.unit_id, new.starts_at, new.shown_by),
            )
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, refusal(refused)) from None
    return lead_of(conn, session.pmc_id, lead_id)


@router.post("/showings/{showing_id}/outcome", status_code=201)
def record_outcome(session: Staff, conn: Conn, showing_id: UUID, new: NewOutcome) -> Lead:
    """How a showing went, once: shown, a no-show, or cancelled."""
    found = conn.execute(
        "SELECT lead_id FROM trust_showings WHERE pmc_id = %s AND id = %s",
        (session.pmc_id, showing_id),
    ).fetchone()
    if found is None:
        raise HTTPException(404, "no such showing")
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_showing_outcomes (pmc_id, showing_id, outcome, note)"
                " VALUES (%s, %s, %s, %s)",
                (session.pmc_id, showing_id, new.outcome, new.note),
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "how this showing went is recorded already") from None
    return lead_of(conn, session.pmc_id, found[0])


@router.post("/leads/{lead_id}/close", status_code=201)
def close_lead(session: Staff, conn: Conn, lead_id: UUID, new: NewClosing) -> Lead:
    """Closes a lead, once: they applied, or the company lost them (and maybe why)."""
    lead_of(conn, session.pmc_id, lead_id)
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_lead_closings (pmc_id, lead_id, outcome, reason)"
                " VALUES (%s, %s, %s, %s)",
                (session.pmc_id, lead_id, new.outcome, new.reason),
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "this lead is closed already") from None
    return lead_of(conn, session.pmc_id, lead_id)
