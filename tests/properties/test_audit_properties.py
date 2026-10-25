"""Property-based tests for the audit log: every write is recorded, with who made it.

Hypothesis makes random writes to one PMC's records and ledger (postings, a batch of two,
units added and renamed, charges, staff added, reconciliations approved, owners invited,
invites accepted, sessions signed out, maintenance requests filed and given work orders),
through the owner role and trust_app, each naming a random member of the PMC's staff, nobody,
a member of another PMC's staff, or someone on no PMC's staff. A Python model predicts every
step: the database must accept exactly what the model accepts and refuse the rest, writing
nothing.

Invariants checked after every step (ci/registry.toml maps each to this test):
  - the PMC's audit log is the model's, row for row and in order: each write's table, action,
    the staff member it named (or none) and the role it was made through;
  - a write naming someone not on the PMC's staff is refused and nothing of it is logged;
  - every transfer of the PMC has exactly one audit row;
  - the log only grows: rows once written never change;
  - no password hash, and nothing of trust_login_secrets, is ever in the log;
  - the PMC's maintenance requests are the model's, each filed by a tenant on its lease and
    never changed, each with at most the one work order the model gave it, at the lease's unit.
    A tenant who isn't on the lease is refused, and so is a second work order for a request.

The rule check_the_audit_report reads the report for the day, as any role, and compares each
line's staff name, role, action and record with the model; the AI's role sees no line of a
table it may never read (logins, invites, sessions: migration 20261013000043).

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from argon2 import PasswordHasher
from helpers import (
    acting_as,
    add_staff,
    add_tenant,
    add_unit,
    add_work_order,
    approve,
    file_request,
    give_roles,
    link_work_order,
    make_pmc,
    open_lease,
    post,
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
from test_privileges import AI_NEVER_READS

from pms import accounts

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
        # Staff 1 is the PMC's admin and may approve; staff added later hold no role.
        give_roles(owner_conn, self.pmc_id, self.staff[0], ["admin", "approve"])
        self.approvers = {self.staff[0]}
        # A home with a lease: its tenant files maintenance requests; a neighbour can't.
        self.property_id = self.pmc.owners[0].property_id
        self.home = add_unit(owner_conn, self.pmc_id, self.property_id, "Home")
        self.renter = add_tenant(owner_conn, self.pmc_id, self.property_id, "Renter")
        self.neighbour = add_tenant(owner_conn, self.pmc_id, self.property_id, "Neighbour")
        self.lease = open_lease(
            owner_conn, self.pmc_id, self.home, CLOCK_START.date(), None, "1000", [self.renter]
        )
        self.start = self.last_id()  # the setup's rows are the fixtures', not the model's
        self.expected: list[tuple] = []
        self.units: list[uuid.UUID] = []
        self.closed = {self.pmc.operating_bank_id: CLOCK_START}
        self.snapshot: list[tuple] = []
        self.logins: dict[int, uuid.UUID] = {}  # owner index -> login
        self.pending: list[tuple[str, int]] = []  # (invite token, owner index), not accepted
        self.live: dict[uuid.UUID, list[str]] = {}  # login -> its live sessions' tokens
        self.requests: dict[uuid.UUID, tuple[str, bool]] = {}  # request -> (summary, entry)
        self.orders: dict[uuid.UUID, uuid.UUID] = {}  # request -> its work order

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
        named, _, _ = self.resolve(whom)
        if role == "app" or (named is not None and named not in self.approvers):
            # The app approves only a month-end preparation, never the core function; a named
            # staff member needs the approve role (another PMC's staff and nobody at all hold
            # none). Refused before anything is written, so nothing is logged.
            before = self.last_id()
            with (
                acting_as(self.conns[role], named),
                pytest.raises(psycopg.errors.InsufficientPrivilege),
            ):
                approve(self.conns[role], self.pmc_id, bank, start, end)
            assert self.last_id() == before
            return
        done = self.write(
            role,
            whom,
            lambda conn: approve(conn, self.pmc_id, bank, start, end),
            [("trust_reconciliations", "insert"), ("trust_bank_accounts", "update")],
        )
        if done is not None:
            self.closed[bank] = end

    # --- logins: invites, accepting one, signing out (always as trust_app, like the API) ---

    @rule(whom=WHOM, n=st.integers(min_value=0, max_value=1))
    def invite_an_owner(self, whom, n):
        owner_id = self.pmc.owners[n].owner_id
        rows = [("trust_invites", "insert")]
        if n not in self.logins:
            rows.insert(0, ("trust_logins", "insert"))

        def do(conn):
            with conn.transaction():
                return accounts.create_invite(
                    conn, self.pmc_id, "owner", owner_id, f"owner{n}@example.com", None
                )

        made = self.write("app", whom, do, rows)
        if made is not None:
            self.logins[n] = made.login_id
            self.pending.append((made.token, n))

    @precondition(lambda self: self.pending)
    @rule(data=st.data(), whom=WHOM)
    def accept_an_invite(self, data, whom):
        """Sets the password (never logged), uses the invite, ends the login's live sessions
        and opens a new one."""
        token, n = data.draw(st.sampled_from(self.pending))
        login = self.logins[n]
        live = self.live.get(login, [])

        def do(conn):
            with conn.transaction():
                return accounts.accept_invite(conn, token, "a long enough password")

        signed = self.write(
            "app",
            whom,
            do,
            [("trust_invites", "update")]
            + [("trust_sessions", "update")] * len(live)
            + [("trust_sessions", "insert")],
        )
        if signed is not None:
            self.pending.remove((token, n))
            self.live[login] = [signed.token]

    @precondition(lambda self: any(self.live.values()))
    @rule(data=st.data(), whom=WHOM)
    def sign_a_session_out(self, data, whom):
        login = data.draw(st.sampled_from([key for key, tokens in self.live.items() if tokens]))
        token = self.live[login][0]

        def do(conn):
            with conn.transaction():
                accounts.sign_out(conn, token)
            return True

        if self.write("app", whom, do, [("trust_sessions", "update")]) is not None:
            self.live[login].remove(token)

    # --- maintenance requests (migration 20261011000029) ----------------------------------

    @rule(role=ROLES, whom=WHOM, entry=st.booleans())
    def file_a_request(self, role, whom, entry):
        summary = f"Request {len(self.requests) + 1}"
        request = self.write(
            role,
            whom,
            lambda conn: file_request(conn, self.pmc_id, self.lease, self.renter, summary, entry),
            [("trust_maintenance_requests", "insert")],
        )
        if request is not None:
            self.requests[request] = (summary, entry)

    @rule(role=ROLES)
    def file_under_someone_elses_lease(self, role):
        before = self.last_id()
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            file_request(self.conns[role], self.pmc_id, self.lease, self.neighbour, "Not mine")
        assert self.last_id() == before

    def order_for(self, conn, request):
        with conn.transaction():
            order = add_work_order(
                conn, self.pmc_id, self.property_id, self.home, "From a request", CLOCK_START
            )
            link_work_order(conn, self.pmc_id, request, order)
        return order

    @precondition(lambda self: any(r not in self.orders for r in self.requests))
    @rule(data=st.data(), role=ROLES, whom=WHOM)
    def open_a_work_order_for_a_request(self, data, role, whom):
        # Requests in the order they were filed, so a replayed example picks the same one.
        request = data.draw(st.sampled_from([r for r in self.requests if r not in self.orders]))
        order = self.write(
            role,
            whom,
            lambda conn: self.order_for(conn, request),
            [("trust_work_orders", "insert"), ("trust_maintenance_request_work_orders", "insert")],
        )
        if order is not None:
            self.orders[request] = order

    @precondition(lambda self: self.orders)
    @rule(data=st.data(), role=ROLES)
    def open_a_second_work_order(self, data, role):
        request = data.draw(st.sampled_from(list(self.orders)))
        before = self.last_id()
        with pytest.raises(psycopg.errors.UniqueViolation):
            self.order_for(self.conns[role], request)
        assert self.last_id() == before

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
        seen = [
            (table, action, staff, db_role)
            for table, action, staff, db_role in self.expected
            if role != "ai" or table not in AI_NEVER_READS
        ]
        mine = rows[len(rows) - len(seen) :] if seen else []
        assert mine == [
            (
                self.names[staff] if staff else "not recorded",
                db_role,
                action,
                table.removeprefix("trust_").removeprefix("pgledger_"),
            )
            for table, action, staff, db_role in seen
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
        # Every audit row of a transfer counted once, whichever PMC it names, then each of this
        # PMC's transfers held to one: a scan of the log per check, not one per transfer (the
        # log holds every earlier test's rows, so that grew with the square of the suite).
        (off,) = self.conn.execute(
            "SELECT count(*) FROM pgledger_transfers tr"
            " JOIN trust_ledger_accounts t ON t.ledger_account_id = tr.from_account_id"
            " LEFT JOIN (SELECT a.row_data ->> 'id' AS id, count(*) AS rows FROM trust_audit_log a"
            "   WHERE a.table_name = 'pgledger_transfers' GROUP BY 1) AS logged"
            " ON logged.id = tr.id"
            " WHERE t.pmc_id = %s AND coalesce(logged.rows, 0) <> 1",
            (self.pmc_id,),
        ).fetchone()
        assert off == 0

    @invariant()
    def no_secret_is_logged(self):
        (found,) = self.conn.execute(
            "SELECT count(*) FROM trust_audit_log WHERE pmc_id = %s AND id > %s"
            " AND (table_name = 'trust_login_secrets' OR row_data::text LIKE '%%$argon2%%'"
            " OR before::text LIKE '%%$argon2%%')",
            (self.pmc_id, self.start),
        ).fetchone()
        assert found == 0

    @invariant()
    def requests_are_the_models_on_their_lease(self):
        rows = self.conn.execute(
            "SELECT r.id, r.summary, r.entry_allowed, w.work_order_id, wo.unit_id,"
            " EXISTS (SELECT 1 FROM trust_lease_tenants AS lt"
            "         WHERE lt.lease_id = r.lease_id AND lt.tenant_id = r.tenant_id)"
            " FROM trust_maintenance_requests AS r"
            " LEFT JOIN trust_maintenance_request_work_orders AS w ON w.request_id = r.id"
            " LEFT JOIN trust_work_orders AS wo ON wo.id = w.work_order_id"
            " WHERE r.pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert {row[0]: (row[1], row[2], row[3]) for row in rows} == {
            request: (summary, entry, self.orders.get(request))
            for request, (summary, entry) in self.requests.items()
        }
        assert all(on_lease and unit in (None, self.home) for *_, unit, on_lease in rows)
        assert all(unit == self.home for _, _, _, order, unit, _ in rows if order is not None)

    @invariant()
    def the_log_only_grows(self):
        rows = self.conn.execute(
            "SELECT id, at, staff_id, db_role, action, table_name, row_data, before"
            " FROM trust_audit_log WHERE pmc_id = %s AND id > %s ORDER BY id",
            (self.pmc_id, self.start),
        ).fetchall()
        assert rows[: len(self.snapshot)] == self.snapshot
        self.snapshot = rows


def test_audit_invariants_hold_under_random_writes_by_random_staff(database_url, monkeypatch):
    # The hash's strength isn't what this checks; a cheap one keeps each example fast.
    monkeypatch.setattr(
        accounts, "HASHER", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    )
    with (
        psycopg.connect(database_url, autocommit=True) as owner_conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
        psycopg.connect(database_url, autocommit=True) as ai_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        ai_conn.execute("SET ROLE trust_ai_agent")
        run_state_machine_as_test(lambda: AuditMachine(owner_conn, app_conn, ai_conn))
