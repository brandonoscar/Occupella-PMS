"""Property-based tests for payment files (migration 0118, src/pms/api/payment_files.py; the
founder's review q035).

Hypothesis brings in random files of payments (each line naming a lease by its unit, its unit and
property, its payer, or nothing that matches), brings some in twice, records waiting lines to
leases it picks (sometimes past a payment hold), sets lines aside, records payments by hand and
places holds, in random order. A model predicts what each line becomes.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - a file comes in once; bringing it again changes nothing;
  - a line naming exactly one lease in effect is recorded to it when it comes in, unless a payment
    of its amount and day is recorded to that lease already, or a hold refuses it; any other line
    waits;
  - a line is recorded at most once and never also set aside; recording it again records nothing
    new, and once dealt with it can't be recorded or set aside again;
  - what each lease owes, and every tenant payment recorded to it, are the model's, and every trust
    bank account ties out.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import psycopg
from fastapi import HTTPException
from helpers import (
    add_tenant,
    add_unit,
    bank_sums,
    charge,
    hold_payments,
    make_pmc,
    open_lease,
    staff_named,
)
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    consumes,
    invariant,
    rule,
    run_state_machine_as_test,
)

from pms.api import payment_files, rent

TODAY = datetime.now(UTC).date()
START = TODAY - timedelta(days=400)
# Amounts repeat often enough that a line looks like a payment recorded already.
CENTS = st.one_of(
    st.sampled_from([Decimal("100.00"), Decimal("250.00"), Decimal("1500.00")]),
    st.integers(min_value=1, max_value=200_000).map(lambda c: Decimal(c) / 100),
)
DAYS_BACK = st.integers(min_value=0, max_value=3)
# Leases 0-2 at Property 1 (units 1A-1C); 3 and 4 at a unit named Twin at Property 1 and 2.
HOMES = [(0, "1A", "Tenant A"), (0, "1B", "Tenant B"), (0, "1C", "Tenant C")]
HOMES += [(0, "Twin", "Tenant D"), (1, "Twin", "Tenant E")]
# How a line names its lease. A unit alone names a Twin lease no more: two units share the name.
NAMED_BY = st.sampled_from(["unit", "unit and property", "payer", "nobody"])


@dataclass
class Model:
    owed: Decimal
    paid: list = field(default_factory=list)  # (amount, day) of each tenant payment
    holds: list = field(default_factory=list)  # each hold's first day


@dataclass
class Said:
    """What the model expects of a line."""

    lease: int | None  # the lease it names, if exactly one
    amount: Decimal
    day: object
    status: str
    recorded_to: int | None = None


class PaymentFilesMachine(RuleBasedStateMachine):
    lines = Bundle("lines")

    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        pmc = make_pmc(conn)
        self.pmc_id = pmc.pmc_id
        staff = staff_named(conn, pmc.pmc_id, "Staff 1")
        self.session = SimpleNamespace(pmc_id=pmc.pmc_id, person_id=staff)
        self.leases = []
        self.model = []
        for owner, unit, tenant in HOMES:
            prop = pmc.owners[owner].property_id
            tenant_id = add_tenant(conn, pmc.pmc_id, prop, tenant)
            unit_id = add_unit(conn, pmc.pmc_id, prop, unit)
            lease = open_lease(conn, pmc.pmc_id, unit_id, START, None, "1500.00", [tenant_id])
            charge(conn, pmc.pmc_id, lease, START, Decimal("1500.00"))
            self.leases.append(lease)
            self.model.append(Model(owed=Decimal("1500.00")))
        self.files: set[str] = set()
        self.said: dict = {}  # line id -> Said

    # --- the model's view of a payment -----------------------------------------------------

    def refused(self, lease, day, past_hold=False):
        """A hold refuses a payment the lease's charges would take."""
        m = self.model[lease]
        return not past_hold and m.owed > 0 and any(first <= day for first in m.holds)

    def pays(self, lease, amount, day):
        m = self.model[lease]
        m.owed -= min(amount, max(m.owed, Decimal(0)))
        m.paid.append((amount, day))

    def call(self, fn, *args):
        """An API function in its own transaction, as a request is; its refusal, or None."""
        try:
            with self.conn.transaction():
                return fn(*args), None
        except HTTPException as refused:
            return None, refused

    # --- rules --------------------------------------------------------------------------------

    @rule(
        target=lines,
        rows=st.lists(
            st.tuples(st.integers(0, 4), NAMED_BY, CENTS, DAYS_BACK), min_size=1, max_size=5
        ),
        again=st.booleans(),
    )
    def bring_in(self, rows, again):
        text = ["Date,Amount,Property,Unit,Payer"]
        expected = []
        for lease, named, amount, back in rows:
            day = TODAY - timedelta(days=back)
            owner, unit, tenant = HOMES[lease]
            if named == "unit":
                cells, names = ("", unit, ""), None if unit == "Twin" else lease
            elif named == "unit and property":
                cells, names = (f"Property {owner + 1}", unit, ""), lease
            elif named == "payer":
                cells, names = ("", "", tenant), lease
            else:
                cells, names = ("", "", "Nobody Sample"), None
            text.append(f"{day},{amount},{','.join(cells)}")
            expected.append((names, amount, day))
        content = "\n".join(text) + "\n"
        new = payment_files.NewFile(file_name="bank.csv", content=content)
        came, refused = self.call(payment_files.bring_in, self.session, self.conn, new)
        if content in self.files:
            event("a file brought in again")
            assert refused is not None and refused.status_code == 409
            return []
        assert refused is None, refused
        self.files.add(content)
        if again:
            again_came, again_refused = self.call(
                payment_files.bring_in, self.session, self.conn, new
            )
            assert again_came is None and again_refused.status_code == 409
        out = []
        for item, (names, amount, day) in zip(came.items, expected, strict=True):
            if names is None:
                said = Said(None, amount, day, "waiting")
            elif (amount, day) in self.model[names].paid:
                event("a line that looks recorded already")
                said = Said(names, amount, day, "waiting")
            elif self.refused(names, day):
                event("a line a hold refuses")
                said = Said(names, amount, day, "waiting")
            else:
                self.pays(names, amount, day)
                said = Said(names, amount, day, "recorded", recorded_to=names)
            assert item.status == said.status, (item, said)
            assert item.lease_id == (None if said.lease is None else self.leases[said.lease])
            event(f"line {said.status}")
            self.said[item.id] = said
            out.append(item.id)
        return out

    @rule(found=lines, which=st.integers(0, 4), lease=st.integers(0, 5), past_hold=st.booleans())
    def record_one(self, found, which, lease, past_hold):
        if not found:
            return
        line_id = found[which % len(found)]
        if lease == len(HOMES):  # a waiting line to a lease on a hold, if there are both
            held = [i for i, m in enumerate(self.model) if m.holds]
            lease = held[which % len(held)] if held else 0
            waiting = [each for each in found if self.said[each].status == "waiting"]
            line_id = waiting[which % len(waiting)] if waiting else line_id
        said = self.said[line_id]
        to = payment_files.RecordLine(lease_id=self.leases[lease], past_hold=past_hold)
        done, refused = self.call(payment_files.record_line, self.session, self.conn, line_id, to)
        if said.status == "recorded" and said.recorded_to == lease:
            event("a line recorded again to its lease")
            assert done is not None and done.status == "recorded"
        elif said.status != "waiting":
            assert refused is not None and refused.status_code == 409
        elif self.refused(lease, said.day, past_hold):
            event("a waiting line a hold refuses")
            assert refused is not None and refused.status_code == 409
        else:
            event("a waiting line recorded")
            assert refused is None, refused
            self.pays(lease, said.amount, said.day)
            said.status, said.recorded_to = "recorded", lease
            assert done.status == "recorded"

    @rule(found=consumes(lines), which=st.integers(0, 4))
    def set_aside(self, found, which):
        if not found:
            return
        line_id = found[which % len(found)]
        said = self.said[line_id]
        aside = payment_files.SetAside(reason="Not a tenant's payment")
        done, refused = self.call(payment_files.set_aside, self.session, self.conn, line_id, aside)
        if said.status == "waiting":
            assert refused is None, refused
            said.status = "set aside"
            assert done.status == "set aside"
        else:
            assert refused is not None and refused.status_code == 409

    @rule(lease=st.integers(0, 4), amount=CENTS, back=DAYS_BACK)
    def pay_by_hand(self, lease, amount, back):
        day = TODAY - timedelta(days=back)
        if self.refused(lease, day):
            return  # Receive payment would refuse it; tested elsewhere
        with self.conn.transaction():
            rent.record_payment(
                self.conn, self.pmc_id, self.leases[lease], uuid.uuid4(), amount, day
            )
        self.pays(lease, amount, day)

    @rule(lease=st.integers(0, 4), amount=CENTS)
    def charge_more(self, lease, amount):
        charge(self.conn, self.pmc_id, self.leases[lease], TODAY, amount, "fee")
        self.model[lease].owed += amount

    @rule(lease=st.integers(0, 4), back=DAYS_BACK)
    def hold(self, lease, back):
        if self.model[lease].holds:
            return  # one hold at a time
        hold_payments(self.conn, self.pmc_id, self.leases[lease], TODAY - timedelta(days=back))
        self.model[lease].holds.append(TODAY - timedelta(days=back))

    # --- invariants ---------------------------------------------------------------------------

    @invariant()
    def the_books_are_the_models(self):
        for lease, m in zip(self.leases, self.model, strict=True):
            assert rent.owed(self.conn, lease) == m.owed
            paid = self.conn.execute(
                "SELECT (metadata ->> 'amount')::numeric, (metadata ->> 'received_on')::date"
                " FROM pgledger_transfers AS t"
                " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id"
                " WHERE a.pmc_id = %s AND metadata ->> 'kind' = 'tenant payment'"
                " AND metadata ->> 'lease_id' = %s",
                (self.pmc_id, str(lease)),
            ).fetchall()
            # A payment over what is owed is two transfers (the owner's, the prepaid rent), both
            # saying the payment's whole amount and day.
            assert set(paid) == set(m.paid)
            assert sum(Decimal(a) for a, _ in m.paid) == self.cash_in(lease)
        assert set(bank_sums(self.conn, self.pmc_id).values()) == {Decimal(0)}

    def cash_in(self, lease):
        return self.conn.execute(
            "SELECT coalesce(sum(t.amount), 0) FROM pgledger_transfers AS t"
            " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id"
            " WHERE a.pmc_id = %s AND t.metadata ->> 'kind' = 'tenant payment'"
            " AND t.metadata ->> 'lease_id' = %s",
            (self.pmc_id, str(lease)),
        ).fetchone()[0]

    @invariant()
    def each_line_is_what_the_model_says(self):
        rows = self.conn.execute(
            "SELECT l.id, p.lease_id, k.line_id IS NOT NULL FROM trust_payment_file_lines AS l"
            " LEFT JOIN trust_payment_file_postings AS p ON p.line_id = l.id"
            " LEFT JOIN trust_payment_file_skips AS k ON k.line_id = l.id"
            " WHERE l.pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert len(rows) == len(self.said)
        for line_id, recorded_to, aside in rows:
            said = self.said[line_id]
            assert not (recorded_to and aside)
            if said.status == "recorded":
                assert recorded_to == self.leases[said.recorded_to]
            else:
                assert recorded_to is None
            assert aside == (said.status == "set aside")


def test_payment_files_record_each_line_once_to_the_lease_it_names(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: PaymentFilesMachine(conn))
