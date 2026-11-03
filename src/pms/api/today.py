"""Staff's day at a glance (/today): late rent, bills due, months not reconciled, applications
waiting for a decision, lease offers waiting for signatures, journal entries waiting to be
posted, vendor insurance expired or running out, the company's earned fees still in trust,
trust bank accounts with no broker on file or a signer's bond running out, security deposits a
signed lease asked that are not all held yet, the owner draws prepared on the draw day, and the
fixed-term leases gone month to month with no renewal signed.

Read only. Each part comes from the route of the page that does that work (rent, bills,
month-end, leasing, the journal, vendors), so the two never disagree; this only picks out what needs
someone today.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Query
from pydantic import BaseModel

from pms.api import (
    journal,
    leasing,
    moveout,
    offers,
    payables,
    payouts,
    reconcile,
    rent,
    signers,
    vendors,
)
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
    # That preparation, for Approve on the Dashboard (another staff member who holds approve).
    prepared: reconcile.Prepared | None = None


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


class DepositDue(BaseModel):
    lease_id: UUID
    property: str
    unit: str
    tenants: list[str]
    asked: str  # what the signed lease offer asked
    to_receive: str  # what is not held yet


class Holdover(BaseModel):
    lease_id: UUID
    property: str
    unit: str
    tenants: list[str]
    term_ended_on: date
    rolled_on: date


HOLDOVERS = """
SELECT h.lease_id, p.display_name, u.display_name,
       ARRAY(SELECT t.display_name FROM trust_lease_tenants AS lt
             JOIN trust_tenants AS t ON t.id = lt.tenant_id
             WHERE lt.lease_id = h.lease_id ORDER BY t.display_name COLLATE "C", t.id),
       h.term_ended_on, h.rolled_on
FROM trust_lease_holdovers AS h
JOIN trust_leases AS l ON l.id = h.lease_id
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
WHERE h.pmc_id = %(pmc)s AND h.rolled_on > %(day)s::date - 30 AND h.rolled_on <= %(day)s
  AND l.ends_on IS NULL
ORDER BY h.rolled_on DESC, p.display_name COLLATE "C", u.display_name COLLATE "C"
"""


def holdovers(conn: psycopg.Connection[Any], pmc_id: UUID, day: date) -> list[Holdover]:
    """The leases gone month to month in the 30 days to the day, still month to month."""
    return [
        Holdover(
            lease_id=row[0],
            property=row[1],
            unit=row[2],
            tenants=list(row[3]),
            term_ended_on=row[4],
            rolled_on=row[5],
        )
        for row in conn.execute(HOLDOVERS, {"pmc": pmc_id, "day": day}).fetchall()
    ]


class Today(BaseModel):
    on: date
    late_rent: list[LateLease]
    bills_due: list[payables.Bill]  # to pay (not paid or declined), due within a week or past
    unreconciled: list[Unreconciled]
    applications: list[leasing.ApplicationOut]  # new or being screened: not decided, nor withdrawn
    unsigned_offers: list[offers.OfferOut]  # sent to sign, not signed by everyone
    journal_waiting: list[journal.Entry]  # proposed, not yet posted or declined
    vendor_insurance: list[Lapsing]  # expired, or expiring within 30 days
    # The company's earned fees still in a trust bank account, to take out (commingling).
    company_fees: list[payables.CompanyFees]
    # Trust bank accounts whose broker isn't on file, and signers' bonds running out.
    signers: list[SignerNote]
    # Leases that require renters insurance with none current on file, or below the minimum.
    renters_insurance: list[Uninsured]
    # Leases not ended whose signed offer asked a security deposit not all held yet.
    deposits_to_receive: list[DepositDue]
    # Leases past their last day whose deposits are still held, with the day they are due back
    # by the strictest deadline found (migration 0120, docs/law.md).
    deposits_to_return: list[moveout.DueBack]
    # The owner draws the nightly run prepared on the draw day, waiting for staff to pay them
    # (migration 0153).
    owner_draws: payouts.DrawRun | None = None
    # Fixed-term leases that went month to month in the last 30 days, their term run out with no
    # renewal signed (migration 0154).
    month_to_month: list[Holdover] = []


# The leases named as the rent page names them: property, unit and every tenant on it.
LATE = """
SELECT l.id, p.display_name, u.display_name,
       ARRAY(SELECT t.display_name FROM trust_lease_tenants AS lt
             JOIN trust_tenants AS t ON t.id = lt.tenant_id
             WHERE lt.lease_id = l.id ORDER BY t.display_name COLLATE "C", t.id)
FROM trust_leases AS l
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
WHERE l.pmc_id = %s AND l.id = ANY(%s)
ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C", l.starts_on, l.id
"""


def months_ended(start: date, on: date) -> list[str]:
    """Each calendar month from start's through the last one ended before on's month."""
    found = []
    month = start.replace(day=1)
    while month < on.replace(day=1):
        found.append(month.strftime("%Y-%m"))
        month = (month + timedelta(days=32)).replace(day=1)
    return found


# How many of a company's rents due before a day, and which leases have one that their own
# payments (net of bounces) have not covered. Every lease with rent still open is one of these:
# credits and waivers only take more off.
UNCOVERED = """
SELECT count(*),
       coalesce(array_agg(DISTINCT c.lease_id)
                FILTER (WHERE c.amount > coalesce(paid.amount, 0) - coalesce(back.amount, 0)),
                '{}')
FROM trust_charges AS c
LEFT JOIN (SELECT p.charge_id, sum(p.amount) AS amount FROM trust_charge_payments AS p
           WHERE p.pmc_id = %(pmc)s GROUP BY p.charge_id) AS paid ON paid.charge_id = c.id
LEFT JOIN (SELECT r.charge_id, sum(r.amount) AS amount FROM trust_payment_reversals AS r
           WHERE r.pmc_id = %(pmc)s GROUP BY r.charge_id) AS back ON back.charge_id = c.id
WHERE c.pmc_id = %(pmc)s AND c.kind = 'rent' AND c.due_on < %(day)s
"""
# What is open of those leases' rents due before the day: each lease's open charges read alone
# (by its index, migration 20261028000200), or the company's all at once.
OPEN_RENT_OF = """
SELECT o.lease_id, c.due_on, o.open
FROM unnest(%(leases)s::uuid[]) AS k (lease_id)
CROSS JOIN LATERAL trust_open_charges(%(pmc)s, k.lease_id, 'infinity') AS o
JOIN trust_charges AS c ON c.id = o.charge_id
WHERE o.open > 0 AND c.kind = 'rent' AND c.due_on < %(day)s
"""
OPEN_RENT = """
SELECT o.lease_id, c.due_on, o.open
FROM trust_open_charges(%(pmc)s, NULL, 'infinity') AS o
JOIN trust_charges AS c ON c.id = o.charge_id
WHERE o.open > 0 AND c.kind = 'rent' AND c.due_on < %(day)s
"""
# One lease's open charges read alone cost about what this many charges cost read all at once:
# on a company of 20,000 units, 0.32 ms a lease against 335 ms for its 22,053 charges (10/29).
ALONE = 20


def rent_past_due(
    conn: psycopg.Connection[Any], pmc_id: UUID, day: date
) -> list[tuple[UUID, date, Decimal]]:
    """Each rent due before the day and still open: lease, due day, what is open of it. Read
    lease by lease for the few leases whose payments haven't covered their rent (most days,
    most leases have paid), all at once when most leases haven't (the days after rent is due);
    either way the same."""
    found = conn.execute(UNCOVERED, {"pmc": pmc_id, "day": day}).fetchone()
    assert found is not None
    rents, leases = found
    if not leases:
        return []
    if len(leases) * ALONE <= rents:
        query, params = OPEN_RENT_OF, {"pmc": pmc_id, "day": day, "leases": leases}
    else:
        query, params = OPEN_RENT, {"pmc": pmc_id, "day": day}
    return [(lease, due_on, Decimal(left)) for lease, due_on, left in conn.execute(query, params)]


@router.get("")
def today(session: Staff, conn: Conn, on: Annotated[date | None, Query()] = None) -> Today:
    """What needs someone on the day (today, UTC, by default)."""
    day = on or rent.today()
    # Rent due before the day and still open, by the rent page's own open charges: only these
    # leases are read further, not every lease's whole rent page.
    due: dict[UUID, list[tuple[date, Decimal]]] = {}
    for lease, due_on, left in rent_past_due(conn, session.pmc_id, day):
        due.setdefault(lease, []).append((due_on, Decimal(left)))
    late = [
        LateLease(
            lease_id=lease,
            property=prop,
            unit=unit,
            tenants=tenants,
            rent_past_due=rent.cents(sum((left for _, left in due[lease]), Decimal(0))),
            oldest_due_on=min(when for when, _ in due[lease]),
        )
        for lease, prop, unit, tenants in conn.execute(LATE, (session.pmc_id, list(due)))
    ]
    asked = {
        lease: d
        for lease, d in rent.deposits_asked(conn, session.pmc_id, open_on=day).items()
        if Decimal(d.to_receive) > 0
    }
    deposits = [
        DepositDue(
            lease_id=lease,
            property=prop,
            unit=unit,
            tenants=tenants,
            asked=asked[lease].asked,
            to_receive=asked[lease].to_receive,
        )
        for lease, prop, unit, tenants in conn.execute(LATE, (session.pmc_id, list(asked)))
    ]
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
                    prepared=bank.prepared,
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
        applications=[
            a
            for a in leasing.applications_of(conn, session.pmc_id, show="open")[0]
            if a.status in ("new", "screening")
        ],
        unsigned_offers=[
            o
            for o in offers.offers_of(conn, session.pmc_id, show="open")[0]
            if o.status in ("sent", "partially signed")
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
        deposits_to_receive=deposits,
        deposits_to_return=moveout.due_back(conn, session.pmc_id, day),
        owner_draws=payouts.waiting_draws_of(conn, session.pmc_id),
        month_to_month=holdovers(conn, session.pmc_id, day),
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
