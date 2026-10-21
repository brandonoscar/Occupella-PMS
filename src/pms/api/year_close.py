"""Closing a year (migration 0106; the founder's review q116): Accounting > Banking, Year close.

Staff see the years closed and the next one to close: last year, with each trust bank account's
month-end so far and the journal entries dated in it still waiting for a decision. Closing takes
the approve role and moves no money (trust_close_year); from then on nothing is charged or billed
with a day in the closed year or before, so a late fix is dated in the open year.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pms.api.deps import Conn, Staff, refusal

router = APIRouter(prefix="/api/year-close")


class Closed(BaseModel):
    year: int
    closed_on: date
    closed_by: str


class AccountState(BaseModel):
    id: UUID
    name: str
    # The last day its month-end is approved through; none yet.
    approved_through: date | None
    # Whether the year touched it (a posting in it or before, or a cutover before its end), and
    # so whether it holds the close back until approved through December 31.
    in_the_year: bool
    ready: bool


class NextYear(BaseModel):
    year: int
    accounts: list[AccountState]
    # Journal entries dated in the year or before, still waiting for a decision.
    waiting_entries: int
    ready: bool


class YearClose(BaseModel):
    closed: list[Closed]
    # The last day closed: nothing is charged or billed with a day up to it.
    closed_until: date | None
    # Last year, while it isn't closed; none once it is.
    next: NextYear | None


class Closing(BaseModel):
    year: int = Field(ge=2000, le=2999)


ACCOUNTS = """
SELECT b.id, b.display_name, b.closed_through,
       b.opened_at < %(end)s OR EXISTS (
           SELECT 1 FROM trust_ledger_accounts AS t
           JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
           JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
           WHERE t.bank_account_id = b.id AND tr.event_at < %(end)s
       )
FROM trust_bank_accounts AS b
WHERE b.pmc_id = %(pmc)s
ORDER BY b.display_name COLLATE "C", b.id
"""


def next_year(conn: psycopg.Connection[Any], pmc: UUID, year: int) -> NextYear:
    end = datetime(year + 1, 1, 1, tzinfo=UTC)
    accounts = []
    for bank, name, closed_through, touched in conn.execute(
        ACCOUNTS, {"pmc": pmc, "end": end}
    ).fetchall():
        through = None if closed_through is None else (closed_through - timedelta(days=1)).date()
        approved = closed_through is not None and closed_through >= end
        accounts.append(
            AccountState(
                id=bank,
                name=name,
                approved_through=through,
                in_the_year=bool(touched),
                ready=approved or not touched,
            )
        )
    waiting = conn.execute(
        "SELECT count(*) FROM trust_journal_entries AS j WHERE j.pmc_id = %s AND j.event_on < %s"
        " AND NOT EXISTS (SELECT 1 FROM trust_journal_entry_decisions AS d"
        "                 WHERE d.entry_id = j.id)",
        (pmc, date(year + 1, 1, 1)),
    ).fetchone()
    assert waiting is not None
    last_day = date(year, 12, 31)
    reconciled = any(
        a.approved_through is not None and a.approved_through >= last_day for a in accounts
    )
    return NextYear(
        year=year,
        accounts=accounts,
        waiting_entries=waiting[0],
        ready=reconciled and all(a.ready for a in accounts) and waiting[0] == 0,
    )


@router.get("")
def year_close(session: Staff, conn: Conn) -> YearClose:
    """The years closed, newest first, and last year's readiness while it is open."""
    closed = [
        Closed(year=year, closed_on=at.astimezone(UTC).date(), closed_by=who)
        for year, at, who in conn.execute(
            "SELECT c.year, c.created_at, s.display_name FROM trust_year_closes AS c"
            " JOIN trust_staff AS s ON s.id = c.closed_by WHERE c.pmc_id = %s"
            " ORDER BY c.year DESC",
            (session.pmc_id,),
        ).fetchall()
    ]
    last_year = datetime.now(UTC).year - 1
    done = closed and closed[0].year >= last_year
    return YearClose(
        closed=closed,
        closed_until=date(closed[0].year, 12, 31) if closed else None,
        next=None if done else next_year(conn, session.pmc_id, last_year),
    )


@router.post("", status_code=201)
def close_year(session: Staff, conn: Conn, body: Closing) -> Closed:
    """Closes a year that has ended once every trust bank account it touched is approved
    through December 31 and no journal entry dated in it waits; the same year again answers the
    close already made. Takes the approve role (403)."""
    try:
        conn.execute("SELECT trust_close_year(%s, %s)", (session.pmc_id, body.year))
    except psycopg.errors.InvalidParameterValue as refused:  # a year not over
        raise HTTPException(422, refusal(refused)) from None
    except psycopg.errors.CheckViolation as refused:  # not ready, or a later year closed
        raise HTTPException(409, refusal(refused)) from None
    row = conn.execute(
        "SELECT c.year, c.created_at, s.display_name FROM trust_year_closes AS c"
        " JOIN trust_staff AS s ON s.id = c.closed_by WHERE c.pmc_id = %s AND c.year = %s",
        (session.pmc_id, body.year),
    ).fetchone()
    assert row is not None
    return Closed(year=row[0], closed_on=row[1].astimezone(UTC).date(), closed_by=row[2])
