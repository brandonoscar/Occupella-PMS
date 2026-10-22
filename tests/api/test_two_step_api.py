"""Two-step sign-in for owners and tenants over the API (src/pms/api/two_step.py, the sign-in and
invite routes in src/pms/api/app.py): an owner or a tenant turns it on and off from the portal,
staff require it for the company from Settings, Logins, and sign-in and invites follow it."""

import time

import pyotp
import pytest
from people import (
    PASSWORD,
    invite_token,
    join_as,
    join_as_owner,
    join_as_staff,
    join_as_tenant,
    tenant_of,
    totp_from,
)

from pms import accounts


class Clock:
    """The authenticator's clock, from now, moved a step (30 seconds) at a time by hand, so each
    code a test uses is fresh. Staff who join set up their app on the real clock, within a step
    of it."""

    def __init__(self) -> None:
        self.now = time.time()

    def time(self) -> float:
        return self.now

    def tick(self) -> None:
        self.now += 30


@pytest.fixture
def clock(monkeypatch):
    found = Clock()
    monkeypatch.setattr(accounts, "time", found)
    return found


def code(secret, clock, steps=0):
    return pyotp.TOTP(secret).at(clock.now + 30 * steps)


def wrong(secret, clock):
    """A code none of the steps around now gives."""
    valid = {code(secret, clock, n) for n in (-1, 0, 1)}
    return next(c for c in ("000000", "111111", "222222", "333333") if c not in valid)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail):
    assert answer.status_code == status, answer.text
    assert answer.json()["detail"] == detail


def body(pmc, person, password=PASSWORD, **more):
    return {"pmc_id": str(pmc.pmc_id), "email": person.email, "password": password, **more}


def turn_on(client, clock):
    setup = ok(client.post("/api/me/two-step/start", json={}))
    ok(client.post("/api/me/two-step/confirm", json={"code": code(setup["secret"], clock)}))
    clock.tick()
    return setup["secret"]


def require(client, required=True):
    return ok(client.post("/api/logins/two-step", json={"required": required}))


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Manager 1")


# --- an owner or a tenant turns it on and off -------------------------------------------------


@pytest.mark.parametrize("kind", ["owner", "tenant"])
def test_a_person_turns_it_on_and_then_signs_in_with_a_code(
    client, other_client, conn, app_conn, pmc, clock, kind
):
    person_id = pmc.owners[0].owner_id if kind == "owner" else tenant_of(conn, pmc)
    person = join_as(other_client, app_conn, pmc.pmc_id, kind, person_id)
    me = ok(other_client.get("/api/me"))
    assert (me["two_step"], me["two_step_required"]) == (False, False)

    setup = ok(other_client.post("/api/me/two-step/start", json={}))
    assert setup["totp_uri"].startswith("otpauth://totp/Occupella%20PMS:")
    assert f"secret={setup['secret']}" in setup["totp_uri"]
    # Waiting for its first code, it changes nothing at sign-in.
    ok(client.post("/api/sign-in", json=body(pmc, person)))
    turned = ok(
        other_client.post("/api/me/two-step/confirm", json={"code": code(setup["secret"], clock)})
    )
    assert turned == {"on": True, "required": False}
    assert ok(other_client.get("/api/me"))["two_step"] is True
    clock.tick()

    refused(
        client.post("/api/sign-in", json=body(pmc, person)),
        401,
        "enter the code from your authenticator app",
    )
    signed = client.post("/api/sign-in", json=body(pmc, person, code=code(setup["secret"], clock)))
    assert ok(signed)["kind"] == kind
    assert "HttpOnly" in signed.headers["set-cookie"]


def test_starting_again_shows_the_same_key_until_it_is_on(client, app_conn, pmc, clock):
    join_as_owner(client, app_conn, pmc)

    first = ok(client.post("/api/me/two-step/start", json={}))
    assert ok(client.post("/api/me/two-step/start", json={})) == first
    refused(
        client.post("/api/me/two-step/confirm", json={"code": wrong(first["secret"], clock)}),
        422,
        "that code is wrong or was already used: try the next one",
    )
    ok(client.post("/api/me/two-step/confirm", json={"code": code(first["secret"], clock)}))

    refused(client.post("/api/me/two-step/start", json={}), 409, "two-step sign-in is on already")
    refused(
        client.post("/api/me/two-step/confirm", json={"code": code(first["secret"], clock, 1)}),
        409,
        "two-step sign-in is on already",
    )


def test_confirming_before_starting_is_refused(client, app_conn, pmc):
    join_as_owner(client, app_conn, pmc)

    refused(
        client.post("/api/me/two-step/confirm", json={"code": "123456"}),
        422,
        "turn two-step sign-in on first",
    )


def test_turning_it_off_takes_a_current_code_and_a_wrong_one_counts(
    client, other_client, conn, app_conn, pmc, clock
):
    owner = join_as_owner(client, app_conn, pmc)
    secret = turn_on(client, clock)

    refused(
        client.post("/api/me/two-step/off", json={"code": wrong(secret, clock)}),
        422,
        "that code is wrong or was already used: try the next one",
    )
    assert conn.execute(
        "SELECT failed_sign_ins FROM trust_login_secrets WHERE login_id = %s", (owner.login_id,)
    ).fetchone() == (1,)
    assert ok(client.post("/api/me/two-step/off", json={"code": code(secret, clock)})) == {
        "on": False,
        "required": False,
    }

    assert ok(client.get("/api/me"))["two_step"] is False
    ok(other_client.post("/api/sign-in", json=body(pmc, owner)))
    refused(
        client.post("/api/me/two-step/off", json={"code": code(secret, clock)}),
        409,
        "two-step sign-in is off already",
    )


def test_turning_it_off_is_refused_while_the_company_requires_it(
    client, other_client, conn, app_conn, pmc, staff, clock
):
    join_as_tenant(other_client, conn, app_conn, pmc)
    secret = turn_on(other_client, clock)
    require(client)

    assert ok(other_client.get("/api/me"))["two_step_required"] is True
    refused(
        other_client.post("/api/me/two-step/off", json={"code": code(secret, clock)}),
        422,
        "your property manager requires two-step sign-in, so it stays on",
    )
    assert ok(other_client.get("/api/me"))["two_step"] is True


@pytest.mark.parametrize("caller", ["nobody", "staff"])
@pytest.mark.parametrize("path", ["start", "confirm", "off"])
def test_only_an_owner_or_a_tenant_changes_their_own(client, conn, app_conn, pmc, caller, path):
    if caller == "staff":
        join_as_staff(client, conn, app_conn, pmc)

    answer = client.post(f"/api/me/two-step/{path}", json={"code": "123456"})

    if caller == "nobody":
        refused(answer, 401, "sign in first")
    else:
        refused(
            answer,
            403,
            "only an owner or a tenant turns two-step sign-in on or off here: staff always sign"
            " in with the authenticator from their invite",
        )


@pytest.mark.parametrize("path", ["confirm", "off"])
@pytest.mark.parametrize("sent", [{}, {"code": "12345"}, {"code": "abcdef"}, {"code": 123456}])
def test_a_code_is_six_digits(client, app_conn, pmc, path, sent):
    join_as_owner(client, app_conn, pmc)

    assert client.post(f"/api/me/two-step/{path}", json=sent).status_code == 422


def test_staff_always_sign_in_with_a_code(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc)

    me = ok(client.get("/api/me"))

    assert (me["two_step"], me["two_step_required"]) == (True, True)


# --- the company requires it ------------------------------------------------------------------


def test_required_an_owner_without_it_sets_it_up_when_signing_in(
    client, other_client, conn, app_conn, pmc, staff, clock
):
    owner = join_as_owner(other_client, app_conn, pmc)
    ok(other_client.post("/api/sign-out", json={}), 204)
    require(client)

    first = other_client.post("/api/sign-in", json=body(pmc, owner))
    setup = ok(first)
    assert set(setup) == {"totp_uri", "secret"} and "set-cookie" not in first.headers
    assert ok(other_client.post("/api/sign-in", json=body(pmc, owner))) == setup
    assert other_client.get("/api/me").status_code == 401
    refused(
        other_client.post(
            "/api/sign-in", json=body(pmc, owner, code=wrong(setup["secret"], clock))
        ),
        401,
        "that code is wrong or was already used: try the next one",
    )
    refused(
        other_client.post("/api/sign-in", json=body(pmc, owner, password="not the password")),
        401,
        "the email or password is wrong",
    )

    signed = other_client.post(
        "/api/sign-in", json=body(pmc, owner, code=code(setup["secret"], clock))
    )

    assert ok(signed)["two_step"] is True and "set-cookie" in signed.headers
    assert conn.execute(
        "SELECT failed_sign_ins FROM trust_login_secrets WHERE login_id = %s", (owner.login_id,)
    ).fetchone() == (0,)


def test_requiring_it_signs_nobody_out(client, other_client, conn, app_conn, pmc, staff):
    join_as_tenant(other_client, conn, app_conn, pmc)

    require(client)

    me = ok(other_client.get("/api/me"))
    assert (me["two_step"], me["two_step_required"]) == (False, True)


def test_required_an_invited_owner_sets_it_up_from_the_invite(
    client, other_client, app_conn, pmc, staff, clock
):
    require(client)
    link = ok(
        client.post(
            "/api/invites",
            json={
                "kind": "owner",
                "person_id": str(pmc.owners[1].owner_id),
                "email": "o@example.com",
            },
        ),
        201,
    )
    token = link["url"].split("#", 1)[1]

    enroll = ok(
        other_client.post("/api/invites/accept", json={"token": token, "password": PASSWORD})
    )
    secret = totp_from(enroll["totp_uri"]).secret
    assert other_client.get("/api/me").status_code == 401
    me = ok(
        other_client.post(
            "/api/invites/confirm", json={"token": token, "code": code(secret, clock)}
        )
    )

    assert (me["kind"], me["two_step"]) == ("owner", True)


# --- recovery ---------------------------------------------------------------------------------


def test_a_reset_the_person_asked_for_keeps_it_and_signs_nobody_in(
    client, other_client, app_conn, pmc, clock
):
    owner = join_as_owner(client, app_conn, pmc)
    secret = turn_on(client, clock)
    token = invite_token(app_conn, pmc.pmc_id, "owner", owner.person_id, owner.email)  # by nobody

    answer = other_client.post(
        "/api/invites/accept", json={"token": token, "password": "a brand new passphrase"}
    )

    assert ok(answer) == {"pmc_id": str(pmc.pmc_id), "sign_in_with_code": True}
    assert "set-cookie" not in answer.headers
    assert client.get("/api/me").status_code == 401  # the new password ended every session
    refused(
        other_client.post("/api/sign-in", json=body(pmc, owner, password="a brand new passphrase")),
        401,
        "enter the code from your authenticator app",
    )
    ok(
        other_client.post(
            "/api/sign-in",
            json=body(pmc, owner, password="a brand new passphrase", code=code(secret, clock)),
        )
    )


def test_an_admins_invite_clears_it(client, other_client, conn, app_conn, pmc, staff, clock):
    tenant = join_as_tenant(other_client, conn, app_conn, pmc)
    turn_on(other_client, clock)
    link = ok(
        client.post(
            "/api/invites",
            json={"kind": "tenant", "person_id": str(tenant.person_id), "email": tenant.email},
        ),
        201,
    )

    me = ok(
        other_client.post(
            "/api/invites/accept",
            json={"token": link["url"].split("#", 1)[1], "password": "a brand new passphrase"},
        )
    )

    assert (me["kind"], me["two_step"]) == ("tenant", False)
    people = {p["name"]: p["two_step"] for p in ok(client.get("/api/people"))}
    assert people["Tenant 1"] is False


# --- the company's setting --------------------------------------------------------------------


def test_staff_require_it_and_who_changed_it_shows(client, conn, pmc, staff):
    assert ok(client.get("/api/logins/two-step")) == {
        "required": False,
        "changed_at": None,
        "changed_by": None,
    }

    set_ = require(client)
    again = require(client)

    assert (set_["required"], set_["changed_by"]) == (True, "Manager 1")
    assert again == set_  # saving what holds writes nothing
    assert conn.execute(
        "SELECT count(*) FROM trust_sign_in_settings WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (1,)
    assert require(client, required=False)["required"] is False
    assert ok(client.get("/api/logins/two-step"))["required"] is False


def test_logins_say_who_signs_in_with_a_code(client, other_client, app_conn, pmc, staff, clock):
    join_as_owner(other_client, app_conn, pmc)
    turn_on(other_client, clock)

    found = {p["name"]: p["two_step"] for p in ok(client.get("/api/people"))}

    assert found == {"Manager 1": True, "Owner 1": True, "Owner 2": False, "Tenant 1": False}


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_read_or_change_the_setting(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403

    assert client.get("/api/logins/two-step").status_code == status
    assert client.post("/api/logins/two-step", json={"required": True}).status_code == status
    assert conn.execute(
        "SELECT count(*) FROM trust_sign_in_settings WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_staff_whose_roles_leave_out_who_can_sign_in_read_it_only(
    client, other_client, conn, app_conn, pmc, staff
):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Rent", "areas": ["rent"]}), 201)
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )

    refused(
        other_client.post("/api/logins/two-step", json={"required": True}),
        403,
        "your roles don't let you change who can sign in: an admin can add it",
    )
    assert ok(other_client.get("/api/logins/two-step"))["required"] is False


@pytest.mark.parametrize("sent", [{}, {"required": "maybe"}, {"required": None}])
def test_the_setting_is_yes_or_no(client, staff, sent):
    assert client.post("/api/logins/two-step", json=sent).status_code == 422
