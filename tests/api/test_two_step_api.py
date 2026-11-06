"""Two-step sign-in for owners and tenants (the founder's scope of 2026-10-22): theirs to turn on
from their Profile, with an authenticator app, as staff sign in with one from their invite.

Once on, signing in asks for its code, and turning it off takes one. A password reset they ask for
keeps it and asks for its code before the password changes, so someone holding their email alone
can't get past it; any other invite (staff's, the operator's) turns it off, which is how a manager
vouches for someone who lost their phone."""

import time

import pyotp
import pytest
from people import PASSWORD, accept, invite_token, join_as_owner, join_as_staff, join_as_tenant

from pms import accounts

WRONG = "that code is wrong or was already used: try the next one"
ASK = "enter the code from your authenticator app"


@pytest.fixture(params=["owner", "tenant"])
def person(request, client, conn, app_conn, pmc):
    if request.param == "owner":
        return join_as_owner(client, app_conn, pmc)
    return join_as_tenant(client, conn, app_conn, pmc)


def sign_in(client, pmc, person, code=None):
    body = {"pmc_id": str(pmc.pmc_id), "email": person.email, "password": PASSWORD}
    if code is not None:
        body["code"] = code
    return client.post("/api/sign-in", json=body)


def turn_on(client) -> pyotp.TOTP:
    started = client.post("/api/me/two-step/start", json={})
    assert started.status_code == 200, started.text
    totp = pyotp.TOTP(pyotp.parse_uri(started.json()["uri"]).secret)
    confirmed = client.post("/api/me/two-step/confirm", json={"code": totp.now()})
    assert confirmed.status_code == 200, confirmed.text
    return totp


def next_code(totp: pyotp.TOTP) -> str:
    """The next step's code: the one now was used turning it on, and a code works once."""
    return totp.at(int(time.time()) + totp.interval)


def detail(answer) -> tuple[int, str]:
    return answer.status_code, answer.json()["detail"]


def test_an_owner_or_tenant_turns_two_step_on_and_then_signs_in_with_its_code(
    client, other_client, pmc, person
):
    assert client.get("/api/me/two-step").json() == {"on": False}
    started = client.post("/api/me/two-step/start", json={})
    uri = started.json()["uri"]
    assert uri.startswith("otpauth://totp/") and person.email.replace("@", "%40") in uri
    assert client.get("/api/me/two-step").json() == {"on": False}  # not until a code proves it
    assert sign_in(other_client, pmc, person).status_code == 200  # nor does sign-in ask yet
    assert detail(client.post("/api/me/two-step/confirm", json={"code": "000000"})) == (422, WRONG)

    totp = pyotp.TOTP(pyotp.parse_uri(uri).secret)
    confirmed = client.post("/api/me/two-step/confirm", json={"code": totp.now()})
    assert confirmed.json() == {"on": True}
    assert client.get("/api/me/two-step").json() == {"on": True}
    # The session opened without a code ended; the one that turned it on stays.
    assert other_client.get("/api/me").status_code == 401

    assert detail(sign_in(other_client, pmc, person)) == (401, ASK)
    assert detail(sign_in(other_client, pmc, person, "000000")) == (401, WRONG)
    assert sign_in(other_client, pmc, person, totp.now()).status_code == 401  # used already
    assert sign_in(other_client, pmc, person, next_code(totp)).status_code == 200


def test_turning_two_step_off_takes_a_code(client, other_client, pmc, person):
    totp = turn_on(client)
    assert detail(client.post("/api/me/two-step/off", json={"code": "000000"})) == (422, WRONG)
    assert client.get("/api/me/two-step").json() == {"on": True}

    assert client.post("/api/me/two-step/off", json={"code": next_code(totp)}).json() == {
        "on": False
    }
    assert sign_in(other_client, pmc, person).status_code == 200  # no code asked
    assert detail(client.post("/api/me/two-step/off", json={"code": "123456"})) == (
        409,
        "two-step sign-in is off already",
    )


def test_wrong_codes_turning_it_off_lock_the_login(client, person):
    turn_on(client)
    for _ in range(accounts.MAX_FAILURES):
        assert client.post("/api/me/two-step/off", json={"code": "000000"}).status_code == 422
    locked = client.post("/api/me/two-step/off", json={"code": "000000"})
    assert detail(locked) == (
        422,
        f"too many wrong codes: try again in {accounts.LOCK_MINUTES} minutes",
    )
    assert client.get("/api/me/two-step").json() == {"on": True}


def test_a_reset_they_ask_for_keeps_two_step_and_asks_its_code_first(
    client, other_client, app_conn, pmc, person
):
    totp = turn_on(client)
    reset = accounts.request_reset(app_conn, pmc.pmc_id, person.email)
    assert reset is not None
    viewed = other_client.post("/api/invites/view", json={"token": reset.invite.token})
    assert viewed.json()["two_step"] is True

    new_password = "a new password, long enough"
    body = accept(reset.invite.token, new_password)
    assert detail(other_client.post("/api/invites/accept", json=body)) == (401, ASK)
    wrong = other_client.post("/api/invites/accept", json={**body, "code": "000000"})
    assert detail(wrong) == (401, WRONG)
    # Nothing changed yet: the old password still signs in, with a code.
    assert sign_in(client, pmc, person, next_code(totp)).status_code == 200


def test_a_reset_with_the_code_sets_the_password_and_keeps_two_step(
    client, other_client, app_conn, pmc, person
):
    totp = turn_on(client)
    reset = accounts.request_reset(app_conn, pmc.pmc_id, person.email)
    assert reset is not None
    new_password = "a new password, long enough"
    body = {**accept(reset.invite.token, new_password), "code": next_code(totp)}
    accepted = other_client.post("/api/invites/accept", json=body)
    assert accepted.status_code == 200, accepted.text
    assert other_client.get("/api/me/two-step").json() == {"on": True}
    # The new password, and still a code: their email alone never gets past it.
    again = {"pmc_id": str(pmc.pmc_id), "email": person.email, "password": new_password}
    assert detail(client.post("/api/sign-in", json=again)) == (401, ASK)


def test_an_invite_from_staff_or_the_operator_turns_two_step_off(
    client, other_client, conn, app_conn, pmc, person
):
    turn_on(client)
    staff = join_as_staff(other_client, conn, app_conn, pmc)
    token = invite_token(
        app_conn, pmc.pmc_id, person.kind, person.person_id, person.email, staff.person_id
    )
    assert other_client.post("/api/invites/view", json={"token": token}).json()["two_step"] is False
    assert other_client.post("/api/invites/accept", json=accept(token)).status_code == 200
    # Staff vouched for them: two-step is off until they turn it on again.
    assert sign_in(client, pmc, person).status_code == 200
    assert client.get("/api/me/two-step").json() == {"on": False}

    turn_on(client)
    # The server's operator (python -m pms.admin invite) names no staff member: off too.
    token = invite_token(app_conn, pmc.pmc_id, person.kind, person.person_id, person.email)
    assert other_client.post("/api/invites/accept", json=accept(token)).status_code == 200
    # Accepting it signed them in there and ended the session here, as a new password does.
    assert other_client.get("/api/me/two-step").json() == {"on": False}
    assert client.get("/api/me").status_code == 401


def test_two_step_is_on_once_and_staff_keep_theirs_from_the_invite(
    client, other_client, conn, app_conn, pmc, person
):
    turn_on(client)
    assert detail(client.post("/api/me/two-step/start", json={})) == (
        409,
        "two-step sign-in is on already: turn it off first to set up another authenticator",
    )
    assert detail(client.post("/api/me/two-step/confirm", json={"code": "123456"})) == (
        409,
        "two-step sign-in is on already",
    )

    join_as_staff(other_client, conn, app_conn, pmc)
    for path in ("", "/start", "/confirm", "/off"):
        if path == "":
            answer = other_client.get("/api/me/two-step")
        else:
            body = {} if path == "/start" else {"code": "123456"}
            answer = other_client.post(f"/api/me/two-step{path}", json=body)
        assert detail(answer) == (403, "only an owner or a tenant may do this"), path


def test_starting_again_before_confirming_replaces_the_setup(client, person):
    first = pyotp.parse_uri(client.post("/api/me/two-step/start", json={}).json()["uri"])
    second = pyotp.parse_uri(client.post("/api/me/two-step/start", json={}).json()["uri"])
    assert first.secret != second.secret
    stale = client.post("/api/me/two-step/confirm", json={"code": first.now()})
    if first.now() != second.now():
        assert detail(stale) == (422, WRONG)
    assert client.post("/api/me/two-step/confirm", json={"code": second.now()}).status_code == 200


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", None])
def test_a_code_is_six_digits(client, person, code):
    client.post("/api/me/two-step/start", json={})
    body = {} if code is None else {"code": code}
    assert client.post("/api/me/two-step/confirm", json=body).status_code == 422
    assert client.get("/api/me/two-step").json() == {"on": False}


def test_confirming_without_starting_is_refused(client, person):
    assert detail(client.post("/api/me/two-step/confirm", json={"code": "123456"})) == (
        409,
        "set up two-step sign-in first: start it from your profile",
    )


def test_signed_out_reaches_none_of_it(other_client):
    assert other_client.get("/api/me/two-step").status_code == 401
    assert other_client.post("/api/me/two-step/start", json={}).status_code == 401
    assert other_client.post("/api/me/two-step/off", json={"code": "123456"}).status_code == 401


def test_the_accounts_functions_refuse_staff_themselves(conn, app_conn, client, pmc):
    staff = join_as_staff(client, conn, app_conn, pmc)
    session = accounts.Session(staff.login_id, pmc.pmc_id, "staff", staff.person_id, staff.email)
    with pytest.raises(accounts.Refused, match="staff sign in with the authenticator"):
        accounts.start_two_step(app_conn, session)
