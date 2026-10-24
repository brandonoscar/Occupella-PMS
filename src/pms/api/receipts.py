"""A tenant's payment receipt (the founder's review, q029): one PDF per payment, for staff to print
from the lease's ledger and for the tenant to download in the portal. It says who paid, for which
home, the day, the amount and how it came, what it paid of each charge, what was held as prepaid
rent, and, once it came back, that it bounced; signed with the company's name and address. It
reads what was recorded and moves no money.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Response

from pms import exports
from pms.api import company
from pms.api.deps import Conn, Staff, Tenant, download

router = APIRouter()

HOW = {
    "check": "Check",
    "cashier's check": "Cashier's check",
    "money order": "Money order",
    "EFT": "EFT",
    "cash": "Cash",
    "online": "Paid online (bank)",
}


def us(day: date) -> str:
    return f"{day:%m/%d/%Y}"


def dollars(amount: Decimal) -> str:
    return f"${amount:,.2f}"


@dataclass(frozen=True)
class Receipt:
    lease_id: UUID
    received_on: date
    table: exports.Table


# Every transfer a tenant payment posted under its key: what went to the owner's account and
# what was held as prepaid rent.
PAYMENT = """
SELECT t.id, a.kind, t.amount, t.metadata
FROM pgledger_transfers AS t
JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id
WHERE a.pmc_id = %(pmc)s AND t.metadata ->> 'kind' = 'tenant payment'
  AND t.metadata ->> 'key' = (SELECT p.metadata ->> 'key' FROM pgledger_transfers AS p
                              WHERE p.id = %(transfer)s
                                AND p.metadata ->> 'kind' = 'tenant payment')
ORDER BY t.created_at, t.id
"""
HOME = """
SELECT p.display_name, u.display_name,
       array(SELECT t.display_name FROM trust_lease_tenants AS lt
             JOIN trust_tenants AS t ON t.id = lt.tenant_id
             WHERE lt.lease_id = l.id ORDER BY t.display_name COLLATE "C", t.id)
FROM trust_leases AS l
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
WHERE l.pmc_id = %s AND l.id = %s
"""
PAID = """
SELECT c.due_on, c.kind, c.memo, cp.amount
FROM trust_charge_payments AS cp
JOIN trust_charges AS c ON c.id = cp.charge_id
WHERE cp.transfer_id = ANY(%s)
ORDER BY c.due_on, c.id
"""
BOUNCED = """
SELECT min(t.event_at)
FROM pgledger_transfers AS t
JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id
WHERE a.pmc_id = %s AND t.metadata ->> 'key' = %s
"""


def receipt_of(conn: psycopg.Connection[Any], pmc_id: UUID, transfer_id: str) -> Receipt:
    """The receipt of the tenant payment a transfer of it names, in the PMC."""
    rows = conn.execute(PAYMENT, {"pmc": pmc_id, "transfer": transfer_id}).fetchall()
    if not rows:
        raise HTTPException(404, "no such tenant payment")
    meta = rows[0][3]
    lease_id = UUID(meta["lease_id"])
    received_on = date.fromisoformat(meta["received_on"])
    home = conn.execute(HOME, (pmc_id, lease_id)).fetchone()
    assert home is not None  # a payment's lease is of its PMC
    prop, unit, tenants = home
    total = sum((Decimal(amount) for _, _, amount, _ in rows), Decimal(0))
    prepaid = sum((Decimal(a) for _, kind, a, _ in rows if kind == "prepaid_rent"), Decimal(0))
    how = " ".join(
        part for part in (HOW.get(meta.get("method") or ""), meta.get("reference")) if part
    )
    lines: list[tuple[str, str]] = [
        ("Received from", ", ".join(tenants)),
        ("For", f"{prop}, {unit}"),
        ("Received on", us(received_on)),
        ("Amount", dollars(total)),
    ]
    if how:
        lines.append(("How", how))
    for due, kind, memo, amount in conn.execute(PAID, ([r[0] for r in rows],)).fetchall():
        what = memo or {"rent": "Rent", "fee": "Fee"}.get(kind, kind)
        lines.append(("Paid toward", f"{what}, due {us(due)}: {dollars(amount)}"))
    if prepaid > 0:
        lines.append(("Held as prepaid rent", dollars(prepaid)))
    came_back = conn.execute(BOUNCED, (pmc_id, f"bounce/{meta['key']}")).fetchone()
    if came_back is not None and came_back[0] is not None:
        lines.append(
            (
                "Returned",
                f"This payment came back unpaid on {us(came_back[0].astimezone(UTC).date())}.",
            )
        )
    found = company.profile(conn, pmc_id)
    address = company.address_of(found)
    by = company.name_of(conn, pmc_id)
    if address is not None:
        street = ", ".join(p for p in (address.address_line_1, address.address_line_2) if p)
        by = f"{by}, {street}, {address.city}, {address.state} {address.postal_code}"
    lines += [("Received by", by), ("Receipt number", rows[0][0])]
    return Receipt(
        lease_id=lease_id,
        received_on=received_on,
        table=exports.Table("Payment receipt", ("item", "detail"), tuple(lines)),
    )


def pdf(receipt: Receipt) -> Response:
    return download(
        exports.to_pdf(receipt.table, datetime.now(UTC)),
        f"receipt-{receipt.received_on.isoformat()}",
        "pdf",
    )


@router.get("/api/rent/payments/{transfer_id}/receipt")
def staff_receipt(session: Staff, conn: Conn, transfer_id: str) -> Response:
    """A payment's receipt, as a PDF to print or hand the tenant."""
    return pdf(receipt_of(conn, session.pmc_id, transfer_id))


@router.get("/api/tenant/payments/{transfer_id}/receipt")
def tenant_receipt(session: Tenant, conn: Conn, transfer_id: str) -> Response:
    """The receipt of a payment to a lease the tenant is on; any other reads as not found."""
    try:
        receipt = receipt_of(conn, session.pmc_id, transfer_id)
    except HTTPException:
        raise HTTPException(404, "no such payment of yours") from None
    if (
        conn.execute(
            "SELECT 1 FROM trust_lease_tenants WHERE pmc_id = %s AND lease_id = %s"
            " AND tenant_id IN (SELECT trust_tenant_records(%s, %s))",
            (session.pmc_id, receipt.lease_id, session.pmc_id, session.person_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such payment of yours")
    return pdf(receipt)
