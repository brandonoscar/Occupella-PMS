"""A trust bank account's own pages (ADR 0006, Banking): its register, every move of its cash in
or out with what it was, who paid or was paid, its memo, the property and the balance after it;
and what it holds broken down: each owner's property account with what it holds, keeps back and
may pay out, and everything else held in it by kind. Read only; always the session's PMC.

The books keep a trust bank account's cash as the side money leaves from, so its balance is the
cash account's negated: a deposit is an entry that lowers the cash account, a withdrawal one that
raises it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from pms.api import payables
from pms.api.deps import Conn, Staff
from pms.api.rent import cents, today
from pms.api.rentals import Page, Paged, PerPage

router = APIRouter(prefix="/api/bank-accounts")


class RegisterLine(BaseModel):
    transfer_id: str
    on: date
    # Rent payment, Security deposit, Returned payment, Owner draw, Owner contribution, Bill
    # payment, Company fees, Deposit returned, Opening balance or Other.
    type: str
    # Who paid it or was paid: the tenants, the owner, the vendor, or the company.
    party: str | None
    memo: str | None
    property: str | None
    deposit: str | None
    withdrawal: str | None
    # The account's balance after it, in the order the moves happened.
    balance: str


# What a move of a trust bank account's cash was, and who paid or was paid, from the move `m`
# (its transfer_id, metadata and amount on the cash account) and its other account `o` (with
# `ow`, `tn` and `vn`, its owner, tenant and vendor); src/pms/api/bank_deposits.py reads them too.
TYPE = """
       CASE
           WHEN m.metadata ->> 'kind' = 'tenant payment' THEN 'Rent payment'
           WHEN m.metadata ->> 'kind' = 'security deposit' THEN 'Security deposit'
           WHEN m.metadata ->> 'kind' = 'bounced payment' THEN 'Returned payment'
           WHEN EXISTS (SELECT 1 FROM trust_owner_draws AS d WHERE d.transfer_id = m.transfer_id)
               THEN 'Owner draw'
           WHEN EXISTS (SELECT 1 FROM trust_owner_contributions AS c
                        WHERE c.transfer_id = m.transfer_id) THEN 'Owner contribution'
           WHEN EXISTS (SELECT 1 FROM trust_bill_payments AS b
                        WHERE b.transfer_id = m.transfer_id AND b.step = 'paid') THEN 'Bill payment'
           WHEN EXISTS (SELECT 1 FROM trust_pmc_fee_withdrawals AS w
                        WHERE w.transfer_id = m.transfer_id) THEN 'Company fees'
           WHEN m.metadata ->> 'memo' = 'Opening balance' THEN 'Opening balance'
           WHEN o.kind = 'tenant_deposit' AND m.amount > 0 THEN 'Deposit returned'
           ELSE 'Other'
       END
"""
PARTY = """
       CASE
           WHEN m.metadata ? 'lease_id' THEN (
               SELECT string_agg(t.display_name, ', ' ORDER BY t.display_name COLLATE "C", t.id)
               FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id
               WHERE lt.lease_id = (m.metadata ->> 'lease_id')::uuid)
           WHEN o.kind = 'owner_property' THEN ow.display_name
           WHEN o.kind IN ('tenant_deposit', 'prepaid_rent') THEN tn.display_name
           WHEN o.kind = 'vendor_payable' THEN vn.display_name
           WHEN o.kind = 'pmc_income' THEN 'The company'
       END
"""

# Every move of the account's cash, its running balance taken in the order they happened (by
# day, then as posted), so a day posted late still lands where it belongs.
REGISTER = f"""
WITH cash AS (
    SELECT a.ledger_account_id FROM trust_ledger_accounts AS a
    WHERE a.pmc_id = %(pmc)s AND a.bank_account_id = %(bank)s AND a.kind = 'bank_cash'
), moves AS (
    SELECT e.id AS entry_id, t.id AS transfer_id, t.event_at, t.created_at, t.metadata, e.amount,
           CASE WHEN t.from_account_id = e.account_id THEN t.to_account_id
                ELSE t.from_account_id END AS other,
           -sum(e.amount) OVER (ORDER BY t.event_at, t.created_at, e.id) AS balance
    FROM pgledger_entries AS e
    JOIN cash ON cash.ledger_account_id = e.account_id
    JOIN pgledger_transfers AS t ON t.id = e.transfer_id
)
SELECT m.transfer_id, (m.event_at AT TIME ZONE 'UTC')::date,
       {TYPE},
       {PARTY},
       coalesce(m.metadata ->> 'memo', m.metadata ->> 'reference'),
       coalesce(op.display_name, tp.display_name, (
           SELECT bp.display_name FROM trust_bill_payments AS b
           JOIN trust_bills AS bl ON bl.id = b.bill_id
           JOIN trust_ledger_accounts AS ba ON ba.ledger_account_id = bl.ledger_account_id
           JOIN trust_properties AS bp ON bp.id = ba.property_id
           WHERE b.transfer_id = m.transfer_id LIMIT 1)),
       m.amount, m.balance, count(*) OVER ()
FROM moves AS m
JOIN trust_ledger_accounts AS o ON o.ledger_account_id = m.other
LEFT JOIN trust_owners AS ow ON ow.id = o.owner_id
LEFT JOIN trust_properties AS op ON op.id = o.property_id
LEFT JOIN trust_tenants AS tn ON tn.id = o.tenant_id
LEFT JOIN trust_properties AS tp ON tp.id = tn.property_id
LEFT JOIN trust_vendors AS vn ON vn.id = o.vendor_id
WHERE m.event_at >= %(start)s::timestamp AT TIME ZONE 'UTC'
  AND m.event_at < (%(end)s::date + 1)::timestamp AT TIME ZONE 'UTC'
ORDER BY m.event_at DESC, m.created_at DESC, m.entry_id DESC
LIMIT %(per_page)s OFFSET %(offset)s
"""  # noqa: S608


def bank_of(conn: psycopg.Connection[Any], pmc_id: UUID, bank_id: UUID) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM trust_bank_accounts WHERE pmc_id = %s AND id = %s", (pmc_id, bank_id)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such trust bank account")


@router.get("/{bank_account_id}/register")
def register(
    session: Staff,
    conn: Conn,
    bank_account_id: UUID,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    page: Page = 1,
    per_page: PerPage = 50,
) -> Paged[RegisterLine]:
    """A trust bank account's register over days (UTC; the last ninety to today by default),
    newest first, a page at a time: each move of its cash with what it was, who paid or was
    paid, its memo, the property, and the balance after it."""
    bank_of(conn, session.pmc_id, bank_account_id)
    last = end or today()
    first = start or date.fromordinal(last.toordinal() - 90)
    if last < first:
        raise HTTPException(422, "a register runs from a day to the same day or a later one")
    rows = conn.execute(
        REGISTER,
        {
            "pmc": session.pmc_id,
            "bank": bank_account_id,
            "start": first,
            "end": last,
            "per_page": per_page,
            "offset": (page - 1) * per_page,
        },
    ).fetchall()
    return Paged(
        items=[
            RegisterLine(
                transfer_id=transfer,
                on=on,
                type=kind,
                party=party,
                memo=memo,
                property=prop,
                deposit=cents(-amount) if amount < 0 else None,
                withdrawal=cents(amount) if amount > 0 else None,
                balance=cents(balance),
            )
            for transfer, on, kind, party, memo, prop, amount, balance, _ in rows
        ],
        total=rows[0][-1] if rows else 0,
        page=page,
        per_page=per_page,
    )


class Held(BaseModel):
    kind: str
    # What it is, in words: owners' money, tenants' deposits, prepaid rent, set aside for
    # vendors, the company's fees.
    label: str
    amount: str


class Breakdown(BaseModel):
    # What the books say the account holds: its cash.
    book_cash: str
    # Everything held in it, by kind; together it is the book cash (the tie-out).
    held: list[Held]
    # Each owner's property account in it: what it holds, keeps back and may pay out.
    owners: list[payables.OwnerMoney]


LABELS = {
    "owner_property": "Owners' money",
    "tenant_deposit": "Tenants' security deposits",
    "prepaid_rent": "Prepaid rent",
    "vendor_payable": "Set aside for vendors",
    "pmc_income": "The company's fees",
}


@router.get("/{bank_account_id}/breakdown")
def breakdown(session: Staff, conn: Conn, bank_account_id: UUID) -> Breakdown:
    """What a trust bank account holds today: its book cash, everything held in it by kind, and
    each owner's property account in it with what it holds, keeps back and may pay out."""
    bank_of(conn, session.pmc_id, bank_account_id)
    sums: dict[str, Decimal] = {
        kind: total
        for kind, total in conn.execute(
            "SELECT a.kind, coalesce(sum(l.balance), 0) FROM trust_ledger_accounts AS a"
            " JOIN pgledger_accounts AS l ON l.id = a.ledger_account_id"
            " WHERE a.pmc_id = %s AND a.bank_account_id = %s GROUP BY a.kind",
            (session.pmc_id, bank_account_id),
        ).fetchall()
    }
    return Breakdown(
        book_cash=cents(-sums.get("bank_cash", Decimal(0))),
        held=[
            Held(kind=kind, label=label, amount=cents(sums[kind]))
            for kind, label in LABELS.items()
            if kind in sums
        ],
        owners=payables.owner_money(conn, session.pmc_id, bank=bank_account_id),
    )
