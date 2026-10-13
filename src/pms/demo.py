"""A demo PMC to show the PMS with: synthetic, and built the way staff build one.

`python -m pms.demo` adds a demo PMC to the database in DATABASE_URL (already migrated) and
prints the links to sign in with. Every step goes through the API's own route functions, as
trust_app, naming a staff member to the audit log as a request does: the setup, two months of
rent (a pet fee, a late fee, a bounced payment), bills, management fees, owner draws, and each
month's bank statement imported and matched to the books. The month before last is reconciled
and approved. Last month is prepared by the bookkeeper and waits for the manager to approve it,
with the roof repair's check not yet cleared at the bank: the three-way reconciliation the demo
opens on. One owner has three properties, so their statement shows how one rolls up.

Every name is synthetic, and every email is at example.com. The dates follow today's: the two
months are the two before this one.

usage: python -m pms.demo
"""

from __future__ import annotations

import csv
import io
import os
import sys
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql

from pms import accounts
from pms.api import payables, reconcile, rent, setup, staff

D = Decimal
DAY = timedelta(days=1)

# Owner, property, then each unit: its name, monthly rent and tenants (None: vacant).
PORTFOLIO: list[tuple[str, str, list[tuple[str, D, list[str] | None]]]] = [
    (
        "Demo Owner A",
        "Elm Court",
        [("Unit 1", D("1450.00"), ["Riley Demo"]), ("Unit 2", D("1525.00"), ["Casey Sample"])],
    ),
    (
        "Demo Owner A",
        "Birch Flats",
        [("Unit A", D("1395.00"), ["Morgan Demo"]), ("Unit B", D("1610.00"), ["Jamie Sample"])],
    ),
    ("Demo Owner A", "Cedar House", [("House", D("2250.00"), ["Taylor Demo", "Quinn Demo"])]),
    (
        "Demo Owner B",
        "Aspen Row",
        [("Unit 1", D("1180.00"), ["Drew Sample"]), ("Unit 2", D("1240.00"), None)],
    ),
]
# Management terms per owner: fee percent, minimum fee, reserve, approval limit.
TERMS = {
    "Demo Owner A": (D("8"), D("50.00"), D("250.00"), D("500.00")),
    "Demo Owner B": (D("10"), D("0"), D("200.00"), D("300.00")),
}
PET_RENT = D("35.00")
LATE_FEE = D("50.00")


@dataclass
class Demo:
    """What the demo made: its PMC, its people and where its money is."""

    pmc_id: UUID
    manager: UUID
    bookkeeper: UUID
    operating: UUID
    deposits: UUID
    owners: dict[str, UUID] = field(default_factory=dict)
    # Property name -> its owner's ledger account.
    accounts: dict[str, str] = field(default_factory=dict)
    # (property, unit) -> lease, and the tenants by name.
    leases: dict[tuple[str, str], UUID] = field(default_factory=dict)
    tenants: dict[str, UUID] = field(default_factory=dict)
    vendors: dict[str, UUID] = field(default_factory=dict)
    # The months: the one before last, last month, and this month's first day.
    months: tuple[date, date, date] = (date.min, date.min, date.min)
    # Last month's preparation of the operating account, waiting for the manager.
    preparation: UUID | None = None
    # The roof repair's check: paid on last month's last day, not on its statement.
    outstanding: str = ""


def uid(created: setup.Created) -> UUID:
    return UUID(str(created.id))


def first_of_month(day: date, back: int) -> date:
    months = day.year * 12 + day.month - 1 - back
    return date(months // 12, months % 12 + 1, 1)


class Builder:
    """Calls the API's routes as a staff member of the demo PMC, one transaction a call."""

    def __init__(self, conn: psycopg.Connection[Any], pmc_id: UUID) -> None:
        self.conn = conn
        self.pmc_id = pmc_id

    def session(self, staff_id: UUID) -> accounts.Session:
        # The routes read who is asking and their PMC; none of them reads the login.
        return accounts.Session(
            login_id=UUID(int=0), pmc_id=self.pmc_id, kind="staff", person_id=staff_id, email=""
        )

    @contextmanager
    def as_staff(self, staff_id: UUID) -> Iterator[accounts.Session]:
        """One request's transaction, naming the staff member to the audit log as the API
        does."""
        with self.conn.transaction():
            self.conn.execute("SELECT set_config('trust.staff_id', %s, true)", (str(staff_id),))
            yield self.session(staff_id)


def one(conn: psycopg.Connection[Any], query: str, params: Sequence[Any] = ()) -> Any:
    row = conn.execute(query, params).fetchone()
    assert row is not None
    return row[0]


def statement_csv(lines: list[tuple[date, D, str]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["date", "amount", "description", "reference"])
    for n, (day, amount, description) in enumerate(lines, start=1):
        writer.writerow([day.isoformat(), f"{amount:.2f}", description, f"DEMO-{n:04d}"])
    return out.getvalue()


def build(conn: psycopg.Connection[Any], today: date | None = None) -> Demo:
    """Builds a demo PMC on a connection acting as trust_app (autocommit), dated from today."""
    today = today or datetime.now(UTC).date()
    month_ago, last_month, this_month = (first_of_month(today, n) for n in (2, 1, 0))
    with conn.transaction():
        pmc_id = one(
            conn,
            "INSERT INTO trust_pmcs (display_name) VALUES ('Demo Property Management')"
            " RETURNING pmc_id",
        )
        # The first staff member and their admin role come in as `python -m pms.admin` makes
        # them: naming nobody, since nobody is signed in yet.
        manager = one(
            conn,
            "INSERT INTO trust_staff (pmc_id, display_name) VALUES (%s, 'Demo Manager')"
            " RETURNING id",
            (pmc_id,),
        )
        conn.execute(
            "INSERT INTO trust_staff_roles (pmc_id, staff_id, role, granted)"
            " VALUES (%s, %s, 'admin', true)",
            (pmc_id, manager),
        )
    b = Builder(conn, pmc_id)
    with b.as_staff(manager) as s:
        for role in ("approve", "disburse"):
            staff.change(s, conn, manager, staff.RoleChange(role=role, granted=True))
        bookkeeper = staff.add(s, conn, staff.NewMember(name="Demo Bookkeeper")).id
        operating = uid(
            setup.add_bank_account(
                s, conn, setup.NewBankAccount(kind="operating", name="Demo operating trust account")
            )
        )
        deposits = uid(
            setup.add_bank_account(
                s,
                conn,
                setup.NewBankAccount(kind="security_deposit", name="Demo security deposit account"),
            )
        )
    demo = Demo(pmc_id, manager, bookkeeper, operating, deposits)
    demo.months = (month_ago, last_month, this_month)
    lease_start = first_of_month(today, 11)

    with b.as_staff(manager) as s:
        for owner in TERMS:
            demo.owners[owner] = uid(setup.add_owner(s, conn, setup.NewName(name=owner)))
        for vendor in ("Demo Plumbing Co", "Demo Roofing Co"):
            demo.vendors[vendor] = uid(setup.add_vendor(s, conn, setup.NewName(name=vendor)))
        for owner, prop, units in PORTFOLIO:
            new = setup.NewProperty(
                name=prop, owner_id=demo.owners[owner], bank_account_id=operating
            )
            property_id = uid(setup.add_property(s, conn, new))
            demo.accounts[prop] = one(
                conn,
                "SELECT ledger_account_id FROM trust_ledger_accounts"
                " WHERE property_id = %s AND kind = 'owner_property'",
                (property_id,),
            )
            percent, minimum, reserve, limit = TERMS[owner]
            setup.add_agreement(
                s,
                conn,
                setup.NewAgreement(
                    account=demo.accounts[prop],
                    starts_on=lease_start,
                    fee_percent=percent,
                    minimum_fee=minimum,
                    reserve=reserve,
                    approval_limit=limit,
                ),
            )
            setup.add_late_fees(
                s,
                conn,
                setup.NewLateFees(
                    property_id=property_id, starts_on=lease_start, grace_days=5, flat_fee=LATE_FEE
                ),
            )
            for unit, monthly, people in units:
                unit_id = uid(
                    setup.add_unit(s, conn, setup.NewUnit(property_id=property_id, name=unit))
                )
                if people is None:
                    continue
                for person in people:
                    demo.tenants[person] = uid(
                        setup.add_tenant(
                            s, conn, setup.NewUnit(property_id=property_id, name=person)
                        )
                    )
                demo.leases[(prop, unit)] = uid(
                    setup.add_lease(
                        s,
                        conn,
                        setup.NewLease(
                            unit_id=unit_id,
                            starts_on=lease_start,
                            monthly_rent=monthly,
                            tenant_ids=[demo.tenants[p] for p in people],
                        ),
                    )
                )
        rent.add_recurring_charge(
            s,
            conn,
            demo.leases[("Birch Flats", "Unit A")],
            rent.NewRecurringCharge(
                kind="fee", amount=PET_RENT, memo="Pet rent", starts_on=month_ago
            ),
        )

    # The month before last: deposits brought over, the owners' reserve, rent (one tenant
    # late), a plumbing bill, fees and draws.
    with b.as_staff(manager) as s:
        for _, _, units in PORTFOLIO:
            for _, monthly, people in units:
                for person in people or []:
                    rent.deposit(
                        s,
                        conn,
                        rent.NewDeposit(
                            request_id=uuid.uuid4(),
                            tenant_id=demo.tenants[person],
                            amount=monthly / len(people or [person]),
                            received_on=month_ago,
                            reference="Deposit brought over",
                            bank_account_id=deposits,
                        ),
                    )
        payables.contribute(
            s,
            conn,
            payables.Contribution(
                request_id=uuid.uuid4(),
                account=demo.accounts["Aspen Row"],
                amount=D("300.00"),
                on=month_ago + DAY,
                memo="Reserve",
            ),
        )
    collect_rent(b, demo, manager, month_ago, late=("Birch Flats", "Unit B"))
    with b.as_staff(manager) as s:
        bill = payables.enter_bill(
            s,
            conn,
            payables.NewBill(
                vendor_id=demo.vendors["Demo Plumbing Co"],
                account=demo.accounts["Elm Court"],
                reference="PLB-1001",
                bill_date=month_ago + 11 * DAY,
                due_on=month_ago + 25 * DAY,
                amount=D("380.00"),
                memo="Kitchen drain, Unit 2",
            ),
        ).id
        payables.set_aside(s, conn, bill, payables.Day(on=month_ago + 11 * DAY))
        payables.pay(s, conn, bill, payables.Day(on=month_ago + 14 * DAY))
    close_month(b, demo, manager, month_ago, last_month)
    reconcile_month(b, demo, month_ago, last_month, approve=True)

    # Last month: rent (one payment bounced and paid again), a roof repair over the owner's
    # approval limit, paid by check on the last day, fees and draws.
    collect_rent(b, demo, manager, last_month, bounced=("Aspen Row", "Unit 1"))
    with b.as_staff(manager) as s:
        roof = payables.enter_bill(
            s,
            conn,
            payables.NewBill(
                vendor_id=demo.vendors["Demo Roofing Co"],
                account=demo.accounts["Cedar House"],
                reference="RF-2207",
                bill_date=last_month + 8 * DAY,
                due_on=this_month - DAY,
                amount=D("650.00"),
                memo="Roof flashing repair",
            ),
        ).id
        payables.approve(
            s,
            conn,
            roof,
            payables.Approval(
                approved_on=last_month + 9 * DAY, approved_by="Demo Owner A, by email"
            ),
        )
        payables.set_aside(s, conn, roof, payables.Day(on=last_month + 10 * DAY))
        demo.outstanding = payables.pay(
            s, conn, roof, payables.Day(on=this_month - DAY)
        ).transfer_id
    close_month(b, demo, manager, last_month, this_month)
    reconcile_month(b, demo, last_month, this_month, approve=False, skip={demo.outstanding})
    return demo


def collect_rent(
    b: Builder,
    demo: Demo,
    manager: UUID,
    month: date,
    late: tuple[str, str] | None = None,
    bounced: tuple[str, str] | None = None,
) -> None:
    """The month's rent run and each lease paying what it owes: one late (a late fee, then
    paid), one bounced and paid again."""
    conn = b.conn
    with b.as_staff(manager) as s:
        rent.charge_rent(s, conn, rent.Day(on=month))
    for n, (key, lease) in enumerate(demo.leases.items()):
        if key == late:
            continue
        with b.as_staff(manager) as s:
            paid = rent.pay(s, conn, lease, payment(conn, lease, month + (1 + n % 4) * DAY))
        if key == bounced:
            with b.as_staff(manager) as s:
                rent.bounced(s, conn, paid.transfers[0], rent.Day(on=month + 7 * DAY))
            with b.as_staff(manager) as s:
                rent.pay(s, conn, lease, payment(conn, lease, month + 12 * DAY))
    with b.as_staff(manager) as s:
        rent.late_fees(s, conn, rent.Day(on=month + 6 * DAY))
    if late is not None:
        with b.as_staff(manager) as s:
            lease = demo.leases[late]
            rent.pay(s, conn, lease, payment(conn, lease, month + 9 * DAY))


def payment(conn: psycopg.Connection[Any], lease: UUID, day: date) -> rent.NewPayment:
    """A payment of everything the lease owes."""
    return rent.NewPayment(request_id=uuid.uuid4(), amount=rent.owed(conn, lease), received_on=day)


def close_month(b: Builder, demo: Demo, manager: UUID, start: date, end: date) -> None:
    """The month's management fees, then each owner draws what is available, on its last
    day."""
    conn = b.conn
    with b.as_staff(manager) as s:
        payables.management_fees(
            s, conn, payables.FeeRun(period_start=start, period_end=end, on=end - DAY)
        )
    for account in demo.accounts.values():
        with b.as_staff(manager) as s:
            payables.draw(
                s, conn, payables.Draw(request_id=uuid.uuid4(), account=account, on=end - DAY)
            )


def reconcile_month(
    b: Builder, demo: Demo, start: date, end: date, approve: bool, skip: set[str] | None = None
) -> None:
    """Each trust bank account's statement for the month, as the bank shows what the books
    moved (a check clears two days after it is written; `skip` hasn't cleared), imported and
    matched line by line by the bookkeeper, who prepares the month; the manager approves it."""
    conn = b.conn
    last_day = end - DAY
    for bank in (demo.operating, demo.deposits):
        moved = [
            t
            for t in reconcile.transfers(
                conn,
                bank,
                "tr.event_at >= %(start)s AND tr.event_at < %(end)s",
                start=datetime.combine(start, time(), UTC),
                end=datetime.combine(end, time(), UTC),
            )
            if t.id not in (skip or set())
        ]
        days = [min(t.on + 2 * DAY, last_day) if D(t.amount) < 0 else t.on for t in moved]
        lines = [
            (day, D(t.amount), t.memo or "Transfer") for day, t in zip(days, moved, strict=True)
        ]
        with b.as_staff(demo.bookkeeper) as s:
            imported = reconcile.import_statement(
                s,
                conn,
                reconcile.NewStatement(
                    bank_account_id=bank,
                    format="csv",
                    content=statement_csv(lines),
                    first_day=start,
                    last_day=last_day,
                ),
            )
            line_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM trust_bank_statement_lines WHERE statement_id = %s"
                    " ORDER BY line_no",
                    (imported.id,),
                )
            ]
            for line_id, transfer in zip(line_ids, moved, strict=True):
                reconcile.match(conn, s, bank, [line_id], [transfer.id])
            prepared = reconcile.prepare(s, conn, bank, reconcile.Preparation(through=last_day)).id
        if approve:
            with b.as_staff(demo.manager) as s:
                reconcile.approve(s, conn, bank, reconcile.Approval(preparation_id=prepared))
        elif bank == demo.operating:
            demo.preparation = prepared


def invite(conn: psycopg.Connection[Any], demo: Demo, kind: str, person: UUID, who: str) -> str:
    made = accounts.create_invite(
        conn, demo.pmc_id, kind, person, f"demo-{who}-{uuid.uuid4().hex[:8]}@example.com", None
    )
    public_url = os.environ.get("PMS_PUBLIC_URL", "http://localhost:8000").rstrip("/")
    return f"{public_url}/invite#{made.token}"


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier("trust_app")))
        demo = build(conn)
        with conn.transaction():
            links = {
                "the manager, who approves last month's reconciliation": invite(
                    conn, demo, "staff", demo.manager, "manager"
                ),
                "Demo Owner A, with three properties": invite(
                    conn, demo, "owner", demo.owners["Demo Owner A"], "owner"
                ),
                "Taylor Demo, a tenant of Cedar House": invite(
                    conn, demo, "tenant", demo.tenants["Taylor Demo"], "tenant"
                ),
            }
    print(f"Demo PMC {demo.pmc_id}: Demo Property Management")
    for who, link in links.items():
        print(f"Sign in as {who}: {link}")
    print("As the manager, open Reconcile: last month waits for your approval.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
