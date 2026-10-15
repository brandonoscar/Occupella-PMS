"""Property-based tests for custom staff roles (migration 20261015000057).

Hypothesis has three staff members of one PMC (an admin and two without the admin role) make
roles, allow and stop allowing areas, and give and take away roles, in random order, as the
app (trust_app) naming each one. A model predicts what each step does; the database must agree,
refusing every change by anyone but the admin.

Invariant checked after every step (ci/registry.toml maps it to this test):
  - what each staff member may change, area by area, is the model's: everything when they hold
    no custom role, else the union of the areas their roles allow.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import psycopg
from helpers import acting_as, add_staff, change_role, make_pmc
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule
from hypothesis.stateful import run_state_machine_as_test as run

from pms.api.deps import AREAS

STAFF = 3  # 0 is the admin
WHO = st.integers(min_value=0, max_value=STAFF - 1)
AREA = st.sampled_from(list(AREAS))


class CustomRolesMachine(RuleBasedStateMachine):
    def __init__(self, conn, app_conn):
        super().__init__()
        self.conn = conn
        self.app = app_conn
        pmc = make_pmc(conn)
        self.pmc = pmc.pmc_id
        self.staff = [add_staff(conn, self.pmc, f"Staff {n}") for n in range(STAFF)]
        change_role(conn, self.pmc, self.staff[0], "admin")
        # Model
        self.roles: list[object] = []
        self.allows: list[set[str]] = []
        self.held: list[set[int]] = [set() for _ in range(STAFF)]

    def write(self, by, query, params):
        """The change as the app makes it for a staff member: done, or refused for not being
        the admin (autocommit: nothing written)."""
        with acting_as(self.app, self.staff[by]):
            if by == 0:
                done = self.app.execute(query, params)
                return done.fetchone() if "RETURNING" in query else None
            try:
                self.app.execute(query, params)
            except psycopg.errors.InsufficientPrivilege as refused:
                assert "may not change a custom role (the admin role)" in str(refused)
                event("change refused: not the admin")
                return "refused"
            raise AssertionError("a change by someone not the admin went through")

    @rule(by=WHO, areas=st.sets(AREA, max_size=3))
    def make_role(self, by, areas):
        name = f"Role {len(self.roles)}-{by}"
        row = self.write(
            by,
            "INSERT INTO trust_custom_roles (pmc_id, name) VALUES (%s, %s) RETURNING id",
            (self.pmc, name),
        )
        if by != 0:
            return
        event("role made")
        self.roles.append(row[0])
        self.allows.append(set())
        for area in areas:
            self.allow(0, len(self.roles) - 1, area, True)

    @precondition(lambda self: self.roles)
    @rule(by=WHO, which=st.integers(min_value=0, max_value=9), area=AREA, granted=st.booleans())
    def allow(self, by, which, area, granted):
        which %= len(self.roles)
        done = self.write(
            by,
            "INSERT INTO trust_custom_role_areas (pmc_id, role_id, area, granted)"
            " VALUES (%s, %s, %s, %s)",
            (self.pmc, self.roles[which], area, granted),
        )
        if done != "refused":
            event("area allowed" if granted else "area taken away")
            (self.allows[which].add if granted else self.allows[which].discard)(area)

    @precondition(lambda self: self.roles)
    @rule(by=WHO, whom=WHO, which=st.integers(min_value=0, max_value=9), granted=st.booleans())
    def give(self, by, whom, which, granted):
        which %= len(self.roles)
        done = self.write(
            by,
            "INSERT INTO trust_staff_custom_roles (pmc_id, staff_id, role_id, granted)"
            " VALUES (%s, %s, %s, %s)",
            (self.pmc, self.staff[whom], self.roles[which], granted),
        )
        if done != "refused":
            event("role given" if granted else "role taken away")
            (self.held[whom].add if granted else self.held[whom].discard)(which)

    @invariant()
    def what_each_may_change_is_the_models(self):
        for n, staff in enumerate(self.staff):
            expected = (
                set(AREAS)
                if not self.held[n]
                else set().union(*(self.allows[r] for r in self.held[n]))
            )
            row = self.app.execute(
                "SELECT array(SELECT a FROM unnest(%s::text[]) AS a"
                " WHERE trust_staff_may(%s, %s, a))",
                (list(AREAS), self.pmc, staff),
            ).fetchone()
            assert set(row[0]) == expected


def test_custom_roles_decide_what_each_staff_member_may_change(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        run(lambda: CustomRolesMachine(conn, app_conn))
