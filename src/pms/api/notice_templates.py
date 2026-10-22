"""Notice templates and the notices made from them (migration 0119; the founder's review q095):
staff keep the company's own notices (late rent, entry, non-renewal, anything it writes) at
Settings, Notice templates, with blanks such as {{tenant_names}} or {{balance_due}}
(pms.notice_letters.FIELDS); on a lease's page they make one into a notice for that lease, dated
a day they pick, kept as filled in and downloaded as a PDF. A template's words are the company's:
the PMS writes no notice text and checks no state's notice period or wording. Area documents.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, StringConstraints

from pms import notice_letters
from pms.api import company, rent
from pms.api.deps import Conn, Staff, download, refusal

router = APIRouter(prefix="/api/notices")

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Body = Annotated[str, StringConstraints(min_length=1, max_length=20000, pattern=r"\S")]


class Field(BaseModel):
    name: str
    what: str


class Template(BaseModel):
    id: UUID
    # The version a notice made now would come from.
    version_id: UUID
    name: str
    body: str
    saved_at: datetime
    saved_by: str | None


class Templates(BaseModel):
    templates: list[Template]
    fields: list[Field]


class NewTemplate(BaseModel):
    name: Name
    body: Body


# The company's templates not removed, each at its latest version, by name.
CURRENT = """
SELECT t.id, v.id, v.name, v.body, v.created_at, s.display_name
FROM trust_notice_templates AS t
CROSS JOIN LATERAL (
    SELECT * FROM trust_notice_template_versions AS v
    WHERE v.template_id = t.id ORDER BY v.created_at DESC, v.id DESC LIMIT 1
) AS v
LEFT JOIN trust_staff AS s ON s.id = v.recorded_by
WHERE t.pmc_id = %(pmc)s AND (%(template)s::uuid IS NULL OR t.id = %(template)s)
  AND NOT EXISTS (SELECT 1 FROM trust_notice_template_removals AS r WHERE r.template_id = t.id)
ORDER BY lower(v.name) COLLATE "C", t.id
"""


def current(
    conn: psycopg.Connection[Any], pmc_id: UUID, template_id: UUID | None = None
) -> list[Template]:
    rows = conn.execute(CURRENT, {"pmc": pmc_id, "template": template_id}).fetchall()
    return [
        Template(id=t, version_id=v, name=n, body=b, saved_at=at, saved_by=by)
        for t, v, n, b, at, by in rows
    ]


def one(conn: psycopg.Connection[Any], pmc_id: UUID, template_id: UUID) -> Template:
    found = current(conn, pmc_id, template_id)
    if not found:
        raise HTTPException(404, "no such notice template")
    return found[0]


def known_blanks(body: str) -> None:
    strange = notice_letters.unknown(body)
    if strange:
        listed = ", ".join("{{" + name + "}}" for name in strange)
        raise HTTPException(
            422,
            f"{listed} {'is not a blank' if len(strange) == 1 else 'are not blanks'} the PMS"
            " fills; the list beside the text shows the ones it does",
        )


def save(
    conn: psycopg.Connection[Any], pmc_id: UUID, template_id: UUID, new: NewTemplate
) -> Template:
    known_blanks(new.body)
    try:
        conn.execute(
            "INSERT INTO trust_notice_template_versions (pmc_id, template_id, name, body)"
            " VALUES (%s, %s, %s, %s)",
            (pmc_id, template_id, new.name, new.body),
        )
    except (psycopg.errors.UniqueViolation, psycopg.errors.CheckViolation) as refused:
        raise HTTPException(409, refusal(refused)) from None
    return one(conn, pmc_id, template_id)


@router.get("/templates")
def templates(session: Staff, conn: Conn) -> Templates:
    """The company's notice templates, and the blanks a template may use."""
    return Templates(
        templates=current(conn, session.pmc_id),
        fields=[Field(name=n, what=w) for n, w in notice_letters.FIELDS.items()],
    )


@router.post("/templates", status_code=201)
def add_template(session: Staff, conn: Conn, new: NewTemplate) -> Template:
    """A new template; its name is the company's only one of that name, whatever its case."""
    known_blanks(new.body)
    row = conn.execute(
        "INSERT INTO trust_notice_templates (pmc_id) VALUES (%s) RETURNING id", (session.pmc_id,)
    ).fetchone()
    assert row is not None
    return save(conn, session.pmc_id, row[0], new)


@router.post("/templates/{template_id}", status_code=201)
def change_template(session: Staff, conn: Conn, template_id: UUID, new: NewTemplate) -> Template:
    """A template's new name and text, from now on; saving what it says already writes nothing.
    Notices made before keep the words they were made with."""
    was = one(conn, session.pmc_id, template_id)
    if (was.name, was.body) == (new.name, new.body):
        return was
    return save(conn, session.pmc_id, template_id, new)


@router.post("/templates/{template_id}/remove", status_code=204)
def remove_template(session: Staff, conn: Conn, template_id: UUID) -> None:
    """Takes a template off the list for good; the notices made from it stay."""
    one(conn, session.pmc_id, template_id)
    try:
        conn.execute(
            "INSERT INTO trust_notice_template_removals (template_id, pmc_id) VALUES (%s, %s)",
            (template_id, session.pmc_id),
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "this notice template is removed already") from None


# --- a notice for a lease ------------------------------------------------------------------------


class NewNotice(BaseModel):
    lease_id: UUID
    template_id: UUID
    dated_on: date


class Filled(BaseModel):
    title: str
    letterhead: str
    body: str


class Notice(BaseModel):
    id: UUID
    lease_id: UUID
    title: str
    dated_on: date
    made_at: datetime
    made_by: str | None


# A lease of the PMC: its property, unit, days and the tenants on it who hadn't left by a day.
LEASE = """
SELECT p.display_name, u.display_name, l.starts_on, l.ends_on,
       trust_rent_on(l.id, %(day)s),
       array(
           SELECT t.display_name FROM trust_lease_tenants AS lt
           JOIN trust_tenants AS t ON t.id = lt.tenant_id
           WHERE lt.lease_id = l.id AND NOT EXISTS (
               SELECT 1 FROM trust_lease_departures AS d
               WHERE d.lease_id = l.id AND d.tenant_id = lt.tenant_id AND d.left_on <= %(day)s
           )
           ORDER BY t.display_name COLLATE "C", t.id
       )
FROM trust_leases AS l
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
WHERE l.pmc_id = %(pmc)s AND l.id = %(lease)s
"""


def dollars(amount: Decimal | None) -> str:
    return "" if amount is None else f"${amount:,.2f}"


def filled(
    conn: psycopg.Connection[Any], pmc_id: UUID, staff_id: UUID, asked: NewNotice
) -> tuple[Template, Filled]:
    template = one(conn, pmc_id, asked.template_id)
    row = conn.execute(
        LEASE, {"pmc": pmc_id, "lease": asked.lease_id, "day": asked.dated_on}
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such lease")
    prop, unit, starts_on, ends_on, monthly, tenants = row
    by = company.name_of(conn, pmc_id)
    street = company.street_of(conn, pmc_id)
    values = {
        "tenant_names": notice_letters.names(list(tenants)),
        "property": prop,
        "unit": unit,
        "monthly_rent": dollars(monthly),
        "balance_due": dollars(rent.owed(conn, asked.lease_id)),
        "lease_start": notice_letters.us(starts_on),
        "lease_end": "month to month" if ends_on is None else notice_letters.us(ends_on),
        "notice_date": notice_letters.us(asked.dated_on),
        "company_name": by,
        "company_address": street,
        "staff_name": rent.staff_name(conn, pmc_id, staff_id),
    }
    letterhead = by if not street else f"{by}\n{street}"
    return template, Filled(
        title=template.name,
        letterhead=letterhead,
        body=notice_letters.fill(template.body, values),
    )


@router.post("/preview")
def preview(session: Staff, conn: Conn, asked: NewNotice) -> Filled:
    """The notice as it would read for the lease on the day; nothing is written."""
    return filled(conn, session.pmc_id, session.person_id, asked)[1]


NOTICES = """
SELECT n.id, n.lease_id, n.title, n.dated_on, n.created_at, s.display_name
FROM trust_notices AS n LEFT JOIN trust_staff AS s ON s.id = n.recorded_by
WHERE n.pmc_id = %(pmc)s AND (%(lease)s::uuid IS NULL OR n.lease_id = %(lease)s)
  AND (%(notice)s::uuid IS NULL OR n.id = %(notice)s)
ORDER BY n.created_at DESC, n.id DESC
"""


def notices_of(
    conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID | None, notice_id: UUID | None
) -> list[Notice]:
    rows = conn.execute(NOTICES, {"pmc": pmc_id, "lease": lease_id, "notice": notice_id}).fetchall()
    return [
        Notice(id=n, lease_id=lease, title=t, dated_on=on, made_at=at, made_by=by)
        for n, lease, t, on, at, by in rows
    ]


@router.post("", status_code=201)
def make(session: Staff, conn: Conn, asked: NewNotice) -> Notice:
    """A notice for a lease from a template, kept as filled in now, to download as a PDF."""
    template, made = filled(conn, session.pmc_id, session.person_id, asked)
    row = conn.execute(
        "INSERT INTO trust_notices (pmc_id, lease_id, template_version_id, dated_on, title,"
        " letterhead, body) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            session.pmc_id,
            asked.lease_id,
            template.version_id,
            asked.dated_on,
            made.title,
            made.letterhead,
            made.body,
        ),
    ).fetchone()
    assert row is not None
    return notices_of(conn, session.pmc_id, None, row[0])[0]


@router.get("")
def notices(session: Staff, conn: Conn, lease_id: UUID) -> list[Notice]:
    """A lease's notices, newest first."""
    return notices_of(conn, session.pmc_id, lease_id, None)


@router.get("/{notice_id}/pdf")
def notice_pdf(session: Staff, conn: Conn, notice_id: UUID) -> Response:
    """A notice as its PDF, the same words whenever it is downloaded."""
    row = conn.execute(
        "SELECT title, letterhead, dated_on, body FROM trust_notices WHERE pmc_id = %s AND id = %s",
        (session.pmc_id, notice_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such notice")
    title, letterhead, dated_on, body = row
    letter = notice_letters.Letter(title, letterhead, dated_on, body)
    return download(
        notice_letters.render(letter, datetime.now(UTC)), f"notice-{dated_on.isoformat()}", "pdf"
    )
