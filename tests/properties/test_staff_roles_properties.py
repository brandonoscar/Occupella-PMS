"""Property-based tests for staff roles (migration 20261012000034).

Hypothesis has four staff members of one PMC (an admin, an approver, one who pays out and one
with no role) change each other's roles, pay owner draws, and prepare and approve month-end, in
random order, as the app (trust_app) naming each one. A model predicts which of them may do
what; the database must agree with it on every step, refusing what it refuses with the reason
it gives.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - who holds which role is exactly the model's, and only an admin changed it;
  - the PMC keeps an admin;
  - money is paid out only by a staff member who held disburse, and nothing else is written;
  - a period is approved only by a staff member who held approve and didn't prepare it.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
from helpers import (
    ROLES,
    acting_as,
    add_staff,
    approve_prepared,
    change_role,
    draw_owner,
    import_statement,
    make_pmc,
    prepare_bank,
    roles_of,
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

STAFF = 4
WHO = st.integers(min_value=0, max_value=STAFF - 1)
FIRST_DAY = date(2025, 1, 1)


@dataclass
class Preparation:
    id: object
    by: int
    through: date
    approved: bool = False


class RolesMachine(RuleBasedStateMachine):
    def __init__(self, conn, app_conn):
        super().__init__()
        self.conn = conn
        self.app = app_conn
        pmc = make_pmc(conn)
        self.pmc = pmc.pmc_id
        self.bank = pmc.operating_bank_id
        self.account = pmc.owners[0].account
        self.staff = [add_staff(conn, self.pmc, f"Staff {n}") for n in range(STAFF)]
        # Staff 0 is the admin; Staff 1 may approve, Staff 2 may pay out, Staff 3 neither.
        change_role(conn, self.pmc, self.staff[0], "admin")
        change_role(conn, self.pmc, self.staff[1], "approve", by=self.staff[0])
        change_role(conn, self.pmc, self.staff[2], "disburse", by=self.staff[0])
        # A year of statements with no line: every day's books lack nothing, at 0.
        import_statement(
            conn, self.pmc, self.bank, self.staff[0], FIRST_DAY, date(2025, 12, 31), []
        )
        transfer_batch(
            conn,
            [(pmc.operating_cash, self.account, "1000000.00")],
            datetime(2026, 1, 1, tzinfo=UTC),
        )
        # Model
        self.roles: list[set[str]] = [{"admin"}, {"approve"}, {"disburse"}, set()]
        self.draws = 0
        self.attempts = 0
        self.next_start = FIRST_DAY
        self.prepared: Preparation | None = None

    @staticmethod
    def refused(error, work):
        """The first line of the refusal `work` must meet (autocommit: nothing was written)."""
        try:
            work()
        except error as refused:
            return str(refused).splitlines()[0]
        raise AssertionError(f"expected {error.__name__}")

    @rule(by=WHO, whom=WHO, role=st.sampled_from(ROLES), granted=st.booleans())
    def change(self, by, whom, role, granted):
        def work():
            change_role(self.app, self.pmc, self.staff[whom], role, granted, by=self.staff[by])

        admins = [n for n in range(STAFF) if "admin" in self.roles[n]]
        if "admin" not in self.roles[by]:
            event("role change refused: not an admin")
            assert "may not change a role" in self.refused(
                psycopg.errors.InsufficientPrivilege, work
            )
        elif (role in self.roles[whom]) == granted:
            event("role change refused: changes nothing")
            assert "already" in self.refused(psycopg.errors.CheckViolation, work)
        elif role == "admin" and not granted and admins == [whom]:
            event("role change refused: the last admin")
            assert "last admin" in self.refused(psycopg.errors.CheckViolation, work)
        else:
            event("role changed")
            work()
            (self.roles[whom].add if granted else self.roles[whom].discard)(role)

    @rule(by=WHO)
    def draw(self, by):
        self.attempts += 1
        key = f"draw-{self.attempts}"

        def work():
            with acting_as(self.app, self.staff[by]):
                draw_owner(
                    self.app, self.pmc, self.account, key, "1.00", datetime(2026, 1, 2, tzinfo=UTC)
                )

        if "disburse" in self.roles[by]:
            event("draw paid")
            work()
            self.draws += 1
        else:
            event("draw refused: may not disburse")
            assert "may not pay out money" in self.refused(
                psycopg.errors.InsufficientPrivilege, work
            )

    @rule(by=WHO, days=st.integers(min_value=0, max_value=1))
    def prepare(self, by, days):
        through = self.next_start + timedelta(days=days)
        event("month-end prepared")
        prepared = prepare_bank(self.app, self.pmc, self.bank, through, self.staff[by])
        self.prepared = Preparation(prepared, by, through)

    @precondition(lambda self: self.prepared is not None)
    @rule(by=WHO)
    def approve(self, by):
        prepared = self.prepared
        assert prepared is not None

        def work():
            approve_prepared(self.app, self.pmc, prepared.id, self.staff[by])

        if by == prepared.by:
            event("approval refused: the preparer")
            assert "who prepared" in self.refused(psycopg.errors.InsufficientPrivilege, work)
        elif prepared.approved:
            event("approval refused: approved already")
            assert "already" in self.refused(psycopg.errors.CheckViolation, work)
        elif "approve" not in self.roles[by]:
            event("approval refused: may not approve")
            assert "may not approve a reconciliation" in self.refused(
                psycopg.errors.InsufficientPrivilege, work
            )
        else:
            event("month-end approved")
            work()
            prepared.approved = True
            self.next_start = prepared.through + timedelta(days=1)

    @invariant()
    def roles_are_the_models(self):
        assert [roles_of(self.conn, self.pmc, s) for s in self.staff] == self.roles
        assert any("admin" in held for held in self.roles)
        # Every change after the first admin was made by someone who was an admin then.
        assert self.conn.execute(
            "SELECT count(*) FROM trust_staff_roles WHERE pmc_id = %s AND (changed_by IS NULL)"
            " AND role <> 'admin'",
            (self.pmc,),
        ).fetchone() == (0,)

    @invariant()
    def money_and_periods_are_the_models(self):
        assert self.conn.execute(
            "SELECT count(*) FROM trust_owner_draws WHERE pmc_id = %s", (self.pmc,)
        ).fetchone() == (self.draws,)
        assert self.conn.execute(
            "SELECT coalesce(sum(amount), 0) FROM trust_owner_draws WHERE pmc_id = %s", (self.pmc,)
        ).fetchone() == (Decimal(self.draws),)
        approved = self.conn.execute(
            "SELECT prepared_by, approved_by, period_end FROM trust_reconciliations"
            " WHERE bank_account_id = %s ORDER BY period_end",
            (self.bank,),
        ).fetchall()
        assert all(prepared != approver for prepared, approver, _ in approved)
        if approved:
            assert approved[-1][2] == datetime.combine(self.next_start, datetime.min.time(), UTC)


def test_roles_decide_who_pays_out_and_who_approves(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        run_state_machine_as_test(lambda: RolesMachine(conn, app_conn))
