"""QuickBooks (the founder's review q117, src/pms/quickbooks.py): each ledger account's name in
the company's QuickBooks, which staff set (or put back to the PMS's own) in Settings, QuickBooks,
and the ledger's transfers over days as journal entries to import, for QuickBooks Desktop (IIF)
or QuickBooks Online (CSV). Naming an account is setup's; the export reads only."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

import psycopg
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, StringConstraints

from pms import exports, quickbooks
from pms.api.deps import Conn, Staff, download

router = APIRouter(prefix="/api/quickbooks")


class QuickBooksAccount(BaseModel):
    ledger_account_id: str
    kind: str
    bank: str
    holder: str
    default_name: str
    name: str | None  # the company's own name for it; None: the PMS's (default_name)


@router.get("/accounts")
def accounts(session: Staff, conn: Conn) -> list[QuickBooksAccount]:
    return [QuickBooksAccount(**vars(a)) for a in quickbooks.accounts(conn, session.pmc_id)]


class Name(BaseModel):
    name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
        | None
    )


@router.post("/accounts/{ledger_account_id}", status_code=204)
def name_account(session: Staff, conn: Conn, ledger_account_id: str, new: Name) -> None:
    """The account's name in QuickBooks from now on; None puts back the PMS's. The same name
    again writes nothing."""
    found = next(
        (
            a
            for a in quickbooks.accounts(conn, session.pmc_id)
            if a.ledger_account_id == ledger_account_id
        ),
        None,
    )
    if found is None:
        raise HTTPException(404, "no such ledger account")
    if found.name == new.name:
        return
    try:
        conn.execute(
            "INSERT INTO trust_quickbooks_accounts (pmc_id, ledger_account_id, name)"
            " VALUES (%s, %s, %s)",
            (session.pmc_id, ledger_account_id, new.name),
        )
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(
            422,
            "a QuickBooks name has no tab or line break, and a colon only between an account"
            " and its subaccount",
        ) from refused


@router.get("/journal")
def journal(
    session: Staff,
    conn: Conn,
    from_day: Annotated[date, Query(alias="from")],
    to_day: Annotated[date, Query(alias="to")],
    fmt: Annotated[Literal["iif", "csv"], Query(alias="format")],
) -> Response:
    """The ledger's transfers over the days (UTC) as journal entries QuickBooks imports."""
    try:
        entries = quickbooks.journal(conn, session.pmc_id, from_day, to_day)
    except exports.ExportError as refused:
        raise HTTPException(422, str(refused)) from None
    data = quickbooks.iif(entries) if fmt == "iif" else quickbooks.online_csv(entries)
    return download(data, f"quickbooks-journal-{from_day}-to-{to_day}", fmt)
