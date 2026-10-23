"""Bank files (migration 20261022000156; the founder, 2026-10-23): the payouts staff recorded as
sent by EFT (owner draws and bills paid) go to the trust bank account's bank in an ACH file.

On a trust bank account's Bank files tab staff keep the account's ACH settings from its bank,
see the EFT payouts of the last 30 days not yet in a file (each with the payee's bank account on
file, by its last four digits), make a file of those they pick for a weekday, download it, and
void one the bank didn't take, which frees its payouts. The file is written each time it is
downloaded (src/pms/ach.py) from rows that never change, opening each payee's sealed account
number with the server's key. Making, voiding and downloading a file and changing the settings
take disburse. Nothing here moves money: each payout has moved in the books already.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, StringConstraints

from pms import ach, exports, sealed
from pms.api import bank_details
from pms.api.deps import Conn, Staff, download, refusal

router = APIRouter(prefix="/api/bank-files")

CENT = Decimal("0.01")
# EFT payouts recorded within these days wait for a file; older ones were sent some other way.
WAITING_DAYS = 30
NO_KEY = "this server has no key to open bank account numbers with (PMS_FIELD_KEY)"

Name = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9 .,&'/()#-]*$"),
]


def cents(value: Decimal) -> str:
    return exports.text(Decimal(value).quantize(CENT))


class Settings(BaseModel):
    bank_routing: str
    bank_name: str
    origin: str
    company_name: str
    company_id: str
    recorded_by: str | None
    recorded_at: datetime


class NewSettings(BaseModel):
    # The trust bank account's bank: its routing number and its name.
    bank_routing: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{9}$")]
    bank_name: Annotated[Name, Field(max_length=23)]
    # As the bank gives them for the company's ACH files.
    origin: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9]{9,10}$")]
    company_name: Annotated[Name, Field(max_length=16)]
    company_id: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9]{1,10}$")
    ]


class Payout(BaseModel):
    transfer_id: str
    paid_on: date
    payee: Literal["vendor", "owner"]
    payee_id: UUID
    name: str
    what: str
    amount: str
    # The payee's bank account on file, as staff see it ("Checking ••••6789"), or None.
    bank_account: str | None


class Item(BaseModel):
    name: str
    what: str
    amount: str
    bank_account: str


class Voided(BaseModel):
    reason: str
    by: str | None
    at: datetime


class BankFile(BaseModel):
    id: UUID
    made_at: datetime
    made_by: str | None
    modifier: str
    effective_on: date
    entries: int
    total: str
    voided: Voided | None
    items: list[Item]


class BankFiles(BaseModel):
    settings: Settings | None
    waiting: list[Payout]
    files: list[BankFile]


class NewFile(BaseModel):
    effective_on: date
    transfer_ids: Annotated[list[str], Field(min_length=1, max_length=1000)]


class Voiding(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


def bank_of(conn: psycopg.Connection[Any], pmc_id: UUID, bank_account_id: UUID) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM trust_bank_accounts WHERE pmc_id = %s AND id = %s",
            (pmc_id, bank_account_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such trust bank account")


SETTINGS = """
SELECT a.id, a.bank_routing, a.bank_name, a.origin, a.company_name, a.company_id,
       s.display_name, a.created_at
FROM trust_ach_settings AS a
LEFT JOIN trust_staff AS s ON s.id = a.recorded_by
WHERE a.pmc_id = %s AND a.{where} = %s
ORDER BY a.created_at DESC, a.id DESC
LIMIT 1
"""


def settings_row(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    *,
    account: UUID | None = None,
    id: UUID | None = None,
) -> tuple[UUID, Settings] | None:
    where, value = ("bank_account_id", account) if id is None else ("id", id)
    row = conn.execute(SETTINGS.format(where=where), (pmc_id, value)).fetchone()  # noqa: S608
    if row is None:
        return None
    settings_id, routing, bank, origin, company, company_id, by, at = row
    return settings_id, Settings(
        bank_routing=routing,
        bank_name=bank,
        origin=origin,
        company_name=company,
        company_id=company_id,
        recorded_by=by,
        recorded_at=at,
    )


# Each EFT payout of the account recorded since a time and in no file not voided, oldest first:
# whom it paid and what it was.
WAITING = """
SELECT m.transfer_id, p.paid_at, p.vendor_id, p.owner_id, p.amount,
       coalesce(v.display_name, o.display_name),
       CASE WHEN p.owner_id IS NOT NULL THEN 'Owner draw, ' || pr.display_name
            ELSE 'Bill ' || b.reference END
FROM trust_payout_methods AS m
CROSS JOIN LATERAL trust_payout(m.pmc_id, m.transfer_id) AS p
LEFT JOIN trust_vendors AS v ON v.pmc_id = m.pmc_id AND v.id = p.vendor_id
LEFT JOIN trust_owners AS o ON o.pmc_id = m.pmc_id AND o.id = p.owner_id
LEFT JOIN trust_owner_draws AS d ON d.pmc_id = m.pmc_id AND d.transfer_id = m.transfer_id
LEFT JOIN trust_ledger_accounts AS la
    ON la.pmc_id = d.pmc_id AND la.ledger_account_id = d.ledger_account_id
LEFT JOIN trust_properties AS pr ON pr.pmc_id = la.pmc_id AND pr.id = la.property_id
LEFT JOIN trust_bill_payments AS bp
    ON bp.pmc_id = m.pmc_id AND bp.transfer_id = m.transfer_id AND bp.step = 'paid'
LEFT JOIN trust_bills AS b ON b.pmc_id = bp.pmc_id AND b.id = bp.bill_id
WHERE m.pmc_id = %(pmc)s AND m.method = 'EFT' AND p.bank_account_id = %(bank)s
  AND m.created_at >= %(since)s
  AND NOT EXISTS (
      SELECT 1 FROM trust_ach_file_items AS i
      WHERE i.transfer_id = m.transfer_id
        AND NOT EXISTS (SELECT 1 FROM trust_ach_file_voids AS x WHERE x.file_id = i.file_id)
  )
ORDER BY p.paid_at, m.transfer_id
"""


def account_line(account: bank_details.BankAccount) -> str:
    kind = "Checking" if account.account_type == "checking" else "Savings"
    return f"{kind} ••••{account.account_last4}"


Found = tuple[Payout, tuple[bank_details.BankAccount, bytes] | None]


def waiting(conn: psycopg.Connection[Any], pmc_id: UUID, bank_account_id: UUID) -> list[Found]:
    since = datetime.now(UTC) - timedelta(days=WAITING_DAYS)
    rows = conn.execute(WAITING, {"pmc": pmc_id, "bank": bank_account_id, "since": since})
    found: list[Found] = []
    on_file: dict[tuple[str, UUID], tuple[bank_details.BankAccount, bytes] | None] = {}
    for transfer, paid_at, vendor, owner, amount, name, what in rows.fetchall():
        payee: Literal["vendor", "owner"] = "vendor" if vendor is not None else "owner"
        person = vendor if vendor is not None else owner
        if (payee, person) not in on_file:
            on_file[(payee, person)] = bank_details.latest(conn, pmc_id, payee, person)
        account = on_file[(payee, person)]
        found.append(
            (
                Payout(
                    transfer_id=transfer,
                    paid_on=paid_at.astimezone(UTC).date(),
                    payee=payee,
                    payee_id=person,
                    name=name,
                    what=what,
                    amount=cents(amount),
                    bank_account=None if account is None else account_line(account[0]),
                ),
                account,
            )
        )
    return found


FILES = """
SELECT f.id, f.created_at, s.display_name, f.modifier, f.effective_on, f.entries, f.total,
       x.reason, xs.display_name, x.created_at
FROM trust_ach_files AS f
LEFT JOIN trust_staff AS s ON s.id = f.recorded_by
LEFT JOIN trust_ach_file_voids AS x ON x.file_id = f.id
LEFT JOIN trust_staff AS xs ON xs.id = x.recorded_by
WHERE f.pmc_id = %(pmc)s AND {where}
ORDER BY f.created_at DESC, f.id DESC
LIMIT 200
"""

# A file's payouts in their order, with what the file pays into.
ITEMS = """
SELECT i.file_id, i.sequence, i.transfer_id, i.amount, b.vendor_id, b.owner_id,
       coalesce(v.display_name, o.display_name), b.holder_name, b.routing_number,
       b.account_type, b.account_last4, b.account_number_sealed,
       CASE WHEN b.owner_id IS NOT NULL THEN 'Owner draw, ' || pr.display_name
            ELSE 'Bill ' || bl.reference END,
       d.transfer_id IS NOT NULL
FROM trust_ach_file_items AS i
JOIN trust_payee_bank_accounts AS b ON b.id = i.payee_bank_account_id
LEFT JOIN trust_vendors AS v ON v.pmc_id = b.pmc_id AND v.id = b.vendor_id
LEFT JOIN trust_owners AS o ON o.pmc_id = b.pmc_id AND o.id = b.owner_id
LEFT JOIN trust_owner_draws AS d ON d.pmc_id = i.pmc_id AND d.transfer_id = i.transfer_id
LEFT JOIN trust_ledger_accounts AS la
    ON la.pmc_id = d.pmc_id AND la.ledger_account_id = d.ledger_account_id
LEFT JOIN trust_properties AS pr ON pr.pmc_id = la.pmc_id AND pr.id = la.property_id
LEFT JOIN trust_bill_payments AS bp
    ON bp.pmc_id = i.pmc_id AND bp.transfer_id = i.transfer_id AND bp.step = 'paid'
LEFT JOIN trust_bills AS bl ON bl.pmc_id = bp.pmc_id AND bl.id = bp.bill_id
WHERE i.pmc_id = %s AND i.file_id = ANY(%s)
ORDER BY i.file_id, i.sequence
"""


def files(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    *,
    bank: UUID | None = None,
    file: UUID | None = None,
) -> list[BankFile]:
    where, value = (
        ("f.bank_account_id = %(value)s", bank) if file is None else ("f.id = %(value)s", file)
    )
    rows = conn.execute(FILES.format(where=where), {"pmc": pmc_id, "value": value}).fetchall()  # noqa: S608
    items: dict[UUID, list[Item]] = {}
    for row in conn.execute(ITEMS, (pmc_id, [r[0] for r in rows])).fetchall():
        file_id, _, _, amount, _, _, name, _, _, kind, last4, _, what, _ = row
        items.setdefault(file_id, []).append(
            Item(
                name=name,
                what=what,
                amount=cents(amount),
                bank_account=f"{'Checking' if kind == 'checking' else 'Savings'} ••••{last4}",
            )
        )
    return [
        BankFile(
            id=file_id,
            made_at=made_at,
            made_by=made_by,
            modifier=modifier,
            effective_on=effective_on,
            entries=entries,
            total=cents(total),
            voided=None if reason is None else Voided(reason=reason, by=void_by, at=void_at),
            items=items.get(file_id, []),
        )
        for (
            file_id,
            made_at,
            made_by,
            modifier,
            effective_on,
            entries,
            total,
            reason,
            void_by,
            void_at,
        ) in rows
    ]


def file_text(conn: psycopg.Connection[Any], pmc_id: UUID, file_id: UUID) -> tuple[str, str]:
    """The file as the bank takes it, and its name; written again from its rows each time."""
    row = conn.execute(
        "SELECT settings_id, created_at, modifier, effective_on FROM trust_ach_files"
        " WHERE pmc_id = %s AND id = %s",
        (pmc_id, file_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such bank file")
    settings_id, made_at, modifier, effective_on = row
    found = settings_row(conn, pmc_id, id=settings_id)
    assert found is not None
    settings = found[1]
    entries = []
    for item in conn.execute(ITEMS, (pmc_id, [file_id])).fetchall():
        _, _, transfer, amount, vendor, owner, _, holder, routing, kind, _, blob, what, draw = item
        payee: Literal["vendor", "owner"] = "vendor" if vendor is not None else "owner"
        person = vendor if vendor is not None else owner
        try:
            number = sealed.open_sealed(bytes(blob), bank_details.context(pmc_id, payee, person))
        except sealed.NoKey:
            raise HTTPException(503, NO_KEY) from None
        except sealed.CannotOpen:
            raise HTTPException(
                503,
                "this server's key doesn't open the payees' bank account numbers:"
                " is it the key they were kept with?",
            ) from None
        entries.append(
            ach.Entry(
                sec="PPD" if draw else "CCD",
                routing=routing,
                account_type=kind,
                account_number=number,
                amount=amount,
                identification=what.removeprefix("Owner draw, ").removeprefix("Bill "),
                name=holder,
            )
        )
    text = ach.build(
        ach.Originator(
            bank_routing=settings.bank_routing,
            bank_name=settings.bank_name,
            origin=settings.origin,
            company_name=settings.company_name,
            company_id=settings.company_id,
        ),
        made_at.astimezone(UTC),
        modifier,
        effective_on,
        entries,
    )
    return text, f"ach-{made_at.astimezone(UTC):%Y-%m-%d}-{modifier}"


@router.get("/{bank_account_id}")
def bank_files(session: Staff, conn: Conn, bank_account_id: UUID) -> BankFiles:
    """The account's ACH settings, the EFT payouts waiting for a file and its files."""
    bank_of(conn, session.pmc_id, bank_account_id)
    found = settings_row(conn, session.pmc_id, account=bank_account_id)
    return BankFiles(
        settings=None if found is None else found[1],
        waiting=[payout for payout, _ in waiting(conn, session.pmc_id, bank_account_id)],
        files=files(conn, session.pmc_id, bank=bank_account_id),
    )


@router.post("/{bank_account_id}/settings", status_code=201)
def keep_settings(session: Staff, conn: Conn, bank_account_id: UUID, new: NewSettings) -> Settings:
    """The account's ACH details as its bank gives them, from now on."""
    bank_of(conn, session.pmc_id, bank_account_id)
    checked = conn.execute("SELECT trust_aba_routing_ok(%s)", (new.bank_routing,)).fetchone()
    if not (checked and checked[0]):
        raise HTTPException(422, "that routing number's check digit is wrong: look at it again")
    conn.execute(
        "INSERT INTO trust_ach_settings (pmc_id, bank_account_id, bank_routing, bank_name, origin,"
        " company_name, company_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            session.pmc_id,
            bank_account_id,
            new.bank_routing,
            new.bank_name,
            new.origin,
            new.company_name,
            new.company_id,
        ),
    )
    found = settings_row(conn, session.pmc_id, account=bank_account_id)
    assert found is not None
    return found[1]


@router.post("/{bank_account_id}", status_code=201)
def make_file(session: Staff, conn: Conn, bank_account_id: UUID, new: NewFile) -> BankFile:
    """A file of the EFT payouts picked, each paying into its payee's bank account on file."""
    bank_of(conn, session.pmc_id, bank_account_id)
    if len(set(new.transfer_ids)) != len(new.transfer_ids):
        raise HTTPException(422, "pick each payout once")
    found = settings_row(conn, session.pmc_id, account=bank_account_id)
    if found is None:
        raise HTTPException(409, "add this account's ACH settings from its bank first")
    if not sealed.configured():
        raise HTTPException(503, NO_KEY)
    by_id = {p.transfer_id: (p, a) for p, a in waiting(conn, session.pmc_id, bank_account_id)}
    picked = []
    for transfer in new.transfer_ids:
        if transfer not in by_id:
            raise HTTPException(409, f"{transfer} is not an EFT payout waiting for a bank file")
        payout, account = by_id[transfer]
        if account is None:
            raise HTTPException(
                409, f"{payout.name} has no bank account on file: add it on their page first"
            )
        picked.append((payout, account))
    # The file's order: owners' draws (its PPD batch), then vendors' bills (CCD), as paid.
    picked.sort(key=lambda p: p[0].payee == "vendor")
    total = sum((Decimal(p.amount) for p, _ in picked), Decimal(0))
    try:
        row = conn.execute(
            # The database sets the day it is made and its modifier.
            "INSERT INTO trust_ach_files (pmc_id, bank_account_id, settings_id, effective_on,"
            " entries, total) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (session.pmc_id, bank_account_id, found[0], new.effective_on, len(picked), total),
        ).fetchone()
        assert row is not None
        file_id = row[0]
        for sequence, (payout, _) in enumerate(picked, start=1):
            vendor = payout.payee_id if payout.payee == "vendor" else None
            owner = payout.payee_id if payout.payee == "owner" else None
            # Into the payee's latest bank account row (the database checks it isn't a removal).
            conn.execute(
                "INSERT INTO trust_ach_file_items (pmc_id, file_id, sequence, transfer_id,"
                " payee_bank_account_id, amount)"
                " SELECT %s, %s, %s, %s, b.id, %s FROM trust_payee_bank_accounts AS b"
                " WHERE b.pmc_id = %s AND (b.vendor_id = %s OR b.owner_id = %s)"
                " ORDER BY b.created_at DESC, b.id DESC LIMIT 1",
                (
                    session.pmc_id,
                    file_id,
                    sequence,
                    payout.transfer_id,
                    Decimal(payout.amount),
                    session.pmc_id,
                    vendor,
                    owner,
                ),
            )
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, refusal(refused)) from None
    (made,) = files(conn, session.pmc_id, file=file_id)
    return made


@router.get("/files/{file_id}.ach")
def download_file(session: Staff, conn: Conn, file_id: UUID) -> Response:
    """The file to upload to the bank. It carries whole account numbers, so only staff who may
    disburse download it."""
    held = conn.execute(
        "SELECT trust_staff_holds(%s, %s, 'disburse')", (session.pmc_id, session.person_id)
    ).fetchone()
    if not (held and held[0]):
        raise HTTPException(
            403, "only staff who may disburse download a bank file: it carries account numbers"
        )
    text, name = file_text(conn, session.pmc_id, file_id)
    return download(text.encode("ascii"), name, "ach")


@router.post("/files/{file_id}/void", status_code=204)
def void_file(session: Staff, conn: Conn, file_id: UUID, voiding: Voiding) -> None:
    """Sets aside a file the bank didn't take, freeing its payouts for another."""
    if not files(conn, session.pmc_id, file=file_id):
        raise HTTPException(404, "no such bank file")
    try:
        conn.execute(
            "INSERT INTO trust_ach_file_voids (pmc_id, file_id, reason) VALUES (%s, %s, %s)",
            (session.pmc_id, file_id, voiding.reason),
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "this bank file is void already") from None
