"""Bank deposits (the founder's review, q025; migration 0078): the checks and money staff take to
the bank together, which the bank shows as one line. A trust bank account's page lists what came
into its cash and is in no deposit yet (each payment once, its prepaid rent with it); staff tick
what they take to the bank and record the deposit with its day, then print its slip. At month-end
the deposit's bank line matches all of it at once (src/pms/api/reconcile.py, auto-match). A
deposit moves no money: it groups what the books already hold. Always the session's PMC.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from pms import exports
from pms.api.banking import PARTY, TYPE, bank_of
from pms.api.deps import Conn, Staff, download, refusal
from pms.api.reconcile import LIVE
from pms.api.rent import cents, today

router = APIRouter(prefix="/api/bank-accounts")


class Undeposited(BaseModel):
    # The first transfer's id, which names the money that came in together (a payment).
    id: str
    transfer_ids: list[str]
    on: date
    type: str
    party: str | None
    memo: str | None
    amount: str


class Deposit(BaseModel):
    id: UUID
    deposited_on: date
    total: str
    items: int
    recorded_by: str | None
    # Every item matched to a bank line at month-end.
    matched: bool


# What came into the account's cash and is in no deposit and no live bank match yet, each payment
# once (its transfers share its key), oldest first.
UNDEPOSITED = f"""
WITH cash AS (
    SELECT a.ledger_account_id FROM trust_ledger_accounts AS a
    WHERE a.pmc_id = %(pmc)s AND a.bank_account_id = %(bank)s AND a.kind = 'bank_cash'
), m AS (
    SELECT t.id AS transfer_id, t.event_at, t.created_at, t.metadata, e.amount,
           coalesce(t.metadata ->> 'key', t.id) AS grp,
           CASE WHEN t.from_account_id = e.account_id THEN t.to_account_id
                ELSE t.from_account_id END AS other
    FROM pgledger_entries AS e
    JOIN cash ON cash.ledger_account_id = e.account_id
    JOIN pgledger_transfers AS t ON t.id = e.transfer_id
    WHERE e.amount < 0
      AND NOT EXISTS (SELECT 1 FROM trust_bank_deposit_items AS i WHERE i.transfer_id = t.id)
      AND NOT EXISTS (SELECT 1 FROM trust_bank_match_transfers AS mt
                      JOIN trust_bank_matches AS bm ON bm.id = mt.match_id
                      WHERE mt.transfer_id = t.id AND bm.bank_account_id = %(bank)s
                        AND {LIVE.format("mt")})
), described AS (
    SELECT m.grp, m.transfer_id, m.event_at, m.created_at, -m.amount AS amount,
           {TYPE} AS type, {PARTY} AS party,
           coalesce(m.metadata ->> 'reference', m.metadata ->> 'memo') AS memo
    FROM m
    JOIN trust_ledger_accounts AS o ON o.ledger_account_id = m.other
    LEFT JOIN trust_owners AS ow ON ow.id = o.owner_id
    LEFT JOIN trust_tenants AS tn ON tn.id = o.tenant_id
    LEFT JOIN trust_vendors AS vn ON vn.id = o.vendor_id
)
SELECT array_agg(d.transfer_id ORDER BY d.created_at, d.transfer_id),
       (min(d.event_at) AT TIME ZONE 'UTC')::date,
       (array_agg(d.type ORDER BY d.created_at, d.transfer_id))[1],
       (array_agg(d.party ORDER BY d.created_at, d.transfer_id))[1],
       (array_agg(d.memo ORDER BY d.created_at, d.transfer_id))[1],
       sum(d.amount)
FROM described AS d
GROUP BY d.grp
ORDER BY min(d.event_at), min(d.created_at), d.grp
"""  # noqa: S608


def undeposited_of(conn: psycopg.Connection[Any], pmc: UUID, bank: UUID) -> list[Undeposited]:
    return [
        Undeposited(
            id=ids[0],
            transfer_ids=list(ids),
            on=on,
            type=kind,
            party=party,
            memo=memo,
            amount=cents(amount),
        )  # fmt: skip
        for ids, on, kind, party, memo, amount in conn.execute(
            UNDEPOSITED, {"pmc": pmc, "bank": bank}
        )
    ]


@router.get("/{bank_account_id}/undeposited")
def undeposited(session: Staff, conn: Conn, bank_account_id: UUID) -> list[Undeposited]:
    """What came into a trust bank account's cash and is in no deposit or bank match yet."""
    bank_of(conn, session.pmc_id, bank_account_id)
    return undeposited_of(conn, session.pmc_id, bank_account_id)


# Each deposit with what its items brought into the account's cash.
DEPOSITS = f"""
SELECT d.id, d.deposited_on,
       coalesce((SELECT -sum(e.amount) FROM trust_bank_deposit_items AS i
                 JOIN pgledger_entries AS e ON e.transfer_id = i.transfer_id
                 JOIN trust_ledger_accounts AS a ON a.ledger_account_id = e.account_id
                 WHERE i.deposit_id = d.id AND a.bank_account_id = d.bank_account_id
                   AND a.kind = 'bank_cash'), 0),
       (SELECT count(*) FROM trust_bank_deposit_items AS i WHERE i.deposit_id = d.id),
       s.display_name,
       NOT EXISTS (SELECT 1 FROM trust_bank_deposit_items AS i
                   WHERE i.deposit_id = d.id AND NOT EXISTS (
                       SELECT 1 FROM trust_bank_match_transfers AS mt
                       WHERE mt.transfer_id = i.transfer_id AND {LIVE.format("mt")}))
FROM trust_bank_deposits AS d
LEFT JOIN trust_staff AS s ON s.id = d.recorded_by
WHERE d.pmc_id = %(pmc)s AND (%(bank)s::uuid IS NULL OR d.bank_account_id = %(bank)s)
  AND (%(deposit)s::uuid IS NULL OR d.id = %(deposit)s)
ORDER BY d.deposited_on DESC, d.created_at DESC, d.id
LIMIT 50
"""  # noqa: S608


def deposits_of(
    conn: psycopg.Connection[Any],
    pmc: UUID,
    bank: UUID | None = None,
    deposit: UUID | None = None,
) -> list[Deposit]:
    return [
        Deposit(
            id=i,
            deposited_on=on,
            total=cents(total),
            items=count,
            recorded_by=by,
            matched=matched,
        )  # fmt: skip
        for i, on, total, count, by, matched in conn.execute(
            DEPOSITS, {"pmc": pmc, "bank": bank, "deposit": deposit}
        )
    ]


@router.get("/{bank_account_id}/deposits")
def deposits(session: Staff, conn: Conn, bank_account_id: UUID) -> list[Deposit]:
    """A trust bank account's latest 50 deposits, newest first."""
    bank_of(conn, session.pmc_id, bank_account_id)
    return deposits_of(conn, session.pmc_id, bank_account_id)


class NewDeposit(BaseModel):
    deposited_on: date
    # The transfers taken to the bank together: whole payments, as the list gives them.
    transfer_ids: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=100)]],
        Field(min_length=1, max_length=500),
    ]


@router.post("/{bank_account_id}/deposits", status_code=201)
def record_deposit(session: Staff, conn: Conn, bank_account_id: UUID, new: NewDeposit) -> Deposit:
    """Records a deposit of what was ticked, all of it or none: each transfer brought money into
    this account by the day of the deposit, and is in no other deposit."""
    bank_of(conn, session.pmc_id, bank_account_id)
    if new.deposited_on > today():
        raise HTTPException(422, "a deposit is recorded on the day it went in, not a later one")
    if len(set(new.transfer_ids)) != len(new.transfer_ids):
        raise HTTPException(422, "each payment goes in a deposit once")
    made = conn.execute(
        "INSERT INTO trust_bank_deposits (pmc_id, bank_account_id, deposited_on)"
        " VALUES (%s, %s, %s) RETURNING id",
        (session.pmc_id, bank_account_id, new.deposited_on),
    ).fetchone()
    assert made is not None
    try:
        with conn.transaction():
            for transfer in new.transfer_ids:
                conn.execute(
                    "INSERT INTO trust_bank_deposit_items (deposit_id, pmc_id, transfer_id)"
                    " VALUES (%s, %s, %s)",
                    (made[0], session.pmc_id, transfer),
                )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "a payment ticked is in another deposit already") from None
    except psycopg.errors.CheckViolation as refused:
        # An unknown payment too: the item's trigger finds no money it brought in.
        raise HTTPException(422, refusal(refused)) from None
    (deposit,) = deposits_of(conn, session.pmc_id, deposit=made[0])
    return deposit


# A deposit's items as its slip lists them: each payment once, with who paid and its check.
SLIP = f"""
WITH m AS (
    SELECT t.id AS transfer_id, t.event_at, t.created_at, t.metadata, e.amount,
           coalesce(t.metadata ->> 'key', t.id) AS grp,
           CASE WHEN t.from_account_id = e.account_id THEN t.to_account_id
                ELSE t.from_account_id END AS other
    FROM trust_bank_deposit_items AS i
    JOIN trust_bank_deposits AS d ON d.id = i.deposit_id
    JOIN pgledger_entries AS e ON e.transfer_id = i.transfer_id
    JOIN trust_ledger_accounts AS c ON c.ledger_account_id = e.account_id
         AND c.bank_account_id = d.bank_account_id AND c.kind = 'bank_cash'
    JOIN pgledger_transfers AS t ON t.id = i.transfer_id
    WHERE i.pmc_id = %(pmc)s AND i.deposit_id = %(deposit)s
), described AS (
    SELECT m.grp, m.transfer_id, m.event_at, m.created_at, -m.amount AS amount,
           {TYPE} AS type, {PARTY} AS party,
           m.metadata ->> 'method' AS method,
           coalesce(m.metadata ->> 'reference', m.metadata ->> 'memo') AS memo
    FROM m
    JOIN trust_ledger_accounts AS o ON o.ledger_account_id = m.other
    LEFT JOIN trust_owners AS ow ON ow.id = o.owner_id
    LEFT JOIN trust_tenants AS tn ON tn.id = o.tenant_id
    LEFT JOIN trust_vendors AS vn ON vn.id = o.vendor_id
)
SELECT (min(d.event_at) AT TIME ZONE 'UTC')::date,
       (array_agg(d.type ORDER BY d.created_at, d.transfer_id))[1],
       (array_agg(d.party ORDER BY d.created_at, d.transfer_id))[1],
       (array_agg(d.method ORDER BY d.created_at, d.transfer_id))[1],
       (array_agg(d.memo ORDER BY d.created_at, d.transfer_id))[1],
       sum(d.amount)
FROM described AS d
GROUP BY d.grp
ORDER BY min(d.event_at), min(d.created_at), d.grp
"""  # noqa: S608


def us(day: date) -> str:
    return f"{day:%m/%d/%Y}"


@router.get("/deposits/{deposit_id}/slip")
def slip(session: Staff, conn: Conn, deposit_id: UUID) -> Response:
    """A deposit's slip as a PDF: the account, the day, each payment with who paid, how and its
    check or reference, and the total."""
    found = deposits_of(conn, session.pmc_id, deposit=deposit_id)
    if not found:
        raise HTTPException(404, "no such deposit")
    (deposit,) = found
    bank = conn.execute(
        "SELECT b.display_name FROM trust_bank_deposits AS d"
        " JOIN trust_bank_accounts AS b ON b.id = d.bank_account_id WHERE d.id = %s",
        (deposit_id,),
    ).fetchone()
    assert bank is not None
    rows: list[tuple[str, ...]] = [
        (us(on), kind, party or "", method or "", memo or "", exports.text(Decimal(amount)))
        for on, kind, party, method, memo, amount in conn.execute(
            SLIP, {"pmc": session.pmc_id, "deposit": deposit_id}
        )
    ]
    rows.append(("", "Total", f"{deposit.items} items", "", "", deposit.total))
    table = exports.Table(
        f"Deposit slip: {bank[0]}, {us(deposit.deposited_on)}",
        ("received", "what", "from", "how", "check or reference", "amount"),
        tuple(rows),
    )
    return download(
        exports.to_pdf(table, datetime.now(UTC)),
        f"deposit-{deposit.deposited_on.isoformat()}",
        "pdf",
    )
