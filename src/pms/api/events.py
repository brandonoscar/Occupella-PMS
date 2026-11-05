"""Events through the API (ADR 0011's third piece, migration 20261105000500): software with a key
reads its company's events in order (`GET /api/events?after=`), and an admin adds the webhooks
they go to (Settings, AI) and takes one back.

An event is a kind and the ids it is about (src/pms/webhooks.py says what each webhook gets).
Read with a key, as the AI's role: the key's company's events after the event named (none: from
the first), in the order their transactions began writing, each only once every transaction that
began writing before it has finished. So a reader that asks again with the last id it got never
misses one, even one written by a transaction that began earlier and finished later; an event
waits for the longest writing transaction on the database server before it, a few seconds as a
rule (a company's nightly run is one). A webhook's secret is shown once and kept
sealed under PMS_DATA_KEY; a server without the key keeps no webhook.
"""

from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, StringConstraints

from pms import sealed, webhooks
from pms.api.ai_keys import Admin, WithKey
from pms.api.deps import Conn

router = APIRouter(prefix="/api")

PREFIX = "whsec_"
Url = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class Event(BaseModel):
    id: int
    kind: str
    at: datetime
    # The ids it is about, by name: read the rest through the API.
    subject: dict[str, Any]


class Events(BaseModel):
    events: list[Event]
    # The id to ask after next: the last one here, or the one asked after when none is new.
    next: int | None


# The company's events after the one named, of transactions all finished (every transaction that
# began writing before them has committed or rolled back), in the order they began writing.
EVENTS = """
SELECT e.id, e.kind, e.created_at, e.subject FROM trust_events AS e
WHERE e.pmc_id = %(pmc)s AND e.xact < pg_snapshot_xmin(pg_current_snapshot())
  AND (%(after)s::bigint IS NULL OR (e.xact, e.id) > (
    SELECT a.xact, a.id FROM trust_events AS a WHERE a.id = %(after)s))
ORDER BY e.xact, e.id
LIMIT %(limit)s
"""


def events_after(
    conn: psycopg.Connection[Any], pmc_id: UUID, after: int | None, limit: int
) -> list[Event]:
    rows = conn.execute(EVENTS, {"pmc": pmc_id, "after": after, "limit": limit}).fetchall()
    return [Event(id=i, kind=k, at=at, subject=s) for i, k, at, s in rows]


@router.get("/events")
def events(
    key: WithKey,
    conn: Conn,
    after: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> Events:
    """The key's company's events after the one named, in the order their transactions began
    writing, of transactions all finished; at most `limit`."""
    if after is not None:
        known = conn.execute(
            "SELECT 1 FROM trust_events WHERE pmc_id = %s AND id = %s", (key.pmc_id, after)
        ).fetchone()
        if known is None:
            raise HTTPException(422, "after names no event of this company")
    found = events_after(conn, key.pmc_id, after, limit)
    return Events(events=found, next=found[-1].id if found else after)


# --- an admin's webhooks -----------------------------------------------------------------------


class Webhook(BaseModel):
    id: UUID
    url: str
    # The secret's last four characters, to tell it apart; never the secret.
    last4: str
    made_at: datetime
    made_by: str
    revoked_at: datetime | None
    # The latest attempt: when, and why it failed (None: it was delivered).
    last_attempt_at: datetime | None
    last_error: str | None


class NewWebhook(BaseModel):
    url: Url


class MadeWebhook(BaseModel):
    id: UUID
    url: str
    # Shown this once: the receiver checks each event's signature with it.
    secret: str


WEBHOOKS = """
SELECT s.id, s.url, s.secret_last4, s.created_at, st.display_name, r.created_at,
       d.attempted_at, d.error
FROM trust_event_subscriptions AS s
JOIN trust_staff AS st ON st.id = s.recorded_by
LEFT JOIN trust_event_subscription_revocations AS r ON r.subscription_id = s.id
LEFT JOIN LATERAL (
    SELECT x.attempted_at, x.error FROM trust_event_deliveries AS x
    WHERE x.subscription_id = s.id ORDER BY x.attempted_at DESC, x.id DESC LIMIT 1
) AS d ON true
WHERE s.pmc_id = %s
ORDER BY s.created_at DESC, s.id
"""


@router.get("/ai/webhooks")
def list_webhooks(session: Admin, conn: Conn) -> list[Webhook]:
    """The company's webhooks, newest first, each standing or when it was taken back, with how
    its latest delivery went."""
    return [
        Webhook(
            id=i,
            url=u,
            last4=l4,
            made_at=m,
            made_by=b,
            revoked_at=r,
            last_attempt_at=at,
            last_error=e,
        )
        for i, u, l4, m, b, r, at, e in conn.execute(WEBHOOKS, (session.pmc_id,)).fetchall()
    ]


@router.post("/ai/webhooks", status_code=201)
def add_webhook(session: Admin, conn: Conn, new: NewWebhook) -> MadeWebhook:
    """A webhook every event of the company goes to from now on; its secret is in this answer
    and nowhere else."""
    try:
        webhooks.parts_of(new.url)
    except webhooks.Refused as refused:
        raise HTTPException(422, str(refused)) from None
    try:
        key = sealed.key_from(os.environ, webhooks.KEEPS)
    except sealed.NoKey as missing:
        raise HTTPException(409, f"{missing}, so it can't keep a webhook's secret") from None
    made = uuid.uuid4()
    secret = PREFIX + secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO trust_event_subscriptions (id, pmc_id, url, secret_sealed, secret_last4)"
        " VALUES (%s, %s, %s, %s, %s)",
        (made, session.pmc_id, new.url, sealed.seal(secret, str(made), key), secret[-4:]),
    )
    return MadeWebhook(id=made, url=new.url, secret=secret)


@router.post("/ai/webhooks/{subscription_id}/revoke", status_code=204)
def revoke_webhook(session: Admin, conn: Conn, subscription_id: UUID) -> None:
    """Takes the webhook back: nothing more is sent to it."""
    found = conn.execute(
        "SELECT 1 FROM trust_event_subscriptions WHERE pmc_id = %s AND id = %s",
        (session.pmc_id, subscription_id),
    ).fetchone()
    if found is None:
        raise HTTPException(404, "no such webhook")
    # Once: a second take-back, or two at once, writes nothing more.
    taken = conn.execute(
        "INSERT INTO trust_event_subscription_revocations (subscription_id, pmc_id)"
        " VALUES (%s, %s) ON CONFLICT (subscription_id) DO NOTHING RETURNING subscription_id",
        (subscription_id, session.pmc_id),
    ).fetchone()
    if taken is None:
        raise HTTPException(409, "this webhook was taken back already")
