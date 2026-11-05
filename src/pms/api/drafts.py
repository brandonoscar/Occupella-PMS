"""Drafts (ADR 0011's second piece, migration 20261105000300): software with an API key, such as
an AI, proposes something; a staff member approves it, edits it first or declines it; the PMS
does it.

Software proposes with `POST /api/ai/drafts`, as the AI's role, whose one write is that INSERT
into trust_drafts: an email to tenants, owners or vendors, a notice from one of the company's
notice templates for leases, a work order opened, assigned to a vendor or scheduled, or a note.
It names records by id and never a contact detail: the PMS fills in each address when it sends.
It reads back what became of its own drafts (`GET /api/ai/drafts`), never what staff made of one.
No kind moves money, and none can: there is no kind for a payment, charge, bill, draw or fee.

Staff see the drafts at /drafts and those waiting on the Dashboard, each saying what it would do
in words: who an email goes to and at which address today, which homes a notice is for. Approving
one does it now, through the write path staff use for it by hand, as the staff member who
approved and with their roles (its area: email, documents or maintenance), in the decision's
transaction: all of it or none. Staff may change its words or its day first; declining says
why, for whoever proposed it. Each draft is decided once.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

from pms import accounts, email
from pms.api import emails, maintenance, notes, notice_templates, work_orders
from pms.api.ai_keys import WithKey
from pms.api.deps import AREAS, Conn, Staff, may
from pms.api.rentals import Page, Paged, PerPage

router = APIRouter(prefix="/api")

# How many drafts may wait for staff at once in a company: past it, software proposes nothing
# more until staff decide some, so a loop can't bury the inbox.
MAX_WAITING = 500

Kind = Literal[
    "email", "notice", "work_order_open", "work_order_assign", "work_order_schedule", "note"
]
# The area of staff work each kind does (custom roles, migration 0057): approving one takes it.
AREA: dict[str, str] = {
    "email": "email",
    "notice": "documents",
    "work_order_open": "maintenance",
    "work_order_assign": "maintenance",
    "work_order_schedule": "maintenance",
    "note": "documents",
}
Why = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000, pattern=r"^[^\x00]*$"),
]
Window = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Person = Literal["tenant", "owner", "vendor"]


class Strict(BaseModel):
    # A proposal says only what its kind takes: nothing else rides along in it, a contact
    # detail least of all.
    model_config = ConfigDict(extra="forbid")


class To(Strict):
    about: Person
    id: UUID


class EmailDraft(Strict):
    kind: Literal["email"]
    to: Annotated[list[To], Field(min_length=1, max_length=100)]
    subject: emails.Subject
    body: emails.Text

    @model_validator(mode="after")
    def each_once(self) -> EmailDraft:
        if len({(t.about, t.id) for t in self.to}) != len(self.to):
            raise ValueError("an email names each person once")
        return self


class NoticeDraft(Strict):
    kind: Literal["notice"]
    template_id: UUID
    lease_ids: Annotated[list[UUID], Field(min_length=1, max_length=100)]
    dated_on: date

    @model_validator(mode="after")
    def each_once(self) -> NoticeDraft:
        if len(set(self.lease_ids)) != len(self.lease_ids):
            raise ValueError("a notice names each lease once")
        return self


class OpenDraft(Strict):
    kind: Literal["work_order_open"]
    property_id: UUID
    unit_id: UUID | None = None
    summary: work_orders.Summary
    priority: work_orders.Priority = "normal"
    description: work_orders.Description | None = None
    due_on: date | None = None


class AssignDraft(Strict):
    kind: Literal["work_order_assign"]
    work_order_id: UUID
    vendor_id: UUID


class ScheduleDraft(Strict):
    kind: Literal["work_order_schedule"]
    work_order_id: UUID
    scheduled_for: date
    visit_window: Window | None = None


class NoteDraft(Strict):
    kind: Literal["note"]
    about: notes.About
    about_id: UUID
    body: notes.Body


Proposal = Annotated[
    EmailDraft | NoticeDraft | OpenDraft | AssignDraft | ScheduleDraft | NoteDraft,
    Field(discriminator="kind"),
]
PROPOSAL: TypeAdapter[Proposal] = TypeAdapter(Proposal)


class NewDraft(Strict):
    proposal: Proposal
    why: Why | None = None


class Proposed(BaseModel):
    id: UUID


Status = Literal["waiting", "approved", "declined"]


class Mine(BaseModel):
    """What became of a draft, as the software that proposed it reads it."""

    id: UUID
    kind: Kind
    made_at: datetime
    status: Status
    decided_at: datetime | None
    # Approved: whether staff changed it first. Declined: why.
    edited: bool | None
    reason: str | None


class Named(BaseModel):
    """A record a draft names, as staff read it."""

    about: str
    id: UUID
    name: str
    # An email's recipient: where it goes today, their contact email else their login's; None,
    # nowhere, so they get none.
    to: str | None = None


class DraftRow(BaseModel):
    id: UUID
    kind: Kind
    # What it would do, in words: "Email to Drew Sample and 2 others: Rent is late".
    what: str
    why: str | None
    # The API key that proposed it.
    key: str
    made_at: datetime
    status: Status
    decided_by: str | None
    decided_at: datetime | None
    edited: bool | None
    reason: str | None


class Draft(DraftRow):
    proposal: dict[str, Any]
    # Each record it names, by id, with its name now.
    names: list[Named]
    # Approved: the proposal as it was done, and what that made, by id.
    acted: dict[str, Any] | None
    result: dict[str, Any] | None


class Approval(Strict):
    # The draft as staff changed it, of its own kind; none: as proposed.
    proposal: Proposal | None = None


class Decline(Strict):
    reason: Why


# --- software, with a key -----------------------------------------------------------------------

# A record of the company, as the AI's role reads it (each table it reads; none holds contact
# details). Each statement takes the company and the id.
EXISTS = {
    "tenant": "SELECT 1 FROM trust_tenants WHERE pmc_id = %s AND id = %s",
    "owner": "SELECT 1 FROM trust_owners WHERE pmc_id = %s AND id = %s",
    "vendor": "SELECT 1 FROM trust_vendors WHERE pmc_id = %s AND id = %s",
    "property": "SELECT 1 FROM trust_properties WHERE pmc_id = %s AND id = %s",
    "unit": "SELECT 1 FROM trust_units WHERE pmc_id = %s AND id = %s",
    "lease": "SELECT 1 FROM trust_leases WHERE pmc_id = %s AND id = %s",
    "work order": "SELECT 1 FROM trust_work_orders WHERE pmc_id = %s AND id = %s",
    "notice template": (
        "SELECT 1 FROM trust_notice_templates AS t WHERE t.pmc_id = %s AND t.id = %s"
        " AND t.used_for = 'notice'"
        " AND NOT EXISTS (SELECT 1 FROM trust_notice_template_removals AS r"
        "   WHERE r.template_id = t.id)"
    ),
}


def named(proposal: Proposal) -> list[tuple[str, UUID]]:
    """Each record a proposal names, as (what it is, its id)."""
    match proposal:
        case EmailDraft():
            return [(t.about, t.id) for t in proposal.to]
        case NoticeDraft():
            return [("notice template", proposal.template_id)] + [
                ("lease", lease) for lease in proposal.lease_ids
            ]
        case OpenDraft():
            unit = [("unit", proposal.unit_id)] if proposal.unit_id is not None else []
            return [("property", proposal.property_id), *unit]
        case AssignDraft():
            return [("work order", proposal.work_order_id), ("vendor", proposal.vendor_id)]
        case ScheduleDraft():
            return [("work order", proposal.work_order_id)]
        case NoteDraft():
            return [(proposal.about, proposal.about_id)]


def check(conn: psycopg.Connection[Any], pmc_id: UUID, proposal: Proposal) -> None:
    """Every record the proposal names is the company's (422 naming the first that isn't)."""
    for about, record in named(proposal):
        if conn.execute(EXISTS[about], (pmc_id, record)).fetchone() is None:
            raise HTTPException(422, f"no such {about} in this company: {record}")
    if isinstance(proposal, OpenDraft) and proposal.unit_id is not None:
        same = conn.execute(
            "SELECT 1 FROM trust_units WHERE pmc_id = %s AND id = %s AND property_id = %s",
            (pmc_id, proposal.unit_id, proposal.property_id),
        ).fetchone()
        if same is None:
            raise HTTPException(422, "that unit is not one of the property's")


def waiting(conn: psycopg.Connection[Any], pmc_id: UUID) -> int:
    row = conn.execute(
        "SELECT count(*) FROM trust_drafts AS d WHERE d.pmc_id = %s"
        " AND NOT EXISTS (SELECT 1 FROM trust_draft_decisions AS x WHERE x.draft_id = d.id)",
        (pmc_id,),
    ).fetchone()
    assert row is not None
    return int(row[0])


@router.post("/ai/drafts", status_code=201)
def propose(key: WithKey, conn: Conn, new: NewDraft) -> Proposed:
    """A draft for staff to decide, as the AI's role: it names the company's records by id, and
    nothing happens until a staff member approves it."""
    if waiting(conn, key.pmc_id) >= MAX_WAITING:
        raise HTTPException(
            429,
            f"{MAX_WAITING} drafts already wait for staff: propose more once they decide some",
        )
    check(conn, key.pmc_id, new.proposal)
    row = conn.execute(
        "INSERT INTO trust_drafts (pmc_id, api_key_id, kind, proposal, why)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (
            key.pmc_id,
            key.id,
            new.proposal.kind,
            Jsonb(new.proposal.model_dump(mode="json", exclude_none=True)),
            new.why,
        ),
    ).fetchone()
    assert row is not None
    return Proposed(id=row[0])


@router.get("/ai/drafts")
def my_drafts(key: WithKey, conn: Conn, page: Page = 1, per_page: PerPage = 50) -> Paged[Mine]:
    """The drafts this key proposed, newest first, each waiting, approved or declined."""
    total = conn.execute(
        "SELECT count(*) FROM trust_drafts WHERE pmc_id = %s AND api_key_id = %s",
        (key.pmc_id, key.id),
    ).fetchone()
    rows = conn.execute(
        "SELECT d.id, d.kind, d.created_at, x.decision, x.created_at, x.edited, x.reason"
        " FROM trust_drafts AS d LEFT JOIN trust_draft_decisions AS x ON x.draft_id = d.id"
        " WHERE d.pmc_id = %s AND d.api_key_id = %s"
        " ORDER BY d.created_at DESC, d.id DESC LIMIT %s OFFSET %s",
        (key.pmc_id, key.id, per_page, (page - 1) * per_page),
    ).fetchall()
    return Paged(
        items=[
            Mine(
                id=i,
                kind=k,
                made_at=at,
                status=decision or "waiting",
                decided_at=decided,
                edited=edited,
                reason=reason,
            )
            for i, k, at, decision, decided, edited, reason in rows
        ],
        total=total[0] if total else 0,
        page=page,
        per_page=per_page,
    )


# --- staff ----------------------------------------------------------------------------------------

DRAFTS = """
SELECT d.id, d.kind, d.proposal, d.why, k.name, d.created_at, x.decision, s.display_name,
       x.created_at, x.edited, x.reason, a.acted, a.result
FROM trust_drafts AS d
JOIN trust_api_keys AS k ON k.id = d.api_key_id
LEFT JOIN trust_draft_decisions AS x ON x.draft_id = d.id
LEFT JOIN trust_staff AS s ON s.id = x.recorded_by
LEFT JOIN trust_draft_actions AS a ON a.draft_id = d.id
WHERE d.pmc_id = %(pmc)s AND (%(draft)s::uuid IS NULL OR d.id = %(draft)s)
  AND (%(show)s = 'all' OR (%(show)s = 'waiting') = (x.draft_id IS NULL))
"""

# A lease as staff's pages name it: its property and unit, and its tenants.
LEASE = """
SELECT p.display_name || ', ' || u.display_name,
       ARRAY(SELECT t.display_name FROM trust_lease_tenants AS lt
             JOIN trust_tenants AS t ON t.id = lt.tenant_id
             WHERE lt.lease_id = l.id ORDER BY t.display_name COLLATE "C", t.id)
FROM trust_leases AS l
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
WHERE l.pmc_id = %s AND l.id = %s
"""
NAMES = {
    "property": "SELECT display_name FROM trust_properties WHERE pmc_id = %s AND id = %s",
    "unit": (
        "SELECT p.display_name || ', ' || u.display_name FROM trust_units AS u"
        " JOIN trust_properties AS p ON p.id = u.property_id WHERE u.pmc_id = %s AND u.id = %s"
    ),
    "work order": (
        "SELECT 'Work order ' || w.number || ': ' || w.summary FROM ("
        "  SELECT id, summary, row_number() OVER (ORDER BY created_at, id) AS number"
        "  FROM trust_work_orders WHERE pmc_id = %s) AS w WHERE w.id = %s"
    ),
    "notice template": (
        "SELECT v.name FROM trust_notice_template_versions AS v"
        " WHERE v.pmc_id = %s AND v.template_id = %s ORDER BY v.created_at DESC, v.id DESC LIMIT 1"
    ),
}


def name_of(conn: psycopg.Connection[Any], pmc_id: UUID, about: str, record: UUID) -> Named:
    """A record's name now; one gone from the company (it can't be: nothing is deleted) reads as
    its id."""
    if about in ("tenant", "owner", "vendor"):
        row = conn.execute(email.PEOPLE[about][0], (pmc_id, record)).fetchone()
        return Named(about=about, id=record, name=row[0] if row else str(record))
    if about == "lease":
        found = conn.execute(LEASE, (pmc_id, record)).fetchone()
        if found is None:
            return Named(about=about, id=record, name=str(record))
        home, tenants = found
        return Named(
            about=about, id=record, name=f"{home} ({', '.join(tenants)})" if tenants else home
        )
    row = conn.execute(NAMES[about], (pmc_id, record)).fetchone()
    return Named(about=about, id=record, name=row[0] if row else str(record))


def names_of(conn: psycopg.Connection[Any], pmc_id: UUID, proposal: Proposal) -> list[Named]:
    found = [name_of(conn, pmc_id, about, record) for about, record in named(proposal)]
    if isinstance(proposal, EmailDraft):
        found = [
            n.model_copy(update={"to": emails.address_of(conn, pmc_id, n.about, n.id)[1]})  # type: ignore[arg-type]
            for n in found
        ]
    return found


def others(first: str, count: int) -> str:
    """ "Drew Sample", "Drew Sample and 1 other", "Drew Sample and 2 others"."""
    if count <= 1:
        return first
    rest = count - 1
    return f"{first} and {rest} other{'' if rest == 1 else 's'}"


def what(proposal: Proposal, names: list[Named]) -> str:
    """What the draft would do, in words."""
    by_id = {(n.about, n.id): n.name for n in names}
    match proposal:
        case EmailDraft():
            first = by_id[(proposal.to[0].about, proposal.to[0].id)]
            return f"Email to {others(first, len(proposal.to))}: {proposal.subject}"
        case NoticeDraft():
            template = by_id[("notice template", proposal.template_id)]
            first = by_id[("lease", proposal.lease_ids[0])]
            return f"Notice from {template} for {others(first, len(proposal.lease_ids))}"
        case OpenDraft():
            where = by_id[("property", proposal.property_id)]
            if proposal.unit_id is not None:
                where = by_id[("unit", proposal.unit_id)]
            return f"Open a work order at {where}: {proposal.summary}"
        case AssignDraft():
            order = by_id[("work order", proposal.work_order_id)]
            return f"Assign {order} to {by_id[('vendor', proposal.vendor_id)]}"
        case ScheduleDraft():
            order = by_id[("work order", proposal.work_order_id)]
            return f"Schedule a visit for {order}"
        case NoteDraft():
            return f"Note about {proposal.about} {by_id[(proposal.about, proposal.about_id)]}"


def drafts_of(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    draft_id: UUID | None = None,
    show: str = "all",
    page: int = 1,
    per_page: int = 200,
) -> tuple[list[Draft], int]:
    """The company's drafts (or one), the waiting oldest first and the decided newest first, with
    how many there are in all."""
    where = {"pmc": pmc_id, "draft": draft_id, "show": show}
    total = conn.execute(f"SELECT count(*) FROM ({DRAFTS}) AS d", where).fetchone()  # noqa: S608
    order = (
        " ORDER BY d.created_at, d.id"
        if show == "waiting"
        else " ORDER BY (x.draft_id IS NOT NULL), coalesce(x.created_at, d.created_at) DESC, d.id"
    )
    rows = conn.execute(
        DRAFTS + order + " LIMIT %(limit)s OFFSET %(offset)s",
        {**where, "limit": per_page, "offset": (page - 1) * per_page},
    ).fetchall()
    found = []
    for i, kind, raw, why, key, made, decision, by, decided, edited, reason, acted, result in rows:
        proposal = PROPOSAL.validate_python(raw)
        names = names_of(conn, pmc_id, proposal)
        found.append(
            Draft(
                id=i,
                kind=kind,
                what=what(proposal, names),
                why=why,
                key=key,
                made_at=made,
                status=decision or "waiting",
                decided_by=by,
                decided_at=decided,
                edited=edited,
                reason=reason,
                proposal=raw,
                names=names,
                acted=acted,
                result=result,
            )
        )
    return found, total[0] if total else 0


def row_of(draft: Draft) -> DraftRow:
    return DraftRow(**{name: getattr(draft, name) for name in DraftRow.model_fields})


@router.get("/drafts")
def drafts(
    session: Staff,
    conn: Conn,
    show: Annotated[Literal["waiting", "decided", "all"], Query()] = "waiting",
    page: Page = 1,
    per_page: PerPage = 50,
) -> Paged[DraftRow]:
    """The company's drafts: those waiting, oldest first; or those decided, newest first."""
    found, total = drafts_of(conn, session.pmc_id, None, show, page, per_page)
    return Paged(items=[row_of(d) for d in found], total=total, page=page, per_page=per_page)


def one(conn: psycopg.Connection[Any], pmc_id: UUID, draft_id: UUID) -> Draft:
    found, _ = drafts_of(conn, pmc_id, draft_id)
    if not found:
        raise HTTPException(404, "no such draft")
    return found[0]


@router.get("/drafts/{draft_id}")
def draft(session: Staff, conn: Conn, draft_id: UUID) -> Draft:
    """A draft: what it would do, each record it names (an email's recipients with the address it
    goes to today), and once decided, by whom and what was done."""
    return one(conn, session.pmc_id, draft_id)


def waiting_draft(conn: psycopg.Connection[Any], pmc_id: UUID, draft_id: UUID) -> Draft:
    """The draft, locked for this transaction so two staff can't decide it at once; refused once
    decided."""
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('trust_draft:' || %s, 0))", (str(draft_id),)
    )
    found = one(conn, pmc_id, draft_id)
    if found.status != "waiting":
        raise HTTPException(409, f"this draft was {found.status} already")
    return found


def act(
    conn: psycopg.Connection[Any], session: accounts.Session, proposal: Proposal
) -> dict[str, Any]:
    """Does the proposal through the write path staff use for it by hand, as the session's staff
    member; what it made, by id."""
    match proposal:
        case EmailDraft():
            sent, without = [], []
            for person in proposal.to:
                _, to = emails.address_of(conn, session.pmc_id, person.about, person.id)
                if to is None:
                    without.append({"about": person.about, "id": str(person.id)})
                    continue
                made = email.write_message(
                    conn,
                    session.pmc_id,
                    person.about,
                    person.id,
                    to,
                    proposal.subject,
                    proposal.body,
                    session.person_id,
                )
                sent.append(str(made))
            if not sent:
                raise HTTPException(
                    422, "nobody it names has an email on file: add one on their page first"
                )
            return {"emails": sent, "without_email": without}
        case NoticeDraft():
            notices_made = [
                notice_templates.make(
                    session,
                    conn,
                    notice_templates.NewNotice(
                        lease_id=lease, template_id=proposal.template_id, dated_on=proposal.dated_on
                    ),
                ).id
                for lease in proposal.lease_ids
            ]
            return {"notices": [str(n) for n in notices_made]}
        case OpenDraft():
            opened = work_orders.open_work_order(
                session,
                conn,
                work_orders.NewWorkOrder(
                    property_id=proposal.property_id,
                    unit_id=proposal.unit_id,
                    summary=proposal.summary,
                    priority=proposal.priority,
                    description=proposal.description,
                    due_on=proposal.due_on,
                ),
            )
            return {"work_order": str(opened.id)}
        case AssignDraft():
            maintenance.record_step(
                session,
                conn,
                proposal.work_order_id,
                maintenance.Step(step="assigned", vendor_id=proposal.vendor_id),
            )
            return {"work_order": str(proposal.work_order_id)}
        case ScheduleDraft():
            maintenance.record_step(
                session,
                conn,
                proposal.work_order_id,
                maintenance.Step(
                    step="scheduled",
                    scheduled_for=proposal.scheduled_for,
                    visit_window=proposal.visit_window,
                ),
            )
            return {"work_order": str(proposal.work_order_id)}
        case NoteDraft():
            note = notes.add_note(
                session,
                conn,
                notes.NewNote(about=proposal.about, about_id=proposal.about_id, body=proposal.body),
            )
            return {"note": str(note.id)}


@router.post("/drafts/{draft_id}/approve")
def approve(session: Staff, conn: Conn, draft_id: UUID, approval: Approval) -> Draft:
    """Does the draft now, as proposed or as changed here, as this staff member with their roles;
    all of it or none."""
    found = waiting_draft(conn, session.pmc_id, draft_id)
    proposed = PROPOSAL.validate_python(found.proposal)
    done = approval.proposal or proposed
    if done.kind != found.kind:
        raise HTTPException(422, f"this draft is a {found.kind}: an edit keeps its kind")
    area = AREA[done.kind]
    if not may(conn, session, area):
        raise HTTPException(
            403, f"your roles don't let you change {AREAS[area]}: an admin can add it"
        )
    check(conn, session.pmc_id, done)
    result = act(conn, session, done)
    acted = done.model_dump(mode="json", exclude_none=True)
    conn.execute(
        "INSERT INTO trust_draft_decisions (draft_id, pmc_id, decision, edited)"
        " VALUES (%s, %s, 'approved', %s)",
        (draft_id, session.pmc_id, acted != found.proposal),
    )
    conn.execute(
        "INSERT INTO trust_draft_actions (draft_id, pmc_id, acted, result) VALUES (%s, %s, %s, %s)",
        (draft_id, session.pmc_id, Jsonb(acted), Jsonb(result)),
    )
    return one(conn, session.pmc_id, draft_id)


@router.post("/drafts/{draft_id}/decline")
def decline(session: Staff, conn: Conn, draft_id: UUID, declined: Decline) -> Draft:
    """Declines the draft, saying why: nothing is done, and the software that proposed it reads
    the reason."""
    waiting_draft(conn, session.pmc_id, draft_id)
    conn.execute(
        "INSERT INTO trust_draft_decisions (draft_id, pmc_id, decision, reason)"
        " VALUES (%s, %s, 'declined', %s)",
        (draft_id, session.pmc_id, declined.reason),
    )
    return one(conn, session.pmc_id, draft_id)
