"""Staff's month-end: import a trust bank account's statements (CSV or OFX), match their lines
to the ledger transfers that moved its cash, and once the books lack no line, one staff member
prepares the period and another approves it.

Statements go in with plain inserts that the database keeps whole (migration 0032); matches go
through trust_match_bank_lines and trust_unmatch_bank_lines. trust_prepare_bank_reconciliation
records the figures through a day, refusing while any line up to it is missing from the books,
and trust_approve_bank_reconciliation approves them for a staff member who holds the approve role
and didn't prepare them, while they still stand (migration 0034). The worksheet is
trust_report_bank_reconciliation, the same report staff download.

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
from pms.api.deps import Conn, Staff, refusal

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


class Prepared(BaseModel):
    """A period prepared and not approved yet: the account's latest preparation, if it is of a
    day after the account's closing."""

    id: UUID
    through: date
    bank_balance: str
    book_cash: str
    prepared_by: str
    preparer_id: UUID
    prepared_at: datetime


class BankBooks(BaseModel):
    id: UUID
    name: str
    kind: str
    # What the books say the account holds now: its cash, everything held in it.
    book_cash: str
    # The last day an approved reconciliation closed, and the next period's first day (known
    # once the account was cut over, reconciled or given a statement).
    closed_through: date | None
    next_start: date | None
    statements: list[StatementOut]
    reconciliations: list[Approved]
    prepared: Prepared | None


def prepared(conn: psycopg.Connection[Any], bank: UUID) -> Prepared | None:
    row = conn.execute(
        "SELECT p.id, p.through, p.bank_balance, p.book_cash, s.display_name, p.prepared_by,"
        " p.created_at FROM trust_reconciliation_preparations AS p"
        " JOIN trust_staff AS s ON s.id = p.prepared_by"
        " JOIN trust_bank_accounts AS b ON b.id = p.bank_account_id"
        " WHERE p.bank_account_id = %s AND (b.closed_through IS NULL"
        "   OR b.closed_through < (p.through + 1)::timestamp AT TIME ZONE 'UTC')"
        " ORDER BY p.created_at DESC LIMIT 1",
        (bank,),
    ).fetchone()
    if row is None:
        return None
    i, through, bank_balance, book_cash, name, preparer, at = row
    return Prepared(
        id=i,
        through=through,
        bank_balance=cents(bank_balance),
        book_cash=cents(book_cash),
        prepared_by=name,
        preparer_id=preparer,
        prepared_at=at,
    )


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
    for bank, name, kind, closed, cash in conn.execute(
        "SELECT k.id, k.display_name, k.kind, (k.closed_through AT TIME ZONE 'UTC')::date - 1,"
        # Cash is the side money leaves from, so the books' cash is its balance negated.
        " (SELECT -coalesce(sum(l.balance), 0) FROM trust_ledger_accounts AS a"
        "  JOIN pgledger_accounts AS l ON l.id = a.ledger_account_id"
        "  WHERE a.bank_account_id = k.id AND a.kind = 'bank_cash')"
        " FROM trust_bank_accounts AS k WHERE k.pmc_id = %s"
        ' ORDER BY k.kind, k.display_name COLLATE "C", k.id',
        (session.pmc_id,),
    ).fetchall():
        found.append(
            BankBooks(
                id=bank,
                name=name,
                kind=kind,
                book_cash=cents(cash),
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
                prepared=prepared(conn, bank),
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
        raise HTTPException(409, refusal(refused)) from None
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


class OpenItem(BaseModel):
    """A check or deposit carried over from the old system that the bank had not cleared."""

    id: UUID
    issued_on: date
    amount: str  # the cash it moved in the old books: a check out is negative
    description: str
    reference: str | None


class StandingMatch(BaseModel):
    id: UUID
    lines: list[OpenLine]
    transfers: list[OpenTransfer]
    items: list[OpenItem]


class Opening(BaseModel):
    """A migrated account's cutover, as the opening match needs it."""

    book_cash: str
    carried: str  # the items not yet cleared at the cutover
    brought_forward: str  # what the first statement's opening line must be
    signed_off: bool
    matched: bool


class Worksheet(BaseModel):
    through: date | None
    rows: list[Row]
    open_lines: list[OpenLine]
    open_transfers: list[OpenTransfer]
    open_items: list[OpenItem]
    opening: Opening | None
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


ITEMS = """
SELECT i.id, i.issued_on, i.amount, i.description, i.reference
FROM trust_cutover_items AS i
WHERE i.bank_account_id = %(bank)s AND {where}
ORDER BY i.issued_on, i.description COLLATE "C", i.id
"""


def items(conn: psycopg.Connection[Any], bank: UUID, where: str, **more: Any) -> list[OpenItem]:
    return [
        OpenItem(id=i, issued_on=o, amount=cents(a), description=d, reference=r)
        for i, o, a, d, r in conn.execute(ITEMS.format(where=where), {"bank": bank, **more})
    ]


def in_live_match(side: str) -> str:
    return (
        "EXISTS (SELECT 1 FROM trust_bank_match_cutover_items AS mi WHERE mi.item_id = i.id"
        f" AND mi.side = '{side}' AND " + LIVE.format("mi") + ")"
    )


# Carried by the opening match and not cleared yet: what a bank line may still clear.
OPEN_ITEM = in_live_match("carried") + " AND NOT " + in_live_match("cleared")


def opening(conn: psycopg.Connection[Any], bank: UUID) -> Opening | None:
    row = conn.execute(
        "SELECT o.book_cash,"
        " (SELECT coalesce(sum(i.amount), 0) FROM trust_cutover_items AS i"
        "  WHERE i.bank_account_id = o.bank_account_id),"
        " EXISTS (SELECT 1 FROM trust_cutover_signoffs AS s"
        "  WHERE s.bank_account_id = o.bank_account_id),"
        " EXISTS (SELECT 1 FROM trust_bank_match_transfers AS mt"
        "  JOIN trust_bank_matches AS m ON m.id = mt.match_id"
        "  WHERE mt.transfer_id = o.transfer_ids[1] AND m.bank_account_id = o.bank_account_id"
        "  AND " + LIVE.format("mt") + ")"
        " FROM trust_opening_balances AS o WHERE o.bank_account_id = %s",
        (bank,),
    ).fetchone()
    if row is None:
        return None
    book, carried, signed, matched = row
    return Opening(
        book_cash=cents(book),
        carried=cents(carried),
        brought_forward=cents(book - carried),
        signed_off=signed,
        matched=matched,
    )


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
            raise HTTPException(422, refusal(refused)) from None
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
                items=items(
                    conn,
                    bank_account_id,
                    "i.id IN (SELECT item_id FROM trust_bank_match_cutover_items"
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
        open_items=items(conn, bank_account_id, OPEN_ITEM),
        opening=opening(conn, bank_account_id),
        matches=matches,
    )


# --- matching ----------------------------------------------------------------------------------


class NewMatch(BaseModel):
    line_ids: Annotated[list[UUID], Field(max_length=100)] = []
    transfer_ids: Annotated[list[Annotated[str, Field(max_length=100)]], Field(max_length=100)] = []
    # Checks and deposits carried over from the old system that these lines clear.
    cutover_item_ids: Annotated[list[UUID], Field(max_length=100)] = []


class Created(BaseModel):
    id: UUID


def match(
    conn: psycopg.Connection[Any],
    session: Any,
    bank: UUID,
    lines: list[UUID],
    moved: list[str],
    carried: list[UUID] | None = None,
) -> UUID:
    return UUID(
        str(
            one(
                conn,
                "SELECT trust_match_bank_lines(%s, %s, %s::uuid[], %s::text[], %s::uuid[], %s)",
                (session.pmc_id, bank, lines, moved, carried or [], session.person_id),
            )
        )
    )


@router.post("/{bank_account_id}/matches", status_code=201)
def add_match(session: Staff, conn: Conn, bank_account_id: UUID, new: NewMatch) -> Created:
    """Matches lines to the transfers that moved the account's cash by the same total."""
    bank_of(conn, session.pmc_id, bank_account_id)
    try:
        return Created(
            id=match(
                conn,
                session,
                bank_account_id,
                new.line_ids,
                new.transfer_ids,
                new.cutover_item_ids,
            )
        )
    except psycopg.errors.InvalidParameterValue as bad:  # not this account's, named twice
        raise HTTPException(422, refusal(bad)) from None
    except psycopg.errors.CheckViolation as refused:  # sides differ, matched, a closed period
        raise HTTPException(409, refusal(refused)) from None


class OpeningMatch(BaseModel):
    line_id: UUID


@router.post("/{bank_account_id}/opening-match", status_code=201)
def match_opening(session: Staff, conn: Conn, bank_account_id: UUID, new: OpeningMatch) -> Created:
    """Matches a migrated account's balance brought forward (the first statement's opening line)
    to its opening balances less the items the bank had not cleared, once the cutover is signed
    off."""
    bank_of(conn, session.pmc_id, bank_account_id)
    try:
        made = one(
            conn,
            "SELECT trust_match_cutover_opening(%s, %s, %s, %s)",
            (session.pmc_id, bank_account_id, new.line_id, session.person_id),
        )
    except psycopg.errors.InvalidParameterValue as bad:  # not this account's line
        raise HTTPException(422, refusal(bad)) from None
    except psycopg.errors.CheckViolation as refused:  # not signed off, amounts differ, matched
        raise HTTPException(409, refusal(refused)) from None
    return Created(id=made)


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
        raise HTTPException(409, refusal(refused)) from None


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


# --- preparing and approving --------------------------------------------------------------


class Preparation(BaseModel):
    through: date


@router.post("/{bank_account_id}/prepare", status_code=201)
def prepare(session: Staff, conn: Conn, bank_account_id: UUID, preparation: Preparation) -> Created:
    """Prepares the next period up to the end of a day, once the books lack no line of the
    statements up to it: another staff member who may approve then approves it."""
    bank_of(conn, session.pmc_id, bank_account_id)
    start = next_start(conn, bank_account_id)
    if start is None:
        raise HTTPException(422, "import a statement first")
    if preparation.through < start:
        raise HTTPException(422, f"the next period starts on {start}")
    if preparation.through >= datetime.now(UTC).date():
        raise HTTPException(422, "prepare a period once its last day has ended")
    try:
        made = one(
            conn,
            "SELECT trust_prepare_bank_reconciliation(%s, %s, %s)",
            (session.pmc_id, bank_account_id, preparation.through),
        )
    except psycopg.errors.InvalidParameterValue as bad:  # no statement covers the day
        raise HTTPException(422, refusal(bad)) from None
    except psycopg.errors.CheckViolation as refused:  # lines the books lack
        raise HTTPException(409, refusal(refused)) from None
    return Created(id=made)


class Approval(BaseModel):
    preparation_id: UUID


@router.post("/{bank_account_id}/approve", status_code=201)
def approve(session: Staff, conn: Conn, bank_account_id: UUID, approval: Approval) -> Created:
    """Approves the account's period as another staff member prepared it, if the figures still
    stand; the account is then closed through its last day. Approving takes the approve role,
    and nobody approves what they prepared (the database refuses both: 403)."""
    bank_of(conn, session.pmc_id, bank_account_id)
    if (
        conn.execute(
            "SELECT 1 FROM trust_reconciliation_preparations WHERE id = %s"
            " AND bank_account_id = %s",
            (approval.preparation_id, bank_account_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such preparation of this trust bank account")
    try:
        reconciliation = one(
            conn,
            "SELECT trust_approve_bank_reconciliation(%s, %s)",
            (session.pmc_id, approval.preparation_id),
        )
    except psycopg.errors.CheckViolation as refused:  # prepared again, approved, changed
        raise HTTPException(409, refusal(refused)) from None
    return Created(id=reconciliation)
