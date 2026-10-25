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
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, StringConstraints

from pms import exports
from pms.api import addresses, rent, scope, setup, unit_details
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
    # Its street address on one line, if it has one (migration 0109).
    address: str | None
    owners: list[str]
    units: int
    occupied: int
    archived: bool


# A unit occupied on the day: a lease of it in effect.
OCCUPIED = """
EXISTS (SELECT 1 FROM trust_leases AS l WHERE l.unit_id = u.id AND l.starts_on <= %(day)s
        AND (l.ends_on IS NULL OR %(day)s <= l.ends_on))
"""

# A property's (p) street address on one line, or NULL.
ADDRESS = f"(SELECT {addresses.ONE_LINE} FROM trust_property_address(p.id) AS a)"  # noqa: S608

PROPERTIES = f"""
SELECT p.id, p.display_name, {ADDRESS},
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
  AND (%(like)s::text IS NULL OR p.display_name ILIKE %(like)s OR {ADDRESS} ILIKE %(like)s)
  AND {scope.within("p.id")}
ORDER BY p.display_name COLLATE "C", p.id
LIMIT %(per_page)s OFFSET %(offset)s
"""  # noqa: S608


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
    """The company's properties a page at a time, by name: their addresses, owners, units and
    how many are occupied today. Searched by name or address; archived ones only when asked;
    maybe of a group or just one."""
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
            PropertyRow(id=i, name=n, address=at, owners=o, units=u, occupied=occ, archived=a)
            for i, n, at, o, u, occ, a, _ in rows
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
    # The management terms in force today, if any.
    agreement: setup.Agreement | None
    # The owner's percent in the ownership shares in force today; None without one.
    percent: str | None = None


class Share(BaseModel):
    account: str
    owner: str
    percent: str


class Ownership(BaseModel):
    """A property's ownership from a day (migration 0094), as recorded."""

    id: UUID
    starts_on: date
    recorded_by: str | None
    shares: list[Share]


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
    # Its bedrooms, bathrooms, square feet and market rent (migration 0124).
    details: unit_details.UnitDetails
    # The lease in effect on the day, or else the next one to start; None when vacant.
    lease: LeaseLine | None


class Property(BaseModel):
    id: UUID
    name: str
    archived: bool
    # Its street address now, and where that address is on a map once found (migration 0109);
    # whether this server has a geocoder to find it with.
    address: addresses.PropertyAddress | None
    location: addresses.Location | None
    can_locate: bool
    owners: list[OwnerAccount]
    # Its ownership shares, the latest first: the one from the latest day not after today holds
    # now, any from a later day hold from then.
    ownership: list[Ownership]
    units: list[UnitLine]
    # The late fee, NSF fee, lease-break fee and deposit interest terms in force today, if any.
    late_fees: setup.LateFees | None
    nsf_fee: setup.NsfFee | None
    lease_break_fee: setup.LeaseBreakFee | None
    deposit_interest: setup.DepositInterestTerms | None


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
"""  # noqa: S608


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
def property_page(request: Request, session: Staff, conn: Conn, property_id: UUID) -> Property:
    """A property: its address, its owners' accounts, what each holds and its management terms,
    its units, each with the lease in effect today (or the next to start), and its late and NSF
    fee terms."""
    day = rent.today()
    row = conn.execute(
        "SELECT p.display_name, trust_is_archived(p.id, NULL) FROM trust_properties AS p"
        " WHERE p.pmc_id = %s AND p.id = %s",
        (session.pmc_id, property_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such property")
    owners = [
        OwnerAccount(
            owner_id=o,
            owner=n,
            account=a,
            bank_account=b,
            balance=rent.cents(bal),
            agreement=None
            if starts is None
            else setup.Agreement(
                starts_on=starts,
                fee_percent=exports.text(fee),
                minimum_fee=exports.text(minimum),
                flat_fee=exports.text(flat),
                leasing_fee_percent=exports.text(leasing),
                reserve=exports.text(reserve),
                approval_limit=exports.text(limit),
                bill_markup_percent=exports.text(markup),
            ),
            percent=None if share is None else exports.text(share),
        )
        for (
            o,
            n,
            a,
            b,
            bal,
            starts,
            fee,
            minimum,
            flat,
            leasing,
            reserve,
            limit,
            markup,
            share,
        ) in conn.execute(
            "SELECT o.id, o.display_name, a.ledger_account_id, k.display_name, b.balance,"
            " m.starts_on, m.fee_percent, m.minimum_fee, m.flat_fee, m.leasing_fee_percent,"
            " m.reserve, m.approval_limit, m.bill_markup_percent,"
            " (SELECT s.percent FROM trust_ownership_on(a.property_id, %s) AS s"
            "  WHERE s.ledger_account_id = a.ledger_account_id)"
            " FROM trust_ledger_accounts AS a JOIN trust_owners AS o ON o.id = a.owner_id"
            " JOIN trust_bank_accounts AS k ON k.id = a.bank_account_id"
            " JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id"
            " LEFT JOIN LATERAL (SELECT * FROM trust_management_agreements AS m"
            "   WHERE m.ledger_account_id = a.ledger_account_id AND m.starts_on <= %s"
            "   ORDER BY m.starts_on DESC LIMIT 1) AS m ON true"
            " WHERE a.property_id = %s AND a.kind = 'owner_property'"
            ' ORDER BY o.display_name COLLATE "C", o.id',
            (day, day, property_id),
        ).fetchall()
    ]
    ownership: dict[UUID, Ownership] = {}
    for share, starts, by, account, owner, percent in conn.execute(
        "SELECT s.id, s.starts_on, st.display_name, l.ledger_account_id, o.display_name,"
        " l.percent FROM trust_ownership_shares AS s"
        " LEFT JOIN trust_staff AS st ON st.id = s.recorded_by"
        " JOIN trust_ownership_share_lines AS l ON l.share_id = s.id"
        " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = l.ledger_account_id"
        " JOIN trust_owners AS o ON o.id = a.owner_id"
        " WHERE s.pmc_id = %s AND s.property_id = %s"
        " ORDER BY s.starts_on DESC, s.created_at DESC, s.id,"
        ' l.percent DESC, o.display_name COLLATE "C", l.ledger_account_id',
        (session.pmc_id, property_id),
    ).fetchall():
        ownership.setdefault(
            share, Ownership(id=share, starts_on=starts, recorded_by=by, shares=[])
        ).shares.append(Share(account=account, owner=owner, percent=exports.text(percent)))
    fees = conn.execute(
        "SELECT f.starts_on, f.grace_days, f.flat_fee, f.percent, f.maximum, s.starts_on, s.fee,"
        " b.starts_on, b.flat_fee, b.rent_months, i.starts_on, i.annual_percent"
        " FROM (SELECT 1) AS one"
        " LEFT JOIN LATERAL (SELECT * FROM trust_late_fee_policies AS f"
        "   WHERE f.property_id = %(property)s AND f.starts_on <= %(day)s"
        "   ORDER BY f.starts_on DESC LIMIT 1) AS f ON true"
        " LEFT JOIN LATERAL (SELECT * FROM trust_nsf_fee_policies AS s"
        "   WHERE s.property_id = %(property)s AND s.starts_on <= %(day)s"
        "   ORDER BY s.starts_on DESC LIMIT 1) AS s ON true"
        " LEFT JOIN LATERAL (SELECT * FROM trust_lease_break_fees AS b"
        "   WHERE b.property_id = %(property)s AND b.starts_on <= %(day)s"
        "   ORDER BY b.starts_on DESC LIMIT 1) AS b ON true"
        " LEFT JOIN LATERAL (SELECT * FROM trust_deposit_interest_terms AS i"
        "   WHERE i.property_id = %(property)s AND i.starts_on <= %(day)s"
        "   ORDER BY i.starts_on DESC LIMIT 1) AS i ON true",
        {"property": property_id, "day": day},
    ).fetchone()
    assert fees is not None
    late_starts, grace, flat_fee, percent, maximum, nsf_starts, nsf = fees[:7]
    break_starts, break_flat, break_months, interest_starts, interest = fees[7:]
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
    rows = conn.execute(
        "SELECT u.id, u.display_name, trust_is_archived(NULL, u.id) FROM trust_units AS u"
        ' WHERE u.property_id = %s ORDER BY u.display_name COLLATE "C", u.id',
        (property_id,),
    ).fetchall()
    details = unit_details.details_of(conn, [u for u, _, _ in rows])
    units = [
        UnitLine(id=u, name=n, archived=a, details=details[u], lease=leases.get(u))
        for u, n, a in rows
    ]
    return Property(
        id=property_id,
        name=row[0],
        archived=row[1],
        address=addresses.address_of(conn, property_id),
        location=addresses.location_of(conn, property_id),
        can_locate=request.app.state.geocoder is not None,
        owners=owners,
        ownership=list(ownership.values()),
        units=units,
        late_fees=None
        if late_starts is None
        else setup.LateFees(
            starts_on=late_starts,
            grace_days=grace,
            flat_fee=exports.text(flat_fee),
            percent=exports.text(percent),
            maximum=setup.money(maximum),
        ),
        nsf_fee=None
        if nsf_starts is None
        else setup.NsfFee(starts_on=nsf_starts, fee=exports.text(nsf)),
        lease_break_fee=None
        if break_starts is None
        else setup.LeaseBreakFee(
            starts_on=break_starts,
            flat_fee=exports.text(break_flat),
            rent_months=exports.text(break_months),
        ),
        deposit_interest=None
        if interest_starts is None
        else setup.DepositInterestTerms(
            starts_on=interest_starts, annual_percent=exports.text(interest.normalize())
        ),
    )


class Rename(BaseModel):
    name: Name


class Archiving(BaseModel):
    archived: bool


def rename(conn: psycopg.Connection[Any], table: str, pmc: UUID, record: UUID, name: str) -> None:
    found = conn.execute(
        f"UPDATE {table} SET display_name = %s WHERE pmc_id = %s AND id = %s RETURNING id",  # noqa: S608
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
            f"SELECT 1 FROM {table} WHERE pmc_id = %s AND id = %s",  # noqa: S608
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
    # Its bedrooms, bathrooms, square feet and market rent (migration 0124).
    details: unit_details.UnitDetails
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
        details=unit_details.details_of(conn, [unit_id])[unit_id],
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
    # The unit's bedrooms, bathrooms, square feet and market rent (migration 0124).
    details: unit_details.UnitDetails
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


# What each lease of the PMC owes, worked out for all of them at once, as rent.OWED works it out
# for one: only for the leases that owe (show "owing"), otherwise no row at all.
OWING = """
SELECT c.lease_id,
       sum(CASE WHEN c.kind = 'credit' THEN -c.amount ELSE c.amount END)
       - coalesce(sum(m.paid), 0) + coalesce(sum(m.reversed), 0) AS owed
FROM trust_charges AS c
LEFT JOIN LATERAL (
    SELECT (SELECT sum(p.amount) FROM trust_charge_payments AS p WHERE p.charge_id = c.id) AS paid,
           (SELECT sum(r.amount) FROM trust_payment_reversals AS r WHERE r.charge_id = c.id)
               AS reversed
) AS m ON true
WHERE c.pmc_id = %(pmc)s AND %(show)s = 'owing'
GROUP BY c.lease_id
"""

# The rent roll's page first, by the cheap columns it is found and sorted by; then what each of
# its leases shows, worked out for that page alone (a deep page used to work out every lease
# before it: 27 s for page 200 of a company of 20,000 units).
RENT_ROLL = f"""
WITH owing AS ({OWING}),
paged AS (
    SELECT l.id, count(*) OVER () AS total
    FROM trust_leases AS l
    JOIN trust_units AS u ON u.id = l.unit_id
    JOIN trust_properties AS p ON p.id = u.property_id
    WHERE l.pmc_id = %(pmc)s
      AND {scope.within("p.id")}
      AND (%(like)s::text IS NULL OR p.display_name ILIKE %(like)s
           OR u.display_name ILIKE %(like)s
           OR EXISTS (SELECT 1 FROM trust_lease_tenants AS lt
                      JOIN trust_tenants AS t ON t.id = lt.tenant_id
                      WHERE lt.lease_id = l.id AND t.display_name ILIKE %(like)s))
      AND CASE %(show)s
            WHEN 'current' THEN l.starts_on <= %(day)s
                                AND (l.ends_on IS NULL OR %(day)s <= l.ends_on)
            WHEN 'future' THEN %(day)s < l.starts_on
            WHEN 'past' THEN l.ends_on < %(day)s
            WHEN 'owing' THEN EXISTS (SELECT 1 FROM owing AS o
                                      WHERE o.lease_id = l.id AND o.owed > 0)
            ELSE true
          END
    ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C", l.starts_on, l.id
    LIMIT %(per_page)s OFFSET %(offset)s
)
SELECT l.id, p.id, p.display_name, u.id, u.display_name, {TENANT_NAMES},
       l.starts_on, l.ends_on, trust_rent_on(l.id, greatest(l.starts_on, %(day)s)), {rent.OWED},
       coalesce((SELECT sum(b.balance) FROM trust_lease_tenants AS lt
                 JOIN trust_ledger_accounts AS a
                   ON a.tenant_id = lt.tenant_id AND a.kind = 'tenant_deposit'
                 JOIN pgledger_accounts AS b ON b.id = a.ledger_account_id
                 WHERE lt.lease_id = l.id), 0),
       {unit_details.COLUMNS}, paged.total
FROM paged
JOIN trust_leases AS l ON l.id = paged.id
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
{unit_details.LATEST}
ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C", l.starts_on, l.id
"""  # noqa: S608


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
                details=unit_details.shown(beds, half_baths, size, market),
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
            for (
                i,
                p,
                pn,
                u,
                un,
                tenants,
                ids,
                starts,
                ends,
                monthly,
                owed,
                deposit,
                beds,
                half_baths,
                size,
                market,
                _,
            ) in rows
        ],
        total=rows[0][-1] if rows else 0,
        page=page,
        per_page=per_page,
    )


# Whether a tenant (on t) can sign in to the portal: no login, invited (no password set yet),
# active, or disabled; and their login's id. A record at another property linked to their first
# (migration 0105) signs in through the first's login.
PORTAL = """
LEFT JOIN trust_logins AS g
  ON g.pmc_id = t.pmc_id AND g.tenant_id IN (SELECT trust_tenant_records(t.pmc_id, t.id))
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
    # The day they left the lease while others stayed on it, and the tenant who took over their
    # deposit then (None: it stayed under their name).
    left_on: date | None
    deposit_to: UUID | None


class Lease(rent.RentLease):
    property_id: UUID
    unit_id: UUID
    status: Literal["current", "future", "ended"]
    # Each tenant on it with their contact details and whether they can sign in to the portal.
    people: list[LeasePerson]
    # Its payment holds (an eviction filed), newest first.
    holds: list[rent.Hold]


LEASE_PEOPLE = f"""
SELECT t.id, t.display_name, c.email, c.phone, {PORTAL_STATUS}, g.email, d.left_on, d.deposit_to
FROM trust_lease_tenants AS lt
JOIN trust_tenants AS t ON t.id = lt.tenant_id
LEFT JOIN trust_lease_departures AS d ON d.lease_id = lt.lease_id AND d.tenant_id = lt.tenant_id
LEFT JOIN LATERAL (
    SELECT k.email, k.phone FROM trust_contacts AS k WHERE k.tenant_id = t.id
    ORDER BY k.created_at DESC, k.id DESC LIMIT 1
) AS c ON true
{PORTAL}
WHERE lt.lease_id = %s
ORDER BY t.display_name COLLATE "C", t.id
"""  # noqa: S608


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
    holds = rent.holds_of(conn, session.pmc_id, lease_id, day)
    people = [
        LeasePerson(
            id=i, name=n, email=e, phone=ph, portal=po, login_email=le, left_on=lo, deposit_to=to
        )
        for i, n, e, ph, po, le, lo, to in conn.execute(LEASE_PEOPLE, (lease_id,)).fetchall()
    ]
    return Lease(
        **detail.model_dump(),
        unit_id=row[0],
        property_id=row[1],
        status=status_of(row[2], row[3], day),
        people=people,
        holds=holds,
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
    kind: Literal[
        "property",
        "unit",
        "tenant",
        "owner",
        "vendor",
        "bill",
        "work order",
        "applicant",
        "lead",
        "file",
    ]
    id: UUID
    label: str
    detail: str
    href: str


SearchWords = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]

# A person's contact details that hold (the latest row), for finding them by email, phone or
# street; a phone matches by its digits, whatever its dashes and brackets.
CONTACT = """
LEFT JOIN LATERAL (
    SELECT k.email, k.phone, k.alternate_phone, k.address_line_1 FROM trust_contacts AS k
    WHERE k.{column} = {key} ORDER BY k.created_at DESC, k.id DESC LIMIT 1
) AS k ON true"""
CONTACT_MATCHES = """(k.email ILIKE %(like)s OR k.address_line_1 ILIKE %(like)s
     OR (%(digits)s::text IS NOT NULL AND (
         regexp_replace(k.phone, '[^0-9]', '', 'g') LIKE %(digits)s
         OR regexp_replace(k.alternate_phone, '[^0-9]', '', 'g') LIKE %(digits)s)))"""


def contact(column: str, key: str) -> str:
    """The person's contact details that hold, joined beside them (`k`)."""
    return CONTACT.format(column=column, key=key)  # noqa: S608 (a column and key of this module)


# Made of this module's constants only; the words searched are parameters.
SEARCH = "".join(
    (
        """
(SELECT 'property', p.id, p.display_name, coalesce(""",
        ADDRESS,
        """, ''), '/properties/' || p.id
 FROM trust_properties AS p
 WHERE p.pmc_id = %(pmc)s AND (p.display_name ILIKE %(like)s OR """,
        ADDRESS,
        """ ILIKE %(like)s)
   AND NOT trust_is_archived(p.id, NULL)
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
 LEFT JOIN trust_units AS u ON u.id = l.unit_id""",
        contact("tenant_id", "t.id"),
        """
 WHERE t.pmc_id = %(pmc)s AND (t.display_name ILIKE %(like)s OR """,
        CONTACT_MATCHES,
        """)
   AND NOT trust_is_archived(NULL, NULL, t.id)
 ORDER BY t.display_name COLLATE "C", t.id LIMIT 10)
UNION ALL
(SELECT 'owner', o.id, o.display_name, '', '/owners/' || o.id
 FROM trust_owners AS o""",
        contact("owner_id", "o.id"),
        """
 WHERE o.pmc_id = %(pmc)s AND (o.display_name ILIKE %(like)s OR """,
        CONTACT_MATCHES,
        """)
   AND NOT trust_is_archived(NULL, NULL, NULL, o.id)
 ORDER BY o.display_name COLLATE "C", o.id LIMIT 10)
UNION ALL
(SELECT 'vendor', v.id, v.display_name, coalesce(f.trade, ''), '/vendors/' || v.id
 FROM trust_vendors AS v
 LEFT JOIN LATERAL (
     SELECT x.trade FROM trust_vendor_profiles AS x WHERE x.vendor_id = v.id
     ORDER BY x.created_at DESC, x.id DESC LIMIT 1
 ) AS f ON true""",
        contact("vendor_id", "v.id"),
        """
 WHERE v.pmc_id = %(pmc)s AND (v.display_name ILIKE %(like)s OR f.trade ILIKE %(like)s OR """,
        CONTACT_MATCHES,
        """)
   AND NOT trust_is_archived(NULL, NULL, NULL, NULL, v.id)
 ORDER BY v.display_name COLLATE "C", v.id LIMIT 10)
UNION ALL
(SELECT 'bill', b.id, v.display_name || ' ' || b.reference,
        p.display_name || ' · ' || b.memo || ' · due ' || to_char(b.due_on, 'MM/DD/YYYY'),
        '/bills/' || b.id
 FROM trust_bills AS b JOIN trust_vendors AS v ON v.id = b.vendor_id
 JOIN trust_ledger_accounts AS a ON a.ledger_account_id = b.ledger_account_id
 JOIN trust_properties AS p ON p.id = a.property_id
 WHERE b.pmc_id = %(pmc)s AND (b.reference ILIKE %(like)s OR b.memo ILIKE %(like)s)
 ORDER BY b.due_on DESC, b.id LIMIT 10)
UNION ALL
(SELECT 'work order', w.id, w.summary,
        coalesce(u.display_name || ', ', '') || p.display_name, '/work-orders/' || w.id
 FROM trust_work_orders AS w JOIN trust_properties AS p ON p.id = w.property_id
 LEFT JOIN trust_units AS u ON u.id = w.unit_id
 WHERE w.pmc_id = %(pmc)s AND w.summary ILIKE %(like)s
 ORDER BY w.opened_at DESC, w.id LIMIT 10)
UNION ALL
(SELECT 'applicant', a.application_id, a.display_name,
        u.display_name || ', ' || p.display_name, '/applicants/' || a.application_id
 FROM trust_applicants AS a JOIN trust_applications AS x ON x.id = a.application_id
 JOIN trust_application_links AS k ON k.id = x.link_id
 JOIN trust_units AS u ON u.id = k.unit_id JOIN trust_properties AS p ON p.id = u.property_id
 WHERE a.pmc_id = %(pmc)s AND (a.display_name ILIKE %(like)s OR a.email ILIKE %(like)s)
 ORDER BY x.submitted_at DESC, a.id LIMIT 10)
UNION ALL
(SELECT 'lead', l.id, l.display_name, coalesce(u.display_name || ', ', '')
        || coalesce(p.display_name, 'any home'), '/leads/' || l.id
 FROM trust_leads AS l LEFT JOIN trust_properties AS p ON p.id = l.property_id
 LEFT JOIN trust_units AS u ON u.id = l.unit_id
 WHERE l.pmc_id = %(pmc)s AND (l.display_name ILIKE %(like)s OR l.email ILIKE %(like)s
     OR (%(digits)s::text IS NOT NULL
         AND regexp_replace(l.phone, '[^0-9]', '', 'g') LIKE %(digits)s))
 ORDER BY l.created_at DESC, l.id LIMIT 10)
UNION ALL
(SELECT 'file', d.id, d.title, to_char(d.created_at, 'MM/DD/YYYY'),
        CASE
          WHEN d.vendor_id IS NOT NULL THEN '/vendors/' || d.vendor_id
          WHEN d.bill_id IS NOT NULL THEN '/bills/' || d.bill_id
          WHEN d.lease_id IS NOT NULL THEN '/leases/' || d.lease_id
          WHEN d.owner_id IS NOT NULL THEN '/owners/' || d.owner_id
          WHEN d.work_order_id IS NOT NULL THEN '/work-orders/' || d.work_order_id
          WHEN d.tenant_id IS NOT NULL THEN '/tenants/' || d.tenant_id
          WHEN d.unit_id IS NOT NULL THEN '/units/' || d.unit_id
          WHEN d.property_id IS NOT NULL THEN '/properties/' || d.property_id
          ELSE '/files'
        END
 FROM trust_documents AS d
 WHERE d.pmc_id = %(pmc)s AND d.title ILIKE %(like)s
   AND d.kind NOT IN ('company_logo', 'income_proof')
   AND NOT EXISTS (SELECT 1 FROM trust_document_removals AS r WHERE r.document_id = d.id)
 ORDER BY d.created_at DESC, d.id LIMIT 10)
""",
    )
)


def digits(q: str) -> str | None:
    """A search that looks like part of a phone number (four digits or more) as a pattern for a
    phone's digits; otherwise None."""
    found = "".join(c for c in q if c.isdigit())
    return f"%{found}%" if len(found) >= 4 else None


@router.get("/search")
def search(session: Staff, conn: Conn, q: Annotated[SearchWords, Query()]) -> list[Found]:
    """Up to 10 of each kind that hold the words, each with the page it opens: properties, units,
    tenants, owners and vendors not archived (a property's says its address and is found by it
    too; a tenant's says their latest unit; vendors by name or trade, or by the email, phone or
    street on file); bills by reference or memo; work orders by what they
    are for; applicants and leads by name or email (a lead by phone too); and files by title,
    opening the page of what they are about."""
    return [
        Found(kind=k, id=i, label=label, detail=detail, href=href)
        for k, i, label, detail, href in conn.execute(
            SEARCH, {"pmc": session.pmc_id, "like": like(q), "digits": digits(q)}
        ).fetchall()
    ]
