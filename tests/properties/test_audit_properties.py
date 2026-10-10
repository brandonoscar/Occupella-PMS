"""Property-based tests for the audit log: every write is recorded, with who made it.

Hypothesis makes random writes to one PMC's records and ledger (postings, a batch of two,
units added and renamed, charges, staff added, reconciliations approved), through the owner role
and trust_app, each naming a random member of the PMC's staff, nobody, a member of another
PMC's staff, or someone on no PMC's staff. A Python model predicts every step: the database
must accept exactly what the model accepts and refuse the rest, writing nothing.

Invariants checked after every step (ci/registry.toml maps each to this test):
  - the PMC's audit log is the model's, row for row and in order: each write's table, action,
    the staff member it named (or none) and the role it was made through;
  - a write naming someone not on the PMC's staff is refused and nothing of it is logged;
  - every transfer of the PMC has exactly one audit row;
  - the log only grows: rows once written never change.

The rule check_the_audit_report reads the report for the day, as any role, and compares each
line's staff name, role, action and record with the model.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from helpers import acting_as, add_staff, add_unit, approve, make_pmc, post, transfer_batch
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

ROLES = st.sampled_from(["owner", "app"])
# Who a write names: an index into the PMC's staff, or one of these.
WHOM = st.one_of(
    st.integers(min_value=0, max_value=3),
    st.sampled_from(["nobody", "nobody", "another PMC's staff", "no staff member"]),
)
AMOUNTS = st.integers(min_value=1, max_value=100_000).map(lambda cents: f"{cents / 100:.2f}")
CLOCK_START = datetime(2000, 1, 1, tzinfo=UTC)
DB_ROLE = {"owner": "postgres", "app": "trust_app"}


class AuditMachine(RuleBasedStateMachine):
    def __init__(self, owner_conn, app_conn, ai_conn):
        super().__init__()
        self.conns = {"owner": owner_conn, "app": app_conn, "ai": ai_conn}
        self.conn = owner_conn
        self.pmc = make_pmc(owner_conn)
        self.pmc_id = self.pmc.pmc_id
        self.outsider = add_staff(owner_conn, make_pmc(owner_conn).pmc_id, "Outsider")
        self.staff = [add_staff(owner_conn, self.pmc_id, "Staff 1")]
        self.names = {self.staff[0]: "Staff 1"}
        self.start = self.last_id()  # the setup's rows are the fixtures', not the model's
        self.expected: list[tuple] = []
        self.units: list[uuid.UUID] = []
        self.closed = {self.pmc.operating_bank_id: CLOCK_START}
        self.snapshot: list[tuple] = []

    def last_id(self):
        return self.conn.execute("SELECT coalesce(max(id), 0) FROM trust_audit_log").fetchone()[0]

    def resolve(self, whom):
        """(the value to name, the staff id the log should hold, refused?)"""
        if whom == "nobody":
            return None, None, False
        if whom == "another PMC's staff":
            return self.outsider, None, True
        if whom == "no staff member":
            return uuid.uuid4(), None, True
        staff = self.staff[whom % len(self.staff)]
        return staff, staff, False

    def write(self, role, whom, do, rows):
        """Make one write naming `whom`; `rows` are the (table, action) it logs if accepted."""
        named, staff, refused = self.resolve(whom)
        conn = self.conns[role]
        before = self.last_id()
        with acting_as(conn, named):
            if refused:
                with pytest.raises(psycopg.errors.InvalidParameterValue, match="not a staff"):
                    do(conn)
                assert self.last_id() == before
                return None
            result = do(conn)
        self.expected += [(table, action, staff, DB_ROLE[role]) for table, action in rows]
        return result

    # --- rules ---------------------------------------------------------------------------

    @rule(role=ROLES, whom=WHOM, amount=AMOUNTS)
    def post_money(self, role, whom, amount):
        transfer = [(self.pmc.operating_cash, self.pmc.owners[0].account, amount)]

        def do(conn):
            if role == "owner":
                return transfer_batch(conn, transfer)
            return post(conn, self.pmc_id, str(uuid.uuid4()), transfer)

        self.write(role, whom, do, [("pgledger_transfers", "insert")])

    @rule(role=ROLES, whom=WHOM, amount=AMOUNTS)
    def post_two_at_once(self, role, whom, amount):
        transfers = [
            (self.pmc.operating_cash, self.pmc.owners[1].account, amount),
            (self.pmc.deposit_cash, self.pmc.tenant_deposit, amount),
        ]

        def do(conn):
            if role == "owner":
                return transfer_batch(conn, transfers)
            return post(conn, self.pmc_id, str(uuid.uuid4()), transfers)

        self.write(role, whom, do, [("pgledger_transfers", "insert")] * 2)

    @rule(role=ROLES, whom=WHOM)
    def add_a_unit(self, role, whom):
        name = f"Unit {len(self.units) + 1}"
        unit = self.write(
            role,
            whom,
            lambda conn: add_unit(conn, self.pmc_id, self.pmc.owners[0].property_id, name),
            [("trust_units", "insert")],
        )
        if unit is not None:
            self.units.append(unit)

    @precondition(lambda self: self.units)
    @rule(data=st.data(), role=ROLES, whom=WHOM)
    def rename_a_unit(self, data, role, whom):
        unit = data.draw(st.sampled_from(self.units))
        self.write(
            role,
            whom,
            lambda conn: conn.execute(
                "UPDATE trust_units SET display_name = display_name || 'A' WHERE id = %s",
                (unit,),
            ),
            [("trust_units", "update")],
        )

    @rule(role=ROLES, whom=WHOM)
    def add_a_staff_member(self, role, whom):
        name = f"Staff {len(self.staff) + 1}"
        staff = self.write(
            role,
            whom,
            lambda conn: add_staff(conn, self.pmc_id, name),
            [("trust_staff", "insert")],
        )
        if staff is not None:
            self.staff.append(staff)
            self.names[staff] = name

    @rule(role=ROLES, whom=WHOM, days=st.integers(min_value=1, max_value=40))
    def approve_a_reconciliation(self, role, whom, days):
        bank = self.pmc.operating_bank_id
        start = self.closed[bank]
        end = start + timedelta(days=days)
        done = self.write(
            role,
            whom,
            lambda conn: approve(conn, self.pmc_id, bank, start, end),
            [("trust_reconciliations", "insert"), ("trust_bank_accounts", "update")],
        )
        if done is not None:
            self.closed[bank] = end

    @rule(role=st.sampled_from(["owner", "app", "ai"]))
    def check_the_audit_report(self, role):
        today = datetime.now(UTC).date()
        rows = (
            self.conns[role]
            .execute(
                "SELECT staff, db_role, action, record FROM trust_report_audit_log(%s, %s, %s)"
                " WHERE item = 'change'",
                (self.pmc_id, today - timedelta(days=1), today + timedelta(days=1)),
            )
            .fetchall()
        )
        mine = rows[len(rows) - len(self.expected) :] if self.expected else []
        assert mine == [
            (
                self.names[staff] if staff else "not recorded",
                db_role,
                action,
                table.removeprefix("trust_").removeprefix("pgledger_"),
            )
            for table, action, staff, db_role in self.expected
        ]

    # --- invariants ----------------------------------------------------------------------

    @invariant()
    def the_log_is_the_models(self):
        rows = self.conn.execute(
            "SELECT table_name, action, staff_id, db_role FROM trust_audit_log"
            " WHERE pmc_id = %s AND id > %s ORDER BY id",
            (self.pmc_id, self.start),
        ).fetchall()
        assert rows == self.expected

    @invariant()
    def every_transfer_has_one_audit_row(self):
        (off,) = self.conn.execute(
            "SELECT count(*) FROM pgledger_transfers tr"
            " JOIN trust_ledger_accounts t ON t.ledger_account_id = tr.from_account_id"
            " WHERE t.pmc_id = %s AND (SELECT count(*) FROM trust_audit_log a"
            "   WHERE a.table_name = 'pgledger_transfers' AND a.row_data ->> 'id' = tr.id) <> 1",
            (self.pmc_id,),
        ).fetchone()
        assert off == 0

    @invariant()
    def the_log_only_grows(self):
        rows = self.conn.execute(
            "SELECT id, at, staff_id, db_role, action, table_name, row_data, before"
            " FROM trust_audit_log WHERE pmc_id = %s AND id > %s ORDER BY id",
            (self.pmc_id, self.start),
        ).fetchall()
        assert rows[: len(self.snapshot)] == self.snapshot
        self.snapshot = rows


def test_audit_invariants_hold_under_random_writes_by_random_staff(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as owner_conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
        psycopg.connect(database_url, autocommit=True) as ai_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        ai_conn.execute("SET ROLE trust_ai_agent")
        run_state_machine_as_test(lambda: AuditMachine(owner_conn, app_conn, ai_conn))
