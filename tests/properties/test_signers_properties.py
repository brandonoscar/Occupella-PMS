"""Property-based tests for who signs a trust bank account (migration 20261015000060).

Hypothesis names and replaces the account's broker, authorizes signers (licensed, or bonded to a
day around today) and takes them away, has staff who are not allowed try the same, and has each
staff member try to pay an owner out of the account, in random order. A model says who may: with
no broker on file, anyone holding disburse; with one, its broker and its signers, a bonded one
only while the bond is in force. After every step:
  - each payout went through exactly when the model allowed it, and the owner's account holds
    what the model says;
  - the signers report lists exactly the model's broker and signers, with each one's standing.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    admin_of,
    balance,
    draw_owner,
    make_pmc,
    sign_for,
    staff_named,
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

TODAY = datetime.now(UTC).date()
STAFF = ("Staff A", "Staff B", "Staff C")
BONDS = st.none() | st.integers(min_value=-3, max_value=40)  # None: licensed; else days to run


class SignersMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        pmc = make_pmc(conn)
        self.pmc, self.bank, self.owner = pmc.pmc_id, pmc.operating_bank_id, pmc.owners[0].account
        transfer_batch(conn, [(pmc.operating_cash, self.owner, "10000.00")], datetime.now(UTC))
        self.admin = admin_of(conn, self.pmc)
        self.staff = {name: staff_named(conn, self.pmc, name, ["disburse"]) for name in STAFF}
        # Model
        self.broker: str | None = None
        self.signers: dict[str, int | None] = {}  # name: None (licensed) or days the bond runs
        self.held = Decimal("10000.00")
        self.draws = 0

    def may_pay(self, name):
        if self.broker is None or name == self.broker:
            return True
        if name not in self.signers:
            return False
        days = self.signers[name]
        return days is None or days >= 0

    @rule(name=st.sampled_from(STAFF))
    def admin_names_a_broker(self, name):
        with self.conn.transaction():
            if self.broker is not None and self.broker != name:
                sign_for(
                    self.conn,
                    self.pmc,
                    self.bank,
                    self.staff[self.broker],
                    "broker",
                    granted=False,
                    on=TODAY,
                    by=self.admin,
                )
            sign_for(
                self.conn,
                self.pmc,
                self.bank,
                self.staff[name],
                "broker",
                license_number=f"LIC-{name[-1]}",
                on=TODAY,
                by=self.admin,
            )
        event("broker named")
        self.broker = name
        self.signers.pop(name, None)

    @precondition(lambda self: self.broker is not None)
    @rule(name=st.sampled_from(STAFF), bond=BONDS)
    def broker_authorizes_a_signer(self, name, bond):
        expires = None if bond is None else ("1000.00", TODAY + timedelta(days=bond))
        if name == self.broker:
            with pytest.raises(psycopg.errors.CheckViolation, match="the broker signs"):
                sign_for(
                    self.conn,
                    self.pmc,
                    self.bank,
                    self.staff[name],
                    bond=expires,
                    on=TODAY,
                    by=self.staff[self.broker],
                )
            return
        sign_for(
            self.conn,
            self.pmc,
            self.bank,
            self.staff[name],
            bond=expires,
            on=TODAY,
            by=self.staff[self.broker],
        )
        event("signer authorized, bonded" if bond is not None else "signer authorized, licensed")
        self.signers[name] = bond

    @precondition(lambda self: self.signers)
    @rule(data=st.data())
    def broker_takes_a_signer_away(self, data):
        name = data.draw(st.sampled_from(sorted(self.signers)))
        sign_for(
            self.conn,
            self.pmc,
            self.bank,
            self.staff[name],
            granted=False,
            on=TODAY,
            by=self.staff[self.broker],
        )
        event("signer taken away")
        del self.signers[name]

    @precondition(lambda self: self.broker is not None)
    @rule(name=st.sampled_from(STAFF), naming_broker=st.booleans())
    def someone_not_allowed_tries(self, name, naming_broker):
        """A staff member who isn't the broker adds a signer, or one who isn't an admin names
        a broker: refused, and nothing changes."""
        if naming_broker:
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="admin role"):
                sign_for(
                    self.conn,
                    self.pmc,
                    self.bank,
                    self.staff[name],
                    "broker",
                    on=TODAY,
                    by=self.staff[name],
                )
        elif name != self.broker:
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="broker authorizes"):
                sign_for(
                    self.conn, self.pmc, self.bank, self.staff[name], on=TODAY, by=self.staff[name]
                )
        event("refused: not allowed to change who signs")

    @precondition(lambda self: self.broker is not None)
    @rule()
    def broker_taken_away(self):
        if self.signers:
            with (
                pytest.raises(psycopg.errors.CheckViolation, match="need its broker"),
                self.conn.transaction(),
            ):
                sign_for(
                    self.conn,
                    self.pmc,
                    self.bank,
                    self.staff[self.broker],
                    "broker",
                    granted=False,
                    on=TODAY,
                    by=self.admin,
                )
            event("broker kept: signers need one")
            return
        sign_for(
            self.conn,
            self.pmc,
            self.bank,
            self.staff[self.broker],
            "broker",
            granted=False,
            on=TODAY,
            by=self.admin,
        )
        event("broker taken away")
        self.broker = None

    @rule(name=st.sampled_from(STAFF))
    def pay_an_owner(self, name):
        self.draws += 1
        key = f"draw-{self.draws}"
        if self.may_pay(name):
            with acting_as(self.conn, self.staff[name]):
                draw_owner(self.conn, self.pmc, self.owner, key, "1.00", datetime.now(UTC))
            event("paid out")
            self.held -= Decimal("1.00")
        else:
            with (
                pytest.raises(psycopg.errors.InsufficientPrivilege),
                acting_as(self.conn, self.staff[name]),
            ):
                draw_owner(self.conn, self.pmc, self.owner, key, "1.00", datetime.now(UTC))
            event("refused: not a signer, or the bond ran out")

    @invariant()
    def the_books_and_the_report_are_the_model(self):
        assert balance(self.conn, self.owner) == self.held
        listed = self.conn.execute(
            "SELECT signer, role, standing FROM trust_report_trust_account_signers(%s, %s)"
            " WHERE bank_account_id = %s ORDER BY line",
            (self.pmc, TODAY, self.bank),
        ).fetchall()
        expected = []
        if self.broker is None:
            expected.append((None, None, "no broker on file"))
        else:
            expected.append((self.broker, "broker", "licensed"))
        for name in sorted(self.signers):
            days = self.signers[name]
            standing = (
                "licensed"
                if days is None
                else "bond ran out"
                if days < 0
                else f"bond runs out in {days} day{'' if days == 1 else 's'}"
                if days <= 30
                else "bonded"
            )
            expected.append((name, "signer", standing))
        assert [tuple(row) for row in listed] == expected


def test_only_the_broker_and_its_signers_pay_out_once_a_broker_is_on_file(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: SignersMachine(conn))
