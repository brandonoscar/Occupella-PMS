"""Where a line of an owner's statement or a tenant's ledger came from (the founder's review of
11/03, item 8): each line opens a side panel saying what it was.

The statement and the ledger are reports (trust_report_owner_statement, trust_report_tenant_ledger)
whose rows carry no ids, so their downloads stay as they are. Here the same postings are read in
the order each report lists them, and each row gets its source: a ledger transfer for the
statement; a charge, a payment or a returned payment for the ledger. Each is checked against the
row it is given to (the amount, the kind of line); if any row disagrees, no row gets a source, so a
line never opens another's.

- A tenant's payment: its receipt (src/pms/api/receipts.py), what it paid of each charge, and the
  receipt to download. Prepaid rent used and a housing authority's payment: what each paid.
- A bill: the vendor, its number, what it was for, its days, its amount and markup, and its
  receipts (an owner sees them, documents.OWNER_SEES).
- A management fee: how it was worked out from the agreement (percent of what was collected, the
  minimum, the flat fee).
- An owner draw: the day, the amount, how it left (check and number, or EFT) and the last four
  digits of the bank account an EFT went to.
- A contribution, a leasing fee, a fee taken from the owner, a returned payment: what it was.
Each is read only for the session's own owner or lease; any other reads as not found.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import psycopg
from fastapi import HTTPException
from pydantic import BaseModel

from pms.api.deps import Rows

if TYPE_CHECKING:
    from pms.api.receipts import Receipt

# receipts is imported where it is used: it reads the company (company.py), which reads the
# owner portal (owner.py), which reads this module.

CENT = Decimal("0.01")
NOT_YOURS_OWNER = "no such line of your statement"
NOT_YOURS_TENANT = "no such line of your ledger"

SourceKind = Literal[
    "payment",
    "returned payment",
    "bill",
    "management fee",
    "leasing fee",
    "owner draw",
    "contribution",
    "fee",
    "charge",
    "other",
]
LedgerKind = Literal["charge", "payment", "returned"]


class SourceRow(BaseModel):
    label: str
    value: str


class SourceLink(BaseModel):
    label: str
    url: str


class Source(BaseModel):
    kind: SourceKind
    title: str
    rows: list[SourceRow]
    links: list[SourceLink] = []


class TenantSource(BaseModel):
    kind: LedgerKind
    id: str


class StatementRows(Rows):
    # Each row's source: a posting's ledger transfer; None for the headings and totals.
    sources: list[str | None]


class PaymentLine(BaseModel):
    # A payment row of the tenant's ledger in words (the founder's review of 11/03, item 9): what
    # paid ("Rent payment"), how ("Check 2101"; None when nobody recorded it) and its receipt.
    what: str
    how: str | None
    receipt: str | None


class HeldLine(BaseModel):
    # What of a tenant's payment was held as prepaid rent (the founder's answer of 11/04, item 9):
    # a line of the ledger page of its own, its amount neither charged nor paid, so the balance
    # stays as the report has it; it counts as paid on its "Prepaid rent used" line.
    on: date
    what: str
    how: str | None
    amount: str
    receipt: str
    returned: bool


class LedgerRows(Rows):
    # Each row's source: a charge, a payment or a returned payment; None for the lease and the
    # balance.
    sources: list[TenantSource | None]
    # Each payment row in words; None for every other row.
    payments: list[PaymentLine | None]
    # What each payment held as prepaid rent, by day.
    held: list[HeldLine]


def us(day: date) -> str:
    return f"{day:%m/%d/%Y}"


def dollars(amount: Decimal | str) -> str:
    return f"${Decimal(amount):,.2f}"


def row(label: str, value: str) -> SourceRow:
    return SourceRow(label=label, value=value)


# --- the owner's statement ---------------------------------------------------------------------

# The postings of an owner's property accounts in a period, in the order
# trust_report_owner_statement lists them: by property (bytes), account, time, version.
OWNER_POSTINGS = """
SELECT tr.id, e.amount
FROM trust_ledger_accounts AS t
JOIN trust_properties AS pr ON pr.id = t.property_id
JOIN pgledger_entries AS e ON e.account_id = t.ledger_account_id
JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
WHERE t.pmc_id = %(pmc)s AND t.owner_id = %(owner)s AND t.kind = 'owner_property'
  AND tr.event_at >= %(start)s AND tr.event_at < %(end)s
ORDER BY pr.display_name COLLATE "C", t.ledger_account_id, tr.event_at, e.account_version
"""


def period(start: date, end: date) -> tuple[datetime, datetime]:
    """Whole days in UTC, from the first day's start to the end of the last."""
    return datetime.combine(start, time(0), UTC), datetime.combine(
        end + timedelta(days=1), time(0), UTC
    )


def statement_rows(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    owner_id: UUID,
    rows: Rows,
    start: datetime,
    end: datetime,
) -> StatementRows:
    """The statement as the report lists it, each posting with its ledger transfer."""
    found = conn.execute(
        OWNER_POSTINGS, {"pmc": pmc_id, "owner": owner_id, "start": start, "end": end}
    ).fetchall()
    item, amount = rows.columns.index("item"), rows.columns.index("amount")
    postings = [r for r in rows.rows if r[item] == "posting"]
    lined_up = len(postings) == len(found) and all(
        Decimal(str(r[amount])) == Decimal(change)
        for r, (_, change) in zip(postings, found, strict=True)
    )
    ids = iter(transfer for transfer, _ in found)
    sources = [next(ids) if lined_up and r[item] == "posting" else None for r in rows.rows]
    return StatementRows(title=rows.title, columns=rows.columns, rows=rows.rows, sources=sources)


def owners_transfer(
    conn: psycopg.Connection[Any], pmc_id: UUID, owner_id: UUID, transfer_id: str
) -> tuple[Decimal, datetime, dict[str, Any]]:
    """What a transfer did to one of the owner's property accounts: the change, when, and its
    metadata. Any other transfer reads as not found."""
    found = conn.execute(
        "SELECT e.amount, tr.event_at, tr.metadata"
        " FROM pgledger_entries AS e"
        " JOIN trust_ledger_accounts AS t ON t.ledger_account_id = e.account_id"
        " JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id"
        " WHERE e.transfer_id = %s AND t.pmc_id = %s AND t.owner_id = %s"
        " AND t.kind = 'owner_property'"
        " ORDER BY e.account_id LIMIT 1",
        (transfer_id, pmc_id, owner_id),
    ).fetchone()
    if found is None:
        raise HTTPException(404, NOT_YOURS_OWNER)
    return Decimal(found[0]), found[1], found[2] or {}


Finder = Callable[[psycopg.Connection[Any], str, Decimal, date], "Source | None"]


def owner_source(
    conn: psycopg.Connection[Any], pmc_id: UUID, owner_id: UUID, transfer_id: str
) -> Source:
    from pms.api import receipts

    change, at, meta = owners_transfer(conn, pmc_id, owner_id, transfer_id)
    on = at.astimezone(UTC).date()
    finders: tuple[Finder, ...] = (
        bill,
        management_fee,
        leasing_fee,
        owner_draw,
        contribution,
        owner_fee,
        returned_payment,
    )
    for finder in finders:
        found = finder(conn, transfer_id, change, on)
        if found is not None:
            return found
    if meta.get("kind") == "tenant payment":
        return Source(
            kind="payment",
            title="Tenant payment",
            rows=receipt_rows(receipts.receipt_of(conn, pmc_id, transfer_id)),
            links=[
                SourceLink(
                    label="Receipt (PDF)",
                    url=f"/api/owner/statement/sources/{transfer_id}/receipt",
                )
            ],
        )
    paid = paid_toward(conn, transfer_id)
    if paid:
        return Source(
            kind="payment",
            title=PAID_BY.get(str(meta.get("kind")), "Payment"),
            rows=[row("Date", us(on)), row("Amount", dollars(change)), *paid],
        )
    return Source(
        kind="other",
        title=str(meta.get("memo") or "Transaction"),
        rows=[row("Date", us(on)), row("Amount", dollars(change))],
    )


# What paid a tenant's charges, besides the tenant's own payment.
PAID_BY = {
    "prepaid rent used": "Prepaid rent used",
    "housing authority payment": "Housing authority payment",
}


def paid_toward(conn: psycopg.Connection[Any], transfer_id: str) -> list[SourceRow]:
    """What a transfer paid of each charge, as a receipt says it."""
    from pms.api import receipts

    return [
        row("Paid toward", f"{memo or CHARGE.get(kind, kind)}, due {us(due)}: {dollars(amount)}")
        for due, kind, memo, amount in conn.execute(receipts.PAID, ([transfer_id],)).fetchall()
    ]


def receipt_rows(receipt: Receipt) -> list[SourceRow]:
    """A receipt's lines, but who received it and its number, which the PDF keeps."""
    return [
        row(str(label), str(value))
        for label, value in receipt.table.rows
        if label not in ("Received by", "Receipt number")
    ]


BILL = """
SELECT b.id, v.display_name, b.reference, b.memo, b.bill_date, b.due_on, b.amount, mk.amount,
       mf.transfer_id IS NOT NULL
FROM trust_bills AS b
JOIN trust_vendors AS v ON v.id = b.vendor_id
LEFT JOIN trust_bill_markups AS mk ON mk.bill_id = b.id
LEFT JOIN trust_bill_markup_fees AS mf ON mf.bill_id = b.id AND mf.transfer_id = %(transfer)s
WHERE b.id IN (
    SELECT s.bill_id FROM trust_bill_payments AS s
    WHERE s.transfer_id = %(transfer)s AND s.step = 'set_aside'
    UNION ALL
    SELECT f.bill_id FROM trust_bill_markup_fees AS f WHERE f.transfer_id = %(transfer)s
)
LIMIT 1
"""


def bill(
    conn: psycopg.Connection[Any], transfer_id: str, change: Decimal, on: date
) -> Source | None:
    found = conn.execute(BILL, {"transfer": transfer_id}).fetchone()
    if found is None:
        return None
    bill_id, vendor, reference, memo, billed, due, amount, markup, is_markup = found
    rows = [
        row("Vendor", vendor),
        row("Bill #", reference),
        row("For", memo),
        row("Bill date", us(billed)),
        row("Due", us(due)),
        row("Amount", dollars(amount)),
    ]
    if markup is not None:
        rows.append(row("Markup", dollars(markup)))
    files = conn.execute(
        "SELECT d.id, d.title FROM trust_documents AS d"
        " WHERE d.bill_id = %s AND d.kind = 'receipt'"
        " AND NOT EXISTS (SELECT 1 FROM trust_document_removals AS r WHERE r.document_id = d.id)"
        " ORDER BY d.created_at, d.id",
        (bill_id,),
    ).fetchall()
    return Source(
        kind="bill",
        title=f"Markup on bill {reference}" if is_markup else f"Bill {reference}",
        rows=rows,
        links=[
            SourceLink(label=title, url=f"/api/owner/documents/{doc}/file") for doc, title in files
        ],
    )


def management_fee(
    conn: psycopg.Connection[Any], transfer_id: str, change: Decimal, on: date
) -> Source | None:
    found = conn.execute(
        "SELECT f.period_start, f.period_end, f.collected, f.fee,"
        " g.fee_percent, g.minimum_fee, g.flat_fee"
        " FROM trust_management_fees AS f"
        " JOIN trust_management_agreements AS g ON g.id = f.agreement_id"
        " WHERE f.transfer_id = %s",
        (transfer_id,),
    ).fetchone()
    if found is None:
        return None
    start, end = found[0], found[1]
    collected, fee, percent, minimum, flat = (Decimal(v) for v in found[2:])
    of_collected = (collected * percent / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    rows = [
        row("Period", f"{us(start)} to {us(end - timedelta(days=1))}"),
        row("Rent and fees collected", dollars(collected)),
        row(f"{percent.normalize():f}% of that", dollars(of_collected)),
    ]
    if minimum > 0:
        rows.append(row("Minimum fee", dollars(minimum)))
    if flat > 0:
        rows.append(row("Flat fee", dollars(flat)))
    if max(of_collected, minimum) + flat != fee:
        # Owned in shares (q044): the property's one fee, split by the owners' shares.
        rows.append(row("Split by ownership", "Your share of the property's fee"))
    rows.append(row("Fee", dollars(fee)))
    return Source(kind="management fee", title="Management fee", rows=rows)


def leasing_fee(
    conn: psycopg.Connection[Any], transfer_id: str, change: Decimal, on: date
) -> Source | None:
    found = conn.execute(
        "SELECT p.display_name, trust_unit_shown(u.id), f.fee FROM trust_leasing_fees AS f"
        " JOIN trust_leases AS l ON l.id = f.lease_id"
        " JOIN trust_units AS u ON u.id = l.unit_id"
        " JOIN trust_properties AS p ON p.id = u.property_id"
        " WHERE f.transfer_id = %s",
        (transfer_id,),
    ).fetchone()
    if found is None:
        return None
    prop, unit, fee = found
    return Source(
        kind="leasing fee",
        title="Leasing fee",
        rows=[
            row("Date", us(on)),
            row("For the lease at", prop if not unit else f"{prop}, {unit}"),
            row("Fee", dollars(fee)),
        ],
    )


def owner_draw(
    conn: psycopg.Connection[Any], transfer_id: str, change: Decimal, on: date
) -> Source | None:
    found = conn.execute(
        "SELECT d.amount, m.method, m.check_number, t.owner_id"
        " FROM trust_owner_draws AS d"
        " JOIN trust_ledger_accounts AS t ON t.ledger_account_id = d.ledger_account_id"
        " LEFT JOIN trust_payout_methods AS m ON m.transfer_id = d.transfer_id"
        " WHERE d.transfer_id = %s",
        (transfer_id,),
    ).fetchone()
    if found is None:
        return None
    amount, method, check_number, owner = found
    rows = [row("Date", us(on)), row("Amount", dollars(amount))]
    if method == "check":
        rows.append(row("How", f"Check No. {check_number}" if check_number else "Check"))
    elif method == "EFT":
        rows.append(row("How", "EFT"))
        # The bank account on file for the owner when the draw went out.
        last4 = conn.execute(
            "SELECT account_last4 FROM trust_payee_bank_accounts"
            " WHERE owner_id = %s AND created_at < %s ORDER BY created_at DESC, id DESC LIMIT 1",
            (owner, datetime.combine(on + timedelta(days=1), time(0), UTC)),
        ).fetchone()
        if last4 is not None:
            rows.append(row("To", f"Bank account ending {last4[0]}"))
    return Source(kind="owner draw", title="Paid to you", rows=rows)


def contribution(
    conn: psycopg.Connection[Any], transfer_id: str, change: Decimal, on: date
) -> Source | None:
    found = conn.execute(
        "SELECT amount FROM trust_owner_contributions WHERE transfer_id = %s", (transfer_id,)
    ).fetchone()
    if found is None:
        return None
    return Source(
        kind="contribution",
        title="Money you added",
        rows=[row("Date", us(on)), row("Amount", dollars(found[0]))],
    )


OWNER_FEE_KIND = {"inspection": "Inspection fee", "lease_up": "Lease-up fee"}


def owner_fee(
    conn: psycopg.Connection[Any], transfer_id: str, change: Decimal, on: date
) -> Source | None:
    found = conn.execute(
        "SELECT kind, memo, amount FROM trust_owner_fees WHERE transfer_id = %s", (transfer_id,)
    ).fetchone()
    if found is None:
        return None
    kind, memo, amount = found
    return Source(
        kind="fee",
        title=OWNER_FEE_KIND.get(kind, "Fee"),
        rows=[row("Date", us(on)), row("For", memo), row("Amount", dollars(amount))],
    )


def returned_payment(
    conn: psycopg.Connection[Any], transfer_id: str, change: Decimal, on: date
) -> Source | None:
    if not conn.execute(
        "SELECT 1 FROM trust_payment_reversals WHERE reversal_id = %s LIMIT 1", (transfer_id,)
    ).fetchone():
        return None
    return Source(
        kind="returned payment",
        title="Payment returned",
        rows=[
            row("Date", us(on)),
            row("Amount", dollars(change)),
            row("What happened", "A tenant's payment came back unpaid."),
        ],
    )


# --- the tenant's ledger -----------------------------------------------------------------------

# A lease's charges, payments and returned payments through a day, in the order
# trust_report_tenant_ledger lists them: by day, then charges, payments and returns, then time.
LEDGER_ENTRIES = """
WITH entries AS (
    SELECT c.due_on AS on_date, 1 AS step, c.created_at AS at, c.id::text AS id
    FROM trust_charges AS c
    WHERE c.lease_id = %(lease)s AND c.due_on <= %(as_of)s
    UNION ALL
    SELECT (tr.event_at AT TIME ZONE 'UTC')::date, 2, tr.event_at, tr.id
    FROM trust_charge_payments AS cp
    JOIN trust_charges AS c ON c.id = cp.charge_id
    JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
    WHERE c.lease_id = %(lease)s AND tr.event_at < %(cutoff)s
    GROUP BY tr.id, tr.event_at
    UNION ALL
    SELECT (tr.event_at AT TIME ZONE 'UTC')::date, 3, tr.event_at, tr.id
    FROM trust_payment_reversals AS r
    JOIN trust_charges AS c ON c.id = r.charge_id
    JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id
    WHERE c.lease_id = %(lease)s AND tr.event_at < %(cutoff)s
    GROUP BY tr.id, tr.event_at
)
SELECT step, id FROM entries ORDER BY on_date, step, at, id
"""
STEP: dict[int, LedgerKind] = {1: "charge", 2: "payment", 3: "returned"}
# A ledger row's item for a payment and a returned payment; any other item is a charge.
ITEM = {"payment": "payment", "bounced payment": "returned"}
# Lines that open nothing: the lease's, its balance's, and memo lines that change no balance (a
# deposit the owner holds, outside trust, migration 20261106000241).
NO_SOURCE = ("lease", "balance", "owner-held deposit", "owner-held deposit settled")


def ledger_rows(
    conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID, rows: Rows, as_of: date
) -> LedgerRows:
    """The ledger as the report lists it, each charge, payment and return with its id."""
    cutoff = datetime.combine(as_of + timedelta(days=1), time(0), UTC)
    found = [
        TenantSource(kind=STEP[step], id=ident)
        for step, ident in conn.execute(
            LEDGER_ENTRIES, {"lease": lease_id, "as_of": as_of, "cutoff": cutoff}
        ).fetchall()
    ]
    item = rows.columns.index("item")
    entries = [r for r in rows.rows if r[item] not in NO_SOURCE]
    lined_up = len(entries) == len(found) and all(
        ITEM.get(str(r[item]), "charge") == source.kind
        for r, source in zip(entries, found, strict=True)
    )
    sources = iter(found)
    given = [next(sources) if lined_up and r[item] not in NO_SOURCE else None for r in rows.rows]
    words = payment_lines(conn, [s.id for s in given if s is not None and s.kind == "payment"])
    return LedgerRows(
        title=rows.title,
        columns=rows.columns,
        rows=rows.rows,
        sources=given,
        payments=[words.get(s.id) if s is not None else None for s in given],
        held=held_lines(conn, pmc_id, lease_id, cutoff),
    )


# Each part of a tenant's payment to the lease held as prepaid rent, before a time, and whether the
# payment came back since (a bounce posts under its key, "bounce/<key>").
HELD = """
SELECT t.id, t.event_at, t.amount, t.metadata,
       EXISTS (SELECT 1 FROM pgledger_transfers AS b
               JOIN trust_ledger_accounts AS ba ON ba.ledger_account_id = b.to_account_id
               WHERE ba.pmc_id = %(pmc)s AND b.event_at < %(cutoff)s
                 AND b.metadata ->> 'key' = 'bounce/' || (t.metadata ->> 'key'))
FROM pgledger_transfers AS t
JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id
WHERE a.pmc_id = %(pmc)s AND a.kind = 'prepaid_rent'
  AND t.metadata ->> 'kind' = 'tenant payment' AND t.metadata ->> 'lease_id' = %(lease)s
  AND t.event_at < %(cutoff)s
ORDER BY t.event_at, t.id
"""


def held_lines(
    conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID, cutoff: datetime
) -> list[HeldLine]:
    """What each of the lease's payments held as prepaid rent, as the ledger page shows it."""
    return [
        HeldLine(
            on=at.astimezone(UTC).date(),
            what="Rent payment",
            how=came((meta or {}).get("method"), (meta or {}).get("reference")),
            amount=f"{Decimal(amount):.2f}",
            receipt=f"/api/tenant/payments/{transfer}/receipt",
            returned=returned,
        )
        for transfer, at, amount, meta, returned in conn.execute(
            HELD, {"pmc": pmc_id, "lease": str(lease_id), "cutoff": cutoff}
        ).fetchall()
    ]


# How a payment came, as a ledger line says it: a payment made online is a bank debit (ACH).
HOW = {
    "check": "Check",
    "cashier's check": "Cashier's check",
    "money order": "Money order",
    "EFT": "EFT",
    "cash": "Cash",
    "online": "ACH",
}


def came(method: str | None, reference: str | None) -> str | None:
    """How a payment came: its method and reference ("Check 2101"), the method once when the
    reference already says it, None when neither was recorded."""
    how = HOW.get(method or "")
    if how and reference and reference.lower().startswith(how.lower()):
        return reference
    return " ".join(p for p in (how, reference) if p) or None


def payment_lines(conn: psycopg.Connection[Any], transfers: list[str]) -> dict[str, PaymentLine]:
    """Each payment of a ledger in words: a tenant's own payment is a rent payment, how it came
    (its method and reference, as staff or the payments partner recorded them) and its receipt;
    prepaid rent used and a housing authority's payment say what they were."""
    lines = {}
    for transfer, meta in conn.execute(
        "SELECT id, metadata FROM pgledger_transfers WHERE id = ANY(%s)", (transfers,)
    ).fetchall():
        meta = meta or {}
        kind = str(meta.get("kind"))
        if kind != "tenant payment":
            # Prepaid rent used, a housing authority's payment; anything else keeps its memo.
            said = PAID_BY.get(kind)
            lines[transfer] = PaymentLine(
                what=said or "Payment", how=None if said else meta.get("memo"), receipt=None
            )
            continue
        lines[transfer] = PaymentLine(
            what="Rent payment",
            how=came(meta.get("method"), meta.get("reference")),
            receipt=f"/api/tenant/payments/{transfer}/receipt",
        )
    return lines


CHARGE = {"rent": "Rent", "fee": "Fee", "credit": "Credit"}


def tenant_source(
    conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID, kind: LedgerKind, source_id: str
) -> Source:
    """A charge, payment or return of the lease (the caller has checked the lease is the
    tenant's); any other reads as not found."""
    if kind == "charge":
        return charge_source(conn, pmc_id, lease_id, source_id)
    if kind == "payment":
        return payment_source(conn, pmc_id, lease_id, source_id)
    found = conn.execute(
        "SELECT tr.event_at, sum(r.amount) FROM trust_payment_reversals AS r"
        " JOIN trust_charges AS c ON c.id = r.charge_id"
        " JOIN pgledger_transfers AS tr ON tr.id = r.reversal_id"
        " WHERE r.reversal_id = %s AND c.lease_id = %s AND c.pmc_id = %s"
        " GROUP BY tr.event_at",
        (source_id, lease_id, pmc_id),
    ).fetchone()
    if found is None:
        raise HTTPException(404, NOT_YOURS_TENANT)
    return Source(
        kind="returned payment",
        title="Payment returned",
        rows=[
            row("Date", us(found[0].astimezone(UTC).date())),
            row("Amount", dollars(found[1])),
            row("What happened", "The payment came back unpaid."),
        ],
    )


def charge_source(
    conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID, source_id: str
) -> Source:
    try:
        charge = UUID(source_id)
    except ValueError:
        raise HTTPException(404, NOT_YOURS_TENANT) from None
    found = conn.execute(
        "SELECT c.kind, c.memo, c.due_on, c.amount,"
        " coalesce((SELECT o.open FROM trust_open_charges(c.pmc_id, c.lease_id, 'infinity') AS o"
        "           WHERE o.charge_id = c.id), 0)"
        " FROM trust_charges AS c WHERE c.id = %s AND c.lease_id = %s AND c.pmc_id = %s",
        (charge, lease_id, pmc_id),
    ).fetchone()
    if found is None:
        raise HTTPException(404, NOT_YOURS_TENANT)
    what, memo, due, amount, still_owed = found
    name = memo or CHARGE.get(what, what)
    rows = [row("What", name), row("Due", us(due)), row("Amount", dollars(amount))]
    if what != "credit":
        rows.append(row("Still owed", dollars(still_owed)))
    return Source(kind="charge", title=CHARGE.get(what, "Charge"), rows=rows)


def payment_source(
    conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID, source_id: str
) -> Source:
    from pms.api import receipts

    found = conn.execute(
        "SELECT tr.event_at, tr.metadata, sum(cp.amount) FROM trust_charge_payments AS cp"
        " JOIN trust_charges AS c ON c.id = cp.charge_id"
        " JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id"
        " WHERE cp.transfer_id = %s AND c.lease_id = %s AND c.pmc_id = %s"
        " GROUP BY tr.event_at, tr.metadata",
        (source_id, lease_id, pmc_id),
    ).fetchone()
    if found is None:
        raise HTTPException(404, NOT_YOURS_TENANT)
    at, meta, amount = found
    if (meta or {}).get("kind") == "tenant payment":
        return Source(
            kind="payment",
            title="Payment",
            rows=receipt_rows(receipts.receipt_of(conn, pmc_id, source_id)),
            links=[
                SourceLink(label="Receipt (PDF)", url=f"/api/tenant/payments/{source_id}/receipt")
            ],
        )
    return Source(
        kind="payment",
        title=PAID_BY.get(str((meta or {}).get("kind")), "Payment"),
        rows=[
            row("Date", us(at.astimezone(UTC).date())),
            row("Amount", dollars(amount)),
            *paid_toward(conn, source_id),
        ],
    )
