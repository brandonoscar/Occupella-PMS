"""Property-based tests for Receive payment's choice of charges and for leases going month to month
(ADR 0010 phase 1c, migration 0154; src/pms/api/rent.py).

Charges: Hypothesis charges a lease rent and fees on random days and records payments of random
amounts, some choosing open charges (and how much of each) to pay first, some choosing what can't
be chosen. Each payment's matches are predicted from the lease's open charges just before it.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - a payment pays each charge chosen exactly what was chosen, then spreads what is left over the
    lease's open charges in the usual order (rent first, oldest first), never past what is open;
  - a choice that doesn't fit (a charge not open, chosen twice, more than is open of it, more than
    the payment) records nothing;
  - what the lease owes is the model's, and every trust bank account ties out.

Month to month: Hypothesis opens fixed-term leases ending on random days for a company in a
random time zone, each entered some hours before or after its last day ended there, gives some an
end of staff's (a notice), and runs nights forward in random steps. A lease goes month to month on
the first night after its term ran out within the week, once, unless staff gave its end or it was
entered after its last day by the company's day; its days and rent are otherwise unchanged.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg
from fastapi import HTTPException
from helpers import add_tenant, add_unit, bank_sums, charge, make_pmc, open_lease
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
    run_state_machine_as_test,
)

from pms.api import rent

DAY = timedelta(days=1)


def on_this_day() -> None:
    """Today's dates (UTC), worked out again before each test (tests/conftest.py,
    pytest_runtest_setup), so a run past midnight compares them with the database's."""
    global TODAY, START
    TODAY = datetime.now(UTC).date()
    START = TODAY - 400 * DAY


on_this_day()
CENTS = st.integers(min_value=1, max_value=300_000).map(lambda c: Decimal(c) / 100)
# Which open charge a pick names (by its place among them) and how much of what is open of it.
PICKS = st.lists(st.tuples(st.integers(0, 9), st.integers(1, 100)), min_size=1, max_size=4)


def cents(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"))


class ChosenChargesMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        pmc = make_pmc(conn)
        self.pmc_id = pmc.pmc_id
        prop = pmc.owners[0].property_id
        tenant = add_tenant(conn, pmc.pmc_id, prop, "Tenant 1")
        unit = add_unit(conn, pmc.pmc_id, prop, "Unit 1")
        self.lease = open_lease(conn, pmc.pmc_id, unit, START, None, "1500.00", [tenant])
        self.owed = Decimal(0)
        self.rent_days: set = set()

    def opens(self) -> list[tuple[uuid.UUID, Decimal]]:
        return rent.open_charges(self.conn, self.pmc_id, self.lease)

    def pay(self, amount, chosen):
        with self.conn.transaction():
            return rent.record_payment(
                self.conn, self.pmc_id, self.lease, uuid.uuid4(), amount, TODAY, chosen=chosen
            )

    @rule(amount=CENTS, back=st.integers(0, 300), fee=st.booleans())
    def charge_it(self, amount, back, fee):
        due = TODAY - back * DAY
        if not fee:
            if due in self.rent_days:
                return  # rent is charged once a due date
            self.rent_days.add(due)
        charge(self.conn, self.pmc_id, self.lease, due, amount, "fee" if fee else "rent")
        self.owed += amount

    @rule(amount=CENTS, picks=PICKS)
    def pay_choosing(self, amount, picks):
        before = self.opens()
        if not before:
            return
        chosen: dict[uuid.UUID, Decimal] = {}
        for place, percent in picks:
            charge_id, left = before[place % len(before)]
            part = max(cents(left * percent / 100), Decimal("0.01"))
            if charge_id in chosen or sum(chosen.values()) + part > amount:
                continue
            chosen[charge_id] = part
        event(f"charges chosen: {len(chosen)}")
        recorded = self.pay(amount, list(chosen.items()) or None)

        # Chosen first, then what is left of the payment over the rest in the usual order.
        rest = [(c, left - chosen.get(c, Decimal(0))) for c, left in before]
        expected = dict(chosen)
        spent = sum(chosen.values(), Decimal(0))
        for charge_id, part in rent.allocate([(c, x) for c, x in rest if x > 0], amount - spent):
            expected[charge_id] = expected.get(charge_id, Decimal(0)) + part
        matched = dict(
            self.conn.execute(
                "SELECT charge_id, sum(amount) FROM trust_charge_payments"
                " WHERE transfer_id = ANY(%s) GROUP BY charge_id",
                (recorded.transfers,),
            ).fetchall()
        )
        assert matched == expected
        self.owed -= min(amount, self.owed)

    @rule(amount=CENTS, place=st.integers(0, 9), how=st.sampled_from(["twice", "over", "beyond"]))
    def choose_what_does_not_fit(self, amount, place, how):
        before = self.opens()
        if not before:
            return
        charge_id, left = before[place % len(before)]
        if how == "twice":
            chosen = [(charge_id, Decimal("0.01")), (charge_id, Decimal("0.01"))]
        elif how == "over":
            chosen = [(charge_id, left + Decimal("0.01"))]
        else:  # more than the payment
            if left <= Decimal("0.01"):
                return
            amount = min(amount, left - Decimal("0.01"))
            chosen = [(charge_id, left)]
        try:
            self.pay(amount, chosen)
        except HTTPException as refused:
            assert refused.status_code == 422
        else:
            raise AssertionError(f"a choice {how} was recorded")

    @invariant()
    def the_books_are_the_models(self):
        assert rent.owed(self.conn, self.lease) == self.owed
        over = self.conn.execute(
            "SELECT count(*) FROM trust_charges AS c WHERE c.lease_id = %s AND c.amount <"
            " (SELECT coalesce(sum(p.amount), 0) FROM trust_charge_payments AS p"
            "  WHERE p.charge_id = c.id)",
            (self.lease,),
        ).fetchone()[0]
        assert over == 0
        assert set(bank_sums(self.conn, self.pmc_id).values()) == {Decimal(0)}


def test_a_payment_pays_the_charges_chosen_first_and_the_rest_as_before(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: ChosenChargesMachine(conn))


class MonthToMonthMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.night = TODAY
        self.leases: list[uuid.UUID] = []
        self.ends: list = []  # each lease's end as the model has it (None: month to month)
        self.term: list = []  # the term that ran out, once it went month to month
        self.notice: list[bool] = []  # staff gave its end, or it was entered after it

    @initialize(
        zone=st.sampled_from(["UTC", "America/New_York", "Pacific/Honolulu"]),
        # Each lease's last day, and when it was entered: hours before (or after) the end of
        # that day in the company's own time zone (docs/edge-cases.md D-055).
        ends=st.lists(st.tuples(st.integers(0, 12), st.integers(-30, 6)), min_size=1, max_size=4),
    )
    def open_leases(self, zone, ends):
        pmc = make_pmc(self.conn, time_zone=zone)
        self.pmc_id, self.prop = pmc.pmc_id, pmc.owners[0].property_id
        for i, (after, entered) in enumerate(ends):
            unit = add_unit(self.conn, self.pmc_id, self.prop, f"Unit {i + 1}")
            tenant = add_tenant(self.conn, self.pmc_id, self.prop, f"Tenant {i + 1}")
            end = TODAY + after * DAY
            self.leases.append(
                open_lease(
                    self.conn, self.pmc_id, unit, TODAY - 200 * DAY, end, "1200.00", [tenant]
                )
            )
            midnight = datetime.combine(end + DAY, time(), ZoneInfo(zone))
            self.conn.execute(
                "UPDATE trust_leases SET created_at = %s WHERE id = %s",
                (midnight + timedelta(hours=entered), self.leases[-1]),
            )
            if entered >= 0:
                event("a lease entered after its last day, in the company's day")
            self.ends.append(end)
            self.term.append(None)
            self.notice.append(entered >= 0)  # a lease from the past stays as it was

    @rule(steps=st.integers(1, 9))
    def a_night(self, steps):
        self.night += steps * DAY
        expected = 0
        for i, end in enumerate(self.ends):
            if (
                end is not None
                and self.term[i] is None
                and not self.notice[i]
                and self.night - 7 * DAY <= end < self.night
            ):
                self.term[i], self.ends[i] = end, None
                expected += 1
        event(f"rolled {expected}")
        rolled = self.conn.execute(
            "SELECT trust_roll_over_leases(%s, %s)", (self.pmc_id, self.night)
        ).fetchone()[0]
        assert rolled == expected

    @rule(which=st.integers(0, 3), later=st.integers(0, 20))
    def staff_give_an_end(self, which, later):
        # A move-out or a tenant's notice, as POST /api/move-out/{id}/end records it.
        i = which % len(self.leases)
        end = self.night + later * DAY
        self.conn.execute("SELECT trust_end_lease(%s, %s, %s)", (self.pmc_id, self.leases[i], end))
        self.conn.execute(
            "INSERT INTO trust_lease_end_notices (pmc_id, lease_id, ends_on) VALUES (%s, %s, %s)",
            (self.pmc_id, self.leases[i], end),
        )
        self.ends[i], self.notice[i] = end, True

    @invariant()
    def each_lease_is_the_models(self):
        rows = dict(
            self.conn.execute(
                "SELECT id, ends_on FROM trust_leases WHERE pmc_id = %s", (self.pmc_id,)
            ).fetchall()
        )
        assert [rows[lease] for lease in self.leases] == self.ends
        held = dict(
            self.conn.execute(
                "SELECT lease_id, term_ended_on FROM trust_lease_holdovers WHERE pmc_id = %s",
                (self.pmc_id,),
            ).fetchall()
        )
        assert held == {
            lease: term for lease, term in zip(self.leases, self.term, strict=True) if term
        }
        # The same rent: going month to month changes nothing else of the lease.
        rents = self.conn.execute(
            "SELECT DISTINCT monthly_rent FROM trust_leases WHERE pmc_id = %s", (self.pmc_id,)
        ).fetchall()
        assert rents == [(Decimal("1200.00"),)]


def test_a_lease_goes_month_to_month_once_when_its_term_runs_out_without_an_end_of_staffs(
    database_url,
):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: MonthToMonthMachine(conn))
