"""Announcements (migration 20261023000203; the founder, 2026-10-23, lane 2's third item): what a
staff member writes once to every tenant living at, or every owner of, a property, a property
group or the whole company. Each person with an email on file gets one email of it (once per
address, so a person renting at two homes gets one), on their Email tab as an announcement; the
rest are named, not written to. Staff see who would get it before sending, and each announcement
after with whether each email went. Writing one is the 'email' area's. The AI role reads none of
it. Nothing here moves money.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, model_validator

from pms import email, notices
from pms.api import scope
from pms.api.deps import Conn, Staff
from pms.api.emails import Subject, Text
from pms.api.rent import today
from pms.api.rentals import Page, Paged, PerPage

router = APIRouter(prefix="/api/announcements")

Audience = Literal["tenants", "owners"]

# The tenants living today at the properties in scope: on a lease in effect, and not one who
# left it before today.
TENANTS = f"""
SELECT DISTINCT t.id, t.display_name
FROM trust_tenants AS t
JOIN trust_lease_tenants AS lt ON lt.tenant_id = t.id
JOIN trust_leases AS l ON l.id = lt.lease_id
JOIN trust_units AS u ON u.id = l.unit_id
WHERE t.pmc_id = %(pmc)s AND {scope.within("u.property_id")}
  AND l.starts_on <= %(day)s AND (l.ends_on IS NULL OR %(day)s <= l.ends_on)
  AND NOT EXISTS (SELECT 1 FROM trust_lease_departures AS d
                  WHERE d.lease_id = l.id AND d.tenant_id = lt.tenant_id AND d.left_on < %(day)s)
ORDER BY t.display_name, t.id
"""  # noqa: S608

# The owners of an account at a property in scope, neither the owner nor the property archived.
OWNERS = f"""
SELECT DISTINCT o.id, o.display_name
FROM trust_owners AS o
JOIN trust_ledger_accounts AS a ON a.owner_id = o.id AND a.kind = 'owner_property'
WHERE o.pmc_id = %(pmc)s AND {scope.within("a.property_id")}
  AND NOT trust_is_archived(NULL, NULL, NULL, o.id)
  AND NOT trust_is_archived(a.property_id, NULL)
ORDER BY o.display_name, o.id
"""  # noqa: S608


class Where(BaseModel):
    """Whom an announcement is for: tenants or owners of one property, one property group, or
    (neither named) the whole company."""

    audience: Audience
    property_id: UUID | None = None
    group_id: UUID | None = None

    @model_validator(mode="after")
    def one_place(self) -> Where:
        if self.property_id is not None and self.group_id is not None:
            raise ValueError("an announcement goes to a property or a group, not both")
        return self


class NewAnnouncement(Where):
    subject: Subject
    body: Text


class Recipient(BaseModel):
    id: UUID
    name: str
    to: str


class Recipients(BaseModel):
    # Who gets it: one each per address.
    emailed: list[Recipient]
    # Living there or owning there with no email on file: nothing is written to them.
    without_email: list[str]
    # Where it goes, in words: "every tenant of Cedar House".
    words: str


def place_words(conn: psycopg.Connection[Any], pmc: UUID, where: Where) -> str:
    """Where an announcement goes, in words, refusing a property or group of another company."""
    who = "every tenant living at" if where.audience == "tenants" else "every owner of"
    if where.property_id is not None:
        row = conn.execute(
            "SELECT display_name FROM trust_properties WHERE pmc_id = %s AND id = %s",
            (pmc, where.property_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "no such property")
        return f"{who} {row[0]}"
    if where.group_id is not None:
        row = conn.execute(
            "SELECT display_name FROM trust_property_groups WHERE pmc_id = %s AND id = %s",
            (pmc, where.group_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "no such property group")
        return f"{who} the {row[0]} group's properties"
    return f"{who} the company's properties"


def recipients(
    conn: psycopg.Connection[Any], pmc: UUID, where: Where, day: date
) -> tuple[list[Recipient], list[str], str]:
    """Who an announcement goes to on a day: each person's address once (the first in name order
    keeps it), and those with none."""
    words = place_words(conn, pmc, where)
    query = TENANTS if where.audience == "tenants" else OWNERS
    kind: notices.Kind = "tenant" if where.audience == "tenants" else "owner"
    found = conn.execute(
        query, {"pmc": pmc, "day": day, **scope.params(where.group_id, where.property_id)}
    ).fetchall()
    emailed: list[Recipient] = []
    without: list[str] = []
    seen: set[str] = set()
    for person, name in sorted(found, key=lambda row: (row[1].encode(), str(row[0]))):
        to = notices.address(conn, pmc, kind, person)
        if to is None:
            without.append(name)
        elif to.lower() not in seen:
            seen.add(to.lower())
            emailed.append(Recipient(id=person, name=name, to=to))
    return emailed, without, words


@router.get("/recipients")
def preview(
    session: Staff,
    conn: Conn,
    audience: Annotated[Audience, Query()],
    property_id: Annotated[UUID | None, Query()] = None,
    group_id: Annotated[UUID | None, Query()] = None,
) -> Recipients:
    """Who an announcement would go to today, before staff write it."""
    try:
        where = Where(audience=audience, property_id=property_id, group_id=group_id)
    except ValueError as bad:
        raise HTTPException(422, str(bad)) from None
    emailed, without, words = recipients(conn, session.pmc_id, where, today())
    return Recipients(emailed=emailed, without_email=without, words=words)


class Sent(Recipients):
    id: UUID


@router.post("", status_code=201)
def announce(session: Staff, conn: Conn, new: NewAnnouncement) -> Sent:
    """Writes the announcement to everyone in its audience today with an email on file: one email
    each, signed with the staff member's name and the company's, going out after the request
    commits (or waiting in the outbox while the server sends none)."""
    emailed, without, words = recipients(conn, session.pmc_id, new, today())
    if not emailed and not without:
        raise HTTPException(422, f"no one is {words.replace('every ', 'among ', 1)} today")
    row = conn.execute(
        "INSERT INTO trust_announcements (pmc_id, audience, property_id, group_id, subject, body,"
        " sent_by) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            session.pmc_id,
            new.audience,
            new.property_id,
            new.group_id,
            new.subject,
            new.body,
            session.person_id,
        ),
    ).fetchone()
    assert row is not None
    kind: notices.Kind = "tenant" if new.audience == "tenants" else "owner"
    for person in emailed:
        email.write_announcement(
            conn,
            session.pmc_id,
            kind,
            person.id,
            person.to,
            new.subject,
            new.body,
            session.person_id,
            row[0],
        )
    return Sent(id=row[0], emailed=emailed, without_email=without, words=words)


class Line(BaseModel):
    id: UUID
    subject: str
    words: str
    by: str
    at: datetime
    emails: int
    sent: int
    failed: int


LIST = f"""
SELECT a.id, a.subject, a.audience, a.property_id, a.group_id, s.display_name, a.created_at,
       (SELECT count(*) FROM trust_emails AS e WHERE e.announcement_id = a.id),
       (SELECT count(*) FROM trust_emails AS e WHERE e.announcement_id = a.id
          AND EXISTS (SELECT 1 FROM trust_email_deliveries AS d
                      WHERE d.email_id = e.id AND d.error IS NULL)),
       (SELECT count(*) FROM trust_emails AS e WHERE e.announcement_id = a.id
          AND NOT EXISTS (SELECT 1 FROM trust_email_deliveries AS d
                          WHERE d.email_id = e.id AND d.error IS NULL)
          AND ((SELECT count(*) FROM trust_email_deliveries AS d WHERE d.email_id = e.id)
                 >= {email.MAX_ATTEMPTS}
               OR EXISTS (SELECT 1 FROM trust_email_deliveries AS d
                          WHERE d.email_id = e.id AND d.error = %(lost)s))),
       count(*) OVER ()
FROM trust_announcements AS a
JOIN trust_staff AS s ON s.id = a.sent_by
WHERE a.pmc_id = %(pmc)s
ORDER BY a.created_at DESC, a.id DESC
LIMIT %(per_page)s OFFSET %(offset)s
"""  # noqa: S608


def line_words(conn: psycopg.Connection[Any], pmc: UUID, row: tuple[Any, ...]) -> str:
    words = place_words(conn, pmc, Where(audience=row[2], property_id=row[3], group_id=row[4]))
    return words[0].upper() + words[1:]


@router.get("")
def announcements(
    session: Staff, conn: Conn, page: Page = 1, per_page: PerPage = 50
) -> Paged[Line]:
    """The company's announcements, newest first, a page at a time, each with how many emails it
    wrote and how many went or failed."""
    rows = conn.execute(
        LIST,
        {
            "pmc": session.pmc_id,
            "lost": email.LINK_LOST,
            "per_page": per_page,
            "offset": (page - 1) * per_page,
        },
    ).fetchall()
    lines = [
        Line(
            id=row[0],
            subject=row[1],
            words=line_words(conn, session.pmc_id, row),
            by=row[5],
            at=row[6],
            emails=row[7],
            sent=row[8],
            failed=row[9],
        )
        for row in rows
    ]
    return Paged(items=lines, total=rows[0][10] if rows else 0, page=page, per_page=per_page)


class EmailOf(BaseModel):
    name: str
    to: str
    status: Literal["sent", "waiting", "failed"]
    error: str | None


class Announcement(BaseModel):
    id: UUID
    subject: str
    body: str
    words: str
    by: str
    at: datetime
    emails: list[EmailOf]


@router.get("/{announcement_id}")
def announcement(session: Staff, conn: Conn, announcement_id: UUID) -> Announcement:
    """An announcement and each email of it, by name, with whether it went."""
    row = conn.execute(
        "SELECT a.id, a.subject, a.audience, a.property_id, a.group_id, s.display_name,"
        " a.created_at, a.body FROM trust_announcements AS a"
        " JOIN trust_staff AS s ON s.id = a.sent_by WHERE a.pmc_id = %s AND a.id = %s",
        (session.pmc_id, announcement_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such announcement")
    emails = conn.execute(
        EMAILS_OF, {"pmc": session.pmc_id, "lost": email.LINK_LOST, "announcement": row[0]}
    ).fetchall()
    return Announcement(
        id=row[0],
        subject=row[1],
        body=row[7],
        words=line_words(conn, session.pmc_id, row),
        by=row[5],
        at=row[6],
        emails=[EmailOf(name=n, to=to, status=st, error=err) for n, to, st, err in emails],
    )


# Each email of an announcement: whom it went to, and whether it went (as the Email tab says).
EMAILS_OF = f"""
SELECT coalesce(t.display_name, o.display_name), e.to_address, x.status, x.error
FROM trust_emails AS e
LEFT JOIN trust_tenants AS t ON t.id = e.tenant_id
LEFT JOIN trust_owners AS o ON o.id = e.owner_id
CROSS JOIN LATERAL (
    SELECT CASE WHEN EXISTS (SELECT 1 FROM trust_email_deliveries AS d
                             WHERE d.email_id = e.id AND d.error IS NULL) THEN 'sent'
                WHEN (SELECT count(*) FROM trust_email_deliveries AS d WHERE d.email_id = e.id)
                     >= {email.MAX_ATTEMPTS}
                  OR EXISTS (SELECT 1 FROM trust_email_deliveries AS d
                             WHERE d.email_id = e.id AND d.error = %(lost)s) THEN 'failed'
                ELSE 'waiting' END AS status,
           (SELECT d.error FROM trust_email_deliveries AS d WHERE d.email_id = e.id
            ORDER BY d.attempted_at DESC LIMIT 1) AS error
) AS x
WHERE e.pmc_id = %(pmc)s AND e.announcement_id = %(announcement)s
ORDER BY coalesce(t.display_name, o.display_name) COLLATE "C", e.to_address
"""  # noqa: S608
