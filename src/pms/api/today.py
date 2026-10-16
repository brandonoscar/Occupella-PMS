"""Staff's day at a glance (/today): late rent, bills due, months not reconciled, applications
waiting for a decision, lease offers waiting for signatures, journal entries waiting to be
posted, vendor insurance expired or running out, the company's earned fees still in trust, and
trust bank accounts with no broker on file or a signer's bond running out.

Read only. Each part comes from the route of the page that does that work (rent, bills,
month-end, leasing, the journal, vendors), so the two never disagree; this only picks out what needs
someone today.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from pms.api import journal, leasing, offers, payables, reconcile, rent, signers, vendors
from pms.api.deps import Conn, Staff

router = APIRouter(prefix="/api/today")

# A bill shows from a week before it is due.
BILLS_AHEAD = timedelta(days=7)


class LateLease(BaseModel):
    lease_id: UUID
    property: str
    unit: str
    tenants: list[str]
    rent_past_due: str  # rent still open of charges due before the day
    oldest_due_on: date


class Unreconciled(BaseModel):
    bank_account_id: UUID
    name: str
    closed_through: date | None
    months: list[str]  # each month ended and not reconciled, "YYYY-MM"
    # A month-end prepared through this day, waiting for someone who holds approve.
    waiting_for_approval: date | None


class Lapsing(BaseModel):
    vendor_id: UUID
    vendor: str
    coverage: str
    expires_on: date
    standing: str


class SignerNote(BaseModel):
    bank_account_id: UUID
    bank_account: str
    signer: str | None
    standing: str  # "no broker on file", "bond runs out in N days" or "bond ran out"


class Uninsured(BaseModel):
    lease_id: UUID
    property: str
    unit: str
    tenants: str
    # none on file, expired, expires today, expires in N days, or below the minimum.
    standing: str


class Today(BaseModel):
    on: date
    late_rent: list[LateLease]
    bills_due: list[payables.Bill]  # to pay (not paid or declined), due within a week or past
    unreconciled: list[Unreconciled]
    applications: list[leasing.ApplicationOut]  # not decided
    unsigned_offers: list[offers.OfferOut]  # sent to sign, not signed by everyone
    journal_waiting: list[journal.Entry]  # proposed, not yet posted or declined
    vendor_insurance: list[Lapsing]  # expired, or expiring within 30 days
    # The company's earned fees still in a trust bank account, to take out (commingling).
    company_fees: list[payables.CompanyFees]
    # Trust bank accounts whose broker isn't on file, and signers' bonds running out.
    signers: list[SignerNote]
    # Leases that require renters insurance with none current on file, or below the minimum.
    renters_insurance: list[Uninsured]


def months_ended(start: date, on: date) -> list[str]:
    """Each calendar month from start's through the last one ended before on's month."""
    found = []
    month = start.replace(day=1)
    while month < on.replace(day=1):
        found.append(month.strftime("%Y-%m"))
        month = (month + timedelta(days=32)).replace(day=1)
    return found


@router.get("")
def today(session: Staff, conn: Conn, on: Annotated[date | None, Query()] = None) -> Today:
    """What needs someone on the day (today, UTC, by default)."""
    day = on or rent.today()
    late = []
    for lease in rent.leases(session, conn, day):
        due = [c for c in lease.open_charges if c.kind == "rent" and c.due_on < day]
        if due:
            late.append(
                LateLease(
                    lease_id=lease.id,
                    property=lease.property,
                    unit=lease.unit,
                    tenants=[t.name for t in lease.tenants],
                    rent_past_due=rent.cents(sum((Decimal(c.open) for c in due), Decimal(0))),
                    oldest_due_on=min(c.due_on for c in due),
                )
            )
    unreconciled = []
    for bank in reconcile.books(session, conn):
        start = bank.closed_through + timedelta(days=1) if bank.closed_through else bank.next_start
        months = months_ended(start, day) if start else []
        if months or bank.prepared:
            unreconciled.append(
                Unreconciled(
                    bank_account_id=bank.id,
                    name=bank.name,
                    closed_through=bank.closed_through,
                    months=months,
                    waiting_for_approval=bank.prepared.through if bank.prepared else None,
                )
            )
    return Today(
        on=day,
        late_rent=late,
        bills_due=[
            b
            for b in payables.find_bills(conn, session.pmc_id, "open")[0]
            if b.due_on <= day + BILLS_AHEAD
        ],
        unreconciled=unreconciled,
        applications=leasing.applications(session, conn, "open"),
        unsigned_offers=[
            o
            for o in offers.offers(session, conn, "open")
            if o.sent and any(s.signed_at is None for s in o.signers)
        ],
        vendor_insurance=[
            Lapsing(
                vendor_id=v.id,
                vendor=v.name,
                coverage=p.coverage,
                expires_on=p.expires_on,
                standing=p.standing,
            )
            for v in vendors.coverage(conn, session.pmc_id, day)
            for p in v.insurance
            if p.standing != "current"
        ],
        journal_waiting=[
            e for e in journal.journal(session, conn).entries if e.status == "waiting"
        ],
        company_fees=[
            f for f in payables.held_fees(conn, session.pmc_id, day) if Decimal(f.held) > 0
        ],
        renters_insurance=[
            Uninsured(
                lease_id=lease,
                property=prop,
                unit=unit,
                tenants=tenants or "",
                standing="below the minimum" if below and standing == "current" else standing,
            )
            for lease, prop, unit, tenants, standing, below in conn.execute(
                "SELECT lease_id, property, unit, tenants, standing, below_minimum"
                " FROM trust_report_renters_insurance(%s, %s)"
                " WHERE required AND (standing <> 'current' OR below_minimum) ORDER BY line",
                (session.pmc_id, day),
            ).fetchall()
        ],
        signers=[
            note
            for account in signers.signers_on(conn, session.pmc_id, day)
            for note in (
                [
                    SignerNote(
                        bank_account_id=account.bank_account_id,
                        bank_account=account.bank_account,
                        signer=None,
                        standing="no broker on file",
                    )
                ]
                if not account.signers
                else [
                    SignerNote(
                        bank_account_id=account.bank_account_id,
                        bank_account=account.bank_account,
                        signer=s.name,
                        standing=s.standing,
                    )
                    for s in account.signers
                    if s.standing.startswith("bond ")
                ]
            )
        ],
    )
