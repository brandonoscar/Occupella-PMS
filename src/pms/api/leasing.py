"""Leasing basics, part one: application links, applications, screening and decisions.

Applicants have no login. Staff open an application link for a unit (its token is shown once
and kept only as a SHA-256), and anyone with it applies. Each application gets a status token
the applicant keeps to see where it stands. Tokens travel in JSON bodies, never in a path, so
they stay out of server logs.

Staff screen an application through the partner the server is set up with
(pms.leasing.screening) and decide on it. A decline or a conditional approval that rests on a
screening needs its adverse action notice recorded with it; the database checks that at
commit, and GET .../notice gives the notice as a PDF to hand over.
"""

from __future__ import annotations

import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from pms import accounts, exports
from pms.api.deps import Conn, Staff, download
from pms.leasing import screening

router = APIRouter()
# Applications one link takes in a day: a busy listing's worth, and a ceiling on a flood.
APPLICATIONS_PER_DAY = 50
MAX_APPLICANTS = 6
Money = Annotated[Decimal, Field(ge=0, le=Decimal("10000000"), decimal_places=2)]
Token = Annotated[str, Field(min_length=20, max_length=100)]
NOT_OPEN = "this application link is closed or doesn't exist"


def today() -> date:
    return datetime.now(UTC).date()


def new_token() -> tuple[str, bytes]:
    token = secrets.token_urlsafe(32)
    return token, accounts.digest(token)


def public_url(request: Request) -> str:
    return str(request.app.state.settings.public_url).rstrip("/")


# --- applicants (no login) ---------------------------------------------------------------


class LinkToken(BaseModel):
    token: Token


class Listing(BaseModel):
    pmc: str
    property: str
    unit: str
    monthly_rent: str


class ApplicantIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=254)


class ApplicationIn(BaseModel):
    token: Token
    move_in_on: date
    monthly_income: Money
    applicants: list[ApplicantIn] = Field(min_length=1, max_length=MAX_APPLICANTS)


class Submitted(BaseModel):
    status_url: str


class StatusToken(BaseModel):
    status_token: Token


StatusName = Literal["received", "in review", "approved", "conditional", "declined"]


class Status(BaseModel):
    property: str
    unit: str
    submitted_at: datetime
    # "received" until staff screen it, "in review" after, then the decision.
    status: StatusName
    conditions: str | None


def open_link(conn: psycopg.Connection[Any], token: str) -> tuple[Any, ...]:
    """(link, PMC, PMC name, property, unit, rent) of an open link, or 404 for any other."""
    row = conn.execute(
        "SELECT l.id, l.pmc_id, pmc.display_name, p.display_name, u.display_name,"
        " l.monthly_rent"
        " FROM trust_application_links AS l"
        " JOIN trust_pmcs AS pmc ON pmc.pmc_id = l.pmc_id"
        " JOIN trust_units AS u ON u.id = l.unit_id"
        " JOIN trust_properties AS p ON p.id = u.property_id"
        " WHERE l.token_sha256 = %s AND NOT EXISTS ("
        "   SELECT 1 FROM trust_application_link_closings AS c WHERE c.link_id = l.id)",
        (accounts.digest(token),),
    ).fetchone()
    if row is None:
        raise HTTPException(404, NOT_OPEN)
    return tuple(row)


@router.post("/api/apply/view")
def view_listing(conn: Conn, link: LinkToken) -> Listing:
    """What an open link is for, so the applicant knows what they apply for."""
    _, _, pmc, prop, unit, rent = open_link(conn, link.token)
    return Listing(pmc=pmc, property=prop, unit=unit, monthly_rent=exports.text(rent))


@router.post("/api/apply", status_code=201)
def apply(request: Request, conn: Conn, application: ApplicationIn) -> Submitted:
    """An application through an open link. The answer's link is the only way back to it."""
    if not today() <= application.move_in_on <= today() + timedelta(days=366):
        raise HTTPException(422, "a move-in day is from today to a year from now")
    people = []
    for applicant in application.applicants:
        name, email = applicant.name.strip(), accounts.normalize_email(applicant.email)
        if not name:
            raise HTTPException(422, "every applicant has a name")
        if not accounts.EMAIL.fullmatch(email):
            raise HTTPException(422, f"{email!r} isn't an email address")
        people.append((name, email))
    if len({email for _, email in people}) < len(people):
        raise HTTPException(422, "each applicant has their own email")
    link, pmc_id, *_ = open_link(conn, application.token)
    recent = conn.execute(
        "SELECT count(*) FROM trust_applications WHERE link_id = %s AND submitted_at > %s",
        (link, datetime.now(UTC) - timedelta(days=1)),
    ).fetchone()
    if recent is not None and recent[0] >= APPLICATIONS_PER_DAY:
        raise HTTPException(429, "this link has taken all the applications it can today")
    token, digest = new_token()
    try:
        created = conn.execute(
            "INSERT INTO trust_applications"
            " (pmc_id, link_id, status_token_sha256, move_in_on, monthly_income)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (pmc_id, link, digest, application.move_in_on, application.monthly_income),
        ).fetchone()
    except psycopg.errors.CheckViolation:
        # Staff closed the link while this was on its way.
        raise HTTPException(404, NOT_OPEN) from None
    assert created is not None
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO trust_applicants (pmc_id, application_id, position, display_name, email)"
            " VALUES (%s, %s, %s, %s, %s)",
            [(pmc_id, created[0], n, name, email) for n, (name, email) in enumerate(people, 1)],
        )
    return Submitted(status_url=f"{public_url(request)}/application#{token}")


@router.post("/api/application/status")
def application_status(conn: Conn, asked: StatusToken) -> Status:
    """Where an application stands, for whoever holds its status token."""
    row = conn.execute(
        "SELECT p.display_name, u.display_name, a.submitted_at, d.decision, d.conditions,"
        " EXISTS (SELECT 1 FROM trust_screenings AS s WHERE s.application_id = a.id)"
        " FROM trust_applications AS a"
        " JOIN trust_application_links AS l ON l.id = a.link_id"
        " JOIN trust_units AS u ON u.id = l.unit_id"
        " JOIN trust_properties AS p ON p.id = u.property_id"
        " LEFT JOIN trust_application_decisions AS d ON d.application_id = a.id"
        " WHERE a.status_token_sha256 = %s",
        (accounts.digest(asked.status_token),),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no application has this link")
    prop, unit, submitted, decision, conditions, screened = row
    status: StatusName = decision or ("in review" if screened else "received")
    return Status(
        property=prop, unit=unit, submitted_at=submitted, status=status, conditions=conditions
    )


# --- staff ------------------------------------------------------------------------------


class Unit(BaseModel):
    unit_id: UUID
    property: str
    unit: str
    leased_today: bool


class NewLink(BaseModel):
    unit_id: UUID
    monthly_rent: Annotated[Decimal, Field(gt=0, le=Decimal("1000000"), decimal_places=2)]


class Link(BaseModel):
    id: UUID
    property: str
    unit: str
    monthly_rent: str
    opened_at: datetime
    applications: int


class OpenedLink(BaseModel):
    id: UUID
    url: str


class Screening(BaseModel):
    id: UUID
    partner: str
    reference: str
    recommendation: str
    reasons: list[str]
    reported_at: datetime


class AdverseAction(BaseModel):
    reasons: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        min_length=1, max_length=20
    )
    notice_given_on: date


class Decision(BaseModel):
    decision: Literal["approved", "conditional", "declined"]
    conditions: str | None = Field(default=None, max_length=2000)
    screening_id: UUID | None = None
    adverse_action: AdverseAction | None = None


class DecisionOut(BaseModel):
    decision: str
    conditions: str | None
    screening_id: UUID | None
    decided_at: datetime
    adverse_action: AdverseAction | None


class ApplicationOut(BaseModel):
    id: UUID
    property: str
    unit: str
    monthly_rent: str
    move_in_on: date
    monthly_income: str
    submitted_at: datetime
    applicants: list[ApplicantIn]
    screenings: list[Screening]
    decision: DecisionOut | None


@router.get("/api/leasing/units")
def units(session: Staff, conn: Conn) -> list[Unit]:
    """The PMC's units, and whether a lease is in effect on each today."""
    rows = conn.execute(
        "SELECT u.id, p.display_name, u.display_name, EXISTS ("
        "   SELECT 1 FROM trust_leases AS l WHERE l.unit_id = u.id AND l.starts_on <= %s"
        "   AND (l.ends_on IS NULL OR l.ends_on >= %s))"
        " FROM trust_units AS u JOIN trust_properties AS p ON p.id = u.property_id"
        ' WHERE u.pmc_id = %s ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C",'
        " u.id",
        (today(), today(), session.pmc_id),
    ).fetchall()
    return [Unit(unit_id=i, property=p, unit=u, leased_today=leased) for i, p, u, leased in rows]


@router.get("/api/leasing/links")
def links(session: Staff, conn: Conn) -> list[Link]:
    """The PMC's open application links, newest first."""
    rows = conn.execute(
        "SELECT l.id, p.display_name, u.display_name, l.monthly_rent, l.opened_at,"
        " (SELECT count(*) FROM trust_applications AS a WHERE a.link_id = l.id)"
        " FROM trust_application_links AS l"
        " JOIN trust_units AS u ON u.id = l.unit_id"
        " JOIN trust_properties AS p ON p.id = u.property_id"
        " WHERE l.pmc_id = %s AND NOT EXISTS ("
        "   SELECT 1 FROM trust_application_link_closings AS c WHERE c.link_id = l.id)"
        " ORDER BY l.opened_at DESC, l.id",
        (session.pmc_id,),
    ).fetchall()
    return [
        Link(
            id=i, property=p, unit=u, monthly_rent=exports.text(rent), opened_at=at, applications=n
        )
        for i, p, u, rent, at, n in rows
    ]


@router.post("/api/leasing/links", status_code=201)
def open_application_link(request: Request, session: Staff, conn: Conn, new: NewLink) -> OpenedLink:
    """A link for applying to one of the PMC's units. Its URL is shown this once."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_units WHERE pmc_id = %s AND id = %s", (session.pmc_id, new.unit_id)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such unit in this PMC")
    token, digest = new_token()
    row = conn.execute(
        "INSERT INTO trust_application_links"
        " (pmc_id, unit_id, token_sha256, monthly_rent, opened_by)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (session.pmc_id, new.unit_id, digest, new.monthly_rent, session.person_id),
    ).fetchone()
    assert row is not None
    return OpenedLink(id=row[0], url=f"{public_url(request)}/apply#{token}")


@router.post("/api/leasing/links/{link_id}/close", status_code=204)
def close_application_link(session: Staff, conn: Conn, link_id: UUID) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM trust_application_links WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, link_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such application link")
    try:
        conn.execute(
            "INSERT INTO trust_application_link_closings (pmc_id, link_id, closed_by)"
            " VALUES (%s, %s, %s)",
            (session.pmc_id, link_id, session.person_id),
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "this link is closed already") from None


def applications_of(
    conn: psycopg.Connection[Any], pmc_id: UUID, only: UUID | None = None
) -> list[ApplicationOut]:
    """The PMC's applications (or one of them), newest first, with everything staff decide on."""
    rows = conn.execute(
        "SELECT a.id, p.display_name, u.display_name, l.monthly_rent, a.move_in_on,"
        " a.monthly_income, a.submitted_at,"
        " d.decision, d.conditions, d.screening_id, d.decided_at,"
        " av.reasons, av.notice_given_on"
        " FROM trust_applications AS a"
        " JOIN trust_application_links AS l ON l.id = a.link_id"
        " JOIN trust_units AS u ON u.id = l.unit_id"
        " JOIN trust_properties AS p ON p.id = u.property_id"
        " LEFT JOIN trust_application_decisions AS d ON d.application_id = a.id"
        " LEFT JOIN trust_adverse_actions AS av ON av.application_id = a.id"
        " WHERE a.pmc_id = %s AND (%s::uuid IS NULL OR a.id = %s::uuid)"
        " ORDER BY a.submitted_at DESC, a.id",
        (pmc_id, only, only),
    ).fetchall()
    ids = [row[0] for row in rows]
    people: dict[UUID, list[ApplicantIn]] = {i: [] for i in ids}
    for application, name, email in conn.execute(
        "SELECT application_id, display_name, email FROM trust_applicants"
        " WHERE application_id = ANY(%s) ORDER BY application_id, position",
        (ids,),
    ).fetchall():
        people[application].append(ApplicantIn(name=name, email=email))
    screened: dict[UUID, list[Screening]] = {i: [] for i in ids}
    for application, *screening_row in conn.execute(
        "SELECT application_id, id, partner, partner_reference, recommendation, reasons,"
        " reported_at FROM trust_screenings WHERE application_id = ANY(%s)"
        " ORDER BY application_id, reported_at, id",
        (ids,),
    ).fetchall():
        i, partner, reference, recommendation, reasons, at = screening_row
        screened[application].append(
            Screening(
                id=i,
                partner=partner,
                reference=reference,
                recommendation=recommendation,
                reasons=list(reasons),
                reported_at=at,
            )
        )
    return [
        ApplicationOut(
            id=i,
            property=prop,
            unit=unit,
            monthly_rent=exports.text(rent),
            move_in_on=move_in,
            monthly_income=exports.text(income),
            submitted_at=submitted,
            applicants=people[i],
            screenings=screened[i],
            decision=None
            if decision is None
            else DecisionOut(
                decision=decision,
                conditions=conditions,
                screening_id=screening_id,
                decided_at=decided_at,
                adverse_action=None
                if reasons is None
                else AdverseAction(reasons=list(reasons), notice_given_on=given_on),
            ),
        )
        for (
            i,
            prop,
            unit,
            rent,
            move_in,
            income,
            submitted,
            decision,
            conditions,
            screening_id,
            decided_at,
            reasons,
            given_on,
        ) in rows
    ]


def application_of(conn: psycopg.Connection[Any], pmc_id: UUID, i: UUID) -> ApplicationOut:
    found = applications_of(conn, pmc_id, i)
    if not found:
        raise HTTPException(404, "no such application")
    return found[0]


@router.get("/api/leasing/applications")
def applications(
    session: Staff,
    conn: Conn,
    show: Annotated[Literal["open", "all"], Query()] = "open",
) -> list[ApplicationOut]:
    """The PMC's applications: by default those not decided yet."""
    found = applications_of(conn, session.pmc_id)
    return found if show == "all" else [a for a in found if a.decision is None]


@router.post("/api/leasing/applications/{application_id}/screen", status_code=201)
def screen(request: Request, session: Staff, conn: Conn, application_id: UUID) -> ApplicationOut:
    """Screens the application through the server's partner and records its answer."""
    partner: screening.Partner | None = request.app.state.screening
    if partner is None:
        raise HTTPException(503, "no screening partner is set up on this server")
    found = application_of(conn, session.pmc_id, application_id)
    if found.decision is not None:
        raise HTTPException(409, "this application is decided already")
    result = partner.screen(
        screening.Application(
            applicants=tuple(screening.Applicant(a.name, a.email) for a in found.applicants),
            move_in_on=found.move_in_on,
            monthly_income=Decimal(found.monthly_income),
            monthly_rent=Decimal(found.monthly_rent),
        )
    )
    conn.execute(
        "INSERT INTO trust_screenings (pmc_id, application_id, partner, partner_reference,"
        " agency, recommendation, reasons, requested_by)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            session.pmc_id,
            application_id,
            result.partner,
            result.reference,
            result.agency,
            result.recommendation,
            list(result.reasons),
            session.person_id,
        ),
    )
    return application_of(conn, session.pmc_id, application_id)


@router.post("/api/leasing/applications/{application_id}/decide", status_code=201)
def decide(session: Staff, conn: Conn, application_id: UUID, decision: Decision) -> ApplicationOut:
    """The PMC's decision. A decline or a conditional approval that rests on a screening comes
    with its adverse action notice: its reasons and the day it was given."""
    found = application_of(conn, session.pmc_id, application_id)
    if found.decision is not None:
        raise HTTPException(409, "this application is decided already")
    conditions = (decision.conditions or "").strip() or None
    if (decision.decision == "conditional") != (conditions is not None):
        raise HTTPException(422, "a conditional approval says its conditions, and only it does")
    if decision.screening_id is not None and decision.screening_id not in {
        s.id for s in found.screenings
    }:
        raise HTTPException(422, "that screening isn't one of this application's")
    needs_notice = decision.decision != "approved" and decision.screening_id is not None
    if needs_notice != (decision.adverse_action is not None):
        raise HTTPException(
            422,
            "a decline or conditional approval that rests on a screening records its adverse"
            " action notice, and nothing else does",
        )
    action = decision.adverse_action
    reasons = [reason.strip() for reason in action.reasons] if action else []
    if action is not None:
        if not all(reasons):
            raise HTTPException(422, "every reason says something")
        if not today() - timedelta(days=30) <= action.notice_given_on <= today():
            raise HTTPException(422, "a notice is given on a day from 30 days ago to today")
    conn.execute(
        "INSERT INTO trust_application_decisions"
        " (pmc_id, application_id, decision, conditions, screening_id, decided_by)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (
            session.pmc_id,
            application_id,
            decision.decision,
            conditions,
            decision.screening_id,
            session.person_id,
        ),
    )
    if action is not None:
        assert decision.screening_id is not None  # needs_notice
        conn.execute(
            "INSERT INTO trust_adverse_actions"
            " (pmc_id, application_id, reasons, agency, notice_given_on, recorded_by)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (
                session.pmc_id,
                application_id,
                reasons,
                screening_agency(conn, decision.screening_id),
                action.notice_given_on,
                session.person_id,
            ),
        )
    return application_of(conn, session.pmc_id, application_id)


def screening_agency(conn: psycopg.Connection[Any], screening_id: UUID) -> str:
    row = conn.execute(
        "SELECT agency FROM trust_screenings WHERE id = %s", (screening_id,)
    ).fetchone()
    assert row is not None
    return str(row[0])


@router.get("/api/leasing/applications/{application_id}/notice")
def notice(session: Staff, conn: Conn, application_id: UUID) -> Response:
    """The adverse action notice recorded for an application, as a PDF to give the applicant."""
    found = application_of(conn, session.pmc_id, application_id)
    if found.decision is None or found.decision.adverse_action is None:
        raise HTTPException(404, "no adverse action notice was recorded for this application")
    action = found.decision.adverse_action
    assert found.decision.screening_id is not None
    agency = screening_agency(conn, found.decision.screening_id)
    outcome = {
        "declined": "Your application was declined.",
        "conditional": f"Your application was approved on conditions: {found.decision.conditions}",
    }[found.decision.decision]
    rows: list[tuple[str, str]] = [
        ("To", ", ".join(f"{a.name} <{a.email}>" for a in found.applicants)),
        ("Home applied for", f"{found.property}, {found.unit}"),
        ("Decision", outcome),
        *[("Reason", reason) for reason in action.reasons],
        ("Based on a report from", agency),
        ("The report's maker", "did not make this decision and can't say why it was made."),
        (
            "Your copy",
            "you may ask the agency above for a free copy of the report within"
            " 60 days of this notice.",
        ),
        (
            "Disputes",
            "you may dispute with the agency anything in the report you believe is"
            " wrong or incomplete.",
        ),
        ("Notice given on", action.notice_given_on.isoformat()),
    ]
    table = exports.Table("Adverse action notice", ("item", "detail"), tuple(rows))
    return download(exports.to_pdf(table, datetime.now(UTC)), "adverse-action-notice", "pdf")
