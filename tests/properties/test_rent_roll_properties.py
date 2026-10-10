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
  - fees and draws are the model's: a management fee is the agreement's percent of rent
    collected in its period (net of bounces), or its minimum, plus its flat fee, taken once per
    period with no two periods of an account overlapping; a leasing fee is taken once per
    lease; a draw never pays out more than the balance above the reserve, and its request key
    pays once;
  - late fees are the model's: the rent run (run_the_rent) charges each lease in effect on the
    due date once, and each rent still unpaid when its grace ran out is charged one late fee,
    worked from what was unpaid then under the terms in force on its due date.

The rule check_the_tenant_reports reads the delinquency report as of a random day and compares
every lease line with the model's aging, and checks a lease's tenant ledger closes at its
balance due. The rule check_owner_balances reads the owner balances report as of a random day
and compares it with the model. The rule check_the_rent_roll reads the roll as of a random day,
as any role including the AI's (trust_ai_agent), and compares every line with the model's: which
lease each unit is in, its tenants in byte order, the money held for them, what was charged and
paid by then, and the totals. The AI's role is refused every write it tries, and nothing
changes.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

import psycopg
import pytest
from helpers import (
    add_agreement,
    add_late_fee_policy,
    add_tenant,
    add_unit,
    apply_payment,
    assess_late_fees,
    charge,
    charge_rent_due,
    draw_owner,
    end_lease,
    make_pmc,
    open_account,
    open_lease,
    post_leasing_fee,
    post_management_fee,
    reverse_payment,
    transfer_batch,
)
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
ROLES = st.sampled_from(["owner", "app"])
# A lease's length in days after its first day; None is month to month.
LENGTHS = st.one_of(st.integers(min_value=0, max_value=200), st.sampled_from([None, 0, 30, 364]))
TIMES = st.integers(min_value=0, max_value=86_399)  # seconds into the day


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
        # Rent automation: (property index, first day) -> late fee terms; rent charge ->
        # (unpaid when its grace ran out, fee, the fee's charge).
        self.late_terms: dict[tuple[int, date], LateTerms] = {}
        self.late_fees: dict[UUID, tuple[Decimal, Decimal, UUID]] = {}

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
        """Rent matched to payments into the account in [start, end), less rent bounced back
        out of it in that time."""
        rent = {c for c, bill in self.charges.items() if bill.kind == "rent"}
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
            end_lease(self.conns[role], self.pmc_id, lease_id, ends_on)
            lease.ends_on = ends_on

    # --- rules: charges and money --------------------------------------------------------

    @precondition(lambda self: self.leases)
    @rule(
        data=st.data(),
        role=ROLES,
        due=st.integers(min_value=-20, max_value=220),
        kind=st.sampled_from(["rent", "rent", "fee", "credit"]),
        amount=AMOUNTS,
    )
    def charge_a_lease(self, data, role, due, kind, amount):
        lease_id = data.draw(st.sampled_from(list(self.leases)))
        due_on = self.leases[lease_id].starts_on + timedelta(days=due)
        billed = any(
            c.lease_id == lease_id and c.due_on == due_on and c.kind == "rent"
            for c in self.charges.values()
        )
        if kind == "rent" and billed:  # a retried rent run
            with pytest.raises(psycopg.errors.UniqueViolation, match="one_rent_per_due_date"):
                charge(self.conns[role], self.pmc_id, lease_id, due_on, amount, kind)
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
        elif not self.pays_for(paid, self.leases[bill.lease_id]):
            refusal = (psycopg.errors.InvalidParameterValue, "did not pay into")
        elif existing is not None:
            if existing != amount:
                refusal = (psycopg.errors.UniqueViolation, "already pays")
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
    )
    def collect_rent_then_take_the_fee(self, data, role, day, percent, rent_cents):
        """A month in one go: an agreement in force (recorded if none is), the month's rent on
        a lease of the property charged, paid in cash and matched, then the month's fee. The
        month is one with no fee taken yet, when there is one."""
        i = data.draw(st.integers(0, len(self.owner_account) - 1))
        account = self.owner_account[i]
        free = [
            k
            for k in range(14)
            if not any(
                a == account and s < month_start(k + 1) and month_start(k) < held[0]
                for (a, s), held in self.fees.items()
            )
        ]
        month = data.draw(st.sampled_from(free or list(range(14))))
        start = month_start(month)
        if self.agreement_on(account, start) is None:
            add_agreement(self.conn, self.pmc_id, account, start, percent, "25.00", "5.00", "50")
            self.agreements[(account, start)] = Terms(
                Decimal(percent), Decimal("25.00"), Decimal("5.00"), Decimal(50), Decimal(0)
            )
        leases = [i_ for i_, lease in self.leases.items() if lease.unit.property_index == i]
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
                rent = charge(self.conn, self.pmc_id, lease_id, start, amount)
                self.charges[rent] = Charge(lease_id, start, "rent", amount)
            owed = self.charges[rent].amount - self.paid_on(rent)
            if owed > 0:
                when = datetime.combine(start + timedelta(days=day), time(12), tzinfo=UTC)
                paid = self.pay_in("cash", self.tenants[0], account, owed, when)
                self.attempt_match(role, rent, paid, owed)
        self.attempt_fee(role, account, start, month_start(month + 1))

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
    @rule(data=st.data(), role=ROLES, day=DAYS)
    def run_the_rent(self, data, role, day):
        """Usually on a day some lease is in effect, so the run charges something."""
        covered = sorted(
            {d for lease in self.leases.values() for d in (lease.starts_on, lease.ends_on) if d}
        )
        self.charge_rent_on(
            role, data.draw(st.sampled_from([*covered, BASE + timedelta(days=day)]))
        )

    def charge_rent_on(self, role, due_on):
        due = [
            lease_id
            for lease_id, lease in self.leases.items()
            if lease.covers(due_on)
            and not any(
                c.lease_id == lease_id and c.due_on == due_on and c.kind == "rent"
                for c in self.charges.values()
            )
        ]

        assert charge_rent_due(self.conns[role], self.pmc_id, due_on) == len(due)

        for lease_id in due:
            (charge_id,) = self.conn.execute(
                "SELECT id FROM trust_charges WHERE lease_id = %s AND due_on = %s"
                " AND kind = 'rent'",
                (lease_id, due_on),
            ).fetchone()
            self.charges[charge_id] = Charge(lease_id, due_on, "rent", self.leases[lease_id].rent)

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
    )
    def let_a_rent_go_late(self, data, role, after, grace, paid):
        """The usual late month: late fee terms in force (recorded if none are), the rent run
        on a day the lease is in effect, a payment dated just before the grace runs out or
        just after, then the late fee run."""
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
        self.charge_rent_on(role, due_on)
        rent = next(
            c
            for c, bill in self.charges.items()
            if bill.lease_id == lease_id and bill.due_on == due_on and bill.kind == "rent"
        )
        terms = self.late_terms_on(place, due_on)
        late_day = due_on + timedelta(days=terms.grace + 1)
        cutoff = datetime.combine(late_day, time(0), tzinfo=UTC)
        owed = self.charges[rent].amount - self.paid_on(rent)
        amount = {"nothing": 0, "half": to_cents(owed / 2)}.get(paid, owed)
        if amount > 0:
            when = cutoff if paid.endswith("late") else cutoff - timedelta(seconds=1)
            payment = self.pay_in("cash", self.tenants[0], self.owner_account[place], amount, when)
            self.attempt_match(role, rent, payment, amount)
        self.assess(role, late_day + timedelta(days=data.draw(st.integers(0, 5))))

    def assess(self, role, as_of):
        expected = {}
        for charge_id, rent in self.charges.items():
            if rent.kind != "rent" or charge_id in self.late_fees:
                continue
            terms = self.late_terms_on(self.leases[rent.lease_id].unit.property_index, rent.due_on)
            if terms is None or rent.due_on + timedelta(days=terms.grace) >= as_of:
                continue
            late_day = rent.due_on + timedelta(days=terms.grace + 1)
            unpaid = self.unpaid_by(charge_id, datetime.combine(late_day, time(0), tzinfo=UTC))
            if unpaid <= 0:
                continue
            fee = to_cents(terms.flat + terms.percent / 100 * unpaid)
            if terms.maximum is not None:
                fee = min(fee, terms.maximum)
            if fee > 0:
                expected[charge_id] = (unpaid, fee, late_day)

        assert assess_late_fees(self.conns[role], self.pmc_id, as_of) == len(expected)

        for rent_id, (unpaid, fee, late_day) in expected.items():
            (fee_id,) = self.conn.execute(
                "SELECT fee_charge_id FROM trust_late_fees WHERE rent_charge_id = %s", (rent_id,)
            ).fetchone()
            self.late_fees[rent_id] = (unpaid, fee, fee_id)
            self.charges[fee_id] = Charge(self.charges[rent_id].lease_id, late_day, "fee", fee)

    # --- rules: the reports --------------------------------------------------------------

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
        credits due by then and payments toward charges not yet due come off the oldest
        charges first."""
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
        for c, bill in sorted(
            ((c, b) for c, b in mine.items() if b.kind != "credit" and b.due_on <= as_of),
            key=lambda pair: (pair[1].due_on, pair[0]),
        ):
            left = bill.amount - self.paid_by(c, cutoff)
            take = min(left, max(offsets, Decimal(0)))
            offsets -= take
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
            else:
                reverse_payment(ai, self.pmc_id, charge_id, transfer_id, transfer_id, "0.01")

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
