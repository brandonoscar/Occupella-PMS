"""Calendar feeds (migration 0098): a staff member or a tenant subscribes their own calendar app
(Google, Apple, Outlook) to a link of the PMS's, and the app fetches what is coming as iCalendar
text (RFC 5545, written by the `icalendar` package).

- A staff member's feed: the company's move-ins and lease ends, work orders' visits and due days,
  showings, bills due, rent changes and insurance running out, from 30 days back to a year ahead,
  maybe of one property group's properties only; and the day to take the company's fees out of
  each trust bank account (docs/law.md).
- A tenant's feed: their leases' rent due days, their requests' visits and their leases' last
  days.

Each person makes their own link (a new one takes the old back) and takes it back. A calendar app
fetches with no login, so the token is in the link; it is kept only as its SHA-256, the server's
access log prints it as "…" (pms.api.app), and the feed stops once the person's login is
disabled. Events name homes and work, never a tenant's contact details, and every event keeps
one id across fetches so a calendar updates it in place. Nothing here moves money, and the AI
role reads none of it.
"""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import icalendar
import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from pms import accounts
from pms.api import payables
from pms.api.deps import Conn, Staff, Tenant, dollars
from pms.api.leasing import public_url

router = APIRouter()
BACK = timedelta(days=30)
AHEAD = timedelta(days=365)
SHOWING = timedelta(minutes=30)
# The token in a feed's path, as the access log hides it.
TOKEN_IN_PATH = re.compile(r"(/api/calendar/)[^/?\s]+(\.ics)")


class HideFeedTokens(logging.Filter):
    """The access log prints a calendar feed's path with its token as "…"."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) > 2 and isinstance(args[2], str):
            record.args = (*args[:2], TOKEN_IN_PATH.sub(r"\1…\2", args[2]), *args[3:])
        return True


def hide_feed_tokens(logger: logging.Logger) -> None:
    """Keeps feed tokens out of the logger's lines, once however often an app is made."""
    if not any(isinstance(f, HideFeedTokens) for f in logger.filters):
        logger.addFilter(HideFeedTokens())


@dataclass(frozen=True)
class Event:
    """One calendar entry: all day on `day`, or from `at` for half an hour."""

    uid: str
    day: date
    summary: str
    description: str | None = None
    at: datetime | None = None


def ics(name: str, events: list[Event], generated: datetime) -> bytes:
    cal = icalendar.Calendar()
    cal.add("prodid", "-//Occupella PMS//Calendar feed//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", name)
    for found in events:
        event = icalendar.Event()
        event.add("uid", found.uid)
        event.add("dtstamp", generated)
        event.add("summary", found.summary)
        if found.at is not None:
            event.add("dtstart", found.at)
            event.add("dtend", found.at + SHOWING)
        else:
            event.add("dtstart", found.day)
            event.add("dtend", found.day + timedelta(days=1))
        if found.description:
            event.add("description", found.description)
        cal.add_component(event)
    return bytes(cal.to_ical())


# --- a staff member's feed -----------------------------------------------------------------------

# Each property-tied event with its property, in the window, narrowed to a group when one is set.
STAFF_EVENTS = """
WITH homes AS (
    SELECT u.id AS unit_id, p.id AS property_id,
           p.display_name || ', ' || u.display_name AS home
    FROM trust_units AS u JOIN trust_properties AS p ON p.id = u.property_id
    WHERE p.pmc_id = %(pmc)s
      AND (%(group)s::uuid IS NULL OR p.id IN (SELECT trust_group_properties(%(group)s::uuid)))
),
props AS (
    SELECT p.id AS property_id, p.display_name AS home FROM trust_properties AS p
    WHERE p.pmc_id = %(pmc)s
      AND (%(group)s::uuid IS NULL OR p.id IN (SELECT trust_group_properties(%(group)s::uuid)))
),
orders AS (
    SELECT w.id, w.summary, coalesce(h.home, pr.home) AS home
    FROM trust_work_orders AS w
    JOIN props AS pr ON pr.property_id = w.property_id
    LEFT JOIN homes AS h ON h.unit_id = w.unit_id
    WHERE w.pmc_id = %(pmc)s
)
SELECT 'move-in-' || l.id, l.starts_on, 'Move-in: ' || h.home, NULL::text, NULL::timestamptz
FROM trust_leases AS l JOIN homes AS h ON h.unit_id = l.unit_id
WHERE l.starts_on BETWEEN %(from)s AND %(to)s
UNION ALL
SELECT 'lease-end-' || l.id, l.ends_on, 'Lease ends: ' || h.home, NULL, NULL
FROM trust_leases AS l JOIN homes AS h ON h.unit_id = l.unit_id
WHERE l.ends_on BETWEEN %(from)s AND %(to)s
UNION ALL
SELECT 'visit-' || s.id, s.scheduled_for, 'Visit: ' || o.summary,
       o.home || coalesce(', ' || v.display_name, '')
           || coalesce('. Window: ' || s.visit_window, ''),
       NULL
FROM trust_work_order_steps AS s
JOIN orders AS o ON o.id = s.work_order_id
LEFT JOIN LATERAL (
    SELECT a.vendor_id FROM trust_work_order_steps AS a
    WHERE a.work_order_id = s.work_order_id AND a.step = 'assigned' AND a.taken_at <= s.taken_at
    ORDER BY a.taken_at DESC, a.created_at DESC LIMIT 1
) AS who ON true
LEFT JOIN trust_vendors AS v ON v.id = who.vendor_id
WHERE s.step = 'scheduled' AND s.scheduled_for BETWEEN %(from)s AND %(to)s
UNION ALL
SELECT 'work-order-due-' || o.id, d.due_on, 'Work order due: ' || o.summary, o.home, NULL
FROM orders AS o
JOIN LATERAL (
    SELECT x.due_on FROM trust_work_order_details AS x WHERE x.work_order_id = o.id
    ORDER BY x.created_at DESC, x.id DESC LIMIT 1
) AS d ON true
WHERE d.due_on BETWEEN %(from)s AND %(to)s
  AND NOT EXISTS (
      SELECT 1 FROM trust_work_order_steps AS c
      WHERE c.work_order_id = o.id AND c.step IN ('completed', 'cancelled')
  )
UNION ALL
SELECT 'showing-' || s.id, (s.starts_at AT TIME ZONE 'UTC')::date, 'Showing: ' || h.home, NULL,
       s.starts_at
FROM trust_showings AS s JOIN homes AS h ON h.unit_id = s.unit_id
WHERE s.starts_at >= %(from)s::timestamp AT TIME ZONE 'UTC'
  AND s.starts_at < (%(to)s + 1)::timestamp AT TIME ZONE 'UTC'
UNION ALL
SELECT 'bill-due-' || b.id, b.due_on,
       'Bill due: ' || v.display_name || ' ' || to_char(b.amount, 'FM$999,999,990.00'),
       pr.home || coalesce('. ' || nullif(b.memo, ''), ''), NULL
FROM trust_bills AS b
JOIN trust_vendors AS v ON v.id = b.vendor_id
JOIN trust_ledger_accounts AS a ON a.ledger_account_id = b.ledger_account_id
JOIN props AS pr ON pr.property_id = a.property_id
WHERE b.pmc_id = %(pmc)s AND b.due_on BETWEEN %(from)s AND %(to)s
  -- Not paid in full: a bill may be paid in parts (ADR 0010).
  AND (SELECT coalesce(sum(t.amount), 0) FROM trust_bill_payments AS p
         JOIN pgledger_transfers AS t ON t.id = p.transfer_id
         WHERE p.bill_id = b.id AND p.step = 'paid') < b.amount
  AND NOT EXISTS (SELECT 1 FROM trust_bill_declines AS x WHERE x.bill_id = b.id)
UNION ALL
SELECT 'rent-change-' || c.id, c.effective_on,
       'Rent changes: ' || h.home || ' to ' || to_char(c.monthly_rent, 'FM$999,999,990.00'), NULL,
       NULL
FROM trust_rent_changes AS c
JOIN trust_leases AS l ON l.id = c.lease_id
JOIN homes AS h ON h.unit_id = l.unit_id
WHERE c.pmc_id = %(pmc)s AND c.effective_on BETWEEN %(from)s AND %(to)s
UNION ALL
SELECT 'renters-insurance-' || r.id, r.expires_on, 'Renters insurance ends: ' || h.home, NULL, NULL
FROM trust_renters_insurance AS r
JOIN trust_leases AS l ON l.id = r.lease_id
JOIN homes AS h ON h.unit_id = l.unit_id
WHERE r.pmc_id = %(pmc)s AND r.expires_on BETWEEN %(from)s AND %(to)s
UNION ALL
SELECT 'vendor-insurance-' || i.id, i.expires_on,
       'Insurance ends: ' || v.display_name || ' (' || i.coverage || ')', NULL, NULL
FROM trust_vendor_insurance AS i JOIN trust_vendors AS v ON v.id = i.vendor_id
WHERE i.pmc_id = %(pmc)s AND i.expires_on BETWEEN %(from)s AND %(to)s
"""


def staff_events(
    conn: psycopg.Connection[Any], pmc_id: UUID, group: UUID | None, today: date
) -> list[Event]:
    found = [
        Event(uid=f"{uid}@occupella-pms", day=day, summary=summary, description=about, at=at)
        for uid, day, summary, about, at in conn.execute(
            STAFF_EVENTS,
            {"pmc": pmc_id, "group": group, "from": today - BACK, "to": today + AHEAD},
        ).fetchall()
    ]
    for held in payables.held_fees(conn, pmc_id, today):
        if held.take_by is not None:
            found.append(
                Event(
                    uid=f"company-fees-{held.bank_account_id}-{held.since}@occupella-pms",
                    day=held.take_by,
                    summary=f"Take the company's fees out of {held.bank_account}",
                    description=f"{dollars(held.held)} held since {held.since:%m/%d/%Y}: earned"
                    " fees left in trust are commingling.",
                )
            )
    return sorted(found, key=lambda e: (e.day, e.summary, e.uid))


# --- a tenant's feed -----------------------------------------------------------------------------

TENANT_EVENTS = """
WITH mine AS (
    SELECT l.id, l.starts_on, l.ends_on, p.display_name || ', ' || u.display_name AS home
    FROM trust_lease_tenants AS lt
    JOIN trust_leases AS l ON l.id = lt.lease_id
    JOIN trust_units AS u ON u.id = l.unit_id
    JOIN trust_properties AS p ON p.id = u.property_id
    WHERE lt.pmc_id = %(pmc)s
      AND lt.tenant_id IN (SELECT trust_tenant_records(%(pmc)s, %(tenant)s))
      AND NOT EXISTS (
          SELECT 1 FROM trust_lease_departures AS d
          WHERE d.lease_id = l.id AND d.tenant_id = lt.tenant_id AND d.left_on < %(from)s
      )
)
SELECT 'rent-due-' || m.id || '-' || to_char(due.on_day, 'YYYY-MM'), due.on_day,
       'Rent due: ' || m.home, NULL::text, NULL::timestamptz
FROM mine AS m
CROSS JOIN LATERAL generate_series(
    date_trunc('month', %(from)s::timestamp), %(to)s::timestamp, interval '1 month'
) AS month
CROSS JOIN LATERAL (SELECT trust_rent_due_on(m.id, month::date) AS on_day) AS due
WHERE due.on_day BETWEEN %(from)s AND %(to)s
UNION ALL
SELECT 'lease-end-' || m.id, m.ends_on, 'Your lease ends: ' || m.home, NULL, NULL
FROM mine AS m
WHERE m.ends_on BETWEEN %(from)s AND %(to)s
UNION ALL
SELECT 'visit-' || s.id, s.scheduled_for, 'Repair visit: ' || w.summary,
       m.home || coalesce('. Window: ' || s.visit_window, ''), NULL
FROM trust_maintenance_requests AS r
JOIN mine AS m ON m.id = r.lease_id
JOIN trust_maintenance_request_work_orders AS rw ON rw.request_id = r.id
JOIN trust_work_orders AS w ON w.id = rw.work_order_id
JOIN trust_work_order_steps AS s ON s.work_order_id = w.id
WHERE s.step = 'scheduled' AND s.scheduled_for BETWEEN %(from)s AND %(to)s
"""


def tenant_events(
    conn: psycopg.Connection[Any], pmc_id: UUID, tenant_id: UUID, today: date
) -> list[Event]:
    return sorted(
        (
            Event(uid=f"{uid}@occupella-pms", day=day, summary=summary, description=about, at=at)
            for uid, day, summary, about, at in conn.execute(
                TENANT_EVENTS,
                {
                    "pmc": pmc_id,
                    "tenant": tenant_id,
                    "from": today - BACK,
                    "to": today + AHEAD,
                },
            ).fetchall()
        ),
        key=lambda e: (e.day, e.summary, e.uid),
    )


# --- the feed itself ------------------------------------------------------------------------------

FEED = """
SELECT f.pmc_id, f.staff_id, f.tenant_id, f.group_id, n.display_name
FROM trust_calendar_feeds AS f
JOIN trust_pmcs AS n ON n.pmc_id = f.pmc_id
WHERE f.token_sha256 = %s
  AND NOT EXISTS (SELECT 1 FROM trust_calendar_feed_revocations AS r WHERE r.feed_id = f.id)
  AND EXISTS (
      SELECT 1 FROM trust_logins AS g
      WHERE g.pmc_id = f.pmc_id AND g.disabled_at IS NULL
        AND (g.staff_id = f.staff_id OR g.tenant_id = f.tenant_id)
  )
"""


@router.get("/api/calendar/{token}.ics")
def feed(conn: Conn, token: str) -> Response:
    """The feed a calendar app fetches: no login, the token in the link. A link taken back, or
    whose person can no longer sign in, reads as not found."""
    row = conn.execute(FEED, (accounts.digest(token),)).fetchone()
    if row is None:
        raise HTTPException(404, "no such calendar")
    pmc_id, staff_id, tenant_id, group, company = row
    today = datetime.now(UTC).date()
    if staff_id is not None:
        events = staff_events(conn, pmc_id, group, today)
    else:
        events = tenant_events(conn, pmc_id, tenant_id, today)
    return Response(
        ics(company, events, datetime.now(UTC)),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'inline; filename="calendar.ics"'},
    )


class FeedState(BaseModel):
    # When the standing link was made, and the property group it shows; None when there is none.
    made_at: datetime | None
    group_id: UUID | None


class NewFeed(BaseModel):
    # A staff member's feed may show one property group's properties only.
    group_id: UUID | None = None


class FeedLink(BaseModel):
    # Shown once: the link to subscribe a calendar app to.
    url: str


def standing(
    conn: psycopg.Connection[Any], pmc_id: UUID, column: str, person: UUID
) -> tuple[UUID, datetime, UUID | None] | None:
    row = conn.execute(
        "SELECT f.id, f.created_at, f.group_id FROM trust_calendar_feeds AS f"  # noqa: S608
        f" WHERE f.pmc_id = %s AND f.{column} = %s"
        " AND NOT EXISTS (SELECT 1 FROM trust_calendar_feed_revocations AS r"
        " WHERE r.feed_id = f.id)"
        " ORDER BY f.created_at DESC, f.id DESC LIMIT 1",
        (pmc_id, person),
    ).fetchone()
    return None if row is None else (row[0], row[1], row[2])


def state(conn: psycopg.Connection[Any], pmc_id: UUID, column: str, person: UUID) -> FeedState:
    found = standing(conn, pmc_id, column, person)
    return FeedState(
        made_at=None if found is None else found[1], group_id=None if found is None else found[2]
    )


def revoke(conn: psycopg.Connection[Any], pmc_id: UUID, column: str, person: UUID) -> bool:
    """Takes back every standing feed of the person; whether there was one. One person's links
    wait for each other, so two at once leave one standing and take none back twice."""
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('trust calendar feed ' || %s, 0))",
        (str(person),),
    )
    rows = conn.execute(
        "INSERT INTO trust_calendar_feed_revocations (pmc_id, feed_id)"  # noqa: S608
        " SELECT f.pmc_id, f.id FROM trust_calendar_feeds AS f"
        f" WHERE f.pmc_id = %s AND f.{column} = %s"
        " AND NOT EXISTS (SELECT 1 FROM trust_calendar_feed_revocations AS r"
        " WHERE r.feed_id = f.id) RETURNING feed_id",
        (pmc_id, person),
    ).fetchall()
    return bool(rows)


def make(
    conn: psycopg.Connection[Any],
    request: Request,
    pmc_id: UUID,
    column: str,
    person: UUID,
    group: UUID | None,
) -> FeedLink:
    revoke(conn, pmc_id, column, person)
    token = secrets.token_urlsafe(32)
    try:
        conn.execute(
            f"INSERT INTO trust_calendar_feeds (pmc_id, {column}, group_id, token_sha256)"  # noqa: S608
            " VALUES (%s, %s, %s, %s)",
            (pmc_id, person, group, accounts.digest(token)),
        )
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(404, "no such property group") from None
    return FeedLink(url=f"{public_url(request)}/api/calendar/{token}.ics")


# --- staff ----------------------------------------------------------------------------------------


@router.get("/api/calendar/mine")
def my_feed(session: Staff, conn: Conn) -> FeedState:
    """Whether the staff member has a calendar link standing, since when and for which group."""
    return state(conn, session.pmc_id, "staff_id", session.person_id)


@router.post("/api/calendar/mine", status_code=201)
def make_my_feed(request: Request, session: Staff, conn: Conn, new: NewFeed) -> FeedLink:
    """A new calendar link for the staff member, shown once; it takes back the one before."""
    return make(conn, request, session.pmc_id, "staff_id", session.person_id, new.group_id)


@router.post("/api/calendar/mine/revoke", status_code=204)
def take_back_my_feed(session: Staff, conn: Conn) -> None:
    """Takes back the staff member's calendar link: the calendar stops updating."""
    if not revoke(conn, session.pmc_id, "staff_id", session.person_id):
        raise HTTPException(409, "you have no calendar link to take back")


# --- tenants --------------------------------------------------------------------------------------


@router.get("/api/tenant/calendar")
def tenant_feed(session: Tenant, conn: Conn) -> FeedState:
    return state(conn, session.pmc_id, "tenant_id", session.person_id)


@router.post("/api/tenant/calendar", status_code=201)
def make_tenant_feed(request: Request, session: Tenant, conn: Conn) -> FeedLink:
    """A new calendar link for the tenant, shown once; it takes back the one before."""
    return make(conn, request, session.pmc_id, "tenant_id", session.person_id, None)


@router.post("/api/tenant/calendar/revoke", status_code=204)
def take_back_tenant_feed(session: Tenant, conn: Conn) -> None:
    if not revoke(conn, session.pmc_id, "tenant_id", session.person_id):
        raise HTTPException(409, "you have no calendar link to take back")
