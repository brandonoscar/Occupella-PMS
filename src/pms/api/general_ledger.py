"""The general ledger on screen (ADR 0005), as Buildium shows it: for days (UTC), each account of
the company's trust ledger with its prior balance, its entries with a running balance, and its
totals. It is trust_report_general_ledger's own answer, grouped by account and paged by account,
so the screen, the downloads and the AI's report agree. Cash basis only: what tenants owe stays
out of the trust ledger (CLAUDE.md), so an accrual view would be made up. It moves no money;
a correction is a journal entry (`journal.py`).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from pms.api.deps import Conn, Staff
from pms.api.rent import cents
from pms.api.rentals import Page, Paged, Search

router = APIRouter(prefix="/api/general-ledger")

AccountsPerPage = Annotated[int, Query(ge=1, le=100)]


class Entry(BaseModel):
    posted_at: str
    description: str
    debit: str
    credit: str
    balance: str


class LedgerAccount(BaseModel):
    bank: str
    account: str
    prior_balance: str
    entries: list[Entry]
    debits: str
    credits: str
    balance: str


def money(value: Decimal | None) -> str:
    return cents(value or Decimal(0))


@router.get("")
def general_ledger(
    session: Staff,
    conn: Conn,
    from_day: Annotated[date, Query(alias="from")],
    to_day: Annotated[date, Query(alias="to")],
    q: Search = None,
    page: Page = 1,
    per_page: AccountsPerPage = 25,
) -> Paged[LedgerAccount]:
    """Each account's prior balance, entries with a running balance and totals for the days
    (UTC), a page of accounts at a time, those whose account or bank holds the words."""
    if to_day < from_day:
        raise HTTPException(422, "a general ledger runs from a day to the same day or a later one")
    accounts: list[LedgerAccount] = []
    for item, bank, account, posted_at, description, debit, credit, balance in conn.execute(
        "SELECT item, bank, account, posted_at, description, debit, credit, balance"
        " FROM trust_report_general_ledger(%s, %s, %s) ORDER BY line",
        (session.pmc_id, from_day, to_day),
    ).fetchall():
        if item == "opening balance":
            accounts.append(
                LedgerAccount(
                    bank=bank,
                    account=account,
                    prior_balance=money(balance),
                    entries=[],
                    debits=money(None),
                    credits=money(None),
                    balance=money(balance),
                )
            )
        elif item == "entry":
            accounts[-1].entries.append(
                Entry(
                    posted_at=posted_at,
                    description=description,
                    debit=money(debit),
                    credit=money(credit),
                    balance=money(balance),
                )
            )
        elif item == "closing balance":
            accounts[-1].debits, accounts[-1].credits = money(debit), money(credit)
            accounts[-1].balance = money(balance)
    words = (q or "").strip().casefold()
    if words:
        accounts = [a for a in accounts if words in f"{a.bank} {a.account}".casefold()]
    first = (page - 1) * per_page
    return Paged(
        items=accounts[first : first + per_page], total=len(accounts), page=page, per_page=per_page
    )
