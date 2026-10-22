"""The tenant portal's endpoints: a tenant's leases with what they owe and what comes due, each
lease's ledger, the statement of a deposit returned at move-out, the lease as signed with its
signing certificate and the lease's documents staff keep (q120), and maintenance requests. No
payments: online payment is out of scope (docs/adr/0002-portals-and-exports.md).

The tenant and the PMC always come from the session. A lease named in the path is answered only
if the tenant is on it; any other reads as not found, whoever's it is. Someone renting at two
properties at once is a record at each (migration 0105, q009): they sign in through their first,
and see and act on the leases of every record of theirs, each lease as the record on it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from pms import accounts, exports, notices
from pms.api import directory, documents, maintenance, offers, outbox, signing
from pms.api import rent as rent_api
from pms.api.deps import Conn, Rows, Tenant, download
from pms.leasing import esign

router = APIRouter(prefix="/api/tenant")
# A tenant files at most this many requests under one lease in a day: enough for a bad day,
# and a ceiling on what a stolen session can pile on staff.
REQUESTS_PER_DAY = 10
PHOTOS_PER_REQUEST = 3


def today() -> date:
    return datetime.now(UTC).date()


class Monthly(BaseModel):
    """What the lease charges every month: its rent from its next due date (and each rent change
    to come after it, until the next), and each recurring fee or credit not stopped (from its
    first due date, until the first one it is no longer charged for)."""

    what: str
    kind: Literal["rent", "fee", "credit"]
    amount: str
    starts_on: date
    ends_on: date | None


class ComingDue(BaseModel):
    """A charge entered already and due after today, with what is still open of it."""

    due_on: date
    kind: Literal["rent", "fee"]
    memo: str | None
    open: str


class RentChangeNotice(BaseModel):
    effective_on: date
    monthly_rent: str
    notice_given_on: date


class LeaseFile(BaseModel):
    kind: Literal["signed lease", "signing certificate", "lease document"]
    title: str
    url: str


class Lease(BaseModel):
    lease_id: UUID
    property: str
    unit: str
    starts_on: date
    ends_on: date | None
    # Money as text, so no float ever rounds a cent. The rent in force today.
    monthly_rent: str
    # The day of the month rent is due this month (migration 0081).
    rent_due_day: int
    # Rent changes to come, each from the first of a month, with the day notice was given.
    rent_changes: list[RentChangeNotice]
    tenants: list[str]
    balance_due: str
    in_effect: bool
    # Nothing for a lease that has ended.
    each_month: list[Monthly]
    coming_due: list[ComingDue]
    # Its tenants' deposits were returned at move-out: the statement can be downloaded.
    deposit_returned: bool
    # The security deposit the signed lease asked, what is held of it and what is still to give
    # the manager; None when it asked none, or once the deposit was returned.
    deposit_asked: rent_api.DepositAsked | None
    # A lease in effect: whether it requires renters insurance, and the policy on file.
    renters_insurance: rent_api.RentersInsurance | None
    # Each payment to it, newest first, each with its receipt to download.
    payments: list[rent_api.Paid]
    # The lease as signed, its signing certificate and the lease's documents, to download.
    files: list[LeaseFile]


class Photo(BaseModel):
    """A photo (or a PDF) filed with a request: a document about it (migration 0084)."""

    title: documents.Title
    content_type: documents.ContentType
    content_base64: str = Field(min_length=4, max_length=7_000_000)


class NewRequest(BaseModel):
    lease_id: UUID
    summary: str = Field(min_length=1, max_length=2000)
    entry_allowed: bool
    # No heat, a leak, no water...: its work order opens urgent, and whoever is on call for
    # emergencies is emailed while the server sends email (migration 0084).
    emergency: bool = False
    photos: list[Photo] = Field(default=[], max_length=PHOTOS_PER_REQUEST)


# The leases of every record of the signed-in person: their own and any linked to it.
MINE = "mine.tenant_id IN (SELECT trust_tenant_records(%(pmc)s, %(person)s))"


def lease_of(conn: psycopg.Connection[Any], session: accounts.Session, lease_id: UUID) -> UUID:
    """The person's record on a lease (theirs, or one at another property linked to it); refuses,
    as not found, a lease they aren't on."""
    row = conn.execute(
        f"SELECT mine.tenant_id FROM trust_lease_tenants AS mine"  # noqa: S608
        f" WHERE mine.pmc_id = %(pmc)s AND mine.lease_id = %(lease)s AND {MINE}",
        {"pmc": session.pmc_id, "lease": lease_id, "person": session.person_id},
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such lease of yours")
    return UUID(str(row[0]))


def still_on(conn: psycopg.Connection[Any], session: accounts.Session, lease_id: UUID) -> UUID:
    """The person's record on a lease, refusing one they left by today while others stayed on it
    (migration 0080): they still read it, but file nothing under it."""
    tenant = lease_of(conn, session, lease_id)
    row = conn.execute(
        "SELECT left_on FROM trust_lease_departures"
        " WHERE pmc_id = %s AND lease_id = %s AND tenant_id = %s AND left_on <= %s",
        (session.pmc_id, lease_id, tenant, today()),
    ).fetchone()
    if row is not None:
        raise HTTPException(409, f"you left this lease on {row[0]:%m/%d/%Y}")
    return tenant


@router.get("/leases")
def leases(session: Tenant, conn: Conn) -> list[Lease]:
    """Every lease the tenant is on, at any property, newest first, with its balance due at the
    end of today."""
    on = today()
    mine = {"on": on, "pmc": session.pmc_id, "person": session.person_id}
    rows = conn.execute(
        "SELECT l.id, p.display_name, u.display_name, l.starts_on, l.ends_on,"  # noqa: S608
        " trust_rent_on(l.id, %(on)s), trust_rent_due_day(l.id, %(on)s),"
        " array(SELECT t.display_name FROM trust_lease_tenants AS everyone"
        "       JOIN trust_tenants AS t ON t.id = everyone.tenant_id"
        '       WHERE everyone.lease_id = l.id ORDER BY t.display_name COLLATE "C", t.id),'
        " (SELECT b.balance FROM trust_report_tenant_ledger(l.pmc_id, l.id, %(on)s) AS b"
        "  WHERE b.item = 'balance'),"
        " EXISTS (SELECT 1 FROM trust_deposit_returns AS r WHERE r.lease_id = l.id)"
        " FROM trust_lease_tenants AS mine"
        " JOIN trust_leases AS l ON l.id = mine.lease_id"
        " JOIN trust_units AS u ON u.id = l.unit_id"
        " JOIN trust_properties AS p ON p.id = u.property_id"
        f" WHERE mine.pmc_id = %(pmc)s AND {MINE}"
        " ORDER BY l.starts_on DESC, l.id",
        mine,
    ).fetchall()
    insured = rent_api.renters_insurance(conn, session.pmc_id, on)
    paid: dict[UUID, list[rent_api.Paid]] = {}
    for lease, transfer, event_at, amount, reference, method, bounced in (
        found
        for row in rows
        for found in conn.execute(
            rent_api.PAYMENTS, {"pmc": session.pmc_id, "lease": row[0]}
        ).fetchall()
    ):
        paid.setdefault(lease, []).append(
            rent_api.Paid(
                transfer_id=transfer,
                received_on=event_at.astimezone(UTC).date(),
                amount=rent_api.cents(amount),
                reference=reference,
                method=method,
                bounced=bounced,
            )
        )
    coming: dict[UUID, list[RentChangeNotice]] = {}
    for lease, effective, monthly_rent, notice in conn.execute(
        "SELECT c.lease_id, c.effective_on, c.monthly_rent, c.notice_given_on"  # noqa: S608
        " FROM trust_rent_changes AS c"
        " JOIN trust_lease_tenants AS mine ON mine.lease_id = c.lease_id"
        f" WHERE mine.pmc_id = %(pmc)s AND {MINE} AND c.effective_on > %(on)s"
        " ORDER BY c.effective_on",
        mine,
    ).fetchall():
        coming.setdefault(lease, []).append(
            RentChangeNotice(
                effective_on=effective,
                monthly_rent=exports.text(monthly_rent),
                notice_given_on=notice,
            )
        )
    files = {row[0]: files_of(conn, session.pmc_id, row[0]) for row in rows}
    asked = {row[0]: rent_api.deposits_asked(conn, session.pmc_id, row[0]) for row in rows}
    return [
        Lease(
            lease_id=lease,
            property=prop,
            unit=unit,
            starts_on=starts,
            ends_on=ends,
            monthly_rent=exports.text(rent),
            rent_due_day=due_day,
            rent_changes=coming.get(lease, []),
            tenants=list(names),
            balance_due=exports.text(balance),
            in_effect=starts <= on and (ends is None or on <= ends),
            each_month=[] if ends is not None and ends < on else monthly(conn, lease),
            coming_due=[
                ComingDue(due_on=due, kind=kind, memo=memo, open=rent_api.cents(left))
                for _, _, due, kind, memo, _, left in rent_api.charges_open(
                    conn, session.pmc_id, lease
                )
                if due > on
            ],
            deposit_returned=returned,
            deposit_asked=asked[lease].get(lease),
            renters_insurance=insured.get(lease),
            payments=paid.get(lease, []),
            files=files[lease],
        )
        for lease, prop, unit, starts, ends, rent, due_day, names, balance, returned in rows
    ]


# The offer that became a lease, and whether it was signed on the PMS's own page.
SIGNED = """
SELECT o.id, o.document_title, r.partner = %s
FROM trust_lease_offer_completions AS c
JOIN trust_lease_offers AS o ON o.id = c.offer_id
JOIN trust_signature_requests AS r ON r.offer_id = o.id
WHERE c.pmc_id = %s AND c.lease_id = %s
"""
LEASE_DOCUMENTS = """
SELECT d.id, d.title FROM trust_documents AS d
WHERE d.pmc_id = %s AND d.lease_id = %s AND d.kind = 'lease_document'
  AND NOT EXISTS (SELECT 1 FROM trust_document_removals AS r WHERE r.document_id = d.id)
ORDER BY d.created_at DESC, d.id
"""


def files_of(conn: psycopg.Connection[Any], pmc_id: UUID, lease: UUID) -> list[LeaseFile]:
    """A lease's files a tenant on it downloads: the lease as signed and, when it was signed on
    the PMS's own page, its signing certificate; then the lease's documents, newest first."""
    base = f"/api/tenant/leases/{lease}"
    found = []
    signed = conn.execute(SIGNED, (esign.PARTNER, pmc_id, lease)).fetchone()
    if signed is not None:
        _, title, here = signed
        found.append(LeaseFile(kind="signed lease", title=title, url=f"{base}/signed-lease"))
        if here:
            found.append(
                LeaseFile(
                    kind="signing certificate",
                    title="Signing certificate",
                    url=f"{base}/signing-certificate",
                )
            )
    found += [
        LeaseFile(kind="lease document", title=title, url=f"{base}/documents/{document}")
        for document, title in conn.execute(LEASE_DOCUMENTS, (pmc_id, lease)).fetchall()
    ]
    return found


def signed_offer(
    conn: psycopg.Connection[Any], session: accounts.Session, lease_id: UUID
) -> tuple[UUID, bool]:
    """The offer that became one of the tenant's leases, and whether it was signed here; 404
    for a lease of someone else or one that came from no offer."""
    lease_of(conn, session, lease_id)
    row = conn.execute(SIGNED, (esign.PARTNER, session.pmc_id, lease_id)).fetchone()
    if row is None:
        raise HTTPException(404, "this lease wasn't signed through the PMS")
    return row[0], row[2]


@router.get("/leases/{lease_id}/signed-lease")
def signed_lease(session: Tenant, conn: Conn, lease_id: UUID) -> Response:
    """The lease as it was sent and signed: the offer's PDF that became this lease."""
    offer, _ = signed_offer(conn, session, lease_id)
    return offers.document_response(conn, offer)


@router.get("/leases/{lease_id}/signing-certificate")
def signing_certificate(session: Tenant, conn: Conn, lease_id: UUID) -> Response:
    """The signing certificate of the lease, when it was signed on the PMS's own page."""
    offer, here = signed_offer(conn, session, lease_id)
    if not here:
        raise HTTPException(404, "this lease was signed elsewhere: it has no certificate here")
    return signing.certificate_pdf(conn, session.pmc_id, offer)


@router.get("/leases/{lease_id}/documents/{document_id}")
def lease_document(session: Tenant, conn: Conn, lease_id: UUID, document_id: UUID) -> Response:
    """One of the lease's documents staff keep on its Files tab; any other reads as not found."""
    lease_of(conn, session, lease_id)
    if (
        conn.execute(
            "SELECT 1 FROM trust_documents WHERE pmc_id = %s AND id = %s AND lease_id = %s"
            " AND kind = 'lease_document'",
            (session.pmc_id, document_id, lease_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such document")
    return documents.served(conn, session.pmc_id, document_id, None)


# The lease's rent from its next due date on (today's or later, its first for a lease to come;
# migration 0081): the rent in force then (migration 0061) from the day it took effect, then each
# change after it. Without a due date to come (the lease ends first), the rent in force today.
RENT_AHEAD = """
WITH next AS (
    SELECT l.id, l.starts_on, coalesce((
        SELECT d.due_on
        FROM (VALUES (trust_rent_due_on(l.id, f.day)),
                     (trust_rent_due_on(l.id, (f.day + interval '1 month')::date))) AS d (due_on)
        WHERE d.due_on >= f.day
        ORDER BY d.due_on
        LIMIT 1
    ), %(on)s) AS due_on
    FROM trust_leases AS l
    CROSS JOIN LATERAL (SELECT greatest(%(on)s, l.starts_on) AS day) AS f
    WHERE l.id = %(lease)s
)
SELECT coalesce((
           SELECT max(c.effective_on) FROM trust_rent_changes AS c
           WHERE c.lease_id = n.id AND c.effective_on <= n.due_on
       ), n.starts_on),
       trust_rent_on(n.id, n.due_on)
FROM next AS n
UNION ALL
SELECT c.effective_on, c.monthly_rent
FROM next AS n
JOIN trust_rent_changes AS c ON c.lease_id = n.id AND c.effective_on > n.due_on
ORDER BY 1
"""


def monthly(conn: psycopg.Connection[Any], lease: UUID) -> list[Monthly]:
    """The lease's rent from its next due date, each rent until the change after it, then each
    recurring fee or credit not stopped, by first due date."""
    rents = conn.execute(RENT_AHEAD, {"lease": lease, "on": today()}).fetchall()
    return [
        Monthly(
            what="Rent",
            kind="rent",
            amount=rent_api.cents(amount),
            starts_on=since,
            ends_on=rents[n + 1][0] if n + 1 < len(rents) else None,
        )
        for n, (since, amount) in enumerate(rents)
    ] + [
        Monthly(what=memo, kind=kind, amount=rent_api.cents(amount), starts_on=first, ends_on=last)
        for memo, kind, amount, first, last in conn.execute(
            "SELECT r.memo, r.kind, r.amount, r.starts_on, e.ends_on"
            " FROM trust_recurring_charges AS r"
            " LEFT JOIN trust_recurring_charge_ends AS e ON e.recurring_charge_id = r.id"
            " WHERE r.lease_id = %s AND (e.ends_on IS NULL OR e.ends_on > %s)"
            ' ORDER BY r.starts_on, r.memo COLLATE "C", r.id',
            (lease, today()),
        ).fetchall()
    ]


def ledger_table(
    conn: psycopg.Connection[Any], session: accounts.Session, lease_id: UUID, as_of: date | None
) -> exports.Table:
    lease_of(conn, session, lease_id)
    through = as_of or today()
    table = exports.run_report(
        conn, "trust_report_tenant_ledger", [session.pmc_id, lease_id, through]
    )
    return exports.Table(f"Tenant ledger through {through.isoformat()}", table.columns, table.rows)


@router.get("/leases/{lease_id}/ledger")
def ledger(
    session: Tenant,
    conn: Conn,
    lease_id: UUID,
    as_of: Annotated[date | None, Query()] = None,
) -> Rows:
    """The lease's charges and payments through the end of a day (UTC), today by default."""
    return Rows.of(ledger_table(conn, session, lease_id, as_of))


@router.get("/leases/{lease_id}/ledger/download")
def ledger_download(
    session: Tenant,
    conn: Conn,
    lease_id: UUID,
    as_of: Annotated[date | None, Query()] = None,
    fmt: Annotated[Literal["csv", "xlsx", "pdf"], Query(alias="format")] = "pdf",
) -> Response:
    table = ledger_table(conn, session, lease_id, as_of)
    name = f"tenant-ledger-{(as_of or today()).isoformat()}"
    return download(exports.export(table, fmt), name, fmt)


@router.get("/leases/{lease_id}/deposit-return/download")
def deposit_return_download(
    session: Tenant,
    conn: Conn,
    lease_id: UUID,
    fmt: Annotated[Literal["csv", "xlsx", "pdf"], Query(alias="format")] = "pdf",
) -> Response:
    """The itemized statement of the lease's deposits returned at move-out: what each tenant
    held, what was kept for what the lease owed, and what went back to each."""
    lease_of(conn, session, lease_id)
    try:
        with conn.transaction():
            table = exports.run_report(
                conn, "trust_report_deposit_return", [session.pmc_id, lease_id]
            )
    except psycopg.errors.InvalidParameterValue:
        raise HTTPException(404, "this lease's deposit has not been returned") from None
    return download(exports.export(table, fmt), "deposit-return", fmt)


@router.get("/contact")
def my_contact(session: Tenant, conn: Conn) -> directory.Contact:
    """The tenant's contact details as the company has them."""
    return directory.contact_of(conn, "tenant_id", session.person_id)


@router.post("/contact", status_code=204)
def change_my_contact(session: Tenant, conn: Conn, body: directory.Contact) -> None:
    """The tenant's own email, phones and mailing address, as they give them (all of them, so a
    field left empty is cleared): the company's emails go to this email from now on, and staff
    see on the tenant's page that the tenant changed them. Their sign-in email stays as it is.
    Renting at more than one property, the details are theirs at each."""
    for (record,) in conn.execute(
        "SELECT trust_tenant_records(%s, %s) ORDER BY 1", (session.pmc_id, session.person_id)
    ).fetchall():
        directory.record_contact(conn, session.pmc_id, "tenant_id", record, body, by_person=True)


@router.get("/requests")
def requests(session: Tenant, conn: Conn) -> list[maintenance.Request]:
    """The maintenance requests filed under any of the tenant's leases, by them or a co-tenant."""
    found = maintenance.requests(
        conn, maintenance.FOR_TENANT, pmc=session.pmc_id, person=session.person_id
    )
    return [maintenance.for_tenant(r) for r in found]


@router.post("/leases/{lease_id}/renters-insurance", status_code=201)
def record_my_policy(
    session: Tenant, conn: Conn, lease_id: UUID, policy: rent_api.NewPolicy
) -> rent_api.Created:
    """The tenant's own renters insurance policy for a lease they are on, with whatever insurer
    they chose; it is recorded as theirs."""
    tenant = still_on(conn, session, lease_id)
    return rent_api.add_policy(conn, session.pmc_id, lease_id, policy, tenant)


@router.post("/requests", status_code=201)
def file_request(session: Tenant, conn: Conn, new: NewRequest) -> maintenance.Request:
    """A request under one of the tenant's leases that is in effect today."""
    summary = new.summary.strip()
    if not summary:
        raise HTTPException(422, "say what needs fixing")
    tenant = still_on(conn, session, new.lease_id)
    on = today()
    row = conn.execute(
        "SELECT starts_on <= %s AND (ends_on IS NULL OR %s <= ends_on) FROM trust_leases"
        " WHERE id = %s",
        (on, on, new.lease_id),
    ).fetchone()
    if row is None or not row[0]:
        raise HTTPException(409, "requests are filed under a lease in effect today")
    recent = conn.execute(
        "SELECT count(*) FROM trust_maintenance_requests"
        " WHERE pmc_id = %s AND lease_id = %s AND submitted_at > %s",
        (session.pmc_id, new.lease_id, datetime.now(UTC) - timedelta(days=1)),
    ).fetchone()
    if recent is not None and recent[0] >= REQUESTS_PER_DAY:
        raise HTTPException(
            429, f"at most {REQUESTS_PER_DAY} requests a day per lease; call the office"
        )
    created = conn.execute(
        "INSERT INTO trust_maintenance_requests"
        " (pmc_id, lease_id, tenant_id, summary, entry_allowed, emergency)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (
            session.pmc_id,
            new.lease_id,
            tenant,
            summary,
            new.entry_allowed,
            new.emergency,
        ),
    ).fetchone()
    assert created is not None
    for photo in new.photos:
        documents.keep(
            conn,
            session.pmc_id,
            documents.NewDocument(kind="request_photo", about_id=created[0], **photo.model_dump()),
        )
    if new.emergency and outbox.SENDING.get():
        notices.write_emergency(conn, session.pmc_id, created[0], outbox.PUBLIC_URL.get())
    return maintenance.for_tenant(maintenance.one(conn, session.pmc_id, created[0]))
