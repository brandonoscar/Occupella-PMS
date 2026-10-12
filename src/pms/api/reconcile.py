"""Staff's month-end: import a trust bank account's statements (CSV or OFX), match their lines
to the ledger transfers that moved its cash, and approve the period once the books lack no line.

Statements go in with plain inserts that the database keeps whole (migration 0032); matches go
through trust_match_bank_lines and trust_unmatch_bank_lines, and approval through
trust_approve_bank_reconciliation, which refuses while any line up to the day is missing from
the books. The worksheet is trust_report_bank_reconciliation, the same report staff download.

Nothing here moves money: it records what the bank says and compares it with the books.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints

from pms import exports, statements
from pms.api.deps import Conn, Staff

router = APIRouter(prefix="/api/reconcile")

Balance = Annotated[
    Decimal, Field(gt=Decimal("-1000000000"), lt=Decimal("1000000000"), decimal_places=2)
]
CENT = Decimal("0.01")
# How far apart a line's day and a transfer's may be for the automatic match to pair them.
MATCH_DAYS = 7


def cents(value: Decimal) -> str:
    return exports.text(Decimal(value).quantize(CENT))


def one(conn: psycopg.Connection[Any], query: str, params: tuple[Any, ...]) -> Any:
    row = conn.execute(query, params).fetchone()
    assert row is not None  # every query here returns one row
    return row[0]


def reason(refused: psycopg.Error) -> str:
    return str(refused).splitlines()[0].removeprefix("trust: ")


def bank_of(conn: psycopg.Connection[Any], pmc_id: UUID, bank: UUID) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM trust_bank_accounts WHERE pmc_id = %s AND id = %s", (pmc_id, bank)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such trust bank account")


# --- what each trust bank account holds ----------------------------------------------------


class StatementOut(BaseModel):
    id: UUID
    first_day: date
    last_day: date
    opening_balance: str
    closing_balance: str
    lines: int
    source: str
    imported_at: datetime


class Approved(BaseModel):
    id: UUID
    period_start: datetime
    period_end: datetime
    statement_balance: str
    book_balance: str
    prepared_by: str
    approved_by: str
    approved_at: datetime


class BankBooks(BaseModel):
    id: UUID
    name: str
    kind: str
    # The last day an approved reconciliation closed, and the next period's first day (known
    # once the account was cut over, reconciled or given a statement).
    closed_through: date | None
    next_start: date | None
    statements: list[StatementOut]
    reconciliations: list[Approved]


def next_start(conn: psycopg.Connection[Any], bank: UUID) -> date | None:
    (start,) = conn.execute(
        "SELECT (coalesce(b.closed_through, b.opened_at, (SELECT min(s.first_day)::timestamp"
        "   AT TIME ZONE 'UTC' FROM trust_bank_statements AS s WHERE s.bank_account_id = b.id))"
        " AT TIME ZONE 'UTC')::date FROM trust_bank_accounts AS b WHERE b.id = %s",
        (bank,),
    ).fetchone() or (None,)
    return start


@router.get("")
def books(session: Staff, conn: Conn) -> list[BankBooks]:
    """Each trust bank account: its statements, its approved reconciliations, where it stands."""
    found: list[BankBooks] = []
    for bank, name, kind, closed in conn.execute(
        "SELECT id, display_name, kind, (closed_through AT TIME ZONE 'UTC')::date - 1"
        ' FROM trust_bank_accounts WHERE pmc_id = %s ORDER BY kind, display_name COLLATE "C", id',
        (session.pmc_id,),
    ).fetchall():
        found.append(
            BankBooks(
                id=bank,
                name=name,
                kind=kind,
                closed_through=closed,
                next_start=next_start(conn, bank),
                statements=[
                    StatementOut(
                        id=i,
                        first_day=f,
                        last_day=la,
                        opening_balance=cents(o),
                        closing_balance=cents(c),
                        lines=n,
                        source=src,
                        imported_at=at,
                    )
                    for i, f, la, o, c, n, src, at in conn.execute(
                        "SELECT id, first_day, last_day, opening_balance, closing_balance,"
                        " line_count, source, imported_at FROM trust_bank_statements"
                        " WHERE bank_account_id = %s ORDER BY first_day DESC",
                        (bank,),
                    ).fetchall()
                ],
                reconciliations=[
                    Approved(
                        id=i,
                        period_start=s,
                        period_end=e,
                        statement_balance=cents(sb),
                        book_balance=cents(bb),
                        prepared_by=p,
                        approved_by=a,
                        approved_at=at,
                    )
                    for i, s, e, sb, bb, p, a, at in conn.execute(
                        "SELECT id, period_start, period_end, statement_balance, book_balance,"
                        " prepared_by, approved_by, approved_at FROM trust_reconciliations"
                        " WHERE bank_account_id = %s ORDER BY period_end DESC",
                        (bank,),
                    ).fetchall()
                ],
            )
        )
    return found


# --- importing a statement -------------------------------------------------------------------


class NewStatement(BaseModel):
    bank_account_id: UUID
    format: Literal["csv", "ofx"]
    # The file's text, as the page read it.
    content: Annotated[str, StringConstraints(min_length=1, max_length=2_000_000)]
    # A CSV file doesn't say these; an OFX file does, and these override it.
    first_day: date | None = None
    last_day: date | None = None
    # The balance the statement opens at; by default where the last one closed (0 for the
    # first), or for OFX its closing balance less its lines.
    opening_balance: Balance | None = None
    # If given (or in the OFX file), the lines must take the opening balance to it.
    closing_balance: Balance | None = None


class Imported(BaseModel):
    id: UUID
    first_day: date
    last_day: date
    lines: int
    closing_balance: str


@router.post("/statements", status_code=201)
def import_statement(session: Staff, conn: Conn, new: NewStatement) -> Imported:
    """Imports one statement of a trust bank account, whole: it starts the day after the last
    one ends, at its closing balance. A first statement's opening balance comes in as a line
    of its own, so every dollar the bank holds can be matched."""
    bank_of(conn, session.pmc_id, new.bank_account_id)
    try:
        read = statements.read(new.content, new.format)
    except statements.StatementError as bad:
        raise HTTPException(422, str(bad)) from None
    last = conn.execute(
        "SELECT last_day, closing_balance FROM trust_bank_statements WHERE bank_account_id = %s"
        " ORDER BY last_day DESC LIMIT 1",
        (new.bank_account_id,),
    ).fetchone()
    days = [line.posted_on for line in read.lines]
    first_day = new.first_day or read.first_day or (last[0] + timedelta(days=1) if last else None)
    last_day = new.last_day or read.last_day or (max(days) if days else None)
    if first_day is None or last_day is None:
        raise HTTPException(422, "say which days the statement covers")
    if last_day < first_day:
        raise HTTPException(422, "a statement's last day is on or after its first")
    outside = [day for day in days if not first_day <= day <= last_day]
    if outside:
        raise HTTPException(
            422, f"a line on {min(outside)} is outside the statement's {first_day} to {last_day}"
        )
    total = sum((line.amount for line in read.lines), Decimal(0))
    stated = new.closing_balance if new.closing_balance is not None else read.closing_balance
    if new.opening_balance is not None:
        opening = new.opening_balance
    elif read.closing_balance is not None:
        opening = read.closing_balance - total
    else:
        opening = last[1] if last else Decimal(0)
    if stated is not None and opening + total != stated:
        raise HTTPException(
            422,
            f"the lines take {cents(opening)} to {cents(opening + total)}; the statement says "
            f"{cents(stated)}",
        )
    lines = list(read.lines)
    if last is None and opening != 0:
        lines.insert(0, statements.Line(first_day, opening, "Balance brought forward", None))
        opening = Decimal(0)
    try:
        statement = one(
            conn,
            "INSERT INTO trust_bank_statements (pmc_id, bank_account_id, first_day, last_day,"
            " opening_balance, closing_balance, line_count, source, file_sha256, imported_by)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                session.pmc_id,
                new.bank_account_id,
                first_day,
                last_day,
                opening,
                opening + sum((line.amount for line in lines), Decimal(0)),
                len(lines),
                new.format,
                hashlib.sha256(new.content.encode()).hexdigest(),
                session.person_id,
            ),
        )
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO trust_bank_statement_lines (pmc_id, statement_id, line_no,"
                " posted_on, amount, description, bank_reference)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        session.pmc_id,
                        statement,
                        n,
                        line.posted_on,
                        line.amount,
                        line.description,
                        line.reference,
                    )
                    for n, line in enumerate(lines, start=1)
                ],
            )
        conn.execute("SET CONSTRAINTS trust_bank_statement_total IMMEDIATE")
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            409, "this file is imported for this trust bank account already"
        ) from None
    except psycopg.errors.CheckViolation as refused:  # not the next statement, a closed period
        raise HTTPException(409, reason(refused)) from None
    return Imported(
        id=statement,
        first_day=first_day,
        last_day=last_day,
        lines=len(lines),
        closing_balance=cents(opening + sum((line.amount for line in lines), Decimal(0))),
    )


# --- the worksheet ---------------------------------------------------------------------------


class Row(BaseModel):
    line: int
    item: str
    detail: str | None
    amount: str | None


class OpenLine(BaseModel):
    id: UUID
    posted_on: date
    amount: str
    description: str
    reference: str | None


class OpenTransfer(BaseModel):
    id: str
    on: date
    amount: str  # the cash it moved: money in is positive
    memo: str | None


class StandingMatch(BaseModel):
    id: UUID
    lines: list[OpenLine]
    transfers: list[OpenTransfer]


class Worksheet(BaseModel):
    through: date | None
    rows: list[Row]
    open_lines: list[OpenLine]
    open_transfers: list[OpenTransfer]
    matches: list[StandingMatch]


LIVE = "NOT EXISTS (SELECT 1 FROM trust_bank_unmatches AS u WHERE u.match_id = {0}.match_id)"

LINES = """
SELECT l.id, l.posted_on, l.amount, l.description, l.bank_reference
FROM trust_bank_statement_lines AS l
JOIN trust_bank_statements AS s ON s.id = l.statement_id
WHERE s.bank_account_id = %(bank)s AND {where}
ORDER BY l.posted_on, s.first_day, l.line_no
"""

TRANSFERS = """
SELECT tr.id, (tr.event_at AT TIME ZONE 'UTC')::date, -sum(e.amount), tr.metadata ->> 'memo'
FROM trust_ledger_accounts AS t
JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
WHERE t.bank_account_id = %(bank)s AND t.kind = 'bank_cash' AND {where}
GROUP BY tr.id
HAVING sum(e.amount) <> 0
ORDER BY tr.event_at, tr.id
"""


def open_lines(
    conn: psycopg.Connection[Any], bank: UUID, where: str, **more: Any
) -> list[OpenLine]:
    return [
        OpenLine(id=i, posted_on=p, amount=cents(a), description=d, reference=r)
        for i, p, a, d, r in conn.execute(LINES.format(where=where), {"bank": bank, **more})
    ]


def transfers(
    conn: psycopg.Connection[Any], bank: UUID, where: str, **more: Any
) -> list[OpenTransfer]:
    return [
        OpenTransfer(id=i, on=o, amount=cents(a), memo=m)
        for i, o, a, m in conn.execute(TRANSFERS.format(where=where), {"bank": bank, **more})
    ]


UNMATCHED_LINE = (
    "NOT EXISTS (SELECT 1 FROM trust_bank_match_lines AS ml WHERE ml.line_id = l.id AND "
    + LIVE.format("ml")
    + ")"
)
UNMATCHED_TRANSFER = (
    "NOT EXISTS (SELECT 1 FROM trust_bank_match_transfers AS mt JOIN trust_bank_matches AS m"
    " ON m.id = mt.match_id WHERE mt.transfer_id = tr.id AND m.bank_account_id = %(bank)s AND "
    + LIVE.format("mt")
    + ")"
)


@router.get("/{bank_account_id}")
def worksheet(
    session: Staff,
    conn: Conn,
    bank_account_id: UUID,
    through: Annotated[date | None, Query()] = None,
) -> Worksheet:
    """The reconciliation at the end of a day (by default the last statement's last day), the
    lines and transfers no match holds, and the matches that stand."""
    bank_of(conn, session.pmc_id, bank_account_id)
    if through is None:
        (through,) = conn.execute(
            "SELECT max(last_day) FROM trust_bank_statements WHERE bank_account_id = %s",
            (bank_account_id,),
        ).fetchone() or (None,)
    rows: list[Row] = []
    if through is not None:
        try:
            rows = [
                Row(line=n, item=i, detail=d, amount=None if a is None else cents(a))
                for n, i, d, a in conn.execute(
                    "SELECT * FROM trust_report_bank_reconciliation(%s, %s, %s)",
                    (session.pmc_id, bank_account_id, through),
                )
            ]
        except psycopg.errors.InvalidParameterValue as refused:  # no statement covers it
            raise HTTPException(422, reason(refused)) from None
    matches: list[StandingMatch] = []
    for (match,) in conn.execute(
        "SELECT m.id FROM trust_bank_matches AS m WHERE m.bank_account_id = %s AND NOT EXISTS"
        " (SELECT 1 FROM trust_bank_unmatches AS u WHERE u.match_id = m.id)"
        " ORDER BY m.matched_at DESC, m.id LIMIT 50",
        (bank_account_id,),
    ).fetchall():
        matches.append(
            StandingMatch(
                id=match,
                lines=open_lines(
                    conn,
                    bank_account_id,
                    "l.id IN (SELECT line_id FROM trust_bank_match_lines WHERE match_id = %(m)s)",
                    m=match,
                ),
                transfers=transfers(
                    conn,
                    bank_account_id,
                    "tr.id IN (SELECT transfer_id FROM trust_bank_match_transfers"
                    " WHERE match_id = %(m)s)",
                    m=match,
                ),
            )
        )
    return Worksheet(
        through=through,
        rows=rows,
        open_lines=open_lines(conn, bank_account_id, UNMATCHED_LINE),
        open_transfers=transfers(conn, bank_account_id, UNMATCHED_TRANSFER),
        matches=matches,
    )


# --- matching ----------------------------------------------------------------------------------


class NewMatch(BaseModel):
    line_ids: Annotated[list[UUID], Field(max_length=100)] = []
    transfer_ids: Annotated[list[Annotated[str, Field(max_length=100)]], Field(max_length=100)] = []


class Created(BaseModel):
    id: UUID


def match(
    conn: psycopg.Connection[Any], session: Any, bank: UUID, lines: list[UUID], moved: list[str]
) -> UUID:
    return UUID(
        str(
            one(
                conn,
                "SELECT trust_match_bank_lines(%s, %s, %s::uuid[], %s::text[], %s)",
                (session.pmc_id, bank, lines, moved, session.person_id),
            )
        )
    )


@router.post("/{bank_account_id}/matches", status_code=201)
def add_match(session: Staff, conn: Conn, bank_account_id: UUID, new: NewMatch) -> Created:
    """Matches lines to the transfers that moved the account's cash by the same total."""
    bank_of(conn, session.pmc_id, bank_account_id)
    try:
        return Created(id=match(conn, session, bank_account_id, new.line_ids, new.transfer_ids))
    except psycopg.errors.InvalidParameterValue as bad:  # not this account's, named twice
        raise HTTPException(422, reason(bad)) from None
    except psycopg.errors.CheckViolation as refused:  # sides differ, matched, a closed period
        raise HTTPException(409, reason(refused)) from None


@router.post("/matches/{match_id}/undo", status_code=204)
def undo(session: Staff, conn: Conn, match_id: UUID) -> None:
    """Undoes a match, unless a reconciliation already approved rests on it."""
    try:
        conn.execute(
            "SELECT trust_unmatch_bank_lines(%s, %s, %s)",
            (session.pmc_id, match_id, session.person_id),
        )
    except psycopg.errors.InvalidParameterValue:
        raise HTTPException(404, "no such match") from None
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, reason(refused)) from None


class Counted(BaseModel):
    matched: int


@router.post("/{bank_account_id}/auto-match")
def auto_match(session: Staff, conn: Conn, bank_account_id: UUID) -> Counted:
    """Matches each open line to the one open transfer of the same amount within a week of it,
    when neither has another such partner. Anything less sure is left to staff."""
    bank_of(conn, session.pmc_id, bank_account_id)
    lines = open_lines(conn, bank_account_id, UNMATCHED_LINE)
    moved = transfers(conn, bank_account_id, UNMATCHED_TRANSFER)

    def near(line: OpenLine, transfer: OpenTransfer) -> bool:
        return (
            line.amount == transfer.amount
            and abs((line.posted_on - transfer.on).days) <= MATCH_DAYS
        )

    matched = 0
    for line in lines:
        partners = [t for t in moved if near(line, t)]
        if len(partners) != 1 or sum(near(other, partners[0]) for other in lines) != 1:
            continue
        try:
            with conn.transaction():  # a savepoint: a refusal leaves the others standing
                match(conn, session, bank_account_id, [line.id], [partners[0].id])
            matched += 1
        except psycopg.errors.CheckViolation:  # in a closed period
            continue
    return Counted(matched=matched)


# --- approving -------------------------------------------------------------------------------


class Approval(BaseModel):
    through: date


@router.post("/{bank_account_id}/approve", status_code=201)
def approve(session: Staff, conn: Conn, bank_account_id: UUID, approval: Approval) -> Created:
    """Approves the next period up to the end of a day, once the books lack no line of the
    statements up to it; the account is then closed through that day."""
    bank_of(conn, session.pmc_id, bank_account_id)
    start = next_start(conn, bank_account_id)
    if start is None:
        raise HTTPException(422, "import a statement first")
    if approval.through < start:
        raise HTTPException(422, f"the next period starts on {start}")
    if approval.through >= datetime.now(UTC).date():
        raise HTTPException(422, "approve a period once its last day has ended")
    name = one(conn, "SELECT display_name FROM trust_staff WHERE id = %s", (session.person_id,))
    try:
        reconciliation = one(
            conn,
            "SELECT trust_approve_bank_reconciliation(%s, %s, %s, %s, %s)",
            (session.pmc_id, bank_account_id, approval.through, name, name),
        )
    except psycopg.errors.InvalidParameterValue as bad:  # no statement covers the day
        raise HTTPException(422, reason(bad)) from None
    except psycopg.errors.CheckViolation as refused:  # lines the books lack
        raise HTTPException(409, reason(refused)) from None
    return Created(id=reconciliation)
