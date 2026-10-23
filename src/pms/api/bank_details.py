"""Where a vendor or an owner is paid by bank transfer (migration 20261022000155; the founder,
2026-10-23): their bank account, kept on their page. Staff see the name on it, the bank's
routing number, checking or savings and the last four digits of the account number; the whole
number is sealed by the app (src/pms/sealed.py) before it reaches the database and is opened only
to write the ACH file that pays into it. Each change is a new row naming who made it, who must
hold disburse (the database checks it); a removal takes the account away. Nothing here moves
money.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StringConstraints, model_validator

from pms import accounts, sealed
from pms.api.deps import Conn, Staff

router = APIRouter()

Payee = Literal["vendor", "owner"]
Holder = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=22)]
Routing = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{9}$")]
AccountNumber = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{4,17}$")]


class BankAccount(BaseModel):
    holder_name: str
    routing_number: str
    account_type: Literal["checking", "savings"]
    # The account number as staff see it: its last four digits.
    account_last4: str
    recorded_by: str | None
    recorded_at: datetime


class NewBankAccount(BaseModel):
    holder_name: Holder
    routing_number: Routing
    account_type: Literal["checking", "savings"]
    account_number: AccountNumber
    # Typed twice, as a bank asks: a digit wrong sends money to someone else.
    account_number_again: AccountNumber

    @model_validator(mode="after")
    def same_twice(self) -> NewBankAccount:
        if self.account_number != self.account_number_again:
            raise ValueError("the account number and its second typing differ")
        return self


def context(pmc_id: UUID, payee: Payee, person_id: UUID) -> str:
    """What a sealed account number is about: a copy on another row doesn't open."""
    return f"payee bank account:{pmc_id}:{payee}:{person_id}"


COLUMN = {"vendor": "vendor_id", "owner": "owner_id"}
TABLE = {"vendor": "trust_vendors", "owner": "trust_owners"}


def payee_of(conn: psycopg.Connection[Any], pmc_id: UUID, payee: Payee, person_id: UUID) -> None:
    row = conn.execute(
        f"SELECT 1 FROM {TABLE[payee]} WHERE pmc_id = %s AND id = %s",  # noqa: S608
        (pmc_id, person_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"no such {payee}")


LATEST = """
SELECT b.removed, b.holder_name, b.routing_number, b.account_type, b.account_last4,
       s.display_name, b.created_at, b.account_number_sealed
FROM trust_payee_bank_accounts AS b
LEFT JOIN trust_staff AS s ON s.id = b.recorded_by
WHERE b.pmc_id = %s AND b.{column} = %s
ORDER BY b.created_at DESC, b.id DESC
LIMIT 1
"""


def latest(
    conn: psycopg.Connection[Any], pmc_id: UUID, payee: Payee, person_id: UUID
) -> tuple[BankAccount, bytes] | None:
    """The payee's bank account on file and its sealed number, or None."""
    row = conn.execute(
        LATEST.format(column=COLUMN[payee]),  # noqa: S608
        (pmc_id, person_id),
    ).fetchone()
    if row is None or row[0]:
        return None
    _, holder, routing, kind, last4, by, at, blob = row
    return (
        BankAccount(
            holder_name=holder,
            routing_number=routing,
            account_type=kind,
            account_last4=last4,
            recorded_by=by,
            recorded_at=at,
        ),
        bytes(blob),
    )


def account_number(
    conn: psycopg.Connection[Any], pmc_id: UUID, payee: Payee, person_id: UUID
) -> str | None:
    """The payee's whole account number, opened with the server's key: for the ACH file only."""
    found = latest(conn, pmc_id, payee, person_id)
    if found is None:
        return None
    return sealed.open_sealed(found[1], context(pmc_id, payee, person_id))


def show(
    conn: psycopg.Connection[Any], session: accounts.Session, payee: Payee, person_id: UUID
) -> BankAccount | None:
    payee_of(conn, session.pmc_id, payee, person_id)
    found = latest(conn, session.pmc_id, payee, person_id)
    return None if found is None else found[0]


def keep(
    conn: psycopg.Connection[Any],
    session: accounts.Session,
    payee: Payee,
    person_id: UUID,
    new: NewBankAccount,
) -> BankAccount:
    payee_of(conn, session.pmc_id, payee, person_id)
    checked = conn.execute("SELECT trust_aba_routing_ok(%s)", (new.routing_number,)).fetchone()
    if not (checked and checked[0]):
        raise HTTPException(422, "that routing number's check digit is wrong: look at it again")
    try:
        blob = sealed.seal(new.account_number, context(session.pmc_id, payee, person_id))
    except sealed.NoKey:
        raise HTTPException(
            503, "this server has no key to keep bank account numbers with (PMS_FIELD_KEY)"
        ) from None
    conn.execute(
        f"INSERT INTO trust_payee_bank_accounts (pmc_id, {COLUMN[payee]}, holder_name,"  # noqa: S608
        " routing_number, account_type, account_number_sealed, account_last4)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            session.pmc_id,
            person_id,
            new.holder_name,
            new.routing_number,
            new.account_type,
            blob,
            new.account_number[-4:],
        ),
    )
    shown = latest(conn, session.pmc_id, payee, person_id)
    assert shown is not None
    return shown[0]


def remove(
    conn: psycopg.Connection[Any], session: accounts.Session, payee: Payee, person_id: UUID
) -> None:
    payee_of(conn, session.pmc_id, payee, person_id)
    if latest(conn, session.pmc_id, payee, person_id) is None:
        raise HTTPException(409, f"this {payee} has no bank account on file")
    conn.execute(
        f"INSERT INTO trust_payee_bank_accounts (pmc_id, {COLUMN[payee]}, removed)"  # noqa: S608
        " VALUES (%s, %s, true)",
        (session.pmc_id, person_id),
    )


@router.get("/api/vendors/{vendor_id}/bank-account")
def vendor_bank_account(session: Staff, conn: Conn, vendor_id: UUID) -> BankAccount | None:
    """The vendor's bank account on file, its number shown by its last four digits; or null."""
    return show(conn, session, "vendor", vendor_id)


@router.post("/api/vendors/{vendor_id}/bank-account", status_code=201)
def keep_vendor_bank_account(
    session: Staff, conn: Conn, vendor_id: UUID, new: NewBankAccount
) -> BankAccount:
    """Where the vendor is paid by bank transfer from now on."""
    return keep(conn, session, "vendor", vendor_id, new)


@router.post("/api/vendors/{vendor_id}/bank-account/remove", status_code=204)
def remove_vendor_bank_account(session: Staff, conn: Conn, vendor_id: UUID) -> None:
    """The vendor has no bank account on file from now on."""
    remove(conn, session, "vendor", vendor_id)


@router.get("/api/owners/{owner_id}/bank-account")
def owner_bank_account(session: Staff, conn: Conn, owner_id: UUID) -> BankAccount | None:
    """The owner's bank account on file, its number shown by its last four digits; or null."""
    return show(conn, session, "owner", owner_id)


@router.post("/api/owners/{owner_id}/bank-account", status_code=201)
def keep_owner_bank_account(
    session: Staff, conn: Conn, owner_id: UUID, new: NewBankAccount
) -> BankAccount:
    """Where the owner is paid by bank transfer from now on."""
    return keep(conn, session, "owner", owner_id, new)


@router.post("/api/owners/{owner_id}/bank-account/remove", status_code=204)
def remove_owner_bank_account(session: Staff, conn: Conn, owner_id: UUID) -> None:
    """The owner has no bank account on file from now on."""
    remove(conn, session, "owner", owner_id)
