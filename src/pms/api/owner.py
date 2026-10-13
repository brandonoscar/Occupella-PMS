"""The owner portal's endpoints: an owner's properties, their statement and their year-end
package, for that owner only.

The owner and the PMC always come from the session, never from the request. The statement is
trust_report_owner_statement over whole days in UTC, from the first day to the last; the
year-end package is trust_report_owner_year_end for a calendar year.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

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
    conn: psycopg.Connection[object], session: accounts.Session, start: date, end: date
) -> exports.Table:
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
        [session.pmc_id, session.person_id, *period],
    )


@router.get("/statement")
def statement(
    session: Owner, conn: Conn, start: Annotated[date, Query()], end: Annotated[date, Query()]
) -> Rows:
    table = statement_table(conn, session, start, end)
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
    table = statement_table(conn, session, start, end)
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


class OwnerDocument(BaseModel):
    id: UUID
    title: str
    content_type: str
    size: int
    uploaded_at: datetime


@router.get("/documents")
def owner_documents(session: Owner, conn: Conn) -> list[OwnerDocument]:
    """The documents staff keep for this owner, newest first."""
    rows = conn.execute(
        "SELECT d.id, d.title, d.content_type, d.size, d.created_at FROM trust_documents AS d"
        " WHERE d.pmc_id = %s AND d.kind = 'owner_document' AND d.owner_id = %s"
        " AND NOT EXISTS (SELECT 1 FROM trust_document_removals AS r WHERE r.document_id = d.id)"
        " ORDER BY d.created_at DESC, d.id",
        (session.pmc_id, session.person_id),
    ).fetchall()
    return [
        OwnerDocument(id=i, title=t, content_type=c, size=n, uploaded_at=at)
        for i, t, c, n, at in rows
    ]


@router.get("/documents/{document_id}/file")
def owner_document_file(session: Owner, conn: Conn, document_id: UUID) -> Response:
    """One of this owner's documents; any other reads as not found."""
    return documents.served(conn, session.pmc_id, document_id, session.person_id)
