"""Property-based tests for API keys (ADR 0011, migration 20261031000201; src/pms/api/ai_keys.py).

Hypothesis has two admins and a clerk of one company, and an admin of another, make keys, take
them back and use them, in random order, through the module's own functions as the app
(trust_app) naming each one; and uses keys it never made. A model of every key made says what
must hold after every step (ci/registry.toml, an-api-key-reads-and-never-moves-money):

  - a key opens a request only while it stands, and only for its own company; a key taken back,
    mangled or never made is refused alike;
  - an opened request reads as trust_ai_agent until its transaction ends, and every write it
    tries (a row, a function that moves money) is refused; the app is trust_app again after;
  - only an admin of the key's company makes or takes back a key (the database refuses a clerk;
    another company's key is not found), and a key is taken back once;
  - each company's list is exactly its keys, standing or taken back, and no list, row or audit
    row holds a secret.

And the rate limit, on a clock Hypothesis drives: a key makes at most PER_MINUTE requests in any
WINDOW seconds, a request is refused only when that many were let through in the WINDOW before
it, and the wait it is told ends exactly when the oldest of them leaves the window.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from types import SimpleNamespace

import psycopg
import pytest
from fastapi import HTTPException
from helpers import add_staff, give_roles, make_pmc
from hypothesis import event, given
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from pms.api import ai_keys

# Staff 0 and 1 are the home company's admins, 2 its clerk, 3 the other company's admin.
WHO = st.integers(min_value=0, max_value=3)
ADMINS = {0, 1, 3}
HOME, ELSEWHERE = 0, 1
# What a key's request tries and the AI's role must refuse: a change to a row it reads, a row of a
# table it may not touch, a function that moves money, and reading the keys themselves.
WRITES = (
    "UPDATE trust_pmcs SET display_name = display_name WHERE false",
    "INSERT INTO trust_api_key_revocations (key_id, pmc_id)"
    " VALUES (gen_random_uuid(), gen_random_uuid())",
    "SELECT trust_record_owner_contribution(NULL::uuid, NULL::text, NULL::text,"
    " NULL::numeric, NULL::timestamptz, NULL::text)",
    "SELECT count(*) FROM trust_api_keys",
)


def request_with(header):
    return SimpleNamespace(headers={} if header is None else {"authorization": header})


class KeyMachine(RuleBasedStateMachine):
    def __init__(self, conn, app_conn):
        super().__init__()
        self.conn = conn
        self.app = app_conn
        self.companies = [make_pmc(conn).pmc_id, make_pmc(conn).pmc_id]
        self.staff = []
        for n, company in enumerate([HOME, HOME, HOME, ELSEWHERE]):
            staff_id = add_staff(conn, self.companies[company], f"Staff {n}")
            if n in ADMINS:
                give_roles(conn, self.companies[company], staff_id, ["admin"])
            self.staff.append((company, staff_id))
        # Model: every key made, in order: (company, secret, id, taken back).
        self.made: list[list] = []
        self.secrets: list[str] = []

    def session(self, who):
        company, staff_id = self.staff[who]
        return SimpleNamespace(pmc_id=self.companies[company], person_id=staff_id)

    def naming(self, who):
        self.app.execute(
            "SELECT set_config('trust.staff_id', %s, true)", (str(self.staff[who][1]),)
        )

    @rule(by=WHO, name=st.text(alphabet="abcdefgh XYZ", min_size=1, max_size=20))
    def make(self, by, name):
        if not name.strip():
            name = "Key"
        try:
            with self.app.transaction():
                self.naming(by)
                made = ai_keys.make_key(self.session(by), self.app, ai_keys.NewKey(name=name))
        except psycopg.errors.InsufficientPrivilege as refused:
            assert by not in ADMINS
            assert "may not make an API key" in str(refused)
            event("make refused: not an admin")
            return
        assert by in ADMINS
        assert made.secret.startswith("occ_") and made.prefix == made.secret[:12]
        assert made.name == name.strip()
        event("key made")
        self.made.append([self.staff[by][0], made.secret, made.id, False])
        self.secrets.append(made.secret)

    @precondition(lambda self: bool(self.made))
    @rule(by=WHO, data=st.data())
    def take_back(self, by, data):
        key = data.draw(st.sampled_from(self.made))
        company, _, key_id, revoked = key
        expected = (
            404
            if company != self.staff[by][0]
            else 409
            if revoked
            else "refused"
            if by not in ADMINS
            else 204
        )
        try:
            with self.app.transaction():
                self.naming(by)
                ai_keys.revoke_key(self.session(by), self.app, key_id)
        except HTTPException as answer:
            event(f"take back answered {answer.status_code}")
            assert answer.status_code == expected
            return
        except psycopg.errors.InsufficientPrivilege as refused:
            event("take back refused: not an admin")
            assert expected == "refused"
            assert "may not take an API key back" in str(refused)
            return
        event("key taken back")
        assert expected == 204
        key[3] = True

    def use(self, header, writes=True):
        """The key the header carries as the request's dependency finds it, and what the request
        then is, inside its own transaction: the AI's role, refused every write it tries."""
        try:
            with self.app.transaction():
                key = ai_keys.with_key(request_with(header), self.app)
                assert self.app.execute("SELECT current_user").fetchone() == ("trust_ai_agent",)
                for write in WRITES if writes else ():
                    with (
                        pytest.raises(psycopg.errors.InsufficientPrivilege),
                        self.app.transaction(),
                    ):
                        self.app.execute(write)
        except HTTPException as answer:
            assert (answer.status_code, answer.detail) == (401, ai_keys.NOT_VALID)
            key = None
        assert self.app.execute("SELECT current_user").fetchone() == ("trust_app",)
        return key

    @precondition(lambda self: bool(self.made))
    @rule(data=st.data(), mangle=st.booleans())
    def use_a_made_key(self, data, mangle):
        company, secret, key_id, revoked = data.draw(st.sampled_from(self.made))
        if mangle:
            secret = secret[:-1] + ("A" if secret[-1] != "A" else "B")
        key = self.use(f"Bearer {secret}")
        if mangle or revoked:
            event("a mangled or taken back key refused")
            assert key is None
        else:
            event("a standing key opened a request")
            assert key is not None
            assert (key.id, key.pmc_id) == (key_id, self.companies[company])

    @rule(
        header=st.one_of(
            st.none(),
            st.text(max_size=60),
            st.from_regex(r"\A(Bearer|bearer|Basic) occ_[A-Za-z0-9_-]{0,110}\Z"),
        )
    )
    def use_a_key_never_made(self, header):
        assert self.use(header) is None

    @invariant()
    def each_key_opens_requests_only_while_it_stands(self):
        for company, secret, key_id, revoked in self.made:
            key = self.use(f"Bearer {secret}", writes=False)
            assert (key is not None) == (not revoked)
            if key is not None:
                assert (key.id, key.pmc_id) == (key_id, self.companies[company])

    @invariant()
    def each_company_lists_exactly_its_keys_and_no_secret(self):
        for company in (HOME, ELSEWHERE):
            admin = 0 if company == HOME else 3
            listed = ai_keys.keys(self.session(admin), self.app)
            mine = [k for k in self.made if k[0] == company]
            assert {(k.id, k.revoked_at is not None) for k in listed} == {
                (k[2], k[3]) for k in mine
            }
            text = "".join(k.model_dump_json() for k in listed)
            assert not [s for s in self.secrets if s in text or s[4:] in text]

    @invariant()
    def no_row_or_audit_row_holds_a_secret(self):
        if not self.secrets:
            return
        patterns = [f"%{s[4:]}%" for s in self.secrets]
        assert self.conn.execute(
            "SELECT count(*) FROM trust_api_keys AS k WHERE k.pmc_id = ANY (%s)"
            " AND row_to_json(k)::text LIKE ANY (%s)",
            (self.companies, patterns),
        ).fetchone() == (0,)
        assert self.conn.execute(
            "SELECT count(*) FROM trust_audit_log AS a WHERE a.pmc_id = ANY (%s)"
            " AND concat(a.row_data::text, a.before::text) LIKE ANY (%s)",
            (self.companies, patterns),
        ).fetchone() == (0,)


def test_a_key_reads_as_the_ai_only_while_it_stands_and_only_its_company(database_url):
    before = ai_keys.PER_MINUTE
    # The machine uses each key many times in an example; the limit has its own test below.
    ai_keys.PER_MINUTE = 1_000_000
    try:
        with (
            psycopg.connect(database_url, autocommit=True) as conn,
            psycopg.connect(database_url, autocommit=True) as app_conn,
        ):
            app_conn.execute("SET ROLE trust_app")
            run_state_machine_as_test(lambda: KeyMachine(conn, app_conn))
    finally:
        ai_keys.PER_MINUTE = before


@given(
    limit=st.integers(min_value=1, max_value=5),
    # Half seconds, so the clock's sums are exact.
    gaps=st.lists(st.integers(min_value=0, max_value=90).map(lambda n: n / 2), max_size=40),
)
def test_a_key_makes_at_most_its_limit_in_any_window(limit, gaps):
    before = ai_keys.PER_MINUTE
    ai_keys.PER_MINUTE = limit
    key_id = uuid.uuid4()
    try:
        now = 1_000.0
        allowed: list[float] = []
        for gap in gaps:
            now += gap
            in_window = [t for t in allowed if t > now - ai_keys.WINDOW]
            wait = ai_keys.too_soon(key_id, now)
            if len(in_window) < limit:
                assert wait is None
                allowed.append(now)
            else:
                assert wait is not None
                assert wait == pytest.approx(in_window[-limit] + ai_keys.WINDOW - now)
                assert 0 < wait <= ai_keys.WINDOW
        # In no window of WINDOW seconds were more than `limit` let through.
        for start in allowed:
            assert sum(start <= t < start + ai_keys.WINDOW for t in allowed) <= limit
    finally:
        ai_keys.PER_MINUTE = before
        ai_keys._recent.pop(key_id, None)
