"""The owner portal's endpoints: an owner's properties, their statement, their year-end
package and income statement, their documents, and the bills over their approval limit, which
they approve or decline. For that owner only.

The owner and the PMC always come from the session, never from the request. The statement is
trust_report_owner_statement over whole days in UTC, from the first day to the last; the
year-end package is trust_report_owner_year_end for a calendar year, and the income statement
trust_report_income_statement over the same year.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, StringConstraints

from pms import accounts, exports
from pms.api import documents
from pms.api.deps import Conn, Owner, Rows, download

router = APIRouter(prefix="/api/owner")
MAX_DAYS = 3660  # about ten years


class Property(BaseModel):
    account_id: str
    property_id: UUID
    name: str
    bank: str
    # Money as text, so no float ever rounds a cent.
    balance: str


@router.get("/properties")
def properties(session: Owner, conn: Conn) -> list[Property]:
    """Each property the owner holds money for, with what it holds now."""
    rows = conn.execute(
        "SELECT t.ledger_account_id, p.id, p.display_name, b.display_name, a.balance"
        " FROM trust_ledger_accounts AS t"
        " JOIN trust_properties AS p ON p.id = t.property_id"
        " JOIN trust_bank_accounts AS b ON b.id = t.bank_account_id"
        " JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id"
        " WHERE t.pmc_id = %s AND t.owner_id = %s AND t.kind = 'owner_property'"
        ' ORDER BY p.display_name COLLATE "C", p.id',
        (session.pmc_id, session.person_id),
    ).fetchall()
    return [
        Property(account_id=a, property_id=p, name=n, bank=b, balance=exports.text(balance))
        for a, p, n, b, balance in rows
    ]


def statement_table(
    conn: psycopg.Connection[object], pmc_id: UUID, owner_id: UUID, start: date, end: date
) -> exports.Table:
    """An owner's statement over whole days (UTC): theirs in the portal, any owner's for staff
    (src/pms/api/directory.py)."""
    if end < start:
        raise HTTPException(422, "a statement runs from a day to the same day or a later one")
    if (end - start).days > MAX_DAYS:
        raise HTTPException(422, f"a statement covers at most {MAX_DAYS} days")
    period = (
        datetime.combine(start, time(0), UTC),
        datetime.combine(end + timedelta(days=1), time(0), UTC),
    )
    return exports.run_report(
        conn,
        "trust_report_owner_statement",
        [pmc_id, owner_id, *period],
    )


@router.get("/statement")
def statement(
    session: Owner, conn: Conn, start: Annotated[date, Query()], end: Annotated[date, Query()]
) -> Rows:
    table = statement_table(conn, session.pmc_id, session.person_id, start, end)
    return Rows.of(
        exports.Table(
            f"Owner statement, {start.isoformat()} to {end.isoformat()}", table.columns, table.rows
        )
    )


@router.get("/statement/download")
def statement_download(
    session: Owner,
    conn: Conn,
    start: Annotated[date, Query()],
    end: Annotated[date, Query()],
    fmt: Annotated[Literal["csv", "xlsx", "pdf"], Query(alias="format")] = "pdf",
) -> Response:
    table = statement_table(conn, session.pmc_id, session.person_id, start, end)
    titled = exports.Table(
        f"Owner statement, {start.isoformat()} to {end.isoformat()}", table.columns, table.rows
    )
    name = f"owner-statement-{start.isoformat()}-{end.isoformat()}"
    return download(exports.export(titled, fmt), name, fmt)


Year = Annotated[int, Query(ge=1900, le=9998)]


def year_end_table(
    conn: psycopg.Connection[object], session: accounts.Session, year: int
) -> exports.Table:
    table = exports.run_report(
        conn, "trust_report_owner_year_end", [session.pmc_id, session.person_id, year]
    )
    return exports.Table(f"Year-end package, {year}", table.columns, table.rows)


@router.get("/year-end")
def year_end(session: Owner, conn: Conn, year: Year) -> Rows:
    """The year's summary per property and across them, and the rents for the owner's
    1099-MISC. The PMS files nothing."""
    return Rows.of(year_end_table(conn, session, year))


@router.get("/year-end/download")
def year_end_download(
    session: Owner,
    conn: Conn,
    year: Year,
    fmt: Annotated[Literal["csv", "xlsx", "pdf"], Query(alias="format")] = "pdf",
) -> Response:
    return download(
        exports.export(year_end_table(conn, session, year), fmt), f"year-end-{year}", fmt
    )


def income_statement_table(
    conn: psycopg.Connection[object], session: accounts.Session, year: int
) -> exports.Table:
    table = exports.run_report(
        conn,
        "trust_report_income_statement",
        [session.pmc_id, session.person_id, date(year, 1, 1), date(year, 12, 31)],
    )
    return exports.Table(f"Income statement, {year}", table.columns, table.rows)


@router.get("/income-statement")
def income_statement(session: Owner, conn: Conn, year: Year) -> Rows:
    """The year's income and expenses per property and across them, cash basis, with the
    Schedule E lines they go on. The PMS files nothing."""
    return Rows.of(income_statement_table(conn, session, year))


@router.get("/income-statement/download")
def income_statement_download(
    session: Owner,
    conn: Conn,
    year: Year,
    fmt: Annotated[Literal["csv", "xlsx", "pdf"], Query(alias="format")] = "pdf",
) -> Response:
    return download(
        exports.export(income_statement_table(conn, session, year), fmt),
        f"income-statement-{year}",
        fmt,
    )


class OwnerDocument(BaseModel):
    id: UUID
    title: str
    content_type: str
    size: int
    uploaded_at: datetime
    # What it is about: the owner's own, a property's (or one of its units'), or a bill's receipt.
    about: Literal["owner", "property", "bill"]
    # Its property, unit or bill in words; None for the owner's own.
    about_name: str | None


DOCUMENTS = f"""
SELECT d.id, d.title, d.content_type, d.size, d.created_at,
       CASE d.kind WHEN 'owner_document' THEN 'owner' WHEN 'receipt' THEN 'bill'
                   ELSE 'property' END,
       CASE d.kind
           WHEN 'property_document' THEN p.display_name
           WHEN 'unit_document' THEN up.display_name || ', ' || u.display_name
           WHEN 'receipt' THEN 'Bill ' || b.reference || ', ' || v.display_name
       END
FROM trust_documents AS d
LEFT JOIN trust_properties AS p ON p.id = d.property_id
LEFT JOIN trust_units AS u ON u.id = d.unit_id
LEFT JOIN trust_properties AS up ON up.id = u.property_id
LEFT JOIN trust_bills AS b ON b.id = d.bill_id
LEFT JOIN trust_vendors AS v ON v.id = b.vendor_id
WHERE d.pmc_id = %(pmc)s AND {documents.OWNER_SEES}
  AND NOT EXISTS (SELECT 1 FROM trust_document_removals AS r WHERE r.document_id = d.id)
ORDER BY d.created_at DESC, d.id
"""  # noqa: S608


@router.get("/documents")
def owner_documents(session: Owner, conn: Conn) -> list[OwnerDocument]:
    """What this owner sees of the files, newest first: their own documents, their properties'
    and units' documents, and their bills' receipts (q046)."""
    rows = conn.execute(DOCUMENTS, {"pmc": session.pmc_id, "owner": session.person_id})
    return [
        OwnerDocument(
            id=i, title=t, content_type=c, size=n, uploaded_at=at, about=about, about_name=name
        )
        for i, t, c, n, at, about, name in rows.fetchall()
    ]


@router.get("/documents/{document_id}/file")
def owner_document_file(session: Owner, conn: Conn, document_id: UUID) -> Response:
    """A document this owner sees (documents.OWNER_SEES); any other reads as not found."""
    return documents.served(conn, session.pmc_id, document_id, session.person_id)


# --- bills over the approval limit ---------------------------------------------------------


class OwnerBill(BaseModel):
    id: UUID
    vendor: str
    reference: str
    property: str
    bill_date: date
    due_on: date
    amount: str
    memo: str
    approval_limit: str
    # The company's markup on it, as the owner's agreement sets it (migration 0083).
    markup: str | None = None
    markup_percent: str | None = None
    # None while it waits for the owner's decision; void when staff voided it (entered by
    # mistake, ADR 0010).
    decision: Literal["approved", "declined", "void"] | None
    decided_on: date | None
    decided_by: str | None
    reason: str | None
    # While the owner's balance is short of it: by how much, and what they need to send for it
    # and the bills due before it (ADR 0010).
    short: str | None = None
    needed: str | None = None


OWNER_BILLS = """
SELECT b.id, v.display_name, b.reference, p.display_name, b.bill_date, b.due_on, b.amount,
       b.memo, g.approval_limit,
       CASE WHEN dc.void THEN 'void'
            WHEN ap.bill_id IS NOT NULL THEN 'approved'
            WHEN dc.bill_id IS NOT NULL THEN 'declined' END,
       CASE WHEN dc.void THEN dc.declined_on ELSE coalesce(ap.approved_on, dc.declined_on) END,
       CASE WHEN dc.void THEN dc.declined_by ELSE coalesce(ap.approved_by, dc.declined_by) END,
       dc.reason, mk.amount, mk.percent, h.short, h.needed
FROM trust_bills AS b
JOIN trust_ledger_accounts AS t ON t.ledger_account_id = b.ledger_account_id
JOIN trust_vendors AS v ON v.id = b.vendor_id
JOIN trust_properties AS p ON p.id = t.property_id
-- The limit of the agreement in force on the bill's date, 0 without one, as
-- trust_set_aside_bill reads it.
CROSS JOIN LATERAL (
    SELECT coalesce((SELECT a.approval_limit FROM trust_management_agreements AS a
                     WHERE a.ledger_account_id = b.ledger_account_id
                       AND a.starts_on <= b.bill_date
                     ORDER BY a.starts_on DESC LIMIT 1), 0) AS approval_limit
) AS g
LEFT JOIN trust_bill_approvals AS ap ON ap.bill_id = b.id
LEFT JOIN trust_bill_declines AS dc ON dc.bill_id = b.id
LEFT JOIN trust_bill_markups AS mk ON mk.bill_id = b.id
-- What the owner's balance is short of it, while it is open (trust_bill_holds).
LEFT JOIN trust_bill_holds(%s) AS h ON h.bill_id = b.id
WHERE b.pmc_id = %s AND t.owner_id = %s AND t.kind = 'owner_property'
  AND (ap.bill_id IS NOT NULL OR dc.bill_id IS NOT NULL OR b.amount > g.approval_limit
       OR h.short > 0)
ORDER BY b.due_on, b.bill_date, v.display_name COLLATE "C", b.reference COLLATE "C", b.id
"""


@router.get("/bills")
def owner_bills(session: Owner, conn: Conn) -> list[OwnerBill]:
    """The owner's bills over the approval limit, by due date: those waiting for their decision
    and those decided, by them or as staff recorded it; and those their balance is short of."""
    rows = conn.execute(OWNER_BILLS, (session.pmc_id, session.pmc_id, session.person_id)).fetchall()
    return [
        OwnerBill(
            id=row[0],
            vendor=row[1],
            reference=row[2],
            property=row[3],
            bill_date=row[4],
            due_on=row[5],
            amount=cents(row[6]),
            memo=row[7],
            approval_limit=cents(row[8]),
            decision=row[9],
            decided_on=row[10],
            decided_by=row[11],
            reason=row[12],
            markup=None if row[13] is None else cents(row[13]),
            markup_percent=None if row[14] is None else f"{row[14].normalize():f}",
            short=cents(row[15]) if row[15] else None,
            needed=cents(row[16]) if row[16] else None,
        )
        for row in rows
    ]


class Decline(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


def your_bill(conn: psycopg.Connection[Any], session: accounts.Session, bill_id: UUID) -> str:
    """The owner's name, for a bill on one of their property accounts; any other bill reads as
    not found."""
    row = conn.execute(
        "SELECT o.display_name FROM trust_bills AS b"
        " JOIN trust_ledger_accounts AS t ON t.ledger_account_id = b.ledger_account_id"
        " JOIN trust_owners AS o ON o.id = t.owner_id"
        " WHERE b.pmc_id = %s AND b.id = %s AND t.owner_id = %s",
        (session.pmc_id, bill_id, session.person_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such bill of yours")
    return str(row[0])


def decide(conn: psycopg.Connection[Any], query: str, params: tuple[Any, ...]) -> None:
    try:
        conn.execute(query, params)
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "this bill has its decision already") from None
    except psycopg.errors.CheckViolation:  # its money was taken before the owner declined it
        raise HTTPException(
            409, "money was taken from your account to pay this bill already"
        ) from None


@router.post("/bills/{bill_id}/approval", status_code=201)
def approve_bill(session: Owner, conn: Conn, bill_id: UUID) -> None:
    """The owner approves one of their bills: staff may then pay it."""
    name = your_bill(conn, session, bill_id)
    decide(
        conn,
        "INSERT INTO trust_bill_approvals (pmc_id, bill_id, approved_on, approved_by, owner_id)"
        " VALUES (%s, %s, %s, %s, %s)",
        (session.pmc_id, bill_id, today(), f"{name}, in the owner portal", session.person_id),
    )


@router.post("/bills/{bill_id}/decline", status_code=201)
def decline_bill(session: Owner, conn: Conn, bill_id: UUID, decline: Decline) -> None:
    """The owner declines one of their bills: it is never paid from their money."""
    name = your_bill(conn, session, bill_id)
    decide(
        conn,
        "INSERT INTO trust_bill_declines"
        " (pmc_id, bill_id, declined_on, declined_by, reason, owner_id)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (
            session.pmc_id,
            bill_id,
            today(),
            f"{name}, in the owner portal",
            decline.reason,
            session.person_id,
        ),
    )


def today() -> date:
    return datetime.now(UTC).date()


def cents(amount: Decimal) -> str:
    return f"{amount.quantize(Decimal('0.01'))}"
