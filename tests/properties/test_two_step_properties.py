"""Property-based tests for two-step sign-in for owners and tenants (migration 20261022000153;
src/pms/accounts.py).

Hypothesis takes one owner's or tenant's login through any order of: signing in (with the right
or a wrong password, and no code, the right code or a wrong one); turning two-step sign-in on in
the portal, confirming it with the right or a wrong code and turning it off with either; the
company requiring it or not; a reset the person asked for by email; an admin's new invite; and
confirming an invite that is waiting for its authenticator. The authenticator's clock moves a
step each time, so every right code is fresh. A model of the login says what each must answer:

  - a sign-in gives a session, asks for a code, asks for the authenticator to be set up (the same
    secret each time until a code confirms it), or is refused the same way for every reason;
  - a wrong password or a wrong code at sign-in or at turning off counts toward the lock, enough
    of them (three here, so it comes often) lock the login, and nothing gets past the lock;
  - a secret not yet confirmed changes nothing at sign-in unless the company requires it;
  - turning it off takes a current code and is refused while required or locked;
  - a reset the person asked for keeps the authenticator (and the lock with it) and, while it is
    on, signs nobody in; an admin's invite clears it all;
  - every session the model holds open is open, and no other.

Invariants checked after every step: the login's authenticator, failures and lock are the
model's, and whether its sign-in asks for a code and whether the company requires it are too.
Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import psycopg
import pyotp
from argon2 import PasswordHasher
from helpers import acting_as
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

from pms import accounts

CODES = st.sampled_from(["none", "right", "wrong"])
WRONG_PASSWORD = "not the password at all"


class Clock:
    """The authenticator's clock: a step (30 seconds) on for every rule."""

    def __init__(self) -> None:
        self.now = 1_900_000_000.0

    def time(self) -> float:
        return self.now


class TwoStepMachine(RuleBasedStateMachine):
    def __init__(self, conn, app_conn, clock):
        super().__init__()
        self.conn = conn
        self.app = app_conn
        self.clock = clock
        one = lambda q, p=(): conn.execute(q, p).fetchone()[0]  # noqa: E731
        self.pmc = one(
            "INSERT INTO trust_pmcs (display_name) VALUES ('Two-step PMC') RETURNING pmc_id"
        )
        self.staff = one(
            "INSERT INTO trust_staff (pmc_id, display_name) VALUES (%s, 'Manager 1') RETURNING id",
            (self.pmc,),
        )
        self.one = one
        # The model.
        self.passwords = 0
        self.password = ""
        self.secret: str | None = None
        self.confirmed = False
        self.failures = 0
        self.locked = False
        self.required = False
        self.settings = 0
        self.live: set[str] = set()
        self.ended: set[str] = set()
        self.waiting: str | None = None  # an invite waiting for its authenticator's first code

    @initialize(kind=st.sampled_from(["owner", "tenant"]))
    def join(self, kind):
        if kind == "owner":
            person = self.one(
                "INSERT INTO trust_owners (pmc_id, display_name) VALUES (%s, 'Owner 1')"
                " RETURNING id",
                (self.pmc,),
            )
        else:
            prop = self.one(
                "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, 'Property 1')"
                " RETURNING id",
                (self.pmc,),
            )
            person = self.one(
                "INSERT INTO trust_tenants (pmc_id, property_id, display_name)"
                " VALUES (%s, %s, 'Tenant 1') RETURNING id",
                (self.pmc, prop),
            )
        self.kind, self.person, self.email = kind, person, f"{kind}.{person.hex[:8]}@example.com"
        token = accounts.create_invite(self.app, self.pmc, kind, person, self.email, None).token
        self.password = self.new_password()
        signed = accounts.accept_invite(self.app, token, self.password)
        assert isinstance(signed, accounts.Signed)
        self.login = signed.session.login_id
        self.live.add(signed.token)

    # --- helpers ------------------------------------------------------------------------------

    def tick(self):
        self.clock.now += 30

    def new_password(self):
        self.passwords += 1
        return f"synthetic passphrase number {self.passwords}"

    def code(self, sent):
        """The code a person sends: none, the right one now, or one no step around now gives."""
        if sent == "none":
            return None
        totp = pyotp.TOTP(self.secret or pyotp.random_base32())
        if sent == "right":
            return totp.at(self.clock.now)
        valid = {totp.at(self.clock.now + 30 * n) for n in (-1, 0, 1)}
        return next(c for c in ("000000", "111111", "222222", "333333") if c not in valid)

    def fail(self):
        self.failures += 1
        if self.failures >= accounts.MAX_FAILURES:
            self.failures, self.locked = 0, True

    def end_sessions(self):
        self.ended |= self.live
        self.live = set()

    def session(self):
        token = next(iter(sorted(self.live)))
        found = accounts.session_for(self.app, token)
        assert found is not None and found.login_id == self.login
        return found

    def setup_shown(self, answer):
        """An authenticator to set up: the one waiting, or a new one that waits from now."""
        assert isinstance(answer, accounts.Enrollment), answer
        if self.secret is not None:
            assert answer.secret == self.secret
        self.secret = answer.secret
        assert f"secret={answer.secret}" in answer.totp_uri

    @staticmethod
    def refused(error, work, words=None):
        try:
            work()
        except error as found:
            assert type(found) is error, found
            if words is not None:
                assert str(found) == words, found
            return
        raise AssertionError(f"expected {error.__name__}")

    # --- signing in ---------------------------------------------------------------------------

    @rule(right_password=st.booleans(), sent=CODES)
    def sign_in(self, right_password, sent):
        self.tick()
        password = self.password if right_password else WRONG_PASSWORD
        code = self.code(sent)

        def work():
            return accounts.sign_in(self.app, self.pmc, self.email, password, code)

        if self.locked or not right_password:
            event("sign-in refused: " + ("locked" if self.locked else "wrong password"))
            self.refused(accounts.SignInRefused, work, "the email or password is wrong")
            if not self.locked:
                self.fail()
        elif self.confirmed or (self.required and self.secret is not None and code is not None):
            if code is None:
                event("sign-in asks for a code")
                self.refused(accounts.CodeNeeded, work, accounts.ENTER_CODE)
            elif sent == "wrong":
                event("sign-in refused: wrong code")
                self.refused(accounts.CodeNeeded, work, accounts.WRONG_CODE)
                self.fail()
            else:
                event("signed in with a code" if self.confirmed else "set up at sign-in")
                self.signed(work())
                self.confirmed = True
        elif self.required:
            event("sign-in asks for the authenticator to be set up")
            self.setup_shown(work())
        else:
            event("signed in with the password alone")
            self.signed(work())

    def signed(self, answer):
        assert isinstance(answer, accounts.Signed), answer
        assert answer.session.login_id == self.login
        self.live.add(answer.token)
        self.failures = 0

    # --- the portal ---------------------------------------------------------------------------

    @precondition(lambda self: self.live)
    @rule()
    def start(self):
        self.tick()

        def work():
            return accounts.start_two_step(self.app, self.session())

        if self.confirmed:
            event("start refused: on already")
            self.refused(accounts.Conflict, work, "two-step sign-in is on already")
        else:
            event("started")
            self.setup_shown(work())

    @precondition(lambda self: self.live)
    @rule(right=st.booleans())
    def confirm(self, right):
        self.tick()
        code = self.code("right" if right else "wrong")

        def work():
            accounts.confirm_two_step(self.app, self.session(), code)

        if self.confirmed:
            event("confirm refused: on already")
            self.refused(accounts.Conflict, work, "two-step sign-in is on already")
        elif self.secret is None:
            event("confirm refused: not started")
            self.refused(accounts.Refused, work, "turn two-step sign-in on first")
        elif not right:
            event("confirm refused: wrong code")
            self.refused(accounts.Refused, work, accounts.WRONG_CODE)
        else:
            event("turned on")
            work()
            self.confirmed = True

    @precondition(lambda self: self.live)
    @rule(right=st.booleans())
    def turn_off(self, right):
        self.tick()
        code = self.code("right" if right else "wrong")

        def work():
            accounts.turn_off_two_step(self.app, self.session(), code)

        if not self.confirmed:
            event("turn off refused: off already")
            self.refused(accounts.Conflict, work, "two-step sign-in is off already")
        elif self.required:
            event("turn off refused: required")
            self.refused(
                accounts.Refused,
                work,
                "your property manager requires two-step sign-in, so it stays on",
            )
        elif self.locked:
            event("turn off refused: locked")
            self.refused(accounts.Refused, work, "too many wrong tries: try again in 15 minutes")
        elif not right:
            event("turn off refused: wrong code")
            self.refused(accounts.Refused, work, accounts.WRONG_CODE)
            self.fail()
        else:
            event("turned off")
            work()
            self.secret, self.confirmed = None, False

    # --- the company --------------------------------------------------------------------------

    @rule(required=st.booleans())
    def require(self, required):
        self.tick()
        with acting_as(self.app, self.staff):
            self.app.execute(
                "INSERT INTO trust_sign_in_settings (pmc_id, require_two_step) VALUES (%s, %s)",
                (self.pmc, required),
            )
        self.required = required
        self.settings += 1

    # --- recovery -----------------------------------------------------------------------------

    @rule()
    def reset_asked_for(self):
        """A reset the person asked for by email: an invite no staff member made."""
        self.tick()
        token = accounts.create_invite(self.app, self.pmc, self.kind, self.person, self.email, None)
        self.password = self.new_password()
        done = accounts.accept_invite(self.app, token.token, self.password)
        if self.confirmed:
            event("reset: two-step stays on")
            assert done == accounts.PasswordSet(self.pmc)
            self.end_sessions()
            return
        self.failures, self.locked = 0, False
        self.accepted(done, token.token)

    @rule()
    def admins_invite(self):
        self.tick()
        token = accounts.create_invite(
            self.app, self.pmc, self.kind, self.person, self.email, self.staff
        )
        self.password = self.new_password()
        done = accounts.accept_invite(self.app, token.token, self.password)
        self.secret, self.confirmed, self.failures, self.locked = None, False, 0, False
        self.accepted(done, token.token)

    def accepted(self, done, token):
        if self.required:
            event("invite: set up next")
            self.setup_shown(done)
            self.waiting = token
        else:
            event("invite: signed in")
            assert isinstance(done, accounts.Signed), done
            self.end_sessions()
            self.live.add(done.token)

    @precondition(lambda self: self.waiting is not None)
    @rule(right=st.booleans())
    def confirm_invite(self, right):
        self.tick()
        token = self.waiting
        code = self.code("right" if right else "wrong")

        def work():
            return accounts.confirm_invite(self.app, token, code)

        if self.secret is None or self.confirmed:
            event("invite confirm refused: nothing waiting")
            self.refused(accounts.Refused, work, "set a password from this invite first")
        elif not right:
            event("invite confirm refused: wrong code")
            self.refused(accounts.CodeNeeded, work, accounts.WRONG_CODE)
        else:
            event("invite confirmed")
            signed = work()
            assert isinstance(signed, accounts.Signed)
            self.confirmed, self.waiting = True, None
            self.end_sessions()
            self.live.add(signed.token)

    # --- what must hold -----------------------------------------------------------------------

    @invariant()
    def the_login_is_the_models(self):
        if not hasattr(self, "login"):
            return
        assert self.conn.execute(
            "SELECT totp_secret, totp_confirmed_at IS NOT NULL, failed_sign_ins,"
            " coalesce(locked_until > now(), false) FROM trust_login_secrets WHERE login_id = %s",
            (self.login,),
        ).fetchone() == (self.secret, self.confirmed, self.failures, self.locked)
        assert accounts.two_step_on(self.app, self.login) == self.confirmed
        assert accounts.two_step_required(self.app, self.pmc) == self.required

    @invariant()
    def sessions_are_the_models(self):
        for token in self.live:
            assert accounts.session_for(self.app, token) is not None
        for token in self.ended:
            assert accounts.session_for(self.app, token) is None

    @invariant()
    def settings_only_grow_and_name_who(self):
        assert self.conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE recorded_by = %s)"
            " FROM trust_sign_in_settings WHERE pmc_id = %s",
            (self.staff, self.pmc),
        ).fetchone() == (self.settings, self.settings)


def test_two_step_sign_in_follows_the_model(database_url, monkeypatch):
    # The hash's strength isn't what this checks; a cheap one keeps each example fast.
    monkeypatch.setattr(
        accounts, "HASHER", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    )
    # Three failures lock the login here, not ten, so the lock comes often enough to be tried.
    monkeypatch.setattr(accounts, "MAX_FAILURES", 3)
    clock = Clock()
    monkeypatch.setattr(accounts, "time", clock)
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        run_state_machine_as_test(lambda: TwoStepMachine(conn, app_conn, clock))
