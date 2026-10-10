"""Property-based tests for leases, charges, payment matching and the rent roll.

Hypothesis opens and ends leases, charges rent, fees and credits, posts payments (from cash, a
tenant's prepaid rent, or a deposit kept with its cash), matches them to charges and bounces
some of them back, in random order and through both the owner role and trust_app. A Python
model predicts every step: the database must accept exactly what the model accepts and refuse
the rest with the model's reason.

Invariants checked after every step (ci/registry.toml maps each to this test):
  - two leases of one unit never overlap, and the leases are the model's;
  - no charge is paid past its amount (net of reversals), no transfer pays more than it moved,
    no reversal undoes more than its match or spends its transfer past its amount, and the
    matches and reversals are the model's, so a retry never adds one;
  - the rent roll for all time ties to the trust ledger: what it shows held for all tenants is
    every tenant's deposit and prepaid rent balance, current and not-current leases add up to
    all of them, and every lease's balance is its charges less its payments, net of reversals.

The rule check_the_rent_roll reads the roll as of a random day and compares every line with the
model's: which lease each unit is in, its tenants in byte order, the money held for them, what
was charged and paid by then, and the totals.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest
from helpers import (
    add_tenant,
    add_unit,
    apply_payment,
    charge,
    end_lease,
    make_pmc,
    open_account,
    open_lease,
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


def overlaps(a_start, a_end, b_start, b_end):
    return a_start <= (b_end or LAST_DAY) and b_start <= (a_end or LAST_DAY)


class RentRollMachine(RuleBasedStateMachine):
    def __init__(self, owner_conn, app_conn):
        super().__init__()
        self.conns = {"owner": owner_conn, "app": app_conn}
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

    # --- helpers -------------------------------------------------------------------------

    def post(self, requests, event_at):
        """Post a batch as the owner, dated event_at; the model records each transfer."""
        ids = transfer_batch(self.conn, requests, event_at=event_at)
        for transfer_id, (source, target, amount) in zip(ids, requests, strict=True):
            self.transfers[transfer_id] = Transfer(source, target, amount, event_at)
            self.balance[source] = self.balance.get(source, Decimal(0)) - amount
            self.balance[target] = self.balance.get(target, Decimal(0)) + amount
        return ids

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

    # --- rules: the report ---------------------------------------------------------------

    @rule(role=ROLES, day=st.integers(min_value=-5, max_value=640))
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
    ):
        app_conn.execute("SET ROLE trust_app")
        run_state_machine_as_test(lambda: RentRollMachine(owner_conn, app_conn))
