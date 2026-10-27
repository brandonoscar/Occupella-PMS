"""Tenants and owners as a property manager works them (ADR 0005): each a page at a time, each on
a page of their own, renamed, archived and reached by their contact details.

A tenant's page has their leases, their prepaid rent and deposit, and their contact details; an
owner's has each of their property accounts (what it holds and may pay out, its draws and
contributions) and their contact details. Contact details (`trust_contacts`, migration 0064) are
a row per change, the latest holding; the AI role never reads them. Archiving is a row of
`trust_archivals`, refused while anything is open on the person. Nothing here moves money: an
owner's draws and contributions go through the owners' money paths (`payables.py`).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, StringConstraints, model_validator

from pms import exports
from pms.api import owner as owner_portal
from pms.api import payables, rent, scope
from pms.api.deps import Conn, Rows, Staff, download, refusal
from pms.api.rentals import (
    PORTAL,
    PORTAL_STATUS,
    Archiving,
    LeaseLine,
    Page,
    Paged,
    PerPage,
    Portal,
    Ref,
    Rename,
    Search,
    lease_lines,
    like,
    status_of,
)

router = APIRouter(prefix="/api")

# --- contact details ---------------------------------------------------------------------------

Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\+?[0-9 ().-]{7,30}$")]


Street = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
City = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
State = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
PostalCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]

ADDRESS = ("address_line_1", "city", "state", "postal_code")


class Contact(BaseModel):
    email: Email | None = None
    phone: Phone | None = None
    alternate_phone: Phone | None = None
    address_line_1: Street | None = None
    address_line_2: Street | None = None
    city: City | None = None
    state: State | None = None
    postal_code: PostalCode | None = None

    @model_validator(mode="after")
    def whole_address(self) -> Contact:
        given = [getattr(self, part) is not None for part in ADDRESS]
        if any(given) and not all(given) or self.address_line_2 and not self.address_line_1:
            raise ValueError("a mailing address needs its street, city, state and ZIP code")
        return self


FIELDS = list(Contact.model_fields)
Person = Literal["tenant_id", "owner_id", "vendor_id"]


def contact_of(conn: psycopg.Connection[Any], person: Person, person_id: UUID) -> Contact:
    """A person's latest contact details; none recorded, all empty."""
    row = conn.execute(
        f"SELECT {', '.join(FIELDS)} FROM trust_contacts WHERE {person} = %s"  # noqa: S608
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (person_id,),
    ).fetchone()
    return Contact() if row is None else Contact(**dict(zip(FIELDS, row, strict=True)))


def contacts_of(conn: psycopg.Connection[Any], pmc: UUID, person: Person) -> dict[UUID, Contact]:
    """The latest contact details of each of the PMC's people of a kind who have some."""
    return {
        row[0]: Contact(**dict(zip(FIELDS, row[1:], strict=True)))
        for row in conn.execute(
            f"SELECT DISTINCT ON ({person}) {person}, {', '.join(FIELDS)} FROM trust_contacts"  # noqa: S608
            f" WHERE pmc_id = %s AND {person} IS NOT NULL"
            f" ORDER BY {person}, created_at DESC, id DESC",
            (pmc,),
        ).fetchall()
    }


def record_contact(
    conn: psycopg.Connection[Any],
    pmc: UUID,
    person: Person,
    person_id: UUID,
    details: Contact,
    by_person: bool = False,
) -> None:
    """Records a person's contact details as given (all of them, so a field left empty is
    cleared), by staff or by the person themselves (a tenant in the portal); the same details
    again write nothing."""
    if contact_of(conn, person, person_id) == details:
        return
    conn.execute(
        f"INSERT INTO trust_contacts (pmc_id, {person}, by_person, {', '.join(FIELDS)})"  # noqa: S608
        f" VALUES (%s, %s, %s, {', '.join(['%s'] * len(FIELDS))})",
        (pmc, person_id, by_person, *(getattr(details, field) for field in FIELDS)),
    )


def changed_by_person(
    conn: psycopg.Connection[Any], person: Person, person_id: UUID
) -> datetime | None:
    """When the person themselves gave the contact details that hold, if they did."""
    row = conn.execute(
        f"SELECT by_person, created_at FROM trust_contacts WHERE {person} = %s"  # noqa: S608
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (person_id,),
    ).fetchone()
    return row[1] if row is not None and row[0] else None


def found(conn: psycopg.Connection[Any], table: str, pmc: UUID, record: UUID, what: str) -> None:
    """404 unless the PMC has the record."""
    if (
        conn.execute(
            f"SELECT 1 FROM {table} WHERE pmc_id = %s AND id = %s",  # noqa: S608
            (pmc, record),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, f"no such {what}")


def archive(
    conn: psycopg.Connection[Any], pmc: UUID, person: Person, record: UUID, archived: bool
) -> None:
    try:
        with conn.transaction():
            conn.execute(
                f"INSERT INTO trust_archivals (pmc_id, {person}, archived) VALUES (%s, %s, %s)",  # noqa: S608
                (pmc, record, archived),
            )
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, refusal(refused)) from None


def rename(conn: psycopg.Connection[Any], table: str, pmc: UUID, record: UUID, name: str) -> None:
    conn.execute(
        f"UPDATE {table} SET display_name = %s WHERE pmc_id = %s AND id = %s",  # noqa: S608
        (name, pmc, record),
    )


# --- tenants -----------------------------------------------------------------------------------


class TenantLeaseRef(BaseModel):
    id: UUID
    unit: Ref
    status: Literal["current", "future", "ended"]


class TenantRow(BaseModel):
    id: UUID
    name: str
    property: Ref
    # The lease in effect today, or else their latest; None before their first.
    lease: TenantLeaseRef | None
    email: str | None
    phone: str | None
    archived: bool
    # What the lease shown owes today (0.00 without one), and whether they can sign in.
    owed: str
    portal: Portal


# A person's latest contact details, on c.
LATEST_CONTACT = """
LEFT JOIN LATERAL (
    SELECT k.email, k.phone FROM trust_contacts AS k WHERE k.{person} = {key}
    ORDER BY k.created_at DESC, k.id DESC LIMIT 1
) AS c ON true
"""

# A tenant's lease in effect today, else their latest (the one the list shows).
TENANT_LEASE = """
LEFT JOIN LATERAL (
    SELECT x.id, x.unit_id, x.starts_on, x.ends_on FROM trust_lease_tenants AS lt
    JOIN trust_leases AS x ON x.id = lt.lease_id
    WHERE lt.tenant_id = t.id
    ORDER BY (x.starts_on <= %(day)s AND (x.ends_on IS NULL OR %(day)s <= x.ends_on)) DESC,
             x.starts_on DESC, x.id
    LIMIT 1
) AS l ON true
LEFT JOIN trust_units AS u ON u.id = l.unit_id
"""

# The page of tenants first, by what they are found and sorted by; then what each shows (their
# contact details, portal login and what their lease owes) for that page alone.
TENANTS = f"""
WITH paged AS (
    SELECT t.id, count(*) OVER () AS total
    FROM trust_tenants AS t
    JOIN trust_properties AS p ON p.id = t.property_id
    {TENANT_LEASE}
    WHERE t.pmc_id = %(pmc)s AND trust_is_archived(NULL, NULL, t.id) = %(archived)s
      AND {scope.within("t.property_id")}
      AND (%(like)s::text IS NULL OR t.display_name ILIKE %(like)s
           OR p.display_name ILIKE %(like)s OR u.display_name ILIKE %(like)s)
    ORDER BY t.display_name COLLATE "C", t.id
    LIMIT %(per_page)s OFFSET %(offset)s
)
SELECT t.id, t.display_name, p.id, p.display_name, l.id, u.id, u.display_name, l.starts_on,
       l.ends_on, c.email, c.phone, trust_is_archived(NULL, NULL, t.id), {rent.OWED},
       {PORTAL_STATUS}, paged.total
FROM paged
JOIN trust_tenants AS t ON t.id = paged.id
JOIN trust_properties AS p ON p.id = t.property_id
{TENANT_LEASE}
{LATEST_CONTACT.format(person="tenant_id", key="t.id")}
{PORTAL}
ORDER BY t.display_name COLLATE "C", t.id
"""  # noqa: S608


@router.get("/tenants")
def tenants(
    session: Staff,
    conn: Conn,
    q: Search = None,
    archived: bool = False,
    group: scope.Group = None,
    property_id: scope.Property = None,
    page: Page = 1,
    per_page: PerPage = 50,
) -> Paged[TenantRow]:
    """The company's tenants a page at a time, found by their name, property or unit: each
    with their lease in effect today (or latest), email and phone. Archived ones on request;
    maybe of a group's properties or one property's."""
    day = rent.today()
    rows = conn.execute(
        TENANTS,
        {
            **scope.params(group, property_id),
            "pmc": session.pmc_id,
            "day": day,
            "archived": archived,
            "like": like(q),
            "per_page": per_page,
            "offset": (page - 1) * per_page,
        },
    ).fetchall()
    return Paged(
        items=[
            TenantRow(
                id=i,
                name=name,
                property=Ref(id=p, name=pn),
                lease=None
                if lease is None
                else TenantLeaseRef(
                    id=lease, unit=Ref(id=u, name=un), status=status_of(starts, ends, day)
                ),
                email=email,
                phone=phone,
                archived=gone,
                owed=rent.cents(owed),
                portal=portal,
            )
            for i, name, p, pn, lease, u, un, starts, ends, email, phone, gone, owed, portal, _ in (
                rows
            )
        ],
        total=rows[0][-1] if rows else 0,
        page=page,
        per_page=per_page,
    )


class TenantLease(LeaseLine):
    unit: Ref
    property: Ref


class OtherRecord(BaseModel):
    """The same person's record at another property (migration 0105)."""

    id: UUID
    property: Ref
    # Their first record: the one they sign in with.
    first: bool


class Tenant(BaseModel):
    id: UUID
    name: str
    archived: bool
    property: Ref
    contact: Contact
    # When the tenant gave the contact details that hold themselves, in the portal; None when
    # staff did (or nobody has).
    contact_by_tenant_at: datetime | None
    prepaid: str
    deposit: str
    leases: list[TenantLease]
    # Whether they can sign in to the portal, and their login's email.
    portal: Portal
    login_email: str | None
    # Renting at other properties too: their records there, each with its own leases and money.
    same_person: list[OtherRecord]


# The person's records but the one asked about (%(pmc)s, %(tenant)s), by property.
SAME_PERSON = """
SELECT t.id, p.id, p.display_name,
       NOT EXISTS (SELECT 1 FROM trust_tenant_links AS k WHERE k.tenant_id = t.id)
FROM trust_tenants AS t JOIN trust_properties AS p ON p.id = t.property_id
WHERE t.pmc_id = %(pmc)s AND t.id <> %(tenant)s
  AND t.id IN (SELECT trust_tenant_records(%(pmc)s, %(tenant)s))
ORDER BY p.display_name COLLATE "C", t.id
"""


@router.get("/tenants/{tenant_id}")
def tenant_page(session: Staff, conn: Conn, tenant_id: UUID) -> Tenant:
    """A tenant: their contact details, prepaid rent and deposits held, and every lease they
    have been on, newest first."""
    row = conn.execute(
        "SELECT t.display_name, trust_is_archived(NULL, NULL, t.id), p.id, p.display_name,"
        " coalesce(sum(b.balance) FILTER (WHERE a.kind = 'prepaid_rent'), 0),"
        " coalesce(sum(b.balance) FILTER (WHERE a.kind = 'tenant_deposit'), 0)"
        " FROM trust_tenants AS t JOIN trust_properties AS p ON p.id = t.property_id"
        " LEFT JOIN trust_ledger_accounts AS a ON a.tenant_id = t.id"
        " LEFT JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id"
        " WHERE t.pmc_id = %s AND t.id = %s GROUP BY t.id, p.id",
        (session.pmc_id, tenant_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such tenant")
    name, gone, p, pn, prepaid, deposit = row
    login = conn.execute(
        f"SELECT {PORTAL_STATUS}, g.email FROM trust_tenants AS t {PORTAL} WHERE t.id = %s",  # noqa: S608
        (tenant_id,),
    ).fetchone()
    assert login is not None
    portal: Portal = login[0]
    login_email: str | None = login[1]
    lines = lease_lines(
        conn,
        session.pmc_id,
        rent.today(),
        "EXISTS (SELECT 1 FROM trust_lease_tenants AS lt"
        " WHERE lt.lease_id = l.id AND lt.tenant_id = %(tenant)s)",
        tenant=tenant_id,
    )
    units = {
        u: (Ref(id=u, name=un), Ref(id=up, name=upn))
        for u, un, up, upn in conn.execute(
            "SELECT u.id, u.display_name, p.id, p.display_name FROM trust_units AS u"
            " JOIN trust_properties AS p ON p.id = u.property_id WHERE u.id = ANY(%s)",
            ([unit for unit, _ in lines],),
        ).fetchall()
    }
    leases = [
        TenantLease(**lease.model_dump(), unit=units[unit][0], property=units[unit][1])
        for unit, lease in lines
    ]
    return Tenant(
        id=tenant_id,
        name=name,
        archived=gone,
        property=Ref(id=p, name=pn),
        contact=contact_of(conn, "tenant_id", tenant_id),
        contact_by_tenant_at=changed_by_person(conn, "tenant_id", tenant_id),
        prepaid=rent.cents(prepaid),
        deposit=rent.cents(deposit),
        leases=leases,
        portal=portal,
        login_email=login_email,
        same_person=[
            OtherRecord(id=record, property=Ref(id=place, name=named), first=first)
            for record, place, named, first in conn.execute(
                SAME_PERSON, {"pmc": session.pmc_id, "tenant": tenant_id}
            ).fetchall()
        ],
    )


class Elsewhere(BaseModel):
    property_id: UUID


class Created(BaseModel):
    id: UUID


class SamePerson(BaseModel):
    # A record of theirs at another property.
    tenant_id: UUID


def first_record(conn: psycopg.Connection[Any], pmc: UUID, tenant_id: UUID) -> UUID:
    """The person's first record: the one a record is linked to, else the record itself."""
    row = conn.execute(
        "SELECT same_as FROM trust_tenant_links WHERE pmc_id = %s AND tenant_id = %s",
        (pmc, tenant_id),
    ).fetchone()
    return tenant_id if row is None else UUID(str(row[0]))


def link(conn: psycopg.Connection[Any], pmc: UUID, tenant_id: UUID, same_as: UUID) -> None:
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_tenant_links (pmc_id, tenant_id, same_as) VALUES (%s, %s, %s)",
                (pmc, tenant_id, same_as),
            )
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, refusal(refused)) from None


@router.post("/tenants/{tenant_id}/elsewhere", status_code=201)
def rent_elsewhere(session: Staff, conn: Conn, tenant_id: UUID, body: Elsewhere) -> Created:
    """A record for the tenant at another property where they rent too (migration 0105, q009):
    the same name and contact details, linked to their first record, to put on the lease there.
    What they hold at each property stays its record's own; they sign in once, as their first."""
    found(conn, "trust_tenants", session.pmc_id, tenant_id, "tenant")
    found(conn, "trust_properties", session.pmc_id, body.property_id, "property")
    row = conn.execute(
        "WITH new AS (INSERT INTO trust_tenants (pmc_id, property_id, display_name)"
        " SELECT pmc_id, %s, display_name FROM trust_tenants WHERE id = %s RETURNING id)"
        " SELECT id FROM new",
        (body.property_id, tenant_id),
    ).fetchone()
    assert row is not None
    record = UUID(str(row[0]))
    link(conn, session.pmc_id, record, first_record(conn, session.pmc_id, tenant_id))
    record_contact(
        conn, session.pmc_id, "tenant_id", record, contact_of(conn, "tenant_id", tenant_id)
    )
    return Created(id=record)


@router.post("/tenants/{tenant_id}/same-person", status_code=204)
def same_person(session: Staff, conn: Conn, tenant_id: UUID, body: SamePerson) -> None:
    """Records that this tenant is the same person as a tenant at another property (an applicant
    who already rents from the company, say): it joins their first record, who signs in for both."""
    found(conn, "trust_tenants", session.pmc_id, tenant_id, "tenant")
    found(conn, "trust_tenants", session.pmc_id, body.tenant_id, "tenant")
    if body.tenant_id == tenant_id:
        raise HTTPException(422, "pick the same person's record at another property")
    link(conn, session.pmc_id, tenant_id, first_record(conn, session.pmc_id, body.tenant_id))


@router.post("/tenants/{tenant_id}/name", status_code=204)
def rename_tenant(session: Staff, conn: Conn, tenant_id: UUID, body: Rename) -> None:
    """Renames a tenant; the audit log keeps the name they had."""
    found(conn, "trust_tenants", session.pmc_id, tenant_id, "tenant")
    rename(conn, "trust_tenants", session.pmc_id, tenant_id, body.name)


@router.post("/tenants/{tenant_id}/archive", status_code=204)
def archive_tenant(session: Staff, conn: Conn, tenant_id: UUID, body: Archiving) -> None:
    """Archives a tenant (refused while on a lease in effect or to come, or holding prepaid
    rent or a deposit) or brings them back."""
    found(conn, "trust_tenants", session.pmc_id, tenant_id, "tenant")
    archive(conn, session.pmc_id, "tenant_id", tenant_id, body.archived)


@router.post("/tenants/{tenant_id}/contact", status_code=204)
def tenant_contact(session: Staff, conn: Conn, tenant_id: UUID, body: Contact) -> None:
    """Records a tenant's contact details; the ones before stay in the history."""
    found(conn, "trust_tenants", session.pmc_id, tenant_id, "tenant")
    record_contact(conn, session.pmc_id, "tenant_id", tenant_id, body)


# --- owners ------------------------------------------------------------------------------------


class OwnerRow(BaseModel):
    id: UUID
    name: str
    properties: list[str]
    # What their property accounts hold today, keep back (the reserves) and may pay out
    # (payables.owner_money), together.
    held: str
    reserve: str
    available: str
    email: str | None
    phone: str | None
    archived: bool


OWNERS = f"""
SELECT o.id, o.display_name,
       array(SELECT p.display_name FROM trust_ledger_accounts AS a
             JOIN trust_properties AS p ON p.id = a.property_id
             WHERE a.owner_id = o.id AND a.kind = 'owner_property'
             ORDER BY p.display_name COLLATE "C", p.id),
       (SELECT coalesce(sum(b.balance), 0) FROM trust_ledger_accounts AS a
        JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
        WHERE a.owner_id = o.id AND a.kind = 'owner_property'),
       c.email, c.phone, trust_is_archived(NULL, NULL, NULL, o.id), count(*) OVER ()
FROM trust_owners AS o
{LATEST_CONTACT.format(person="owner_id", key="o.id")}
WHERE o.pmc_id = %(pmc)s AND trust_is_archived(NULL, NULL, NULL, o.id) = %(archived)s
  AND (%(scope_group)s::uuid IS NULL AND %(scope_property)s::uuid IS NULL
       OR EXISTS (SELECT 1 FROM trust_ledger_accounts AS a
                  WHERE a.owner_id = o.id AND a.kind = 'owner_property'
                    AND {scope.within("a.property_id")}))
  AND (%(like)s::text IS NULL OR o.display_name ILIKE %(like)s
       OR EXISTS (SELECT 1 FROM trust_ledger_accounts AS a
                  JOIN trust_properties AS p ON p.id = a.property_id
                  WHERE a.owner_id = o.id AND p.display_name ILIKE %(like)s))
ORDER BY o.display_name COLLATE "C", o.id
LIMIT %(per_page)s OFFSET %(offset)s
"""  # noqa: S608


@router.get("/owners")
def owners(
    session: Staff,
    conn: Conn,
    q: Search = None,
    archived: bool = False,
    group: scope.Group = None,
    property_id: scope.Property = None,
    page: Page = 1,
    per_page: PerPage = 50,
) -> Paged[OwnerRow]:
    """The company's owners a page at a time, found by their name or a property's: each with
    their properties, what their accounts hold, email and phone. Archived ones on request;
    maybe only owners of a group's properties or one property."""
    rows = conn.execute(
        OWNERS,
        {
            **scope.params(group, property_id),
            "pmc": session.pmc_id,
            "archived": archived,
            "like": like(q),
            "per_page": per_page,
            "offset": (page - 1) * per_page,
        },
    ).fetchall()
    money = payables.owner_money(conn, session.pmc_id, owners=[row[0] for row in rows])
    reserve: dict[UUID, Decimal] = {}
    available: dict[UUID, Decimal] = {}
    for account in money:
        reserve[account.owner_id] = reserve.get(account.owner_id, rent.ZERO) + Decimal(
            account.reserve
        )
        available[account.owner_id] = available.get(account.owner_id, rent.ZERO) + Decimal(
            account.available
        )
    return Paged(
        items=[
            OwnerRow(
                id=i,
                name=name,
                properties=props,
                held=rent.cents(held),
                reserve=rent.cents(reserve.get(i, rent.ZERO)),
                available=rent.cents(available.get(i, rent.ZERO)),
                email=email,
                phone=phone,
                archived=gone,
            )
            for i, name, props, held, email, phone, gone, _ in rows
        ],
        total=rows[0][-1] if rows else 0,
        page=page,
        per_page=per_page,
    )


class Agreement(BaseModel):
    account: str
    starts_on: date
    fee_percent: str
    minimum_fee: str
    flat_fee: str
    leasing_fee_percent: str
    reserve: str
    approval_limit: str
    # The percent of a bill the company may add as its markup (migration 0083); 0: none.
    bill_markup_percent: str


class Owner(BaseModel):
    id: UUID
    name: str
    archived: bool
    contact: Contact
    accounts: list[payables.OwnerMoney]
    # The management agreement in force today on each account that has one.
    agreements: list[Agreement]
    # Whether they can sign in to the portal, and their login's email.
    portal: Portal
    login_email: str | None


AGREEMENTS = """
SELECT DISTINCT ON (g.ledger_account_id) g.ledger_account_id, g.starts_on, g.fee_percent,
       g.minimum_fee, g.flat_fee, g.leasing_fee_percent, g.reserve, g.approval_limit,
       g.bill_markup_percent
FROM trust_management_agreements AS g
JOIN trust_ledger_accounts AS a ON a.ledger_account_id = g.ledger_account_id
WHERE a.pmc_id = %s AND a.owner_id = %s AND g.starts_on <= %s
ORDER BY g.ledger_account_id, g.starts_on DESC
"""
OWNER_PORTAL = """
LEFT JOIN trust_logins AS g ON g.pmc_id = o.pmc_id AND g.owner_id = o.id
LEFT JOIN trust_login_secrets AS s ON s.login_id = g.id
"""


@router.get("/owners/{owner_id}")
def owner_page(session: Staff, conn: Conn, owner_id: UUID) -> Owner:
    """An owner: their contact details and each of their property accounts today, what it
    holds, keeps back and may pay out."""
    row = conn.execute(
        "SELECT display_name, trust_is_archived(NULL, NULL, NULL, id) FROM trust_owners"
        " WHERE pmc_id = %s AND id = %s",
        (session.pmc_id, owner_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such owner")
    login = conn.execute(
        f"SELECT {PORTAL_STATUS}, g.email FROM trust_owners AS o {OWNER_PORTAL} WHERE o.id = %s",  # noqa: S608
        (owner_id,),
    ).fetchone()
    assert login is not None
    portal: Portal = login[0]
    return Owner(
        id=owner_id,
        name=row[0],
        archived=row[1],
        contact=contact_of(conn, "owner_id", owner_id),
        accounts=payables.owner_money(conn, session.pmc_id, owner_id),
        agreements=[
            Agreement(
                account=account,
                starts_on=starts,
                fee_percent=f"{percent.normalize():f}",
                minimum_fee=rent.cents(minimum),
                flat_fee=rent.cents(flat),
                leasing_fee_percent=f"{leasing.normalize():f}",
                reserve=rent.cents(reserve),
                approval_limit=rent.cents(limit),
                bill_markup_percent=f"{markup.normalize():f}",
            )
            for (
                account,
                starts,
                percent,
                minimum,
                flat,
                leasing,
                reserve,
                limit,
                markup,
            ) in conn.execute(AGREEMENTS, (session.pmc_id, owner_id, rent.today())).fetchall()
        ],
        portal=portal,
        login_email=login[1],
    )


@router.get("/owners/{owner_id}/statement")
def owner_statement(
    session: Staff,
    conn: Conn,
    owner_id: UUID,
    start: Annotated[date, Query()],
    end: Annotated[date, Query()],
) -> Rows:
    """An owner's statement over whole days (UTC), as the owner reads it in the portal: each
    property account's opening balance, what came in and went out, and its closing balance."""
    return Rows.of(statement_of(conn, session.pmc_id, owner_id, start, end))


def statement_of(
    conn: psycopg.Connection[Any], pmc_id: UUID, owner_id: UUID, start: date, end: date
) -> exports.Table:
    found = conn.execute(
        "SELECT 1 FROM trust_owners WHERE pmc_id = %s AND id = %s", (pmc_id, owner_id)
    ).fetchone()
    if found is None:
        raise HTTPException(404, "no such owner")
    table = owner_portal.statement_table(conn, pmc_id, owner_id, start, end)
    return exports.Table(
        f"Owner statement, {start.isoformat()} to {end.isoformat()}", table.columns, table.rows
    )


@router.get("/owners/{owner_id}/statement/download")
def owner_statement_download(
    session: Staff,
    conn: Conn,
    owner_id: UUID,
    start: Annotated[date, Query()],
    end: Annotated[date, Query()],
    fmt: Annotated[Literal["csv", "xlsx", "pdf"], Query(alias="format")] = "pdf",
) -> Response:
    """The same statement as a file, as the owner downloads it in the portal."""
    table = statement_of(conn, session.pmc_id, owner_id, start, end)
    name = f"owner-statement-{start.isoformat()}-{end.isoformat()}"
    return download(exports.export(table, fmt), name, fmt)


@router.post("/owners/{owner_id}/name", status_code=204)
def rename_owner(session: Staff, conn: Conn, owner_id: UUID, body: Rename) -> None:
    """Renames an owner; the audit log keeps the name they had."""
    found(conn, "trust_owners", session.pmc_id, owner_id, "owner")
    rename(conn, "trust_owners", session.pmc_id, owner_id, body.name)


@router.post("/owners/{owner_id}/archive", status_code=204)
def archive_owner(session: Staff, conn: Conn, owner_id: UUID, body: Archiving) -> None:
    """Archives an owner (refused while their accounts hold money or a bill not paid) or
    brings them back."""
    found(conn, "trust_owners", session.pmc_id, owner_id, "owner")
    archive(conn, session.pmc_id, "owner_id", owner_id, body.archived)


@router.post("/owners/{owner_id}/contact", status_code=204)
def owner_contact(session: Staff, conn: Conn, owner_id: UUID, body: Contact) -> None:
    """Records an owner's contact details; the ones before stay in the history."""
    found(conn, "trust_owners", session.pmc_id, owner_id, "owner")
    record_contact(conn, session.pmc_id, "owner_id", owner_id, body)


# --- vendors -----------------------------------------------------------------------------------


@router.post("/vendors/{vendor_id}/contact", status_code=204)
def vendor_contact(session: Staff, conn: Conn, vendor_id: UUID, body: Contact) -> None:
    """Records a vendor's contact details; the ones before stay in the history."""
    found(conn, "trust_vendors", session.pmc_id, vendor_id, "vendor")
    record_contact(conn, session.pmc_id, "vendor_id", vendor_id, body)
