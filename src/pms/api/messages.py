"""Messages between the company and its tenants and owners (the portals' second version,
migration 20261103000100): one conversation per tenant or owner, each message by the person or
by a staff member, kept for good (trust_messages, append-only).

A tenant or an owner reads their conversation and writes in it from their portal's Messages
section, at most MESSAGES_PER_DAY a day. Staff see every conversation at /messages, the ones
whose latest message is the person's first ("waiting"), and answer from there or from the
person's page; staff need the custom roles' 'email' area to write. An answer is also emailed to
the person while the server sends email (a staff member's message, email.write_message), with a
line back to the portal. The person and the company always come from the session; no request
names another's conversation (a staff route's person must be of the session's company).

The AI role reads no message (AI_NEVER_READS). Nothing here moves money.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StringConstraints

from pms import email, notices
from pms.api import outbox
from pms.api.deps import Conn, Owner, Staff, Tenant

router = APIRouter(prefix="/api")

MESSAGES_PER_DAY = 20
About = Literal["tenant", "owner"]
COLUMN = {"tenant": "tenant_id", "owner": "owner_id"}
TABLE = {"tenant": "trust_tenants", "owner": "trust_owners"}
# Any text but a NUL (Postgres text can't hold one), up to 4,000 characters.
Body = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000, pattern=r"^[^\x00]*$"),
]


class Message(BaseModel):
    id: UUID
    # Who wrote it: the person themselves, or the company (with the staff member's name, which
    # staff see; the portal shows the company's).
    by: Literal["person", "company"]
    staff: str | None
    body: str
    at: datetime


class NewMessage(BaseModel):
    body: Body


class Conversation(BaseModel):
    about: About
    person_id: UUID
    name: str
    messages: list[Message]


class Thread(BaseModel):
    about: About
    person_id: UUID
    name: str
    # The latest message: when, by whom, and its first line or so.
    last_at: datetime
    last_by: Literal["person", "company"]
    preview: str
    # The person wrote last: it waits for an answer.
    waiting: bool
    count: int


def conversation(
    conn: psycopg.Connection[Any], pmc: UUID, about: About, person: UUID
) -> list[Message]:
    rows = conn.execute(
        f"SELECT m.id, m.recorded_by IS NULL, s.display_name, m.body, m.created_at"  # noqa: S608
        " FROM trust_messages AS m LEFT JOIN trust_staff AS s ON s.id = m.recorded_by"
        f" WHERE m.pmc_id = %s AND m.{COLUMN[about]} = %s"
        " ORDER BY m.created_at, m.id",
        (pmc, person),
    ).fetchall()
    return [
        Message(id=i, by="person" if mine else "company", staff=staff, body=body, at=at)
        for i, mine, staff, body, at in rows
    ]


def write(conn: psycopg.Connection[Any], pmc: UUID, about: About, person: UUID, body: str) -> UUID:
    row = conn.execute(
        f"INSERT INTO trust_messages (pmc_id, {COLUMN[about]}, body)"  # noqa: S608
        " VALUES (%s, %s, %s) RETURNING id",
        (pmc, person, body),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def from_person(
    conn: psycopg.Connection[Any], pmc: UUID, about: About, person: UUID, body: str
) -> Message:
    """A tenant's or owner's own message, at most MESSAGES_PER_DAY a day (UTC)."""
    (today,) = conn.execute(
        f"SELECT count(*) FROM trust_messages WHERE {COLUMN[about]} = %s"  # noqa: S608
        " AND recorded_by IS NULL"
        " AND (created_at AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date",
        (person,),
    ).fetchone() or (0,)
    if today >= MESSAGES_PER_DAY:
        raise HTTPException(
            429, f"at most {MESSAGES_PER_DAY} messages a day; call the office if it can't wait"
        )
    made = write(conn, pmc, about, person, body)
    return next(m for m in conversation(conn, pmc, about, person) if m.id == made)


def name_of(conn: psycopg.Connection[Any], pmc: UUID, about: About, person: UUID) -> str:
    row = conn.execute(
        f"SELECT display_name FROM {TABLE[about]} WHERE pmc_id = %s AND id = %s",  # noqa: S608
        (pmc, person),
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"no such {about}")
    return str(row[0])


# --- the portals ---------------------------------------------------------------------------


@router.get("/tenant/messages")
def tenant_messages(session: Tenant, conn: Conn) -> list[Message]:
    """The tenant's conversation with the company, oldest first."""
    return conversation(conn, session.pmc_id, "tenant", session.person_id)


@router.post("/tenant/messages", status_code=201)
def tenant_writes(session: Tenant, conn: Conn, new: NewMessage) -> Message:
    """A message from the tenant to the company."""
    return from_person(conn, session.pmc_id, "tenant", session.person_id, new.body)


@router.get("/owner/messages")
def owner_messages(session: Owner, conn: Conn) -> list[Message]:
    """The owner's conversation with the company, oldest first."""
    return conversation(conn, session.pmc_id, "owner", session.person_id)


@router.post("/owner/messages", status_code=201)
def owner_writes(session: Owner, conn: Conn, new: NewMessage) -> Message:
    """A message from the owner to the company."""
    return from_person(conn, session.pmc_id, "owner", session.person_id, new.body)


# --- staff ---------------------------------------------------------------------------------

THREADS = """
WITH m AS (
    SELECT m.*, CASE WHEN m.tenant_id IS NOT NULL THEN 'tenant' ELSE 'owner' END AS about,
           coalesce(m.tenant_id, m.owner_id) AS person,
           row_number() OVER (PARTITION BY coalesce(m.tenant_id, m.owner_id)
                              ORDER BY m.created_at DESC, m.id DESC) AS newest,
           count(*) OVER (PARTITION BY coalesce(m.tenant_id, m.owner_id)) AS n
    FROM trust_messages AS m WHERE m.pmc_id = %(pmc)s
)
SELECT m.about, m.person, coalesce(t.display_name, o.display_name), m.created_at,
       m.recorded_by IS NULL, m.body, m.n
FROM m
LEFT JOIN trust_tenants AS t ON t.id = m.tenant_id
LEFT JOIN trust_owners AS o ON o.id = m.owner_id
WHERE m.newest = 1
ORDER BY (m.recorded_by IS NULL) DESC, m.created_at DESC, m.person
LIMIT 500
"""


def preview(body: str) -> str:
    line = " ".join(body.split())
    return line if len(line) <= 140 else line[:139] + "…"


def threads(conn: psycopg.Connection[Any], pmc: UUID) -> list[Thread]:
    return [
        Thread(
            about=about,
            person_id=person,
            name=name,
            last_at=at,
            last_by="person" if mine else "company",
            preview=preview(body),
            waiting=mine,
            count=n,
        )
        for about, person, name, at, mine, body, n in conn.execute(THREADS, {"pmc": pmc}).fetchall()
    ]


@router.get("/messages")
def inbox(session: Staff, conn: Conn) -> list[Thread]:
    """Every conversation with a tenant or an owner, those waiting for an answer first, then the
    latest first (500 at most)."""
    return threads(conn, session.pmc_id)


@router.get("/messages/{about}/{person_id}")
def staff_conversation(session: Staff, conn: Conn, about: About, person_id: UUID) -> Conversation:
    """A tenant's or an owner's conversation with the company, oldest first."""
    name = name_of(conn, session.pmc_id, about, person_id)
    messages = conversation(conn, session.pmc_id, about, person_id)
    return Conversation(about=about, person_id=person_id, name=name, messages=messages)


class Answered(BaseModel):
    message: Message
    # Where the email copy went, when the person has an email on file; None otherwise (they see
    # the answer when they next sign in).
    emailed_to: str | None


@router.post("/messages/{about}/{person_id}", status_code=201)
def answer(session: Staff, conn: Conn, about: About, person_id: UUID, new: NewMessage) -> Answered:
    """A staff member's message to a tenant or an owner, in the conversation and, while the
    server sends email, to their email too."""
    name_of(conn, session.pmc_id, about, person_id)
    made = write(conn, session.pmc_id, about, person_id, new.body)
    to = notices.address(conn, session.pmc_id, about, person_id)
    if to is not None:
        company = conn.execute(
            "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (session.pmc_id,)
        ).fetchone()
        portal = f"{outbox.PUBLIC_URL.get().rstrip('/')}/messages"
        email.write_message(
            conn,
            session.pmc_id,
            about,
            person_id,
            to,
            f"A message from {company[0] if company else 'your manager'}",
            f"{new.body}\n\nRead it and answer in your portal:\n{portal}",
            session.person_id,
        )
    message = next(m for m in conversation(conn, session.pmc_id, about, person_id) if m.id == made)
    return Answered(message=message, emailed_to=to)
