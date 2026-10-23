"""The payouts staff recorded as sent (owner draws and bills paid, trust_payout_methods) that wait
to go out: those sent by EFT in a bank file (src/pms/api/ach.py), those sent by check on a printed
check (src/pms/api/checks.py). Each waits from the day it was recorded for 30 days; an older one
was sent some other way.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import psycopg
from pydantic import BaseModel

from pms import exports

CENT = Decimal("0.01")
WAITING_DAYS = 30
Method = Literal["EFT", "check"]


def cents(value: Decimal) -> str:
    return exports.text(Decimal(value).quantize(CENT))


class Payout(BaseModel):
    transfer_id: str
    paid_on: date
    payee: Literal["vendor", "owner"]
    payee_id: UUID
    name: str
    # What it paid: "Owner draw, Elm Court" or "Bill ROOF-17".
    what: str
    # A bill's memo; an owner draw's is "Owner draw".
    memo: str
    amount: str
    # For EFT, the payee's bank account on file as staff see it ("Checking ••••6789"), or None.
    bank_account: str | None = None


# What already takes a payout of each method: a bank file not voided, a check not voided.
TAKEN: dict[Method, str] = {
    "EFT": """
      SELECT 1 FROM trust_ach_file_items AS i
      WHERE i.transfer_id = m.transfer_id
        AND NOT EXISTS (SELECT 1 FROM trust_ach_file_voids AS x WHERE x.file_id = i.file_id)
    """,
    "check": """
      SELECT 1 FROM trust_checks AS c
      WHERE c.transfer_id = m.transfer_id
        AND NOT EXISTS (SELECT 1 FROM trust_check_voids AS x WHERE x.check_id = c.id)
    """,
}

# Each payout of the account by a method, recorded since a time and taken by nothing, oldest
# first: whom it paid and what it was.
WAITING = """
SELECT m.transfer_id, p.paid_at, p.vendor_id, p.owner_id, p.amount,
       coalesce(v.display_name, o.display_name),
       CASE WHEN p.owner_id IS NOT NULL THEN 'Owner draw, ' || pr.display_name
            ELSE 'Bill ' || b.reference END,
       coalesce(b.memo, 'Owner draw')
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
WHERE m.pmc_id = %(pmc)s AND m.method = %(method)s AND p.bank_account_id = %(bank)s
  AND m.created_at >= %(since)s
  AND NOT EXISTS ({taken})
ORDER BY p.paid_at, m.transfer_id
"""


def waiting(
    conn: psycopg.Connection[Any], pmc_id: UUID, bank_account_id: UUID, method: Method
) -> list[Payout]:
    since = datetime.now(UTC) - timedelta(days=WAITING_DAYS)
    rows = conn.execute(
        WAITING.format(taken=TAKEN[method]),  # noqa: S608
        {"pmc": pmc_id, "bank": bank_account_id, "method": method, "since": since},
    )
    return [
        Payout(
            transfer_id=transfer,
            paid_on=paid_at.astimezone(UTC).date(),
            payee="vendor" if vendor is not None else "owner",
            payee_id=vendor if vendor is not None else owner,
            name=name,
            what=what,
            memo=memo,
            amount=cents(amount),
        )
        for transfer, paid_at, vendor, owner, amount, name, what, memo in rows.fetchall()
    ]
