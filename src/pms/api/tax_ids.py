"""Owners' and vendors' tax IDs (ADR 0010 phase 2, part F; migration 0155).

A tax ID (an EIN or an SSN) is sealed by the app with the server's key (src/pms/sealed.py) before
it is kept: the database holds the sealed bytes and the last four, to show. Every staff member
sees the last four. Only an admin sees one whole, on screen (Reveal) or in the year's 1099
export, and each time is a row of trust_tax_id_reveals naming them, which the database refuses
to anyone else. The number travels only in a request's body and an admin's answer, never in a
path or a log, and no other export holds it (the everything export keeps the sealed bytes). No
tenant or applicant is ever asked for one: the screening partner keeps those. The AI role reads
none of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, Response
from psycopg import sql
from pydantic import BaseModel, Field

from pms import exports, sealed
from pms.api.deps import Conn, Staff, download
from pms.api.staff import roles_of

router = APIRouter(prefix="/api/tax-ids")

Who = Literal["owner", "vendor"]
Kind = Literal["EIN", "SSN"]
KEEPS = "tax IDs"
NINE = re.compile(r"[0-9]{9}")
CENT = Decimal("0.01")


class TaxIdIn(BaseModel):
    kind: Kind
    # Nine digits, dashes and spaces allowed: XX-XXXXXXX or XXX-XX-XXXX. Checked by the route,
    # whose refusal never repeats it.
    number: Annotated[str, Field(min_length=1, max_length=20)]


class TaxId(BaseModel):
    id: UUID
    kind: Kind
    last4: str
    recorded_at: datetime
    recorded_by: str | None


class OnFile(BaseModel):
    tax_id: TaxId | None
    # Whether the staff member asking may see it whole (an admin).
    may_reveal: bool


class Whole(BaseModel):
    kind: Kind
    number: str


def written(kind: str, digits: str) -> str:
    """How a tax ID is written: XX-XXXXXXX for an EIN, XXX-XX-XXXX for an SSN."""
    return (
        f"{digits[:2]}-{digits[2:]}"
        if kind == "EIN"
        else f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    )


def payee_of(conn: psycopg.Connection[Any], pmc_id: UUID, who: Who, payee: UUID) -> None:
    query = sql.SQL("SELECT 1 FROM {} WHERE pmc_id = %s AND id = %s").format(
        sql.Identifier("trust_owners" if who == "owner" else "trust_vendors")
    )
    if conn.execute(query, (pmc_id, payee)).fetchone() is None:
        raise HTTPException(404, f"no such {who}")


def latest(
    conn: psycopg.Connection[Any], pmc_id: UUID, who: Who, payee: UUID
) -> tuple[TaxId, bytes] | None:
    query = sql.SQL(
        "SELECT t.id, t.kind, t.last4, t.created_at, s.display_name, t.tax_id_sealed"
        " FROM trust_tax_ids AS t LEFT JOIN trust_staff AS s ON s.id = t.recorded_by"
        " WHERE t.pmc_id = %s AND t.{} = %s ORDER BY t.created_at DESC, t.id DESC LIMIT 1"
    ).format(sql.Identifier(f"{who}_id"))
    row = conn.execute(query, (pmc_id, payee)).fetchone()
    if row is None:
        return None
    held = TaxId(id=row[0], kind=row[1], last4=row[2], recorded_at=row[3], recorded_by=row[4])
    return held, bytes(row[5])


def is_admin(conn: psycopg.Connection[Any], session: Any) -> bool:
    return "admin" in roles_of(conn, session)


def on_file(conn: psycopg.Connection[Any], session: Any, who: Who, payee: UUID) -> OnFile:
    payee_of(conn, session.pmc_id, who, payee)
    found = latest(conn, session.pmc_id, who, payee)
    return OnFile(tax_id=found[0] if found else None, may_reveal=is_admin(conn, session))


def key() -> bytes:
    try:
        return sealed.key_from(keeps=KEEPS)
    except sealed.NoKey as missing:
        raise HTTPException(409, f"{missing}; ask whoever runs it to set one") from None


def opened(row_id: UUID, sealed_id: bytes, with_key: bytes) -> str:
    try:
        return sealed.unseal(sealed_id, str(row_id), with_key)
    except sealed.Unreadable:
        raise HTTPException(409, "that tax ID doesn't open under this server's key") from None


def save(
    conn: psycopg.Connection[Any], session: Any, who: Who, payee: UUID, new: TaxIdIn
) -> OnFile:
    payee_of(conn, session.pmc_id, who, payee)
    digits = re.sub(r"[\s-]", "", new.number)
    if not NINE.fullmatch(digits):
        raise HTTPException(
            422, "a tax ID is nine digits: XX-XXXXXXX for an EIN, XXX-XX-XXXX for an SSN"
        )
    with_key = key()
    found = latest(conn, session.pmc_id, who, payee)
    if found is not None:
        held, sealed_id = found
        # The same again writes nothing.
        if (
            held.kind == new.kind
            and held.last4 == digits[-4:]
            and opened(held.id, sealed_id, with_key) == digits
        ):
            return on_file(conn, session, who, payee)
    row = conn.execute("SELECT gen_random_uuid()").fetchone()
    assert row is not None
    row_id: UUID = row[0]
    conn.execute(
        "INSERT INTO trust_tax_ids (id, pmc_id, owner_id, vendor_id, kind, tax_id_sealed, last4)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            row_id,
            session.pmc_id,
            payee if who == "owner" else None,
            payee if who == "vendor" else None,
            new.kind,
            sealed.seal(digits, str(row_id), with_key),
            digits[-4:],
        ),
    )
    return on_file(conn, session, who, payee)


@router.get("/owners/{owner_id}")
def owner_tax_id(session: Staff, conn: Conn, owner_id: UUID) -> OnFile:
    """The owner's tax ID on file: its kind and last four."""
    return on_file(conn, session, "owner", owner_id)


@router.get("/vendors/{vendor_id}")
def vendor_tax_id(session: Staff, conn: Conn, vendor_id: UUID) -> OnFile:
    """The vendor's tax ID on file: its kind and last four."""
    return on_file(conn, session, "vendor", vendor_id)


@router.post("/owners/{owner_id}", status_code=201)
def set_owner_tax_id(session: Staff, conn: Conn, owner_id: UUID, new: TaxIdIn) -> OnFile:
    """The owner's tax ID from now on, sealed before it is kept; only its last four come back.
    The same again writes nothing."""
    return save(conn, session, "owner", owner_id, new)


@router.post("/vendors/{vendor_id}", status_code=201)
def set_vendor_tax_id(session: Staff, conn: Conn, vendor_id: UUID, new: TaxIdIn) -> OnFile:
    """The vendor's tax ID from now on, kept as an owner's is."""
    return save(conn, session, "vendor", vendor_id, new)


def refuse_unless_admin(conn: psycopg.Connection[Any], session: Any, doing: str) -> None:
    if not is_admin(conn, session):
        raise HTTPException(403, f"only an admin {doing}")


def seen(conn: psycopg.Connection[Any], pmc_id: UUID, tax_id: UUID, purpose: str) -> None:
    # Names the staff member from the request (trust_name_recorder); the database refuses one
    # who isn't an admin.
    conn.execute(
        "INSERT INTO trust_tax_id_reveals (pmc_id, tax_id, purpose) VALUES (%s, %s, %s)",
        (pmc_id, tax_id, purpose),
    )


@router.get("/{tax_id}/whole")
def reveal(session: Staff, conn: Conn, tax_id: UUID) -> Whole:
    """A tax ID whole, for an admin: each time is recorded, naming them."""
    refuse_unless_admin(conn, session, "sees a whole tax ID")
    row = conn.execute(
        "SELECT kind, tax_id_sealed FROM trust_tax_ids WHERE pmc_id = %s AND id = %s",
        (session.pmc_id, tax_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such tax ID")
    digits = opened(tax_id, bytes(row[1]), key())
    seen(conn, session.pmc_id, tax_id, "reveal")
    return Whole(kind=row[0], number=written(row[0], digits))


# --- the year's 1099 export --------------------------------------------------------------------


@dataclass(frozen=True)
class Payee:
    who: Who
    payee_id: UUID
    name: str
    form: str
    amount: Decimal


def threshold(conn: psycopg.Connection[Any], pmc_id: UUID, year: int) -> Decimal:
    """The IRS's threshold for the year, as the vendor payments report gives it."""
    table = exports.run_report(conn, "trust_report_vendor_payments", [pmc_id, year])
    found = next(row[4] for row in table.rows if row[1] == "threshold")
    return Decimal(found)


# What each vendor was paid in the year, as trust_report_vendor_payments counts it (bills paid,
# and what the old system paid before the cutover), for those whose latest profile marks them
# for a 1099.
VENDORS_PAID = """
WITH here AS (
    SELECT b.vendor_id, sum(tr.amount) AS total
    FROM trust_bill_payments AS p
    JOIN trust_bills AS b ON b.id = p.bill_id
    JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
    WHERE p.pmc_id = %(pmc)s AND p.step = 'paid'
      AND tr.event_at >= %(start)s AND tr.event_at < %(end)s
    GROUP BY b.vendor_id
),
profiles AS (
    SELECT DISTINCT ON (pr.vendor_id) pr.vendor_id, pr.gets_1099
    FROM trust_vendor_profiles AS pr
    WHERE pr.pmc_id = %(pmc)s
    ORDER BY pr.vendor_id, pr.created_at DESC, pr.id DESC
)
SELECT v.id, v.display_name, coalesce(h.total, 0) + coalesce(c.amount, 0)
FROM trust_vendors AS v
JOIN profiles AS pf ON pf.vendor_id = v.id AND pf.gets_1099
LEFT JOIN here AS h ON h.vendor_id = v.id
LEFT JOIN trust_vendor_payments_carried_over AS c
       ON c.vendor_id = v.id AND c.pmc_id = %(pmc)s AND c.year = %(year)s
WHERE v.pmc_id = %(pmc)s AND (h.vendor_id IS NOT NULL OR c.vendor_id IS NOT NULL)
ORDER BY v.display_name COLLATE "C", v.id
"""


def payees(conn: psycopg.Connection[Any], pmc_id: UUID, year: int) -> list[Payee]:
    """Each payee the year's 1099s are for: vendors marked for one and paid the threshold or more,
    and owners whose rents collected (1099-MISC box 1) came to it."""
    at_least = threshold(conn, pmc_id, year)
    start = datetime.fromisoformat(f"{year}-01-01T00:00:00+00:00")
    end = datetime.fromisoformat(f"{year + 1}-01-01T00:00:00+00:00")
    found = [
        Payee("vendor", vendor_id, name, "1099-NEC or 1099-MISC", Decimal(total).quantize(CENT))
        for vendor_id, name, total in conn.execute(
            VENDORS_PAID, {"pmc": pmc_id, "year": year, "start": start, "end": end}
        ).fetchall()
        if total >= at_least
    ]
    owners = conn.execute(
        "SELECT o.id, o.display_name FROM trust_owners AS o WHERE o.pmc_id = %s"
        ' ORDER BY o.display_name COLLATE "C", o.id',
        (pmc_id,),
    ).fetchall()
    for owner_id, name in owners:
        table = exports.run_report(conn, "trust_report_owner_year_end", [pmc_id, owner_id, year])
        rents = next(
            (row[3] for row in table.rows if row[2] == "1099-MISC rents (box 1)"), Decimal(0)
        )
        if rents >= at_least:
            found.append(Payee("owner", owner_id, name, "1099-MISC box 1 (rents)", rents))
    return found


COLUMNS_1099 = ("payee_type", "payee", "form", "amount", "tin_type", "tin", "note")


@router.get("/1099")
def export_1099(
    session: Staff, conn: Conn, year: Annotated[int, Query(ge=1900, le=9998)]
) -> Response:
    """The year's 1099s as CSV, for an admin to give whoever files them: each payee, the form,
    the amount and their tax ID whole (or that none is on file). Every tax ID in it is recorded
    as seen by them. The PMS files nothing."""
    refuse_unless_admin(conn, session, "exports the year's 1099s")
    with_key: bytes | None = None
    rows: list[tuple[Any, ...]] = []
    for payee in payees(conn, session.pmc_id, year):
        found = latest(conn, session.pmc_id, payee.who, payee.payee_id)
        kind: str | None = None
        number: str | None = None
        note: str | None = "no tax ID on file: ask them for a W-9"
        if found is not None:
            held, sealed_id = found
            with_key = with_key or key()
            number = written(held.kind, opened(held.id, sealed_id, with_key))
            kind, note = held.kind, None
            seen(conn, session.pmc_id, held.id, "1099 export")
        rows.append((payee.who, payee.name, payee.form, payee.amount, kind, number, note))
    table = exports.Table(f"1099s {year}", COLUMNS_1099, tuple(rows))
    return download(exports.to_csv(table), f"1099s-{year}", "csv")
