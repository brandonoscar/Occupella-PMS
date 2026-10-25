"""The PDF an automatic email carries (migration 0111; the founder's review q029, q045 and q055):
a payment receipt's receipt, an owner statement's whole statement for its month, and since
migration 0120 (q039) a tenant's security deposit statement with its receipts and photos. Each is
made when the email is handed to the sender (src/pms/email.py, deliver_pending), from what the
database holds then, so the outbox keeps no file. Every other kind of email carries none.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from fastapi import HTTPException

from pms import email, exports


def _month_end(first: date) -> date:
    """The month's last day."""
    return (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def for_email(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    kind: str,
    owner_id: UUID | None,
    period: date | None,
    transfer_id: str | None,
    tenant_id: UUID | None = None,
    lease_id: UUID | None = None,
) -> tuple[email.Attachment, ...]:
    """The files an email of this kind carries, made now."""
    # Imported here: the API's modules import this package's email module themselves.
    from pms.api import moveout, owner, receipts

    if kind == "deposit_statement" and tenant_id is not None and lease_id is not None:
        try:
            pdf = moveout.statement_pdf(conn, pmc_id, lease_id, tenant_id)
        except HTTPException as missing:
            raise LookupError(str(missing.detail)) from None
        return (email.Attachment("security-deposit-statement.pdf", pdf),)

    if kind == "payment_receipt" and transfer_id is not None:
        try:
            receipt = receipts.receipt_of(conn, pmc_id, transfer_id)
        except HTTPException as missing:
            raise LookupError(str(missing.detail)) from None
        return (
            email.Attachment(
                f"receipt-{receipt.received_on.isoformat()}.pdf",
                exports.to_pdf(receipt.table, datetime.now(UTC)),
            ),
        )
    if kind == "owner_statement" and owner_id is not None and period is not None:
        end = _month_end(period)
        table = owner.statement_table(conn, pmc_id, owner_id, period, end)
        titled = exports.Table(
            f"Owner statement, {period.isoformat()} to {end.isoformat()}", table.columns, table.rows
        )
        return (
            email.Attachment(
                f"owner-statement-{period.isoformat()}-{end.isoformat()}.pdf",
                exports.export(titled, "pdf"),
            ),
        )
    return ()
