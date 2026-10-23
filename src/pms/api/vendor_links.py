"""A vendor's private link (migration 0086; the founder's review, q069): vendors have no login.
Staff make a vendor a link from the vendor's page (a newer one replaces the older) and take it
back; it is shown once, and kept only as its SHA-256, like an invite's.

With the link, a vendor sees the work orders assigned to them that are still open (where, what,
priority, the day due, entry, the visit) and schedules a visit (a day and maybe a window) or says
the work is done, with a note. Each such step names the link (trust_work_order_steps
.vendor_link_id): a trigger lets it through only for the vendor last assigned the work order,
while the link stands. The token travels in the JSON body, never in a URL the server logs; a
token that matches no standing link reads as not found. Nothing here moves money, and the AI role
reads none of it.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, StringConstraints

from pms import accounts, notices, texts
from pms.api.deps import Conn, Staff, refusal
from pms.api.leasing import new_token, public_url
from pms.api.work_orders import WORK_ORDERS, Priority, query, rows_of

staff_router = APIRouter(prefix="/api/vendors")
router = APIRouter(prefix="/api/vendor-link")

Token = Annotated[str, Field(min_length=20, max_length=100)]
NOT_VALID = "this link was taken back or doesn't exist"


# --- staff --------------------------------------------------------------------------------------


class LinkState(BaseModel):
    # When the standing link was made, or None when the vendor has none.
    made_at: str | None


class NewLink(BaseModel):
    # Shown once: the vendor's link with its token.
    url: str


def vendor_of(conn: psycopg.Connection[Any], pmc_id: UUID, vendor_id: UUID) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM trust_vendors WHERE pmc_id = %s AND id = %s", (pmc_id, vendor_id)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such vendor")


STANDING = """
SELECT l.id, l.created_at FROM trust_vendor_links AS l
WHERE l.pmc_id = %s AND l.vendor_id = %s
  AND NOT EXISTS (SELECT 1 FROM trust_vendor_link_revocations AS r WHERE r.link_id = l.id)
ORDER BY l.created_at, l.id
"""


def revoke_all(conn: psycopg.Connection[Any], pmc_id: UUID, vendor_id: UUID) -> int:
    """Takes back every standing link of the vendor; how many. One vendor's links wait for each
    other, so two staff at once leave one link standing and take none back twice."""
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('trust vendor link ' || %s, 0))",
        (str(vendor_id),),
    )
    standing = conn.execute(STANDING, (pmc_id, vendor_id)).fetchall()
    for link, _ in standing:
        conn.execute(
            "INSERT INTO trust_vendor_link_revocations (link_id, pmc_id) VALUES (%s, %s)",
            (link, pmc_id),
        )
    return len(standing)


@staff_router.get("/{vendor_id}/link")
def link_state(session: Staff, conn: Conn, vendor_id: UUID) -> LinkState:
    """Whether the vendor has a standing link, and since when (never the link itself)."""
    vendor_of(conn, session.pmc_id, vendor_id)
    standing = conn.execute(STANDING, (session.pmc_id, vendor_id)).fetchall()
    return LinkState(made_at=standing[-1][1].isoformat() if standing else None)


@staff_router.post("/{vendor_id}/link", status_code=201)
def make_link(session: Staff, conn: Conn, request: Request, vendor_id: UUID) -> NewLink:
    """A new link for the vendor, replacing any they had; its URL is shown this once."""
    vendor_of(conn, session.pmc_id, vendor_id)
    revoke_all(conn, session.pmc_id, vendor_id)
    token, digest = new_token()
    conn.execute(
        "INSERT INTO trust_vendor_links (pmc_id, vendor_id, token_sha256) VALUES (%s, %s, %s)",
        (session.pmc_id, vendor_id, digest),
    )
    return NewLink(url=f"{public_url(request)}/vendor#{token}")


@staff_router.post("/{vendor_id}/link/revoke", status_code=204)
def revoke_link(session: Staff, conn: Conn, vendor_id: UUID) -> None:
    """Takes the vendor's link back: it opens nothing from now on."""
    vendor_of(conn, session.pmc_id, vendor_id)
    if revoke_all(conn, session.pmc_id, vendor_id) == 0:
        raise HTTPException(409, "this vendor has no link to take back")


# --- the vendor, through their link ---------------------------------------------------------


class Through(BaseModel):
    token: Token


class VendorWorkOrder(BaseModel):
    id: UUID
    number: int
    property: str
    unit: str | None
    summary: str
    description: str | None
    priority: Priority
    due_on: date | None
    entry: str | None
    status: Literal["assigned", "scheduled"]
    visit_on: date | None
    visit_window: str | None


class VendorView(BaseModel):
    vendor: str
    company: str
    work_orders: list[VendorWorkOrder]


Window = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class VendorStep(Through):
    step: Literal["scheduled", "completed"]
    # A schedule names its day, and maybe when in it.
    scheduled_for: date | None = None
    visit_window: Window | None = None
    note: Note | None = None


def standing_link(conn: psycopg.Connection[Any], token: str) -> tuple[UUID, UUID, UUID]:
    """(link, PMC, vendor) of a standing link by its token, or 404."""
    row = conn.execute(
        "SELECT l.id, l.pmc_id, l.vendor_id FROM trust_vendor_links AS l"
        " WHERE l.token_sha256 = %s AND NOT EXISTS ("
        "   SELECT 1 FROM trust_vendor_link_revocations AS r WHERE r.link_id = l.id)",
        (accounts.digest(token),),
    ).fetchone()
    if row is None:
        raise HTTPException(404, NOT_VALID)
    return row[0], row[1], row[2]


def open_orders(
    conn: psycopg.Connection[Any], pmc_id: UUID, vendor: UUID, company: str
) -> list[VendorWorkOrder]:
    """The vendor's open work orders: those whose latest assignment is to them, oldest first,
    each with its entry in the words their email uses."""
    found = conn.execute(
        WORK_ORDERS.format(
            where="a.vendor_id = %(vendor)s"
            " AND coalesce(s.step, 'open') NOT IN ('completed', 'cancelled')"
        ),
        query(pmc=pmc_id, vendor=vendor, per_page=500, offset=0),
    ).fetchall()
    return sorted(
        (
            VendorWorkOrder(
                id=row.id,
                number=row.number,
                property=row.property.name,
                unit=None if row.unit is None else row.unit.name,
                summary=row.summary,
                description=raw[13],
                priority=row.priority,
                due_on=row.due_on,
                entry=None if raw[14] is None else notices.ENTRY[raw[14]].format(company=company),
                status="scheduled" if row.status == "scheduled" else "assigned",
                visit_on=row.scheduled_for,
                visit_window=row.visit_window,
            )
            for row, raw in zip(rows_of(found), found, strict=True)
        ),
        key=lambda w: w.number,
    )


@router.post("/work-orders")
def vendor_work_orders(conn: Conn, through: Through) -> VendorView:
    """What the link's vendor sees: their open work orders, and whose they are."""
    _, pmc_id, vendor = standing_link(conn, through.token)
    names = conn.execute(
        "SELECT v.display_name, p.display_name FROM trust_vendors AS v"
        " JOIN trust_pmcs AS p ON p.pmc_id = v.pmc_id WHERE v.id = %s",
        (vendor,),
    ).fetchone()
    assert names is not None
    return VendorView(
        vendor=names[0], company=names[1], work_orders=open_orders(conn, pmc_id, vendor, names[1])
    )


@router.post("/work-orders/{work_order_id}/steps", status_code=201)
def vendor_step(conn: Conn, work_order_id: UUID, step: VendorStep) -> VendorView:
    """The vendor schedules a visit or says the work is done, as of now."""
    link, pmc_id, vendor = standing_link(conn, step.token)
    if (step.step == "scheduled") != (step.scheduled_for is not None):
        raise HTTPException(422, "a schedule names the day you come, and only it does")
    if step.visit_window is not None and step.step != "scheduled":
        raise HTTPException(422, "only a schedule says when in the day you come")
    if work_order_id not in {w.id for w in open_orders(conn, pmc_id, vendor, "")}:
        raise HTTPException(404, "no such work order of yours")
    try:
        with conn.transaction():
            made = conn.execute(
                "INSERT INTO trust_work_order_steps (pmc_id, work_order_id, step, scheduled_for,"
                " visit_window, taken_at, note, vendor_link_id)"
                " VALUES (%s, %s, %s, %s, %s, now(), %s, %s) RETURNING id",
                (
                    pmc_id,
                    work_order_id,
                    step.step,
                    step.scheduled_for,
                    step.visit_window,
                    step.note,
                    link,
                ),
            ).fetchone()
    except psycopg.errors.CheckViolation as refused:
        # A step out of order (a schedule after the work is done...).
        raise HTTPException(409, refusal(refused)) from None
    assert made is not None
    if step.step == "scheduled":
        # The visit texted to the home's tenants who agreed (migration 20261023000204).
        texts.write_visit(conn, pmc_id, made[0])
    return vendor_work_orders(conn, Through(token=step.token))
