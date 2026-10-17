"""Email from staff (docs/adr/0007-email.md; migration 0076): what staff write to a tenant, an
owner or a vendor from that person's Email tab, or to every tenant living at a property, and the
list of every email the PMS wrote to a person (invites, resets, messages, a bill's approval),
each with whether it went. An email goes to the person's contact email, else to their portal
login's. Staff need the 'email' area to write (custom roles). The AI role reads none of it.
Nothing here moves money.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, StringConstraints

from pms import email
from pms.api import directory
from pms.api.deps import Conn, Staff
from pms.api.rent import today

router = APIRouter(prefix="/api")

About = Literal["tenant", "owner", "vendor"]
# One line, no NUL: an email header can't hold a line break.
Subject = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[^\x00\r\n]*$"
    ),
]
# Any text but a NUL, which Postgres text can't hold; the signature is added below it.
Text = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=8000, pattern=r"^[^\x00]*$"),
]
# What the list shows where an invite's or a reset's link went: the link itself is never kept.
LINK_SHOWN = "(the sign-in link, sent only in the email)"
COLUMN: dict[str, directory.Person] = {
    "tenant": "tenant_id",
    "owner": "owner_id",
    "vendor": "vendor_id",
}


class NewEmail(BaseModel):
    about: About
    about_id: UUID
    subject: Subject
    body: Text


class NewPropertyEmail(BaseModel):
    property_id: UUID
    subject: Subject
    body: Text


class Sent(BaseModel):
    id: UUID
    name: str
    to: str


class PropertySent(BaseModel):
    emailed: list[Sent]
    # Tenants living there with no email on file: nothing was written to them.
    without_email: list[str]


class EmailLine(BaseModel):
    id: UUID
    kind: str
    to: str
    subject: str
    body: str
    at: datetime
    # Who wrote a message; none for what the PMS writes by itself.
    by: str | None
    status: Literal["sent", "waiting", "failed"]
    # Why the last attempt failed, when it did.
    error: str | None


def address_of(
    conn: psycopg.Connection[Any], pmc: UUID, about: About, person_id: UUID
) -> tuple[str, str | None]:
    """A person's name and the address their email goes to: their contact email, else their
    portal login's (an owner's or a tenant's, while it can sign in), else none."""
    named = conn.execute(email.PEOPLE[about][0], (pmc, person_id)).fetchone()
    if named is None:
        raise HTTPException(404, f"no such {about}")
    contact = directory.contact_of(conn, COLUMN[about], person_id).email
    if contact:
        return named[0], contact
    login = conn.execute(
        "SELECT email FROM trust_logins WHERE pmc_id = %s AND disabled_at IS NULL"
        " AND (owner_id = %s OR tenant_id = %s)",
        (pmc, person_id, person_id),
    ).fetchone()
    return named[0], None if login is None else login[0]


EMAILS = f"""
SELECT e.id, e.kind, e.to_address, e.subject, e.body, e.created_at, s.display_name,
       CASE WHEN EXISTS (SELECT 1 FROM trust_email_deliveries AS d
                         WHERE d.email_id = e.id AND d.error IS NULL) THEN 'sent'
            WHEN (SELECT count(*) FROM trust_email_deliveries AS d WHERE d.email_id = e.id)
                 >= {email.MAX_ATTEMPTS}
              OR EXISTS (SELECT 1 FROM trust_email_deliveries AS d
                         WHERE d.email_id = e.id AND d.error = %(lost)s) THEN 'failed'
            ELSE 'waiting' END,
       (SELECT d.error FROM trust_email_deliveries AS d WHERE d.email_id = e.id
        ORDER BY d.attempted_at DESC LIMIT 1)
FROM trust_emails AS e
LEFT JOIN trust_staff AS s ON s.id = e.sent_by
LEFT JOIN trust_bills AS b ON b.id = e.bill_id
LEFT JOIN trust_ledger_accounts AS t ON t.ledger_account_id = b.ledger_account_id
WHERE e.pmc_id = %(pmc)s
  AND (e.tenant_id = %(tenant)s OR e.vendor_id = %(vendor)s OR e.owner_id = %(owner)s
       OR (e.kind = 'bill_approval' AND t.owner_id = %(owner)s))
ORDER BY e.created_at DESC, e.id DESC
"""


@router.get("/emails")
def emails_of(
    session: Staff, conn: Conn, about: Annotated[About, Query()], about_id: UUID
) -> list[EmailLine]:
    """Every email the PMS wrote to a person, newest first, each with whether it went (a bill's
    approval email to an owner included, whenever it was written)."""
    keys = {kind: about_id if kind == about else None for kind in ("tenant", "owner", "vendor")}
    rows = conn.execute(EMAILS, {"pmc": session.pmc_id, "lost": email.LINK_LOST, **keys}).fetchall()
    return [
        EmailLine(
            id=i,
            kind=kind,
            to=to,
            subject=subject,
            body=body.replace(email.LINK, LINK_SHOWN),
            at=at,
            by=by,
            status=status,
            error=error,
        )
        for i, kind, to, subject, body, at, by, status, error in rows
    ]


@router.post("/emails", status_code=201)
def send_email(session: Staff, conn: Conn, new: NewEmail) -> Sent:
    """Writes a staff member's email to a tenant, an owner or a vendor of the company; it goes
    out after the request commits (or waits in the outbox while the server sends none)."""
    name, to = address_of(conn, session.pmc_id, new.about, new.about_id)
    if to is None:
        raise HTTPException(422, f"{name} has no email on file: add one on their page first")
    made = email.write_message(
        conn, session.pmc_id, new.about, new.about_id, to, new.subject, new.body, session.person_id
    )
    return Sent(id=made, name=name, to=to)


LIVING_AT = """
SELECT t.id, t.display_name
FROM trust_tenants AS t
WHERE t.pmc_id = %(pmc)s
  AND EXISTS (
    SELECT 1 FROM trust_lease_tenants AS lt
    JOIN trust_leases AS l ON l.id = lt.lease_id
    JOIN trust_units AS u ON u.id = l.unit_id
    WHERE lt.tenant_id = t.id AND u.property_id = %(property)s
      AND l.starts_on <= %(day)s AND (l.ends_on IS NULL OR %(day)s <= l.ends_on)
  )
ORDER BY t.display_name COLLATE "C", t.id
"""


@router.post("/emails/property", status_code=201)
def email_property(session: Staff, conn: Conn, new: NewPropertyEmail) -> PropertySent:
    """Writes the same email to every tenant on a lease in effect today at a property: one email
    each, on each tenant's Email tab. Tenants with no email on file are named, not written to."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_properties WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, new.property_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such property")
    living = conn.execute(
        LIVING_AT, {"property": new.property_id, "pmc": session.pmc_id, "day": today()}
    ).fetchall()
    if not living:
        raise HTTPException(422, "no tenant lives there today")
    emailed: list[Sent] = []
    without: list[str] = []
    for tenant_id, name in living:
        _, to = address_of(conn, session.pmc_id, "tenant", tenant_id)
        if to is None:
            without.append(name)
            continue
        made = email.write_message(
            conn, session.pmc_id, "tenant", tenant_id, to, new.subject, new.body, session.person_id
        )
        emailed.append(Sent(id=made, name=name, to=to))
    return PropertySent(emailed=emailed, without_email=without)
