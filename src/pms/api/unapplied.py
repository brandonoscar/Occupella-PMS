"""Unapplied cash (migration 20261106000240, the founder's chart of accounts pass, part 2): money
that came into an operating trust bank account staff can't yet match to a tenant or a charge.

Staff record it as it came (who it seems to be from, how, the day, a memo) into the trust bank
account's unapplied cash account, under a key the page makes, as every receipt is posted. Later
they apply it to a lease, where it pays what the lease owes as Receive payment does (rent first,
the owners' accounts and the company's fee account for fees it keeps, the rest prepaid rent), or
refund it through the bank's cash, a payout naming to whom (the disburse role and the account's
signers). The list says how long each has waited. While any is held in a trust bank account, its
month-end isn't approved. Nothing here sends money: the PMS records what the bank did.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StringConstraints

from pms import notices
from pms.api import outbox, rent
from pms.api.deps import Conn, Staff, refusal

router = APIRouter(prefix="/api/unapplied")

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Memo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Reference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class Unapplied(BaseModel):
    id: UUID
    bank_account_id: UUID
    bank_account: str
    received_on: date
    payer: str
    memo: str | None
    method: str
    reference: str | None
    amount: str
    # What is still held of it: not yet applied to a lease nor refunded.
    left: str
    # Days since it came in, by the company's day.
    age_days: int


LIST = """
SELECT r.id, r.bank_account_id, b.display_name, r.received_on, r.payer, r.memo, r.method,
       r.reference, r.amount, trust_unapplied_left(r.id),
       trust_pmc_day(r.pmc_id) - r.received_on
FROM trust_unapplied_receipts AS r
JOIN trust_bank_accounts AS b ON b.id = r.bank_account_id
WHERE r.pmc_id = %(pmc)s AND (%(all)s OR trust_unapplied_left(r.id) > 0)
ORDER BY r.received_on, r.created_at, r.id
"""


def listed(conn: psycopg.Connection[Any], pmc_id: UUID, everything: bool) -> list[Unapplied]:
    return [
        Unapplied(
            id=i,
            bank_account_id=bank,
            bank_account=bank_name,
            received_on=on,
            payer=payer,
            memo=memo,
            method=method,
            reference=reference,
            amount=rent.cents(amount),
            left=rent.cents(left),
            age_days=age,
        )
        for i, bank, bank_name, on, payer, memo, method, reference, amount, left, age in (
            conn.execute(LIST, {"pmc": pmc_id, "all": everything}).fetchall()
        )
    ]


@router.get("")
def unapplied(session: Staff, conn: Conn, all: bool = False) -> list[Unapplied]:  # noqa: A002
    """The unapplied cash still held, oldest first (with `all`, every receipt, applied, refunded
    or not)."""
    return listed(conn, session.pmc_id, all)


class NewUnapplied(BaseModel):
    request_id: UUID
    amount: rent.Amount
    received_on: date
    payer: Text
    memo: Memo | None = None
    method: rent.Method
    reference: Reference | None = None
    # The operating trust bank account it came into: the only one, unless the company has more.
    bank_account_id: UUID | None = None


class Received(BaseModel):
    id: UUID
    transfers: list[str]


def operating_bank(conn: psycopg.Connection[Any], pmc_id: UUID, chosen: UUID | None) -> UUID:
    banks: list[UUID] = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM trust_bank_accounts WHERE pmc_id = %s AND kind = 'operating'"
            " ORDER BY id",
            (pmc_id,),
        ).fetchall()
    ]
    if chosen is not None:
        if chosen not in banks:
            raise HTTPException(404, "no such operating trust bank account")
        return chosen
    if not banks:
        raise HTTPException(409, "the company has no operating trust bank account yet")
    if len(banks) > 1:
        raise HTTPException(
            422, "the company has more than one operating trust bank account; choose one"
        )
    return banks[0]


def unapplied_account(conn: psycopg.Connection[Any], pmc_id: UUID, bank: UUID) -> str:
    """The trust bank account's unapplied cash account, opened if it has none yet."""
    row = conn.execute(
        "SELECT ledger_account_id FROM trust_ledger_accounts"
        " WHERE bank_account_id = %s AND kind = 'unapplied_cash'",
        (bank,),
    ).fetchone()
    if row is not None:
        return str(row[0])
    opened = conn.execute(
        "SELECT trust_open_ledger_account(%s, %s, 'unapplied_cash')", (pmc_id, bank)
    ).fetchone()
    assert opened is not None
    return str(opened[0])


@router.post("", status_code=201)
def receive(session: Staff, conn: Conn, new: NewUnapplied) -> Received:
    """Money that came in and can't be matched yet, into the unapplied cash account. The same
    request id again returns it as recorded."""
    rent.not_after_today(conn, session.pmc_id, new.received_on)
    bank = operating_bank(conn, session.pmc_id, new.bank_account_id)
    account = unapplied_account(conn, session.pmc_id, bank)
    key = f"unapplied/{new.request_id}"
    metadata = {
        "key": key,
        "kind": "unapplied cash",
        "amount": rent.cents(new.amount),
        "received_on": new.received_on.isoformat(),
        "reference": new.reference,
        "method": new.method,
        "memo": f"Unapplied cash from {new.payer}",
    }
    try:
        posted = rent.post(
            conn,
            session.pmc_id,
            key,
            [(rent.cash_of(conn, bank), account, new.amount)],
            new.received_on,
            metadata,
        )
    except psycopg.errors.CheckViolation as refused:  # a closed period, say
        raise HTTPException(409, refusal(refused)) from None
    if posted is None:
        try:
            posted = rent.earlier(conn, session.pmc_id, key, metadata)
        except rent.Conflict:
            raise HTTPException(409, "this request id was used for something else") from None
    row = conn.execute(
        "SELECT id FROM trust_unapplied_receipts WHERE transfer_id = %s", (posted[0],)
    ).fetchone()
    if row is None:
        row = conn.execute(
            "INSERT INTO trust_unapplied_receipts (pmc_id, bank_account_id, ledger_account_id,"
            " transfer_id, amount, received_on, payer, memo, method, reference)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                session.pmc_id,
                bank,
                account,
                posted[0],
                new.amount,
                new.received_on,
                new.payer,
                new.memo,
                new.method,
                new.reference,
            ),
        ).fetchone()
    assert row is not None
    return Received(id=row[0], transfers=posted)


class Apply(BaseModel):
    request_id: UUID
    lease_id: UUID
    # How much of it; all that is left unless said.
    amount: rent.Amount | None = None
    on: date | None = None
    # The owner's account, when the lease's property has more than one and no shares.
    account: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = None


def lock_receipt(conn: psycopg.Connection[Any], receipt_id: UUID) -> None:
    """Applying and refunding one receipt wait for each other here, before either reads what is
    left of it or posts a transfer. An application locks the unapplied cash account by posting
    its payment and then the receipt (its trigger), a refund the receipt and then the account, so
    the two at once deadlocked: one answered a server error (the review of 11/07)."""
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"unapplied receipt {receipt_id}",),
    )


def receipt_row(conn: psycopg.Connection[Any], pmc_id: UUID, receipt_id: UUID) -> Any:
    row = conn.execute(
        "SELECT ledger_account_id, bank_account_id, method, reference,"
        " trust_unapplied_left(id) FROM trust_unapplied_receipts WHERE pmc_id = %s AND id = %s",
        (pmc_id, receipt_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such unapplied cash")
    return row


@router.post("/{receipt_id}/apply", status_code=201)
def apply(session: Staff, conn: Conn, receipt_id: UUID, new: Apply) -> rent.Payment:
    """Applies unapplied cash to a lease: it pays what the lease owes as Receive payment does,
    rent first, and holds the rest as the first tenant's prepaid rent; its tenants are emailed
    its receipt while the server sends email. The same request id again returns the payment as it
    was recorded."""
    lock_receipt(conn, receipt_id)
    account, bank, method, reference, left = receipt_row(conn, session.pmc_id, receipt_id)
    on = new.on or rent.today(conn, session.pmc_id)
    rent.not_after_today(conn, session.pmc_id, on)
    key = f"payment/{new.request_id}"
    done = conn.execute(
        "SELECT amount FROM trust_unapplied_applications WHERE pmc_id = %s AND payment_key = %s",
        (session.pmc_id, key),
    ).fetchone()
    amount = new.amount if new.amount is not None else (done[0] if done else Decimal(left))
    if done is None and amount > left:
        raise HTTPException(
            422,
            f"only {rent.cents(left)} of this unapplied cash is left; {rent.cents(amount)} is more",
        )
    if amount <= 0:
        raise HTTPException(409, "nothing is left of this unapplied cash to apply")
    try:
        recorded = rent.record_payment(
            conn,
            session.pmc_id,
            new.lease_id,
            new.request_id,
            amount,
            on,
            reference,
            new.account,
            method,
            source=(account, bank),
            extra={"unapplied_id": str(receipt_id)},
        )
        if done is None:
            conn.execute(
                "INSERT INTO trust_unapplied_applications"
                " (pmc_id, receipt_id, lease_id, amount, payment_key) VALUES (%s, %s, %s, %s, %s)",
                (session.pmc_id, receipt_id, new.lease_id, amount, key),
            )
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        raise HTTPException(409, refusal(refused)) from None
    if outbox.SENDING.get():
        # Its tenants get its receipt as for any payment to the lease (once per payment).
        notices.write_payment_receipts(
            conn, session.pmc_id, new.lease_id, recorded.transfers[0], amount, on
        )
    return rent.answer(recorded)


class Refund(BaseModel):
    request_id: UUID
    paid_to: Text
    method: Literal["check", "EFT"]
    amount: rent.Amount | None = None
    on: date | None = None


class Refunded(BaseModel):
    transfer: str
    amount: str


@router.post("/{receipt_id}/refund", status_code=201)
def refund(session: Staff, conn: Conn, receipt_id: UUID, new: Refund) -> Refunded:
    """Refunds unapplied cash, all that is left unless said, out through the trust bank account's
    cash to whom it is paid: a payout (disburse). The same request id again returns it."""
    lock_receipt(conn, receipt_id)
    *_, left = receipt_row(conn, session.pmc_id, receipt_id)
    on = new.on or rent.today(conn, session.pmc_id)
    rent.not_after_today(conn, session.pmc_id, on)
    amount = new.amount if new.amount is not None else Decimal(left)
    done = conn.execute(
        "SELECT amount FROM trust_unapplied_refunds WHERE pmc_id = %s AND request_key = %s",
        (session.pmc_id, str(new.request_id)),
    ).fetchone()
    if done is not None and new.amount is None:
        amount = Decimal(done[0])
    if amount <= 0:
        raise HTTPException(409, "nothing is left of this unapplied cash to refund")
    try:
        with conn.transaction():
            row = conn.execute(
                "SELECT trust_refund_unapplied_cash(%s, %s, %s, %s, %s, %s, %s)",
                (
                    session.pmc_id,
                    receipt_id,
                    amount,
                    new.paid_to,
                    new.method,
                    str(new.request_id),
                    rent.at(on),
                ),
            ).fetchone()
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        raise HTTPException(409, refusal(refused)) from None
    assert row is not None
    return Refunded(transfer=row[0], amount=rent.cents(amount))
