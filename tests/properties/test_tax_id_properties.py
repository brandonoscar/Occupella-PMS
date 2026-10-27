"""Property-based tests for tax IDs (ADR 0010 phase 2, part F; migration 20261027000155).

Hypothesis has three staff members of one PMC keep owners' and a vendor's tax IDs, try to see
them whole, and give and take the admin role among themselves, in random order, as the app
(trust_app) naming each one. A model predicts who may see a tax ID whole: only a staff member who
holds admin at that moment. The database must agree on every step, refusing the rest.

Invariants checked after every step (ci/registry.toml, tax-id-whole-only-to-an-admin):
  - every reveal on record names a staff member who held admin when it was written, and the
    reveals are exactly the model's;
  - each payee's latest tax ID opens, under the server's key and its row's id, to the nine
    digits last kept for them, its last four in the clear;
  - no row of the company's audit log holds a kept tax ID's digits in the clear.

The digits are of the forms never issued (an EIN starting 00, an SSN starting 000), as
tools/check_synthetic_data.py and .gitleaks.toml ask.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import os
import uuid

import psycopg
from helpers import acting_as, add_staff, change_role, make_pmc
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from pms import sealed

STAFF = 3
WHO = st.integers(min_value=0, max_value=STAFF - 1)
# Two owners and the vendor make_pmc gives.
PAYEES = 3
PAYEE = st.integers(min_value=0, max_value=PAYEES - 1)
# The form's digits after its never-issued prefix: 7 for an EIN (00), 6 for an SSN (000).
REST = st.integers(min_value=0, max_value=9_999_999)


class TaxIdMachine(RuleBasedStateMachine):
    def __init__(self, conn, app_conn):
        super().__init__()
        self.conn = conn
        self.app = app_conn
        pmc = make_pmc(conn)
        self.pmc = pmc.pmc_id
        self.payees = [
            ("owner", pmc.owners[0].owner_id),
            ("owner", pmc.owners[1].owner_id),
            ("vendor", pmc.vendor_id),
        ]
        self.staff = [add_staff(conn, self.pmc, f"Staff {n}") for n in range(STAFF)]
        change_role(conn, self.pmc, self.staff[0], "admin")
        self.key = os.urandom(32)
        # Model
        self.admins = {0}
        self.latest: dict[int, tuple[str, str]] = {}
        self.kept: set[str] = set()
        self.reveals: list[uuid.UUID] = []

    @rule(by=WHO, whom=WHO, granted=st.booleans())
    def change_admin(self, by, whom, granted):
        def work():
            change_role(self.app, self.pmc, self.staff[whom], "admin", granted, by=self.staff[by])

        if by not in self.admins or (whom in self.admins) == granted:
            event("admin change refused")
            try:
                work()
            except (psycopg.errors.InsufficientPrivilege, psycopg.errors.CheckViolation):
                return
            raise AssertionError("expected a refusal")
        if not granted and self.admins == {whom}:
            event("admin change refused: the last admin")
            try:
                work()
            except psycopg.errors.CheckViolation as refused:
                assert "last admin" in str(refused)
                return
            raise AssertionError("expected a refusal")
        event("admin given" if granted else "admin taken away")
        work()
        (self.admins.add if granted else self.admins.discard)(whom)

    @rule(by=WHO, payee=PAYEE, kind=st.sampled_from(("EIN", "SSN")), rest=REST)
    def keep(self, by, payee, kind, rest):
        digits = f"00{rest:07d}" if kind == "EIN" else f"000{rest % 1_000_000:06d}"
        who, payee_id = self.payees[payee]
        row_id = uuid.uuid4()
        with acting_as(self.app, self.staff[by]):
            self.app.execute(
                "INSERT INTO trust_tax_ids"
                " (id, pmc_id, owner_id, vendor_id, kind, tax_id_sealed, last4)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    row_id,
                    self.pmc,
                    payee_id if who == "owner" else None,
                    payee_id if who == "vendor" else None,
                    kind,
                    sealed.seal(digits, str(row_id), self.key),
                    digits[-4:],
                ),
            )
        event("tax ID kept")
        self.latest[payee] = (kind, digits)
        self.kept.add(digits)

    @precondition(lambda self: bool(self.latest))
    @rule(by=WHO, payee=PAYEE, purpose=st.sampled_from(("reveal", "1099 export")))
    def reveal(self, by, payee, purpose):
        if payee not in self.latest:
            return
        tax_id = self.latest_row(payee)[0]

        def work():
            with acting_as(self.app, self.staff[by]):
                self.app.execute(
                    "INSERT INTO trust_tax_id_reveals (pmc_id, tax_id, purpose)"
                    " VALUES (%s, %s, %s)",
                    (self.pmc, tax_id, purpose),
                )

        if by in self.admins:
            event("seen whole by an admin")
            work()
            self.reveals.append(self.staff[by])
        else:
            event("refused: not an admin")
            try:
                work()
            except psycopg.errors.InsufficientPrivilege as refused:
                assert "may not see a whole tax ID" in str(refused)
                return
            raise AssertionError("expected a refusal")

    def latest_row(self, payee):
        who, payee_id = self.payees[payee]
        found = self.conn.execute(
            "SELECT id, kind, tax_id_sealed, last4 FROM trust_tax_ids"
            f" WHERE pmc_id = %s AND {who}_id = %s"
            " ORDER BY created_at DESC, id DESC LIMIT 1",
            (self.pmc, payee_id),
        ).fetchone()
        assert found is not None
        return found

    @invariant()
    def reveals_are_by_admins_and_the_models(self):
        rows = self.conn.execute(
            "SELECT r.recorded_by, trust_staff_holds(r.pmc_id, r.recorded_by, 'admin')"
            " FROM trust_tax_id_reveals AS r WHERE r.pmc_id = %s ORDER BY r.created_at, r.id",
            (self.pmc,),
        ).fetchall()
        assert [by for by, _ in rows] == self.reveals
        # Nobody named on a reveal ever lost admin without the model knowing it.
        assert all(held == (self.staff.index(by) in self.admins) for by, held in rows)

    @invariant()
    def each_latest_tax_id_opens_to_what_was_kept(self):
        for payee, (kind, digits) in self.latest.items():
            row_id, held_kind, sealed_id, last4 = self.latest_row(payee)
            assert (held_kind, last4) == (kind, digits[-4:])
            assert sealed.unseal(bytes(sealed_id), str(row_id), self.key) == digits

    @invariant()
    def the_audit_log_never_holds_the_digits(self):
        # Every row the company's writes logged, before and after, in one pass.
        assert self.conn.execute(
            "SELECT count(*) FROM trust_audit_log AS a WHERE a.pmc_id = %s"
            " AND concat(a.row_data::text, a.before::text) LIKE ANY (%s)",
            (self.pmc, [f"%{digits}%" for digits in self.kept]),
        ).fetchone() == (0,)


def test_only_an_admin_sees_a_tax_id_whole_and_it_stays_sealed(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        run_state_machine_as_test(lambda: TaxIdMachine(conn, app_conn))
