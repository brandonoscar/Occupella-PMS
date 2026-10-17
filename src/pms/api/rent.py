"""Staff's rent work: charge the month's rent (with each lease's recurring charges) and late
fees, add a fee or a credit once or every month, record a tenant's payment or security deposit,
use prepaid rent, and undo a payment that bounced (charging the property's NSF fee).

What a lease owes (its rent and fees, less its credits, less what payments matched, plus what
bounced back) stays out of the trust ledger. Money is recorded as it reached the trust bank
account, and only through the database's write paths: trust_post_transfers (with a key built
from the request's id, so a retry posts nothing twice), trust_apply_payment and
trust_reverse_payment. A payment pays the owner's account for what the lease owes, matched to
its open charges oldest first; anything more is held for the tenant as prepaid rent.

Nothing here sends money anywhere: the PMS records what the bank already did.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, StringConstraints

from pms import exports, notices
from pms.api import outbox
from pms.api.deps import Conn, Staff, refusal

router = APIRouter(prefix="/api/rent")

Amount = Annotated[Decimal, Field(gt=0, le=Decimal("1000000"), decimal_places=2)]
Memo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ZERO = Decimal(0)
CENT = Decimal("0.01")


def cents(value: Decimal) -> str:
    """Money as text, always to the cent ("0.00", never "0")."""
    return exports.text(Decimal(value).quantize(CENT))


def today() -> date:
    return datetime.now(UTC).date()


def at(day: date) -> datetime:
    """A day's postings are dated at its start, UTC, as every report reads them."""
    return datetime.combine(day, time(0), UTC)


# --- reading a lease ------------------------------------------------------------------------


@dataclass(frozen=True)
class LeaseRow:
    id: UUID
    property_id: UUID
    tenants: list[UUID]


def lease_of(conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID) -> LeaseRow:
    row = conn.execute(
        "SELECT u.property_id, array(SELECT lt.tenant_id FROM trust_lease_tenants AS lt"
        "  JOIN trust_tenants AS t ON t.id = lt.tenant_id WHERE lt.lease_id = l.id"
        # Tenants staying on the lease first: the first holds what is paid ahead.
        "  ORDER BY EXISTS (SELECT 1 FROM trust_lease_departures AS d"
        "                   WHERE d.lease_id = lt.lease_id AND d.tenant_id = lt.tenant_id),"
        '  t.display_name COLLATE "C", t.id)'
        " FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id"
        " WHERE l.pmc_id = %s AND l.id = %s",
        (pmc_id, lease_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such lease")
    return LeaseRow(lease_id, row[0], row[1])


def lease_memo(conn: psycopg.Connection[Any], lease_id: UUID) -> str:
    """Whose money and for which home, for a receipt's memo: "Morgan Demo, Birch Flats Unit A"
    (the punch list, ADR 0006: the books side of a reconciliation said "No memo")."""
    row = conn.execute(
        "SELECT (SELECT string_agg(t.display_name, ', '"
        '                           ORDER BY t.display_name COLLATE "C", t.id)'
        "        FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id"
        "        WHERE lt.lease_id = l.id), p.display_name, u.display_name"
        " FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id"
        " JOIN trust_properties AS p ON p.id = u.property_id WHERE l.id = %s",
        (lease_id,),
    ).fetchone()
    assert row is not None
    names, prop, unit = row
    return f"{names}, {prop} {unit}"


# What lease l owes: its rent and fees, less its credits, less what payments matched, plus
# what bounced back.
OWED = """
coalesce((SELECT sum(CASE WHEN c.kind = 'credit' THEN -c.amount ELSE c.amount END)
          FROM trust_charges AS c WHERE c.lease_id = l.id), 0)
- coalesce((SELECT sum(p.amount) FROM trust_charge_payments AS p
            JOIN trust_charges AS x ON x.id = p.charge_id WHERE x.lease_id = l.id), 0)
+ coalesce((SELECT sum(r.amount) FROM trust_payment_reversals AS r
            JOIN trust_charges AS x ON x.id = r.charge_id WHERE x.lease_id = l.id), 0)
"""


def owed(conn: psycopg.Connection[Any], lease_id: UUID) -> Decimal:
    row = conn.execute(f"SELECT {OWED} FROM trust_leases AS l WHERE l.id = %s", (lease_id,))
    found = row.fetchone()
    assert found is not None
    return Decimal(found[0])


# Rent and fees not fully paid, oldest first, with what is still open of each: less what
# payments matched and plus what bounced, less the lease's credits, which come off its oldest
# open charges first (trust_open_charges, migration 0049).
OPEN_CHARGES = """
SELECT c.lease_id, c.id, c.due_on, c.kind, c.memo, c.amount, o.open
FROM trust_open_charges(%s, %s, 'infinity') AS o
JOIN trust_charges AS c ON c.id = o.charge_id
WHERE o.open > 0
ORDER BY c.due_on, c.created_at, c.id
"""


def charges_open(
    conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID | None = None
) -> list[tuple[Any, ...]]:
    """The open charges of the PMC's leases, or of one of its leases."""
    return conn.execute(OPEN_CHARGES, (pmc_id, lease_id)).fetchall()


def open_charges(
    conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID
) -> list[tuple[UUID, Decimal]]:
    return [(row[1], Decimal(row[6])) for row in charges_open(conn, pmc_id, lease_id)]


def allocate(charges: list[tuple[UUID, Decimal]], amount: Decimal) -> list[tuple[UUID, Decimal]]:
    """`amount` spread over open charges oldest first, each up to what is open of it."""
    matches = []
    for charge, left in charges:
        if amount <= 0:
            break
        part = min(left, amount)
        matches.append((charge, part))
        amount -= part
    return matches


# --- ledger accounts --------------------------------------------------------------------------


def owner_account(
    conn: psycopg.Connection[Any], pmc_id: UUID, property_id: UUID, chosen: str | None
) -> tuple[str, UUID]:
    """The owner's account a property's rent goes to, and its trust bank account: the only one,
    or the one staff chose when the property has more than one owner account."""
    rows = conn.execute(
        "SELECT ledger_account_id, bank_account_id FROM trust_ledger_accounts"
        " WHERE pmc_id = %s AND kind = 'owner_property' AND property_id = %s"
        " ORDER BY ledger_account_id",
        (pmc_id, property_id),
    ).fetchall()
    if chosen is not None:
        rows = [row for row in rows if row[0] == chosen]
        if not rows:
            raise HTTPException(404, "no such owner account for this lease's property")
    if not rows:
        raise HTTPException(409, "the lease's property has no owner account to pay rent into")
    if len(rows) > 1:
        raise HTTPException(422, "the property has more than one owner account; choose one")
    return rows[0][0], rows[0][1]


def cash_of(conn: psycopg.Connection[Any], bank_id: UUID) -> str:
    row = conn.execute(
        "SELECT ledger_account_id FROM trust_ledger_accounts"
        " WHERE bank_account_id = %s AND kind = 'bank_cash'",
        (bank_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(409, "the trust bank account has no cash account; set it up again")
    return str(row[0])


def tenant_account(
    conn: psycopg.Connection[Any], pmc_id: UUID, bank_id: UUID, kind: str, tenant_id: UUID
) -> str:
    """The tenant's prepaid rent or deposit account in a trust bank account, opened if new."""
    row = conn.execute(
        "SELECT ledger_account_id FROM trust_ledger_accounts"
        " WHERE bank_account_id = %s AND kind = %s AND tenant_id = %s",
        (bank_id, kind, tenant_id),
    ).fetchone()
    if row is not None:
        return str(row[0])
    opened = conn.execute(
        "SELECT trust_open_ledger_account(%s, %s, %s, NULL, NULL, %s)",
        (pmc_id, bank_id, kind, tenant_id),
    ).fetchone()
    assert opened is not None
    return str(opened[0])


def balances(conn: psycopg.Connection[Any], accounts: list[str]) -> dict[str, Decimal]:
    return {
        account: Decimal(balance)
        for account, balance in conn.execute(
            "SELECT id, balance FROM pgledger_accounts WHERE id = ANY(%s)", (accounts,)
        ).fetchall()
    }


# --- posting ----------------------------------------------------------------------------------


class Conflict(Exception):
    """The request id was used for a different posting."""


def post(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    key: str,
    transfers: list[tuple[str, str, Decimal]],
    day: date,
    metadata: dict[str, Any],
) -> list[str] | None:
    """Posts the transfers under `key`; their ids, or None when the key was used before for
    something else (a retry after the books moved on, or another request)."""
    rows = sql.SQL(", ").join(sql.SQL("(%s, %s, %s::numeric)::transfer_request") for _ in transfers)
    query = sql.SQL(
        "SELECT id FROM trust_post_transfers(%s, %s, ARRAY[{}]::transfer_request[], %s, %s)"
    ).format(rows)
    params = [pmc_id, key, *(value for transfer in transfers for value in transfer)]
    try:
        with conn.transaction():  # a savepoint: a refused key leaves the request usable
            return [
                str(row[0])
                for row in conn.execute(query, [*params, at(day), Jsonb(metadata)]).fetchall()
            ]
    except psycopg.errors.UniqueViolation:
        return None


DESCRIBED = ("reference", "method", "memo")
# How a tenant's payment came: recorded as said, nothing sent (ADR 0006).
Method = Literal["check", "cashier's check", "money order", "EFT", "cash"]


def earlier(
    conn: psycopg.Connection[Any], pmc_id: UUID, key: str, metadata: dict[str, Any]
) -> list[str]:
    """The transfers an earlier request with this key posted, if it asked for the same thing
    (a retry); Conflict if it asked for something else."""
    rows = conn.execute(
        "SELECT t.id, t.metadata FROM pgledger_transfers AS t"
        " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id"
        " WHERE a.pmc_id = %s AND t.metadata ->> 'key' = %s ORDER BY t.created_at, t.id",
        (pmc_id, key),
    ).fetchall()
    # What describes a payment (its check number, how it came) doesn't change what it asked for.
    asked = {k: v for k, v in metadata.items() if k not in DESCRIBED}
    if not rows or any(
        {k: v for k, v in m.items() if k not in DESCRIBED} != asked for _, m in rows
    ):
        raise Conflict
    return [str(row[0]) for row in rows]


def lock_lease(conn: psycopg.Connection[Any], lease_id: UUID) -> None:
    """Rent work on one lease waits for the rest (so what it owes is read once at a time)."""
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('rent of lease ' || %s, 0))",
        (str(lease_id),),
    )


def match(
    conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID, transfer: str, amount: Decimal
) -> list[tuple[UUID, Decimal]]:
    """Matches a transfer to the lease's open charges, oldest first. A transfer matched before
    (posted again by a retry that asked for the very same thing) is left as it was."""
    if conn.execute(
        "SELECT 1 FROM trust_charge_payments WHERE transfer_id = %s LIMIT 1", (transfer,)
    ).fetchone():
        return []
    matched = allocate(open_charges(conn, pmc_id, lease_id), amount)
    for charge, part in matched:
        conn.execute("SELECT trust_apply_payment(%s, %s, %s, %s)", (pmc_id, charge, transfer, part))
    return matched


@dataclass(frozen=True)
class Recorded:
    transfers: list[str]
    to_owner: Decimal
    to_prepaid: Decimal


def record_payment(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    lease_id: UUID,
    request_id: UUID,
    amount: Decimal,
    received_on: date,
    reference: str | None = None,
    account: str | None = None,
    method: Method | None = None,
    past_hold_by: str | None = None,
) -> Recorded:
    """A tenant's payment into the operating trust bank account: the owner's account takes
    what the lease owes, matched to its charges oldest first; the rest is the first tenant's
    prepaid rent. The same request id again returns the payment as it was recorded. A lease on
    a payment hold refuses it, unless staff accept it past the hold (`past_hold_by` names them):
    then every hold in force that day allows it, in the same transaction."""
    lease = lease_of(conn, pmc_id, lease_id)
    owner, bank = owner_account(conn, pmc_id, lease.property_id, account)
    lock_lease(conn, lease_id)
    key = f"payment/{request_id}"
    metadata = {
        "key": key,
        "kind": "tenant payment",
        "lease_id": str(lease_id),
        "amount": cents(amount),
        "received_on": received_on.isoformat(),
        "reference": reference,
        "method": method,
        "memo": f"Rent – {lease_memo(conn, lease_id)}",
    }
    to_owner = min(amount, max(owed(conn, lease_id), ZERO))
    to_prepaid = amount - to_owner
    cash = cash_of(conn, bank)
    transfers = []
    if to_owner > 0:
        transfers.append((cash, owner, to_owner))
    if to_prepaid > 0:
        prepaid = tenant_account(conn, pmc_id, bank, "prepaid_rent", lease.tenants[0])
        transfers.append((cash, prepaid, to_prepaid))
    posted = post(conn, pmc_id, key, transfers, received_on, metadata)
    if posted is None:
        return replay(conn, pmc_id, key, metadata, owner)
    if to_owner > 0:
        if past_hold_by is not None:
            allow_past_holds(conn, pmc_id, lease_id, posted[0], received_on, past_hold_by)
        match(conn, pmc_id, lease_id, posted[0], to_owner)
    return Recorded(posted, to_owner, to_prepaid)


# The payment holds of a lease in force on a day: from a hold's first day until its release ends
# it, as trust_apply_payment reads them.
IN_FORCE = """
SELECT h.id FROM trust_payment_holds AS h
LEFT JOIN trust_payment_hold_releases AS r ON r.hold_id = h.id
WHERE h.pmc_id = %(pmc)s AND h.lease_id = %(lease)s AND h.starts_on <= %(day)s
  AND (r.ends_on IS NULL OR %(day)s < r.ends_on)
"""


def allow_past_holds(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    lease_id: UUID,
    transfer_id: str,
    day: date,
    by: str,
) -> None:
    """Lets one payment past every hold of the lease in force on its day, naming who did."""
    for (hold,) in conn.execute(IN_FORCE, {"pmc": pmc_id, "lease": lease_id, "day": day}):
        conn.execute(
            "INSERT INTO trust_payment_hold_allowances (hold_id, transfer_id, pmc_id, allowed_by)"
            " VALUES (%s, %s, %s, %s)",
            (hold, transfer_id, pmc_id, by),
        )


def replay(
    conn: psycopg.Connection[Any], pmc_id: UUID, key: str, metadata: dict[str, Any], owner: str
) -> Recorded:
    try:
        posted = earlier(conn, pmc_id, key, metadata)
    except Conflict:
        raise HTTPException(409, "this request id was used for a different payment") from None
    rows = conn.execute(
        "SELECT to_account_id, amount FROM pgledger_transfers WHERE id = ANY(%s)", (posted,)
    ).fetchall()
    to_owner = sum((Decimal(a) for to, a in rows if to == owner), ZERO)
    return Recorded(posted, to_owner, sum((Decimal(a) for _, a in rows), ZERO) - to_owner)


def use_prepaid(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    lease_id: UUID,
    request_id: UUID,
    on: date,
    account: str | None = None,
) -> Recorded:
    """The lease's tenants' prepaid rent paid to the owner's account for what the lease owes,
    matched to its charges oldest first."""
    lease = lease_of(conn, pmc_id, lease_id)
    owner, bank = owner_account(conn, pmc_id, lease.property_id, account)
    lock_lease(conn, lease_id)
    key = f"prepaid/{request_id}"
    metadata = {
        "key": key,
        "kind": "prepaid rent used",
        "lease_id": str(lease_id),
        "on": on.isoformat(),
        "reference": None,
    }
    prepaid = {
        tenant: tenant_account(conn, pmc_id, bank, "prepaid_rent", tenant)
        for tenant in lease.tenants
    }
    held = balances(conn, list(prepaid.values()))
    left = max(owed(conn, lease_id), ZERO)
    transfers = []
    for account_id in prepaid.values():
        part = min(held.get(account_id, ZERO), left)
        if part > 0:
            transfers.append((account_id, owner, part))
            left -= part
    if not transfers:
        try:  # a retry finds nothing left to use: what it used the first time
            earlier(conn, pmc_id, key, metadata)
        except Conflict:
            raise HTTPException(
                409, "there is no prepaid rent to use, or nothing is owed"
            ) from None
        return replay(conn, pmc_id, key, metadata, owner)
    posted = post(conn, pmc_id, key, transfers, on, metadata)
    if posted is None:
        return replay(conn, pmc_id, key, metadata, owner)
    for transfer, (_, _, part) in zip(posted, transfers, strict=True):
        match(conn, pmc_id, lease_id, transfer, part)
    return Recorded(posted, sum((t[2] for t in transfers), ZERO), ZERO)


@dataclass(frozen=True)
class Returned:
    transfers: list[str]
    nsf_fee: Decimal | None  # the NSF fee charged for it, if the property's terms charge one


def bounce(conn: psycopg.Connection[Any], pmc_id: UUID, transfer_id: str, on: date) -> Returned:
    """A payment the bank returned: its transfers move back to cash, their matches to charges
    are reversed (trust_reverse_payment), and the lease is charged the property's NSF fee
    (trust_charge_nsf_fee). Once per payment."""
    rows = conn.execute(
        "SELECT t.id, t.from_account_id, t.to_account_id, t.amount, t.metadata, t.event_at"
        " FROM pgledger_transfers AS t"
        " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id"
        " WHERE a.pmc_id = %s AND t.metadata ->> 'key' = ("
        "   SELECT p.metadata ->> 'key' FROM pgledger_transfers AS p WHERE p.id = %s"
        "     AND p.metadata ->> 'kind' = 'tenant payment')"
        " ORDER BY t.created_at, t.id",
        (pmc_id, transfer_id),
    ).fetchall()
    if not rows:
        raise HTTPException(404, "no such tenant payment")
    payment_key = rows[0][4]["key"]
    lease_id = UUID(rows[0][4]["lease_id"])
    lock_lease(conn, lease_id)
    if on < rows[0][5].astimezone(UTC).date():
        raise HTTPException(422, "a payment bounces on or after the day it was received")
    key = f"bounce/{payment_key}"
    metadata = {
        "key": key,
        "kind": "bounced payment",
        "lease_id": str(lease_id),
        "reference": None,
        "memo": f"Returned payment – {lease_memo(conn, lease_id)}",
    }
    transfers = [(to, frm, Decimal(amount)) for _, frm, to, amount, _, _ in rows]
    posted = post(conn, pmc_id, key, transfers, on, metadata)
    if posted is None:
        raise HTTPException(409, "this payment bounced already")
    for (payment, *_), reversal in zip(rows, posted, strict=True):
        for charge, matched in conn.execute(
            "SELECT charge_id, amount FROM trust_charge_payments WHERE transfer_id = %s",
            (payment,),
        ).fetchall():
            conn.execute(
                "SELECT trust_reverse_payment(%s, %s, %s, %s, %s)",
                (pmc_id, charge, payment, reversal, matched),
            )
    # Two statements: one that read the charge in the same statement as the call wouldn't see it.
    charged = conn.execute("SELECT trust_charge_nsf_fee(%s, %s)", (pmc_id, key)).fetchone()
    fee = charged[0] if charged else None  # None: the terms charge none
    amount = conn.execute("SELECT amount FROM trust_charges WHERE id = %s", (fee,)).fetchone()
    return Returned(posted, None if amount is None else amount[0])


def record_deposit(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    tenant_id: UUID,
    request_id: UUID,
    amount: Decimal,
    received_on: date,
    bank_id: UUID | None = None,
    reference: str | None = None,
) -> list[str]:
    """A tenant's security deposit into a security-deposit trust bank account: the only one,
    or the one staff chose."""
    banks = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM trust_bank_accounts WHERE pmc_id = %s AND kind = 'security_deposit'"
            " AND (%s::uuid IS NULL OR id = %s::uuid) ORDER BY id",
            (pmc_id, bank_id, bank_id),
        ).fetchall()
    ]
    if not banks:
        raise HTTPException(409, "set up a security deposit trust bank account first")
    if len(banks) > 1:
        raise HTTPException(422, "the PMC has more than one security deposit account; choose one")
    key = f"deposit/{request_id}"
    who = conn.execute(
        "SELECT t.display_name, p.display_name FROM trust_tenants AS t"
        " JOIN trust_properties AS p ON p.id = t.property_id WHERE t.pmc_id = %s AND t.id = %s",
        (pmc_id, tenant_id),
    ).fetchone()
    metadata = {
        "key": key,
        "kind": "security deposit",
        "tenant_id": str(tenant_id),
        "amount": cents(amount),
        "received_on": received_on.isoformat(),
        "reference": reference,
        "memo": None if who is None else f"Security deposit – {who[0]}, {who[1]}",
    }
    deposit = tenant_account(conn, pmc_id, banks[0], "tenant_deposit", tenant_id)
    posted = post(
        conn, pmc_id, key, [(cash_of(conn, banks[0]), deposit, amount)], received_on, metadata
    )
    if posted is None:
        try:
            return earlier(conn, pmc_id, key, metadata)
        except Conflict:
            raise HTTPException(409, "this request id was used for a different deposit") from None
    return posted


# --- the API ------------------------------------------------------------------------------------


class OpenCharge(BaseModel):
    id: UUID
    due_on: date
    kind: Literal["rent", "fee"]
    memo: str | None
    amount: str
    open: str


class Paid(BaseModel):
    transfer_id: str
    received_on: date
    amount: str
    reference: str | None
    method: Method | None
    bounced: bool


class LeaseTenant(BaseModel):
    id: UUID
    name: str
    prepaid: str
    deposit: str


class RecurringCharge(BaseModel):
    id: UUID
    kind: Literal["fee", "credit"]
    memo: str
    amount: str
    starts_on: date
    ends_on: date | None  # the first due date it is no longer charged for


class RentChange(BaseModel):
    id: UUID
    effective_on: date
    monthly_rent: str
    notice_given_on: date
    served_by: str
    lease_terms: str | None


class RentersInsurance(BaseModel):
    required: bool
    minimum_liability: str | None
    # The policy that counts on the day: of those started by then, the one that runs out last.
    carrier: str | None
    policy_number: str | None
    expires_on: date | None
    liability_limit: str | None
    # current, expires today, expires in N days (30 or fewer), expired, or none on file.
    standing: str
    below_minimum: bool


RENTERS_INSURANCE = """
SELECT lease_id, required, minimum_liability, carrier, policy_number, expires_on,
       liability_limit, standing, below_minimum
FROM trust_report_renters_insurance(%s, %s)
"""


def renters_insurance(
    conn: psycopg.Connection[Any], pmc_id: UUID, on: date
) -> dict[UUID, RentersInsurance]:
    """Each lease in effect on the day: what its renters insurance stands at."""
    return {
        lease: RentersInsurance(
            required=required,
            minimum_liability=None if minimum is None else cents(minimum),
            carrier=carrier,
            policy_number=number,
            expires_on=expires,
            liability_limit=None if limit is None else cents(limit),
            standing=standing,
            below_minimum=below,
        )
        for lease, required, minimum, carrier, number, expires, limit, standing, below in (
            conn.execute(RENTERS_INSURANCE, (pmc_id, on)).fetchall()
        )
    }


class RentLease(BaseModel):
    id: UUID
    property: str
    unit: str
    starts_on: date
    ends_on: date | None
    # The rent in force on the day; changes recorded, past and to come, in rent_changes.
    monthly_rent: str
    rent_changes: list[RentChange]
    tenants: list[LeaseTenant]
    owed: str
    on_hold: bool
    open_charges: list[OpenCharge]
    payments: list[Paid]
    recurring: list[RecurringCharge]
    # A lease in effect on the day: its renters insurance; None for one that isn't.
    renters_insurance: RentersInsurance | None


LEASES = f"""
SELECT l.id, p.display_name, u.display_name, l.starts_on, l.ends_on,
       trust_rent_on(l.id, %(as_of)s), {OWED},
       EXISTS (SELECT 1 FROM trust_payment_holds AS h
               LEFT JOIN trust_payment_hold_releases AS r ON r.hold_id = h.id
               WHERE h.lease_id = l.id AND h.starts_on <= %(as_of)s
                 AND (r.ends_on IS NULL OR %(as_of)s < r.ends_on))
FROM trust_leases AS l
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
WHERE l.pmc_id = %(pmc)s AND (%(lease)s::uuid IS NULL OR l.id = %(lease)s)
ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C", l.starts_on, l.id
"""

TENANTS = """
SELECT lt.lease_id, t.id, t.display_name,
       coalesce((SELECT sum(b.balance) FROM trust_ledger_accounts AS a
                 JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
                 WHERE a.tenant_id = t.id AND a.kind = 'prepaid_rent'), 0),
       coalesce((SELECT sum(b.balance) FROM trust_ledger_accounts AS a
                 JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
                 WHERE a.tenant_id = t.id AND a.kind = 'tenant_deposit'), 0)
FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id
WHERE lt.pmc_id = %(pmc)s AND (%(lease)s::uuid IS NULL OR lt.lease_id = %(lease)s)
ORDER BY t.display_name COLLATE "C", t.id
"""

RENT_CHANGES = """
SELECT c.lease_id, c.id, c.effective_on, c.monthly_rent, c.notice_given_on, c.served_by,
       c.lease_terms
FROM trust_rent_changes AS c
WHERE c.pmc_id = %(pmc)s AND (%(lease)s::uuid IS NULL OR c.lease_id = %(lease)s)
ORDER BY c.effective_on, c.id
"""

# Recurring charges not stopped by the day: still charged, or to be.
RECURRING = """
SELECT r.lease_id, r.id, r.kind, r.memo, r.amount, r.starts_on, e.ends_on
FROM trust_recurring_charges AS r
LEFT JOIN trust_recurring_charge_ends AS e ON e.recurring_charge_id = r.id
WHERE r.pmc_id = %(pmc)s AND (e.ends_on IS NULL OR %(as_of)s < e.ends_on)
  AND (%(lease)s::uuid IS NULL OR r.lease_id = %(lease)s)
ORDER BY r.starts_on, r.memo COLLATE "C", r.id
"""

# Each tenant payment as it was received: its transfers (to the owner's account, to prepaid
# rent, or both) are one payment under one key, its amount what came in, and it bounced once a
# bounce was posted under its key. A payment all of it prepaid rent is listed too, so it can
# bounce like any other.
PAYMENTS = """
SELECT p.lease_id, p.first_id, p.received_at, p.amount, p.reference, p.method,
       EXISTS (SELECT 1 FROM pgledger_transfers AS b
               JOIN trust_ledger_accounts AS ba ON ba.ledger_account_id = b.to_account_id
               WHERE ba.pmc_id = %(pmc)s AND b.metadata ->> 'key' = 'bounce/' || p.key)
FROM (SELECT t.metadata ->> 'key' AS key,
             (t.metadata ->> 'lease_id')::uuid AS lease_id,
             (array_agg(t.id ORDER BY t.created_at, t.id))[1] AS first_id,
             min(t.event_at) AS received_at,
             min(t.created_at) AS created_at,
             sum(t.amount) AS amount,
             t.metadata ->> 'reference' AS reference,
             t.metadata ->> 'method' AS method
      FROM pgledger_transfers AS t
      JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id
      WHERE a.pmc_id = %(pmc)s AND t.metadata ->> 'kind' = 'tenant payment'
        AND (%(lease)s::uuid IS NULL OR t.metadata ->> 'lease_id' = %(lease)s::text)
      GROUP BY t.metadata ->> 'key', t.metadata ->> 'lease_id', t.metadata ->> 'reference',
               t.metadata ->> 'method') AS p
ORDER BY p.received_at DESC, p.created_at DESC, p.first_id
"""


def leases(session: Staff, conn: Conn, as_of: date | None = None) -> list[RentLease]:
    """Leases in effect on `as_of` (today, UTC, by default) and any other lease that owes
    something or is owed, with their open charges and tenant payments (what the Dashboard's
    late rent reads)."""
    return built(conn, session.pmc_id, as_of or today())


def lease_detail(
    conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID, day: date
) -> RentLease:
    """One lease of the PMC as the rent page shows it, in effect or not, with every payment."""
    found = built(conn, pmc_id, day, lease_id)
    if not found:
        raise HTTPException(404, "no such lease")
    return found[0]


def built(
    conn: psycopg.Connection[Any], pmc: UUID, day: date, only: UUID | None = None
) -> list[RentLease]:
    """The PMC's leases as the rent page shows them, or the one lease `only` names. Of the
    whole PMC: those in effect on the day, and others only while something is owed, each with
    its latest 12 payments; one lease named: whatever it is, with all of them."""
    names = {"pmc": pmc, "as_of": day, "lease": only}
    tenants: dict[UUID, list[LeaseTenant]] = {}
    for lease, i, name, prepaid, deposit in conn.execute(TENANTS, names).fetchall():
        tenants.setdefault(lease, []).append(
            LeaseTenant(id=i, name=name, prepaid=cents(prepaid), deposit=cents(deposit))
        )
    payments: dict[UUID, list[Paid]] = {}
    for lease, transfer, event_at, amount, reference, method, bounced in conn.execute(
        PAYMENTS, names
    ).fetchall():
        payments.setdefault(lease, []).append(
            Paid(
                transfer_id=transfer,
                received_on=event_at.astimezone(UTC).date(),
                amount=cents(amount),
                reference=reference,
                method=method,
                bounced=bounced,
            )
        )
    charges: dict[UUID, list[OpenCharge]] = {}
    for lease, c, due_on, kind, memo, amount, left in charges_open(conn, pmc, only):
        charges.setdefault(lease, []).append(
            OpenCharge(
                id=c,
                due_on=due_on,
                kind=kind,
                memo=memo,
                amount=cents(amount),
                open=cents(left),
            )
        )
    recurring: dict[UUID, list[RecurringCharge]] = {}
    for lease, r, kind, memo, amount, starts, ends in conn.execute(RECURRING, names).fetchall():
        recurring.setdefault(lease, []).append(
            RecurringCharge(
                id=r, kind=kind, memo=memo, amount=cents(amount), starts_on=starts, ends_on=ends
            )
        )
    changes: dict[UUID, list[RentChange]] = {}
    for lease, c, effective, monthly, notice, served, terms in conn.execute(
        RENT_CHANGES, names
    ).fetchall():
        changes.setdefault(lease, []).append(
            RentChange(
                id=c,
                effective_on=effective,
                monthly_rent=cents(monthly),
                notice_given_on=notice,
                served_by=served,
                lease_terms=terms,
            )
        )
    insured = renters_insurance(conn, pmc, day)
    found = []
    for i, prop, unit, starts, ends, rent, due, on_hold in conn.execute(LEASES, names).fetchall():
        in_effect = starts <= day and (ends is None or day <= ends)
        if only is None and not in_effect and due == 0:
            continue
        found.append(
            RentLease(
                id=i,
                property=prop,
                unit=unit,
                starts_on=starts,
                ends_on=ends,
                monthly_rent=cents(rent),
                rent_changes=changes.get(i, []),
                tenants=tenants.get(i, []),
                owed=cents(due),
                on_hold=on_hold,
                open_charges=charges.get(i, []),
                payments=payments.get(i, []) if only else payments.get(i, [])[:12],
                recurring=recurring.get(i, []),
                renters_insurance=insured.get(i),
            )
        )
    return found


class Day(BaseModel):
    on: date


class RunRent(BaseModel):
    due_on: date
    late_fees_as_of: date


class RentRun(BaseModel):
    rents: int  # leases charged their rent
    recurring: int  # recurring charges charged
    late_fees: int  # rents charged a late fee


class RentRunDone(RentRun):
    # Rent reminders, late notices and owner statements written (src/pms/notices.py).
    emails: int


class RentRunLine(RentRun):
    at: datetime
    due_on: date
    late_fees_as_of: date
    # The staff member who ran it by hand; None for the nightly run.
    by: str | None


@router.post("/run")
def run_rent(session: Staff, conn: Conn, run: RunRent) -> RentRunDone:
    """Runs by hand what posts by itself every night (pms.nightly), an admin's catch-up: every
    lease's rent and recurring charges for a due date (each lease in effect on it for the days of
    the month it is in effect, and each starting later that month its first part month), then a
    late fee for each rent still open after its grace days as of a day. Each once per lease (or
    recurring charge, or rent) and date, so running it again charges nothing new. While the
    server sends email, it also writes today's emails the company's settings ask for, as the
    nightly run does."""
    if run.late_fees_as_of > today():
        raise HTTPException(422, "late fees are assessed as of today or an earlier day")
    made = conn.execute(
        "SELECT trust_run_rent(%s, %s, %s)", (session.pmc_id, run.due_on, run.late_fees_as_of)
    ).fetchone()
    assert made is not None
    row = conn.execute(
        "SELECT rents, recurring, late_fees FROM trust_rent_runs WHERE id = %s", (made[0],)
    ).fetchone()
    assert row is not None
    written = (
        notices.write_scheduled(conn, session.pmc_id, today(), outbox.PUBLIC_URL.get())
        if outbox.SENDING.get()
        else notices.Written()
    )
    return RentRunDone(
        rents=row[0], recurring=row[1], late_fees=row[2], emails=sum(vars(written).values())
    )


@router.get("/runs")
def rent_runs(session: Staff, conn: Conn) -> list[RentRunLine]:
    """The latest ten rent runs, nightly or by hand, newest first."""
    rows = conn.execute(
        "SELECT r.created_at, r.due_on, r.late_fees_as_of, r.rents, r.recurring, r.late_fees,"
        " s.display_name FROM trust_rent_runs AS r"
        " LEFT JOIN trust_staff AS s ON s.id = r.recorded_by"
        " WHERE r.pmc_id = %s ORDER BY r.created_at DESC, r.id DESC LIMIT 10",
        (session.pmc_id,),
    ).fetchall()
    return [
        RentRunLine(
            at=at,
            due_on=due,
            late_fees_as_of=as_of,
            rents=rents,
            recurring=recurring,
            late_fees=late,
            by=by,
        )
        for at, due, as_of, rents, recurring, late, by in rows
    ]


class NewCharge(BaseModel):
    kind: Literal["fee", "credit"]
    amount: Amount
    due_on: date
    memo: Memo


class Created(BaseModel):
    id: UUID


@router.post("/leases/{lease_id}/charges", status_code=201)
def add_charge(session: Staff, conn: Conn, lease_id: UUID, new: NewCharge) -> Created:
    """A one-off fee the lease owes, or a credit that takes its amount off what it owes."""
    lease_of(conn, session.pmc_id, lease_id)
    row = conn.execute(
        "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (session.pmc_id, lease_id, new.due_on, new.kind, new.amount, new.memo),
    ).fetchone()
    assert row is not None
    return Created(id=row[0])


class NewRecurringCharge(BaseModel):
    kind: Literal["fee", "credit"]
    amount: Amount
    memo: Memo
    starts_on: date


@router.post("/leases/{lease_id}/recurring-charges", status_code=201)
def add_recurring_charge(
    session: Staff, conn: Conn, lease_id: UUID, new: NewRecurringCharge
) -> Created:
    """A fee the lease owes every month besides its rent (pet rent, parking), or a credit it
    gets every month, charged by the rent run from the first due date on or after starts_on."""
    lease_of(conn, session.pmc_id, lease_id)
    row = conn.execute(
        "INSERT INTO trust_recurring_charges (pmc_id, lease_id, kind, amount, memo, starts_on)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (session.pmc_id, lease_id, new.kind, new.amount, new.memo, new.starts_on),
    ).fetchone()
    assert row is not None
    return Created(id=row[0])


class NewRentChange(BaseModel):
    effective_on: date  # the first of a month
    monthly_rent: Amount
    # The day written notice was given to the tenants, and how.
    notice_given_on: date
    served_by: Literal["in person", "mail", "posted", "email", "other"]
    # A lease with an end date: the lease term that lets its rent change during it.
    lease_terms: Annotated[
        str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ] = None


def first_of_month_before(day: date) -> date:
    return (day.replace(day=1) - timedelta(days=1)).replace(day=1)


@router.post("/leases/{lease_id}/rent-changes", status_code=201)
def change_rent(session: Staff, conn: Conn, lease_id: UUID, new: NewRentChange) -> Created:
    """The lease's rent from the first of a month on, without a renewal: after written notice of a
    full month (given by the first of the month before), and for a lease with an end date only as
    its terms allow (docs/law.md). The rent run charges it from that month."""
    lease_of(conn, session.pmc_id, lease_id)
    if new.effective_on.day != 1:
        raise HTTPException(422, "a rent change starts on the first of a month")
    if new.notice_given_on > today():
        raise HTTPException(422, "notice is recorded once it was given, not before")
    by = first_of_month_before(new.effective_on)
    if new.notice_given_on > by:
        raise HTTPException(
            422,
            f"written notice of a full month comes first: for a change from {new.effective_on},"
            f" give it by {by}",
        )
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO trust_rent_changes (pmc_id, lease_id, effective_on, monthly_rent,"
                " notice_given_on, served_by, lease_terms) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " RETURNING id",
                (
                    session.pmc_id,
                    lease_id,
                    new.effective_on,
                    new.monthly_rent,
                    new.notice_given_on,
                    new.served_by,
                    new.lease_terms,
                ),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "this lease's rent changes from that month already") from None
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, refusal(refused)) from None
    assert row is not None
    return Created(id=row[0])


Coverage = Annotated[Decimal, Field(gt=0, le=Decimal("100000000"), decimal_places=2)]


class InsuranceTerms(BaseModel):
    required: bool
    # The least liability coverage the lease asks; only when it requires insurance.
    minimum_liability: Coverage | None = None
    starts_on: date


@router.post("/leases/{lease_id}/renters-insurance-terms", status_code=201)
def renters_insurance_terms(
    session: Staff, conn: Conn, lease_id: UUID, terms: InsuranceTerms
) -> Created:
    """Whether the lease requires renters insurance, and the least liability it asks, from a
    day. The tenant picks the insurer; the PMS charges nothing for it (docs/law.md)."""
    lease_of(conn, session.pmc_id, lease_id)
    if not terms.required and terms.minimum_liability is not None:
        raise HTTPException(422, "a minimum is asked only of insurance the lease requires")
    row = conn.execute(
        "INSERT INTO trust_renters_insurance_terms (pmc_id, lease_id, required,"
        " minimum_liability, starts_on) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (session.pmc_id, lease_id, terms.required, terms.minimum_liability, terms.starts_on),
    ).fetchone()
    assert row is not None
    return Created(id=row[0])


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class NewPolicy(BaseModel):
    carrier: Text
    policy_number: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    starts_on: date
    expires_on: date
    liability_limit: Coverage | None = None
    landlord_interested_party: bool = False


def add_policy(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    lease_id: UUID,
    policy: NewPolicy,
    submitted_by: UUID | None = None,
) -> Created:
    if policy.expires_on < policy.starts_on:
        raise HTTPException(422, "a policy runs out on or after the day it starts")
    row = conn.execute(
        "INSERT INTO trust_renters_insurance (pmc_id, lease_id, carrier, policy_number,"
        " starts_on, expires_on, liability_limit, landlord_interested_party, submitted_by)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            pmc_id,
            lease_id,
            policy.carrier,
            policy.policy_number,
            policy.starts_on,
            policy.expires_on,
            policy.liability_limit,
            policy.landlord_interested_party,
            submitted_by,
        ),
    ).fetchone()
    assert row is not None
    return Created(id=row[0])


@router.post("/leases/{lease_id}/renters-insurance", status_code=201)
def record_policy(session: Staff, conn: Conn, lease_id: UUID, policy: NewPolicy) -> Created:
    """A tenant's renters insurance policy for the lease, with whatever insurer they chose; a
    renewal is a new policy."""
    lease_of(conn, session.pmc_id, lease_id)
    return add_policy(conn, session.pmc_id, lease_id, policy)


class Stop(BaseModel):
    ends_on: date  # the first due date it is no longer charged for


@router.post("/recurring-charges/{recurring_id}/stop", status_code=201)
def stop_recurring_charge(session: Staff, conn: Conn, recurring_id: UUID, stop: Stop) -> None:
    """Stops a recurring charge: the rent run charges it for no due date from the day on."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_recurring_charges WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, recurring_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such recurring charge")
    try:
        conn.execute(
            "INSERT INTO trust_recurring_charge_ends (pmc_id, recurring_charge_id, ends_on)"
            " VALUES (%s, %s, %s)",
            (session.pmc_id, recurring_id, stop.ends_on),
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "this recurring charge has stopped already") from None


class NewPayment(BaseModel):
    # Made by the page once per payment it records, so sending it again records nothing new.
    request_id: UUID
    amount: Amount
    received_on: date
    reference: Memo | None = None
    method: Method | None = None
    # The owner's account it pays, when the property has more than one.
    account: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    # Accept it although the lease is on a payment hold (an eviction filed): the PMC's choice.
    past_hold: bool = False


class Payment(BaseModel):
    transfers: list[str]
    to_owner: str
    to_prepaid: str


def answer(recorded: Recorded) -> Payment:
    return Payment(
        transfers=recorded.transfers,
        to_owner=cents(recorded.to_owner),
        to_prepaid=cents(recorded.to_prepaid),
    )


def not_after_today(day: date) -> None:
    if day > today():
        raise HTTPException(422, "money is recorded on the day it arrived, not a later one")


@router.post("/leases/{lease_id}/payments", status_code=201)
def pay(session: Staff, conn: Conn, lease_id: UUID, new: NewPayment) -> Payment:
    not_after_today(new.received_on)
    try:
        return answer(
            record_payment(
                conn,
                session.pmc_id,
                lease_id,
                new.request_id,
                new.amount,
                new.received_on,
                new.reference,
                new.account,
                new.method,
                staff_name(conn, session.pmc_id, session.person_id) if new.past_hold else None,
            )
        )
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        # a payment hold, a closed period, or a match made differently before
        raise HTTPException(409, refusal(refused)) from None


class UsePrepaid(BaseModel):
    request_id: UUID
    on: date
    account: Annotated[str, Field(min_length=1, max_length=100)] | None = None


@router.post("/leases/{lease_id}/use-prepaid", status_code=201)
def prepaid(session: Staff, conn: Conn, lease_id: UUID, use: UsePrepaid) -> Payment:
    not_after_today(use.on)
    try:
        return answer(
            use_prepaid(conn, session.pmc_id, lease_id, use.request_id, use.on, use.account)
        )
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, refusal(refused)) from None


class NewDeposit(BaseModel):
    request_id: UUID
    tenant_id: UUID
    amount: Amount
    received_on: date
    reference: Memo | None = None
    bank_account_id: UUID | None = None


class Posted(BaseModel):
    transfers: list[str]


@router.post("/deposits", status_code=201)
def deposit(session: Staff, conn: Conn, new: NewDeposit) -> Posted:
    not_after_today(new.received_on)
    if (
        conn.execute(
            "SELECT 1 FROM trust_tenants WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, new.tenant_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such tenant")
    try:
        return Posted(
            transfers=record_deposit(
                conn,
                session.pmc_id,
                new.tenant_id,
                new.request_id,
                new.amount,
                new.received_on,
                new.bank_account_id,
                new.reference,
            )
        )
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, refusal(refused)) from None


class Bounced(Posted):
    nsf_fee: str | None  # the NSF fee charged for it, if the property's terms charge one


@router.post("/payments/{transfer_id}/bounce", status_code=201)
def bounced(session: Staff, conn: Conn, transfer_id: str, returned: Day) -> Bounced:
    """Records a returned payment: its money leaves the owner's account (and prepaid rent)
    back to cash, what it paid of each charge is owed again, and the lease is charged the
    property's NSF fee in force that day."""
    not_after_today(returned.on)
    try:
        done = bounce(conn, session.pmc_id, transfer_id, returned.on)
    except psycopg.errors.CheckViolation as refused:  # the owner's account would go negative
        raise HTTPException(409, refusal(refused)) from None
    return Bounced(
        transfers=done.transfers, nsf_fee=None if done.nsf_fee is None else cents(done.nsf_fee)
    )


# --- payment holds (an eviction filed) ------------------------------------------------------------

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class Hold(BaseModel):
    id: UUID
    starts_on: date
    reason: str
    # The day its release ends it, and who released it; none while it isn't released.
    ends_on: date | None
    released_by: str | None
    # Payments staff accepted past it.
    allowed: int
    in_force: bool


HOLDS = """
SELECT h.id, h.starts_on, h.reason, r.ends_on, r.released_by,
       (SELECT count(*) FROM trust_payment_hold_allowances AS a WHERE a.hold_id = h.id)
FROM trust_payment_holds AS h
LEFT JOIN trust_payment_hold_releases AS r ON r.hold_id = h.id
WHERE h.pmc_id = %s AND h.lease_id = %s
ORDER BY h.starts_on DESC, h.created_at DESC, h.id
"""


def holds_of(conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID, day: date) -> list[Hold]:
    """A lease's payment holds, newest first."""
    return [
        Hold(
            id=i,
            starts_on=starts,
            reason=reason,
            ends_on=ends,
            released_by=by,
            allowed=allowed,
            in_force=starts <= day and (ends is None or day < ends),
        )
        for i, starts, reason, ends, by, allowed in conn.execute(HOLDS, (pmc_id, lease_id))
    ]


def staff_name(conn: psycopg.Connection[Any], pmc_id: UUID, staff_id: UUID) -> str:
    row = conn.execute(
        "SELECT display_name FROM trust_staff WHERE pmc_id = %s AND id = %s", (pmc_id, staff_id)
    ).fetchone()
    assert row is not None  # the session's own staff member
    return str(row[0])


class NewHold(BaseModel):
    starts_on: date
    reason: Reason


@router.post("/leases/{lease_id}/holds", status_code=201)
def place_hold(session: Staff, conn: Conn, lease_id: UUID, new: NewHold) -> Hold:
    """Puts a lease on a payment hold from a day (an eviction filed): from then on a payment to
    it is refused unless staff accept it past the hold. One hold at a time: a lease with one not
    released is refused another."""
    lease_of(conn, session.pmc_id, lease_id)
    if new.starts_on > today():
        raise HTTPException(422, "a hold starts today or on an earlier day")
    lock_lease(conn, lease_id)
    if any(h.ends_on is None for h in holds_of(conn, session.pmc_id, lease_id, today())):
        raise HTTPException(409, "this lease is on a payment hold already: release it first")
    made = conn.execute(
        "INSERT INTO trust_payment_holds (pmc_id, lease_id, starts_on, reason)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (session.pmc_id, lease_id, new.starts_on, new.reason),
    ).fetchone()
    assert made is not None
    return next(h for h in holds_of(conn, session.pmc_id, lease_id, today()) if h.id == made[0])


class Release(BaseModel):
    ends_on: date


@router.post("/holds/{hold_id}/release", status_code=201)
def release_hold(session: Staff, conn: Conn, hold_id: UUID, release: Release) -> Hold:
    """Ends a payment hold from a day, after it started, naming who released it; once."""
    row = conn.execute(
        "SELECT lease_id FROM trust_payment_holds WHERE pmc_id = %s AND id = %s",
        (session.pmc_id, hold_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such hold")
    try:
        conn.execute(
            "INSERT INTO trust_payment_hold_releases (hold_id, pmc_id, ends_on, released_by)"
            " VALUES (%s, %s, %s, %s)",
            (
                hold_id,
                session.pmc_id,
                release.ends_on,
                staff_name(conn, session.pmc_id, session.person_id),
            ),
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "this hold is released already") from None
    except psycopg.errors.CheckViolation:
        raise HTTPException(422, "a hold ends after the day it started") from None
    return next(h for h in holds_of(conn, session.pmc_id, row[0], today()) if h.id == hold_id)


# --- waiving a charge -------------------------------------------------------------------------


class Waive(BaseModel):
    amount: Amount
    reason: Reason


class Waived(BaseModel):
    credit_id: UUID
    charge_id: UUID
    amount: str
    # What is still open of the charge once waived.
    open: str


KINDS = {"rent": "Rent", "fee": "Fee"}


@router.post("/charges/{charge_id}/waive", status_code=201)
def waive(session: Staff, conn: Conn, charge_id: UUID, waive: Waive) -> Waived:
    """Takes a charge (a late fee, an NSF fee, any fee or rent) off its lease, all of it or part,
    with why: a credit of today linked to it, which comes off that charge first. Never more than
    is left of it once its payments and earlier waivers count."""
    row = conn.execute(
        "SELECT lease_id, kind, due_on, memo FROM trust_charges WHERE pmc_id = %s AND id = %s",
        (session.pmc_id, charge_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such charge")
    lease, kind, due_on, memo = row
    if kind == "credit":
        raise HTTPException(422, "a credit is not waived")
    lock_lease(conn, lease)
    what = memo or KINDS.get(kind, kind)
    credit = conn.execute(
        "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)"
        " VALUES (%s, %s, %s, 'credit', %s, %s) RETURNING id",
        (session.pmc_id, lease, today(), waive.amount, f"Waived: {what}, due {due_on:%m/%d/%Y}"),
    ).fetchone()
    assert credit is not None
    try:
        conn.execute(
            "INSERT INTO trust_charge_waivers (credit_charge_id, pmc_id, waived_charge_id, reason)"
            " VALUES (%s, %s, %s, %s)",
            (credit[0], session.pmc_id, charge_id, waive.reason),
        )
    except psycopg.errors.CheckViolation as refused:  # more than is left of it
        raise HTTPException(409, refusal(refused)) from None
    left = next(
        (Decimal(o) for _, c, *_, o in charges_open(conn, session.pmc_id, lease) if c == charge_id),
        ZERO,
    )
    return Waived(
        credit_id=credit[0], charge_id=charge_id, amount=cents(waive.amount), open=cents(left)
    )
