"""Staff set up their PMC's books: trust bank accounts, owners, properties and the owner accounts
that hold each owner's money for a property, units, tenants, vendors, expense accounts (each on
its line of Schedule E), management agreements, late fee and NSF fee terms, and leases of
tenants already in place (a new tenant's lease comes through a lease offer, offers.py).

Every write is a plain insert the app role may make, or one of the database's own write paths
(trust_open_ledger_account, trust_open_lease), so the database's rules hold whoever calls: a
deposit account only in a security-deposit bank account, one lease per unit at a time, terms
in force from a day. Nothing here moves money.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from psycopg import sql
from pydantic import BaseModel, Field, StringConstraints

from pms import exports
from pms.api.deps import Conn, Staff, refusal

router = APIRouter(prefix="/api/setup")

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Money = Annotated[Decimal, Field(ge=0, le=Decimal("10000000"), decimal_places=2)]
Rent = Annotated[Decimal, Field(gt=0, le=Decimal("1000000"), decimal_places=2)]
Percent = Annotated[Decimal, Field(ge=0, le=100, decimal_places=4)]
BankKind = Literal["operating", "security_deposit"]

# Schedule E (Form 1040), Part I: the lines an expense account may be on, as
# trust_report_income_statement labels them (tests/api/test_expense_accounts.py reads both).
SCHEDULE_E = {
    5: "Advertising",
    6: "Auto and travel",
    7: "Cleaning and maintenance",
    8: "Commissions",
    9: "Insurance",
    10: "Legal and other professional fees",
    11: "Management fees",
    12: "Mortgage interest paid to banks, etc.",
    13: "Other interest",
    14: "Repairs",
    15: "Supplies",
    16: "Taxes",
    17: "Utilities",
    18: "Depreciation expense or depletion",
    19: "Other",
}

# The records a request may name, and the table each lives in.
TABLES = {
    "bank account": "trust_bank_accounts",
    "owner": "trust_owners",
    "property": "trust_properties",
    "unit": "trust_units",
    "tenant": "trust_tenants",
}


# --- what staff see -------------------------------------------------------------------------


class Named(BaseModel):
    id: UUID
    name: str


class BankAccount(Named):
    kind: BankKind
    opened_at: datetime | None
    closed_through: datetime | None


class Agreement(BaseModel):
    starts_on: date
    fee_percent: str
    minimum_fee: str
    flat_fee: str
    leasing_fee_percent: str
    reserve: str
    approval_limit: str
    # The percent of a bill the company may add as its markup (migration 0083); 0: none.
    bill_markup_percent: str = "0"


class OwnerAccount(BaseModel):
    """One owner's money for one property, in one operating trust bank account."""

    account: str
    owner_id: UUID
    owner: str
    property_id: UUID
    property: str
    bank_account: str
    # The agreement in force today, if any.
    agreement: Agreement | None
    # The owner's percent of the property in its ownership shares in force today (migration
    # 0094); None when the property has none, or the owner has no share in them.
    ownership_percent: str | None = None


class Lease(BaseModel):
    id: UUID
    starts_on: date
    ends_on: date | None
    monthly_rent: str
    tenants: list[str]


class Unit(Named):
    # Leases that haven't ended, oldest first.
    leases: list[Lease]


class LateFees(BaseModel):
    starts_on: date
    grace_days: int
    flat_fee: str
    percent: str
    maximum: str | None


class NsfFee(BaseModel):
    starts_on: date
    fee: str


class LeaseBreakFee(BaseModel):
    starts_on: date
    flat_fee: str
    # Months of the rent in force on the lease's new last day, to the cent.
    rent_months: str


class DepositInterestTerms(BaseModel):
    starts_on: date
    annual_percent: str  # the yearly rate on the property's deposits; "0" earns none


class Property(Named):
    units: list[Unit]
    # The late fee terms in force today, if any.
    late_fees: LateFees | None
    # The NSF fee terms in force today, if any.
    nsf_fee: NsfFee | None


class Tenant(Named):
    property_id: UUID
    property: str


class ExpenseAccount(Named):
    schedule_e_line: int


class ScheduleELine(BaseModel):
    line: int
    label: str


class Portfolio(BaseModel):
    bank_accounts: list[BankAccount]
    owners: list[Named]
    properties: list[Property]
    accounts: list[OwnerAccount]
    tenants: list[Tenant]
    vendors: list[Named]
    expense_accounts: list[ExpenseAccount]
    schedule_e_lines: list[ScheduleELine]


class Created(BaseModel):
    id: UUID | str


def named(conn: psycopg.Connection[Any], table: str, pmc_id: UUID) -> list[Named]:
    rows = conn.execute(
        sql.SQL(
            "SELECT id, display_name FROM {} WHERE pmc_id = %s"
            ' ORDER BY display_name COLLATE "C", id'
        ).format(sql.Identifier(table)),
        (pmc_id,),
    ).fetchall()
    return [Named(id=i, name=n) for i, n in rows]


def money(value: Decimal | None) -> str | None:
    return None if value is None else exports.text(value)


@router.get("")
def portfolio(session: Staff, conn: Conn) -> Portfolio:
    """Everything set up in the PMC, as of today (UTC)."""
    pmc = session.pmc_id
    banks = [
        BankAccount(id=i, name=n, kind=k, opened_at=o, closed_through=c)
        for i, n, k, o, c in conn.execute(
            "SELECT id, display_name, kind, opened_at, closed_through FROM trust_bank_accounts"
            ' WHERE pmc_id = %s ORDER BY display_name COLLATE "C", id',
            (pmc,),
        ).fetchall()
    ]
    leases: dict[UUID, list[Lease]] = {}
    for unit, i, starts, ends, rent, tenants in conn.execute(
        "SELECT l.unit_id, l.id, l.starts_on, l.ends_on, l.monthly_rent,"
        " array(SELECT t.display_name FROM trust_lease_tenants AS lt"
        "       JOIN trust_tenants AS t ON t.id = lt.tenant_id"
        '       WHERE lt.lease_id = l.id ORDER BY t.display_name COLLATE "C", t.id)'
        " FROM trust_leases AS l WHERE l.pmc_id = %s"
        "   AND (l.ends_on IS NULL OR l.ends_on >= (now() AT TIME ZONE 'UTC')::date)"
        " ORDER BY l.starts_on, l.id",
        (pmc,),
    ).fetchall():
        leases.setdefault(unit, []).append(
            Lease(
                id=i,
                starts_on=starts,
                ends_on=ends,
                monthly_rent=exports.text(rent),
                tenants=tenants,
            )
        )
    units: dict[UUID, list[Unit]] = {}
    for prop, i, n in conn.execute(
        "SELECT property_id, id, display_name FROM trust_units WHERE pmc_id = %s"
        ' ORDER BY display_name COLLATE "C", id',
        (pmc,),
    ).fetchall():
        units.setdefault(prop, []).append(Unit(id=i, name=n, leases=leases.get(i, [])))
    properties = [
        Property(
            id=i,
            name=n,
            units=units.get(i, []),
            late_fees=None
            if starts is None
            else LateFees(
                starts_on=starts,
                grace_days=grace,
                flat_fee=exports.text(flat),
                percent=exports.text(percent),
                maximum=money(maximum),
            ),
            nsf_fee=None
            if nsf_starts is None
            else NsfFee(starts_on=nsf_starts, fee=exports.text(nsf_fee)),
        )
        for i, n, starts, grace, flat, percent, maximum, nsf_starts, nsf_fee in conn.execute(
            "SELECT p.id, p.display_name, f.starts_on, f.grace_days, f.flat_fee, f.percent,"
            " f.maximum, s.starts_on, s.fee FROM trust_properties AS p"
            " LEFT JOIN LATERAL (SELECT * FROM trust_late_fee_policies AS f"
            "   WHERE f.property_id = p.id AND f.starts_on <= (now() AT TIME ZONE 'UTC')::date"
            "   ORDER BY f.starts_on DESC LIMIT 1) AS f ON true"
            " LEFT JOIN LATERAL (SELECT * FROM trust_nsf_fee_policies AS s"
            "   WHERE s.property_id = p.id AND s.starts_on <= (now() AT TIME ZONE 'UTC')::date"
            "   ORDER BY s.starts_on DESC LIMIT 1) AS s ON true"
            ' WHERE p.pmc_id = %s ORDER BY p.display_name COLLATE "C", p.id',
            (pmc,),
        ).fetchall()
    ]
    accounts = [
        OwnerAccount(
            account=account,
            owner_id=owner_id,
            owner=owner,
            property_id=property_id,
            property=prop,
            bank_account=bank,
            agreement=None
            if starts is None
            else Agreement(
                starts_on=starts,
                fee_percent=exports.text(fee),
                minimum_fee=exports.text(minimum),
                flat_fee=exports.text(flat),
                leasing_fee_percent=exports.text(leasing),
                reserve=exports.text(reserve),
                approval_limit=exports.text(limit),
                bill_markup_percent=exports.text(markup),
            ),
            ownership_percent=money(share),
        )
        for (
            account,
            owner_id,
            owner,
            property_id,
            prop,
            bank,
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
            "SELECT a.ledger_account_id, o.id, o.display_name, p.id, p.display_name,"
            " b.display_name, m.starts_on, m.fee_percent, m.minimum_fee, m.flat_fee,"
            " m.leasing_fee_percent, m.reserve, m.approval_limit, m.bill_markup_percent,"
            " (SELECT s.percent FROM trust_ownership_on(p.id, (now() AT TIME ZONE 'UTC')::date)"
            "  AS s WHERE s.ledger_account_id = a.ledger_account_id)"
            " FROM trust_ledger_accounts AS a"
            " JOIN trust_owners AS o ON o.id = a.owner_id"
            " JOIN trust_properties AS p ON p.id = a.property_id"
            " JOIN trust_bank_accounts AS b ON b.id = a.bank_account_id"
            " LEFT JOIN LATERAL (SELECT * FROM trust_management_agreements AS m"
            "   WHERE m.ledger_account_id = a.ledger_account_id"
            "     AND m.starts_on <= (now() AT TIME ZONE 'UTC')::date"
            "   ORDER BY m.starts_on DESC LIMIT 1) AS m ON true"
            " WHERE a.pmc_id = %s AND a.kind = 'owner_property'"
            ' ORDER BY o.display_name COLLATE "C", p.display_name COLLATE "C", a.ledger_account_id',
            (pmc,),
        ).fetchall()
    ]
    tenants = [
        Tenant(id=i, name=n, property_id=p, property=pn)
        for i, n, p, pn in conn.execute(
            "SELECT t.id, t.display_name, p.id, p.display_name FROM trust_tenants AS t"
            " JOIN trust_properties AS p ON p.id = t.property_id WHERE t.pmc_id = %s"
            ' ORDER BY t.display_name COLLATE "C", t.id',
            (pmc,),
        ).fetchall()
    ]
    return Portfolio(
        bank_accounts=banks,
        owners=named(conn, "trust_owners", pmc),
        properties=properties,
        accounts=accounts,
        tenants=tenants,
        # Vendors not archived: an archived one takes no new bill (migration 0110).
        vendors=[
            Named(id=i, name=n)
            for i, n in conn.execute(
                "SELECT id, display_name FROM trust_vendors WHERE pmc_id = %s"
                " AND NOT trust_is_archived(NULL, NULL, NULL, NULL, id)"
                ' ORDER BY display_name COLLATE "C", id',
                (pmc,),
            ).fetchall()
        ],
        expense_accounts=[
            ExpenseAccount(id=i, name=n, schedule_e_line=line)
            for i, n, line in conn.execute(
                "SELECT id, name, schedule_e_line FROM trust_expense_accounts WHERE pmc_id = %s"
                ' ORDER BY name COLLATE "C", id',
                (pmc,),
            ).fetchall()
        ],
        schedule_e_lines=[ScheduleELine(line=n, label=label) for n, label in SCHEDULE_E.items()],
    )


# --- adding to it ---------------------------------------------------------------------------


def found(conn: psycopg.Connection[Any], what: str, pmc_id: UUID, record: UUID) -> None:
    """404 unless the PMC has this record."""
    row = conn.execute(
        sql.SQL("SELECT 1 FROM {} WHERE pmc_id = %s AND id = %s").format(
            sql.Identifier(TABLES[what])
        ),
        (pmc_id, record),
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"no such {what}")


@contextmanager
def refusals(already: str) -> Iterator[None]:
    """The database's refusals as answers: `already` when the record exists, else its reason."""
    try:
        yield
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, already) from None
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, refusal(refused)) from None


def insert(conn: psycopg.Connection[Any], statement: str, params: tuple[Any, ...]) -> Any:
    row = conn.execute(statement, params).fetchone()
    assert row is not None
    return row[0]


class NewBankAccount(BaseModel):
    kind: BankKind
    name: Name


@router.post("/bank-accounts", status_code=201)
def add_bank_account(session: Staff, conn: Conn, new: NewBankAccount) -> Created:
    """A trust bank account, with the ledger account for its cash (and, in an operating one,
    for the fees the PMC earns there)."""
    bank = insert(
        conn,
        "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name) VALUES (%s, %s, %s)"
        " RETURNING id",
        (session.pmc_id, new.kind, new.name),
    )
    kinds = ["bank_cash"] + (["pmc_income"] if new.kind == "operating" else [])
    for kind in kinds:
        conn.execute("SELECT trust_open_ledger_account(%s, %s, %s)", (session.pmc_id, bank, kind))
    return Created(id=bank)


class NewName(BaseModel):
    name: Name


@router.post("/owners", status_code=201)
def add_owner(session: Staff, conn: Conn, new: NewName) -> Created:
    return Created(
        id=insert(
            conn,
            "INSERT INTO trust_owners (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
            (session.pmc_id, new.name),
        )
    )


class NewExpenseAccount(BaseModel):
    name: Name
    schedule_e_line: Annotated[int, Field(ge=5, le=19)]


@router.post("/expense-accounts", status_code=201)
def add_expense_account(session: Staff, conn: Conn, new: NewExpenseAccount) -> Created:
    """An account bills go in (Repairs, Landscaping, Water), on the line of Schedule E its
    expenses are reported on."""
    with refusals("the company has an expense account of that name already"):
        return Created(
            id=insert(
                conn,
                "INSERT INTO trust_expense_accounts (pmc_id, name, schedule_e_line)"
                " VALUES (%s, %s, %s) RETURNING id",
                (session.pmc_id, new.name, new.schedule_e_line),
            )
        )


# The income statement's own income lines: a charge type takes none of their names.
INCOME_LINES = ("rent", "recurring charges", "late fees", "nsf fees", "other charges")


@router.get("/charge-types")
def charge_types(session: Staff, conn: Conn) -> list[Named]:
    """The company's own kinds of fee (migration 0100), each on its own income statement line."""
    return [
        Named(id=i, name=n)
        for i, n in conn.execute(
            "SELECT id, name FROM trust_charge_types WHERE pmc_id = %s"
            ' ORDER BY name COLLATE "C", id',
            (session.pmc_id,),
        ).fetchall()
    ]


class NewChargeType(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


@router.post("/charge-types", status_code=201)
def add_charge_type(session: Staff, conn: Conn, new: NewChargeType) -> Created:
    """A kind of fee the company charges (Application fee, Key replacement, Pet rent), which the
    owners' income statements count on a line of its own."""
    if new.name.lower() in INCOME_LINES:
        raise HTTPException(422, f"{new.name} is a line of the income statement already")
    with refusals("the company has a charge type of that name already"):
        return Created(
            id=insert(
                conn,
                "INSERT INTO trust_charge_types (pmc_id, name) VALUES (%s, %s) RETURNING id",
                (session.pmc_id, new.name),
            )
        )


class NewProperty(BaseModel):
    name: Name
    owner_id: UUID
    bank_account_id: UUID


def open_owner_account(
    conn: psycopg.Connection[Any], pmc_id: UUID, owner: UUID, prop: UUID, bank: UUID
) -> str:
    found(conn, "owner", pmc_id, owner)
    found(conn, "bank account", pmc_id, bank)
    with refusals("this owner has an account for this property in that bank account already"):
        return str(
            insert(
                conn,
                "SELECT trust_open_ledger_account(%s, %s, 'owner_property', %s, %s)",
                (pmc_id, bank, owner, prop),
            )
        )


@router.post("/properties", status_code=201)
def add_property(session: Staff, conn: Conn, new: NewProperty) -> Created:
    """A property and its first owner's account for it, in an operating trust bank account, with
    the company's default late fee and NSF fee terms from today."""
    prop = insert(
        conn,
        "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
        (session.pmc_id, new.name),
    )
    open_owner_account(conn, session.pmc_id, new.owner_id, prop, new.bank_account_id)
    with_default_fees(conn, session.pmc_id, prop)
    return Created(id=prop)


def with_default_fees(conn: psycopg.Connection[Any], pmc_id: UUID, prop: UUID) -> None:
    """A new property's late fee and NSF fee terms from its first day (today, UTC): the
    company's defaults as they stand (src/pms/api/company.py), if it has any."""
    defaults = conn.execute(
        "SELECT late_grace_days, late_flat_fee, late_percent, late_maximum, nsf_fee"
        " FROM trust_company_profiles WHERE pmc_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
        (pmc_id,),
    ).fetchone()
    if defaults is None:
        return
    grace, flat, percent, maximum, nsf = defaults
    if grace is not None:
        conn.execute(
            "INSERT INTO trust_late_fee_policies (pmc_id, property_id, starts_on, grace_days,"
            " flat_fee, percent, maximum)"
            " VALUES (%s, %s, (now() AT TIME ZONE 'UTC')::date, %s, %s, %s, %s)",
            (pmc_id, prop, grace, flat, percent, maximum),
        )
    if nsf is not None:
        conn.execute(
            "INSERT INTO trust_nsf_fee_policies (pmc_id, property_id, starts_on, fee)"
            " VALUES (%s, %s, (now() AT TIME ZONE 'UTC')::date, %s)",
            (pmc_id, prop, nsf),
        )


class NewOwnerAccount(BaseModel):
    owner_id: UUID
    bank_account_id: UUID


@router.post("/properties/{property_id}/owners", status_code=201)
def add_property_owner(
    session: Staff, conn: Conn, property_id: UUID, new: NewOwnerAccount
) -> Created:
    """Another owner's account for a property (an owner of part of it)."""
    found(conn, "property", session.pmc_id, property_id)
    return Created(
        id=open_owner_account(conn, session.pmc_id, new.owner_id, property_id, new.bank_account_id)
    )


# --- ownership shares (migration 0094) ------------------------------------------------------

Share = Annotated[Decimal, Field(gt=0, le=100, max_digits=7, decimal_places=4)]


class OwnerShare(BaseModel):
    account: Annotated[str, Field(min_length=1, max_length=100)]
    percent: Share


class NewOwnership(BaseModel):
    starts_on: date
    # Each owner's percent, by their owner's property account at the property: together 100.
    shares: Annotated[list[OwnerShare], Field(min_length=1, max_length=20)]


@router.post("/properties/{property_id}/ownership", status_code=201)
def set_ownership(session: Staff, conn: Conn, property_id: UUID, new: NewOwnership) -> Created:
    """A property's ownership from a day (q044): each owner's percent of it, by their owner's
    property account there, together 100. From that day its payments, prepaid rent used,
    deposits kept and the bills split by ownership go to its owners by these shares."""
    found(conn, "property", session.pmc_id, property_id)
    accounts = [share.account for share in new.shares]
    if len(set(accounts)) != len(accounts):
        raise HTTPException(422, "each owner's account is in the shares once")
    total = sum((share.percent for share in new.shares), Decimal(0))
    if total != 100:
        raise HTTPException(422, f"the shares come to {exports.text(total)}%, not 100%")
    try:
        with conn.transaction():  # a savepoint: the check at commit runs here too
            made = insert(
                conn,
                "INSERT INTO trust_ownership_shares (pmc_id, property_id, starts_on)"
                " VALUES (%s, %s, %s) RETURNING id",
                (session.pmc_id, property_id, new.starts_on),
            )
            for share in new.shares:
                conn.execute(
                    "INSERT INTO trust_ownership_share_lines"
                    " (share_id, ledger_account_id, pmc_id, percent) VALUES (%s, %s, %s, %s)",
                    (made, share.account, session.pmc_id, share.percent.normalize()),
                )
            conn.execute("SET CONSTRAINTS trust_ownership_shares_whole IMMEDIATE")
    except (psycopg.errors.CheckViolation, psycopg.errors.ForeignKeyViolation) as refused:
        # an account not at the property, or not in the one trust bank account
        raise HTTPException(422, refusal(refused)) from None
    return Created(id=made)


class NewUnit(BaseModel):
    property_id: UUID
    name: Name


@router.post("/units", status_code=201)
def add_unit(session: Staff, conn: Conn, new: NewUnit) -> Created:
    found(conn, "property", session.pmc_id, new.property_id)
    return Created(
        id=insert(
            conn,
            "INSERT INTO trust_units (pmc_id, property_id, display_name) VALUES (%s, %s, %s)"
            " RETURNING id",
            (session.pmc_id, new.property_id, new.name),
        )
    )


@router.post("/tenants", status_code=201)
def add_tenant(session: Staff, conn: Conn, new: NewUnit) -> Created:
    """A tenant of a property, to put on a lease that is already in place."""
    found(conn, "property", session.pmc_id, new.property_id)
    return Created(
        id=insert(
            conn,
            "INSERT INTO trust_tenants (pmc_id, property_id, display_name) VALUES (%s, %s, %s)"
            " RETURNING id",
            (session.pmc_id, new.property_id, new.name),
        )
    )


class NewAgreement(BaseModel):
    account: Annotated[str, Field(min_length=1, max_length=100)]
    starts_on: date
    fee_percent: Percent = Decimal(0)
    minimum_fee: Money = Decimal(0)
    flat_fee: Money = Decimal(0)
    leasing_fee_percent: Percent = Decimal(0)
    reserve: Money = Decimal(0)
    approval_limit: Money = Decimal(0)
    bill_markup_percent: Percent = Decimal(0)
    # The same terms for every owner's account at the account's property (q044): a property owned
    # in shares takes one management fee, under the terms its owners share, split by the shares.
    every_owner: bool = False


@router.post("/agreements", status_code=201)
def add_agreement(session: Staff, conn: Conn, new: NewAgreement) -> Created:
    """Management terms for an owner's property account, in force from a day, or for every
    owner's account at its property. New terms are a new agreement from a later day; earlier ones
    stay as they were. The id is the named account's agreement."""
    row = conn.execute(
        "SELECT property_id FROM trust_ledger_accounts WHERE pmc_id = %s"
        " AND ledger_account_id = %s AND kind = 'owner_property'",
        (session.pmc_id, new.account),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such owner account")
    accounts = [new.account]
    if new.every_owner:
        accounts += [
            other
            for (other,) in conn.execute(
                "SELECT ledger_account_id FROM trust_ledger_accounts WHERE pmc_id = %s"
                " AND property_id = %s AND kind = 'owner_property' AND ledger_account_id <> %s"
                " ORDER BY ledger_account_id",
                (session.pmc_id, row[0], new.account),
            ).fetchall()
        ]
    made = []
    with refusals(
        "an owner's account at this property has terms starting that day already"
        if new.every_owner
        else "this account has terms starting that day already"
    ):
        for account in accounts:
            made.append(
                insert(
                    conn,
                    "INSERT INTO trust_management_agreements (pmc_id, ledger_account_id,"
                    " starts_on, fee_percent, minimum_fee, flat_fee, leasing_fee_percent,"
                    " reserve, approval_limit, bill_markup_percent)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (
                        session.pmc_id,
                        account,
                        new.starts_on,
                        new.fee_percent,
                        new.minimum_fee,
                        new.flat_fee,
                        new.leasing_fee_percent,
                        new.reserve,
                        new.approval_limit,
                        new.bill_markup_percent,
                    ),
                )
            )
    return Created(id=made[0])


class NewLateFees(BaseModel):
    property_id: UUID
    starts_on: date
    grace_days: Annotated[int, Field(ge=0, le=365)]
    flat_fee: Money = Decimal(0)
    percent: Percent = Decimal(0)
    maximum: Money | None = None


@router.post("/late-fees", status_code=201)
def add_late_fees(session: Staff, conn: Conn, new: NewLateFees) -> Created:
    """A property's late fee terms, in force from a day."""
    found(conn, "property", session.pmc_id, new.property_id)
    with refusals("this property has late fee terms starting that day already"):
        return Created(
            id=insert(
                conn,
                "INSERT INTO trust_late_fee_policies (pmc_id, property_id, starts_on,"
                " grace_days, flat_fee, percent, maximum) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " RETURNING id",
                (
                    session.pmc_id,
                    new.property_id,
                    new.starts_on,
                    new.grace_days,
                    new.flat_fee,
                    new.percent,
                    new.maximum,
                ),
            )
        )


class NewNsfFee(BaseModel):
    property_id: UUID
    starts_on: date
    fee: Money  # 0: none from that day


@router.post("/nsf-fees", status_code=201)
def add_nsf_fee(session: Staff, conn: Conn, new: NewNsfFee) -> Created:
    """A property's NSF fee, charged for a payment the bank returns, in force from a day."""
    found(conn, "property", session.pmc_id, new.property_id)
    with refusals("this property has an NSF fee starting that day already"):
        return Created(
            id=insert(
                conn,
                "INSERT INTO trust_nsf_fee_policies (pmc_id, property_id, starts_on, fee)"
                " VALUES (%s, %s, %s, %s) RETURNING id",
                (session.pmc_id, new.property_id, new.starts_on, new.fee),
            )
        )


class NewLeaseBreakFee(BaseModel):
    property_id: UUID
    starts_on: date
    flat_fee: Money = Decimal(0)
    rent_months: Annotated[Decimal, Field(ge=0, le=12, decimal_places=2)] = Decimal(0)


@router.post("/lease-break-fees", status_code=201)
def add_lease_break_fee(session: Staff, conn: Conn, new: NewLeaseBreakFee) -> Created:
    """A property's lease-break fee terms from a day: a flat fee and months of rent. A lease
    breaks under the terms in force on its first day, and only when staff charge it as it ends
    early; both 0 charge none for leases starting from that day."""
    found(conn, "property", session.pmc_id, new.property_id)
    with refusals("this property has lease-break fee terms starting that day already"):
        return Created(
            id=insert(
                conn,
                "INSERT INTO trust_lease_break_fees"
                " (pmc_id, property_id, starts_on, flat_fee, rent_months)"
                " VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (session.pmc_id, new.property_id, new.starts_on, new.flat_fee, new.rent_months),
            )
        )


class NewDepositInterest(BaseModel):
    property_id: UUID
    starts_on: date
    # As a percent a year, up to four decimals: 0 earns none from that day (migration 0122).
    annual_percent: Annotated[Decimal, Field(ge=0, le=25, decimal_places=4)]


@router.post("/deposit-interest", status_code=201)
def add_deposit_interest(session: Staff, conn: Conn, new: NewDepositInterest) -> Created:
    """The yearly rate a property's security deposits earn from a day, as the law the company
    follows sets it (the PMS builds in no state's rate): each day's deposit earns the day's rate
    over the days in its year, and staff pay it from the lease's page."""
    found(conn, "property", session.pmc_id, new.property_id)
    with refusals("this property's deposit interest changes on that day already"):
        return Created(
            id=insert(
                conn,
                "INSERT INTO trust_deposit_interest_terms"
                " (pmc_id, property_id, starts_on, annual_percent) VALUES (%s, %s, %s, %s)"
                " RETURNING id",
                (session.pmc_id, new.property_id, new.starts_on, new.annual_percent),
            )
        )


class NewLease(BaseModel):
    unit_id: UUID
    starts_on: date
    ends_on: date | None = None
    monthly_rent: Rent
    tenant_ids: list[UUID] = Field(min_length=1, max_length=6)
    # The day of the month its rent is due, from its first month (migration 0081).
    rent_due_day: Annotated[int, Field(ge=1, le=28)] = 1


@router.post("/leases", status_code=201)
def add_lease(session: Staff, conn: Conn, new: NewLease) -> Created:
    """A lease already in place, for tenants of the unit's property (trust_open_lease)."""
    found(conn, "unit", session.pmc_id, new.unit_id)
    if new.ends_on is not None and new.ends_on < new.starts_on:
        raise HTTPException(422, "a lease ends on or after the day it starts")
    try:
        lease = insert(
            conn,
            "SELECT trust_open_lease(%s, %s, %s, %s, %s, %s::uuid[])",
            (
                session.pmc_id,
                new.unit_id,
                new.starts_on,
                new.ends_on,
                new.monthly_rent,
                new.tenant_ids,
            ),
        )
    except psycopg.errors.ExclusionViolation as refused:  # another lease of the unit
        raise HTTPException(409, refusal(refused)) from None
    except psycopg.errors.InvalidParameterValue as refused:  # a tenant of another property
        raise HTTPException(422, refusal(refused)) from None
    if new.rent_due_day != 1:
        conn.execute(
            "INSERT INTO trust_rent_due_days (pmc_id, lease_id, starts_on, due_day)"
            " VALUES (%s, %s, %s, %s)",
            (session.pmc_id, lease, new.starts_on.replace(day=1), new.rent_due_day),
        )
    return Created(id=lease)
