"""Property-based tests for autopay (migration 0151, src/pms/autopay.py).

Hypothesis gives a lease's two tenants autopays (a fixed amount or the balance up to an amount),
saves their bank accounts or not, stops them, walks the months charging the rent, runs the night
on random days, places and releases payment holds, has the payments partner be down or lose its
answer after taking a payment, and reports payments paid, failed or returned in any order and more
than once. A model predicts each step.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - each autopay comes to each due date at most once: a payment of what its rule says (a fixed
    amount, or what the lease owes by then less what is on its way, up to its amount), or the
    reason it paid nothing (a payment hold in force on the due date, nothing owed); never before
    its account was saved, never after it stopped;
  - two payments in a row that didn't go through or came back stop it, by itself;
  - the partner takes each payment once, however often it is asked or loses its answer, and takes
    none the PMS has no record of; one it took is recorded as accepted once its answer arrives;
  - a payment not yet accepted is never sent once something was reported of it or its autopay
    stopped, and one of a stopped autopay is given up (expired), so it is no longer on its way;
  - every trust bank account ties out.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import psycopg
from helpers import (
    add_tenant,
    add_unit,
    bank_sums,
    charge,
    hold_payments,
    make_pmc,
    open_lease,
    release_hold,
)
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from pms import autopay, payments
from pms.api import online_payments as op

ZERO = Decimal(0)
RENT = Decimal("1200.00")
THIS_MONTH = datetime.now(UTC).date().replace(day=1)
CENTS = st.integers(min_value=1, max_value=250_000).map(lambda c: Decimal(c) / 100)
# What a tenant sets: often a round amount near the rent (so a balance autopay's limit bites or
# doesn't), else any.
AMOUNTS = st.one_of(
    st.sampled_from(
        [Decimal("600.00"), Decimal("1200.00"), Decimal("1500.00"), Decimal("3000.00")]
    ),
    CENTS,
)


def month_after(day: date) -> date:
    return (day.replace(day=1) + timedelta(days=32)).replace(day=1)


def months_back(n: int) -> date:
    day = THIS_MONTH
    for _ in range(n):
        day = (day - timedelta(days=1)).replace(day=1)
    return day


class Partner(payments.FakePartner):
    """The fake partner, as Stripe behaves for a charge: it finds a payment it took before under
    the same id rather than take another. Down, it takes nothing; losing its answer, it takes the
    payment and the PMS hears nothing back."""

    def __init__(self):
        self.taken: dict[uuid.UUID, int] = {}
        self.mode = "up"

    def charge(self, payment_id, amount, memo, account):
        if payment_id in self.taken:
            return super().charge(payment_id, amount, memo, account)
        if self.mode == "down":
            raise payments.PartnerFailed("down")
        self.taken[payment_id] = self.taken.get(payment_id, 0) + 1
        if self.mode == "lost":
            raise payments.PartnerFailed("the answer was lost")
        return super().charge(payment_id, amount, memo, account)


@dataclass
class Autopay:
    id: uuid.UUID
    tenant: int
    rule: str
    amount: Decimal
    saved_on: date | None = None
    stopped: str | None = None


@dataclass
class Paying:
    id: uuid.UUID
    amount: Decimal
    autopay: uuid.UUID
    reported: set = field(default_factory=set)
    recorded: bool = False
    bounced: bool = False


class AutopayMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.pmc = make_pmc(conn, owners=1)
        prop = self.pmc.owners[0].property_id
        first = conn.execute(
            "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
            (self.pmc.prepaid_rent,),
        ).fetchone()[0]
        self.tenants = [first, add_tenant(conn, self.pmc.pmc_id, prop, "Tenant 2")]
        unit = add_unit(conn, self.pmc.pmc_id, prop)
        self.month = months_back(11)
        self.lease = open_lease(
            conn, self.pmc.pmc_id, unit, months_back(13), None, str(RENT), self.tenants
        )
        conn.execute(
            "INSERT INTO trust_payment_settings (pmc_id, online, ach_fee_payer)"
            " VALUES (%s, true, 'company')",
            (self.pmc.pmc_id,),
        )
        charge(conn, self.pmc.pmc_id, self.lease, self.month, str(RENT))
        self.day = self.month
        self.charged = RENT
        self.autopays: list[Autopay] = []
        # (autopay id, due date) -> payment id or the reason none
        self.runs: dict[tuple[uuid.UUID, date], uuid.UUID | str] = {}
        self.paying: dict[uuid.UUID, Paying] = {}
        self.hold: uuid.UUID | None = None
        # The hold's first day and its release's: in force from the first until the release.
        self.holds: list[list] = []
        self.partner = Partner()
        self.submitted: set[uuid.UUID] = set()

    # --- the model ----------------------------------------------------------------------------

    def held_on(self, day: date) -> bool:
        return any(start <= day and (end is None or day < end) for start, end in self.holds)

    def recorded(self) -> Decimal:
        return sum((p.amount for p in self.paying.values() if p.recorded and not p.bounced), ZERO)

    def on_its_way(self) -> Decimal:
        return sum(
            (
                p.amount
                for p in self.paying.values()
                if not p.recorded and not p.reported & {"failed", "returned", "expired"}
            ),
            ZERO,
        )

    def failed_twice(self, a: Autopay) -> bool:
        mine = sorted(
            (
                (due, pid)
                for (aid, due), pid in self.runs.items()
                if aid == a.id and isinstance(pid, uuid.UUID)
            ),
            reverse=True,
        )[:2]
        return len(mine) == 2 and all(
            self.paying[pid].reported & {"failed", "returned"} for _, pid in mine
        )

    def predict_night(self):
        due = self.month  # every month's rent is due on the first
        # First, a stopped autopay's payment never accepted, with nothing reported, is given up.
        stopped = {a.id for a in self.autopays if a.stopped}
        for p in self.paying.values():
            if p.autopay in stopped and p.id not in self.submitted and not p.reported:
                p.reported.add("expired")
                event("an autopay payment given up: its autopay stopped before it was sent")
        for a in self.autopays:
            if a.stopped or a.saved_on is None:
                continue
            if self.failed_twice(a):
                a.stopped = "failed twice"
                event("an autopay stopped after two payments didn't go through")
                continue
            if due < a.saved_on or (a.id, due) in self.runs:
                continue
            if self.held_on(due):
                self.runs[(a.id, due)] = "payment hold"
                continue
            if a.rule == "fixed":
                amount = a.amount
            else:
                amount = max(
                    min(self.charged - self.recorded() - self.on_its_way(), a.amount), ZERO
                )
            if amount <= 0:
                self.runs[(a.id, due)] = "nothing owed"
                continue
            pid = autopay.payment_id_for(a.id, due)
            self.runs[(a.id, due)] = pid
            self.paying[pid] = Paying(pid, amount, a.id)

    # --- rules --------------------------------------------------------------------------------

    @initialize(fixed=st.booleans(), amount=AMOUNTS)
    def a_first_autopay(self, fixed, amount):
        """The first tenant's autopay, saved on the first day: every example starts with one."""
        self.set_up(0, fixed, amount, True)

    @rule(tenant=st.integers(0, 1), fixed=st.booleans(), amount=AMOUNTS, saved=st.booleans())
    def set_up(self, tenant, fixed, amount, saved):
        for a in self.autopays:
            if a.tenant == tenant and a.stopped is None:
                autopay.stop(self.conn, self.pmc.pmc_id, a.id, "replaced")
                a.stopped = "replaced"
        made = self.conn.execute(
            "INSERT INTO trust_autopay (pmc_id, lease_id, tenant_id, rule, amount, partner)"
            " VALUES (%s, %s, %s, %s, %s, 'fake') RETURNING id",
            (
                self.pmc.pmc_id,
                self.lease,
                self.tenants[tenant],
                "fixed" if fixed else "balance",
                amount,
            ),
        ).fetchone()[0]
        self.autopays.append(Autopay(made, tenant, "fixed" if fixed else "balance", amount))
        if saved:
            self.save_one(self.autopays[-1])

    def save_one(self, a: Autopay):
        saved_at = datetime.combine(self.day, time(12), UTC)
        op.record_saved(self.conn, payments.fake_saved(a.id, saved_at), "fake")
        a.saved_on = self.day

    @precondition(lambda self: any(a.saved_on is None for a in self.autopays))
    @rule(which=st.integers(min_value=0))
    def save(self, which):
        unsaved = [a for a in self.autopays if a.saved_on is None]
        self.save_one(unsaved[which % len(unsaved)])

    @precondition(lambda self: any(a.stopped is None for a in self.autopays))
    @rule(which=st.integers(min_value=0))
    def stop(self, which):
        on = [a for a in self.autopays if a.stopped is None]
        a = on[which % len(on)]
        assert autopay.stop(self.conn, self.pmc.pmc_id, a.id, "tenant")
        a.stopped = "tenant"

    @precondition(lambda self: self.month < THIS_MONTH)
    @rule()
    def next_month(self):
        self.month = month_after(self.month)
        self.day = self.month
        charge(self.conn, self.pmc.pmc_id, self.lease, self.month, str(RENT))
        self.charged += RENT

    @rule(day=st.integers(min_value=1, max_value=28))
    def later_in_the_month(self, day):
        later = self.month.replace(day=day)
        if later > self.day and later <= datetime.now(UTC).date():
            self.day = later

    @rule()
    def hold_or_release(self):
        if self.hold is None:
            self.hold = hold_payments(self.conn, self.pmc.pmc_id, self.lease, self.day)
            self.holds.append([self.day, None])
        else:
            ends = self.day + timedelta(days=1)
            release_hold(self.conn, self.pmc.pmc_id, self.hold, ends)
            self.holds[-1][1] = ends
            self.hold = None

    @rule(mode=st.sampled_from(["up", "up", "down", "lost"]))
    def night(self, mode):
        self.partner.mode = mode
        with self.conn.transaction():
            done = autopay.start_due(self.conn, self.pmc.pmc_id, self.day, "fake")
        self.predict_night()
        accepted_before = set(self.submitted)
        autopay.submit_waiting(self.conn, self.partner, self.pmc.pmc_id)
        self.submitted = {
            row[0]
            for row in self.conn.execute(
                "SELECT payment_id FROM trust_autopay_submissions WHERE pmc_id = %s",
                (self.pmc.pmc_id,),
            ).fetchall()
        }
        if mode == "up":
            # Everything still to be sent (nothing reported of it, its autopay not stopped) is
            # accepted once the partner answers.
            stopped = {a.id for a in self.autopays if a.stopped}
            expected = accepted_before | {
                pid
                for pid, p in self.paying.items()
                if pid not in accepted_before and not p.reported and p.autopay not in stopped
            }
            assert self.submitted == expected
        if mode == "lost" and self.submitted == accepted_before and done.payments:
            event("the partner took a payment and its answer was lost")

    @precondition(lambda self: self.month < THIS_MONTH)
    @rule(mode=st.sampled_from(["up", "up", "down", "lost"]))
    def a_month_passes(self, mode):
        """The next month's first day, and its night: most autopays pay then."""
        self.next_month()
        self.night(mode)

    @precondition(lambda self: any(not p.reported for p in self.paying.values()))
    @rule()
    def the_bank_refuses_what_is_pending(self):
        """Every payment nothing was reported of yet doesn't go through (the account closed)."""
        for p in [p for p in self.paying.values() if not p.reported]:
            self.report_one(p, "failed")

    @precondition(lambda self: self.paying)
    @rule(which=st.integers(min_value=0), kind=st.sampled_from(["paid", "failed", "returned"]))
    def report(self, which, kind):
        # Mostly a payment nothing was reported of yet, so reports reach the latest payments.
        quiet = [p for p in self.paying.values() if not p.reported]
        pool = quiet if quiet and which % 4 else list(self.paying.values())
        self.report_one(pool[which % len(pool)], kind)

    def report_one(self, p: Paying, kind: str):
        happened = datetime.combine(self.day, time(12), UTC)
        with self.conn.transaction():
            op.apply(
                self.conn,
                payments.Event(
                    p.id,
                    kind,
                    f"evt_{uuid.uuid4().hex}",
                    happened,
                    ZERO if kind == "paid" else None,
                ),
                "fake",
            )
        allowed = (
            (kind == "paid" and "failed" not in p.reported)
            or (kind == "failed" and "paid" not in p.reported)
            or (kind == "returned" and "paid" in p.reported)
        )
        if not allowed or kind in p.reported:
            return
        p.reported.add(kind)
        if kind == "paid" and not self.held_on(self.day):
            p.recorded = True
        if kind == "returned" and p.recorded:
            p.bounced = True
            event("an autopay payment came back")

    # --- invariants ---------------------------------------------------------------------------

    @invariant()
    def runs_are_the_models(self):
        rows = self.conn.execute(
            "SELECT r.autopay_id, r.due_on, r.payment_id, r.skipped, p.amount, p.lease_id,"
            " p.tenant_id FROM trust_autopay_runs AS r"
            " LEFT JOIN trust_online_payments AS p ON p.id = r.payment_id"
            " JOIN trust_autopay AS a ON a.id = r.autopay_id WHERE a.pmc_id = %s",
            (self.pmc.pmc_id,),
        ).fetchall()
        found = {(aid, due): pid or skipped for aid, due, pid, skipped, *_ in rows}
        assert found == self.runs
        tenant_of = {a.id: self.tenants[a.tenant] for a in self.autopays}
        for aid, _, pid, _, amount, lease, tenant in rows:
            if pid is not None:
                assert (amount, lease, tenant) == (
                    self.paying[pid].amount,
                    self.lease,
                    tenant_of[aid],
                )

    @invariant()
    def stops_are_the_models(self):
        rows = dict(
            self.conn.execute(
                "SELECT s.autopay_id, s.why FROM trust_autopay_stops AS s WHERE s.pmc_id = %s",
                (self.pmc.pmc_id,),
            ).fetchall()
        )
        assert rows == {a.id: a.stopped for a in self.autopays if a.stopped}

    @invariant()
    def the_partner_takes_each_payment_once_and_only_ours(self):
        assert all(n == 1 for n in self.partner.taken.values())
        assert set(self.partner.taken) <= set(self.paying)
        assert self.submitted <= set(self.partner.taken)

    @invariant()
    def trust_ties_out(self):
        assert all(total == 0 for total in bank_sums(self.conn, self.pmc.pmc_id).values())


def test_autopay_pays_each_due_date_once_by_its_rule_and_the_partner_takes_each_once(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: AutopayMachine(conn))
