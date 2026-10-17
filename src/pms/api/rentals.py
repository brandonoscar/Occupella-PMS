"""Rentals as a property manager works them (ADR 0005): properties, a property's units, a unit's
leases and a lease with everything about it, the rent roll a page at a time, and a search box.

Reads are paged and counted on the server, so a company with thousands of units loads one
page. A property or unit is renamed (an audited update of its name) or archived (a row of
`trust_archivals`, migration 0063, refused while anything is open on it); nothing is deleted
and nothing here moves money.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, StringConstraints

from pms.api import rent, scope
from pms.api.deps import Conn, Staff, refusal
from pms.api.setup import Name

router = APIRouter(prefix="/api")

T = TypeVar("T")
Page = Annotated[int, Query(ge=1, le=100_000)]
PerPage = Annotated[int, Query(ge=1, le=200)]
Search = Annotated[str | None, Query(max_length=100)]


class Paged(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    per_page: int


def like(q: str | None) -> str | None:
    """A search's words as an ILIKE pattern that matches them anywhere, its own % and _ taken
    as themselves; nothing searched, None."""
    words = (q or "").strip()
    if not words:
        return None
    return "%" + words.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class Ref(BaseModel):
    id: UUID
    name: str


# --- properties --------------------------------------------------------------------------------


class PropertyRow(BaseModel):
    id: UUID
    name: str
    owners: list[str]
    units: int
    occupied: int
    archived: bool


# A unit occupied on the day: a lease of it in effect.
OCCUPIED = """
EXISTS (SELECT 1 FROM trust_leases AS l WHERE l.unit_id = u.id AND l.starts_on <= %(day)s
        AND (l.ends_on IS NULL OR %(day)s <= l.ends_on))
"""

PROPERTIES = f"""
SELECT p.id, p.display_name,
       array(SELECT o.display_name FROM trust_ledger_accounts AS a
             JOIN trust_owners AS o ON o.id = a.owner_id
             WHERE a.property_id = p.id AND a.kind = 'owner_property'
             ORDER BY o.display_name COLLATE "C", o.id),
       (SELECT count(*) FROM trust_units AS u
        WHERE u.property_id = p.id AND NOT trust_is_archived(NULL, u.id)),
       (SELECT count(*) FROM trust_units AS u
        WHERE u.property_id = p.id AND NOT trust_is_archived(NULL, u.id) AND {OCCUPIED}),
       trust_is_archived(p.id, NULL),
       count(*) OVER ()
FROM trust_properties AS p
WHERE p.pmc_id = %(pmc)s AND trust_is_archived(p.id, NULL) = %(archived)s
  AND (%(like)s::text IS NULL OR p.display_name ILIKE %(like)s)
  AND {scope.within("p.id")}
ORDER BY p.display_name COLLATE "C", p.id
LIMIT %(per_page)s OFFSET %(offset)s
"""


@router.get("/properties")
def properties(
    session: Staff,
    conn: Conn,
    q: Search = None,
    archived: bool = False,
    group: scope.Group = None,
    property_id: scope.Property = None,
    page: Page = 1,
    per_page: PerPage = 50,
) -> Paged[PropertyRow]:
    """The company's properties a page at a time, by name: their owners, units and how many
    are occupied today. Archived ones only when asked; maybe of a group or just one."""
    rows = conn.execute(
        PROPERTIES,
        {
            **scope.params(group, property_id),
            "pmc": session.pmc_id,
            "day": rent.today(),
            "archived": archived,
            "like": like(q),
            "per_page": per_page,
            "offset": (page - 1) * per_page,
        },
    ).fetchall()
    return Paged(
        items=[
            PropertyRow(id=i, name=n, owners=o, units=u, occupied=occ, archived=a)
            for i, n, o, u, occ, a, _ in rows
        ],
        total=rows[0][-1] if rows else 0,
        page=page,
        per_page=per_page,
    )


class OwnerAccount(BaseModel):
    owner_id: UUID
    owner: str
    account: str
    bank_account: str
    balance: str


class LeaseLine(BaseModel):
    id: UUID
    tenants: list[str]
    # The same tenants with their ids, each opening their own page.
    people: list[Ref]
    starts_on: date
    ends_on: date | None
    monthly_rent: str
    owed: str
    # current: in effect on the day; future: starts after it; ended: ended before it.
    status: Literal["current", "future", "ended"]


class UnitLine(BaseModel):
    id: UUID
    name: str
    archived: bool
    # The lease in effect on the day, or else the next one to start; None when vacant.
    lease: LeaseLine | None


class Property(BaseModel):
    id: UUID
    name: str
    archived: bool
    owners: list[OwnerAccount]
    units: list[UnitLine]


# A lease's (on l) tenants: their names, and their ids in the same order.
TENANT_NAMES = """
array(SELECT t.display_name FROM trust_lease_tenants AS lt
      JOIN trust_tenants AS t ON t.id = lt.tenant_id WHERE lt.lease_id = l.id
      ORDER BY t.display_name COLLATE "C", t.id),
array(SELECT t.id FROM trust_lease_tenants AS lt
      JOIN trust_tenants AS t ON t.id = lt.tenant_id WHERE lt.lease_id = l.id
      ORDER BY t.display_name COLLATE "C", t.id)
"""

LEASE_LINES = f"""
SELECT l.id, l.unit_id, {TENANT_NAMES},
       l.starts_on, l.ends_on, trust_rent_on(l.id, greatest(l.starts_on, %(day)s)),
       {rent.OWED}
FROM trust_leases AS l
JOIN trust_units AS u ON u.id = l.unit_id
WHERE l.pmc_id = %(pmc)s AND {{where}}
ORDER BY l.starts_on DESC, l.id
"""


def status_of(starts: date, ends: date | None, day: date) -> Literal["current", "future", "ended"]:
    if day < starts:
        return "future"
    return "ended" if ends is not None and ends < day else "current"


def lease_lines(
    conn: psycopg.Connection[Any], pmc: UUID, day: date, where: str, **params: Any
) -> list[tuple[UUID, LeaseLine]]:
    """Leases of the PMC that `where` picks (on l, the lease, and u, its unit), newest first,
    each with the unit it is of."""
    return [
        (
            unit,
            LeaseLine(
                id=i,
                tenants=tenants,
                people=[Ref(id=t, name=n) for t, n in zip(ids, tenants, strict=True)],
                starts_on=starts,
                ends_on=ends,
                monthly_rent=rent.cents(monthly),
                owed=rent.cents(owed),
                status=status_of(starts, ends, day),
            ),
        )
        for i, unit, tenants, ids, starts, ends, monthly, owed in conn.execute(
            LEASE_LINES.format(where=where), {"pmc": pmc, "day": day, **params}
        ).fetchall()
    ]


@router.get("/properties/{property_id}")
def property_page(session: Staff, conn: Conn, property_id: UUID) -> Property:
    """A property: its owners' accounts and what each holds, and its units, each with the lease
    in effect today (or the next to start)."""
    day = rent.today()
    row = conn.execute(
        "SELECT p.display_name, trust_is_archived(p.id, NULL) FROM trust_properties AS p"
        " WHERE p.pmc_id = %s AND p.id = %s",
        (session.pmc_id, property_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such property")
    owners = [
        OwnerAccount(owner_id=o, owner=n, account=a, bank_account=b, balance=rent.cents(bal))
        for o, n, a, b, bal in conn.execute(
            "SELECT o.id, o.display_name, a.ledger_account_id, k.display_name, b.balance"
            " FROM trust_ledger_accounts AS a JOIN trust_owners AS o ON o.id = a.owner_id"
            " JOIN trust_bank_accounts AS k ON k.id = a.bank_account_id"
            " JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id"
            " WHERE a.property_id = %s AND a.kind = 'owner_property'"
            ' ORDER BY o.display_name COLLATE "C", o.id',
            (property_id,),
        ).fetchall()
    ]
    # Each unit's lease in effect today, or else the soonest to start (a unit has one lease at a
    # time, so at most one is in effect).
    leases: dict[UUID, LeaseLine] = {}
    for unit, lease in sorted(
        lease_lines(
            conn,
            session.pmc_id,
            day,
            "u.property_id = %(property)s AND (l.ends_on IS NULL OR l.ends_on >= %(day)s)",
            property=property_id,
        ),
        key=lambda found: (found[1].status != "current", found[1].starts_on),
    ):
        leases.setdefault(unit, lease)
    units = [
        UnitLine(id=u, name=n, archived=a, lease=leases.get(u))
        for u, n, a in conn.execute(
            "SELECT u.id, u.display_name, trust_is_archived(NULL, u.id) FROM trust_units AS u"
            ' WHERE u.property_id = %s ORDER BY u.display_name COLLATE "C", u.id',
            (property_id,),
        ).fetchall()
    ]
    return Property(id=property_id, name=row[0], archived=row[1], owners=owners, units=units)


class Rename(BaseModel):
    name: Name


class Archiving(BaseModel):
    archived: bool


def rename(conn: psycopg.Connection[Any], table: str, pmc: UUID, record: UUID, name: str) -> None:
    found = conn.execute(
        f"UPDATE {table} SET display_name = %s WHERE pmc_id = %s AND id = %s RETURNING id",
        (name, pmc, record),
    ).fetchone()
    if found is None:
        raise HTTPException(404, f"no such {'property' if table == 'trust_properties' else 'unit'}")


def archive(
    conn: psycopg.Connection[Any],
    pmc: UUID,
    property_id: UUID | None,
    unit_id: UUID | None,
    archived: bool,
) -> None:
    table = "trust_properties" if property_id else "trust_units"
    if (
        conn.execute(
            f"SELECT 1 FROM {table} WHERE pmc_id = %s AND id = %s",
            (pmc, property_id or unit_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, f"no such {'property' if property_id else 'unit'}")
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_archivals (pmc_id, property_id, unit_id, archived)"
                " VALUES (%s, %s, %s, %s)",
                (pmc, property_id, unit_id, archived),
            )
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, refusal(refused)) from None


@router.post("/properties/{property_id}/name", status_code=204)
def rename_property(session: Staff, conn: Conn, property_id: UUID, body: Rename) -> None:
    """Renames a property; the audit log keeps the name it had."""
    rename(conn, "trust_properties", session.pmc_id, property_id, body.name)


@router.post("/properties/{property_id}/archive", status_code=204)
def archive_property(session: Staff, conn: Conn, property_id: UUID, body: Archiving) -> None:
    """Archives a property (refused while a lease, its owners' money or a bill is open on it)
    or brings it back."""
    archive(conn, session.pmc_id, property_id, None, body.archived)


# --- units ---------------------------------------------------------------------------------------


class Unit(BaseModel):
    id: UUID
    name: str
    archived: bool
    property: Ref
    leases: list[LeaseLine]


@router.get("/units/{unit_id}")
def unit_page(session: Staff, conn: Conn, unit_id: UUID) -> Unit:
    """A unit and every lease it has had, newest first."""
    row = conn.execute(
        "SELECT u.display_name, trust_is_archived(NULL, u.id), p.id, p.display_name"
        " FROM trust_units AS u JOIN trust_properties AS p ON p.id = u.property_id"
        " WHERE u.pmc_id = %s AND u.id = %s",
        (session.pmc_id, unit_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such unit")
    leases = lease_lines(conn, session.pmc_id, rent.today(), "l.unit_id = %(unit)s", unit=unit_id)
    return Unit(
        id=unit_id,
        name=row[0],
        archived=row[1],
        property=Ref(id=row[2], name=row[3]),
        leases=[lease for _, lease in leases],
    )


@router.post("/units/{unit_id}/name", status_code=204)
def rename_unit(session: Staff, conn: Conn, unit_id: UUID, body: Rename) -> None:
    """Renames a unit; the audit log keeps the name it had."""
    rename(conn, "trust_units", session.pmc_id, unit_id, body.name)


@router.post("/units/{unit_id}/archive", status_code=204)
def archive_unit(session: Staff, conn: Conn, unit_id: UUID, body: Archiving) -> None:
    """Archives a unit (refused while a lease of it is in effect or to come) or brings it
    back."""
    archive(conn, session.pmc_id, None, unit_id, body.archived)


# --- leases: the rent roll --------------------------------------------------------------------


class RentRollRow(BaseModel):
    id: UUID
    property: Ref
    unit: Ref
    tenants: list[str]
    people: list[Ref]
    starts_on: date
    ends_on: date | None
    status: Literal["current", "future", "ended"]
    # Days from today to its last day, for a lease in effect with an end date.
    days_left: int | None
    monthly_rent: str
    # The security deposits held for its tenants.
    deposit: str
    owed: str


RENT_ROLL = f"""
SELECT l.id, p.id, p.display_name, u.id, u.display_name, {TENANT_NAMES},
       l.starts_on, l.ends_on, trust_rent_on(l.id, greatest(l.starts_on, %(day)s)), x.owed,
       coalesce((SELECT sum(b.balance) FROM trust_lease_tenants AS lt
                 JOIN trust_ledger_accounts AS a
                   ON a.tenant_id = lt.tenant_id AND a.kind = 'tenant_deposit'
                 JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
                 WHERE lt.lease_id = l.id), 0),
       count(*) OVER ()
FROM trust_leases AS l
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
CROSS JOIN LATERAL (SELECT {rent.OWED} AS owed) AS x
WHERE l.pmc_id = %(pmc)s
  AND {scope.within("p.id")}
  AND (%(like)s::text IS NULL OR p.display_name ILIKE %(like)s OR u.display_name ILIKE %(like)s
       OR EXISTS (SELECT 1 FROM trust_lease_tenants AS lt
                  JOIN trust_tenants AS t ON t.id = lt.tenant_id
                  WHERE lt.lease_id = l.id AND t.display_name ILIKE %(like)s))
  AND CASE %(show)s
        WHEN 'current' THEN l.starts_on <= %(day)s AND (l.ends_on IS NULL OR %(day)s <= l.ends_on)
        WHEN 'future' THEN %(day)s < l.starts_on
        WHEN 'past' THEN l.ends_on < %(day)s
        WHEN 'owing' THEN x.owed > 0
        ELSE true
      END
ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C", l.starts_on, l.id
LIMIT %(per_page)s OFFSET %(offset)s
"""


@router.get("/leases")
def rent_roll(
    session: Staff,
    conn: Conn,
    group: scope.Group = None,
    property_id: scope.Property = None,
    q: Search = None,
    show: Literal["current", "future", "past", "owing", "all"] = "current",
    page: Page = 1,
    per_page: PerPage = 50,
) -> Paged[RentRollRow]:
    """The rent roll a page at a time: leases in effect today (or those to come, those ended,
    those that owe, or all), maybe of a group or one property, or matching a tenant, unit or
    property name, each with its days left, rent, the deposits held and what it owes."""
    day = rent.today()
    rows = conn.execute(
        RENT_ROLL,
        {
            **scope.params(group, property_id),
            "pmc": session.pmc_id,
            "day": day,
            "like": like(q),
            "show": show,
            "per_page": per_page,
            "offset": (page - 1) * per_page,
        },
    ).fetchall()
    return Paged(
        items=[
            RentRollRow(
                id=i,
                property=Ref(id=p, name=pn),
                unit=Ref(id=u, name=un),
                tenants=tenants,
                people=[Ref(id=t, name=n) for t, n in zip(ids, tenants, strict=True)],
                starts_on=starts,
                ends_on=ends,
                status=status_of(starts, ends, day),
                days_left=(ends - day).days
                if ends is not None and status_of(starts, ends, day) == "current"
                else None,
                monthly_rent=rent.cents(monthly),
                deposit=rent.cents(Decimal(deposit)),
                owed=rent.cents(Decimal(owed)),
            )
            for i, p, pn, u, un, tenants, ids, starts, ends, monthly, owed, deposit, _ in rows
        ],
        total=rows[0][-1] if rows else 0,
        page=page,
        per_page=per_page,
    )


# Whether a tenant (on t) can sign in to the portal: no login, invited (no password set yet),
# active, or disabled; and their login's id.
PORTAL = """
LEFT JOIN trust_logins AS g ON g.pmc_id = t.pmc_id AND g.tenant_id = t.id
LEFT JOIN trust_login_secrets AS s ON s.login_id = g.id
"""
PORTAL_STATUS = """
CASE WHEN g.id IS NULL THEN 'no login' WHEN g.disabled_at IS NOT NULL THEN 'disabled'
     WHEN s.login_id IS NULL THEN 'invited' ELSE 'active' END
"""
Portal = Literal["no login", "invited", "active", "disabled"]


class LeasePerson(BaseModel):
    id: UUID
    name: str
    email: str | None
    phone: str | None
    portal: Portal
    login_email: str | None


class Lease(rent.RentLease):
    property_id: UUID
    unit_id: UUID
    status: Literal["current", "future", "ended"]
    # Each tenant on it with their contact details and whether they can sign in to the portal.
    people: list[LeasePerson]


LEASE_PEOPLE = f"""
SELECT t.id, t.display_name, c.email, c.phone, {PORTAL_STATUS}, g.email
FROM trust_lease_tenants AS lt
JOIN trust_tenants AS t ON t.id = lt.tenant_id
LEFT JOIN LATERAL (
    SELECT k.email, k.phone FROM trust_contacts AS k WHERE k.tenant_id = t.id
    ORDER BY k.created_at DESC, k.id DESC LIMIT 1
) AS c ON true
{PORTAL}
WHERE lt.lease_id = %s
ORDER BY t.display_name COLLATE "C", t.id
"""


@router.get("/leases/{lease_id}")
def lease_page(session: Staff, conn: Conn, lease_id: UUID) -> Lease:
    """One lease with everything the rent work needs: its tenants (with their contact details
    and portal logins), charges, payments, recurring charges, rent changes and renters
    insurance."""
    row = conn.execute(
        "SELECT u.id, u.property_id, l.starts_on, l.ends_on FROM trust_leases AS l"
        " JOIN trust_units AS u ON u.id = l.unit_id WHERE l.pmc_id = %s AND l.id = %s",
        (session.pmc_id, lease_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such lease")
    day = rent.today()
    detail = rent.lease_detail(conn, session.pmc_id, lease_id, day)
    people = [
        LeasePerson(id=i, name=n, email=e, phone=ph, portal=po, login_email=le)
        for i, n, e, ph, po, le in conn.execute(LEASE_PEOPLE, (lease_id,)).fetchall()
    ]
    return Lease(
        **detail.model_dump(),
        unit_id=row[0],
        property_id=row[1],
        status=status_of(row[2], row[3], day),
        people=people,
    )


LedgerKind = Literal[
    "rent", "recurring", "late fee", "NSF fee", "fee", "credit", "payment", "bounced payment"
]


class LedgerLine(BaseModel):
    on: date
    # A charge's kind (rent, a recurring charge, a late fee, an NSF fee, another fee, a credit),
    # or a payment, or a bounced payment.
    kind: LedgerKind
    memo: str | None
    charged: str | None
    paid: str | None
    balance: str
    # A payment that bounced since (its bounce is a line of its own).
    bounced: bool


class Ledger(BaseModel):
    lines: list[LedgerLine]
    balance: str


# A lease's charges through a day, each with what it is, in the order its ledger lists them
# (trust_report_tenant_ledger: by due date, then when entered, then id as text).
LEDGER_CHARGES = """
SELECT CASE
         WHEN c.kind <> 'fee' THEN c.kind
         WHEN EXISTS (SELECT 1 FROM trust_late_fees AS f WHERE f.fee_charge_id = c.id)
           THEN 'late fee'
         WHEN EXISTS (SELECT 1 FROM trust_nsf_fees AS f WHERE f.fee_charge_id = c.id)
           THEN 'NSF fee'
         WHEN c.recurring_charge_id IS NOT NULL THEN 'recurring'
         ELSE 'fee'
       END
FROM trust_charges AS c
WHERE c.lease_id = %(lease)s AND c.due_on <= %(day)s
ORDER BY c.due_on, c.created_at, c.id::text
"""

# A lease's payments, in the order its ledger lists them (trust_report_tenant_ledger), each
# with its check or reference (or else how it came) and whether it bounced.
LEDGER_PAYMENTS = """
SELECT coalesce(tr.metadata ->> 'reference', tr.metadata ->> 'method'),
       EXISTS (SELECT 1 FROM trust_payment_reversals AS r WHERE r.transfer_id = tr.id)
FROM trust_charge_payments AS cp
JOIN trust_charges AS c ON c.id = cp.charge_id
JOIN pgledger_transfers AS tr ON tr.id = cp.transfer_id
WHERE c.lease_id = %(lease)s AND tr.event_at < (%(day)s::date + 1)::timestamp AT TIME ZONE 'UTC'
GROUP BY tr.id, tr.event_at, tr.metadata
ORDER BY (tr.event_at AT TIME ZONE 'UTC')::date, tr.event_at, tr.id
"""


@router.get("/leases/{lease_id}/ledger")
def lease_ledger(session: Staff, conn: Conn, lease_id: UUID) -> Ledger:
    """A lease's ledger through today: its charges, payments and bounces, oldest first, each
    with the balance after it, as `trust_report_tenant_ledger` gives it; each charge says what it
    is, and a payment its check or reference and whether it bounced."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_leases WHERE pmc_id = %s AND id = %s", (session.pmc_id, lease_id)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such lease")
    day = rent.today()
    report = conn.execute(
        "SELECT on_date, item, detail, charged, paid, balance"
        " FROM trust_report_tenant_ledger(%s, %s, %s) ORDER BY line",
        (session.pmc_id, lease_id, day),
    ).fetchall()
    asked = {"lease": lease_id, "day": day}
    charges = iter(row[0] for row in conn.execute(LEDGER_CHARGES, asked).fetchall())
    payments = iter(conn.execute(LEDGER_PAYMENTS, asked).fetchall())
    lines = []
    for on, item, memo, charged, paid, balance in report[1:-1]:
        reference, bounced = next(payments) if item == "payment" else (None, False)
        kind = item if item in ("payment", "bounced payment") else next(charges)
        lines.append(
            LedgerLine(
                on=on,
                kind=kind,
                # A payment's check or reference, or how it came: the lease names its tenants and
                # home already, as the receipt's memo does (src/pms/api/rent.py).
                memo=reference if item == "payment" else memo,
                charged=None if not charged else rent.cents(charged),
                paid=None if not paid else rent.cents(paid),
                balance=rent.cents(balance),
                bounced=bounced,
            )
        )
    owed = report[-1][3] - report[-1][4]
    return Ledger(lines=lines, balance=rent.cents(owed))


# --- search ------------------------------------------------------------------------------------


class Found(BaseModel):
    kind: Literal["property", "unit", "tenant", "owner"]
    id: UUID
    label: str
    detail: str
    href: str


SearchWords = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]

SEARCH = """
(SELECT 'property', p.id, p.display_name, '', '/properties/' || p.id
 FROM trust_properties AS p
 WHERE p.pmc_id = %(pmc)s AND p.display_name ILIKE %(like)s AND NOT trust_is_archived(p.id, NULL)
 ORDER BY p.display_name COLLATE "C", p.id LIMIT 10)
UNION ALL
(SELECT 'unit', u.id, u.display_name, p.display_name, '/units/' || u.id
 FROM trust_units AS u JOIN trust_properties AS p ON p.id = u.property_id
 WHERE u.pmc_id = %(pmc)s AND u.display_name ILIKE %(like)s AND NOT trust_is_archived(NULL, u.id)
 ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C", u.id LIMIT 10)
UNION ALL
(SELECT 'tenant', t.id, t.display_name,
        coalesce(u.display_name || ', ' || p.display_name, p.display_name), '/tenants/' || t.id
 FROM trust_tenants AS t JOIN trust_properties AS p ON p.id = t.property_id
 LEFT JOIN LATERAL (
     SELECT x.unit_id FROM trust_lease_tenants AS lt JOIN trust_leases AS x
     ON x.id = lt.lease_id WHERE lt.tenant_id = t.id ORDER BY x.starts_on DESC, x.id LIMIT 1
 ) AS l ON true
 LEFT JOIN trust_units AS u ON u.id = l.unit_id
 WHERE t.pmc_id = %(pmc)s AND t.display_name ILIKE %(like)s
   AND NOT trust_is_archived(NULL, NULL, t.id)
 ORDER BY t.display_name COLLATE "C", t.id LIMIT 10)
UNION ALL
(SELECT 'owner', o.id, o.display_name, '', '/owners/' || o.id
 FROM trust_owners AS o
 WHERE o.pmc_id = %(pmc)s AND o.display_name ILIKE %(like)s
   AND NOT trust_is_archived(NULL, NULL, NULL, o.id)
 ORDER BY o.display_name COLLATE "C", o.id LIMIT 10)
"""


@router.get("/search")
def search(session: Staff, conn: Conn, q: Annotated[SearchWords, Query()]) -> list[Found]:
    """Properties, units, tenants and owners not archived whose names hold the words, up to 10
    of each, each with the page it opens (a tenant's says their latest unit)."""
    return [
        Found(kind=k, id=i, label=label, detail=detail, href=href)
        for k, i, label, detail, href in conn.execute(
            SEARCH, {"pmc": session.pmc_id, "like": like(q)}
        ).fetchall()
    ]
