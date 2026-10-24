"""Paying owners and vendors (ADR 0010 phase 1b; ADR 0009): the company's draw day and the draws
the nightly run prepares on it, how each owner is paid, owners' and vendors' bank accounts for
ACH, what each trust bank account's bank asks in an ACH file and the first number of its checks,
and the files of payouts recorded together: an ACH file (NACHA) for the company to upload to its
own bank, or the checks to print on its pre-printed stock. Pay owners and Pay bills
(src/pms/api/payables.py) record the payouts and make the runs these files are of. The PMS never
moves money: it records what was paid and writes the file the bank acts on.

A bank account number is sealed by the app with the server's key (src/pms/sealed.py); only its
last four come back. It is opened only to write an ACH file, which takes the disburse role, and
each file taken is logged with who took it. The AI role reads none of it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field, StringConstraints, model_validator

from pms import sealed
from pms.api.deps import Conn, Staff, download
from pms.payouts import checks, nacha

router = APIRouter(prefix="/api/payouts")

PayoutMethod = Literal["check", "EFT"]
Kind = Literal["owners", "bills"]
Routing = Annotated[str, StringConstraints(pattern=r"^[0-9]{9}$")]
BankText = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9 .,&'-]+$")]
Id10 = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9 ]{10}$")]
CENT = Decimal("0.01")


def money(value: Decimal) -> str:
    return f"{Decimal(value).quantize(CENT)}"


def routing_ok(number: str) -> bool:
    d = [int(c) for c in number]
    return (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + d[2] + d[5] + d[8]) % 10 == 0


def refuse_routing(number: str | None) -> None:
    if number is not None and not routing_ok(number):
        raise HTTPException(422, "that routing number's last digit doesn't check: look at it again")


# --- the draw day ------------------------------------------------------------------------------


class DrawDay(BaseModel):
    # The day of the month the nightly run prepares every owner's draw; none: no draw day.
    draw_day: Annotated[int, Field(ge=1, le=28)] | None


class DrawDaySet(DrawDay):
    changed_at: datetime | None
    changed_by: str | None


def draw_day_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> DrawDaySet:
    row = conn.execute(
        "SELECT s.draw_day, s.created_at, st.display_name FROM trust_owner_draw_settings AS s"
        " LEFT JOIN trust_staff AS st ON st.id = s.recorded_by"
        " WHERE s.pmc_id = %s ORDER BY s.created_at DESC, s.id DESC LIMIT 1",
        (pmc_id,),
    ).fetchone()
    if row is None:
        return DrawDaySet(draw_day=None, changed_at=None, changed_by=None)
    return DrawDaySet(draw_day=row[0], changed_at=row[1], changed_by=row[2])


@router.get("/draw-day")
def draw_day(session: Staff, conn: Conn) -> DrawDaySet:
    """The company's draw day, and when it was last changed and by whom."""
    return draw_day_of(conn, session.pmc_id)


@router.post("/draw-day")
def set_draw_day(session: Staff, conn: Conn, wanted: DrawDay) -> DrawDaySet:
    """Sets the draw day (1 to 28), or none. Saving what holds already writes nothing."""
    if draw_day_of(conn, session.pmc_id).draw_day != wanted.draw_day:
        conn.execute(
            "INSERT INTO trust_owner_draw_settings (pmc_id, draw_day) VALUES (%s, %s)",
            (session.pmc_id, wanted.draw_day),
        )
    return draw_day_of(conn, session.pmc_id)


# --- payees: how an owner is paid, and an owner's or vendor's bank account --------------------


class BankAccountIn(BaseModel):
    routing_number: Routing
    account_number: Annotated[str, StringConstraints(pattern=r"^[0-9]{4,17}$")]
    account_type: Literal["checking", "savings"]
    holder_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]


class BankShown(BaseModel):
    routing_number: str
    account_type: str
    # Only the last four of the account number ever come back.
    last4: str
    holder_name: str
    recorded_at: datetime
    recorded_by: str | None


class Payee(BaseModel):
    # How an owner is paid (a check unless they chose ACH); a vendor's is on their profile.
    method: PayoutMethod | None
    bank: BankShown | None


Who = Literal["owner", "vendor"]


def bank_of(
    conn: psycopg.Connection[Any], pmc_id: UUID, who: Who, payee: UUID, by: datetime | None = None
) -> tuple[UUID, str, bool, bytes, str, BankShown] | None:
    """The payee's bank account (the latest recorded by a moment, if given): its row's id,
    routing number, whether it is savings, the sealed number, the name on it, and what shows."""
    row = conn.execute(
        "SELECT b.id, b.routing_number, b.account_type, b.account_sealed, b.account_last4,"  # noqa: S608
        " b.holder_name, b.created_at, st.display_name FROM trust_payee_bank_accounts AS b"
        " LEFT JOIN trust_staff AS st ON st.id = b.recorded_by"
        f" WHERE b.pmc_id = %s AND b.{who}_id = %s"
        " AND (%s::timestamptz IS NULL OR b.created_at <= %s)"
        " ORDER BY b.created_at DESC, b.id DESC LIMIT 1",
        (pmc_id, payee, by, by),
    ).fetchone()
    if row is None:
        return None
    shown = BankShown(
        routing_number=row[1],
        account_type=row[2],
        last4=row[4],
        holder_name=row[5],
        recorded_at=row[6],
        recorded_by=row[7],
    )
    return row[0], row[1], row[2] == "savings", bytes(row[3]), row[5], shown


def owner_method(conn: psycopg.Connection[Any], pmc_id: UUID, owner: UUID) -> PayoutMethod:
    row = conn.execute(
        "SELECT method FROM trust_owner_payout_methods WHERE pmc_id = %s AND owner_id = %s"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (pmc_id, owner),
    ).fetchone()
    return "EFT" if row and row[0] == "EFT" else "check"


def payee_of(conn: psycopg.Connection[Any], pmc_id: UUID, who: Who, payee: UUID) -> None:
    if not conn.execute(
        f"SELECT 1 FROM trust_{who}s WHERE pmc_id = %s AND id = %s",  # noqa: S608 (who is owner or vendor)
        (pmc_id, payee),
    ).fetchone():
        raise HTTPException(404, f"no such {who}")


@router.get("/owners/{owner_id}")
def owner_payee(session: Staff, conn: Conn, owner_id: UUID) -> Payee:
    """How the owner is paid and the bank account their ACH goes to (its last four only)."""
    payee_of(conn, session.pmc_id, "owner", owner_id)
    found = bank_of(conn, session.pmc_id, "owner", owner_id)
    return Payee(
        method=owner_method(conn, session.pmc_id, owner_id), bank=found[5] if found else None
    )


@router.get("/vendors/{vendor_id}")
def vendor_payee(session: Staff, conn: Conn, vendor_id: UUID) -> Payee:
    """The bank account a vendor's ACH goes to (its last four only)."""
    payee_of(conn, session.pmc_id, "vendor", vendor_id)
    found = bank_of(conn, session.pmc_id, "vendor", vendor_id)
    return Payee(method=None, bank=found[5] if found else None)


class MethodIn(BaseModel):
    method: PayoutMethod


@router.post("/owners/{owner_id}/method")
def set_owner_method(session: Staff, conn: Conn, owner_id: UUID, wanted: MethodIn) -> Payee:
    """How the owner is paid: ACH or check. Saving what holds already writes nothing."""
    payee_of(conn, session.pmc_id, "owner", owner_id)
    if owner_method(conn, session.pmc_id, owner_id) != wanted.method:
        conn.execute(
            "INSERT INTO trust_owner_payout_methods (pmc_id, owner_id, method) VALUES (%s, %s, %s)",
            (session.pmc_id, owner_id, wanted.method),
        )
    return owner_payee(session, conn, owner_id)


def save_bank(
    conn: psycopg.Connection[Any], pmc_id: UUID, who: Who, payee: UUID, new: BankAccountIn
) -> None:
    payee_of(conn, pmc_id, who, payee)
    refuse_routing(new.routing_number)
    try:
        key = sealed.key_from()
    except sealed.NoKey as missing:
        raise HTTPException(409, f"{missing}; ask whoever runs it to set one") from None
    row = conn.execute("SELECT gen_random_uuid()").fetchone()
    assert row is not None
    row_id: UUID = row[0]
    conn.execute(
        "INSERT INTO trust_payee_bank_accounts (id, pmc_id, owner_id, vendor_id, routing_number,"
        " account_type, account_sealed, account_last4, holder_name)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            row_id,
            pmc_id,
            payee if who == "owner" else None,
            payee if who == "vendor" else None,
            new.routing_number,
            new.account_type,
            sealed.seal(new.account_number, str(row_id), key),
            new.account_number[-4:],
            new.holder_name,
        ),
    )


@router.post("/owners/{owner_id}/bank-account", status_code=201)
def set_owner_bank(session: Staff, conn: Conn, owner_id: UUID, new: BankAccountIn) -> Payee:
    """The bank account the owner's ACH goes to, from now on: its number is sealed before it is
    kept, and only its last four come back."""
    save_bank(conn, session.pmc_id, "owner", owner_id, new)
    return owner_payee(session, conn, owner_id)


@router.post("/vendors/{vendor_id}/bank-account", status_code=201)
def set_vendor_bank(session: Staff, conn: Conn, vendor_id: UUID, new: BankAccountIn) -> Payee:
    """The bank account a vendor's ACH goes to, from now on, kept as an owner's is."""
    save_bank(conn, session.pmc_id, "vendor", vendor_id, new)
    return vendor_payee(session, conn, vendor_id)


# --- what a trust bank account's bank asks -----------------------------------------------------


class BankSettingsIn(BaseModel):
    # What the bank gives the company for its ACH files: all of them, or none.
    bank_routing_number: Routing | None = None
    bank_name: Annotated[BankText, StringConstraints(max_length=23)] | None = None
    origin_id: Id10 | None = None
    origin_name: Annotated[BankText, StringConstraints(max_length=23)] | None = None
    company_id: Id10 | None = None
    company_name: Annotated[BankText, StringConstraints(max_length=16)] | None = None
    # The number of the first check the PMS prints from this account.
    first_check_number: Annotated[int, Field(ge=1, le=999_999_999_999_999)] | None = None

    @model_validator(mode="after")
    def whole(self) -> BankSettingsIn:
        given = [
            self.bank_routing_number,
            self.bank_name,
            self.origin_id,
            self.origin_name,
            self.company_id,
            self.company_name,
        ]
        if any(given) and not all(given):
            raise ValueError("an ACH file needs every one of the bank's details, or none")
        return self


class BankSettings(BankSettingsIn):
    bank_account_id: UUID
    # Whether ACH files can be made from this account.
    ach_ready: bool
    # The number the next printed check takes.
    next_check_number: int
    changed_at: datetime | None
    changed_by: str | None


SETTING_COLUMNS = (
    "bank_routing_number",
    "bank_name",
    "origin_id",
    "origin_name",
    "company_id",
    "company_name",
    "first_check_number",
)


def bank_account_of(conn: psycopg.Connection[Any], pmc_id: UUID, bank: UUID) -> None:
    if not conn.execute(
        "SELECT 1 FROM trust_bank_accounts WHERE pmc_id = %s AND id = %s", (pmc_id, bank)
    ).fetchone():
        raise HTTPException(404, "no such trust bank account")


def next_check(conn: psycopg.Connection[Any], pmc_id: UUID, bank: UUID) -> int:
    """The next check's number: one past the highest recorded on a payout from the account, and
    no lower than the first number its settings give (1 without one)."""
    row = conn.execute(
        "SELECT greatest("
        " coalesce((SELECT s.first_check_number FROM trust_bank_payout_settings AS s"
        "  WHERE s.pmc_id = %(pmc)s AND s.bank_account_id = %(bank)s"
        "  ORDER BY s.created_at DESC, s.id DESC LIMIT 1), 1),"
        " coalesce((SELECT max(m.check_number::bigint) + 1 FROM trust_payout_methods AS m"
        "  JOIN pgledger_transfers AS t ON t.id = m.transfer_id"
        "  JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id"
        "  WHERE m.pmc_id = %(pmc)s AND a.kind = 'bank_cash' AND a.bank_account_id = %(bank)s"
        "  AND m.check_number IS NOT NULL), 1))",
        {"pmc": pmc_id, "bank": bank},
    ).fetchone()
    assert row is not None
    return int(row[0])


def bank_settings_of(conn: psycopg.Connection[Any], pmc_id: UUID, bank: UUID) -> BankSettings:
    row = conn.execute(
        "SELECT " + ", ".join(f"s.{c}" for c in SETTING_COLUMNS) + ", s.created_at,"  # noqa: S608
        " st.display_name FROM trust_bank_payout_settings AS s"
        " LEFT JOIN trust_staff AS st ON st.id = s.recorded_by"
        " WHERE s.pmc_id = %s AND s.bank_account_id = %s"
        " ORDER BY s.created_at DESC, s.id DESC LIMIT 1",
        (pmc_id, bank),
    ).fetchone()
    values: dict[str, Any] = dict(zip(SETTING_COLUMNS, row[:7], strict=True)) if row else {}
    return BankSettings(
        **values,
        bank_account_id=bank,
        ach_ready=bool(row and row[0]),
        next_check_number=next_check(conn, pmc_id, bank),
        changed_at=row[7] if row else None,
        changed_by=row[8] if row else None,
    )


@router.get("/bank-accounts/{bank_account_id}")
def bank_settings(session: Staff, conn: Conn, bank_account_id: UUID) -> BankSettings:
    """What the account's bank asks in an ACH file, and the number of its next check."""
    bank_account_of(conn, session.pmc_id, bank_account_id)
    return bank_settings_of(conn, session.pmc_id, bank_account_id)


@router.post("/bank-accounts/{bank_account_id}")
def set_bank_settings(
    session: Staff, conn: Conn, bank_account_id: UUID, wanted: BankSettingsIn
) -> BankSettings:
    """Keeps what the bank gave the company for ACH files and the first check number. Saving
    what holds already writes nothing."""
    bank_account_of(conn, session.pmc_id, bank_account_id)
    refuse_routing(wanted.bank_routing_number)
    now = bank_settings_of(conn, session.pmc_id, bank_account_id)
    if any(getattr(now, c) != getattr(wanted, c) for c in SETTING_COLUMNS):
        conn.execute(
            "INSERT INTO trust_bank_payout_settings (pmc_id, bank_account_id, "  # noqa: S608
            + ", ".join(SETTING_COLUMNS)
            + ") VALUES (%s, %s, "
            + ", ".join(["%s"] * len(SETTING_COLUMNS))
            + ")",
            (session.pmc_id, bank_account_id, *(getattr(wanted, c) for c in SETTING_COLUMNS)),
        )
    return bank_settings_of(conn, session.pmc_id, bank_account_id)


# --- runs: made by Pay owners and Pay bills ---------------------------------------------------


@dataclass(frozen=True)
class Payout:
    """A payout about to be recorded: from which trust bank account, to whom, and how."""

    bank: UUID
    method: PayoutMethod
    owner_id: UUID | None
    vendor_id: UUID | None
    name: str


def ready(conn: psycopg.Connection[Any], pmc_id: UUID, payouts: list[Payout]) -> None:
    """Refuses, before any money is recorded, payouts whose file can't be made: an ACH file
    needs the server's key, its bank's details and every payee's bank account."""
    by_ach = [p for p in payouts if p.method == "EFT"]
    if not by_ach:
        return
    try:
        sealed.key_from()
    except sealed.NoKey as missing:
        raise HTTPException(409, f"{missing}, so it makes no ACH file") from None
    for bank in sorted({p.bank for p in by_ach}, key=str):
        if not bank_settings_of(conn, pmc_id, bank).ach_ready:
            name = conn.execute(
                "SELECT display_name FROM trust_bank_accounts WHERE id = %s", (bank,)
            ).fetchone()
            raise HTTPException(
                409,
                f"{name[0] if name else 'This trust bank account'} has no ACH details from its "
                "bank yet: add them on its Payouts tab first",
            )
    lacking = sorted(
        {
            p.name
            for p in by_ach
            if bank_of(
                conn,
                pmc_id,
                "owner" if p.owner_id else "vendor",
                p.owner_id or p.vendor_id,  # type: ignore[arg-type]
            )
            is None
        }
    )
    if lacking:
        raise HTTPException(
            409, f"no bank account on file for {', '.join(lacking)}: add it or pay by check"
        )


def checks_for(
    conn: psycopg.Connection[Any], pmc_id: UUID, payouts: list[Payout]
) -> dict[Any, str]:
    """A check number for each payee paid by printed check, by (bank, payee): one check each,
    numbered on from each account's next. Two runs at once wait for each other."""
    numbers: dict[Any, str] = {}
    next_of: dict[UUID, int] = {}
    for p in payouts:
        if p.method != "check":
            continue
        key = (p.bank, p.owner_id or p.vendor_id)
        if key in numbers:
            continue
        if p.bank not in next_of:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"checks:{p.bank}",)
            )
            next_of[p.bank] = next_check(conn, pmc_id, p.bank)
        numbers[key] = str(next_of[p.bank])
        next_of[p.bank] += 1
    return numbers


def record(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    kind: Kind,
    request_id: UUID,
    paid_on: date,
    payout: Payout,
    transfer_id: str,
) -> UUID:
    """Puts a payout's transfer in its run (made on the first), once; the run's id."""
    conn.execute(
        "INSERT INTO trust_payout_runs (pmc_id, bank_account_id, kind, method, paid_on,"
        " request_id) VALUES (%s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (pmc_id, request_id, bank_account_id, method) DO NOTHING",
        (pmc_id, payout.bank, kind, payout.method, paid_on, request_id),
    )
    row = conn.execute(
        "SELECT id FROM trust_payout_runs WHERE pmc_id = %s AND request_id = %s"
        " AND bank_account_id = %s AND method = %s",
        (pmc_id, request_id, payout.bank, payout.method),
    ).fetchone()
    assert row is not None
    conn.execute(
        "INSERT INTO trust_payout_run_items (run_id, pmc_id, transfer_id, owner_id, vendor_id)"
        " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (transfer_id) DO NOTHING",
        (row[0], pmc_id, transfer_id, payout.owner_id, payout.vendor_id),
    )
    run: UUID = row[0]
    return run


class RunShown(BaseModel):
    id: UUID
    kind: Kind
    # 'EFT': an ACH file; 'check': checks to print.
    method: PayoutMethod
    bank_account_id: UUID
    bank_account: str
    paid_on: date
    payouts: int
    total: str
    recorded_by: str | None
    recorded_at: datetime
    # How many times its file was taken.
    downloads: int


RUNS = """
SELECT r.id, r.kind, r.method, r.bank_account_id, b.display_name, r.paid_on,
       count(i.transfer_id), coalesce(sum(t.amount), 0), st.display_name, r.created_at,
       (SELECT count(*) FROM trust_payout_file_downloads AS d WHERE d.run_id = r.id)
FROM trust_payout_runs AS r
JOIN trust_bank_accounts AS b ON b.id = r.bank_account_id
LEFT JOIN trust_payout_run_items AS i ON i.run_id = r.id
LEFT JOIN pgledger_transfers AS t ON t.id = i.transfer_id
LEFT JOIN trust_staff AS st ON st.id = r.recorded_by
WHERE r.pmc_id = %(pmc)s AND (%(kind)s::text IS NULL OR r.kind = %(kind)s)
  AND (%(run)s::uuid IS NULL OR r.id = %(run)s)
GROUP BY r.id, b.display_name, st.display_name
ORDER BY r.created_at DESC, r.id DESC
LIMIT 20
"""


def runs_of(
    conn: psycopg.Connection[Any], pmc_id: UUID, kind: Kind | None = None, run: UUID | None = None
) -> list[RunShown]:
    return [
        RunShown(
            id=row[0],
            kind=row[1],
            method=row[2],
            bank_account_id=row[3],
            bank_account=row[4],
            paid_on=row[5],
            payouts=row[6],
            total=money(row[7]),
            recorded_by=row[8],
            recorded_at=row[9],
            downloads=row[10],
        )
        for row in conn.execute(RUNS, {"pmc": pmc_id, "kind": kind, "run": run}).fetchall()
    ]


@router.get("/runs")
def runs(session: Staff, conn: Conn, kind: Kind | None = None) -> list[RunShown]:
    """The latest runs that made a payout file, newest first."""
    return runs_of(conn, session.pmc_id, kind)


@dataclass(frozen=True)
class Item:
    owner_id: UUID | None
    vendor_id: UUID | None
    name: str
    amount: Decimal
    check_number: str | None
    # What it pays: "Owner draw, Elm Court" or "Bill PLB-1001".
    detail: str
    reference: str


ITEMS = """
SELECT i.owner_id, i.vendor_id, coalesce(o.display_name, v.display_name), t.amount,
       m.check_number, pr.display_name, bl.reference
FROM trust_payout_run_items AS i
JOIN pgledger_transfers AS t ON t.id = i.transfer_id
LEFT JOIN trust_payout_methods AS m ON m.transfer_id = i.transfer_id
LEFT JOIN trust_owners AS o ON o.id = i.owner_id
LEFT JOIN trust_vendors AS v ON v.id = i.vendor_id
LEFT JOIN trust_owner_draws AS d ON d.transfer_id = i.transfer_id
LEFT JOIN trust_ledger_accounts AS a ON a.ledger_account_id = d.ledger_account_id
LEFT JOIN trust_properties AS pr ON pr.id = a.property_id
LEFT JOIN trust_bill_payments AS p ON p.transfer_id = i.transfer_id AND p.step = 'paid'
LEFT JOIN trust_bills AS bl ON bl.id = p.bill_id
WHERE i.run_id = %s
ORDER BY coalesce(o.display_name, v.display_name) COLLATE "C", t.id
"""


def items_of(conn: psycopg.Connection[Any], run: UUID) -> list[Item]:
    return [
        Item(
            owner_id=row[0],
            vendor_id=row[1],
            name=row[2],
            amount=Decimal(row[3]),
            check_number=row[4],
            detail=f"Owner draw, {row[5]}" if row[0] else f"Bill {row[6]}",
            reference="OWNER DRAW" if row[0] else row[6],
        )
        for row in conn.execute(ITEMS, (run,)).fetchall()
    ]


def take_file(
    conn: psycopg.Connection[Any], session: Any, run_id: UUID, method: PayoutMethod
) -> RunShown:
    """The run, once staff who may pay out money ask for its file; logs who took it."""
    found = runs_of(conn, session.pmc_id, run=run_id)
    if not found:
        raise HTTPException(404, "no such payout run")
    run = found[0]
    if run.method != method:
        raise HTTPException(
            409,
            "this run was paid by check: print its checks"
            if method == "EFT"
            else "this run was paid by ACH: take its ACH file",
        )
    holds = conn.execute(
        "SELECT trust_staff_holds(%s, %s, 'disburse')", (session.pmc_id, session.person_id)
    ).fetchone()
    if not (holds and holds[0]):
        raise HTTPException(
            403, "only staff who may pay out money (the disburse role) take a payout file"
        )
    conn.execute(
        "INSERT INTO trust_payout_file_downloads (pmc_id, run_id, file) VALUES (%s, %s, %s)",
        (session.pmc_id, run_id, "ach" if method == "EFT" else "checks"),
    )
    return run


@router.get("/runs/{run_id}/ach")
def ach_file(session: Staff, conn: Conn, run_id: UUID) -> Response:
    """The run's ACH file (NACHA) for the company to upload to its bank: one credit to each
    payee's bank account, their payouts in the run summed."""
    run = take_file(conn, session, run_id, "EFT")
    settings = bank_settings_of(conn, session.pmc_id, run.bank_account_id)
    try:
        key = sealed.key_from()
    except sealed.NoKey as missing:
        raise HTTPException(409, f"{missing}, so it makes no ACH file") from None
    created = conn.execute(
        "SELECT created_at FROM trust_payout_runs WHERE id = %s", (run_id,)
    ).fetchone()
    assert created is not None and settings.ach_ready
    summed: dict[Any, list[Item]] = defaultdict(list)
    for item in items_of(conn, run_id):
        summed[item.owner_id or item.vendor_id].append(item)
    entries = []
    for payee, items in summed.items():
        who: Who = "owner" if items[0].owner_id else "vendor"
        found = bank_of(conn, session.pmc_id, who, payee, by=created[0])
        if found is None:
            raise HTTPException(409, f"no bank account on file for {items[0].name}")
        row_id, routing, savings, sealed_number, holder, _ = found
        try:
            account = sealed.unseal(sealed_number, str(row_id), key)
        except sealed.Unreadable:
            raise HTTPException(
                409, f"{items[0].name}'s account number doesn't open under this server's key"
            ) from None
        entries.append(
            nacha.Entry(
                routing_number=routing,
                account_number=account,
                savings=savings,
                amount=sum((i.amount for i in items), Decimal(0)),
                name=holder,
                reference=items[0].reference if len(items) == 1 else f"{len(items)} PAYMENTS",
            )
        )
    origin = nacha.Origin(
        bank_routing_number=settings.bank_routing_number or "",
        bank_name=settings.bank_name or "",
        origin_id=settings.origin_id or "",
        origin_name=settings.origin_name or "",
        company_id=settings.company_id or "",
        company_name=settings.company_name or "",
    )
    batch = nacha.Batch(
        sec_code="PPD" if run.kind == "owners" else "CCD",
        description="OWNER DRAW" if run.kind == "owners" else "VENDOR PAY",
        effective_on=run.paid_on,
        entries=entries,
    )
    text = nacha.write(origin, [batch], created[0])
    return download(text.encode("ascii"), f"ach-{run.paid_on}-{str(run_id)[:8]}", "ach")


ADDRESS = """
SELECT c.address_line_1, c.address_line_2, c.city, c.state, c.postal_code
FROM trust_contacts AS c
WHERE c.pmc_id = %s AND (c.owner_id = %s OR c.vendor_id = %s)
ORDER BY c.created_at DESC, c.id DESC LIMIT 1
"""


@router.get("/runs/{run_id}/checks")
def checks_file(session: Staff, conn: Conn, run_id: UUID) -> Response:
    """The run's checks, one a page, to print on the company's pre-printed stock: one to each
    payee, its stubs listing what it pays."""
    run = take_file(conn, session, run_id, "check")
    company = conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (session.pmc_id,)
    ).fetchone()
    grouped: dict[Any, list[Item]] = defaultdict(list)
    for item in items_of(conn, run_id):
        grouped[(item.check_number, item.owner_id or item.vendor_id)].append(item)
    printed = []
    for (number, payee), items in sorted(grouped.items(), key=lambda g: int(g[0][0] or 0)):
        address = conn.execute(ADDRESS, (session.pmc_id, payee, payee)).fetchone()
        lines = []
        if address and address[0]:
            lines = [address[0], *([address[1]] if address[1] else [])]
            lines.append(f"{address[2]}, {address[3]} {address[4]}")
        printed.append(
            checks.Check(
                number=number or "",
                on=run.paid_on,
                payee=items[0].name,
                amount=sum((i.amount for i in items), Decimal(0)),
                memo=items[0].detail if len(items) == 1 else f"{len(items)} payments",
                address=lines,
                lines=[f"{i.detail}: ${i.amount:,.2f}" for i in items],
            )
        )
    pdf = checks.write(company[0] if company else "", printed)
    return download(pdf, f"checks-{run.paid_on}-{str(run_id)[:8]}", "pdf")


# --- prepared draws ---------------------------------------------------------------------------


class DrawLine(BaseModel):
    account: str
    owner_id: UUID
    owner: str
    property: str
    # What it had available on the day the draws were prepared, and has now.
    amount: str
    available_now: str
    method: PayoutMethod


class DrawRun(BaseModel):
    id: UUID
    month: date
    prepared_on: date
    lines: list[DrawLine]
    total: str


@router.get("/draw-runs/waiting")
def waiting_draws(session: Staff, conn: Conn) -> DrawRun | None:
    """The draws the nightly run prepared on the draw day and nobody has paid yet, if any."""
    return waiting_draws_of(conn, session.pmc_id)


def waiting_draws_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> DrawRun | None:
    row = conn.execute(
        "SELECT r.id, r.month, r.prepared_on FROM trust_draw_runs AS r"
        " WHERE r.pmc_id = %s AND NOT EXISTS ("
        "  SELECT 1 FROM trust_draw_run_approvals AS a WHERE a.run_id = r.id)"
        " ORDER BY r.month DESC LIMIT 1",
        (pmc_id,),
    ).fetchone()
    if row is None:
        return None
    # What each account has available now, as the owners page and a draw count it.
    from pms.api import payables  # payables records the runs this module makes

    found = conn.execute(
        "SELECT l.ledger_account_id, a.owner_id, o.display_name, p.display_name, l.amount, l.method"
        " FROM trust_draw_run_lines AS l"
        " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = l.ledger_account_id"
        " JOIN trust_owners AS o ON o.id = a.owner_id"
        " JOIN trust_properties AS p ON p.id = a.property_id"
        " WHERE l.run_id = %s"
        ' ORDER BY o.display_name COLLATE "C", p.display_name COLLATE "C"',
        (row[0],),
    ).fetchall()
    now = {
        m.account: m.available
        for m in payables.owner_money(conn, pmc_id, owners=sorted({line[1] for line in found}))
    }
    lines = [
        DrawLine(
            account=line[0],
            owner_id=line[1],
            owner=line[2],
            property=line[3],
            amount=money(line[4]),
            available_now=now.get(line[0], "0.00"),
            method=line[5],
        )
        for line in found
    ]
    return DrawRun(
        id=row[0],
        month=row[1],
        prepared_on=row[2],
        lines=lines,
        total=money(sum((Decimal(line.amount) for line in lines), Decimal(0))),
    )
