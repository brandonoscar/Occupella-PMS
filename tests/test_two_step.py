"""Two-step sign-in for owners and tenants (migration 20261022000153, src/pms/accounts.py): turned
on per login, required by a company, kept through a reset the person asked for and cleared by an
admin's invite. The API's routes are tested in tests/api/test_two_step_api.py."""

import psycopg
import pyotp
import pytest
from helpers import acting_as, add_staff, make_pmc

from pms import accounts

PASSWORD = "a long enough password"


class Clock:
    """The authenticator's clock, moved by hand a step (30 seconds) at a time, so each code a
    test uses is fresh whatever the time is."""

    def __init__(self) -> None:
        self.now = 1_900_000_000.0

    def time(self) -> float:
        return self.now

    def tick(self) -> None:
        self.now += 30


@pytest.fixture
def clock(monkeypatch):
    found = Clock()
    monkeypatch.setattr(accounts, "time", found)
    return found


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def staff(conn, pmc):
    return add_staff(conn, pmc.pmc_id, "Manager 1")


EMAIL = "owner.two.step@example.com"


def join(app_conn, pmc, email=EMAIL):
    """Owner 1 accepts an invite no staff member made (as the seed's and a reset's are)."""
    token = accounts.create_invite(
        app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, email, None
    ).token
    signed = accounts.accept_invite(app_conn, token, PASSWORD)
    assert isinstance(signed, accounts.Signed)
    return signed.session


def code(secret, clock, steps=0):
    return pyotp.TOTP(secret).at(clock.now + 30 * steps)


def turn_on(app_conn, session, clock):
    secret = accounts.start_two_step(app_conn, session).secret
    accounts.confirm_two_step(app_conn, session, code(secret, clock))
    clock.tick()
    return secret


def sign_in(app_conn, pmc, password=PASSWORD, with_code=None, email=EMAIL):
    return accounts.sign_in(app_conn, pmc.pmc_id, email, password, with_code)


def secrets_row(conn, session):
    return conn.execute(
        "SELECT totp_secret, totp_confirmed_at IS NOT NULL, failed_sign_ins,"
        " coalesce(locked_until > now(), false) FROM trust_login_secrets WHERE login_id = %s",
        (session.login_id,),
    ).fetchone()


def require(conn, pmc, staff, required=True):
    with acting_as(conn, staff):
        conn.execute(
            "INSERT INTO trust_sign_in_settings (pmc_id, require_two_step) VALUES (%s, %s)",
            (pmc.pmc_id, required),
        )


# --- turned on by the person ----------------------------------------------------------------


def test_with_two_step_on_a_sign_in_needs_a_code(conn, app_conn, pmc, clock):
    session = join(app_conn, pmc)
    secret = turn_on(app_conn, session, clock)

    with pytest.raises(accounts.CodeNeeded, match="^enter the code from your authenticator app$"):
        sign_in(app_conn, pmc)
    signed = sign_in(app_conn, pmc, with_code=code(secret, clock))

    assert isinstance(signed, accounts.Signed) and signed.session.login_id == session.login_id
    assert accounts.two_step_on(app_conn, session.login_id)


def test_a_wrong_or_used_code_is_refused_and_counts_toward_the_lock(conn, app_conn, pmc, clock):
    session = join(app_conn, pmc)
    secret = turn_on(app_conn, session, clock)
    used = code(secret, clock)
    sign_in(app_conn, pmc, with_code=used)

    for wrong in (used, "000000" if used != "000000" else "111111"):
        with pytest.raises(accounts.CodeNeeded, match="wrong or was already used"):
            sign_in(app_conn, pmc, with_code=wrong)
    assert secrets_row(conn, session)[2:] == (2, False)
    for _ in range(8):
        with pytest.raises(accounts.CodeNeeded):
            sign_in(app_conn, pmc, with_code="000000" if used != "000000" else "111111")
    # Ten failures lock the login: even the right code with the right password is refused.
    clock.tick()
    with pytest.raises(accounts.SignInRefused):
        sign_in(app_conn, pmc, with_code=code(secret, clock))
    assert secrets_row(conn, session)[2:] == (0, True)


def test_a_secret_not_yet_confirmed_changes_nothing_and_shows_the_same_each_time(
    conn, app_conn, pmc, clock
):
    session = join(app_conn, pmc)

    first = accounts.start_two_step(app_conn, session)
    again = accounts.start_two_step(app_conn, session)

    assert again == first and first.totp_uri.startswith("otpauth://totp/Occupella%20PMS:")
    assert f"secret={first.secret}" in first.totp_uri
    assert isinstance(sign_in(app_conn, pmc), accounts.Signed)
    assert not accounts.two_step_on(app_conn, session.login_id)


def test_the_first_code_confirms_it_once_and_a_wrong_one_does_not(conn, app_conn, pmc, clock):
    session = join(app_conn, pmc)
    with pytest.raises(accounts.Refused, match="^turn two-step sign-in on first$"):
        accounts.confirm_two_step(app_conn, session, "123456")
    secret = accounts.start_two_step(app_conn, session).secret

    with pytest.raises(accounts.Refused, match="wrong or was already used"):
        accounts.confirm_two_step(app_conn, session, code(secret, clock, steps=5))
    accounts.confirm_two_step(app_conn, session, code(secret, clock))

    # Confirming isn't a sign-in: no failure counted for the wrong code.
    assert secrets_row(conn, session)[1:] == (True, 0, False)
    with pytest.raises(accounts.Conflict, match="on already"):
        accounts.confirm_two_step(app_conn, session, code(secret, clock, steps=1))
    with pytest.raises(accounts.Conflict, match="on already"):
        accounts.start_two_step(app_conn, session)


def test_turning_it_off_takes_a_current_code_and_a_wrong_one_counts(conn, app_conn, pmc, clock):
    session = join(app_conn, pmc)
    secret = turn_on(app_conn, session, clock)

    with pytest.raises(accounts.Refused, match="wrong or was already used"):
        accounts.turn_off_two_step(app_conn, session, code(secret, clock, steps=-3))
    assert secrets_row(conn, session)[1:] == (True, 1, False)
    accounts.turn_off_two_step(app_conn, session, code(secret, clock))

    assert secrets_row(conn, session)[:2] == (None, False)
    assert isinstance(sign_in(app_conn, pmc), accounts.Signed)
    with pytest.raises(accounts.Conflict, match="off already"):
        accounts.turn_off_two_step(app_conn, session, code(secret, clock))


def test_turning_it_off_is_refused_while_locked(conn, app_conn, pmc, clock):
    session = join(app_conn, pmc)
    secret = turn_on(app_conn, session, clock)
    conn.execute(
        "UPDATE trust_login_secrets SET locked_until = now() + interval '1 minute'"
        " WHERE login_id = %s",
        (session.login_id,),
    )

    with pytest.raises(accounts.Refused, match="too many wrong tries: try again in 15 minutes"):
        accounts.turn_off_two_step(app_conn, session, code(secret, clock))
    assert secrets_row(conn, session)[1] is True


def test_staff_keep_their_own_authenticator(conn, app_conn, pmc, staff):
    session = accounts.Session(staff, pmc.pmc_id, "staff", staff, "staff@example.com")

    for change in (
        lambda: accounts.start_two_step(app_conn, session),
        lambda: accounts.confirm_two_step(app_conn, session, "123456"),
        lambda: accounts.turn_off_two_step(app_conn, session, "123456"),
    ):
        with pytest.raises(accounts.Refused, match="^staff sign in with the authenticator"):
            change()


# --- required by the company ----------------------------------------------------------------


def test_required_an_owner_without_it_sets_it_up_when_they_sign_in(
    conn, app_conn, pmc, staff, clock
):
    session = join(app_conn, pmc)
    require(conn, pmc, staff)

    first = sign_in(app_conn, pmc)
    again = sign_in(app_conn, pmc)
    assert isinstance(first, accounts.Enrollment) and again == first
    # The password comes first: a wrong one gets the same answer as ever, and no secret.
    with pytest.raises(accounts.SignInRefused):
        sign_in(app_conn, pmc, password="not the password")
    with pytest.raises(accounts.CodeNeeded, match="wrong or was already used"):
        sign_in(app_conn, pmc, with_code=code(first.secret, clock, steps=7))
    assert secrets_row(conn, session)[1:3] == (False, 2)

    signed = sign_in(app_conn, pmc, with_code=code(first.secret, clock))

    assert isinstance(signed, accounts.Signed)
    assert secrets_row(conn, session)[:3] == (first.secret, True, 0)
    clock.tick()
    with pytest.raises(accounts.CodeNeeded, match="^enter the code"):
        sign_in(app_conn, pmc)


def test_required_a_secret_started_in_the_portal_is_the_one_set_up(
    conn, app_conn, pmc, staff, clock
):
    session = join(app_conn, pmc)
    started = accounts.start_two_step(app_conn, session)
    require(conn, pmc, staff)

    assert sign_in(app_conn, pmc) == started


def test_required_an_owner_with_it_signs_in_with_their_code_and_cant_turn_it_off(
    conn, app_conn, pmc, staff, clock
):
    session = join(app_conn, pmc)
    secret = turn_on(app_conn, session, clock)
    require(conn, pmc, staff)

    with pytest.raises(accounts.Refused, match="^your property manager requires two-step"):
        accounts.turn_off_two_step(app_conn, session, code(secret, clock))
    assert secrets_row(conn, session)[1:3] == (True, 0)
    assert isinstance(sign_in(app_conn, pmc, with_code=code(secret, clock)), accounts.Signed)
    require(conn, pmc, staff, required=False)
    clock.tick()
    accounts.turn_off_two_step(app_conn, session, code(secret, clock))
    assert not accounts.two_step_on(app_conn, session.login_id)


def test_requiring_it_ends_no_session(conn, app_conn, pmc, staff):
    session = join(app_conn, pmc)
    token = sign_in(app_conn, pmc).token

    require(conn, pmc, staff)

    assert accounts.session_for(app_conn, token) == session


def test_staff_sign_in_as_before_whatever_the_setting(conn, app_conn, pmc, staff, clock):
    require(conn, pmc, staff)
    token = accounts.create_invite(
        app_conn, pmc.pmc_id, "staff", staff, "staff@example.com", None
    ).token
    enrolled = accounts.accept_invite(app_conn, token, PASSWORD)
    assert isinstance(enrolled, accounts.Enrollment)
    accounts.confirm_invite(app_conn, token, code(enrolled.secret, clock))
    clock.tick()

    with pytest.raises(accounts.CodeNeeded, match="^enter the code"):
        sign_in(app_conn, pmc, email="staff@example.com")
    signed = sign_in(
        app_conn, pmc, email="staff@example.com", with_code=code(enrolled.secret, clock)
    )
    assert isinstance(signed, accounts.Signed)


# --- recovery: an admin's invite clears it, a reset the person asked for keeps it -----------


def test_a_reset_the_person_asked_for_keeps_the_authenticator(conn, app_conn, pmc, clock):
    session = join(app_conn, pmc)
    secret = turn_on(app_conn, session, clock)
    open_session = sign_in(app_conn, pmc, with_code=code(secret, clock)).token
    clock.tick()
    with pytest.raises(accounts.CodeNeeded):
        sign_in(app_conn, pmc, with_code="000000" if code(secret, clock) != "000000" else "1" * 6)
    reset = accounts.request_reset(app_conn, pmc.pmc_id, EMAIL)
    assert reset is not None

    done = accounts.accept_invite(app_conn, reset.invite.token, "a brand new passphrase")

    # The new password is set and every session ends, but no session comes of the emailed link:
    # the person signs in with the new password and a code, the failure still counted.
    assert done == accounts.PasswordSet(pmc.pmc_id)
    assert accounts.session_for(app_conn, open_session) is None
    assert secrets_row(conn, session)[:3] == (secret, True, 1)
    with pytest.raises(accounts.NotFound):
        accounts.view_invite(app_conn, reset.invite.token)
    with pytest.raises(accounts.SignInRefused):
        sign_in(app_conn, pmc)
    with pytest.raises(accounts.CodeNeeded, match="^enter the code"):
        sign_in(app_conn, pmc, password="a brand new passphrase")
    signed = sign_in(
        app_conn, pmc, password="a brand new passphrase", with_code=code(secret, clock)
    )
    assert isinstance(signed, accounts.Signed)


def test_a_reset_keeps_a_lock_while_two_step_is_on(conn, app_conn, pmc, clock):
    session = join(app_conn, pmc)
    turn_on(app_conn, session, clock)
    conn.execute(
        "UPDATE trust_login_secrets SET locked_until = now() + interval '15 minutes'"
        " WHERE login_id = %s",
        (session.login_id,),
    )
    reset = accounts.request_reset(app_conn, pmc.pmc_id, EMAIL)
    assert reset is not None

    accounts.accept_invite(app_conn, reset.invite.token, "a brand new passphrase")

    assert secrets_row(conn, session)[1:] == (True, 0, True)


def test_without_two_step_a_reset_signs_in_and_clears_a_lock_as_before(conn, app_conn, pmc):
    session = join(app_conn, pmc)
    accounts.start_two_step(app_conn, session)  # waiting for its first code: not on
    conn.execute(
        "UPDATE trust_login_secrets SET failed_sign_ins = 3, locked_until = now() + interval"
        " '15 minutes' WHERE login_id = %s",
        (session.login_id,),
    )
    reset = accounts.request_reset(app_conn, pmc.pmc_id, EMAIL)
    assert reset is not None

    done = accounts.accept_invite(app_conn, reset.invite.token, "a brand new passphrase")

    assert isinstance(done, accounts.Signed)
    assert secrets_row(conn, session)[1:] == (False, 0, False)


def test_an_admins_invite_clears_it_so_a_lost_phone_gets_back_in(conn, app_conn, pmc, staff, clock):
    session = join(app_conn, pmc)
    turn_on(app_conn, session, clock)
    conn.execute(
        "UPDATE trust_login_secrets SET locked_until = now() + interval '15 minutes'"
        " WHERE login_id = %s",
        (session.login_id,),
    )
    token = accounts.create_invite(
        app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, EMAIL, staff
    ).token

    done = accounts.accept_invite(app_conn, token, "a brand new passphrase")

    assert isinstance(done, accounts.Signed)
    assert secrets_row(conn, session) == (None, False, 0, False)
    assert isinstance(sign_in(app_conn, pmc, password="a brand new passphrase"), accounts.Signed)


def test_required_an_admins_invite_sets_it_up_again_before_signing_in(
    conn, app_conn, pmc, staff, clock
):
    session = join(app_conn, pmc)
    old = turn_on(app_conn, session, clock)
    require(conn, pmc, staff)
    token = accounts.create_invite(
        app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, EMAIL, staff
    ).token

    enrolled = accounts.accept_invite(app_conn, token, "a brand new passphrase")

    assert isinstance(enrolled, accounts.Enrollment) and enrolled.secret != old
    with pytest.raises(accounts.CodeNeeded, match="wrong or was already used"):
        accounts.confirm_invite(app_conn, token, code(enrolled.secret, clock, steps=4))
    signed = accounts.confirm_invite(app_conn, token, code(enrolled.secret, clock))
    assert isinstance(signed, accounts.Signed)
    assert secrets_row(conn, session)[:2] == (enrolled.secret, True)
    with pytest.raises(accounts.NotFound):
        accounts.confirm_invite(app_conn, token, code(enrolled.secret, clock, steps=1))


def test_an_owners_invite_confirms_only_a_secret_waiting_for_its_first_code(
    conn, app_conn, pmc, staff, clock
):
    session = join(app_conn, pmc)
    secret = turn_on(app_conn, session, clock)
    token = accounts.create_invite(
        app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, EMAIL, staff
    ).token

    # An authenticator already on is never a way past the password.
    with pytest.raises(accounts.Refused, match="^set a password from this invite first$"):
        accounts.confirm_invite(app_conn, token, code(secret, clock))
    assert accounts.view_invite(app_conn, token).email == EMAIL


# --- the setting ----------------------------------------------------------------------------


def test_the_setting_names_who_changed_it_and_the_latest_holds(conn, app_conn, pmc, staff):
    assert not accounts.two_step_required(app_conn, pmc.pmc_id)
    require(app_conn, pmc, staff)
    other = make_pmc(conn)

    assert accounts.two_step_required(app_conn, pmc.pmc_id)
    assert not accounts.two_step_required(app_conn, other.pmc_id)
    require(app_conn, pmc, staff, required=False)
    assert not accounts.two_step_required(app_conn, pmc.pmc_id)
    rows = conn.execute(
        "SELECT require_two_step, recorded_by FROM trust_sign_in_settings WHERE pmc_id = %s"
        " ORDER BY created_at",
        (pmc.pmc_id,),
    ).fetchall()
    assert rows == [(True, staff), (False, staff)]


def test_the_setting_is_append_only_even_for_the_owner_role(conn, pmc, staff):
    require(conn, pmc, staff)

    for query in (
        "UPDATE trust_sign_in_settings SET require_two_step = false",
        "DELETE FROM trust_sign_in_settings",
        "TRUNCATE trust_sign_in_settings",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(query)


def test_the_setting_is_audited_naming_who_and_no_secret_is(conn, app_conn, pmc, staff, clock):
    session = join(app_conn, pmc)
    require(app_conn, pmc, staff)
    secret = sign_in(app_conn, pmc).secret

    audited = conn.execute(
        "SELECT staff_id, db_role FROM trust_audit_log WHERE pmc_id = %s"
        " AND table_name = 'trust_sign_in_settings'",
        (pmc.pmc_id,),
    ).fetchall()
    assert audited == [(staff, "trust_app")]
    assert accounts.two_step_on(app_conn, session.login_id) is False
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE row_data::text LIKE %s", (f"%{secret}%",)
    ).fetchone() == (0,)


def test_a_setting_names_a_staff_member_of_its_own_pmc(conn, pmc):
    stranger = add_staff(conn, make_pmc(conn).pmc_id, "Staff Elsewhere")

    with pytest.raises(psycopg.Error):
        require(conn, pmc, stranger)


def test_the_ai_role_reads_neither_the_setting_nor_its_audit_rows(conn, ai_conn, pmc, staff):
    require(conn, pmc, staff)

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ai_conn.execute("SELECT 1 FROM trust_sign_in_settings")
    assert ai_conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_sign_in_settings'"
    ).fetchone() == (0,)


def test_the_app_never_deletes_or_rewrites_a_setting(app_conn):
    for change in (
        "DELETE FROM trust_sign_in_settings",
        "UPDATE trust_sign_in_settings SET id = id",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app_conn.execute(change)
