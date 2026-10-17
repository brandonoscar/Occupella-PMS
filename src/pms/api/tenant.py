"""The tenant portal's endpoints: a tenant's leases with what they owe and what comes due, each
lease's ledger, the statement of a deposit returned at move-out, and maintenance requests. No
payments: online payment is out of scope (docs/adr/0002-portals-and-exports.md).

The tenant and the PMC always come from the session. A lease named in the path is answered only
if the tenant is on it; any other reads as not found, whoever's it is.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from pms import accounts, exports
from pms.api import maintenance
from pms.api import rent as rent_api
from pms.api.deps import Conn, Rows, Tenant, download

router = APIRouter(prefix="/api/tenant")
# A tenant files at most this many requests under one lease in a day: enough for a bad day,
# and a ceiling on what a stolen session can pile on staff.
REQUESTS_PER_DAY = 10


def today() -> date:
    return datetime.now(UTC).date()


class Monthly(BaseModel):
    """What the lease charges every month: its rent, and each recurring fee or credit not
    stopped (from its first due date, until the first one it is no longer charged for)."""

    what: str
    kind: Literal["rent", "fee", "credit"]
    amount: str
    starts_on: date
    ends_on: date | None


class ComingDue(BaseModel):
    """A charge entered already and due after today, with what is still open of it."""

    due_on: date
    kind: Literal["rent", "fee"]
    memo: str | None
    open: str


class RentChangeNotice(BaseModel):
    effective_on: date
    monthly_rent: str
    notice_given_on: date


class Lease(BaseModel):
    lease_id: UUID
    property: str
    unit: str
    starts_on: date
    ends_on: date | None
    # Money as text, so no float ever rounds a cent. The rent in force today.
    monthly_rent: str
    # Rent changes to come, each from the first of a month, with the day notice was given.
    rent_changes: list[RentChangeNotice]
    tenants: list[str]
    balance_due: str
    in_effect: bool
    # Nothing for a lease that has ended.
    each_month: list[Monthly]
    coming_due: list[ComingDue]
    # Its tenants' deposits were returned at move-out: the statement can be downloaded.
    deposit_returned: bool
    # A lease in effect: whether it requires renters insurance, and the policy on file.
    renters_insurance: rent_api.RentersInsurance | None
    # Each payment to it, newest first, each with its receipt to download.
    payments: list[rent_api.Paid]


class NewRequest(BaseModel):
    lease_id: UUID
    summary: str = Field(min_length=1, max_length=2000)
    entry_allowed: bool


def lease_of(conn: psycopg.Connection[Any], session: accounts.Session, lease_id: UUID) -> None:
    """Refuses, as not found, a lease the tenant isn't on."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_lease_tenants"
            " WHERE pmc_id = %s AND lease_id = %s AND tenant_id = %s",
            (session.pmc_id, lease_id, session.person_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such lease of yours")


@router.get("/leases")
def leases(session: Tenant, conn: Conn) -> list[Lease]:
    """Every lease the tenant is on, newest first, with its balance due at the end of today."""
    on = today()
    rows = conn.execute(
        "SELECT l.id, p.display_name, u.display_name, l.starts_on, l.ends_on,"
        " trust_rent_on(l.id, %s),"
        " array(SELECT t.display_name FROM trust_lease_tenants AS everyone"
        "       JOIN trust_tenants AS t ON t.id = everyone.tenant_id"
        '       WHERE everyone.lease_id = l.id ORDER BY t.display_name COLLATE "C", t.id),'
        " (SELECT b.balance FROM trust_report_tenant_ledger(l.pmc_id, l.id, %s) AS b"
        "  WHERE b.item = 'balance'),"
        " EXISTS (SELECT 1 FROM trust_deposit_returns AS r WHERE r.lease_id = l.id)"
        " FROM trust_lease_tenants AS mine"
        " JOIN trust_leases AS l ON l.id = mine.lease_id"
        " JOIN trust_units AS u ON u.id = l.unit_id"
        " JOIN trust_properties AS p ON p.id = u.property_id"
        " WHERE mine.pmc_id = %s AND mine.tenant_id = %s"
        " ORDER BY l.starts_on DESC, l.id",
        (on, on, session.pmc_id, session.person_id),
    ).fetchall()
    insured = rent_api.renters_insurance(conn, session.pmc_id, on)
    paid: dict[UUID, list[rent_api.Paid]] = {}
    for lease, transfer, event_at, amount, reference, method, bounced in (
        found
        for row in rows
        for found in conn.execute(
            rent_api.PAYMENTS, {"pmc": session.pmc_id, "lease": row[0]}
        ).fetchall()
    ):
        paid.setdefault(lease, []).append(
            rent_api.Paid(
                transfer_id=transfer,
                received_on=event_at.astimezone(UTC).date(),
                amount=rent_api.cents(amount),
                reference=reference,
                method=method,
                bounced=bounced,
            )
        )
    coming: dict[UUID, list[RentChangeNotice]] = {}
    for lease, effective, monthly_rent, notice in conn.execute(
        "SELECT c.lease_id, c.effective_on, c.monthly_rent, c.notice_given_on"
        " FROM trust_rent_changes AS c"
        " JOIN trust_lease_tenants AS mine ON mine.lease_id = c.lease_id"
        " WHERE mine.pmc_id = %s AND mine.tenant_id = %s AND c.effective_on > %s"
        " ORDER BY c.effective_on",
        (session.pmc_id, session.person_id, on),
    ).fetchall():
        coming.setdefault(lease, []).append(
            RentChangeNotice(
                effective_on=effective,
                monthly_rent=exports.text(monthly_rent),
                notice_given_on=notice,
            )
        )
    return [
        Lease(
            lease_id=lease,
            property=prop,
            unit=unit,
            starts_on=starts,
            ends_on=ends,
            monthly_rent=exports.text(rent),
            rent_changes=coming.get(lease, []),
            tenants=list(names),
            balance_due=exports.text(balance),
            in_effect=starts <= on and (ends is None or on <= ends),
            each_month=[] if ends is not None and ends < on else monthly(conn, lease, starts, rent),
            coming_due=[
                ComingDue(due_on=due, kind=kind, memo=memo, open=rent_api.cents(left))
                for _, _, due, kind, memo, _, left in rent_api.charges_open(
                    conn, session.pmc_id, lease
                )
                if due > on
            ],
            deposit_returned=returned,
            renters_insurance=insured.get(lease),
            payments=paid.get(lease, []),
        )
        for lease, prop, unit, starts, ends, rent, names, balance, returned in rows
    ]


def monthly(conn: psycopg.Connection[Any], lease: UUID, starts: date, rent: Any) -> list[Monthly]:
    """The lease's rent, then each recurring fee or credit not stopped, by first due date."""
    return [
        Monthly(
            what="Rent", kind="rent", amount=rent_api.cents(rent), starts_on=starts, ends_on=None
        )
    ] + [
        Monthly(what=memo, kind=kind, amount=rent_api.cents(amount), starts_on=first, ends_on=last)
        for memo, kind, amount, first, last in conn.execute(
            "SELECT r.memo, r.kind, r.amount, r.starts_on, e.ends_on"
            " FROM trust_recurring_charges AS r"
            " LEFT JOIN trust_recurring_charge_ends AS e ON e.recurring_charge_id = r.id"
            " WHERE r.lease_id = %s AND (e.ends_on IS NULL OR e.ends_on > %s)"
            ' ORDER BY r.starts_on, r.memo COLLATE "C", r.id',
            (lease, today()),
        ).fetchall()
    ]


def ledger_table(
    conn: psycopg.Connection[Any], session: accounts.Session, lease_id: UUID, as_of: date | None
) -> exports.Table:
    lease_of(conn, session, lease_id)
    through = as_of or today()
    table = exports.run_report(
        conn, "trust_report_tenant_ledger", [session.pmc_id, lease_id, through]
    )
    return exports.Table(f"Tenant ledger through {through.isoformat()}", table.columns, table.rows)


@router.get("/leases/{lease_id}/ledger")
def ledger(
    session: Tenant,
    conn: Conn,
    lease_id: UUID,
    as_of: Annotated[date | None, Query()] = None,
) -> Rows:
    """The lease's charges and payments through the end of a day (UTC), today by default."""
    return Rows.of(ledger_table(conn, session, lease_id, as_of))


@router.get("/leases/{lease_id}/ledger/download")
def ledger_download(
    session: Tenant,
    conn: Conn,
    lease_id: UUID,
    as_of: Annotated[date | None, Query()] = None,
    fmt: Annotated[Literal["csv", "xlsx", "pdf"], Query(alias="format")] = "pdf",
) -> Response:
    table = ledger_table(conn, session, lease_id, as_of)
    name = f"tenant-ledger-{(as_of or today()).isoformat()}"
    return download(exports.export(table, fmt), name, fmt)


@router.get("/leases/{lease_id}/deposit-return/download")
def deposit_return_download(
    session: Tenant,
    conn: Conn,
    lease_id: UUID,
    fmt: Annotated[Literal["csv", "xlsx", "pdf"], Query(alias="format")] = "pdf",
) -> Response:
    """The itemized statement of the lease's deposits returned at move-out: what each tenant
    held, what was kept for what the lease owed, and what went back to each."""
    lease_of(conn, session, lease_id)
    try:
        with conn.transaction():
            table = exports.run_report(
                conn, "trust_report_deposit_return", [session.pmc_id, lease_id]
            )
    except psycopg.errors.InvalidParameterValue:
        raise HTTPException(404, "this lease's deposit has not been returned") from None
    return download(exports.export(table, fmt), "deposit-return", fmt)


@router.get("/requests")
def requests(session: Tenant, conn: Conn) -> list[maintenance.Request]:
    """The maintenance requests filed under any of the tenant's leases, by them or a co-tenant."""
    found = maintenance.requests(
        conn, maintenance.FOR_TENANT, pmc=session.pmc_id, person=session.person_id
    )
    return [maintenance.for_tenant(r) for r in found]


@router.post("/leases/{lease_id}/renters-insurance", status_code=201)
def record_my_policy(
    session: Tenant, conn: Conn, lease_id: UUID, policy: rent_api.NewPolicy
) -> rent_api.Created:
    """The tenant's own renters insurance policy for a lease they are on, with whatever insurer
    they chose; it is recorded as theirs."""
    lease_of(conn, session, lease_id)
    return rent_api.add_policy(conn, session.pmc_id, lease_id, policy, session.person_id)


@router.post("/requests", status_code=201)
def file_request(session: Tenant, conn: Conn, new: NewRequest) -> maintenance.Request:
    """A request under one of the tenant's leases that is in effect today."""
    summary = new.summary.strip()
    if not summary:
        raise HTTPException(422, "say what needs fixing")
    lease_of(conn, session, new.lease_id)
    on = today()
    row = conn.execute(
        "SELECT starts_on <= %s AND (ends_on IS NULL OR %s <= ends_on) FROM trust_leases"
        " WHERE id = %s",
        (on, on, new.lease_id),
    ).fetchone()
    if row is None or not row[0]:
        raise HTTPException(409, "requests are filed under a lease in effect today")
    recent = conn.execute(
        "SELECT count(*) FROM trust_maintenance_requests"
        " WHERE pmc_id = %s AND lease_id = %s AND submitted_at > %s",
        (session.pmc_id, new.lease_id, datetime.now(UTC) - timedelta(days=1)),
    ).fetchone()
    if recent is not None and recent[0] >= REQUESTS_PER_DAY:
        raise HTTPException(
            429, f"at most {REQUESTS_PER_DAY} requests a day per lease; call the office"
        )
    created = conn.execute(
        "INSERT INTO trust_maintenance_requests"
        " (pmc_id, lease_id, tenant_id, summary, entry_allowed)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (session.pmc_id, new.lease_id, session.person_id, summary, new.entry_allowed),
    ).fetchone()
    assert created is not None
    return maintenance.for_tenant(maintenance.one(conn, session.pmc_id, created[0]))
