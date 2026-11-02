"""Payment files (migration 0118; the founder's review q035): a bank's or lockbox's CSV file of
the tenants' payments it received, brought in once, each line recorded as a tenant's payment to
the lease it names, as Receive payment records one (rent.record_payment): what the lease owes
first, the rest the tenant's prepaid rent, a receipt emailed when the company asks for them.

A line names its lease by its unit (and property, when units share a name), or, with no unit, by
the payer's name: a tenant on a lease in effect on its day. One that names no single lease, looks
like a payment already recorded (same lease, day and amount), or that the books refuse (a payment
hold, a closed period, an owner's account to choose) waits for staff, who record it to a lease
they pick or set it aside with why. Each line is recorded at most once: its payment's request id
comes from the line's own id, so sending it again records nothing new.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, cast
from uuid import UUID, uuid4, uuid5

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from pms import notices
from pms.api import outbox, rent, switching
from pms.api.deps import Conn, Staff, refusal
from pms.api.rentals import Page, Paged, PerPage

router = APIRouter(prefix="/api/rent/payment-files")

# A line's payment's request id: uuid5 of this and the line's id.
LINES = UUID("6d1f5f0e-3a57-4c38-9f0b-5d8f2b7c1a90")
MAX_LINES = 5000
Content = Annotated[str, StringConstraints(min_length=1, max_length=2_000_000)]
FileName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Status = Literal["to record", "recorded", "waiting", "set aside"]

# The columns a file may use for each thing, by lower-case name.
COLUMNS = {
    "property": ("property",),
    "unit": ("unit",),
    "payer": ("payer", "tenant", "name"),
    "reference": ("reference", "check number", "check no"),
    "method": ("method", "payment method"),
}
# How a bank's words for how the money came read here.
METHODS: dict[str, rent.Method] = {
    "check": "check",
    "personal check": "check",
    "cashier's check": "cashier's check",
    "cashiers check": "cashier's check",
    "certified check": "cashier's check",
    "money order": "money order",
    "eft": "EFT",
    "ach": "EFT",
    "electronic": "EFT",
    "wire": "EFT",
    "cash": "cash",
}


def us(day: date) -> str:
    return f"{day:%m/%d/%Y}"


# --- reading a file -------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    line_no: int
    received_on: date
    amount: Decimal
    property: str | None
    unit: str | None
    payer: str | None
    reference: str | None
    method: rent.Method | None


def read(content: str) -> list[Line]:
    """A file's lines, each a payment received by today; Bad names the first line that isn't."""
    found = switching.rows(content, ("date", "amount"))
    headers = set(found[0].cells) if found else set()
    picked = {
        what: next((name for name in names if name in headers), None)
        for what, names in COLUMNS.items()
    }
    if picked["unit"] is None and picked["payer"] is None:
        raise switching.Bad(
            "line 1: no unit or payer column: each line names its lease by its unit or by who paid"
        )
    if len(found) > MAX_LINES:
        raise switching.Bad(f"a payment file has at most {MAX_LINES} lines")
    lines = []
    for row in found:
        amount = row.money("amount")
        if amount <= 0:
            raise switching.Bad(
                f"line {row.line}: a payment file lists money received, not {amount}"
            )
        day = row.day("date")
        assert day is not None  # required
        if day > rent.today():
            raise switching.Bad(f"line {row.line}: {us(day)} is still to come")
        cells = {
            what: (row.get(column, required=False) or None) if column else None
            for what, column in picked.items()
        }
        method = cells["method"]
        if method is not None and method.lower() not in METHODS:
            raise switching.Bad(
                f"line {row.line}: {method!r} is not a way money comes (check, cashier's check,"
                " money order, EFT or cash)"
            )
        lines.append(
            Line(
                line_no=row.line,
                received_on=day,
                amount=amount,
                property=cells["property"],
                unit=cells["unit"],
                payer=cells["payer"],
                reference=cells["reference"],
                method=None if method is None else METHODS[method.lower()],
            )
        )
    return lines


# --- finding a line's lease -----------------------------------------------------------------

# The leases in effect on a day at the units of a name (and property, when given).
BY_UNIT = """
SELECT l.id FROM trust_leases AS l
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
WHERE l.pmc_id = %(pmc)s AND lower(btrim(u.display_name)) = lower(%(unit)s)
  AND (%(property)s::text IS NULL OR lower(btrim(p.display_name)) = lower(%(property)s))
  AND l.starts_on <= %(day)s AND (l.ends_on IS NULL OR %(day)s <= l.ends_on)
"""

# The leases in effect on a day with a tenant of a name on them who hadn't left by then.
BY_PAYER = """
SELECT DISTINCT l.id FROM trust_leases AS l
JOIN trust_lease_tenants AS lt ON lt.lease_id = l.id
JOIN trust_tenants AS t ON t.id = lt.tenant_id
WHERE l.pmc_id = %(pmc)s AND lower(btrim(t.display_name)) = lower(%(payer)s)
  AND l.starts_on <= %(day)s AND (l.ends_on IS NULL OR %(day)s <= l.ends_on)
  AND NOT EXISTS (
      SELECT 1 FROM trust_lease_departures AS d
      WHERE d.lease_id = l.id AND d.tenant_id = t.id AND d.left_on <= %(day)s
  )
"""

# A tenant's payment of an amount on a day already recorded to a lease (another key's).
RECORDED = """
SELECT 1 FROM pgledger_transfers AS t
JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id
WHERE a.pmc_id = %(pmc)s AND t.event_at = %(at)s
  AND t.metadata ->> 'kind' = 'tenant payment' AND t.metadata ->> 'lease_id' = %(lease)s
  AND t.metadata ->> 'amount' = %(amount)s AND t.metadata ->> 'key' <> %(key)s
LIMIT 1
"""


@dataclass(frozen=True)
class Found:
    lease_id: UUID | None
    # Why it waits for staff; None when it can be recorded.
    note: str | None


def find(conn: psycopg.Connection[Any], pmc_id: UUID, line: Line, line_id: UUID) -> Found:
    if line.unit is not None:
        params = {"pmc": pmc_id, "unit": line.unit, "property": line.property}
        leases = conn.execute(BY_UNIT, {**params, "day": line.received_on}).fetchall()
        where = f"{line.property} {line.unit}" if line.property else f"unit {line.unit}"
    else:
        params = {"pmc": pmc_id, "payer": line.payer}
        leases = conn.execute(BY_PAYER, {**params, "day": line.received_on}).fetchall()
        where = f"a tenant named {line.payer}"
    if not leases:
        return Found(None, f"no lease in effect on {us(line.received_on)} at {where}")
    if len(leases) > 1:
        more = "; name the property too" if line.unit is not None and not line.property else ""
        return Found(
            None, f"{len(leases)} leases in effect on {us(line.received_on)} at {where}{more}"
        )
    lease_id = cast(UUID, leases[0][0])
    already = conn.execute(
        RECORDED,
        {
            "pmc": pmc_id,
            "at": rent.at(line.received_on),
            "lease": str(lease_id),
            "amount": rent.cents(line.amount),
            "key": f"payment/{request_id(line_id)}",
        },
    ).fetchone()
    if already is not None:
        return Found(
            lease_id,
            f"a payment of ${line.amount:,.2f} on {us(line.received_on)} is recorded to this lease"
            " already",
        )
    return Found(lease_id, None)


def request_id(line_id: UUID) -> UUID:
    return uuid5(LINES, str(line_id))


def words(refused: Exception) -> str:
    """Why the books refused a line's payment, in words."""
    if isinstance(refused, HTTPException):
        return str(refused.detail)
    text = refusal(cast(psycopg.Error, refused))
    if "payment hold" in text:
        return "the lease is on a payment hold: accept the payment past the hold, or skip the line"
    return text


def record(
    conn: psycopg.Connection[Any],
    session_pmc: UUID,
    line_id: UUID,
    line: Line,
    lease_id: UUID,
    account: str | None = None,
    past_hold_by: str | None = None,
) -> rent.Recorded:
    """The line's payment to the lease, as Receive payment records one; refusals raise."""
    recorded = rent.record_payment(
        conn,
        session_pmc,
        lease_id,
        request_id(line_id),
        line.amount,
        line.received_on,
        line.reference,
        account,
        line.method,
        past_hold_by,
    )
    if outbox.SENDING.get():
        notices.write_payment_receipts(
            conn, session_pmc, lease_id, recorded.transfers[0], line.amount, line.received_on
        )
    return recorded


# --- what staff see ---------------------------------------------------------------------------


class FileLine(BaseModel):
    # None in a preview: nothing is written until the file comes in.
    id: UUID | None
    line_no: int
    received_on: date
    amount: str
    property: str | None
    unit: str | None
    payer: str | None
    reference: str | None
    method: rent.Method | None
    status: Status
    # Recorded: its lease. Otherwise the one lease the line names, if it names one.
    lease_id: UUID | None
    # Whose money and for which home: "Morgan Demo, Birch Flats Unit A".
    lease: str | None
    # Waiting: why. Set aside: the reason staff gave.
    note: str | None
    # Who recorded it (when the file came in: who brought it in) or set it aside.
    by: str | None


class Counts(BaseModel):
    lines: int
    total: str
    recorded: int
    recorded_total: str
    waiting: int
    set_aside: int


class Preview(BaseModel):
    lines: list[FileLine]
    # What would happen if it came in now.
    to_record: int
    to_record_total: str
    waiting: int
    total: str
    # When this very file came in before, if it did: it comes in once.
    came_in: datetime | None


class FileSummary(Counts):
    id: UUID
    file_name: str
    came_in: datetime
    brought_by: str | None


class FileDetail(FileSummary):
    items: list[FileLine]


class NewFile(BaseModel):
    file_name: FileName
    content: Content


def digest(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def came_in(conn: psycopg.Connection[Any], pmc_id: UUID, content: str) -> datetime | None:
    row = conn.execute(
        "SELECT created_at FROM trust_payment_files WHERE pmc_id = %s AND file_sha256 = %s",
        (pmc_id, digest(content)),
    ).fetchone()
    return None if row is None else cast(datetime, row[0])


def lines_of(content: str) -> list[Line]:
    try:
        return read(content)
    except switching.Bad as bad:
        raise HTTPException(422, str(bad)) from None


def label(conn: psycopg.Connection[Any], lease_id: UUID | None) -> str | None:
    return None if lease_id is None else rent.lease_memo(conn, lease_id)


def money(amounts: list[Decimal]) -> str:
    return rent.cents(sum(amounts, Decimal(0)))


@router.post("/preview")
def preview(session: Staff, conn: Conn, new: NewFile) -> Preview:
    """What bringing the file in would do, line by line; nothing is written."""
    lines = lines_of(new.content)
    shown = []
    for line in lines:
        found = find(conn, session.pmc_id, line, uuid4())
        shown.append(
            FileLine(
                id=None,
                line_no=line.line_no,
                received_on=line.received_on,
                amount=rent.cents(line.amount),
                property=line.property,
                unit=line.unit,
                payer=line.payer,
                reference=line.reference,
                method=line.method,
                status="waiting" if found.note else "to record",
                lease_id=found.lease_id,
                lease=label(conn, found.lease_id),
                note=found.note,
                by=None,
            )
        )
    recording = [line.amount for line, s in zip(lines, shown, strict=True) if s.note is None]
    return Preview(
        lines=shown,
        to_record=len(recording),
        to_record_total=money(recording),
        waiting=len(lines) - len(recording),
        total=money([line.amount for line in lines]),
        came_in=came_in(conn, session.pmc_id, new.content),
    )


@router.post("", status_code=201)
def bring_in(session: Staff, conn: Conn, new: NewFile) -> FileDetail:
    """Brings a file in once and records each line that names one lease the books take; the
    rest wait for staff, each with why."""
    lines = lines_of(new.content)
    before = came_in(conn, session.pmc_id, new.content)
    if before is not None:
        raise HTTPException(409, f"this file came in on {us(before.date())}; it comes in once")
    file_id = conn.execute(
        "INSERT INTO trust_payment_files (pmc_id, file_name, file_sha256, line_count)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (session.pmc_id, new.file_name, digest(new.content), len(lines)),
    ).fetchone()
    assert file_id is not None
    for line in lines:
        line_id = uuid4()
        found = find(conn, session.pmc_id, line, line_id)
        note = found.note
        if found.lease_id is not None and note is None:
            try:
                with conn.transaction():
                    record(conn, session.pmc_id, line_id, line, found.lease_id)
            except (HTTPException, psycopg.errors.CheckViolation) as refused:
                note = words(refused)
        conn.execute(
            "INSERT INTO trust_payment_file_lines (id, pmc_id, file_id, line_no, received_on,"
            " amount, property, unit, payer, reference, method, lease_id, note)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                line_id,
                session.pmc_id,
                file_id[0],
                line.line_no,
                line.received_on,
                line.amount,
                line.property,
                line.unit,
                line.payer,
                line.reference,
                line.method,
                found.lease_id,
                note,
            ),
        )
        if note is None:
            assert found.lease_id is not None
            conn.execute(
                "INSERT INTO trust_payment_file_postings (line_id, pmc_id, lease_id, request_id)"
                " VALUES (%s, %s, %s, %s)",
                (line_id, session.pmc_id, found.lease_id, request_id(line_id)),
            )
    return detail(conn, session.pmc_id, file_id[0])


# Each file of a PMC with its counts (%(pmc)s; %(file)s narrows to one).
FILES = """
SELECT f.id, f.file_name, f.created_at, s.display_name,
       count(l.id), coalesce(sum(l.amount), 0),
       count(p.line_id), coalesce(sum(l.amount) FILTER (WHERE p.line_id IS NOT NULL), 0),
       count(l.id) FILTER (WHERE p.line_id IS NULL AND k.line_id IS NULL),
       count(k.line_id),
       count(*) OVER ()
FROM trust_payment_files AS f
LEFT JOIN trust_staff AS s ON s.id = f.recorded_by
JOIN trust_payment_file_lines AS l ON l.file_id = f.id
LEFT JOIN trust_payment_file_postings AS p ON p.line_id = l.id
LEFT JOIN trust_payment_file_skips AS k ON k.line_id = l.id
WHERE f.pmc_id = %(pmc)s AND (%(file)s::uuid IS NULL OR f.id = %(file)s)
GROUP BY f.id, s.display_name
ORDER BY f.created_at DESC, f.id
LIMIT %(per_page)s OFFSET %(offset)s
"""


def summary(row: Any) -> dict[str, Any]:
    file_id, name, created, by, lines, total, recorded, recorded_total, waiting, aside, _ = row
    return {
        "id": file_id,
        "file_name": name,
        "came_in": created,
        "brought_by": by,
        "lines": lines,
        "total": rent.cents(total),
        "recorded": recorded,
        "recorded_total": rent.cents(recorded_total),
        "waiting": waiting,
        "set_aside": aside,
    }


@router.get("")
def files(session: Staff, conn: Conn, page: Page = 1, per_page: PerPage = 50) -> Paged[FileSummary]:
    """The payment files brought in, newest first."""
    asked = {"pmc": session.pmc_id, "file": None, "per_page": per_page}
    rows = conn.execute(FILES, {**asked, "offset": (page - 1) * per_page}).fetchall()
    return Paged(
        items=[FileSummary(**summary(row)) for row in rows],
        total=rows[0][-1] if rows else 0,
        page=page,
        per_page=per_page,
    )


# A file's lines, each with what became of it (%(pmc)s, %(file)s or %(line)s).
LINES_OF = """
SELECT l.id, l.line_no, l.received_on, l.amount, l.property, l.unit, l.payer, l.reference,
       l.method, coalesce(p.lease_id, l.lease_id), l.note,
       p.line_id IS NOT NULL, k.reason, coalesce(ps.display_name, ks.display_name)
FROM trust_payment_file_lines AS l
LEFT JOIN trust_payment_file_postings AS p ON p.line_id = l.id
LEFT JOIN trust_staff AS ps ON ps.id = p.recorded_by
LEFT JOIN trust_payment_file_skips AS k ON k.line_id = l.id
LEFT JOIN trust_staff AS ks ON ks.id = k.recorded_by
WHERE l.pmc_id = %(pmc)s AND (%(file)s::uuid IS NULL OR l.file_id = %(file)s)
  AND (%(line)s::uuid IS NULL OR l.id = %(line)s)
ORDER BY l.line_no
"""


def file_lines(
    conn: psycopg.Connection[Any], pmc_id: UUID, file_id: UUID | None, line_id: UUID | None
) -> list[FileLine]:
    rows = conn.execute(LINES_OF, {"pmc": pmc_id, "file": file_id, "line": line_id}).fetchall()
    shown = []
    for (
        each,
        line_no,
        received_on,
        amount,
        prop,
        unit,
        payer,
        reference,
        method,
        lease_id,
        note,
        recorded,
        reason,
        by,
    ) in rows:
        status: Status = "recorded" if recorded else "set aside" if reason else "waiting"
        shown.append(
            FileLine(
                id=each,
                line_no=line_no,
                received_on=received_on,
                amount=rent.cents(amount),
                property=prop,
                unit=unit,
                payer=payer,
                reference=reference,
                method=method,
                status=status,
                lease_id=lease_id,
                lease=label(conn, lease_id),
                note=reason if status == "set aside" else note if status == "waiting" else None,
                by=by,
            )
        )
    return shown


def detail(conn: psycopg.Connection[Any], pmc_id: UUID, file_id: UUID) -> FileDetail:
    row = conn.execute(
        FILES, {"pmc": pmc_id, "file": file_id, "per_page": 1, "offset": 0}
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such payment file")
    return FileDetail(**summary(row), items=file_lines(conn, pmc_id, file_id, None))


@router.get("/{file_id}")
def one_file(session: Staff, conn: Conn, file_id: UUID) -> FileDetail:
    """A file's lines, each recorded (to which lease), waiting (why) or set aside (why)."""
    return detail(conn, session.pmc_id, file_id)


# --- a waiting line ---------------------------------------------------------------------------


class RecordLine(BaseModel):
    lease_id: UUID
    # The owner's account it pays, when the property has more than one.
    account: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    # Accept it although the lease is on a payment hold: the PMC's choice.
    past_hold: bool = False


class SetAside(BaseModel):
    reason: Reason


def lock(conn: psycopg.Connection[Any], line_id: UUID) -> None:
    """Dealing with a line waits for whoever deals with it now (the lock its triggers take)."""
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('payment file line ' || %s, 0))",
        (str(line_id),),
    )


def waiting_line(conn: psycopg.Connection[Any], pmc_id: UUID, line_id: UUID) -> Line:
    """The line, locked, if it waits; 404 if it isn't the PMC's, 409 if it was dealt with."""
    lock(conn, line_id)
    row = conn.execute(
        "SELECT l.line_no, l.received_on, l.amount, l.property, l.unit, l.payer, l.reference,"
        " l.method, p.lease_id, k.line_id IS NOT NULL"
        " FROM trust_payment_file_lines AS l"
        " LEFT JOIN trust_payment_file_postings AS p ON p.line_id = l.id"
        " LEFT JOIN trust_payment_file_skips AS k ON k.line_id = l.id"
        " WHERE l.pmc_id = %s AND l.id = %s",
        (pmc_id, line_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such payment file line")
    *said, recorded_to, aside = row
    if recorded_to is not None:
        raise HTTPException(409, f"this line is recorded already, to {label(conn, recorded_to)}")
    if aside:
        raise HTTPException(409, "this line was skipped")
    return Line(*said)


def shown(conn: psycopg.Connection[Any], pmc_id: UUID, line_id: UUID) -> FileLine:
    return file_lines(conn, pmc_id, None, line_id)[0]


@router.post("/lines/{line_id}/record", status_code=201)
def record_line(session: Staff, conn: Conn, line_id: UUID, to: RecordLine) -> FileLine:
    """Records a waiting line's payment to the lease staff picked. The same again (a retry)
    records nothing new."""
    done = conn.execute(
        "SELECT lease_id FROM trust_payment_file_postings WHERE pmc_id = %s AND line_id = %s",
        (session.pmc_id, line_id),
    ).fetchone()
    if done is not None and done[0] == to.lease_id:
        return shown(conn, session.pmc_id, line_id)
    line = waiting_line(conn, session.pmc_id, line_id)
    hold_by = rent.staff_name(conn, session.pmc_id, session.person_id) if to.past_hold else None
    try:
        record(conn, session.pmc_id, line_id, line, to.lease_id, to.account, hold_by)
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        # a payment hold, a closed period, or a match made differently before
        raise HTTPException(409, words(refused)) from None
    conn.execute(
        "INSERT INTO trust_payment_file_postings (line_id, pmc_id, lease_id, request_id)"
        " VALUES (%s, %s, %s, %s)",
        (line_id, session.pmc_id, to.lease_id, request_id(line_id)),
    )
    return shown(conn, session.pmc_id, line_id)


@router.post("/lines/{line_id}/set-aside", status_code=201)
def set_aside(session: Staff, conn: Conn, line_id: UUID, aside: SetAside) -> FileLine:
    """Sets a waiting line aside, with why: it records nothing."""
    waiting_line(conn, session.pmc_id, line_id)
    conn.execute(
        "INSERT INTO trust_payment_file_skips (line_id, pmc_id, reason) VALUES (%s, %s, %s)",
        (line_id, session.pmc_id, aside.reason),
    )
    return shown(conn, session.pmc_id, line_id)
