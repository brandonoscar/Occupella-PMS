"""Checks (migration 20261022000157; the founder, 2026-10-23): the payouts staff recorded as sent
by check (owner draws and bills paid) are printed on the company's pre-printed voucher check stock.

On an operating trust bank account's Checks tab staff set where its checks sit on the sheet and
move the printing for their printer, see the payouts by check of the last 30 days not yet printed,
print those they pick under the numbers on the stock (the first one given), download a run's PDF
again (its checks not voided), and void a check that was spoiled or never sent, which frees its
payout for a new check under a new number. Printing, downloading and voiding take disburse. Nothing
here moves money: each payout has moved in the books already, and a check is signed by hand.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, StringConstraints

from pms import checks
from pms.api import company, payouts
from pms.api.deps import Conn, Staff, download, refusal
from pms.api.payouts import Payout, cents

router = APIRouter(prefix="/api/checks")

Position = Literal["top", "middle", "bottom"]
Shift = Annotated[int, Field(ge=-36, le=36)]


class Settings(BaseModel):
    position: Position
    shift_right: int
    shift_down: int
    # None until staff set them: the defaults, top and unmoved.
    recorded_by: str | None
    recorded_at: datetime | None


class NewSettings(BaseModel):
    position: Position
    shift_right: Shift = 0
    shift_down: Shift = 0


class Voided(BaseModel):
    reason: str
    by: str | None
    at: datetime


class Printed(BaseModel):
    id: UUID
    number: int
    name: str
    what: str
    amount: str
    voided: Voided | None


class Run(BaseModel):
    id: UUID
    dated_on: date
    made_by: str | None
    checks: list[Printed]


class Checks(BaseModel):
    settings: Settings
    waiting: list[Payout]
    runs: list[Run]
    # The number after the account's last check, or None before its first.
    next_number: int | None


class NewRun(BaseModel):
    # The number on the first sheet of stock in the printer.
    first_number: Annotated[int, Field(ge=1, le=99_999_999)]
    transfer_ids: Annotated[list[str], Field(min_length=1, max_length=500)]


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


def settings_of(conn: psycopg.Connection[Any], pmc_id: UUID, bank_account_id: UUID) -> Settings:
    row = conn.execute(
        "SELECT c.position, c.shift_right, c.shift_down, s.display_name, c.created_at"
        " FROM trust_check_settings AS c LEFT JOIN trust_staff AS s ON s.id = c.recorded_by"
        " WHERE c.pmc_id = %s AND c.bank_account_id = %s"
        " ORDER BY c.created_at DESC, c.id DESC LIMIT 1",
        (pmc_id, bank_account_id),
    ).fetchone()
    if row is None:
        return Settings(
            position="top", shift_right=0, shift_down=0, recorded_by=None, recorded_at=None
        )
    position, right, down, by, at = row
    return Settings(
        position=position, shift_right=right, shift_down=down, recorded_by=by, recorded_at=at
    )


# A run's checks in number order: whom each paid, what it was, and whether it was voided.
CHECKS = """
SELECT r.id, r.dated_on, s.display_name, c.id, c.check_number, c.amount, c.transfer_id,
       coalesce(v.display_name, o.display_name), v.id, o.id,
       CASE WHEN p.owner_id IS NOT NULL THEN 'Owner draw, ' || pr.display_name
            ELSE 'Bill ' || b.reference END,
       coalesce(b.memo, 'Owner draw'),
       x.reason, xs.display_name, x.created_at
FROM trust_check_runs AS r
LEFT JOIN trust_staff AS s ON s.id = r.recorded_by
JOIN trust_checks AS c ON c.pmc_id = r.pmc_id AND c.run_id = r.id
CROSS JOIN LATERAL trust_payout(c.pmc_id, c.transfer_id) AS p
LEFT JOIN trust_vendors AS v ON v.pmc_id = c.pmc_id AND v.id = p.vendor_id
LEFT JOIN trust_owners AS o ON o.pmc_id = c.pmc_id AND o.id = p.owner_id
LEFT JOIN trust_owner_draws AS d ON d.pmc_id = c.pmc_id AND d.transfer_id = c.transfer_id
LEFT JOIN trust_ledger_accounts AS la
    ON la.pmc_id = d.pmc_id AND la.ledger_account_id = d.ledger_account_id
LEFT JOIN trust_properties AS pr ON pr.pmc_id = la.pmc_id AND pr.id = la.property_id
LEFT JOIN trust_bill_payments AS bp
    ON bp.pmc_id = c.pmc_id AND bp.transfer_id = c.transfer_id AND bp.step = 'paid'
LEFT JOIN trust_bills AS b ON b.pmc_id = bp.pmc_id AND b.id = bp.bill_id
LEFT JOIN trust_check_voids AS x ON x.check_id = c.id
LEFT JOIN trust_staff AS xs ON xs.id = x.recorded_by
WHERE r.pmc_id = %(pmc)s AND {where}
ORDER BY r.created_at DESC, r.id, c.check_number
"""

Row = tuple[Any, ...]


def rows_of(conn: psycopg.Connection[Any], pmc_id: UUID, where: str, value: UUID) -> list[Row]:
    return conn.execute(
        CHECKS.format(where=where),  # noqa: S608
        {"pmc": pmc_id, "value": value},
    ).fetchall()


def runs_of(rows: list[Row]) -> list[Run]:
    runs: dict[UUID, Run] = {}
    for (
        run_id,
        dated_on,
        by,
        check_id,
        number,
        amount,
        _,
        name,
        _,
        _,
        what,
        _,
        why,
        vby,
        vat,
    ) in rows:
        run = runs.setdefault(run_id, Run(id=run_id, dated_on=dated_on, made_by=by, checks=[]))
        run.checks.append(
            Printed(
                id=check_id,
                number=number,
                name=name,
                what=what,
                amount=cents(amount),
                voided=None if why is None else Voided(reason=why, by=vby, at=vat),
            )
        )
    return list(runs.values())


def address_of(
    conn: psycopg.Connection[Any], pmc_id: UUID, vendor: UUID | None, owner: UUID | None
) -> tuple[str, ...]:
    """The payee's mailing address on file, for a window envelope; none when none is whole."""
    row = conn.execute(
        "SELECT address_line_1, address_line_2, city, state, postal_code FROM trust_contacts"
        " WHERE pmc_id = %s AND (vendor_id = %s OR owner_id = %s)"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (pmc_id, vendor, owner),
    ).fetchone()
    if row is None or row[0] is None:
        return ()
    line_1, line_2, city, state, postal = row
    return tuple(line for line in (line_1, line_2, f"{city}, {state} {postal}") if line)


@router.get("/{bank_account_id}")
def check_page(session: Staff, conn: Conn, bank_account_id: UUID) -> Checks:
    """Where the account's checks print, the payouts by check waiting for one, and its runs."""
    bank_of(conn, session.pmc_id, bank_account_id)
    last = conn.execute(
        "SELECT max(check_number) FROM trust_checks WHERE pmc_id = %s AND bank_account_id = %s",
        (session.pmc_id, bank_account_id),
    ).fetchone()
    return Checks(
        settings=settings_of(conn, session.pmc_id, bank_account_id),
        waiting=payouts.waiting(conn, session.pmc_id, bank_account_id, "check"),
        runs=runs_of(
            rows_of(conn, session.pmc_id, "r.bank_account_id = %(value)s", bank_account_id)
        )[:50],
        next_number=None if last is None or last[0] is None else last[0] + 1,
    )


@router.post("/{bank_account_id}/settings", status_code=201)
def keep_settings(session: Staff, conn: Conn, bank_account_id: UUID, new: NewSettings) -> Settings:
    """Where the account's checks sit on the sheet, and how far to move the printing."""
    bank_of(conn, session.pmc_id, bank_account_id)
    conn.execute(
        "INSERT INTO trust_check_settings (pmc_id, bank_account_id, position, shift_right,"
        " shift_down) VALUES (%s, %s, %s, %s, %s)",
        (session.pmc_id, bank_account_id, new.position, new.shift_right, new.shift_down),
    )
    return settings_of(conn, session.pmc_id, bank_account_id)


@router.post("/{bank_account_id}", status_code=201)
def print_checks(session: Staff, conn: Conn, bank_account_id: UUID, new: NewRun) -> Run:
    """A run of checks for the payouts picked, numbered from the first sheet's number on."""
    bank_of(conn, session.pmc_id, bank_account_id)
    if len(set(new.transfer_ids)) != len(new.transfer_ids):
        raise HTTPException(422, "pick each payout once")
    by_id = {
        p.transfer_id: p for p in payouts.waiting(conn, session.pmc_id, bank_account_id, "check")
    }
    picked = []
    for transfer in new.transfer_ids:
        if transfer not in by_id:
            raise HTTPException(409, f"{transfer} is not a payout by check waiting to be printed")
        picked.append(by_id[transfer])
    # The stock's order: as the payouts were paid.
    picked.sort(key=lambda p: (p.paid_on, p.transfer_id))
    if new.first_number + len(picked) - 1 > 99_999_999:
        raise HTTPException(422, "check numbers go to 99999999")
    total = sum((Decimal(p.amount) for p in picked), Decimal(0))
    try:
        row = conn.execute(
            # The database dates it today.
            "INSERT INTO trust_check_runs (pmc_id, bank_account_id, checks, total)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (session.pmc_id, bank_account_id, len(picked), total),
        ).fetchone()
        assert row is not None
        run_id = row[0]
        for number, payout in enumerate(picked, start=new.first_number):
            conn.execute(
                "INSERT INTO trust_checks (pmc_id, run_id, bank_account_id, check_number,"
                " transfer_id, amount) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    session.pmc_id,
                    run_id,
                    bank_account_id,
                    number,
                    payout.transfer_id,
                    Decimal(payout.amount),
                ),
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            409,
            f"a check numbered {new.first_number} to {new.first_number + len(picked) - 1} is"
            " printed already from this account: give the number on the first sheet loaded",
        ) from None
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, refusal(refused)) from None
    (made,) = runs_of(rows_of(conn, session.pmc_id, "r.id = %(value)s", run_id))
    return made


@router.get("/runs/{run_id}.pdf")
def run_pdf(session: Staff, conn: Conn, run_id: UUID) -> Response:
    """The run's checks not voided, to print on the stock, where the account's settings put them."""
    held = conn.execute(
        "SELECT trust_staff_holds(%s, %s, 'disburse')", (session.pmc_id, session.person_id)
    ).fetchone()
    if not (held and held[0]):
        raise HTTPException(403, "only staff who may disburse print checks")
    rows = rows_of(conn, session.pmc_id, "r.id = %(value)s", run_id)
    if not rows:
        raise HTTPException(404, "no such check run")
    bank = conn.execute(
        "SELECT bank_account_id FROM trust_check_runs WHERE pmc_id = %s AND id = %s",
        (session.pmc_id, run_id),
    ).fetchone()
    assert bank is not None
    settings = settings_of(conn, session.pmc_id, bank[0])
    printed = []
    for _, dated_on, _, _, number, amount, _, name, vendor, owner, what, memo, why, _, _ in rows:
        if why is not None:
            continue
        stub = f"{what}: {memo}" if vendor is not None else what
        printed.append(
            checks.Check(
                number=number,
                dated_on=dated_on,
                payee=name,
                address=address_of(conn, session.pmc_id, vendor, owner),
                amount=Decimal(amount),
                memo=what,
                stub=((stub, Decimal(amount)),),
            )
        )
    if not printed:
        raise HTTPException(409, "every check of this run is void")
    pdf = checks.render(
        printed,
        checks.Layout(settings.position, settings.shift_right, settings.shift_down),
        company.name_of(conn, session.pmc_id),
    )
    return download(pdf, f"checks-{printed[0].number}-{printed[-1].number}", "pdf")


@router.post("/{check_id}/void", status_code=204)
def void_check(session: Staff, conn: Conn, check_id: UUID, voiding: Voiding) -> None:
    """Sets aside a check spoiled, lost or never sent; its payout waits for a new check."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_checks WHERE pmc_id = %s AND id = %s", (session.pmc_id, check_id)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such check")
    try:
        conn.execute(
            "INSERT INTO trust_check_voids (pmc_id, check_id, reason) VALUES (%s, %s, %s)",
            (session.pmc_id, check_id, voiding.reason),
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "this check is void already") from None
