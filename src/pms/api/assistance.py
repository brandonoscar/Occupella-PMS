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

Since migration 0108 (the founder's review q143) a lease's page also keeps it in compliance: the
household's income certifications (at move-in, every year, 24 CFR 982.516(a), and interim ones
between) and the home's inspections for its authority (at least every two years, 24 CFR
982.405(a)), each saying where it stands today as trust_report_assistance_compliance words it. The
PMS files nothing with HUD and checks no income or rent limit.

Since migration 0116 (the rest of q034) an authority's page sets its late payment fee, as its HAP
contract provides (the nightly run and Run rent now charge it on the authority, never the tenant),
lists its payments with It bounced for one the bank returned (its money moves back to cash and
its charges are open again), and a payment dated in a lease's payment hold is refused unless staff
accept it past the hold, as a tenant's is.
"""

from __future__ import annotations

from datetime import UTC, date
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
    # 'rent': a month's part of the rent; 'staff': charged by hand; 'late fee': the authority's
    # late payment fee on a rent's part (migration 0116).
    source: Literal["rent", "staff", "late fee"]
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
                 WHERE p.assistance_charge_id = c.id), 0)
       - coalesce((SELECT sum(r.amount) FROM trust_assistance_payment_reversals AS r
                   WHERE r.assistance_charge_id = c.id), 0),
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


CertificationKind = Literal["move-in", "annual", "interim"]
InspectionResult = Literal["passed", "failed"]


class Certification(BaseModel):
    id: UUID
    kind: CertificationKind
    effective_on: date
    household_size: int
    annual_income: str
    # The day the next annual certification is due.
    next_due_on: date
    recorded_by: str | None


class Inspection(BaseModel):
    id: UUID
    authority_id: UUID
    authority: str
    inspected_on: date
    result: InspectionResult
    # Passed: the next inspection is due by it. Failed: the home must pass a reinspection by it.
    due_on: date
    recorded_by: str | None


class Compliance(BaseModel):
    # The latest certification and inspection in effect today and where each stands, in the
    # compliance report's words: current, due within 120 days, overdue, none on file, and for an
    # inspection failed, reinspection due or overdue.
    recertify_by: date | None
    recertification: str
    inspect_by: date | None
    inspection: str
    # Every one recorded, the latest first.
    certifications: list[Certification]
    inspections: list[Inspection]


CERTIFICATIONS = """
SELECT c.id, c.kind, c.effective_on, c.household_size, c.annual_income, c.next_due_on,
       s.display_name
FROM trust_income_certifications AS c
LEFT JOIN trust_staff AS s ON s.id = c.recorded_by
WHERE c.pmc_id = %s AND c.lease_id = %s
ORDER BY c.effective_on DESC, c.created_at DESC, c.id DESC
"""

INSPECTIONS = """
SELECT i.id, i.housing_authority_id, h.name, i.inspected_on, i.result, i.due_on, s.display_name
FROM trust_assistance_inspections AS i
JOIN trust_housing_authorities AS h ON h.id = i.housing_authority_id
LEFT JOIN trust_staff AS s ON s.id = i.recorded_by
WHERE i.pmc_id = %s AND i.lease_id = %s
ORDER BY i.inspected_on DESC, i.created_at DESC, i.id DESC
"""


def compliance_of(conn: psycopg.Connection[Any], pmc_id: UUID, lease_id: UUID) -> Compliance:
    certifications = [
        Certification(
            id=i,
            kind=kind,
            effective_on=effective,
            household_size=size,
            annual_income=rent.cents(income),
            next_due_on=due,
            recorded_by=by,
        )
        for i, kind, effective, size, income, due, by in conn.execute(
            CERTIFICATIONS, (pmc_id, lease_id)
        ).fetchall()
    ]
    inspections = [
        Inspection(
            id=i,
            authority_id=authority,
            authority=name,
            inspected_on=inspected,
            result=result,
            due_on=due,
            recorded_by=by,
        )
        for i, authority, name, inspected, result, due, by in conn.execute(
            INSPECTIONS, (pmc_id, lease_id)
        ).fetchall()
    ]
    day = rent.today()
    # Each list is the latest first, so the first one in effect today holds, as in the report.
    certified = next((c for c in certifications if c.effective_on <= day), None)
    inspected = next((i for i in inspections if i.inspected_on <= day), None)
    recertify_by = certified.next_due_on if certified else None
    inspect_by = inspected.due_on if inspected else None
    row = conn.execute(
        "SELECT trust_due_standing(%s, %s), trust_inspection_standing(%s, %s, %s)",
        (recertify_by, day, inspected.result if inspected else None, inspect_by, day),
    ).fetchone()
    assert row is not None
    return Compliance(
        recertify_by=recertify_by,
        recertification=row[0],
        inspect_by=inspect_by,
        inspection=row[1],
        certifications=certifications,
        inspections=inspections,
    )


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
    # The household's certifications and the home's inspections.
    compliance: Compliance


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
        compliance=compliance_of(conn, session.pmc_id, lease_id),
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


# --- keeping the lease in compliance ----------------------------------------------------------


def a_year_after(day: date, years: int = 1) -> date:
    """The same day `years` on (February 28 for a February 29)."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


class NewCertification(BaseModel):
    kind: CertificationKind
    effective_on: date
    household_size: Annotated[int, Field(ge=1, le=30)]
    annual_income: Annotated[Decimal, Field(ge=0, lt=Decimal("100000000"), decimal_places=2)]
    # The day the next annual one is due: unless given, a year after a move-in or annual one
    # takes effect, and an interim one keeps the due day of the one before it.
    next_due_on: date | None = None


@router.post("/leases/{lease_id}/certifications", status_code=201)
def certify(session: Staff, conn: Conn, lease_id: UUID, new: NewCertification) -> Created:
    """A certification of the household's income and size: at move-in, the annual one (at least
    once a year, 24 CFR 982.516(a)) or an interim one between, from the day it takes effect."""
    rent.lease_of(conn, session.pmc_id, lease_id)
    due = new.next_due_on
    if due is None and new.kind != "interim":
        due = a_year_after(new.effective_on)
    elif due is None:
        row = conn.execute(
            "SELECT next_due_on FROM trust_income_certifications"
            " WHERE pmc_id = %s AND lease_id = %s AND effective_on <= %s"
            " ORDER BY effective_on DESC, created_at DESC, id DESC LIMIT 1",
            (session.pmc_id, lease_id, new.effective_on),
        ).fetchone()
        if row is None:
            raise HTTPException(
                422, "an interim certification follows a move-in or annual one: give its due day"
            )
        due = row[0]
    if due <= new.effective_on:
        raise HTTPException(422, "the next annual certification is due after this one")
    try:
        with conn.transaction():
            made = conn.execute(
                "INSERT INTO trust_income_certifications (pmc_id, lease_id, kind, effective_on,"
                " household_size, annual_income, next_due_on)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    session.pmc_id,
                    lease_id,
                    new.kind,
                    new.effective_on,
                    new.household_size,
                    new.annual_income,
                    due,
                ),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            409, f"an {new.kind} certification from that day is on file already"
        ) from None
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, refusal(refused)) from None
    assert made is not None
    return Created(id=made[0])


class NewInspection(BaseModel):
    inspected_on: date
    result: InspectionResult
    # Passed: the next is due by it, two years on unless given (24 CFR 982.405(a)). Failed: the
    # day the authority gave for the home to pass a reinspection by.
    due_on: date | None = None
    # Unless given, the authority assisting the lease on the day, else the next to (a move-in
    # inspection comes before the assistance starts).
    housing_authority_id: UUID | None = None


@router.post("/leases/{lease_id}/inspections", status_code=201)
def inspect(session: Staff, conn: Conn, lease_id: UUID, new: NewInspection) -> Created:
    """The home's inspection for its housing authority, once it happened: passed, with the day
    the next is due, or failed, with the day it must pass a reinspection by."""
    rent.lease_of(conn, session.pmc_id, lease_id)
    if new.inspected_on > rent.today():
        raise HTTPException(422, "an inspection is recorded once it happened")
    authority = new.housing_authority_id
    if authority is None:
        row = conn.execute(
            "SELECT housing_authority_id FROM ("
            "  SELECT a.housing_authority_id, a.amount, a.starts_on"
            "  FROM trust_assistance_on(%(lease)s, %(day)s) AS a"
            "  UNION ALL"
            "  SELECT a.housing_authority_id, a.amount, a.starts_on FROM trust_rent_assistance AS a"
            "  WHERE a.lease_id = %(lease)s AND a.starts_on > %(day)s"
            ") AS a WHERE a.amount > 0 ORDER BY a.starts_on LIMIT 1",
            {"lease": lease_id, "day": new.inspected_on},
        ).fetchone()
        if row is None:
            raise HTTPException(
                422,
                "no housing authority assists the lease that day or later:"
                " name the one that inspected",
            )
        authority = row[0]
    else:
        authority_named(conn, session.pmc_id, authority)
    due = new.due_on
    if due is None and new.result == "failed":
        raise HTTPException(422, "a failed inspection gives the day to pass a reinspection by")
    if due is None:
        due = a_year_after(new.inspected_on, 2)
    if due <= new.inspected_on:
        raise HTTPException(422, "the next inspection is due after this one")
    try:
        with conn.transaction():
            made = conn.execute(
                "INSERT INTO trust_assistance_inspections (pmc_id, lease_id,"
                " housing_authority_id, inspected_on, result, due_on)"
                " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (session.pmc_id, lease_id, authority, new.inspected_on, new.result, due),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            409, f"an inspection {new.result} that day is on file already"
        ) from None
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
    # A payment hold on the lease in force today (an eviction filed): from when, and why.
    hold_since: date | None = None
    hold_reason: str | None = None


class AuthorityLateFee(BaseModel):
    starts_on: date
    grace_days: int
    flat_fee: str


class AuthorityPaymentRow(BaseModel):
    # The payment's first transfer: It bounced names it.
    transfer_id: str
    received_on: date
    amount: str
    reference: str | None
    method: str | None
    # The day the bank returned it, once it bounced.
    bounced_on: date | None


class AuthorityPage(BaseModel):
    id: UUID
    name: str
    leases: list[AssistedLease]
    owed: str
    # Its late payment fee in force today (none: no terms, or terms of 0).
    late_fee: AuthorityLateFee | None = None
    # Its payments, the latest first.
    payments: list[AuthorityPaymentRow] = []


LATE_FEE = """
SELECT f.starts_on, f.grace_days, f.flat_fee FROM trust_authority_late_fees AS f
WHERE f.pmc_id = %s AND f.housing_authority_id = %s AND f.starts_on <= %s
ORDER BY f.starts_on DESC LIMIT 1
"""

# An authority's payments: the transfers posted under each key, and the day a bounce of it moved
# the money back.
PAYMENTS = """
WITH paid AS (
    SELECT t.metadata ->> 'key' AS key, min(t.id) AS first,
           (t.metadata ->> 'received_on')::date AS received_on, sum(t.amount) AS amount,
           t.metadata ->> 'reference' AS reference, t.metadata ->> 'method' AS method
    FROM pgledger_transfers AS t
    JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id
    WHERE a.pmc_id = %s AND t.metadata ->> 'kind' = 'housing authority payment'
      AND t.metadata ->> 'housing_authority_id' = %s
    GROUP BY 1, 3, 5, 6
)
SELECT p.first, p.received_on, p.amount, p.reference, p.method,
       (SELECT min(b.event_at) FROM pgledger_transfers AS b
        WHERE b.metadata ->> 'key' = 'bounce/' || p.key)
FROM paid AS p
ORDER BY p.received_on DESC, p.first DESC
"""

HOLD_TODAY = """
SELECT h.starts_on, h.reason FROM trust_payment_holds AS h
LEFT JOIN trust_payment_hold_releases AS r ON r.hold_id = h.id
WHERE h.pmc_id = %(pmc)s AND h.lease_id = %(lease)s AND h.starts_on <= %(day)s
  AND (r.ends_on IS NULL OR %(day)s < r.ends_on)
ORDER BY h.starts_on, h.id LIMIT 1
"""


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
        hold = conn.execute(
            HOLD_TODAY, {"pmc": session.pmc_id, "lease": lease_id, "day": rent.today()}
        ).fetchone()
        leases.append(
            AssistedLease(
                lease_id=lease_id,
                home=rent.lease_memo(conn, lease_id),
                monthly=None if monthly is None else rent.cents(monthly),
                contract=contract,
                owed=rent.cents(sum((Decimal(c.open) for c in mine), ZERO)),
                open_charges=[c for c in mine if Decimal(c.open) != 0],
                hold_since=None if hold is None else hold[0],
                hold_reason=None if hold is None else hold[1],
            )
        )
    leases.sort(key=lambda lease: (lease.home, str(lease.lease_id)))
    fee = conn.execute(LATE_FEE, (session.pmc_id, authority_id, rent.today())).fetchone()
    return AuthorityPage(
        id=authority_id,
        name=name,
        leases=leases,
        owed=rent.cents(sum((Decimal(c.open) for c in charges), ZERO)),
        late_fee=None
        if fee is None or fee[2] == 0
        else AuthorityLateFee(starts_on=fee[0], grace_days=fee[1], flat_fee=rent.cents(fee[2])),
        payments=[
            AuthorityPaymentRow(
                transfer_id=first,
                received_on=received,
                amount=rent.cents(amount),
                reference=reference,
                method=method,
                bounced_on=None if bounced is None else bounced.astimezone(UTC).date(),
            )
            for first, received, amount, reference, method, bounced in conn.execute(
                PAYMENTS, (session.pmc_id, str(authority_id))
            ).fetchall()
        ],
    )


class NewAuthorityLateFee(BaseModel):
    starts_on: date
    grace_days: Annotated[int, Field(ge=0, le=60)]
    # 0 charges none from the day.
    flat_fee: Annotated[Decimal, Field(ge=0, le=Decimal("100000"), decimal_places=2)]


@router.post("/authorities/{authority_id}/late-fees", status_code=201)
def set_late_fee(
    session: Staff, conn: Conn, authority_id: UUID, new: NewAuthorityLateFee
) -> Created:
    """An authority's late payment fee from a day, as its HAP contract provides (24 CFR
    982.451(b)(4)): its part of a rent still open after the grace days is charged the fee, on the
    authority, once. New terms are a new row from a later day."""
    authority_named(conn, session.pmc_id, authority_id)
    try:
        with conn.transaction():
            made = conn.execute(
                "INSERT INTO trust_authority_late_fees (pmc_id, housing_authority_id, starts_on,"
                " grace_days, flat_fee) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (session.pmc_id, authority_id, new.starts_on, new.grace_days, new.flat_fee),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            409, "this authority has late fee terms from that day already"
        ) from None
    assert made is not None
    return Created(id=made[0])


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
    # Accept it past a lease's payment hold (an eviction filed), naming the staff member.
    past_hold: bool = False


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
    # A lease on a payment hold that day takes it only when staff accept it past the hold.
    if not new.past_hold:
        for line in new.lines:
            hold = conn.execute(
                HOLD_TODAY,
                {"pmc": session.pmc_id, "lease": line.lease_id, "day": new.received_on},
            ).fetchone()
            if hold is not None:
                raise HTTPException(
                    409,
                    f"{rent.lease_memo(conn, line.lease_id)} is on a payment hold from"
                    f" {hold[0]:%m/%d/%Y} ({hold[1]}); accept the payment past the hold to"
                    " record it",
                )
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
    by = rent.staff_name(conn, session.pmc_id, session.person_id) if new.past_hold else None
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
            if by is not None:
                rent.allow_past_holds(
                    conn, session.pmc_id, line.lease_id, transfer, new.received_on, by
                )
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


# --- an authority's payment the bank returned (migration 0116) ---------------------------------


class AuthorityReturn(BaseModel):
    on: date


class AuthorityReturned(BaseModel):
    transfers: list[str]
    amount: str


@router.post("/payments/{transfer_id}/bounce", status_code=201)
def bounce(
    session: Staff, conn: Conn, transfer_id: str, returned: AuthorityReturn
) -> AuthorityReturned:
    """An authority's payment the bank returned: every transfer it posted moves back to cash, and
    what each paid of the authority's charges is reversed (trust_reverse_assistance_payment), so
    they are owed again. Once per payment."""
    rent.not_after_today(returned.on)
    rows = conn.execute(
        "SELECT t.id, t.from_account_id, t.to_account_id, t.amount, t.metadata, t.event_at"
        " FROM pgledger_transfers AS t"
        " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id"
        " WHERE a.pmc_id = %s AND t.metadata ->> 'key' = ("
        "   SELECT p.metadata ->> 'key' FROM pgledger_transfers AS p WHERE p.id = %s"
        "     AND p.metadata ->> 'kind' = 'housing authority payment')"
        " ORDER BY t.created_at, t.id",
        (session.pmc_id, transfer_id),
    ).fetchall()
    if not rows:
        raise HTTPException(404, "no such housing authority payment")
    metadata = rows[0][4]
    for lease_id in sorted({line[0] for line in metadata["lines"]}):
        rent.lock_lease(conn, UUID(lease_id))
    if returned.on < rows[0][5].astimezone(UTC).date():
        raise HTTPException(422, "a payment bounces on or after the day it was received")
    name = authority_named(conn, session.pmc_id, UUID(metadata["housing_authority_id"]))
    key = f"bounce/{metadata['key']}"
    back = {
        "key": key,
        "kind": "bounced housing authority payment",
        "housing_authority_id": metadata["housing_authority_id"],
        "reference": None,
        "memo": f"Returned payment – {name}",
    }
    transfers = [(to, frm, Decimal(amount)) for _, frm, to, amount, _, _ in rows]
    posted = rent.post(conn, session.pmc_id, key, transfers, returned.on, back)
    if posted is None:
        raise HTTPException(409, "this payment bounced already")
    for (payment, *_), reversal in zip(rows, posted, strict=True):
        for charge, matched in conn.execute(
            "SELECT assistance_charge_id, amount FROM trust_assistance_payments"
            " WHERE transfer_id = %s",
            (payment,),
        ).fetchall():
            conn.execute(
                "SELECT trust_reverse_assistance_payment(%s, %s, %s, %s, %s)",
                (session.pmc_id, charge, payment, reversal, matched),
            )
    return AuthorityReturned(
        transfers=posted, amount=rent.cents(sum((t[2] for t in transfers), ZERO))
    )
