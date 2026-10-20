"""A housing authority's part of a lease's rent (migration 0095, the founder's review q034;
docs/law.md): the company's housing authorities, a lease's assistance from a month (what its
authority pays of the rent), what each authority owes (each month's part of the rent, split from it
as the rent is charged, and what staff charge or credit it by hand) and the authority's payments,
which pay its charges into the property's owners' accounts (by their ownership shares) and count
as rent collected.

The tenant's side is unchanged (src/pms/api/rent.py): their rent is their part alone, so their
payments, late fees, deposit and balance never reach the authority's part, which the family is
not responsible for (24 CFR 982.310(b)). The PMS records what the authority paid; it sends and
asks for nothing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from pms.api import rent
from pms.api.deps import Conn, Staff, refusal

router = APIRouter(prefix="/api/assistance")

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Contract = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
# What an authority pays of a month's rent: 0 ends a lease's assistance.
Monthly = Annotated[Decimal, Field(ge=0, le=Decimal("1000000"), decimal_places=2)]
ZERO = Decimal(0)


class Created(BaseModel):
    id: UUID


def authority_named(conn: psycopg.Connection[Any], pmc_id: UUID, authority_id: UUID) -> str:
    row = conn.execute(
        "SELECT name FROM trust_housing_authorities WHERE pmc_id = %s AND id = %s",
        (pmc_id, authority_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such housing authority")
    return str(row[0])


# --- the authorities ---------------------------------------------------------------------------


class Authority(BaseModel):
    id: UUID
    name: str
    # Leases it assists today, and what it owes on all of them.
    leases: int
    owed: str


AUTHORITIES = """
SELECT h.id, h.name,
       (SELECT count(*) FROM trust_leases AS l
        WHERE l.pmc_id = h.pmc_id AND (l.ends_on IS NULL OR l.ends_on >= %(day)s)
          AND EXISTS (SELECT 1 FROM trust_assistance_on(l.id, greatest(%(day)s, l.starts_on)) AS a
                      WHERE a.housing_authority_id = h.id AND a.amount > 0)),
       coalesce((SELECT sum(trust_assistance_open(c.id)) FROM trust_assistance_charges AS c
                 WHERE c.housing_authority_id = h.id AND c.adjusts_id IS NULL), 0)
FROM trust_housing_authorities AS h
WHERE h.pmc_id = %(pmc)s AND (%(id)s::uuid IS NULL OR h.id = %(id)s)
ORDER BY h.name COLLATE "C", h.id
"""


def authorities_of(
    conn: psycopg.Connection[Any], pmc_id: UUID, authority_id: UUID | None = None
) -> list[Authority]:
    return [
        Authority(id=i, name=n, leases=count, owed=rent.cents(owed))
        for i, n, count, owed in conn.execute(
            AUTHORITIES, {"pmc": pmc_id, "day": rent.today(), "id": authority_id}
        ).fetchall()
    ]


@router.get("/authorities")
def authorities(session: Staff, conn: Conn) -> list[Authority]:
    """The company's housing authorities, each with the leases it assists today and what it
    owes."""
    return authorities_of(conn, session.pmc_id)


class NewAuthority(BaseModel):
    name: Name


@router.post("/authorities", status_code=201)
def add_authority(session: Staff, conn: Conn, new: NewAuthority) -> Created:
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO trust_housing_authorities (pmc_id, name) VALUES (%s, %s) RETURNING id",
                (session.pmc_id, new.name),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "a housing authority by that name is on file already") from None
    assert row is not None
    return Created(id=row[0])


# --- a lease's assistance and what its authority owes ------------------------------------------


class Terms(BaseModel):
    id: UUID
    authority_id: UUID
    authority: str
    starts_on: date
    # What the authority pays of each month's rent; "0.00" ended the assistance.
    amount: str
    contract: str | None
    recorded_by: str | None


class AuthorityCharge(BaseModel):
    id: UUID
    lease_id: UUID
    authority_id: UUID
    due_on: date
    memo: str
    # 'rent': a month's part of the rent; 'staff': charged by hand.
    source: Literal["rent", "staff"]
    charged: str
    # What a lease's end or staff's credits added to it or took off it (signed).
    adjusted: str
    paid: str
    # Below 0 when the authority paid more than it now owes.
    open: str


TERMS = """
SELECT a.id, a.housing_authority_id, h.name, a.starts_on, a.amount, a.contract, s.display_name
FROM trust_rent_assistance AS a
JOIN trust_housing_authorities AS h ON h.id = a.housing_authority_id
LEFT JOIN trust_staff AS s ON s.id = a.recorded_by
WHERE a.pmc_id = %s AND a.lease_id = %s
ORDER BY a.starts_on DESC, a.created_at DESC, a.id DESC
"""

CHARGES = """
SELECT c.id, c.lease_id, c.housing_authority_id, c.due_on, c.memo, c.source, c.amount,
       coalesce((SELECT sum(CASE a.kind WHEN 'credit' THEN -a.amount ELSE a.amount END)
                 FROM trust_assistance_charges AS a WHERE a.adjusts_id = c.id), 0),
       coalesce((SELECT sum(p.amount) FROM trust_assistance_payments AS p
                 WHERE p.assistance_charge_id = c.id), 0),
       trust_assistance_open(c.id)
FROM trust_assistance_charges AS c
WHERE c.pmc_id = %(pmc)s AND c.adjusts_id IS NULL
  AND (%(lease)s::uuid IS NULL OR c.lease_id = %(lease)s)
  AND (%(authority)s::uuid IS NULL OR c.housing_authority_id = %(authority)s)
ORDER BY c.due_on, c.created_at, c.id
"""


def charges_of(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    lease_id: UUID | None = None,
    authority_id: UUID | None = None,
) -> list[AuthorityCharge]:
    return [
        AuthorityCharge(
            id=i,
            lease_id=lease,
            authority_id=authority,
            due_on=due,
            memo=memo,
            source=source,
            charged=rent.cents(amount),
            adjusted=rent.cents(adjusted),
            paid=rent.cents(paid),
            open=rent.cents(left),
        )
        for i, lease, authority, due, memo, source, amount, adjusted, paid, left in conn.execute(
            CHARGES, {"pmc": pmc_id, "lease": lease_id, "authority": authority_id}
        ).fetchall()
    ]


class LeaseAssistance(BaseModel):
    # The assistance in force today (none when ended or never recorded), and the rent then.
    in_force: Terms | None
    monthly_rent: str
    tenant_part: str
    # Every assistance recorded, the latest first.
    terms: list[Terms]
    # What the authorities owe for the lease, oldest first, and all of it.
    charges: list[AuthorityCharge]
    owed: str


@router.get("/leases/{lease_id}")
def lease_assistance(session: Staff, conn: Conn, lease_id: UUID) -> LeaseAssistance:
    """A lease's housing assistance: in force today, every one recorded, and what its
    authority owes for it."""
    rent.lease_of(conn, session.pmc_id, lease_id)
    terms = [
        Terms(
            id=i,
            authority_id=authority,
            authority=name,
            starts_on=starts,
            amount=rent.cents(amount),
            contract=contract,
            recorded_by=by,
        )
        for i, authority, name, starts, amount, contract, by in conn.execute(
            TERMS, (session.pmc_id, lease_id)
        ).fetchall()
    ]
    row = conn.execute(
        "SELECT (SELECT a.id FROM trust_assistance_on(l.id, d.day) AS a WHERE a.amount > 0),"
        " trust_rent_on(l.id, d.day)"
        " FROM trust_leases AS l, LATERAL (SELECT greatest(%s, l.starts_on) AS day) AS d"
        " WHERE l.id = %s",
        (rent.today(), lease_id),
    ).fetchone()
    assert row is not None
    in_force = next((t for t in terms if t.id == row[0]), None)
    monthly = Decimal(row[1])
    charges = charges_of(conn, session.pmc_id, lease_id)
    return LeaseAssistance(
        in_force=in_force,
        monthly_rent=rent.cents(monthly),
        tenant_part=rent.cents(
            monthly - min(monthly, Decimal(in_force.amount)) if in_force else monthly
        ),
        terms=terms,
        charges=charges,
        owed=rent.cents(sum((Decimal(c.open) for c in charges), ZERO)),
    )


class NewTerms(BaseModel):
    housing_authority_id: UUID
    # The first of a month of the lease not yet charged.
    starts_on: date
    amount: Monthly
    contract: Contract | None = None


@router.post("/leases/{lease_id}/terms", status_code=201)
def set_terms(session: Staff, conn: Conn, lease_id: UUID, new: NewTerms) -> Created:
    """A lease's housing assistance from the first of a month: what the authority pays of each
    month's rent from then (0 ends it). Each month's rent is split as the rent run charges it."""
    rent.lease_of(conn, session.pmc_id, lease_id)
    authority_named(conn, session.pmc_id, new.housing_authority_id)
    if new.starts_on.day != 1:
        raise HTTPException(422, "housing assistance starts on the first of a month")
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO trust_rent_assistance"
                " (pmc_id, lease_id, housing_authority_id, starts_on, amount, contract)"
                " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    session.pmc_id,
                    lease_id,
                    new.housing_authority_id,
                    new.starts_on,
                    new.amount,
                    new.contract,
                ),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "the lease has assistance from that month already") from None
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, refusal(refused)) from None
    assert row is not None
    return Created(id=row[0])


class NewAuthorityCharge(BaseModel):
    kind: Literal["charge", "credit"]
    amount: rent.Amount
    due_on: date
    memo: rent.Memo
    # A charge names its authority; a credit names the authority's charge it takes off.
    housing_authority_id: UUID | None = None
    charge_id: UUID | None = None


@router.post("/leases/{lease_id}/charges", status_code=201)
def add_charge(session: Staff, conn: Conn, lease_id: UUID, new: NewAuthorityCharge) -> Created:
    """What staff charge an authority for a lease by hand (a month it pays from before the
    assistance was on file, say), or credit it off one of its charges (an abatement), never more
    than is open of it."""
    rent.lease_of(conn, session.pmc_id, lease_id)
    if new.kind == "charge":
        if new.housing_authority_id is None or new.charge_id is not None:
            raise HTTPException(422, "a charge names its housing authority, and no charge")
        authority = new.housing_authority_id
        authority_named(conn, session.pmc_id, authority)
    else:
        if new.charge_id is None:
            raise HTTPException(422, "a credit names the authority's charge it takes off")
        row = conn.execute(
            "SELECT housing_authority_id FROM trust_assistance_charges"
            " WHERE pmc_id = %s AND lease_id = %s AND id = %s AND adjusts_id IS NULL",
            (session.pmc_id, lease_id, new.charge_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "no such charge of the housing authority's on this lease")
        authority = row[0]
    try:
        with conn.transaction():
            made = conn.execute(
                "INSERT INTO trust_assistance_charges (pmc_id, lease_id, housing_authority_id,"
                " due_on, kind, amount, memo, source, adjusts_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, 'staff', %s) RETURNING id",
                (
                    session.pmc_id,
                    lease_id,
                    authority,
                    new.due_on,
                    new.kind,
                    new.amount,
                    new.memo,
                    new.charge_id,
                ),
            ).fetchone()
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, refusal(refused)) from None
    assert made is not None
    return Created(id=made[0])


# --- an authority's page ----------------------------------------------------------------------


class AssistedLease(BaseModel):
    lease_id: UUID
    # "Morgan Demo, Birch Flats Unit A"
    home: str
    # What the authority pays of each month's rent today, if it assists the lease today.
    monthly: str | None
    contract: str | None
    owed: str
    # Its charges with something open, oldest first.
    open_charges: list[AuthorityCharge]


class AuthorityPage(BaseModel):
    id: UUID
    name: str
    leases: list[AssistedLease]
    owed: str


@router.get("/authorities/{authority_id}")
def authority_page(session: Staff, conn: Conn, authority_id: UUID) -> AuthorityPage:
    """A housing authority: each lease it assists or owes on, what it pays of the rent today and
    what it owes, to record its payments against."""
    name = authority_named(conn, session.pmc_id, authority_id)
    charges = charges_of(conn, session.pmc_id, authority_id=authority_id)
    rows = conn.execute(
        "SELECT l.id, a.amount, a.contract FROM trust_leases AS l"
        " LEFT JOIN LATERAL trust_assistance_on(l.id, greatest(%s, l.starts_on)) AS a"
        "   ON a.housing_authority_id = %s AND a.amount > 0"
        " AND (l.ends_on IS NULL OR l.ends_on >= %s)"
        " WHERE l.pmc_id = %s AND (EXISTS (SELECT 1 FROM trust_rent_assistance AS r"
        "   WHERE r.lease_id = l.id AND r.housing_authority_id = %s)"
        "   OR EXISTS (SELECT 1 FROM trust_assistance_charges AS c"
        "   WHERE c.lease_id = l.id AND c.housing_authority_id = %s))",
        (rent.today(), authority_id, rent.today(), session.pmc_id, authority_id, authority_id),
    ).fetchall()
    leases = []
    for lease_id, monthly, contract in rows:
        mine = [c for c in charges if c.lease_id == lease_id]
        leases.append(
            AssistedLease(
                lease_id=lease_id,
                home=rent.lease_memo(conn, lease_id),
                monthly=None if monthly is None else rent.cents(monthly),
                contract=contract,
                owed=rent.cents(sum((Decimal(c.open) for c in mine), ZERO)),
                open_charges=[c for c in mine if Decimal(c.open) != 0],
            )
        )
    leases.sort(key=lambda lease: (lease.home, str(lease.lease_id)))
    return AuthorityPage(
        id=authority_id,
        name=name,
        leases=leases,
        owed=rent.cents(sum((Decimal(c.open) for c in charges), ZERO)),
    )


# --- an authority's payment -------------------------------------------------------------------


class PaymentLine(BaseModel):
    lease_id: UUID
    amount: rent.Amount
    # The owner's account it pays, when the property has several and no ownership shares.
    account: Annotated[str, Field(min_length=1, max_length=100)] | None = None


class NewAuthorityPayment(BaseModel):
    # Made by the page once per payment it records, so sending it again records nothing new.
    request_id: UUID
    housing_authority_id: UUID
    received_on: date
    reference: rent.Memo | None = None
    method: rent.Method | None = None
    lines: Annotated[list[PaymentLine], Field(min_length=1, max_length=200)]


class AuthorityPayment(BaseModel):
    transfers: list[str]
    paid: str


@router.post("/payments", status_code=201)
def record_payment(session: Staff, conn: Conn, new: NewAuthorityPayment) -> AuthorityPayment:
    """An authority's payment for one lease or many (its one check or deposit): each lease's part
    pays what the authority owes on it, oldest first, into its property's owners' accounts (by
    their ownership shares), never more than it owes. All or none."""
    rent.not_after_today(new.received_on)
    name = authority_named(conn, session.pmc_id, new.housing_authority_id)
    leases = [line.lease_id for line in new.lines]
    if len(set(leases)) != len(leases):
        raise HTTPException(422, "each lease is in the payment once")
    key = f"assistance/{new.request_id}"
    total = sum((line.amount for line in new.lines), ZERO)
    metadata = {
        "key": key,
        "kind": "housing authority payment",
        "housing_authority_id": str(new.housing_authority_id),
        "received_on": new.received_on.isoformat(),
        "amount": rent.cents(total),
        "lines": [[str(line.lease_id), rent.cents(line.amount)] for line in new.lines],
        "reference": new.reference,
        "method": new.method,
        "memo": f"Housing authority payment – {name}",
    }
    # Rent work on each lease waits for the rest, taken in one order so payments never deadlock.
    properties = {}
    for lease_id in sorted(leases, key=str):
        properties[lease_id] = rent.lease_of(conn, session.pmc_id, lease_id).property_id
        rent.lock_lease(conn, lease_id)
    # Each lease's part goes to its property's owners, by their shares on the day received.
    plans: list[tuple[PaymentLine, list[tuple[str, Decimal]]]] = []
    transfers: list[tuple[str, str, Decimal]] = []
    for line in new.lines:
        owners = rent.owners_of(
            conn, session.pmc_id, properties[line.lease_id], new.received_on, line.account
        )
        parts = owners.parts(conn, line.amount)
        transfers += [(rent.cash_of(conn, owners.bank), account, part) for account, part in parts]
        plans.append((line, parts))
    posted = rent.post(conn, session.pmc_id, key, transfers, new.received_on, metadata)
    if posted is None:
        try:
            posted = rent.earlier(conn, session.pmc_id, key, metadata)
        except rent.Conflict:
            raise HTTPException(409, "this request id was used for a different payment") from None
        return AuthorityPayment(transfers=posted, paid=rent.cents(total))
    if conn.execute(
        "SELECT 1 FROM trust_assistance_payments WHERE transfer_id = ANY(%s) LIMIT 1", (posted,)
    ).fetchone():
        # The very same request again: recorded and matched already.
        return AuthorityPayment(transfers=posted, paid=rent.cents(total))
    # Each owner's part pays what the authority owes on the lease, oldest first.
    sent = iter(posted)
    for line, parts in plans:
        open_charges = [
            (charge, Decimal(left))
            for charge, left in conn.execute(
                "SELECT id, trust_assistance_open(id) FROM trust_assistance_charges"
                " WHERE pmc_id = %s AND lease_id = %s AND housing_authority_id = %s"
                " AND adjusts_id IS NULL ORDER BY due_on, created_at, id",
                (session.pmc_id, line.lease_id, new.housing_authority_id),
            ).fetchall()
            if Decimal(left) > 0
        ]
        owing = sum((left for _, left in open_charges), ZERO)
        if line.amount > owing:
            raise HTTPException(
                422,
                f"{rent.cents(line.amount)} is more than {name} owes on"
                f" {rent.lease_memo(conn, line.lease_id)}: {rent.cents(owing)}",
            )
        for _, part in parts:
            transfer = next(sent)
            for charge, piece in rent.allocate(open_charges, part):
                conn.execute(
                    "SELECT trust_apply_assistance_payment(%s, %s, %s, %s)",
                    (session.pmc_id, charge, transfer, piece),
                )
            open_charges = shrink(open_charges, part)
    return AuthorityPayment(transfers=posted, paid=rent.cents(total))


def shrink(charges: list[tuple[UUID, Decimal]], amount: Decimal) -> list[tuple[UUID, Decimal]]:
    """What is still open of the charges, oldest first, once `amount` paid them."""
    left: list[tuple[UUID, Decimal]] = []
    for charge, open_ in charges:
        piece = min(amount, open_)
        amount -= piece
        if open_ > piece:
            left.append((charge, open_ - piece))
    return left
