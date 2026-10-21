"""A demo PMC to show the PMS with: synthetic, and built the way staff build one.

`python -m pms.demo` adds a demo PMC to the database in DATABASE_URL (already migrated) and
prints the links to sign in with. Every step goes through the API's own route functions, as
trust_app, naming a staff member to the audit log as a request does. Its books (`books`): the
setup, two months of rent (a pet fee, a late fee, a bounced payment), bills, management fees,
the company's fees taken out of trust the month after (docs/law.md), owner draws, and each
month's bank statement imported and matched to the books. The month before last is reconciled
and approved. Last month is prepared by the bookkeeper and waits for the manager to approve it,
with the roof repair's check not yet cleared at the bank: the three-way reconciliation the demo
opens on. One owner has three properties, so their statement shows how one rolls up.

Then something on every other page (`every_page`): the manager is each trust bank account's
broker and the bookkeeper a bonded signer; a leasing agent holds a custom role; the vendors have
insurance (the roofer's running out); a move-in inspection; a year's budget; renters insurance,
on file for Cedar House and missing for Aspen Row; Cedar House's rent going up next month on a
month's written notice; and a journal entry the bookkeeper proposed, waiting for the manager.
The Dashboard lists what waits, last month's fees in trust among it.

Then the founder's demo seed (`showcase`, ADR 0006), so every list has something to click
into: Harbor Tower (48 units) and Oak Terrace, two property groups, this month's rent with three
tenants owing (part paid, unpaid, a bounce with its NSF fee), a lease ending in 45 days, one to
come and one ended with its deposit held, applicants at each step, a lease out to sign and a
renewal half signed, bills of every status, work orders at every step, four vendors (a W-9 on
file, an expired policy) and files.

Every name is synthetic, and every email is at example.com. The dates follow today's: the two
months are the two before this one.

usage: python -m pms.demo
"""

from __future__ import annotations

import base64
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
from types import SimpleNamespace
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg import sql

from pms import accounts, nightly
from pms.api import (
    assistance,
    budgets,
    company,
    custom_roles,
    documents,
    emails,
    groups,
    inspections,
    journal,
    leads,
    leasing,
    listings,
    maintenance,
    offers,
    payables,
    reconcile,
    rent,
    setup,
    signers,
    signing,
    staff,
    vendors,
    work_orders,
)
from pms.leasing import screening

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
# Cedar House's rent from next month, after a month's written notice.
NEW_RENT = D("2300.00")


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
    # Expense account name -> its id: bills go in one, for the owners' income statements.
    expense_accounts: dict[str, UUID] = field(default_factory=dict)
    # The months: the one before last, last month, and this month's first day.
    months: tuple[date, date, date] = (date.min, date.min, date.min)
    # Last month's preparation of the operating account, waiting for the manager.
    preparation: UUID | None = None
    # The roof repair's check: paid on last month's last day, not on its statement.
    outstanding: str = ""
    leasing_agent: UUID | None = None
    # The bookkeeper's journal entry, waiting for the manager to post it.
    journal_entry: UUID | None = None


def uid(created: setup.Created | vendors.Created) -> UUID:
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


def build(
    conn: psycopg.Connection[Any],
    today: date | None = None,
    public_url: str = "http://localhost:8000",
) -> Demo:
    """Builds a demo PMC on a connection acting as trust_app (autocommit), dated from today:
    its books, something on every other page, and the founder's demo seed (ADR 0006)."""
    today = today or datetime.now(UTC).date()
    demo = books(conn, today)
    every_page(conn, demo, today)
    showcase(conn, demo, today, public_url)
    return demo


def books(conn: psycopg.Connection[Any], today: date | None = None) -> Demo:
    """A demo PMC's setup and two months of its books, through last month's month-end; each
    browser test of a feature starts from these (frontend/e2e/seed.py)."""
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
        for vendor, trade, method in VENDORS[:2]:
            demo.vendors[vendor] = uid(
                vendors.add_vendor(
                    s,
                    conn,
                    vendors.NewVendor(
                        name=vendor, trade=trade, gets_1099=True, payment_method=method
                    ),
                )
            )
        for name, line in (("Repairs", 14), ("Landscaping", 7), ("Water", 17)):
            demo.expense_accounts[name] = uid(
                setup.add_expense_account(
                    s, conn, setup.NewExpenseAccount(name=name, schedule_e_line=line)
                )
            )
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
                expense_account_id=demo.expense_accounts["Repairs"],
            ),
        ).id
        payables.set_aside(s, conn, bill, payables.Day(on=month_ago + 11 * DAY))
        payables.pay(s, conn, bill, payables.Paying(on=month_ago + 14 * DAY))
    close_month(b, demo, manager, month_ago, last_month)
    reconcile_month(b, demo, month_ago, last_month, approve=True)

    # Last month: the month before last's fees taken out of trust (docs/law.md), rent (one
    # payment bounced and paid again), a roof repair over the owner's approval limit, paid by
    # check on the last day, fees and draws.
    with b.as_staff(manager) as s:
        payables.take_company_fees(
            s,
            conn,
            payables.FeeWithdrawal(
                request_id=uuid.uuid4(), bank_account_id=operating, on=last_month + 4 * DAY
            ),
        )
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
                expense_account_id=demo.expense_accounts["Repairs"],
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
            s, conn, roof, payables.Paying(on=this_month - DAY)
        ).transfer_id
    close_month(b, demo, manager, last_month, this_month)
    reconcile_month(b, demo, last_month, this_month, approve=False, skip={demo.outstanding})
    return demo


def every_page(conn: psycopg.Connection[Any], demo: Demo, today: date) -> None:
    """Something on each page the books leave empty, and on the Dashboard what waits: the trust bank
    accounts' broker and a signer, a leasing agent's custom role, vendors' insurance (the
    roofer's running out), a move-in inspection, a year's budget, renters insurance on file
    for one lease and missing for another, a rent change on notice, and a journal entry
    waiting for the manager."""
    b = Builder(conn, demo.pmc_id)
    lease_start = first_of_month(today, 11)
    this_month = demo.months[2]
    staff_and_signers(b, demo, lease_start)
    with b.as_staff(demo.manager) as s:
        # The plumber's general liability runs a while yet; the roofer's runs out in 20 days.
        for vendor, number, starts, expires in (
            ("Demo Plumbing Co", "GL-DEMO-1001", lease_start, first_of_month(today, -13) - DAY),
            ("Demo Roofing Co", "GL-DEMO-2001", today - 345 * DAY, today + 20 * DAY),
        ):
            vendors.record_policy(
                s,
                conn,
                demo.vendors[vendor],
                vendors.NewPolicy(
                    coverage="general liability",
                    carrier="Demo Casualty Co",
                    policy_number=number,
                    starts_on=starts,
                    expires_on=expires,
                    limit_amount=D("1000000.00"),
                ),
            )
        inspections.record(
            s,
            conn,
            inspections.NewInspection(
                lease_id=demo.leases[("Elm Court", "Unit 1")],
                kind="move-in",
                inspected_on=lease_start,
                items=[
                    inspections.NewItem(room="Kitchen", condition="good"),
                    inspections.NewItem(room="Living room", condition="good"),
                    inspections.NewItem(
                        room="Bathroom", condition="fair", note="Grout stained by the tub"
                    ),
                ],
            ),
        )
        # Elm Court's year: its two rents each month, and a little for repairs.
        budgets.set_budget(
            s,
            conn,
            budgets.NewBudget(
                account=demo.accounts["Elm Court"],
                year=today.year,
                lines=[
                    budgets.LineBudget(line="rent", months=[D("2975.00")] * 12),
                    budgets.LineBudget(
                        line="expense account",
                        expense_account_id=demo.expense_accounts["Repairs"],
                        months=[D("100.00")] * 12,
                    ),
                ],
            ),
        )
        renters_insurance(b, demo, s, lease_start, today)
        # Cedar House's rent goes up next month, on written notice given before this one.
        rent.change_rent(
            s,
            conn,
            demo.leases[("Cedar House", "House")],
            rent.NewRentChange(
                effective_on=first_of_month(today, -1),
                monthly_rent=NEW_RENT,
                notice_given_on=this_month - 10 * DAY,
                served_by="email",
            ),
        )
    # The bookkeeper proposes moving 25.00 of Elm Court's reserve to Birch Flats.
    with b.as_staff(demo.bookkeeper) as s:
        demo.journal_entry = journal.propose(
            s,
            conn,
            journal.NewEntry(
                event_on=this_month,
                memo="Reserve moved to Birch Flats, as Demo Owner A asked",
                lines=[
                    journal.NewLine(
                        from_account=demo.accounts["Elm Court"],
                        to_account=demo.accounts["Birch Flats"],
                        amount=D("25.00"),
                    )
                ],
            ),
        ).id


# --- the founder's demo seed (ADR 0006): something to click into on every page ---------------

# Harbor Tower: four floors of twelve, its rent by floor. The last four units are vacant and the
# eighth on the top floor's lease ended last month, its deposit not yet returned.
HARBOR_FLOORS = 4
HARBOR_PER_FLOOR = 12
HARBOR_VACANT = ("Unit 409", "Unit 410", "Unit 411", "Unit 412")
HARBOR_ENDED = "Unit 408"
# This month's rent at Harbor Tower: one tenant paid part of it, one none, one's payment bounced.
HARBOR_PARTIAL, HARBOR_UNPAID, HARBOR_BOUNCED = "Unit 105", "Unit 107", "Unit 109"
WorkOrderStep = Literal["assigned", "scheduled", "completed", "cancelled"]
# The demo's vendors: what they do and how they are paid.
VENDORS: tuple[tuple[str, str, vendors.Method], ...] = (
    ("Demo Plumbing Co", "Plumbing", "EFT"),
    ("Demo Roofing Co", "Roofing", "check"),
    ("Demo Landscaping Co", "Landscaping", "EFT"),
    ("Demo Electric Co", "Electrical", "check"),
)
NSF_FEE = D("35.00")
PDF = b"%PDF-1.4\n% synthetic demo document\n"
PNG = b"\x89PNG\r\n\x1a\n synthetic demo photo"


def harbor_rent(unit: str) -> D:
    return D("1050.00") + D("25.00") * int(unit.split()[1][0])


class _Request:
    """What the leasing and signing routes read off a request: the server's settings and its
    screening partner, here the fake the demo screens with; no address or browser."""

    def __init__(self, public_url: str) -> None:
        state = SimpleNamespace(
            settings=SimpleNamespace(public_url=public_url),
            screening=screening.FakeScreening(),
        )
        self.app = SimpleNamespace(state=state)
        self.client = None
        self.headers: dict[str, str] = {}


def on_or_before(day: date, today: date) -> date:
    return min(day, today)


def showcase(conn: psycopg.Connection[Any], demo: Demo, today: date, public_url: str) -> None:
    """The founder's demo seed (ADR 0006), so every page has something to open: two more
    properties (Harbor Tower, 48 units, and Oak Terrace), two property groups, this month's rent
    charged with two tenants owing and a bounce with its NSF fee, a lease ending in 45 days, one
    to come and one ended with its deposit held, applicants at every step on the vacant units,
    lease offers sent and partly signed, bills of every status, work orders at every step, four
    vendors with a W-9 on file and an expired policy, and files on a lease, a vendor, an owner
    and a bill. Everything this month is dated after last month's prepared reconciliation."""
    b = Builder(conn, demo.pmc_id)
    this_month = demo.months[2]
    lease_start = first_of_month(today, 11)
    request: Any = _Request(public_url)
    with b.as_staff(demo.manager) as s:
        demo.owners["Demo Owner C"] = uid(
            setup.add_owner(s, conn, setup.NewName(name="Demo Owner C"))
        )
        for vendor, trade, method in VENDORS[2:]:
            # The electrician is a corporation: no 1099.
            demo.vendors[vendor] = uid(
                vendors.add_vendor(
                    s,
                    conn,
                    vendors.NewVendor(
                        name=vendor,
                        trade=trade,
                        gets_1099=vendor != "Demo Electric Co",
                        payment_method=method,
                    ),
                )
            )
        # The electrician's policy ran out last month: assigning them warns of it.
        vendors.record_policy(
            s,
            conn,
            demo.vendors["Demo Electric Co"],
            vendors.NewPolicy(
                coverage="general liability",
                carrier="Demo Casualty Co",
                policy_number="GL-DEMO-3001",
                starts_on=first_of_month(today, 13),
                expires_on=this_month - 5 * DAY,
                limit_amount=D("500000.00"),
            ),
        )
    harbor = new_property(
        b,
        demo,
        "Demo Owner C",
        "Harbor Tower",
        lease_start,
        (D("8"), D("0"), D("500.00"), D("500.00")),
    )
    oak = new_property(b, demo, "Demo Owner B", "Oak Terrace", lease_start, TERMS["Demo Owner B"])
    units: dict[str, UUID] = {}
    with b.as_staff(demo.manager) as s:
        setup.add_nsf_fee(
            s, conn, setup.NewNsfFee(property_id=harbor, starts_on=lease_start, fee=NSF_FEE)
        )
        for floor in range(1, HARBOR_FLOORS + 1):
            for n in range(1, HARBOR_PER_FLOOR + 1):
                unit = f"Unit {floor}{n:02d}"
                units[unit] = uid(
                    setup.add_unit(s, conn, setup.NewUnit(property_id=harbor, name=unit))
                )
        for unit in units:
            if unit in HARBOR_VACANT:
                continue
            tenant = f"Resident {unit.split()[1]}"
            demo.tenants[tenant] = uid(
                setup.add_tenant(s, conn, setup.NewUnit(property_id=harbor, name=tenant))
            )
            ends = this_month - 16 * DAY if unit == HARBOR_ENDED else None
            demo.leases[("Harbor Tower", unit)] = uid(
                setup.add_lease(
                    s,
                    conn,
                    setup.NewLease(
                        unit_id=units[unit],
                        starts_on=lease_start,
                        ends_on=ends,
                        monthly_rent=harbor_rent(unit),
                        tenant_ids=[demo.tenants[tenant]],
                    ),
                )
            )
        # Oak Terrace: two tenants whose lease ends in 45 days, and a lease from next month.
        oak_units = {
            name: uid(setup.add_unit(s, conn, setup.NewUnit(property_id=oak, name=name)))
            for name in ("Unit 1", "Unit 2")
        }
        for person in ("Avery Demo", "Jordan Sample", "Parker Demo"):
            demo.tenants[person] = uid(
                setup.add_tenant(s, conn, setup.NewUnit(property_id=oak, name=person))
            )
        demo.leases[("Oak Terrace", "Unit 1")] = uid(
            setup.add_lease(
                s,
                conn,
                setup.NewLease(
                    unit_id=oak_units["Unit 1"],
                    starts_on=lease_start,
                    ends_on=today + 45 * DAY,
                    monthly_rent=D("1325.00"),
                    tenant_ids=[demo.tenants["Avery Demo"], demo.tenants["Jordan Sample"]],
                ),
            )
        )
        demo.leases[("Oak Terrace", "Unit 2")] = uid(
            setup.add_lease(
                s,
                conn,
                setup.NewLease(
                    unit_id=oak_units["Unit 2"],
                    starts_on=first_of_month(today, -1),
                    monthly_rent=D("1290.00"),
                    tenant_ids=[demo.tenants["Parker Demo"]],
                ),
            )
        )
        groups.create_group(
            s,
            conn,
            groups.NewGroup(
                name="North portfolio",
                property_ids=[
                    property_of(conn, demo, p) for p in ("Elm Court", "Birch Flats", "Harbor Tower")
                ],
            ),
        )
        groups.create_group(
            s,
            conn,
            groups.NewGroup(
                name="South portfolio",
                property_ids=[
                    property_of(conn, demo, p) for p in ("Cedar House", "Aspen Row", "Oak Terrace")
                ],
            ),
        )
    # Harbor Tower's and Oak Terrace's deposits came over from the old system this month.
    with b.as_staff(demo.manager) as s:
        for (prop, unit), lease in demo.leases.items():
            if prop not in ("Harbor Tower", "Oak Terrace") or (prop, unit) == (
                "Oak Terrace",
                "Unit 2",
            ):
                continue
            monthly = harbor_rent(unit) if prop == "Harbor Tower" else D("1325.00")
            people = lease_tenants(conn, lease)
            for tenant_id in people:
                rent.deposit(
                    s,
                    conn,
                    rent.NewDeposit(
                        request_id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        amount=monthly / len(people),
                        received_on=this_month,
                        reference="Deposit brought over",
                        bank_account_id=demo.deposits,
                    ),
                )
    this_months_rent(b, demo, today)
    leasing_pipeline(b, demo, request, units, today)
    bills_of_every_status(b, demo, today, public_url)
    work_orders_at_every_step(b, demo, units, today)
    files(b, demo)
    company_profile(b, demo)
    lead_and_housing_authority(b, demo, units, today)


def lead_and_housing_authority(b: Builder, demo: Demo, units: dict[str, UUID], today: date) -> None:
    """A lead who asked about a vacant unit at Harbor Tower, to be shown it next week, and a
    housing authority on file, so Leads and Housing authorities have something to open."""
    conn = b.conn
    harbor = property_of(conn, demo, "Harbor Tower")
    with b.as_staff(demo.manager) as s:
        lead = leads.add_lead(
            s,
            conn,
            leads.NewLead(
                name="Morgan Sample",
                email="morgan.sample@example.com",
                property_id=harbor,
                unit_id=units["Unit 412"],
                source="Website",
            ),
        )
        leads.add_showing(
            s,
            conn,
            lead.id,
            leads.NewShowing(
                unit_id=units["Unit 412"],
                starts_at=datetime.combine(today + 7 * DAY, time(17, 0), UTC),
                shown_by=demo.manager,
            ),
        )
        assistance.add_authority(
            s, conn, assistance.NewAuthority(name="Sample County Housing Authority")
        )


def company_profile(b: Builder, demo: Demo) -> None:
    """The company's mailing address and the fee terms a property added from now on starts with,
    set last, so the demo's own properties keep the terms each was given; and every email the PMS
    writes by itself turned on (none is written until a server that sends email runs)."""
    with b.as_staff(demo.manager) as s:
        company.save_company(
            s,
            b.conn,
            company.NewCompany(
                name="Demo Property Management",
                address=company.Address(
                    address_line_1="100 Example Street",
                    address_line_2="Suite 200",
                    city="Sampletown",
                    state="ZZ",
                    postal_code="00000",
                ),
                late_fees=company.LateFeeTerms(grace_days=5, flat_fee=D("50.00")),
                nsf_fee=D("35.00"),
            ),
        )
        emails.save_email_settings(
            s,
            b.conn,
            emails.EmailSettings(
                rent_reminder_days=5,
                late_notices=True,
                owner_statement_day=5,
                work_order_notices=True,
                adverse_action_notices=True,
            ),
        )


def new_property(
    b: Builder, demo: Demo, owner: str, prop: str, since: date, terms: tuple[D, D, D, D]
) -> UUID:
    """A property of an owner's, in the operating account, with its management terms and late
    fees."""
    conn = b.conn
    with b.as_staff(demo.manager) as s:
        property_id = uid(
            setup.add_property(
                s,
                conn,
                setup.NewProperty(
                    name=prop, owner_id=demo.owners[owner], bank_account_id=demo.operating
                ),
            )
        )
        demo.accounts[prop] = one(
            conn,
            "SELECT ledger_account_id FROM trust_ledger_accounts"
            " WHERE property_id = %s AND kind = 'owner_property'",
            (property_id,),
        )
        percent, minimum, reserve, limit = terms
        setup.add_agreement(
            s,
            conn,
            setup.NewAgreement(
                account=demo.accounts[prop],
                starts_on=since,
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
                property_id=property_id, starts_on=since, grace_days=5, flat_fee=LATE_FEE
            ),
        )
    return property_id


def property_of(conn: psycopg.Connection[Any], demo: Demo, prop: str) -> UUID:
    return UUID(
        str(
            one(
                conn,
                "SELECT property_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
                (demo.accounts[prop],),
            )
        )
    )


def lease_tenants(conn: psycopg.Connection[Any], lease: UUID) -> list[UUID]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT tenant_id FROM trust_lease_tenants WHERE lease_id = %s ORDER BY tenant_id",
            (lease,),
        ).fetchall()
    ]


def this_months_rent(b: Builder, demo: Demo, today: date) -> None:
    """This month's rent charged to every lease in effect and paid, but at Harbor Tower: one
    tenant paid part of it, one none, and one's payment bounced, charging the NSF fee. Late fees
    come with the nightly run."""
    conn = b.conn
    this_month = demo.months[2]
    with b.as_staff(demo.manager) as s:
        rent.run_rent(s, conn, rent.RunRent(due_on=this_month, late_fees_as_of=this_month))
    for n, ((prop, unit), lease) in enumerate(demo.leases.items()):
        if rent.owed(conn, lease) <= 0 or (prop, unit) == ("Harbor Tower", HARBOR_UNPAID):
            continue
        day = on_or_before(this_month + (1 + n % 4) * DAY, today)
        with b.as_staff(demo.manager) as s:
            if (prop, unit) == ("Harbor Tower", HARBOR_PARTIAL):
                rent.pay(
                    s,
                    conn,
                    lease,
                    rent.NewPayment(request_id=uuid.uuid4(), amount=D("400.00"), received_on=day),
                )
                continue
            paid = rent.pay(s, conn, lease, payment(conn, lease, day))
        if (prop, unit) == ("Harbor Tower", HARBOR_BOUNCED):
            with b.as_staff(demo.manager) as s:
                rent.bounced(
                    s, conn, paid.transfers[0], rent.Day(on=on_or_before(day + 2 * DAY, today))
                )


# Applicants on the vacant units, one at each step: (unit, names, monthly income, the step).
APPLICANTS: list[tuple[str, list[str], D, str]] = [
    ("Unit 409", ["Sage Applicant"], D("5200.00"), "received"),
    ("Unit 410", ["Rowan Applicant"], D("3900.00"), "screened"),
    ("Unit 411", ["Emery Applicant", "Blake Applicant"], D("7400.00"), "approved"),
    ("Unit 412", ["Hayden Applicant"], D("1800.00"), "declined"),
    ("Unit 409", ["Kendall Applicant"], D("4600.00"), "withdrawn"),
]
# The vacant units on the company's public vacancy page, with what it says of each.
LISTED = {
    "Unit 409": "Fourth floor, two bedrooms, river view.",
    "Unit 410": "Fourth floor, one bedroom, laundry in the unit.",
}


def leasing_pipeline(
    b: Builder, demo: Demo, request: Any, units: dict[str, UUID], today: date
) -> None:
    """An application link on each vacant unit at Harbor Tower (two of them on the public
    vacancy page) and applications through them: one received, one screened, one approved and
    sent its lease to sign (asking a deposit, and parking every month), one declined with its
    adverse action notice and one its applicant withdrew. Oak Terrace's lease ending in 45 days
    has its renewal out, signed by one of its two tenants, and Cedar House's renewal is a draft.
    The applicants apply themselves: no staff member to name."""
    conn = b.conn
    move_in = first_of_month(today, -1)
    links: dict[str, leasing.OpenedLink] = {}
    for unit, names, income, step in APPLICANTS:
        if unit not in links:
            listed = LISTED.get(unit)
            with b.as_staff(demo.manager) as s:
                links[unit] = leasing.open_application_link(
                    request,
                    s,
                    conn,
                    leasing.NewLink(
                        unit_id=units[unit],
                        monthly_rent=harbor_rent(unit),
                        listing=None if listed is None else listings.NewListing(description=listed),
                    ),
                )
        opened = links[unit]
        with conn.transaction():
            leasing.apply(
                request,
                conn,
                leasing.ApplicationIn(
                    token=opened.url.split("#", 1)[1],
                    move_in_on=move_in,
                    monthly_income=income,
                    applicants=[
                        leasing.ApplicantIn(
                            name=name, email=f"{name.split()[0].lower()}.applicant@example.com"
                        )
                        for name in names
                    ],
                ),
            )
        application = one(
            conn,
            "SELECT a.id FROM trust_applications AS a"
            " JOIN trust_applicants AS p ON p.application_id = a.id"
            " WHERE a.link_id = %s AND p.position = 1 AND p.display_name = %s",
            (opened.id, names[0]),
        )
        if step == "withdrawn":
            with b.as_staff(demo.manager) as s:
                leasing.withdraw(
                    s, conn, application, leasing.Withdraw(reason="Took a home closer to work")
                )
        if step in ("received", "withdrawn"):
            continue
        with b.as_staff(demo.manager) as s:
            screened = leasing.screen(request, s, conn, application)
        if step == "screened":
            continue
        with b.as_staff(demo.manager) as s:
            if step == "approved":
                leasing.decide(
                    s,
                    conn,
                    application,
                    leasing.Decision(decision="approved", screening_id=screened.screenings[-1].id),
                )
            else:
                leasing.decide(
                    s,
                    conn,
                    application,
                    leasing.Decision(
                        decision="declined",
                        screening_id=screened.screenings[-1].id,
                        adverse_action=leasing.AdverseAction(
                            reasons=list(screened.screenings[-1].reasons)
                            or ["Income below three times the rent"],
                            notice_given_on=today,
                        ),
                    ),
                )
        if step == "approved":
            with b.as_staff(demo.manager) as s:
                offers.offer_lease(
                    request,
                    s,
                    conn,
                    application,
                    offers.OfferIn(
                        starts_on=move_in,
                        ends_on=first_of_month(today, -13) - DAY,
                        monthly_rent=harbor_rent(unit),
                        security_deposit=harbor_rent(unit),
                        recurring_charges=[
                            offers.ChargeIn(kind="fee", memo="Parking", amount=D("75.00"))
                        ],
                        document_title=f"Harbor Tower {unit} lease",
                        document_base64=base64.b64encode(PDF).decode(),
                    ),
                )
    # Oak Terrace Unit 1's renewal: sent to its two tenants, and one has signed.
    lease = demo.leases[("Oak Terrace", "Unit 1")]
    with b.as_staff(demo.manager) as s:
        offered = offers.offer_renewal(
            request,
            s,
            conn,
            lease,
            offers.OfferIn(
                starts_on=today + 46 * DAY,
                ends_on=today + 46 * DAY + 364 * DAY,
                monthly_rent=D("1360.00"),
                document_title="Oak Terrace Unit 1 renewal",
                document_base64=base64.b64encode(PDF).decode(),
                signers=[
                    offers.SignerIn(name="Avery Demo", email="avery.demo@example.com"),
                    offers.SignerIn(name="Jordan Sample", email="jordan.sample@example.com"),
                ],
            ),
        )
    # The first signer signs on the signing page: agrees to sign electronically, opens the
    # lease and types their name.
    first = offered.links[0]
    token = first.url.split("#", 1)[1]
    seen = signing.view(conn, signing.SigningToken(token=token))
    signing.consent(
        request, conn, signing.Consent(token=token, disclosure_sha256=seen.disclosure_sha256)
    )
    signing.document(request, conn, signing.SigningToken(token=token))
    signing.sign(
        request,
        conn,
        signing.Signature(token=token, typed_name=first.name, document_sha256=seen.document_sha256),
    )
    # Cedar House's renewal for next year, kept as a draft until its terms are agreed.
    with b.as_staff(demo.manager) as s:
        offers.offer_renewal(
            request,
            s,
            conn,
            demo.leases[("Cedar House", "House")],
            offers.OfferIn(
                starts_on=first_of_month(today, -3),
                ends_on=first_of_month(today, -15) - DAY,
                monthly_rent=D("2400.00"),
                document_title="Cedar House renewal",
                document_base64=base64.b64encode(PDF).decode(),
                signers=[
                    offers.SignerIn(name="Taylor Demo", email="taylor.demo@example.com"),
                    offers.SignerIn(name="Quinn Demo", email="quinn.demo@example.com"),
                ],
                send=False,
            ),
        )


def bills_of_every_status(b: Builder, demo: Demo, today: date, public_url: str) -> None:
    """Bills of every status this month: two due this week, one overdue, one over Demo Owner A's
    $500.00 limit waiting for them, one paid, with the two paid before; and the landscaper's
    monthly bill at Harbor Tower, entered by the nightly run (with this month's late fees)."""
    conn = b.conn
    this_month = demo.months[2]
    entered = on_or_before(this_month + DAY, today)
    bills = [
        (
            "Demo Landscaping Co",
            "Harbor Tower",
            "LND-3001",
            entered,
            today + 3 * DAY,
            D("260.00"),
            "Leaf cleanup",
            "Landscaping",
        ),
        (
            "Demo Plumbing Co",
            "Oak Terrace",
            "PLB-3002",
            entered,
            today + 5 * DAY,
            D("180.00"),
            "Water heater flush",
            "Repairs",
        ),
        (
            "Demo Plumbing Co",
            "Elm Court",
            "PLB-3003",
            today - 20 * DAY,
            today - 3 * DAY,
            D("240.00"),
            "Garbage disposal, Unit 1",
            "Repairs",
        ),
        (
            "Demo Roofing Co",
            "Cedar House",
            "RF-3004",
            entered,
            today + 14 * DAY,
            D("900.00"),
            "Gutter replacement",
            "Repairs",
        ),
        (
            "Demo Electric Co",
            "Harbor Tower",
            "ELC-3005",
            entered,
            entered + 10 * DAY,
            D("320.00"),
            "Hallway lighting",
            "Repairs",
        ),
    ]
    made: dict[str, UUID] = {}
    with b.as_staff(demo.manager) as s:
        for vendor, prop, reference, dated, due, amount, memo, account in bills:
            made[reference] = payables.enter_bill(
                s,
                conn,
                payables.NewBill(
                    vendor_id=demo.vendors[vendor],
                    account=demo.accounts[prop],
                    reference=reference,
                    bill_date=min(dated, due),
                    due_on=due,
                    amount=amount,
                    memo=memo,
                    expense_account_id=demo.expense_accounts[account],
                ),
            ).id
        payables.add_recurring(
            s,
            conn,
            payables.NewRecurring(
                vendor_id=demo.vendors["Demo Landscaping Co"],
                account=demo.accounts["Harbor Tower"],
                reference_prefix="LAWN",
                amount=D("400.00"),
                memo="Monthly grounds service",
                # Due the day it comes: this month's is entered today, not overdue yet.
                day_of_month=min(today.day, 28),
                starts_on=this_month,
                expense_account_id=demo.expense_accounts["Landscaping"],
            ),
        )
    with b.as_staff(demo.manager) as s:
        payables.pay_bills(
            s,
            conn,
            payables.PayBills(
                on=on_or_before(entered + DAY, today), method="EFT", bill_ids=[made["ELC-3005"]]
            ),
        )
    # Tonight's run, as the manager runs it by hand: late fees on rent still open past its
    # grace, and this month's recurring bills.
    with b.as_staff(demo.manager):
        nightly.run_pmc(conn, demo.pmc_id, today, public_url)


def work_orders_at_every_step(b: Builder, demo: Demo, units: dict[str, UUID], today: date) -> None:
    """Six work orders, one at each step: just opened (urgent, due tomorrow), waiting on the
    electrician (whose insurance ran out, so they can't be assigned), assigned to the roofer
    (whose insurance runs out in 20 days), the plumber's visit scheduled for two days from now,
    completed with its bill, and cancelled."""
    conn = b.conn
    orders: list[
        tuple[str, str | None, str, work_orders.Details, list[tuple[WorkOrderStep, str | None]]]
    ] = [
        (
            "Harbor Tower",
            "Unit 203",
            "Smoke detector chirping",
            work_orders.Details(
                priority="urgent",
                description="The hallway smoke detector chirps every minute; likely the battery.",
                due_on=today + DAY,
                entry="call first",
            ),
            [],
        ),
        (
            "Oak Terrace",
            None,
            "Front walk light out",
            work_orders.Details(
                description="For the electrician, once their insurance is renewed.",
                due_on=today + 7 * DAY,
                entry="allowed",
            ),
            [],
        ),
        (
            "Cedar House",
            None,
            "Inspect roof after the storm",
            work_orders.Details(priority="high", description="Shingles found in the yard."),
            [("assigned", "Demo Roofing Co")],
        ),
        (
            "Harbor Tower",
            "Unit 305",
            "Water heater rumbles",
            work_orders.Details(priority="normal", entry="call first"),
            [("assigned", "Demo Plumbing Co"), ("scheduled", None)],
        ),
        (
            "Harbor Tower",
            "Unit 111",
            "Kitchen faucet drips",
            work_orders.Details(priority="low", entry="not allowed"),
            [("assigned", "Demo Plumbing Co"), ("completed", None)],
        ),
        ("Elm Court", None, "Repaint the mailbox", work_orders.Details(), [("cancelled", None)]),
    ]
    for prop, unit, summary, details, steps in orders:
        with b.as_staff(demo.manager) as s:
            opened = work_orders.open_work_order(
                s,
                conn,
                work_orders.NewWorkOrder(
                    property_id=property_of(conn, demo, prop),
                    unit_id=units[unit] if unit and prop == "Harbor Tower" else None,
                    summary=summary,
                    **details.model_dump(),
                ),
            )
        for step, vendor in steps:
            with b.as_staff(demo.manager) as s:
                maintenance.record_step(
                    s,
                    conn,
                    opened.id,
                    maintenance.Step(
                        step=step,
                        vendor_id=demo.vendors[vendor] if vendor else None,
                        scheduled_for=today + 2 * DAY if step == "scheduled" else None,
                    ),
                )


def files(b: Builder, demo: Demo) -> None:
    """A W-9 on file for the plumber, Demo Owner C's management agreement, a move-out photo of
    Harbor Tower's ended lease, the roof repair's receipt and a photo of the dripping faucet."""
    conn = b.conn
    roof = one(
        conn,
        "SELECT id FROM trust_bills WHERE pmc_id = %s AND reference = 'RF-2207'",
        (demo.pmc_id,),
    )
    faucet = one(
        conn,
        "SELECT id FROM trust_work_orders WHERE pmc_id = %s AND summary = 'Kitchen faucet drips'",
        (demo.pmc_id,),
    )
    kept: list[tuple[documents.Kind, UUID, str, documents.ContentType, bytes]] = [
        ("w9", demo.vendors["Demo Plumbing Co"], "W-9, Demo Plumbing Co", "application/pdf", PDF),
        (
            "owner_document",
            demo.owners["Demo Owner C"],
            "Management agreement",
            "application/pdf",
            PDF,
        ),
        (
            "move_out_photo",
            demo.leases[("Harbor Tower", HARBOR_ENDED)],
            "Kitchen at move-out",
            "image/png",
            PNG,
        ),
        ("receipt", roof, "Roof repair receipt", "application/pdf", PDF),
        ("work_order_photo", faucet, "Faucet before the repair", "image/png", PNG),
    ]
    with b.as_staff(demo.manager) as s:
        for kind, about, title, content_type, content in kept:
            documents.upload(
                s,
                conn,
                documents.NewDocument(
                    kind=kind,
                    about_id=about,
                    title=title,
                    content_type=content_type,
                    content_base64=base64.b64encode(content).decode(),
                ),
            )


def staff_and_signers(b: Builder, demo: Demo, since: date) -> None:
    """The manager is each trust bank account's broker and the bookkeeper a bonded signer of
    the operating one (docs/law.md); a leasing agent holds a custom role allowing leasing,
    maintenance and documents."""
    conn = b.conn
    with b.as_staff(demo.manager) as s:
        for bank in (demo.operating, demo.deposits):
            signers.change(
                s,
                conn,
                signers.Change(
                    bank_account_id=bank,
                    staff_id=demo.manager,
                    role="broker",
                    granted=True,
                    license_number="DEMO-LIC-1",
                    authorized_on=since,
                ),
            )
        signers.change(
            s,
            conn,
            signers.Change(
                bank_account_id=demo.operating,
                staff_id=demo.bookkeeper,
                role="signer",
                granted=True,
                bond_amount=D("25000.00"),
                bond_expires_on=date(since.year + 2, 12, 31),
                authorized_on=since,
            ),
        )
        demo.leasing_agent = staff.add(s, conn, staff.NewMember(name="Demo Leasing Agent")).id
        role = custom_roles.create(
            s,
            conn,
            custom_roles.NewRole(name="Leasing", areas=["leasing", "maintenance", "documents"]),
        ).id
        custom_roles.change_staff(
            s, conn, role, custom_roles.StaffChange(staff_id=demo.leasing_agent, granted=True)
        )


def renters_insurance(
    b: Builder, demo: Demo, s: accounts.Session, since: date, today: date
) -> None:
    """Cedar House's lease and Aspen Row's require renters insurance; Cedar House's tenants
    have a policy on file, with an insurer of their choosing, and Aspen Row's have none."""
    conn = b.conn
    for unit in (("Cedar House", "House"), ("Aspen Row", "Unit 1")):
        rent.renters_insurance_terms(
            s,
            conn,
            demo.leases[unit],
            rent.InsuranceTerms(required=True, minimum_liability=D("100000.00"), starts_on=since),
        )
    rent.record_policy(
        s,
        conn,
        demo.leases[("Cedar House", "House")],
        rent.NewPolicy(
            carrier="Demo Mutual Insurance",
            policy_number="RI-DEMO-2201",
            starts_on=since,
            expires_on=first_of_month(today, -3) - DAY,
            liability_limit=D("300000.00"),
            landlord_interested_party=True,
        ),
    )


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
        rent.run_rent(s, conn, rent.RunRent(due_on=month, late_fees_as_of=month))
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
        rent.run_rent(s, conn, rent.RunRent(due_on=month, late_fees_as_of=month + 6 * DAY))
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
            payables.pay_owners(
                s,
                conn,
                payables.PayOwners(
                    request_id=uuid.uuid4(),
                    on=end - DAY,
                    method="check",
                    payouts=[payables.Payout(account=account)],
                ),
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
        demo = build(conn, public_url=os.environ.get("PMS_PUBLIC_URL", "http://localhost:8000"))
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
    print("As the manager, open Banking: last month waits for your approval.")
    print("The Dashboard lists the rest that waits: a journal entry, the company's fees in")
    print("trust, a vendor's insurance running out and a lease without renters insurance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
