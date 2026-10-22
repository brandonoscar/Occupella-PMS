"""Property-based tests for leases, charges, payment matching and the rent roll.

Hypothesis opens and ends leases, charges rent, fees and credits, posts payments (from cash, a
tenant's prepaid rent, or a deposit kept with its cash), matches them to charges and bounces
some of them back. It also records management agreements, takes management and leasing fees,
and pays owners. All in random order, through both the owner role and trust_app. A Python
model predicts every step: the database must accept exactly what the model accepts and refuse
the rest with the model's reason.

Invariants checked after every step (ci/registry.toml maps each to this test):
  - two leases of one unit never overlap, and the leases are the model's;
  - no charge is paid past its amount (net of reversals), no transfer pays more than it moved,
    no reversal undoes more than its match or spends its transfer past its amount, and the
    matches and reversals are the model's, so a retry never adds one;
  - the rent roll for all time ties to the trust ledger: what it shows held for all tenants is
    every tenant's deposit and prepaid rent balance, current and not-current leases add up to
    all of them, and every lease's balance is its charges less its payments, net of reversals;
  - fees and draws are the model's: a management fee is the agreement's percent of the rent and
    recurring fees collected in its period (net of bounces), or its minimum, plus its flat fee,
    taken once per period with no two periods of an account overlapping; a leasing fee is taken
    once per lease; a draw never pays out more than the balance above the reserve, and its
    request key pays once;
  - an owner's contribution is recorded once per request key and never matched to a charge;
  - payment holds are the model's: a match of a payment dated on or after a hold's first day
    and before its release is refused unless that hold allowed the transfer, and a retry of a
    match made before still returns it; a release ends a hold once, after it starts;
  - rent and late fees are the model's: the rent run (run_the_rent) charges each lease in effect
    on the due date once, its rent for the days of the month it is in effect (monthly rent x
    days / days in the month, to the cent), and each lease starting later that month with no
    rent charged in the month yet its first part month, due on its first day; each rent still
    open when its grace ran out (unpaid, less its share of the lease's credits due by then,
    which come off its rent first, then its other charges, each oldest first: migration 0093)
    is charged one late fee, worked from what was open then under the terms in force on its due
    date. Its grace counts from its due date, or from the day it was charged if that came later
    (migration 0151): rents are made on their due date, ahead of it, or nights after it (a run
    that didn't run, a lease entered late), the day each was made set on the database (whose
    clock reads today) with helpers.charged_on;
  - recurring charges are the model's: the rent run charges each recurring charge in force on
    the due date (from its first day, before the day it stops) whose lease is in effect then,
    once per recurring charge and due date, as a fee or credit of its lease, for the days of the
    month the lease is in effect (its amount x days / days in the month, to the cent), and each
    in force on the first day of a lease starting later that month, with nothing charged for it
    in the month yet, its first part month, due on that day.

The rule check_the_owner_year_end reads an owner's year-end package for a random year and
compares each part (collected from tenants net of bounces, fees, draws, contributions, the
balances at the year's start and end, and the 1099-MISC rents) with the model. The rule
check_the_income_statement reads an owner's income statement over random days and compares
each line (collected by what each charge is, each of the company's charge types on a line of its
own once money was collected on it (migration 0100), the fees, net income and the Schedule E
lines) with the model. The rule
check_the_tenant_reports reads the delinquency report as of a random day and compares
every lease line with the model's aging, and checks a lease's tenant ledger closes at its
balance due. The rule check_owner_balances reads the owner balances report as of a random day
and compares it with the model. The rule check_the_lease_expirations reads the lease
expirations report as of a day near some lease's end and compares every line with the
model's leases. The rule check_the_rent_roll reads the roll as of a random day,
as any role including the AI's (trust_ai_agent), and compares every line with the model's: which
lease each unit is in, its tenants in byte order, the money held for them, what was charged and
paid by then, and the totals. The AI's role is refused every write it tries, and nothing
changes.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

import psycopg
import pytest
from helpers import (
    add_agreement,
    add_late_fee_policy,
    add_recurring_charge,
    add_tenant,
    add_unit,
    allow_payment,
    apply_payment,
    assess_late_fees,
    charge,
    charge_recurring_due,
    charge_rent_due,
    charged_on,
    contribute,
    draw_owner,
    end_lease,
    hold_payments,
    make_pmc,
    open_account,
    open_lease,
    post_leasing_fee,
    post_management_fee,
    release_hold,
    reverse_payment,
    stop_recurring_charge,
    transfer_batch,
)
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

CENT = Decimal("0.01")
AMOUNTS = st.integers(min_value=1, max_value=300_000).map(lambda cents: Decimal(cents) / 100)
BASE = date(2000, 1, 1)
DAYS = st.integers(min_value=0, max_value=400)
LAST_DAY = date(9999, 12, 31)  # stands for "no last day" when comparing
# A fee's charge type (migration 0100), if any.
TYPED = st.sampled_from([None, "Cleaning", "Pet rent"])
ROLES = st.sampled_from(["owner", "app"])
# A lease's length in days after its first day; None is month to month.
LENGTHS = st.one_of(st.integers(min_value=0, max_value=200), st.sampled_from([None, 0, 30, 364]))
TIMES = st.integers(min_value=0, max_value=86_399)  # seconds into the day
# Days after its due date a rent is made: mostly that day, as the nightly run makes it; now and
# then ahead of it (a run by hand for a later due date) or nights after it (a run that didn't
# run, a lease entered after it started).
CHARGED_AFTER = st.sampled_from([0, 0, 0, -4, 1, 6, 25])


@dataclass(frozen=True)
class Tenant:
    tenant_id: UUID
    name: str
    property_index: int
    deposit: str  # tenant_deposit ledger account
    prepaid: str  # prepaid_rent ledger account


@dataclass(frozen=True)
class Unit:
    unit_id: UUID
    name: str
    property_index: int


@dataclass
class Lease:
    unit: Unit
    starts_on: date
    ends_on: date | None
    rent: Decimal
    tenants: frozenset[UUID]

    def covers(self, day):
        return self.starts_on <= day <= (self.ends_on or LAST_DAY)


@dataclass(frozen=True)
class Charge:
    lease_id: UUID
    due_on: date
    kind: str
    amount: Decimal
    charge_type: str | None = None  # the company's type of a fee (migration 0100)
    # The day a rent was made (UTC): its grace counts from it when it came after its due date
    # (migration 0151). None for the rest, made on the database's clock and never charged late.
    charged_on: date | None = None


@dataclass
class Recurring:
    lease_id: UUID
    kind: str
    amount: Decimal
    starts_on: date
    ends_on: date | None = None  # the first due date it is no longer charged for
    charge_type: str | None = None  # the company's type of a fee (migration 0100)

    def in_force(self, day):
        return self.starts_on <= day < (self.ends_on or LAST_DAY)


@dataclass(frozen=True)
class Hold:
    lease_id: UUID
    starts_on: date
    ends_on: date | None
    reason: str


@dataclass(frozen=True)
class Transfer:
    source: str
    target: str
    amount: Decimal
    event_at: datetime


@dataclass(frozen=True)
class Terms:
    percent: Decimal
    minimum: Decimal
    flat: Decimal
    leasing: Decimal
    reserve: Decimal


@dataclass(frozen=True)
class LateTerms:
    grace: int
    flat: Decimal
    percent: Decimal
    maximum: Decimal | None


def month_start(n):
    """The first day of the n-th month after BASE."""
    return date(BASE.year + n // 12, n % 12 + 1, 1)


def to_cents(amount):
    """Round to cents, halves away from zero, as Postgres's round(numeric, 2) does."""
    return amount.quantize(CENT, ROUND_HALF_UP)


def for_days_in_month(lease, amount, day):
    """A monthly amount for the days the lease is in effect in day's month, to the cent, and
    whether that is only part of the month."""
    month_start = day.replace(day=1)
    month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    first = max(lease.starts_on, month_start)
    last = min(lease.ends_on or month_end, month_end)
    days, month_days = (last - first).days + 1, (month_end - month_start).days + 1
    return to_cents(amount * days / month_days), days < month_days


def rent_for_month(lease, day):
    """A lease's rent for the days it is in effect in day's month, and whether that is only
    part of the month."""
    return for_days_in_month(lease, lease.rent, day)


def overlaps(a_start, a_end, b_start, b_end):
    return a_start <= (b_end or LAST_DAY) and b_start <= (a_end or LAST_DAY)


class RentRollMachine(RuleBasedStateMachine):
    def __init__(self, owner_conn, app_conn, ai_conn):
        super().__init__()
        self.conns = {"owner": owner_conn, "app": app_conn, "ai": ai_conn}
        self.conn = owner_conn
        pmc = make_pmc(owner_conn, owners=2)
        self.pmc, self.pmc_id = pmc, pmc.pmc_id
        self.properties = [owner.property_id for owner in pmc.owners]
        self.property_names = ["Property 1", "Property 2"]
        self.owner_account = [owner.account for owner in pmc.owners]
        # Names that sort differently by byte and by language rules.
        self.units = [
            Unit(add_unit(owner_conn, self.pmc_id, self.properties[p], name), name, p)
            for name, p in [("Unit 1", 0), ("unit 2", 0), ("Unit 3", 1)]
        ]
        first = owner_conn.execute(
            "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
            (pmc.tenant_deposit,),
        ).fetchone()[0]
        self.tenants = [Tenant(first, "Tenant 1", 0, pmc.tenant_deposit, pmc.prepaid_rent)]
        for name, p in [("tenant 2", 0), ("Tenant 3", 1)]:
            tenant_id = add_tenant(owner_conn, self.pmc_id, self.properties[p], name)
            deposit = open_account(
                owner_conn, self.pmc_id, pmc.deposit_bank_id, "tenant_deposit", tenant_id=tenant_id
            )
            prepaid = open_account(
                owner_conn, self.pmc_id, pmc.operating_bank_id, "prepaid_rent", tenant_id=tenant_id
            )
            self.tenants.append(Tenant(tenant_id, name, p, deposit, prepaid))
        self.leases: dict[UUID, Lease] = {}
        self.charges: dict[UUID, Charge] = {}
        self.transfers: dict[str, Transfer] = {}
        self.matches: dict[tuple[UUID, str], Decimal] = {}
        # (charge, payment transfer, reversing transfer) -> amount undone
        self.reversals: dict[tuple[UUID, str, str], Decimal] = {}
        self.balance: dict[str, Decimal] = {}  # current balances of the accounts used
        self.owner_names = ["Owner 1", "Owner 2"]
        # The owner money loop: (account, first day) -> terms; (account, period start) ->
        # (period end, collected, fee); lease -> (account, fee); request key -> (account,
        # amount); and the transfers that paid owners.
        self.agreements: dict[tuple[str, date], Terms] = {}
        self.fees: dict[tuple[str, date], tuple[date, Decimal, Decimal]] = {}
        self.leasing: dict[UUID, tuple[str, Decimal]] = {}
        self.draws: dict[str, tuple[str, Decimal]] = {}
        self.draw_transfers: set[str] = set()
        # The transfers that took management and leasing fees.
        self.fee_transfers: set[str] = set()
        self.leasing_transfers: set[str] = set()
        # Rent automation: (property index, first day) -> late fee terms; rent charge ->
        # (unpaid when its grace ran out, fee, the fee's charge).
        self.late_terms: dict[tuple[int, date], LateTerms] = {}
        self.late_fees: dict[UUID, tuple[Decimal, Decimal, UUID]] = {}
        # Payment holds, and the (hold, transfer) pairs allowed despite one.
        self.holds: dict[UUID, Hold] = {}
        self.allowed: set[tuple[UUID, str]] = set()
        # Owners' contributions: request key -> (account, amount, transfer).
        self.contributions: dict[str, tuple[str, Decimal, str]] = {}
        # Recurring charges, and (recurring charge, due date) -> the charge the run made.
        self.recurring: dict[UUID, Recurring] = {}
        self.recurring_charged: dict[tuple[UUID, date], UUID] = {}
        # Lease ends: charge -> the credits and fees that followed its lease's end since.
        self.adjusted: dict[UUID, list[UUID]] = {}
        # The company's charge types (migration 0100): name -> id. Names that sort the same by
        # byte and by language rules, so the statement's order is the model's either way.
        self.charge_types = {
            name: owner_conn.execute(
                "INSERT INTO trust_charge_types (pmc_id, name) VALUES (%s, %s) RETURNING id",
                (self.pmc_id, name),
            ).fetchone()[0]
            for name in ("Cleaning", "Pet rent")
        }

    # --- helpers -------------------------------------------------------------------------

    def post(self, requests, event_at):
        """Post a batch as the owner, dated event_at; the model records each transfer."""
        ids = transfer_batch(self.conn, requests, event_at=event_at)
        for transfer_id, request in zip(ids, requests, strict=True):
            self.record(transfer_id, *request, event_at)
        return ids

    def record(self, transfer_id, source, target, amount, event_at):
        """A transfer the database posted: the model's copy."""
        self.transfers[transfer_id] = Transfer(source, target, amount, event_at)
        self.balance[source] = self.balance.get(source, Decimal(0)) - amount
        self.balance[target] = self.balance.get(target, Decimal(0)) + amount

    def agreement_on(self, account, day):
        """The terms in force for an account on a day: the latest agreement starting by it."""
        starts = [start for (held, start) in self.agreements if held == account and start <= day]
        return self.agreements[(account, max(starts))] if starts else None

    def collected(self, account, start, end):
        """Rent and recurring fees matched to payments into the account in [start, end), less
        what of them bounced back out of it in that time."""
        rent = {c for c, bill in self.charges.items() if bill.kind == "rent"}
        rent |= {c for c in self.recurring_charged.values() if self.charges[c].kind == "fee"}
        got = sum(
            (
                a
                for (c, t), a in self.matches.items()
                if c in rent
                and self.transfers[t].target == account
                and start <= self.transfers[t].event_at < end
            ),
            Decimal(0),
        )
        back = sum(
            (
                a
                for (c, _, r), a in self.reversals.items()
                if c in rent
                and self.transfers[r].source == account
                and start <= self.transfers[r].event_at < end
            ),
            Decimal(0),
        )
        return got - back

    def held_on(self, account, cutoff):
        return sum(
            (
                (t.amount if t.target == account else -t.amount)
                for t in self.transfers.values()
                if account in (t.source, t.target) and t.event_at < cutoff
            ),
            Decimal(0),
        )

    @staticmethod
    def moment(day, second):
        start = datetime.combine(BASE + timedelta(days=day), time(0), tzinfo=UTC)
        return start + timedelta(seconds=second)

    def unit_overlap(self, unit, starts_on, ends_on, lease_id=None):
        return any(
            other.unit == unit and overlaps(starts_on, ends_on, other.starts_on, other.ends_on)
            for other_id, other in self.leases.items()
            if other_id != lease_id
        )

    def paid_on(self, charge_id):
        """What is paid of a charge: matched, less what bounced back."""
        matched = sum((a for (c, _), a in self.matches.items() if c == charge_id), Decimal(0))
        undone = sum((a for (c, _, _), a in self.reversals.items() if c == charge_id), Decimal(0))
        return matched - undone

    def reversed_of(self, charge_id, transfer_id):
        return sum(
            (a for (c, t, _), a in self.reversals.items() if (c, t) == (charge_id, transfer_id)),
            Decimal(0),
        )

    def spent_of(self, reversal_id):
        return sum((a for (_, _, r), a in self.reversals.items() if r == reversal_id), Decimal(0))

    def used_of(self, transfer_id):
        return sum((a for (_, t), a in self.matches.items() if t == transfer_id), Decimal(0))

    def held_back(self, lease_id, transfer_id):
        """Is a hold on the lease in force on the transfer's day (UTC), not allowing it?"""
        day = self.transfers[transfer_id].event_at.astimezone(UTC).date()
        return any(
            hold.lease_id == lease_id
            and hold.starts_on <= day
            and (hold.ends_on is None or day < hold.ends_on)
            and (hold_id, transfer_id) not in self.allowed
            for hold_id, hold in self.holds.items()
        )

    def pays_for(self, transfer, lease):
        """Would this transfer pay a charge of this lease: into the owner's account for its
        property, from cash or money held for one of its tenants."""
        if transfer.target != self.owner_account[lease.unit.property_index]:
            return False
        held = {
            account
            for tenant in self.tenants
            if tenant.tenant_id in lease.tenants
            for account in (tenant.deposit, tenant.prepaid)
        }
        return transfer.source == self.pmc.operating_cash or transfer.source in held

    def held(self, cutoff):
        """Each tenant's (deposits, prepaid rent) from the transfers dated before cutoff."""
        out = {}
        for tenant in self.tenants:
            sums = []
            for account in (tenant.deposit, tenant.prepaid):
                change = Decimal(0)
                for t in self.transfers.values():
                    if t.event_at < cutoff:
                        change += t.amount if t.target == account else 0
                        change -= t.amount if t.source == account else 0
                sums.append(change)
            out[tenant.tenant_id] = tuple(sums)
        return out

    def expected_roll(self, as_of):
        cutoff = datetime.combine(as_of + timedelta(days=1), time(0), tzinfo=UTC)
        held = self.held(cutoff)
        names = {tenant.tenant_id: tenant.name for tenant in self.tenants}

        def charged(lease_id):
            return sum(
                (
                    -c.amount if c.kind == "credit" else c.amount
                    for c in self.charges.values()
                    if c.lease_id == lease_id and c.due_on <= as_of
                ),
                Decimal(0),
            )

        def paid(lease_id):
            matched = sum(
                (
                    amount
                    for (charge_id, transfer_id), amount in self.matches.items()
                    if self.charges[charge_id].lease_id == lease_id
                    and self.transfers[transfer_id].event_at < cutoff
                ),
                Decimal(0),
            )
            undone = sum(
                (
                    amount
                    for (charge_id, _, reversal_id), amount in self.reversals.items()
                    if self.charges[charge_id].lease_id == lease_id
                    and self.transfers[reversal_id].event_at < cutoff
                ),
                Decimal(0),
            )
            return matched - undone

        current = {
            lease.unit: (lease_id, lease)
            for lease_id, lease in self.leases.items()
            if lease.covers(as_of)
        }
        lines = []
        units = sorted(self.units, key=lambda u: (self.property_names[u.property_index], u.name))
        for unit in units:
            where = ("unit", self.property_names[unit.property_index], unit.name)
            if unit not in current:
                lines.append((*where, None, "vacant", *[None] * 6))
                continue
            lease_id, lease = current[unit]
            term = (
                f"{lease.starts_on:%Y-%m-%d}, month to month"
                if lease.ends_on is None
                else f"{lease.starts_on:%Y-%m-%d} to {lease.ends_on:%Y-%m-%d}"
            )
            tenants = sorted(names[t] for t in lease.tenants)
            ch, pd = charged(lease_id), paid(lease_id)
            lines.append(
                (
                    *where,
                    ", ".join(tenants),
                    term,
                    lease.rent,
                    sum((held[t][0] for t in lease.tenants), Decimal(0)),
                    sum((held[t][1] for t in lease.tenants), Decimal(0)),
                    ch,
                    pd,
                    ch - pd,
                )
            )

        on_current = {t for _, lease in current.values() for t in lease.tenants}
        current_ids = {lease_id for lease_id, _ in current.values()}

        def summary(tenant_ok, lease_ok):
            ch = sum((charged(i) for i in self.leases if lease_ok(i)), Decimal(0))
            pd = sum((paid(i) for i in self.leases if lease_ok(i)), Decimal(0))
            return (
                sum((h[0] for t, h in held.items() if tenant_ok(t)), Decimal(0)),
                sum((h[1] for t, h in held.items() if tenant_ok(t)), Decimal(0)),
                ch,
                pd,
                ch - pd,
            )

        rent = sum((lease.rent for _, lease in current.values()), Decimal(0))
        leased = f"{len(current)} of {len(self.units)} units leased"
        blank = (None, None, None)
        lines += [
            (
                "total: current leases",
                *blank,
                leased,
                rent,
                *summary(lambda t: t in on_current, lambda i: i in current_ids),
            ),
            (
                "not on a current lease",
                *blank,
                None,
                None,
                *summary(lambda t: t not in on_current, lambda i: i not in current_ids),
            ),
            ("total: all tenants", *blank, None, None, *summary(lambda t: True, lambda i: True)),
        ]
        return lines

    def roll(self, role, as_of):
        return (
            self.conns[role]
            .execute(
                "SELECT line, item, property, unit, tenants, detail, monthly_rent, deposits,"
                " prepaid, charged, paid, balance_due FROM trust_report_rent_roll(%s, %s)",
                (self.pmc_id, as_of),
            )
            .fetchall()
        )

    # --- rules: leases -------------------------------------------------------------------

    @rule(
        data=st.data(),
        role=ROLES,
        start=DAYS,
        length=LENGTHS,
        rent=AMOUNTS,
        stranger=st.booleans(),
    )
    def open_a_lease(self, data, role, start, length, rent, stranger):
        unit = data.draw(st.sampled_from(self.units))
        neighbors = [t for t in self.tenants if t.property_index == unit.property_index]
        tenants = data.draw(st.sets(st.sampled_from(neighbors), min_size=1))
        if stranger and data.draw(st.booleans()):  # now and then, another property's tenant
            others = [t for t in self.tenants if t.property_index != unit.property_index]
            tenants = tenants | {data.draw(st.sampled_from(others))}
        starts_on = BASE + timedelta(days=start)
        ends_on = None if length is None else starts_on + timedelta(days=length)
        ids = [t.tenant_id for t in tenants]

        def send():
            return open_lease(
                self.conns[role], self.pmc_id, unit.unit_id, starts_on, ends_on, rent, ids
            )

        if any(t.property_index != unit.property_index for t in tenants):
            with pytest.raises(psycopg.errors.InvalidParameterValue, match="tenant of the unit"):
                send()
        elif self.unit_overlap(unit, starts_on, ends_on):
            with pytest.raises(psycopg.errors.ExclusionViolation, match="overlap"):
                send()
        else:
            self.leases[send()] = Lease(unit, starts_on, ends_on, rent, frozenset(ids))

    @precondition(lambda self: self.leases)
    @rule(
        data=st.data(),
        role=ROLES,
        length=LENGTHS,
    )
    def end_a_lease(self, data, role, length):
        lease_id = data.draw(st.sampled_from(list(self.leases)))
        lease = self.leases[lease_id]
        ends_on = None if length is None else lease.starts_on + timedelta(days=length)
        if self.unit_overlap(lease.unit, lease.starts_on, ends_on, lease_id):
            with pytest.raises(psycopg.errors.ExclusionViolation, match="overlap"):
                end_lease(self.conns[role], self.pmc_id, lease_id, ends_on)
        else:
            old = replace(lease)
            end_lease(self.conns[role], self.pmc_id, lease_id, ends_on)
            lease.ends_on = ends_on
            self.follow_the_end(lease_id, old)

    def follow_the_end(self, lease_id, old):
        """trust_end_lease: each charge the run makes (rent, a recurring charge's) that, with
        what followed earlier ends, stands at its month's days under the old end gets a credit or
        a fee, due on its day, so it stands at its month's days under the new end."""
        lease = self.leases[lease_id]
        recurring_of = {c: r for (r, _), c in self.recurring_charged.items()}
        expected = set()
        for charge_id, c in self.charges.items():
            if c.lease_id != lease_id or (c.kind != "rent" and charge_id not in recurring_of):
                continue
            base = (
                self.recurring[recurring_of[charge_id]].amount if c.kind != "rent" else lease.rent
            )
            sign = -1 if c.kind == "credit" else 1
            net = sign * c.amount + sum(
                (
                    -self.charges[a].amount
                    if self.charges[a].kind == "credit"
                    else self.charges[a].amount
                    for a in self.adjusted.get(charge_id, [])
                ),
                Decimal(0),
            )
            was = sign * max(for_days_in_month(old, base, c.due_on)[0], Decimal(0))
            now = sign * max(for_days_in_month(lease, base, c.due_on)[0], Decimal(0))
            if net == was and now != was:
                kind = "credit" if now < was else "fee"
                expected.add((charge_id, kind, abs(now - was), c.due_on))
        if expected:
            event("lease end: a charged month followed the end")
        known = {a for adjusted in self.adjusted.values() for a in adjusted}
        made = {
            (charge_id, kind, amount, due_on): adjustment
            for adjustment, charge_id, kind, amount, due_on in self.conn.execute(
                "SELECT x.adjustment_charge_id, x.charge_id, a.kind, a.amount, a.due_on"
                " FROM trust_lease_end_adjustments AS x"
                " JOIN trust_charges AS a ON a.id = x.adjustment_charge_id"
                " WHERE a.lease_id = %s",
                (lease_id,),
            ).fetchall()
            if adjustment not in known
        }
        assert set(made) == expected
        for (charge_id, kind, amount, due_on), adjustment in made.items():
            self.charges[adjustment] = Charge(lease_id, due_on, kind, amount)
            self.adjusted.setdefault(charge_id, []).append(adjustment)

    # --- rules: charges and money --------------------------------------------------------

    @precondition(lambda self: self.leases)
    @rule(
        data=st.data(),
        role=ROLES,
        due=st.integers(min_value=-20, max_value=220),
        kind=st.sampled_from(["rent", "rent", "fee", "credit"]),
        amount=AMOUNTS,
        typed=TYPED,
        after=CHARGED_AFTER,
    )
    def charge_a_lease(self, data, role, due, kind, amount, typed, after):
        lease_id = data.draw(st.sampled_from(list(self.leases)))
        due_on = self.leases[lease_id].starts_on + timedelta(days=due)
        billed = any(
            c.lease_id == lease_id and c.due_on == due_on and c.kind == "rent"
            for c in self.charges.values()
        )
        if kind == "rent" and billed:  # a retried rent run
            with pytest.raises(psycopg.errors.UniqueViolation, match="one_rent_per_due_date"):
                charge(self.conns[role], self.pmc_id, lease_id, due_on, amount, kind)
        elif kind == "fee" and typed:  # a fee of one of the company's types
            charge_id = (
                self.conns[role]
                .execute(
                    "INSERT INTO trust_charges"
                    " (pmc_id, lease_id, due_on, kind, amount, memo, charge_type_id)"
                    " VALUES (%s, %s, %s, 'fee', %s, %s, %s) RETURNING id",
                    (self.pmc_id, lease_id, due_on, amount, typed, self.charge_types[typed]),
                )
                .fetchone()[0]
            )
            self.charges[charge_id] = Charge(lease_id, due_on, kind, amount, typed)
        elif kind == "rent":
            made = due_on + timedelta(days=after)
            charge_id = charge(
                self.conns[role],
                self.pmc_id,
                lease_id,
                due_on,
                amount,
                kind,
                charged_at=datetime.combine(made, time(6), tzinfo=UTC),
            )
            self.charges[charge_id] = Charge(lease_id, due_on, kind, amount, charged_on=made)
        else:
            charge_id = charge(self.conns[role], self.pmc_id, lease_id, due_on, amount, kind)
            self.charges[charge_id] = Charge(lease_id, due_on, kind, amount)

    @rule(
        data=st.data(),
        kind=st.sampled_from(["deposit", "prepaid"]),
        amount=AMOUNTS,
        day=DAYS,
        second=TIMES,
    )
    def hold_money_for_a_tenant(self, data, kind, amount, day, second):
        tenant = data.draw(st.sampled_from(self.tenants))
        when = self.moment(day, second)
        if kind == "deposit":
            self.post([(self.pmc.deposit_cash, tenant.deposit, amount)], when)
        else:
            self.post([(self.pmc.operating_cash, tenant.prepaid, amount)], when)

    @rule(
        data=st.data(),
        source=st.sampled_from(["cash", "cash", "prepaid", "deposit"]),
        into=st.sampled_from([0, 0, 1, "fees"]),
        amount=AMOUNTS,
        day=DAYS,
        second=TIMES,
    )
    def receive_a_payment(self, data, source, into, amount, day, second):
        """Money received into an owner's account (or, now and then, somewhere a payment of rent
        never goes) from cash, a tenant's prepaid rent, or a deposit kept with its cash."""
        to = self.pmc.pmc_income if into == "fees" else self.owner_account[into]
        tenant = data.draw(st.sampled_from(self.tenants))
        self.pay_in(source, tenant, to, amount, self.moment(day, second))

    def pay_in(self, source, tenant, to, amount, when):
        """Post a payment into `to`; from a tenant's prepaid rent or deposit, the money is first
        held for them (an hour earlier, so it is there by the payment's date). The payment's
        transfer id."""
        if source == "cash":
            return self.post([(self.pmc.operating_cash, to, amount)], when)[0]
        if source == "prepaid":
            self.post(
                [(self.pmc.operating_cash, tenant.prepaid, amount)], when - timedelta(hours=1)
            )
            return self.post([(tenant.prepaid, to, amount)], when)[0]
        self.post([(self.pmc.deposit_cash, tenant.deposit, amount)], when - timedelta(hours=1))
        # The kept deposit and its cash move together.
        return self.post(
            [
                (tenant.deposit, to, amount),
                (self.pmc.operating_cash, self.pmc.deposit_cash, amount),
            ],
            when,
        )[0]

    @precondition(lambda self: any(c.kind != "credit" for c in self.charges.values()))
    @rule(
        data=st.data(),
        role=ROLES,
        source=st.sampled_from(["cash", "prepaid", "deposit"]),
        share=st.sampled_from(["what is owed", "half", "a cent too much"]),
        day=DAYS,
        second=TIMES,
        retry=st.booleans(),
    )
    def pay_a_charge(self, data, role, source, share, day, second, retry):
        """A tenant on the lease pays a charge, and the payment is matched to it."""
        bills = [c for c, bill in self.charges.items() if bill.kind != "credit"]
        charge_id = data.draw(st.sampled_from(bills))
        lease = self.leases[self.charges[charge_id].lease_id]
        tenant = data.draw(
            st.sampled_from([t for t in self.tenants if t.tenant_id in lease.tenants])
        )
        owed = self.charges[charge_id].amount - self.paid_on(charge_id)
        amount = {
            "what is owed": owed,
            "half": (owed / 2).quantize(CENT),
            "a cent too much": owed + CENT,
        }[share]
        to = self.owner_account[lease.unit.property_index]
        transfer_id = self.pay_in(source, tenant, to, max(amount, CENT), self.moment(day, second))
        self.attempt_match(role, charge_id, transfer_id, amount)
        if retry:
            self.attempt_match(role, charge_id, transfer_id, amount)

    @precondition(lambda self: self.charges and self.transfers)
    @rule(data=st.data(), role=ROLES, nudge=st.sampled_from([Decimal(0), CENT, -CENT]))
    def match_a_payment(self, data, role, nudge):
        """Any charge with any transfer: mostly refused, now and then a second match."""
        charge_id = data.draw(st.sampled_from(list(self.charges)))
        transfer_id = data.draw(st.sampled_from(list(self.transfers)))
        existing = self.matches.get((charge_id, transfer_id))
        base = data.draw(
            st.sampled_from(
                [
                    self.charges[charge_id].amount - self.paid_on(charge_id),
                    self.transfers[transfer_id].amount - self.used_of(transfer_id),
                    existing if existing is not None else self.charges[charge_id].amount,
                ]
            )
        )
        self.attempt_match(role, charge_id, transfer_id, base + nudge)

    def attempt_match(self, role, charge_id, transfer_id, amount):
        """Match a payment as the model predicts: recorded, the original returned for a retry,
        or refused with the model's reason."""
        bill, paid = self.charges[charge_id], self.transfers[transfer_id]
        existing = self.matches.get((charge_id, transfer_id))

        def send():
            return apply_payment(self.conns[role], self.pmc_id, charge_id, transfer_id, amount)

        refusal = None
        if amount <= 0:
            refusal = (psycopg.errors.InvalidParameterValue, "positive amount")
        elif bill.kind == "credit":
            refusal = (psycopg.errors.InvalidParameterValue, "credit is not paid")
        elif any(t == transfer_id for _, _, t in self.contributions.values()):
            refusal = (psycopg.errors.InvalidParameterValue, "an owner's contribution")
        elif not self.pays_for(paid, self.leases[bill.lease_id]):
            refusal = (psycopg.errors.InvalidParameterValue, "did not pay into")
        elif existing is not None:
            if existing != amount:
                refusal = (psycopg.errors.UniqueViolation, "already pays")
        elif self.held_back(bill.lease_id, transfer_id):
            refusal = (psycopg.errors.CheckViolation, "is on a payment hold")
        elif self.paid_on(charge_id) + amount > bill.amount:
            refusal = (psycopg.errors.CheckViolation, "and already paid")
        elif self.used_of(transfer_id) + amount > paid.amount:
            refusal = (psycopg.errors.CheckViolation, "and already pays")

        if refusal is not None:
            with pytest.raises(refusal[0], match=refusal[1]):
                send()
        elif existing is not None:
            assert send() == existing  # a retry: the original match, nothing added
        else:
            assert send() == amount
            self.matches[(charge_id, transfer_id)] = amount

    # --- rules: owners' contributions ----------------------------------------------------

    @rule(
        role=ROLES,
        key=st.sampled_from([f"contribution-{n}" for n in range(4)]),
        owner=st.integers(min_value=0, max_value=1),
        amount=AMOUNTS,
        day=DAYS,
        second=TIMES,
    )
    def record_an_owners_contribution(self, role, key, owner, amount, day, second):
        """Money from an owner, under a small pool of request keys so retries come often."""
        account = self.owner_account[owner]
        when = self.moment(day, second)

        def send():
            return contribute(self.conns[role], self.pmc_id, account, key, amount, when)

        taken = self.contributions.get(key)
        if taken is None:
            transfer_id = send()
            self.record(transfer_id, self.pmc.operating_cash, account, amount, when)
            self.contributions[key] = (account, amount, transfer_id)
        elif taken[:2] == (account, amount):
            assert send() == taken[2]  # a retry: the original, nothing added
        else:
            with pytest.raises(psycopg.errors.UniqueViolation, match="already recorded"):
                send()

    @precondition(lambda self: self.contributions and self.charges)
    @rule(data=st.data(), role=ROLES)
    def try_to_match_a_contribution(self, data, role):
        """An owner's money is never a tenant's payment."""
        charge_id = data.draw(st.sampled_from(list(self.charges)))
        _, amount, transfer_id = data.draw(st.sampled_from(list(self.contributions.values())))
        self.attempt_match(
            role, charge_id, transfer_id, min(amount, self.charges[charge_id].amount)
        )

    # --- rules: payment holds ----------------------------------------------------------

    @precondition(lambda self: self.leases)
    @rule(data=st.data(), role=ROLES, day=DAYS)
    def place_a_payment_hold(self, data, role, day):
        """A hold from a random day or, half the time, from on or just before a day the lease
        was paid, so it catches payments still to be matched."""
        lease_id = data.draw(st.sampled_from(list(self.leases)))
        paid_on = [
            paid.event_at.astimezone(UTC).date()
            for paid in self.transfers.values()
            if self.pays_for(paid, self.leases[lease_id])
        ]
        starts_on = BASE + timedelta(days=day)
        if paid_on and data.draw(st.booleans()):
            starts_on = data.draw(st.sampled_from(paid_on)) - timedelta(
                days=data.draw(st.integers(0, 3))
            )
        reason = f"Filing {len(self.holds) + 1}"
        hold_id = hold_payments(self.conns[role], self.pmc_id, lease_id, starts_on, reason)
        self.holds[hold_id] = Hold(lease_id, starts_on, None, reason)

    @precondition(lambda self: self.holds)
    @rule(data=st.data(), role=ROLES, days=st.integers(min_value=-2, max_value=120))
    def release_a_payment_hold(self, data, role, days):
        hold_id = data.draw(st.sampled_from(list(self.holds)))
        hold = self.holds[hold_id]
        ends_on = hold.starts_on + timedelta(days=days)

        def send():
            release_hold(self.conns[role], self.pmc_id, hold_id, ends_on)

        if ends_on <= hold.starts_on:
            with pytest.raises(psycopg.errors.CheckViolation, match="must end it later"):
                send()
        elif hold.ends_on is not None:
            with pytest.raises(psycopg.errors.UniqueViolation):
                send()
        else:
            send()
            self.holds[hold_id] = replace(hold, ends_on=ends_on)

    @precondition(lambda self: self.holds and self.transfers)
    @rule(data=st.data(), role=ROLES)
    def allow_a_held_payment(self, data, role):
        hold_id = data.draw(st.sampled_from(list(self.holds)))
        transfer_id = data.draw(st.sampled_from(list(self.transfers)))

        def send():
            allow_payment(self.conns[role], self.pmc_id, hold_id, transfer_id)

        if (hold_id, transfer_id) in self.allowed:
            with pytest.raises(psycopg.errors.UniqueViolation):
                send()
        else:
            send()
            self.allowed.add((hold_id, transfer_id))

    @precondition(lambda self: self.holds and self.charges and self.transfers)
    @rule(data=st.data(), role=ROLES)
    def allow_a_held_payment_and_match_it(self, data, role):
        """A payment a hold refuses, allowed on every hold in force, then matched."""
        blocked = [
            (charge_id, transfer_id)
            for charge_id, bill in self.charges.items()
            if bill.kind != "credit"
            for transfer_id, paid in self.transfers.items()
            if self.pays_for(paid, self.leases[bill.lease_id])
            and (charge_id, transfer_id) not in self.matches
            and self.held_back(bill.lease_id, transfer_id)
        ]
        if not blocked:
            return
        charge_id, transfer_id = data.draw(st.sampled_from(blocked))
        lease_id = self.charges[charge_id].lease_id
        day = self.transfers[transfer_id].event_at.astimezone(UTC).date()
        for hold_id, hold in self.holds.items():
            in_force = hold.starts_on <= day and (hold.ends_on is None or day < hold.ends_on)
            if (
                hold.lease_id == lease_id
                and in_force
                and (hold_id, transfer_id) not in self.allowed
            ):
                allow_payment(self.conns[role], self.pmc_id, hold_id, transfer_id)
                self.allowed.add((hold_id, transfer_id))
        owed = self.charges[charge_id].amount - self.paid_on(charge_id)
        room = self.transfers[transfer_id].amount - self.used_of(transfer_id)
        self.attempt_match(role, charge_id, transfer_id, min(owed, room) or CENT)

    # --- rules: bounced payments --------------------------------------------------------

    @precondition(lambda self: self.matches)
    @rule(
        data=st.data(),
        role=ROLES,
        share=st.sampled_from(["what is left", "half", "a cent too much"]),
        delay=st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=30)),
        retry=st.booleans(),
    )
    def bounce_a_payment(self, data, role, share, delay, retry):
        """A matched payment comes back: the money moves back where it came from (a kept
        deposit with its cash), and the reversal is recorded against the match."""
        charge_id, transfer_id = data.draw(st.sampled_from(list(self.matches)))
        payment = self.transfers[transfer_id]
        left = self.matches[(charge_id, transfer_id)] - self.reversed_of(charge_id, transfer_id)
        if left <= 0 or self.balance.get(payment.target, Decimal(0)) < left:
            return
        back = [(payment.target, payment.source, left)]
        if payment.source in {t.deposit for t in self.tenants}:
            back.append((self.pmc.deposit_cash, self.pmc.operating_cash, left))
        reversal_id = self.post(back, payment.event_at + delay)[0]
        amount = {
            "what is left": left,
            "half": (left / 2).quantize(CENT),
            "a cent too much": left + CENT,
        }[share]
        self.attempt_reversal(role, charge_id, transfer_id, reversal_id, amount)
        if retry:
            self.attempt_reversal(role, charge_id, transfer_id, reversal_id, amount)

    @precondition(lambda self: self.matches)
    @rule(data=st.data(), role=ROLES, nudge=st.sampled_from([Decimal(0), CENT]))
    def reverse_with_any_transfer(self, data, role, nudge):
        """Any match with any transfer as its reversal: mostly refused (the wrong direction, or
        dated before the payment), now and then a second reversal or a retry."""
        charge_id, transfer_id = data.draw(st.sampled_from(list(self.matches)))
        reversal_id = data.draw(st.sampled_from(list(self.transfers)))
        existing = self.reversals.get((charge_id, transfer_id, reversal_id))
        left = self.matches[(charge_id, transfer_id)] - self.reversed_of(charge_id, transfer_id)
        base = data.draw(st.sampled_from([left, existing if existing is not None else left]))
        self.attempt_reversal(role, charge_id, transfer_id, reversal_id, base + nudge)

    def attempt_reversal(self, role, charge_id, transfer_id, reversal_id, amount):
        """Reverse a payment as the model predicts: recorded, the original returned for a
        retry, or refused with the model's reason."""
        payment, reversal = self.transfers[transfer_id], self.transfers[reversal_id]
        key = (charge_id, transfer_id, reversal_id)
        existing = self.reversals.get(key)

        def send():
            return reverse_payment(
                self.conns[role], self.pmc_id, charge_id, transfer_id, reversal_id, amount
            )

        refusal = None
        if amount <= 0:
            refusal = (psycopg.errors.InvalidParameterValue, "positive amount")
        elif reversal_id in self.draw_transfers:
            refusal = (psycopg.errors.InvalidParameterValue, "paid the owner")
        elif (reversal.source, reversal.target) != (payment.target, payment.source):
            refusal = (psycopg.errors.InvalidParameterValue, "back the way")
        elif reversal.event_at < payment.event_at:
            refusal = (psycopg.errors.InvalidParameterValue, "dated before")
        elif existing is not None:
            if existing != amount:
                refusal = (psycopg.errors.UniqueViolation, "already reverses")
        elif self.reversed_of(charge_id, transfer_id) + amount > self.matches[key[:2]]:
            refusal = (psycopg.errors.CheckViolation, "reversed already")
        elif self.spent_of(reversal_id) + amount > reversal.amount:
            refusal = (psycopg.errors.CheckViolation, "and already reverses")

        if refusal is not None:
            with pytest.raises(refusal[0], match=refusal[1]):
                send()
        elif existing is not None:
            assert send() == existing  # a retry: the original reversal, nothing added
        else:
            assert send() == amount
            self.reversals[key] = amount

    # --- rules: the owner money loop ------------------------------------------------------

    @rule(
        data=st.data(),
        role=ROLES,
        start=DAYS,
        percent=st.sampled_from(["0", "8", "10.5", "100"]),
        minimum=st.sampled_from(["0", "50.00"]),
        flat=st.sampled_from(["0", "10.00"]),
        leasing=st.sampled_from(["0", "50", "100"]),
        reserve=st.sampled_from(["0", "200.00", "1000.00"]),
    )
    def record_an_agreement(self, data, role, start, percent, minimum, flat, leasing, reserve):
        account = data.draw(st.sampled_from(self.owner_account))
        starts_on = BASE + timedelta(days=start)
        terms = Terms(*(Decimal(v) for v in (percent, minimum, flat, leasing, reserve)))

        def send():
            add_agreement(
                self.conns[role],
                self.pmc_id,
                account,
                starts_on,
                percent,
                minimum,
                flat,
                leasing,
                reserve,
            )

        if (account, starts_on) in self.agreements:  # new terms need a new day
            with pytest.raises(psycopg.errors.UniqueViolation):
                send()
        else:
            send()
            self.agreements[(account, starts_on)] = terms

    @rule(data=st.data(), role=ROLES, month=st.integers(0, 13), shifted=st.booleans())
    def take_a_management_fee(self, data, role, month, shifted):
        """A month's fee, or now and then one for the 15th to the 15th, which overlaps the
        months on either side."""
        account = data.draw(st.sampled_from(self.owner_account))
        shift = timedelta(days=14 if shifted else 0)
        self.attempt_fee(role, account, month_start(month) + shift, month_start(month + 1) + shift)

    @rule(
        data=st.data(),
        role=ROLES,
        day=st.integers(0, 27),
        percent=st.sampled_from(["8", "10.5", "100"]),
        rent_cents=st.integers(min_value=50_000, max_value=300_000),
        recurring_cents=st.one_of(st.none(), st.integers(min_value=1_000, max_value=20_000)),
    )
    def collect_rent_then_take_the_fee(self, data, role, day, percent, rent_cents, recurring_cents):
        """A month in one go: an agreement in force (recorded if none is), the month's rent on
        a lease of the property charged, paid in cash and matched, now and then a fee recurring
        on the lease (pet rent, parking) charged by the rent run and paid too, then the month's
        fee. The month is one with no fee taken yet, when there is one; with a recurring fee,
        one a lease of the property is in effect on the first day of, when there is one."""
        i = data.draw(st.integers(0, len(self.owner_account) - 1))
        account = self.owner_account[i]
        leases = [i_ for i_, lease in self.leases.items() if lease.unit.property_index == i]
        free = [
            k
            for k in range(14)
            if not any(
                a == account and s < month_start(k + 1) and month_start(k) < held[0]
                for (a, s), held in self.fees.items()
            )
        ]
        leased = [k for k in free if any(self.leases[i_].covers(month_start(k)) for i_ in leases)]
        if recurring_cents is not None and leased:
            free = leased
        month = data.draw(st.sampled_from(free or list(range(14))))
        start = month_start(month)
        if self.agreement_on(account, start) is None:
            add_agreement(self.conn, self.pmc_id, account, start, percent, "25.00", "5.00", "50")
            self.agreements[(account, start)] = Terms(
                Decimal(percent), Decimal("25.00"), Decimal("5.00"), Decimal(50), Decimal(0)
            )
        if leases:
            lease_id = data.draw(st.sampled_from(leases))
            rent = next(
                (
                    c
                    for c, bill in self.charges.items()
                    if bill.lease_id == lease_id and bill.kind == "rent" and bill.due_on == start
                ),
                None,
            )
            if rent is None:
                amount = Decimal(rent_cents) / 100
                rent = charge(
                    self.conn,
                    self.pmc_id,
                    lease_id,
                    start,
                    amount,
                    charged_at=datetime.combine(start, time(6), tzinfo=UTC),
                )
                self.charges[rent] = Charge(lease_id, start, "rent", amount, charged_on=start)
            owed = self.charges[rent].amount - self.paid_on(rent)
            if owed > 0:
                when = datetime.combine(start + timedelta(days=day), time(12), tzinfo=UTC)
                paid = self.pay_in("cash", self.tenants[0], account, owed, when)
                self.attempt_match(role, rent, paid, owed)
            # The rent run charges a lease in effect on the day, so the fee goes on one.
            covering = [i_ for i_ in leases if self.leases[i_].covers(start)]
            if recurring_cents is not None and covering:
                lease_id = data.draw(st.sampled_from(covering))
                amount = Decimal(recurring_cents) / 100
                self.pay_a_recurring_fee(role, lease_id, account, start, day, amount)
        self.attempt_fee(role, account, start, month_start(month + 1))

    def pay_a_recurring_fee(self, role, lease_id, account, start, day, amount):
        """A fee recurring on the lease from `start` (one is added if none is in force then),
        charged by the rent run on `start`, and what is owed of it paid in cash and matched."""
        if not any(
            r.lease_id == lease_id and r.kind == "fee" and r.in_force(start)
            for r in self.recurring.values()
        ):
            recurring_id = add_recurring_charge(
                self.conns[role], self.pmc_id, lease_id, amount, start, "Parking"
            )
            self.recurring[recurring_id] = Recurring(lease_id, "fee", amount, start)
        self.charge_recurring_on(role, start)
        for (recurring_id, due_on), charged in list(self.recurring_charged.items()):
            recurring = self.recurring[recurring_id]
            if due_on != start or recurring.lease_id != lease_id or recurring.kind != "fee":
                continue
            owed = self.charges[charged].amount - self.paid_on(charged)
            if owed > 0:
                when = datetime.combine(start + timedelta(days=day), time(13), tzinfo=UTC)
                paid = self.pay_in("cash", self.tenants[0], account, owed, when)
                self.attempt_match(role, charged, paid, owed)

    def attempt_fee(self, role, account, start, end):
        """Take the fee for [start, end) as the model predicts: taken, the fee already taken
        returned for a retry, or refused with the model's reason."""
        posted_at = datetime.combine(end, time(0), tzinfo=UTC) - timedelta(hours=1)

        def send():
            return post_management_fee(
                self.conns[role], self.pmc_id, account, start, end, posted_at
            )

        taken = [
            (s, held[0])
            for (a, s), held in self.fees.items()
            if a == account and s < end and start < held[0]
        ]
        terms = self.agreement_on(account, start)
        if taken and taken[0] == (start, end):
            assert send() == self.fees[(account, start)][2]  # a retry: the fee already taken
            return
        if taken:
            with pytest.raises(psycopg.errors.ExclusionViolation, match="overlaps"):
                send()
            return
        if terms is None:
            with pytest.raises(psycopg.errors.InvalidParameterValue, match="no management"):
                send()
            return
        utc = {d: datetime.combine(d, time(0), tzinfo=UTC) for d in (start, end)}
        collected = self.collected(account, utc[start], utc[end])
        fee = max(to_cents(terms.percent / 100 * collected), terms.minimum) + terms.flat
        if fee > self.balance.get(account, Decimal(0)):
            with pytest.raises(psycopg.errors.CheckViolation, match="below zero"):
                send()
            return
        assert send() == fee
        self.fees[(account, start)] = (end, collected, fee)
        if fee > 0:
            (transfer_id,) = self.conn.execute(
                "SELECT transfer_id FROM trust_management_fees"
                " WHERE ledger_account_id = %s AND period_start = %s",
                (account, start),
            ).fetchone()
            self.record(transfer_id, account, self.pmc.pmc_income, fee, posted_at)
            self.fee_transfers.add(transfer_id)

    @precondition(lambda self: self.leases)
    @rule(data=st.data(), role=ROLES, day=DAYS, second=TIMES, its_owner=st.booleans())
    def take_a_leasing_fee(self, data, role, day, second, its_owner):
        """From the owner of the lease's property, or now and then another owner's account."""
        lease_id = data.draw(st.sampled_from(list(self.leases)))
        lease, when = self.leases[lease_id], self.moment(day, second)
        account = (
            self.owner_account[lease.unit.property_index]
            if its_owner
            else data.draw(st.sampled_from(self.owner_account))
        )

        def send():
            return post_leasing_fee(self.conns[role], self.pmc_id, lease_id, account, when)

        taken = self.leasing.get(lease_id)
        terms = self.agreement_on(account, lease.starts_on)
        if self.owner_account[lease.unit.property_index] != account:
            refusal = (psycopg.errors.InvalidParameterValue, "lease's property")
        elif taken is not None:
            if taken[0] == account:
                assert send() == taken[1]  # a retry: the fee already taken
                return
            refusal = (psycopg.errors.UniqueViolation, "was taken from")
        elif terms is None:
            refusal = (psycopg.errors.InvalidParameterValue, "no management")
        else:
            fee = to_cents(terms.leasing / 100 * lease.rent)
            if fee > self.balance.get(account, Decimal(0)):
                refusal = (psycopg.errors.CheckViolation, "below zero")
            else:
                assert send() == fee
                self.leasing[lease_id] = (account, fee)
                if fee > 0:
                    (transfer_id,) = self.conn.execute(
                        "SELECT transfer_id FROM trust_leasing_fees WHERE lease_id = %s",
                        (lease_id,),
                    ).fetchone()
                    self.record(transfer_id, account, self.pmc.pmc_income, fee, when)
                    self.leasing_transfers.add(transfer_id)
                return
        with pytest.raises(refusal[0], match=refusal[1]):
            send()

    @rule(
        data=st.data(),
        role=ROLES,
        key=st.sampled_from(["draw-1", "draw-2", "draw-3"]),
        choice=st.sampled_from(["all", "what is available", "a cent too much", "half"]),
        day=DAYS,
        second=TIMES,
    )
    def pay_the_owner(self, data, role, key, choice, day, second):
        account = data.draw(st.sampled_from(self.owner_account))
        when = self.moment(day, second)
        terms = self.agreement_on(account, when.date())
        available = self.balance.get(account, Decimal(0)) - (terms.reserve if terms else 0)
        amount = {
            "all": None,
            "what is available": available,
            "a cent too much": available + CENT,
            "half": to_cents(available / 2),
        }[choice]

        def send():
            return draw_owner(self.conns[role], self.pmc_id, account, key, amount, when)

        paid = self.draws.get(key)
        if amount is not None and amount <= 0:
            refusal = (psycopg.errors.InvalidParameterValue, "positive amount")
        elif paid is not None:
            if paid[0] == account and amount in (None, paid[1]):
                assert send() == paid[1]  # a retry: the original draw, nothing more paid
                return
            refusal = (psycopg.errors.UniqueViolation, "already paid")
        elif available <= 0 or (amount if amount is not None else available) > available:
            refusal = (psycopg.errors.CheckViolation, "available")
        else:
            paying = amount if amount is not None else available
            assert send() == paying
            self.draws[key] = (account, paying)
            (transfer_id,) = self.conn.execute(
                "SELECT transfer_id FROM trust_owner_draws WHERE pmc_id = %s AND request_key = %s",
                (self.pmc_id, key),
            ).fetchone()
            self.record(transfer_id, account, self.pmc.operating_cash, paying, when)
            self.draw_transfers.add(transfer_id)
            return
        with pytest.raises(refusal[0], match=refusal[1]):
            send()

    # --- rules: rent automation ----------------------------------------------------------

    @rule(
        role=ROLES,
        property_index=st.integers(0, 1),
        start=DAYS,
        grace=st.sampled_from([0, 3, 10]),
        flat=st.sampled_from(["0", "25.00"]),
        percent=st.sampled_from(["0", "5", "10"]),
        maximum=st.sampled_from([None, "0", "40.00"]),
    )
    def record_late_fee_terms(self, role, property_index, start, grace, flat, percent, maximum):
        starts_on = BASE + timedelta(days=start)

        def send():
            add_late_fee_policy(
                self.conns[role],
                self.pmc_id,
                self.properties[property_index],
                starts_on,
                grace,
                flat,
                percent,
                maximum,
            )

        if (property_index, starts_on) in self.late_terms:  # new terms need a new day
            with pytest.raises(psycopg.errors.UniqueViolation):
                send()
            return
        send()
        self.late_terms[(property_index, starts_on)] = LateTerms(
            grace, Decimal(flat), Decimal(percent), None if maximum is None else Decimal(maximum)
        )

    def late_terms_on(self, property_index, day):
        starts = [s for (p, s) in self.late_terms if p == property_index and s <= day]
        return self.late_terms[(property_index, max(starts))] if starts else None

    @precondition(lambda self: self.leases)
    @rule(data=st.data(), role=ROLES, day=DAYS, after=CHARGED_AFTER)
    def run_the_rent(self, data, role, day, after):
        """Usually on a day some lease is in effect, or starts later in the month (the first of
        its month), or some recurring charge starts or stops, so the run charges something. The
        staff rent run charges the rent and then the recurring charges, on the due date or some
        days before or after it."""
        covered = sorted(
            {d for lease in self.leases.values() for d in (lease.starts_on, lease.ends_on) if d}
            | {lease.starts_on.replace(day=1) for lease in self.leases.values()}
            | {d for r in self.recurring.values() for d in (r.starts_on, r.ends_on) if d}
        )
        due_on = data.draw(st.sampled_from([*covered, BASE + timedelta(days=day)]))
        self.charge_rent_on(role, due_on, due_on + timedelta(days=after))
        self.charge_recurring_on(role, due_on)

    @precondition(lambda self: self.recurring)
    @rule(data=st.data(), role=ROLES, after=st.integers(min_value=0, max_value=90))
    def run_the_rent_while_a_charge_recurs(self, data, role, after):
        """The rent run on a day after some recurring charge's first day, so it is usually in
        force then; now and then the same day twice, as a retried run."""
        recurring = self.recurring[data.draw(st.sampled_from(list(self.recurring)))]
        due_on = recurring.starts_on + timedelta(days=after)
        for _ in range(data.draw(st.integers(min_value=1, max_value=2))):
            self.charge_rent_on(role, due_on)
            self.charge_recurring_on(role, due_on)

    def recurring_for_month(self, recurring_id, day):
        """A recurring charge's amount for the days its lease is in effect in day's month, and
        whether that is only part of the month."""
        recurring = self.recurring[recurring_id]
        return for_days_in_month(self.leases[recurring.lease_id], recurring.amount, day)

    def charge_recurring_on(self, role, due_on):
        """The rent run's recurring charges: each in force on due_on whose lease is in effect
        then, for the days of the month the lease is in effect, due that day; then each in force
        on the first day of a lease starting later that month, with nothing charged for it in
        the month yet, its first part month, due on that day."""
        month = (due_on.year, due_on.month)
        due = [
            (recurring_id, due_on)
            for recurring_id, recurring in self.recurring.items()
            if recurring.in_force(due_on)
            and self.leases[recurring.lease_id].covers(due_on)
            and self.recurring_for_month(recurring_id, due_on)[0] > 0
            and (recurring_id, due_on) not in self.recurring_charged
        ]
        first = [
            (recurring_id, starts_on)
            for recurring_id, recurring in self.recurring.items()
            for starts_on in [self.leases[recurring.lease_id].starts_on]
            if starts_on > due_on
            and (starts_on.year, starts_on.month) == month
            and recurring.in_force(starts_on)
            and self.recurring_for_month(recurring_id, starts_on)[0] > 0
            and not any(
                charged == recurring_id and (day.year, day.month) == month
                for charged, day in self.recurring_charged
            )
        ]
        if first:
            event("recurring run: a first part month charged")
        if any(self.recurring_for_month(r, d)[1] for r, d in due):
            event("recurring run: a part month in effect on the due date")

        assert charge_recurring_due(self.conns[role], self.pmc_id, due_on) == len(due) + len(first)

        for recurring_id, day in due + first:
            recurring = self.recurring[recurring_id]
            (charge_id, amount, memo) = self.conn.execute(
                "SELECT id, amount, memo FROM trust_charges"
                " WHERE recurring_charge_id = %s AND due_on = %s",
                (recurring_id, day),
            ).fetchone()
            expected, part = self.recurring_for_month(recurring_id, day)
            assert amount == expected
            assert memo.startswith(f"Parking due {day}") and (", " in memo) == part
            self.charges[charge_id] = Charge(recurring.lease_id, day, recurring.kind, expected)
            self.recurring_charged[(recurring_id, day)] = charge_id

    @precondition(lambda self: self.leases)
    @rule(
        data=st.data(),
        role=ROLES,
        start=st.one_of(st.just(0), st.integers(min_value=-20, max_value=220)),
        kind=st.sampled_from(["fee", "fee", "credit"]),
        amount=AMOUNTS,
        typed=TYPED,
    )
    def add_a_recurring_charge(self, data, role, start, kind, amount, typed):
        lease_id = data.draw(st.sampled_from(list(self.leases)))
        starts_on = self.leases[lease_id].starts_on + timedelta(days=start)
        typed = typed if kind == "fee" else None
        recurring_id = (
            self.conns[role]
            .execute(
                "INSERT INTO trust_recurring_charges"
                " (pmc_id, lease_id, kind, amount, memo, starts_on, charge_type_id)"
                " VALUES (%s, %s, %s, %s, 'Parking', %s, %s) RETURNING id",
                (
                    self.pmc_id,
                    lease_id,
                    kind,
                    amount,
                    starts_on,
                    self.charge_types[typed] if typed else None,
                ),
            )
            .fetchone()[0]
        )
        self.recurring[recurring_id] = Recurring(lease_id, kind, amount, starts_on, None, typed)

    @precondition(lambda self: self.recurring)
    @rule(data=st.data(), role=ROLES, after=st.integers(min_value=-10, max_value=220))
    def stop_a_recurring_charge(self, data, role, after):
        recurring_id = data.draw(st.sampled_from(list(self.recurring)))
        recurring = self.recurring[recurring_id]
        ends_on = recurring.starts_on + timedelta(days=after)
        if recurring.ends_on is not None:
            with pytest.raises(psycopg.errors.UniqueViolation, match="ends_pkey"):
                stop_recurring_charge(self.conns[role], self.pmc_id, recurring_id, ends_on)
        else:
            stop_recurring_charge(self.conns[role], self.pmc_id, recurring_id, ends_on)
            recurring.ends_on = ends_on

    def charge_rent_on(self, role, due_on, on=None):
        """The rent run: each lease in effect on due_on its rent for the days of the month it is
        in effect, due that day; then each lease starting later that month, with no rent charged
        in the month yet, its first part month, due on its first day. It runs on `on` (due_on
        unless named): the rents it makes are said to have been made that day."""
        on = on or due_on
        month = (due_on.year, due_on.month)
        due = [
            (lease_id, due_on)
            for lease_id, lease in self.leases.items()
            if lease.covers(due_on)
            and rent_for_month(lease, due_on)[0] > 0
            and not any(
                c.lease_id == lease_id and c.due_on == due_on and c.kind == "rent"
                for c in self.charges.values()
            )
        ]
        first = [
            (lease_id, lease.starts_on)
            for lease_id, lease in self.leases.items()
            if lease.starts_on > due_on
            and (lease.starts_on.year, lease.starts_on.month) == month
            and rent_for_month(lease, lease.starts_on)[0] > 0
            and not any(
                c.lease_id == lease_id
                and c.kind == "rent"
                and (c.due_on.year, c.due_on.month) == month
                for c in self.charges.values()
            )
        ]
        if first:
            event("rent run: a first part month charged")
        if any(rent_for_month(self.leases[i], d)[1] for i, d in due):
            event("rent run: a part month in effect on the due date")

        assert charge_rent_due(self.conns[role], self.pmc_id, due_on) == len(due) + len(first)

        for lease_id, day in due + first:
            (charge_id, amount, memo) = self.conn.execute(
                "SELECT id, amount, memo FROM trust_charges WHERE lease_id = %s AND due_on = %s"
                " AND kind = 'rent'",
                (lease_id, day),
            ).fetchone()
            rent, part = rent_for_month(self.leases[lease_id], day)
            assert amount == rent
            assert memo.startswith(f"Rent due {day}") and (", " in memo) == part
            charged_on(self.conn, [charge_id], on)
            self.charges[charge_id] = Charge(lease_id, day, "rent", rent, charged_on=on)

    def unpaid_by(self, charge_id, cutoff):
        """A charge less its matches dated before cutoff, plus what of them bounced back
        before it."""
        paid = sum(
            (
                a
                for (c, t), a in self.matches.items()
                if c == charge_id and self.transfers[t].event_at < cutoff
            ),
            Decimal(0),
        )
        back = sum(
            (
                a
                for (c, _, r), a in self.reversals.items()
                if c == charge_id and self.transfers[r].event_at < cutoff
            ),
            Decimal(0),
        )
        return self.charges[charge_id].amount - paid + back

    def pay_rank(self, charge_id):
        """Where a charge comes in the order money pays a lease's charges (migration 0093): 0
        its rent, 1 a monthly charge (a recurring fee, or a lease end's fee for days back of
        either), 2 the rest."""
        bill = self.charges[charge_id]
        if bill.kind == "rent":
            return 0
        monthly = set(self.recurring_charged.values())
        if bill.kind == "fee" and (
            charge_id in monthly
            or any(
                charge_id in adjustments
                and (self.charges[original].kind == "rent" or original in monthly)
                for original, adjustments in self.adjusted.items()
            )
        ):
            return 1
        return 2

    def pay_order(self, charge_id):
        """Rent and monthly charges first, then the rest, each by due date. The database breaks
        a tie of the same place and day by when each was charged, then id; no figure checked
        here turns on it: a rent is the only one of its place on its day, and the aging sums
        what is due on a day together."""
        rank = self.pay_rank(charge_id)
        return (rank == 2, self.charges[charge_id].due_on, rank, charge_id)

    def open_by(self, charge_id, cutoff):
        """What is open of a charge at cutoff: unpaid by then, less its share of its lease's
        credits due before cutoff's day, which come off the lease's open charges rent first
        (pay_order): what the credits leave once everything before it is owed, a running
        total, so a charge paid twice by then (paid again before a bounce dated later) lowers
        what is owed before the charges after it."""
        lease_id = self.charges[charge_id].lease_id
        mine = {c: b for c, b in self.charges.items() if b.lease_id == lease_id}
        credits = sum(
            (b.amount for b in mine.values() if b.kind == "credit" and b.due_on < cutoff.date()),
            Decimal(0),
        )
        before = Decimal(0)
        for c in sorted((c for c, b in mine.items() if b.kind != "credit"), key=self.pay_order):
            left = self.unpaid_by(c, cutoff)
            if c == charge_id:
                return left - min(left, max(credits - before, Decimal(0)))
            before += left
        raise AssertionError("a charge that is not a credit is open or not")

    @rule(role=ROLES, day=st.integers(min_value=0, max_value=640))
    def assess_late_fees_as_of(self, role, day):
        self.assess(role, BASE + timedelta(days=day))

    @precondition(lambda self: self.leases)
    @rule(
        data=st.data(),
        role=ROLES,
        after=st.integers(min_value=0, max_value=60),
        grace=st.sampled_from([0, 3]),
        paid=st.sampled_from(["nothing", "half", "all", "all, a second late"]),
        late_by=CHARGED_AFTER,
    )
    def let_a_rent_go_late(self, data, role, after, grace, paid, late_by):
        """The usual late month: late fee terms in force (recorded if none are), the rent run
        for a day the lease is in effect, on that day or some days before or after it, a payment
        dated just before the grace runs out or just after, then the late fee run."""
        lease_id = data.draw(st.sampled_from(list(self.leases)))
        lease = self.leases[lease_id]
        due_on = lease.starts_on + timedelta(days=after)
        if not lease.covers(due_on):
            due_on = lease.starts_on
        place = lease.unit.property_index
        if self.late_terms_on(place, due_on) is None:
            add_late_fee_policy(
                self.conn, self.pmc_id, self.properties[place], due_on, grace, "25.00", "10"
            )
            self.late_terms[(place, due_on)] = LateTerms(grace, Decimal(25), Decimal(10), None)
        self.charge_rent_on(role, due_on, due_on + timedelta(days=late_by))
        rent = next(
            (
                c
                for c, bill in self.charges.items()
                if bill.lease_id == lease_id and bill.due_on == due_on and bill.kind == "rent"
            ),
            None,
        )
        if rent is None:
            event("late month: a day's rent rounds to nothing, none charged")
            return
        terms = self.late_terms_on(place, due_on)
        late_day = self.grace_from(rent) + timedelta(days=terms.grace + 1)
        if late_day > due_on + timedelta(days=terms.grace + 1):
            event("late month: the rent was charged after its due date")
        cutoff = datetime.combine(late_day, time(0), tzinfo=UTC)
        owed = self.charges[rent].amount - self.paid_on(rent)
        amount = {"nothing": 0, "half": to_cents(owed / 2)}.get(paid, owed)
        if amount > 0:
            when = cutoff if paid.endswith("late") else cutoff - timedelta(seconds=1)
            payment = self.pay_in("cash", self.tenants[0], self.owner_account[place], amount, when)
            self.attempt_match(role, rent, payment, amount)
        self.assess(role, late_day + timedelta(days=data.draw(st.integers(0, 5))))

    def grace_from(self, rent_id):
        """The first day of a rent's grace: its due date, or the day it was charged if that came
        later (migration 0151)."""
        rent = self.charges[rent_id]
        return max(rent.due_on, rent.charged_on)

    def assess(self, role, as_of):
        """The late fee run: the rents past their grace, in the order their grace ends (then by
        due date, then id), each seeing the fees charged before it, as a run every day would."""
        due = []
        for charge_id, rent in self.charges.items():
            if rent.kind != "rent" or charge_id in self.late_fees:
                continue
            terms = self.late_terms_on(self.leases[rent.lease_id].unit.property_index, rent.due_on)
            last = None if terms is None else self.grace_from(charge_id) + timedelta(terms.grace)
            if last is None or last >= as_of:
                continue
            due.append((last + timedelta(days=1), rent.due_on, charge_id, terms))

        assessed = assess_late_fees(self.conns[role], self.pmc_id, as_of)
        # The fee charges the run made, by rent, so the model orders them by the same ids; a
        # rent the run left alone gets an id of its own, and the late fee check tells them apart.
        fee_ids = dict(
            self.conn.execute(
                "SELECT rent_charge_id, fee_charge_id FROM trust_late_fees WHERE pmc_id = %s",
                (self.pmc_id,),
            ).fetchall()
        )
        charged = 0
        for late_day, _, charge_id, terms in sorted(due, key=lambda d: d[:3]):
            unpaid = self.open_by(charge_id, datetime.combine(late_day, time(0), tzinfo=UTC))
            if unpaid <= 0:
                continue
            fee = to_cents(terms.flat + terms.percent / 100 * unpaid)
            if terms.maximum is not None:
                fee = min(fee, terms.maximum)
            if fee > 0:
                fee_id = fee_ids.get(charge_id) or uuid4()
                self.late_fees[charge_id] = (unpaid, fee, fee_id)
                # Its fee is open from now on: the lease's credits may come off it before the
                # rents whose grace ends later.
                lease_id = self.charges[charge_id].lease_id
                self.charges[fee_id] = Charge(lease_id, late_day, "fee", fee)
                charged += 1

        assert assessed == charged

    # --- rules: the reports --------------------------------------------------------------

    @rule(data=st.data(), role=st.sampled_from(["owner", "app", "ai"]), owner=st.integers(0, 1))
    def check_the_owner_year_end(self, data, role, owner):
        """An owner's year-end package for a year (UTC), part by part, as the model has it:
        the owner's one property account here, so its lines and the totals are the same.
        Mostly a year its account moved in, now and then one either side."""
        account = self.owner_account[owner]
        years = sorted(
            {
                t.event_at.astimezone(UTC).year
                for t in self.transfers.values()
                if account in (t.source, t.target)
            }
        ) or [BASE.year]
        if data.draw(st.integers(0, 3)) == 0:
            years = [*range(years[0] - 1, years[-1] + 2)]
        self.year_end_is_the_models(role, owner, data.draw(st.sampled_from(years)))

    @precondition(lambda self: self.matches or self.fee_transfers or self.leasing_transfers)
    @rule(data=st.data(), role=st.sampled_from(["owner", "app", "ai"]))
    def check_the_year_end_of_a_payment_or_fee(self, data, role):
        """The year-end package of the year a payment was matched or a fee taken, for the
        owner whose account it moved."""
        moving = sorted({t for _, t in self.matches} | self.fee_transfers | self.leasing_transfers)
        transfer = self.transfers[data.draw(st.sampled_from(moving))]
        for owner, account in enumerate(self.owner_account):
            if account in (transfer.source, transfer.target):
                self.year_end_is_the_models(role, owner, transfer.event_at.astimezone(UTC).year)

    def year_end_is_the_models(self, role, owner, year):
        account = self.owner_account[owner]
        start = datetime(year, 1, 1, tzinfo=UTC)
        end = datetime(year + 1, 1, 1, tzinfo=UTC)

        def moved(ids, sign=-1):
            return sum(
                (
                    sign * self.transfers[t].amount
                    for t in ids
                    if account in (self.transfers[t].source, self.transfers[t].target)
                    and start <= self.transfers[t].event_at < end
                ),
                Decimal(0),
            )

        collected = sum(
            (
                a
                for (_, t), a in self.matches.items()
                if self.transfers[t].target == account and start <= self.transfers[t].event_at < end
            ),
            Decimal(0),
        ) - sum(
            (
                a
                for (_, _, r), a in self.reversals.items()
                if self.transfers[r].source == account and start <= self.transfers[r].event_at < end
            ),
            Decimal(0),
        )
        contributed = [t for (held, _, t) in self.contributions.values() if held == account]
        opening, closing = self.held_on(account, start), self.held_on(account, end)
        parts = [
            ("opening balance", opening),
            ("collected from tenants", collected),
            ("management fees", moved(self.fee_transfers)),
            ("leasing fees", moved(self.leasing_transfers)),
            ("bills paid", Decimal(0)),
            ("owner draws", moved(self.draw_transfers)),
            ("owner contributions", moved(contributed, 1)),
        ]
        moved_total = sum((a for _, a in parts[1:]), Decimal(0))
        parts += [("other", closing - opening - moved_total), ("closing balance", closing)]
        rows = (
            self.conns[role]
            .execute(
                "SELECT property, item, amount FROM trust_report_owner_year_end(%s, %s, %s)"
                " WHERE line > 3",
                (self.pmc_id, self.pmc.owners[owner].owner_id, year),
            )
            .fetchall()
        )
        name = self.property_names[owner]
        assert rows == [
            *((name, item, amount) for item, amount in parts),
            *((None, f"all properties: {item}", amount) for item, amount in parts),
            (None, "1099-MISC rents (box 1)", collected),
        ]

    @rule(data=st.data(), role=st.sampled_from(["owner", "app", "ai"]), owner=st.integers(0, 1))
    def check_the_income_statement(self, data, role, owner):
        """An owner's income statement over random days (UTC), line by line, as the model has
        it: what was collected (matched payments less what bounced back) by what each charge
        is (rent, a recurring charge, a late fee, another charge; a lease end's adjustment is
        what it adjusts), the management and leasing fees, net income, and the Schedule E
        lines. The owner's one property account here, so its lines and the totals agree."""
        days = sorted({t.event_at.astimezone(UTC).date() for t in self.transfers.values()}) or [
            BASE
        ]
        first = data.draw(st.sampled_from(days)) + timedelta(days=data.draw(st.integers(-3, 1)))
        last = data.draw(st.sampled_from(days)) + timedelta(days=data.draw(st.integers(-1, 3)))
        if last < first:
            first, last = last, first
        self.income_statement_is_the_models(role, owner, first, last)

    @precondition(lambda self: self.leases)
    @rule(
        data=st.data(),
        role=ROLES,
        typed=st.sampled_from(["Cleaning", "Pet rent"]),
        monthly=st.booleans(),
        cents=st.integers(min_value=100, max_value=50_000),
        day=st.integers(0, 27),
    )
    def collect_a_typed_fee_and_read_the_statement(self, data, role, typed, monthly, cents, day):
        """A fee of one of the company's types (migration 0100) on a lease, once or every month
        from the lease's first day (charged by the rent run then), paid in cash and matched,
        then the owner's income statement over the day it was paid."""
        lease_id = data.draw(st.sampled_from(list(self.leases)))
        lease = self.leases[lease_id]
        owner = lease.unit.property_index
        amount = Decimal(cents) / 100
        if monthly:
            due_on = lease.starts_on
            recurring_id = (
                self.conns[role]
                .execute(
                    "INSERT INTO trust_recurring_charges"
                    " (pmc_id, lease_id, kind, amount, memo, starts_on, charge_type_id)"
                    " VALUES (%s, %s, 'fee', %s, 'Parking', %s, %s) RETURNING id",
                    (self.pmc_id, lease_id, amount, due_on, self.charge_types[typed]),
                )
                .fetchone()[0]
            )
            self.recurring[recurring_id] = Recurring(lease_id, "fee", amount, due_on, None, typed)
            self.charge_recurring_on(role, due_on)
            charge_id = self.recurring_charged.get((recurring_id, due_on))
            if charge_id is None:  # nothing to charge for its month (the lease's own rules)
                return
        else:
            due_on = lease.starts_on + timedelta(days=day)
            charge_id = (
                self.conns[role]
                .execute(
                    "INSERT INTO trust_charges"
                    " (pmc_id, lease_id, due_on, kind, amount, memo, charge_type_id)"
                    " VALUES (%s, %s, %s, 'fee', %s, %s, %s) RETURNING id",
                    (self.pmc_id, lease_id, due_on, amount, typed, self.charge_types[typed]),
                )
                .fetchone()[0]
            )
            self.charges[charge_id] = Charge(lease_id, due_on, "fee", amount, typed)
        owed = self.charges[charge_id].amount - self.paid_on(charge_id)
        when = datetime.combine(due_on + timedelta(days=day), time(12), tzinfo=UTC)
        paid = self.pay_in("cash", self.tenants[0], self.owner_account[owner], owed, when)
        self.attempt_match(role, charge_id, paid, owed)
        self.income_statement_is_the_models(role, owner, when.date(), when.date())

    def all_owners_statement_is_each_owners(self, role, first, last):
        """The income statement across every owner (q108): each owner's property account's
        lines as that owner's own statement has them, named by its property and owner, and each
        line across them all the sum of theirs."""
        conn = self.conns[role]
        everyone = conn.execute(
            "SELECT property, item, schedule_e, amount"
            " FROM trust_report_income_statement_all_owners(%s, %s, %s) WHERE line > 3",
            (self.pmc_id, first, last),
        ).fetchall()
        totals: dict[str, tuple[int | None, Decimal]] = {}
        for i, owner in enumerate(self.pmc.owners):
            own = conn.execute(
                "SELECT item, schedule_e, amount FROM trust_report_income_statement(%s, %s, %s, %s)"
                " WHERE line > 3 AND property IS NOT NULL",
                (self.pmc_id, owner.owner_id, first, last),
            ).fetchall()
            named = f"{self.property_names[i]}, {self.owner_names[i]}"
            assert [(item, line, amount) for p, item, line, amount in everyone if p == named] == own
            for item, line, amount in own:
                totals[item] = (line, totals.get(item, (line, Decimal(0)))[1] + amount)
        across = {
            item.removeprefix("all properties: "): (line, amount)
            for p, item, line, amount in everyone
            if p is None
        }
        assert across == totals
        assert len(everyone) == len(across) + sum(1 for row in everyone if row[0] is not None)

    def income_statement_is_the_models(self, role, owner, first, last):
        account = self.owner_account[owner]
        start = datetime.combine(first, time(0), UTC)
        end = datetime.combine(last + timedelta(days=1), time(0), UTC)

        def within(transfer):
            return start <= self.transfers[transfer].event_at < end

        adjusts = {a: c for c, adjustments in self.adjusted.items() for a in adjustments}
        recurring = set(self.recurring_charged.values())
        recurring_of = {c: r for (r, _), c in self.recurring_charged.items()}
        late = {fee for _, _, fee in self.late_fees.values()}

        def part(charge_id):
            """What a charge's money is: one of the company's charge types if it (or the
            recurring charge it was charged for) has one, else what kind of charge it is."""
            charge_id = adjusts.get(charge_id, charge_id)
            typed = (
                self.recurring[recurring_of[charge_id]].charge_type
                if charge_id in recurring_of
                else self.charges[charge_id].charge_type
            )
            if typed:
                return typed
            if self.charges[charge_id].kind == "rent":
                return "rent"
            if charge_id in recurring:
                return "recurring charges"
            return "late fees" if charge_id in late else "other charges"

        income = dict.fromkeys(
            ["rent", "recurring charges", "late fees", "NSF fees", "other charges"], Decimal(0)
        )
        # A type has a line once money was collected on it in the days, after the others.
        typed: dict[str, Decimal] = {}
        for (charge_id, t), amount in self.matches.items():
            if self.transfers[t].target == account and within(t):
                kind = part(charge_id)
                (income if kind in income else typed).setdefault(kind, Decimal(0))
                (income if kind in income else typed)[kind] += amount
        for (charge_id, _, r), amount in self.reversals.items():
            if self.transfers[r].source == account and within(r):
                kind = part(charge_id)
                (income if kind in income else typed).setdefault(kind, Decimal(0))
                (income if kind in income else typed)[kind] -= amount
        if typed:
            event("income statement: a line of a charge type")
        income |= {name: typed[name] for name in sorted(typed)}

        def fees(ids):
            return sum(
                (
                    self.transfers[t].amount
                    for t in ids
                    if self.transfers[t].source == account and within(t)
                ),
                Decimal(0),
            )

        management, leasing = fees(self.fee_transfers), fees(self.leasing_transfers)
        collected = sum(income.values(), Decimal(0))
        expected = [
            *((f"income: {name}", 3, amount) for name, amount in income.items()),
            ("total income", None, collected),
            ("expense: management fees", 11, management),
            ("expense: leasing fees", 8, leasing),
            ("total expenses", None, management + leasing),
            ("net income", None, collected - management - leasing),
            ("Schedule E line 3: Rents received", 3, collected),
            *([("Schedule E line 8: Commissions", 8, leasing)] if leasing else []),
            *([("Schedule E line 11: Management fees", 11, management)] if management else []),
        ]
        rows = (
            self.conns[role]
            .execute(
                "SELECT property, item, schedule_e, amount"
                " FROM trust_report_income_statement(%s, %s, %s, %s) WHERE line > 3",
                (self.pmc_id, self.pmc.owners[owner].owner_id, first, last),
            )
            .fetchall()
        )
        name = self.property_names[owner]
        assert rows == [
            *((name, item, line, amount) for item, line, amount in expected),
            *((None, f"all properties: {item}", line, amount) for item, line, amount in expected),
        ]
        self.all_owners_statement_is_each_owners(role, first, last)
        # With no budget, the budget against actual is every line of it at a budget of 0.00, and
        # each line's actual is the statement's.
        against = (
            self.conns[role]
            .execute(
                "SELECT property, item, budget, actual, actual_less_budget"
                " FROM trust_report_budget_vs_actual(%s, %s, %s, %s) WHERE line > 4",
                (self.pmc_id, self.pmc.owners[owner].owner_id, first, last),
            )
            .fetchall()
        )
        lines = [(i, amount) for i, _, amount in expected if not i.startswith("Schedule E")]
        assert against == [
            *((name, item, 0, amount, amount) for item, amount in lines),
            *((None, f"all properties: {item}", 0, amount, amount) for item, amount in lines),
        ]

    @rule(data=st.data(), role=st.sampled_from(["owner", "app", "ai"]), owner=st.integers(0, 1))
    def check_the_owner_on_the_accrual_basis(self, data, role, owner):
        """An owner's income statement on the accrual basis over random days, and their balance
        sheet at the end of the last, as the model has them (migration 0104). Income is what was
        charged in the days to the leases at the owner's property, a credit as less: a lease
        end's adjustment as what it adjusts, a credit on its own as rent; the fees as taken. The
        sheet holds the account's money at the day's end and what tenants owe then of what was
        due by it (as the delinquency report counts it), and the owner's equity is the two: no
        bills here."""
        mine = {
            c: b
            for c, b in self.charges.items()
            if self.leases[b.lease_id].unit.property_index == owner
        }
        days = sorted(
            {b.due_on for b in mine.values()}
            | {t.event_at.astimezone(UTC).date() for t in self.transfers.values()}
        ) or [BASE]
        first = data.draw(st.sampled_from(days)) + timedelta(days=data.draw(st.integers(-3, 1)))
        last = data.draw(st.sampled_from(days)) + timedelta(days=data.draw(st.integers(-1, 3)))
        if last < first:
            first, last = last, first
        self.accrual_is_the_models(role, owner, first, last)

    @precondition(lambda self: self.leases)
    @rule(data=st.data(), role=ROLES, amount=AMOUNTS, due=st.integers(0, 60))
    def credit_a_lease_and_read_the_accrual_basis(self, data, role, amount, due):
        """A credit given to a lease on its own, then its owner's accrual income statement and
        balance sheet over the day it is due: less rent, and less owed once due."""
        lease_id = data.draw(st.sampled_from(list(self.leases)))
        due_on = self.leases[lease_id].starts_on + timedelta(days=due)
        charge_id = charge(self.conns[role], self.pmc_id, lease_id, due_on, amount, "credit")
        self.charges[charge_id] = Charge(lease_id, due_on, "credit", amount)
        owner = self.leases[lease_id].unit.property_index
        self.accrual_is_the_models(role, owner, due_on, due_on)

    def accrual_is_the_models(self, role, owner, first, last):
        account = self.owner_account[owner]
        mine = {
            c: b
            for c, b in self.charges.items()
            if self.leases[b.lease_id].unit.property_index == owner
        }
        start = datetime.combine(first, time(0), UTC)
        cutoff = datetime.combine(last + timedelta(days=1), time(0), UTC)

        adjusts = {a: c for c, adjustments in self.adjusted.items() for a in adjustments}
        recurring_of = {c: r for (r, _), c in self.recurring_charged.items()}
        late = {fee for _, _, fee in self.late_fees.values()}

        def part(charge_id):
            original = adjusts.get(charge_id, charge_id)
            typed = (
                self.recurring[recurring_of[original]].charge_type
                if original in recurring_of
                else self.charges[original].charge_type
            )
            if typed:
                return typed
            kind = self.charges[original].kind
            if kind == "rent":
                return "rent"
            if original in recurring_of:
                return "recurring charges"
            if original in late:
                return "late fees"
            return "rent" if kind == "credit" else "other charges"

        income = dict.fromkeys(
            ["rent", "recurring charges", "late fees", "NSF fees", "other charges"], Decimal(0)
        )
        typed: dict[str, Decimal] = {}
        for charge_id, bill in mine.items():
            if first <= bill.due_on <= last:
                kind = part(charge_id)
                lines = income if kind in income else typed
                sign = -1 if bill.kind == "credit" else 1
                lines[kind] = lines.get(kind, Decimal(0)) + sign * bill.amount
        if any(b.kind == "credit" for b in mine.values() if first <= b.due_on <= last):
            event("accrual income statement: a credit in the days")
        income |= {name: typed[name] for name in sorted(typed)}

        def fees(ids):
            return sum(
                (
                    self.transfers[t].amount
                    for t in ids
                    if self.transfers[t].source == account
                    and start <= self.transfers[t].event_at < cutoff
                ),
                Decimal(0),
            )

        management, leasing = fees(self.fee_transfers), fees(self.leasing_transfers)
        charged = sum(income.values(), Decimal(0))
        expected = [
            *((f"income: {name}", 3, amount) for name, amount in income.items()),
            ("total income", None, charged),
            ("expense: management fees", 11, management),
            ("expense: leasing fees", 8, leasing),
            ("total expenses", None, management + leasing),
            ("net income", None, charged - management - leasing),
            ("Schedule E line 3: Rents received", 3, charged),
            *([("Schedule E line 8: Commissions", 8, leasing)] if leasing else []),
            *([("Schedule E line 11: Management fees", 11, management)] if management else []),
        ]
        owner_id = self.pmc.owners[owner].owner_id
        rows = (
            self.conns[role]
            .execute(
                "SELECT property, item, schedule_e, amount"
                " FROM trust_report_income_statement_accrual(%s, %s, %s, %s) WHERE line > 3",
                (self.pmc_id, owner_id, first, last),
            )
            .fetchall()
        )
        name = self.property_names[owner]
        assert rows == [
            *((name, item, line, amount) for item, line, amount in expected),
            *((None, f"all properties: {item}", line, amount) for item, line, amount in expected),
        ]

        cash = self.held_on(account, cutoff)
        # What tenants owe, as the delinquency report counts it.
        owed = sum(
            (
                total
                for lease_id, lease in self.leases.items()
                if lease.unit.property_index == owner
                and (total := self.aging(lease_id, last)[-1]) > 0
            ),
            Decimal(0),
        )
        sheet = [
            ("cash held in trust", cash),
            ("owed by tenants", owed),
            ("owed by housing authorities", Decimal(0)),
            ("bills not yet set aside", Decimal(0)),
            ("owner's equity", cash + owed),
        ]
        rows = (
            self.conns[role]
            .execute(
                "SELECT property, item, amount FROM trust_report_owner_balance_sheet(%s, %s, %s)"
                " WHERE line > 3",
                (self.pmc_id, owner_id, last),
            )
            .fetchall()
        )
        assert rows == [
            *((name, item, amount) for item, amount in sheet),
            *((None, f"all properties: {item}", amount) for item, amount in sheet),
        ]

    @rule(
        role=st.sampled_from(["owner", "app", "ai"]), day=st.integers(min_value=-5, max_value=640)
    )
    def check_owner_balances(self, role, day):
        as_of = BASE + timedelta(days=day)
        cutoff = datetime.combine(as_of + timedelta(days=1), time(0), tzinfo=UTC)
        rows = (
            self.conns[role]
            .execute(
                "SELECT line, item, owner, property, detail, balance, reserve, available"
                " FROM trust_report_owner_balances(%s, %s)",
                (self.pmc_id, as_of),
            )
            .fetchall()
        )
        expected = []
        for i, account in enumerate(self.owner_account):
            terms = self.agreement_on(account, as_of)
            held = self.held_on(account, cutoff)
            reserve = terms.reserve if terms else Decimal(0)
            detail = (
                f"{format(terms.percent.normalize(), 'f')}% of collected rent,"
                f" minimum {terms.minimum:.2f}, flat {terms.flat:.2f}"
                if terms
                else "no management agreement"
            )
            names = (self.owner_names[i], self.property_names[i])
            expected.append(
                ("owner property", *names, detail, held, reserve, max(held - reserve, Decimal(0)))
            )
        expected.append(
            (
                "total",
                None,
                None,
                None,
                sum((line[4] for line in expected), Decimal(0)),
                sum((line[5] for line in expected), Decimal(0)),
                sum((line[6] for line in expected), Decimal(0)),
            )
        )
        assert [row[0] for row in rows] == list(range(1, len(rows) + 1))
        assert [tuple(row[1:]) for row in rows[2:]] == expected

    def paid_by(self, charge_id, cutoff):
        """What of a charge was paid before cutoff, net of what bounced back before it."""
        paid = sum(
            (
                a
                for (c, t), a in self.matches.items()
                if c == charge_id and self.transfers[t].event_at < cutoff
            ),
            Decimal(0),
        )
        back = sum(
            (
                a
                for (c, _, r), a in self.reversals.items()
                if c == charge_id and self.transfers[r].event_at < cutoff
            ),
            Decimal(0),
        )
        return paid - back

    def aging(self, lease_id, as_of):
        """(current, 1-30, 31-60, 61-90, over 90, total) a lease owes at the end of a day:
        credits due by then and payments toward charges not yet due come off its charges rent
        first (pay_order), a running total as open_by keeps."""
        cutoff = datetime.combine(as_of + timedelta(days=1), time(0), tzinfo=UTC)
        mine = {c: bill for c, bill in self.charges.items() if bill.lease_id == lease_id}
        offsets = sum(
            (b.amount for b in mine.values() if b.kind == "credit" and b.due_on <= as_of),
            Decimal(0),
        ) + sum(
            (
                self.paid_by(c, cutoff)
                for c, b in mine.items()
                if b.kind != "credit" and b.due_on > as_of
            ),
            Decimal(0),
        )
        out = [Decimal(0)] * 5
        before = Decimal(0)
        for c in sorted(
            (c for c, b in mine.items() if b.kind != "credit" and b.due_on <= as_of),
            key=self.pay_order,
        ):
            bill = mine[c]
            left = bill.amount - self.paid_by(c, cutoff)
            take = min(left, max(offsets - before, Decimal(0)))
            before += left
            late = (as_of - bill.due_on).days
            out[next(i for i, top in enumerate((0, 30, 60, 90, late)) if late <= top)] += (
                left - take
            )
        return (*out, sum(out, Decimal(0)))

    @precondition(lambda self: self.leases)
    @rule(
        role=st.sampled_from(["owner", "app", "ai"]), day=st.integers(min_value=-5, max_value=640)
    )
    def check_the_tenant_reports(self, role, day):
        """The delinquency report line by line, and each lease's tenant ledger closing at its
        balance due."""
        as_of = BASE + timedelta(days=day)
        conn = self.conns[role]
        names = {t.tenant_id: t.name for t in self.tenants}
        expected = []
        for lease_id, lease in self.leases.items():
            aged = self.aging(lease_id, as_of)
            if aged[-1] > 0:
                tenants = ", ".join(
                    names[t] for t in sorted(lease.tenants, key=lambda t: (names[t].encode(), t))
                )
                expected.append(
                    (
                        self.property_names[lease.unit.property_index].encode(),
                        lease.unit.name.encode(),
                        lease.starts_on,
                        (
                            self.property_names[lease.unit.property_index],
                            lease.unit.name,
                            tenants,
                            *aged,
                        ),
                    )
                )
        rows = conn.execute(
            "SELECT property, unit, tenants, current_due, days_1_30, days_31_60, days_61_90,"
            " over_90, total FROM trust_report_delinquency(%s, %s) WHERE item = 'lease'",
            (self.pmc_id, as_of),
        ).fetchall()
        assert rows == [line for *_, line in sorted(expected, key=lambda e: e[:3])]

        lease_id = min(self.leases)
        cutoff = datetime.combine(as_of + timedelta(days=1), time(0), tzinfo=UTC)
        balance = sum(
            (
                (-b.amount if b.kind == "credit" else b.amount)
                for b in self.charges.values()
                if b.lease_id == lease_id and b.due_on <= as_of
            ),
            Decimal(0),
        ) - sum(
            (self.paid_by(c, cutoff) for c, b in self.charges.items() if b.lease_id == lease_id),
            Decimal(0),
        )
        closing = conn.execute(
            "SELECT balance FROM trust_report_tenant_ledger(%s, %s, %s) WHERE item = 'balance'",
            (self.pmc_id, lease_id, as_of),
        ).fetchone()[0]
        assert closing == balance

    @precondition(lambda self: self.leases)
    @rule(
        data=st.data(),
        write=st.sampled_from(
            [
                "open a lease",
                "end a lease",
                "charge",
                "match a payment",
                "reverse a payment",
                "run the rent",
                "assess late fees",
                "record late fee terms",
                "record a recurring charge",
                "charge recurring charges",
            ]
        ),
    )
    def the_ai_role_cannot_write(self, data, write):
        """Whatever the AI tries, valid or not, is refused for lack of a grant, and nothing
        changes (the invariants below compare every table with the model)."""
        ai, lease_id = self.conns["ai"], data.draw(st.sampled_from(list(self.leases)))
        lease = self.leases[lease_id]
        transfer_id = data.draw(st.sampled_from(list(self.transfers) or ["pglt_none"]))
        charge_id = data.draw(st.sampled_from(list(self.charges) or [lease_id]))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            if write == "open a lease":
                open_lease(
                    ai, self.pmc_id, lease.unit.unit_id, LAST_DAY, None, "1.00", lease.tenants
                )
            elif write == "end a lease":
                end_lease(ai, self.pmc_id, lease_id, None)
            elif write == "charge":
                charge(ai, self.pmc_id, lease_id, lease.starts_on, "1.00", "fee")
            elif write == "match a payment":
                apply_payment(ai, self.pmc_id, charge_id, transfer_id, "0.01")
            elif write == "run the rent":
                charge_rent_due(ai, self.pmc_id, lease.starts_on)
            elif write == "assess late fees":
                assess_late_fees(ai, self.pmc_id, LAST_DAY)
            elif write == "record late fee terms":
                add_late_fee_policy(ai, self.pmc_id, self.properties[0], LAST_DAY, 0, "1")
            elif write == "record a recurring charge":
                add_recurring_charge(ai, self.pmc_id, lease_id, "1.00", lease.starts_on)
            elif write == "charge recurring charges":
                charge_recurring_due(ai, self.pmc_id, lease.starts_on)
            else:
                reverse_payment(ai, self.pmc_id, charge_id, transfer_id, transfer_id, "0.01")

    @precondition(lambda self: self.leases)
    @rule(data=st.data(), role=st.sampled_from(["owner", "app", "ai"]))
    def check_the_lease_expirations(self, data, role):
        """The lease expirations report as of a day, mostly one some lease ends within 90 days
        of: every lease whose last day falls in the window, soonest first in its group of days
        left, and each group's and the whole list's rent and leases with no offer out (all of
        them here: this model makes no lease offers)."""
        ends = sorted({lease.ends_on for lease in self.leases.values() if lease.ends_on})
        as_of = data.draw(
            st.one_of(
                st.sampled_from(ends).flatmap(
                    lambda end: st.integers(0, 95).map(lambda back: end - timedelta(days=back))
                )
                if ends
                else st.nothing(),
                st.integers(-5, 640).map(lambda day: BASE + timedelta(days=day)),
            )
        )
        rows = (
            self.conns[role]
            .execute(
                "SELECT line, item, property, unit, tenants, ends_on, days_left, bucket,"
                " monthly_rent, renewal FROM trust_report_lease_expirations(%s, %s)",
                (self.pmc_id, as_of),
            )
            .fetchall()
        )
        names = {t.tenant_id: t.name for t in self.tenants}
        expected = []
        everything = Decimal(0)
        count = 0
        for label, low, high in (("0-30", 0, 30), ("31-60", 31, 60), ("61-90", 61, 90)):
            ending = sorted(
                (
                    (lease.ends_on, self.property_names[lease.unit.property_index], lease_id)
                    for lease_id, lease in self.leases.items()
                    if lease.ends_on and low <= (lease.ends_on - as_of).days <= high
                ),
                key=lambda e: (
                    e[0],
                    e[1].encode(),
                    self.leases[e[2]].unit.name.encode(),
                    e[2],
                ),
            )
            for ends_on, place, lease_id in ending:
                lease = self.leases[lease_id]
                tenants = ", ".join(
                    names[t] for t in sorted(lease.tenants, key=lambda t: (names[t].encode(), t))
                )
                days = (ends_on - as_of).days
                expected.append(
                    (
                        "lease",
                        place,
                        lease.unit.name,
                        tenants,
                        ends_on,
                        days,
                        label,
                        lease.rent,
                        "none",
                    )
                )
            rent = sum((self.leases[e[2]].rent for e in ending), Decimal(0))
            n = len(ending)
            expected.append(
                (
                    "group total",
                    None,
                    None,
                    None,
                    None,
                    None,
                    label,
                    rent,
                    f"{n} of {n} with no renewal offer out",
                )
            )
            everything += rent
            count += n
        expected.append(
            (
                "total",
                None,
                None,
                None,
                None,
                None,
                None,
                everything,
                f"{count} of {count} with no renewal offer out",
            )
        )
        if count:
            event("lease expirations: some lease listed")

        assert [row[0] for row in rows] == list(range(1, len(rows) + 1))
        assert rows[1][1:3] == (
            "as of",
            f"{as_of:%Y-%m-%d}, end of day UTC; leases ending in the next 90 days",
        )
        assert [tuple(row[1:]) for row in rows[2:]] == expected

    @rule(
        role=st.sampled_from(["owner", "app", "ai"]), day=st.integers(min_value=-5, max_value=640)
    )
    def check_the_rent_roll(self, role, day):
        as_of = BASE + timedelta(days=day)
        rows = self.roll(role, as_of)
        assert [row[0] for row in rows] == list(range(1, len(rows) + 1))
        assert rows[1][1:6:4] == ("as of", f"{as_of:%Y-%m-%d}, end of day UTC")
        assert [tuple(row[1:]) for row in rows[2:]] == self.expected_roll(as_of)

    # --- invariants: checked after every step --------------------------------------------

    @invariant()
    def holds_are_the_models(self):
        holds = self.conn.execute(
            "SELECT h.id, h.lease_id, h.starts_on, r.ends_on, h.reason"
            " FROM trust_payment_holds h"
            " LEFT JOIN trust_payment_hold_releases r ON r.hold_id = h.id WHERE h.pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert {h[0]: Hold(*h[1:]) for h in holds} == self.holds
        allowed = self.conn.execute(
            "SELECT hold_id, transfer_id FROM trust_payment_hold_allowances WHERE pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert set(allowed) == self.allowed

    @invariant()
    def leases_never_overlap(self):
        overlapping = self.conn.execute(
            "SELECT a.id, b.id FROM trust_leases a JOIN trust_leases b"
            " ON a.unit_id = b.unit_id AND a.id < b.id"
            " AND a.starts_on <= coalesce(b.ends_on, 'infinity'::date)"
            " AND b.starts_on <= coalesce(a.ends_on, 'infinity'::date)"
            " WHERE a.pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert overlapping == []
        stored = self.conn.execute(
            "SELECT id, unit_id, starts_on, ends_on, monthly_rent FROM trust_leases"
            " WHERE pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert sorted(stored) == sorted(
            (i, lease.unit.unit_id, lease.starts_on, lease.ends_on, lease.rent)
            for i, lease in self.leases.items()
        )

    @invariant()
    def matches_never_overpay_a_charge_or_overspend_a_transfer(self):
        # A charge is paid net of what bounced back: matched, less reversed.
        over = self.conn.execute(
            """
            SELECT 'charge', c.id::text FROM trust_charges c
            WHERE c.pmc_id = %(pmc)s AND (
                coalesce((SELECT sum(p.amount) FROM trust_charge_payments p
                          WHERE p.charge_id = c.id), 0)
                - coalesce((SELECT sum(r.amount) FROM trust_payment_reversals r
                            WHERE r.charge_id = c.id), 0)
            ) NOT BETWEEN 0 AND c.amount
            UNION ALL
            SELECT 'transfer', t.id FROM pgledger_transfers t
            JOIN trust_charge_payments p ON p.transfer_id = t.id
            WHERE p.pmc_id = %(pmc)s GROUP BY t.id, t.amount HAVING sum(p.amount) > t.amount
            """,
            {"pmc": self.pmc_id},
        ).fetchall()
        assert over == []
        stored = self.conn.execute(
            "SELECT charge_id, transfer_id, amount FROM trust_charge_payments WHERE pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert {(c, t): a for c, t, a in stored} == self.matches

    @invariant()
    def reversals_stay_inside_their_match_and_transfer(self):
        over = self.conn.execute(
            """
            SELECT 'match', p.transfer_id FROM trust_charge_payments p
            JOIN trust_payment_reversals r
              ON (r.charge_id, r.transfer_id) = (p.charge_id, p.transfer_id)
            WHERE p.pmc_id = %(pmc)s
            GROUP BY p.charge_id, p.transfer_id, p.amount HAVING sum(r.amount) > p.amount
            UNION ALL
            SELECT 'reversal', t.id FROM pgledger_transfers t
            JOIN trust_payment_reversals r ON r.reversal_id = t.id
            WHERE r.pmc_id = %(pmc)s GROUP BY t.id, t.amount HAVING sum(r.amount) > t.amount
            """,
            {"pmc": self.pmc_id},
        ).fetchall()
        assert over == []
        stored = self.conn.execute(
            "SELECT charge_id, transfer_id, reversal_id, amount FROM trust_payment_reversals"
            " WHERE pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert {(c, t, r): a for c, t, r, a in stored} == self.reversals

    @invariant()
    def fees_and_draws_are_the_models(self):
        fees = self.conn.execute(
            "SELECT ledger_account_id, period_start, period_end, collected, fee"
            " FROM trust_management_fees WHERE pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert {(a, s): (e, c, f) for a, s, e, c, f in fees} == self.fees
        overlapping = self.conn.execute(
            "SELECT 1 FROM trust_management_fees a JOIN trust_management_fees b"
            " ON a.ledger_account_id = b.ledger_account_id AND a.period_start < b.period_start"
            " AND b.period_start < a.period_end WHERE a.pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert overlapping == []
        leasing = self.conn.execute(
            "SELECT lease_id, ledger_account_id, fee FROM trust_leasing_fees WHERE pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert {lease: (a, f) for lease, a, f in leasing} == self.leasing
        draws = self.conn.execute(
            "SELECT request_key, ledger_account_id, amount FROM trust_owner_draws"
            " WHERE pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert {k: (a, amount) for k, a, amount in draws} == self.draws

    @invariant()
    def late_fees_are_the_models(self):
        stored = self.conn.execute(
            "SELECT f.rent_charge_id, f.unpaid, f.fee, f.fee_charge_id, c.lease_id, c.due_on,"
            " c.kind, c.amount FROM trust_late_fees f JOIN trust_charges c"
            " ON c.id = f.fee_charge_id WHERE f.pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert {r: (u, f, i) for r, u, f, i, *_ in stored} == self.late_fees
        assert all(
            Charge(lease, due, kind, amount) == self.charges[fee_id]
            for _, _, _, fee_id, lease, due, kind, amount in stored
        )

    @invariant()
    def recurring_charges_are_the_models(self):
        stored = self.conn.execute(
            "SELECT r.id, r.lease_id, r.kind, r.amount, r.starts_on, e.ends_on, t.name"
            " FROM trust_recurring_charges r LEFT JOIN trust_recurring_charge_ends e"
            " ON e.recurring_charge_id = r.id"
            " LEFT JOIN trust_charge_types t ON t.id = r.charge_type_id WHERE r.pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert {i: Recurring(*rest) for i, *rest in stored} == self.recurring
        charged = self.conn.execute(
            "SELECT recurring_charge_id, due_on, id, lease_id, kind, amount FROM trust_charges"
            " WHERE pmc_id = %s AND recurring_charge_id IS NOT NULL",
            (self.pmc_id,),
        ).fetchall()
        assert {(r, d): i for r, d, i, *_ in charged} == self.recurring_charged
        assert all(
            self.charges[i] == Charge(lease, d, kind, amount)
            for _, d, i, lease, kind, amount in charged
        )

    @invariant()
    def the_rent_roll_ties_to_the_ledger(self):
        rows = self.roll("owner", LAST_DAY - timedelta(days=1))
        totals = {row[1]: row[7:] for row in rows if row[1].startswith(("total", "not"))}
        current, other, every = (
            totals[k]
            for k in ("total: current leases", "not on a current lease", "total: all tenants")
        )
        assert tuple(a + b for a, b in zip(current, other, strict=True)) == every
        ledger = dict(
            self.conn.execute(
                "SELECT t.kind, coalesce(sum(a.balance), 0) FROM trust_ledger_accounts t"
                " JOIN pgledger_accounts a ON a.id = t.ledger_account_id"
                " WHERE t.pmc_id = %s AND t.kind IN ('tenant_deposit', 'prepaid_rent')"
                " GROUP BY t.kind",
                (self.pmc_id,),
            ).fetchall()
        )
        assert every[:2] == (ledger["tenant_deposit"], ledger["prepaid_rent"])
        charged = sum(
            (-c.amount if c.kind == "credit" else c.amount for c in self.charges.values()),
            Decimal(0),
        )
        paid = sum(self.matches.values(), Decimal(0)) - sum(self.reversals.values(), Decimal(0))
        assert every[2:] == (charged, paid, charged - paid)


def test_rent_roll_invariants_hold_under_random_leases_charges_and_payments(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as owner_conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
        psycopg.connect(database_url, autocommit=True) as ai_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        ai_conn.execute("SET ROLE trust_ai_agent")
        run_state_machine_as_test(lambda: RentRollMachine(owner_conn, app_conn, ai_conn))


def test_the_model_counts_a_charge_paid_twice_by_then_as_the_database_does(conn):
    """A charge paid, its payment bouncing only later, and paid again in between (matched once
    the bounce made room) was paid twice as of a day between. The database keeps a running
    total of what each lease owes, oldest first, so the second payment comes off what is owed
    before it and nothing more: here an older rent still owes far more, so the credit is its and
    a newer rent keeps all it owes. The model above must count it the same way (it once gave the
    second payment to the rent as a credit, so CI's seed saw a late fee worked from a cent less
    than the database's)."""
    pmc = make_pmc(conn)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    place = pmc.owners[0]
    lease = open_lease(
        conn,
        pmc.pmc_id,
        add_unit(conn, pmc.pmc_id, place.property_id),
        BASE,
        None,
        "1.00",
        [tenant],
    )
    owed = charge(conn, pmc.pmc_id, lease, BASE, "5.00", "rent")
    twice = charge(conn, pmc.pmc_id, lease, BASE + timedelta(days=1), "0.01", "rent")
    credit = charge(conn, pmc.pmc_id, lease, BASE, "0.01", "credit", "Courtesy")
    # Charged on its due date, so its grace runs from then (migration 0151).
    rent = charge(
        conn,
        pmc.pmc_id,
        lease,
        BASE + timedelta(days=31),
        "0.02",
        "rent",
        charged_at=datetime.combine(BASE + timedelta(days=31), time(6), tzinfo=UTC),
    )
    model = RentRollMachine.__new__(RentRollMachine)
    model.charges = {
        owed: Charge(lease, BASE, "rent", Decimal("5.00")),
        twice: Charge(lease, BASE + timedelta(days=1), "rent", CENT),
        credit: Charge(lease, BASE, "credit", CENT),
        rent: Charge(lease, BASE + timedelta(days=31), "rent", Decimal("0.02")),
    }
    model.transfers, model.matches, model.reversals = {}, {}, {}
    model.recurring_charged, model.adjusted = {}, {}

    def post(source, target, day):
        when = datetime.combine(BASE + timedelta(days=day), time(12), tzinfo=UTC)
        (transfer,) = transfer_batch(conn, [(source, target, CENT)], when)
        model.transfers[transfer] = Transfer(source, target, CENT, when)
        return transfer

    first = post(pmc.operating_cash, place.account, 1)
    apply_payment(conn, pmc.pmc_id, twice, first, CENT)
    back = post(place.account, pmc.operating_cash, 50)  # it bounces on day 50
    reverse_payment(conn, pmc.pmc_id, twice, first, back, CENT)
    second = post(pmc.operating_cash, place.account, 2)  # paid again, dated day 2
    apply_payment(conn, pmc.pmc_id, twice, second, CENT)
    model.matches = {(twice, first): CENT, (twice, second): CENT}
    model.reversals = {(twice, first, back): CENT}
    # Late fee terms from the newer rent's day, so the older two take none.
    add_late_fee_policy(
        conn, pmc.pmc_id, place.property_id, BASE + timedelta(days=31), 0, "25.00", "10"
    )

    assert assess_late_fees(conn, pmc.pmc_id, BASE + timedelta(days=40)) == 1
    late_day = datetime.combine(BASE + timedelta(days=32), time(0), tzinfo=UTC)
    assert (
        conn.execute(
            "SELECT unpaid FROM trust_late_fees WHERE rent_charge_id = %s", (rent,)
        ).fetchone()[0]
        == model.open_by(rent, late_day)
        == Decimal("0.02")
    )
    fee, due_on, amount = conn.execute(
        "SELECT c.id, c.due_on, c.amount FROM trust_late_fees AS f"
        " JOIN trust_charges AS c ON c.id = f.fee_charge_id WHERE f.rent_charge_id = %s",
        (rent,),
    ).fetchone()
    model.charges[fee] = Charge(lease, due_on, "fee", amount)
    as_of = BASE + timedelta(days=35)
    assert conn.execute(
        "SELECT current_due, days_1_30, days_31_60, days_61_90, over_90, total"
        " FROM trust_report_delinquency(%s, %s) WHERE item = 'lease'",
        (pmc.pmc_id, as_of),
    ).fetchone() == model.aging(lease, as_of)
