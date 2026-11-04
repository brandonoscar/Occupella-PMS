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
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field, StringConstraints

from pms import accounts, exports, notices
from pms.api import (
    directory,
    documents,
    leaving,
    ledger_sources,
    maintenance,
    moveout,
    offers,
    outbox,
    signing,
)
from pms.api import rent as rent_api
from pms.api.deps import Conn, Rows, Tenant, download, refusal
from pms.homes import home
from pms.leasing import esign

router = APIRouter(prefix="/api/tenant")
# A tenant files at most this many requests under one lease in a day: enough for a bad day,
# and a ceiling on what a stolen session can pile on staff.
REQUESTS_PER_DAY = 10
PHOTOS_PER_REQUEST = 3
# A tenant's own lease documents and notices, at most so many a day per lease (UTC).
UPLOADS_PER_DAY = 10
NOTICES_PER_DAY = 5


def today() -> date:
    return datetime.now(UTC).date()


class Monthly(BaseModel):
    """What the lease charges every month: its rent, and each recurring fee or credit not
    stopped (from its first due date, until the first one it is no longer charged for)."""

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


class NextLine(BaseModel):
    what: str
    kind: Literal["rent", "fee", "credit"]
    amount: str


class NextCharge(BaseModel):
    """The next month's charges not entered yet (the founder's review of 11/03, item 10): the rent
    in force on its due day, a rent change to come included, and each monthly fee or credit, for
    the days the lease is in effect then, as the rent run will charge them."""

    due_on: date
    total: str
    lines: list[NextLine]


# The first due day after today, within the next three months, whose month's rent is not charged
# yet, and what the rent run will charge on it (trust_rent_for_month, as trust_charge_rent_due and
# trust_charge_recurring_due count a month's days).
NEXT_CHARGE = """
WITH months AS (
    SELECT l.id, l.starts_on, l.ends_on,
           trust_rent_due_on(
               l.id, (date_trunc('month', %(on)s::timestamp) + make_interval(months => n))::date
           ) AS due
    FROM trust_leases AS l
    CROSS JOIN generate_series(0, 2) AS n
    WHERE l.id = %(lease)s
),
next AS (
    SELECT m.* FROM months AS m
    WHERE m.due > %(on)s
      AND NOT EXISTS (
          SELECT 1 FROM trust_charges AS c
          WHERE c.lease_id = m.id AND c.kind = 'rent'
            AND date_trunc('month', c.due_on::timestamp) = date_trunc('month', m.due::timestamp)
      )
    ORDER BY m.due
    LIMIT 1
)
SELECT x.due, x.kind, x.what, x.amount FROM (
    SELECT n.due, 'rent' AS kind, 'Rent' AS what, p.amount, 0 AS step
    FROM next AS n
    CROSS JOIN LATERAL trust_rent_for_month(
        n.starts_on, n.ends_on, trust_rent_on(n.id, n.due), n.due
    ) AS p
    UNION ALL
    SELECT n.due, r.kind, r.memo, p.amount, 1
    FROM next AS n
    JOIN trust_recurring_charges AS r ON r.lease_id = n.id
    LEFT JOIN trust_recurring_charge_ends AS e ON e.recurring_charge_id = r.id
    CROSS JOIN LATERAL trust_rent_for_month(n.starts_on, n.ends_on, r.amount, n.due) AS p
    WHERE r.starts_on <= n.due AND (e.ends_on IS NULL OR n.due < e.ends_on) AND p.amount > 0
) AS x
ORDER BY x.step, x.what COLLATE "C"
"""


# The security deposits held for each lease's tenants (a pet or key deposit is its own), as
# rent_api.DEPOSITS_ASKED counts them.
DEPOSIT_HELD = """
SELECT lt.lease_id, sum(b.balance)
FROM trust_lease_tenants AS lt
JOIN trust_ledger_accounts AS a
  ON a.tenant_id = lt.tenant_id AND a.kind = 'tenant_deposit'
 AND coalesce(a.deposit_kind, 'security') = 'security'
JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
WHERE lt.lease_id = ANY(%s)
GROUP BY lt.lease_id
"""


def next_charge(conn: psycopg.Connection[Any], lease_id: UUID, on: date) -> NextCharge | None:
    lines = conn.execute(NEXT_CHARGE, {"lease": lease_id, "on": on}).fetchall()
    if not lines:
        return None
    total = sum(
        (-Decimal(amount) if kind == "credit" else Decimal(amount) for _, kind, _, amount in lines),
        Decimal(0),
    )
    return NextCharge(
        due_on=lines[0][0],
        total=rent_api.cents(total),
        lines=[
            NextLine(what=what, kind=kind, amount=rent_api.cents(amount))
            for _, kind, what, amount in lines
        ],
    )


class RentChangeNotice(BaseModel):
    effective_on: date
    monthly_rent: str
    notice_given_on: date


class LeaseFile(BaseModel):
    kind: Literal["signed lease", "signing certificate", "lease document"]
    title: str
    url: str
    # A document the tenant (or a co-tenant) filed themselves from the portal.
    from_tenant: bool = False


class NewMoveOutNotice(BaseModel):
    leaving_on: date
    note: str | None = Field(default=None, max_length=1000)


class NewLeaseDocument(BaseModel):
    title: documents.Title
    content_type: documents.ContentType
    content_base64: str = Field(min_length=4, max_length=7_000_000)


class Lease(BaseModel):
    lease_id: UUID
    property: str
    unit: str
    # The home as the tenant reads it: "Elm Court, Unit 4", or "Cedar House" when the unit's name
    # adds nothing (src/pms/homes.py).
    home: str
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
    # The next charge not entered yet; None when the lease has none to come.
    next_charge: NextCharge | None
    coming_due: list[ComingDue]
    # Its tenants' deposits were returned at move-out: the statement can be downloaded.
    deposit_returned: bool
    # The security deposit the signed lease asked, what is held of it and what is still to give
    # the manager; None when it asked none, or once the deposit was returned.
    deposit_asked: rent_api.DepositAsked | None
    # The security deposit held for the lease's tenants (the founder's review of 11/03, item 11);
    # None when none is held (none was recorded, or it was returned).
    deposit_held: str | None
    # A lease in effect: whether it requires renters insurance, and the policy on file.
    renters_insurance: rent_api.RentersInsurance | None
    # Each payment to it, newest first, each with its receipt to download.
    payments: list[rent_api.Paid]
    # The lease as signed, its signing certificate and the lease's documents, to download.
    files: list[LeaseFile]
    # The latest notice to move out a tenant on it gave, if any.
    move_out_notice: leaving.MoveOutNotice | None = None


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
    # What kind of repair it is and where in the unit (fix-list item 12); the portal asks both.
    category: maintenance.Category | None = None
    location: (
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[^\r\n]*$"
            ),
        ]
        | None
    ) = None


class NewRequestNote(BaseModel):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


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
        raise HTTPException(409, f"You left this lease on {row[0]:%m/%d/%Y}.")
    return tenant


@router.get("/leases")
def leases(session: Tenant, conn: Conn) -> list[Lease]:
    """Every lease the tenant is on, at any property, newest first, with its balance due at the
    end of today."""
    on = today()
    mine = {"on": on, "pmc": session.pmc_id, "person": session.person_id}
    rows = conn.execute(
        "SELECT l.id, p.display_name, u.display_name, trust_unit_shown(u.id),"  # noqa: S608
        " l.starts_on, l.ends_on,"
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
    # What staff wrote about a payment and its files stay with staff.
    for lease, transfer, event_at, amount, reference, method, bounced, _note, _files in (
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
    notices_given = {row[0]: leaving.notice_of(conn, session.pmc_id, row[0]) for row in rows}
    asked = {row[0]: rent_api.deposits_asked(conn, session.pmc_id, row[0]) for row in rows}
    held_deposits = {
        lease: rent_api.cents(held)
        for lease, held in conn.execute(DEPOSIT_HELD, ([row[0] for row in rows],)).fetchall()
        if held > 0
    }
    return [
        Lease(
            lease_id=lease,
            property=prop,
            unit=unit,
            home=home(prop, shown),
            starts_on=starts,
            ends_on=ends,
            monthly_rent=exports.text(rent),
            rent_due_day=due_day,
            rent_changes=coming.get(lease, []),
            tenants=list(names),
            balance_due=exports.text(balance),
            in_effect=starts <= on and (ends is None or on <= ends),
            each_month=[] if ends is not None and ends < on else monthly(conn, lease, starts, rent),
            next_charge=next_charge(conn, lease, on),
            coming_due=[
                ComingDue(due_on=due, kind=kind, memo=memo, open=rent_api.cents(left))
                for _, _, due, kind, memo, _, left in rent_api.charges_open(
                    conn, session.pmc_id, lease
                )
                if due > on
            ],
            deposit_returned=returned,
            deposit_asked=asked[lease].get(lease),
            deposit_held=held_deposits.get(lease),
            renters_insurance=insured.get(lease),
            payments=paid.get(lease, []),
            files=files[lease],
            move_out_notice=notices_given[lease],
        )
        for lease, prop, unit, shown, starts, ends, rent, due_day, names, balance, returned in rows
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
SELECT d.id, d.title, d.uploaded_by_tenant IS NOT NULL FROM trust_documents AS d
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
        LeaseFile(
            kind="lease document",
            title=title,
            url=f"{base}/documents/{document}",
            from_tenant=mine,
        )
        for document, title, mine in conn.execute(LEASE_DOCUMENTS, (pmc_id, lease)).fetchall()
    ]
    return found


def given_today(conn: psycopg.Connection[Any], table: str, column: str, lease_id: UUID) -> int:
    row = conn.execute(
        f"SELECT count(*) FROM {table} WHERE lease_id = %s AND {column} IS NOT NULL"  # noqa: S608
        " AND (created_at AT TIME ZONE 'UTC')::date = (now() AT TIME ZONE 'UTC')::date",
        (lease_id,),
    ).fetchone()
    return int(row[0]) if row else 0


@router.post("/leases/{lease_id}/move-out-notice", status_code=201)
def give_notice(
    session: Tenant, conn: Conn, lease_id: UUID, new: NewMoveOutNotice
) -> leaving.MoveOutNotice:
    """A tenant on a lease tells the company the last day they will live there; a new notice
    replaces the one before. Staff see it on the lease and with the move-outs, and end the lease
    themselves: the notice ends nothing and moves no money."""
    tenant = still_on(conn, session, lease_id)
    note = None if new.note is None or not new.note.strip() else new.note.strip()
    if note is not None and "\x00" in note:
        raise HTTPException(422, "Your note has characters that can't be saved.")
    if given_today(conn, "trust_move_out_notices", "tenant_id", lease_id) >= NOTICES_PER_DAY:
        raise HTTPException(
            429, f"You can give notice {NOTICES_PER_DAY} times a day. Try again tomorrow."
        )
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_move_out_notices (pmc_id, lease_id, tenant_id, leaving_on, note)"
                " VALUES (%s, %s, %s, %s, %s)",
                (session.pmc_id, lease_id, tenant, new.leaving_on, note),
            )
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(422, refusal(refused)) from None
    found = leaving.notice_of(conn, session.pmc_id, lease_id)
    assert found is not None
    return found


@router.post("/leases/{lease_id}/documents", status_code=201)
def file_document(session: Tenant, conn: Conn, lease_id: UUID, new: NewLeaseDocument) -> LeaseFile:
    """A tenant on a lease files a document of it (an insurance certificate, a pet's records):
    a PDF, JPEG or PNG of at most 5 MB, which staff see with the lease's files, named as the
    tenant's. It can't be taken back from the portal: staff remove it."""
    tenant = still_on(conn, session, lease_id)
    if given_today(conn, "trust_documents", "uploaded_by_tenant", lease_id) >= UPLOADS_PER_DAY:
        raise HTTPException(429, f"You can send {UPLOADS_PER_DAY} files a day. Try again tomorrow.")
    made = documents.keep(
        conn,
        session.pmc_id,
        documents.NewDocument(kind="lease_document", about_id=lease_id, **new.model_dump()),
        uploaded_by_tenant=tenant,
    )
    return LeaseFile(
        kind="lease document",
        title=new.title,
        url=f"/api/tenant/leases/{lease_id}/documents/{made}",
        from_tenant=True,
    )


def signed_offer(
    conn: psycopg.Connection[Any], session: accounts.Session, lease_id: UUID
) -> tuple[UUID, bool]:
    """The offer that became one of the tenant's leases, and whether it was signed here; 404
    for a lease of someone else or one that came from no offer."""
    lease_of(conn, session, lease_id)
    row = conn.execute(SIGNED, (esign.PARTNER, session.pmc_id, lease_id)).fetchone()
    if row is None:
        raise HTTPException(404, "This lease wasn't signed online.")
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
        raise HTTPException(
            404, "This lease wasn't signed online, so it has no signing certificate."
        )
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


def monthly(conn: psycopg.Connection[Any], lease: UUID, starts: date, rent: Any) -> list[Monthly]:
    """The lease's rent, then each recurring fee or credit not stopped, by first due date."""
    return [
        Monthly(
            what="Rent", kind="rent", amount=rent_api.cents(rent), starts_on=starts, ends_on=None
        )
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
) -> ledger_sources.LedgerRows:
    """The lease's charges and payments through the end of a day (UTC), today by default, each
    line with the charge, payment or returned payment it shows, which opens its source."""
    rows = Rows.of(ledger_table(conn, session, lease_id, as_of))
    return ledger_sources.ledger_rows(conn, session.pmc_id, lease_id, rows, as_of or today())


@router.get("/leases/{lease_id}/ledger/sources/{kind}/{source_id}")
def ledger_source(
    session: Tenant,
    conn: Conn,
    lease_id: UUID,
    kind: ledger_sources.LedgerKind,
    source_id: Annotated[str, Path(max_length=64)],
) -> ledger_sources.Source:
    """Where a line of the tenant's ledger came from: a charge, or a payment and its receipt. A
    line of a lease they aren't on reads as not found."""
    lease_of(conn, session, lease_id)
    return ledger_sources.tenant_source(conn, session.pmc_id, lease_id, kind, source_id)


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


@router.get("/leases/{lease_id}/deposit-statement")
def deposit_statement_letter(session: Tenant, conn: Conn, lease_id: UUID) -> Response:
    """Their own deposit statement once the deposit was returned (migration 0120): a letter with
    what each tenant held, what was kept for what the lease owed and why, what went back to
    each, and the receipts and move-out photos it names; the copy staff mail or email them."""
    tenant = lease_of(conn, session, lease_id)
    return Response(
        moveout.statement_pdf(conn, session.pmc_id, lease_id, tenant),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="security-deposit-statement.pdf"'},
    )


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
        raise HTTPException(422, "Tell us what needs fixing.")
    tenant = still_on(conn, session, new.lease_id)
    on = today()
    row = conn.execute(
        "SELECT starts_on <= %s AND (ends_on IS NULL OR %s <= ends_on) FROM trust_leases"
        " WHERE id = %s",
        (on, on, new.lease_id),
    ).fetchone()
    if row is None or not row[0]:
        raise HTTPException(409, "You can send requests only for your current lease.")
    recent = conn.execute(
        "SELECT count(*) FROM trust_maintenance_requests"
        " WHERE pmc_id = %s AND lease_id = %s AND submitted_at > %s",
        (session.pmc_id, new.lease_id, datetime.now(UTC) - timedelta(days=1)),
    ).fetchone()
    if recent is not None and recent[0] >= REQUESTS_PER_DAY:
        raise HTTPException(
            429,
            f"You can send {REQUESTS_PER_DAY} requests a day. If it can't wait, call your manager.",
        )
    created = conn.execute(
        "INSERT INTO trust_maintenance_requests"
        " (pmc_id, lease_id, tenant_id, summary, entry_allowed, emergency, category, location)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            session.pmc_id,
            new.lease_id,
            tenant,
            summary,
            new.entry_allowed,
            new.emergency,
            new.category,
            new.location,
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


NOTES_PER_DAY = 20


def my_request(
    conn: psycopg.Connection[Any], session: accounts.Session, request_id: UUID
) -> tuple[maintenance.StaffRequest, UUID]:
    """A request under a lease the tenant is still on, and their record on it; any other reads
    as not found."""
    found = maintenance.requests(
        conn, maintenance.FOR_TENANT, pmc=session.pmc_id, person=session.person_id
    )
    request = next((r for r in found if r.id == request_id), None)
    if request is None:
        raise HTTPException(404, "no such request of yours")
    return request, still_on(conn, session, request.lease_id)


@router.post("/requests/{request_id}/cancel")
def cancel_request(session: Tenant, conn: Conn, request_id: UUID) -> maintenance.Request:
    """Take a request back while it is still received (no work order yet), once."""
    request, tenant = my_request(conn, session, request_id)
    if request.taken_back:
        return maintenance.for_tenant(request)  # taken back already: sent again, nothing new
    if request.status != "received":
        raise HTTPException(
            409, "It's being worked on, so it can't be cancelled. Send a note instead."
        )
    try:
        conn.execute(
            "INSERT INTO trust_maintenance_request_cancellations"
            " (pmc_id, request_id, lease_id, tenant_id) VALUES (%s, %s, %s, %s)",
            (session.pmc_id, request_id, request.lease_id, tenant),
        )
    except psycopg.errors.CheckViolation:
        # Staff opened its work order at the same moment.
        raise HTTPException(
            409, "It's being worked on, so it can't be cancelled. Send a note instead."
        ) from None
    return maintenance.for_tenant(maintenance.one(conn, session.pmc_id, request_id))


@router.post("/requests/{request_id}/notes", status_code=201)
def add_request_note(
    session: Tenant, conn: Conn, request_id: UUID, note: NewRequestNote
) -> maintenance.Request:
    """A note on an open request (not cancelled, its work order not closed), 20 a day at most."""
    request, tenant = my_request(conn, session, request_id)
    if request.status in maintenance.CLOSED:
        raise HTTPException(409, "This request is closed. Send a new one if you still need help.")
    recent = conn.execute(
        "SELECT count(*) FROM trust_maintenance_request_notes"
        " WHERE pmc_id = %s AND lease_id = %s AND created_at > %s",
        (session.pmc_id, request.lease_id, datetime.now(UTC) - timedelta(days=1)),
    ).fetchone()
    if recent is not None and recent[0] >= NOTES_PER_DAY:
        raise HTTPException(
            429, f"You can add {NOTES_PER_DAY} notes a day. If it can't wait, call your manager."
        )
    try:
        conn.execute(
            "INSERT INTO trust_maintenance_request_notes"
            " (pmc_id, request_id, lease_id, tenant_id, body) VALUES (%s, %s, %s, %s, %s)",
            (session.pmc_id, request_id, request.lease_id, tenant, note.body),
        )
    except psycopg.errors.CheckViolation:
        # Closed, or cancelled, at the same moment.
        raise HTTPException(
            409, "This request is closed. Send a new one if you still need help."
        ) from None
    return maintenance.for_tenant(maintenance.one(conn, session.pmc_id, request_id))
