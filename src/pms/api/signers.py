"""Who may pay money out of each trust bank account (migration 0060, docs/law.md): its broker on
file, named by an admin, and the signers the broker authorized in writing, each licensed (a
license number) or bonded (a fidelity bond's amount and the day it runs out). Once an account
has a broker on file, the database refuses a payout from it recorded by any other staff member.

Every staff member sees who may sign; the database keeps the rules: an admin names the broker,
only the broker authorizes or takes away a signer, one broker per account, and signers only
while it has one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints

from pms import exports
from pms.api.deps import Conn, Staff, refusal

router = APIRouter(prefix="/api/signers")

License = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Bond = Annotated[Decimal, Field(gt=0, le=Decimal("100000000"), decimal_places=2)]
CENT = Decimal("0.01")


class Signer(BaseModel):
    staff_id: UUID
    name: str
    role: Literal["broker", "signer"]
    license_number: str | None
    bond_amount: str | None
    bond_expires_on: date | None
    authorized_on: date
    # licensed, bonded, "bond runs out in N days" (30 or fewer), or "bond ran out".
    standing: str


class Account(BaseModel):
    bank_account_id: UUID
    bank_account: str
    # Its broker and signers on the day, the broker first; empty: no broker on file.
    signers: list[Signer]


def signers_on(conn: psycopg.Connection[Any], pmc_id: UUID, on: date) -> list[Account]:
    found: dict[UUID, Account] = {}
    for (
        bank,
        name,
        staff_id,
        signer,
        role,
        license_number,
        bond,
        expires,
        authorized,
        standing,
    ) in conn.execute(
        "SELECT bank_account_id, bank_account, staff_id, signer, role, license_number,"
        " bond_amount, bond_expires_on, authorized_on, standing"
        " FROM trust_report_trust_account_signers(%s, %s) ORDER BY line",
        (pmc_id, on),
    ).fetchall():
        account = found.setdefault(
            bank, Account(bank_account_id=bank, bank_account=name, signers=[])
        )
        if staff_id is not None:
            account.signers.append(
                Signer(
                    staff_id=staff_id,
                    name=signer,
                    role=role,
                    license_number=license_number,
                    bond_amount=None if bond is None else exports.text(bond.quantize(CENT)),
                    bond_expires_on=expires,
                    authorized_on=authorized,
                    standing=standing,
                )
            )
    return list(found.values())


@router.get("")
def signers(
    session: Staff, conn: Conn, on: Annotated[date | None, Query()] = None
) -> list[Account]:
    """Each trust bank account's broker and signers on a day (today, UTC, by default)."""
    return signers_on(conn, session.pmc_id, on or datetime.now(UTC).date())


class Change(BaseModel):
    bank_account_id: UUID
    staff_id: UUID
    role: Literal["broker", "signer"]
    # True authorizes them (or records their renewed bond); false takes it away.
    granted: bool
    license_number: License | None = None
    bond_amount: Bond | None = None
    bond_expires_on: date | None = None
    # The day of the broker's written authorization, or of taking it away.
    authorized_on: date


@router.post("", status_code=204)
def change(session: Staff, conn: Conn, body: Change) -> None:
    """Names an account's broker (an admin; one already on file is replaced in the same change)
    or authorizes or takes away a signer (the account's broker)."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_bank_accounts WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, body.bank_account_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such trust bank account")
    if (
        conn.execute(
            "SELECT 1 FROM trust_staff WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, body.staff_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such staff member in this PMC")
    if (body.bond_amount is None) != (body.bond_expires_on is None):
        raise HTTPException(422, "a bond has an amount and a day it runs out")
    if body.granted and body.license_number is None and body.bond_amount is None:
        raise HTTPException(422, "say how they may sign: a license number, or a bond")
    if not body.granted and (body.license_number or body.bond_amount):
        raise HTTPException(422, "taking it away says nothing more")
    if body.role == "broker" and body.granted and body.license_number is None:
        raise HTTPException(422, "the broker is licensed: give their license number")
    try:
        with conn.transaction():
            if body.role == "broker" and body.granted:
                # A new broker replaces the one on file, in the same change.
                conn.execute(
                    "INSERT INTO trust_account_signers (pmc_id, bank_account_id, staff_id, role,"
                    " granted, authorized_on)"
                    " SELECT %s, %s, b, 'broker', false, %s FROM trust_broker_of(%s) AS b"
                    " WHERE b IS NOT NULL AND b <> %s",
                    (
                        session.pmc_id,
                        body.bank_account_id,
                        body.authorized_on,
                        body.bank_account_id,
                        body.staff_id,
                    ),
                )
            conn.execute(
                "INSERT INTO trust_account_signers (pmc_id, bank_account_id, staff_id, role,"
                " granted, license_number, bond_amount, bond_expires_on, authorized_on)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    session.pmc_id,
                    body.bank_account_id,
                    body.staff_id,
                    body.role,
                    body.granted,
                    body.license_number,
                    body.bond_amount,
                    body.bond_expires_on,
                    body.authorized_on,
                ),
            )
            conn.execute("SET CONSTRAINTS trust_signers_have_a_broker IMMEDIATE")
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        raise HTTPException(409, refusal(refused)) from None
