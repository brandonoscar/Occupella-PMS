"""Invites, enrolment and disabling logins (src/pms/api, src/pms/accounts)."""

import hashlib
import uuid

import pytest
from helpers import add_staff, make_pmc
from people import (
    PASSWORD,
    invite_token,
    join_as_owner,
    join_as_staff,
    join_as_tenant,
    tenant_of,
    totp_from,
)

# --- staff make invites -------------------------------------------------------------------


def test_staff_invite_an_owner_with_a_link_whose_token_is_kept_only_as_a_hash(
    client, conn, app_conn, pmc
):
    staff = join_as_staff(client, conn, app_conn, pmc)
    owner_id = pmc.owners[0].owner_id

    answer = client.post(
        "/api/invites",
        json={"kind": "owner", "person_id": str(owner_id), "email": " Owner.One@Example.com "},
    )

    assert answer.status_code == 201
    link = answer.json()
    assert link["url"].startswith("http://testserver/invite#")
    token = link["url"].split("#", 1)[1]
    login = conn.execute(
        "SELECT kind, owner_id, email FROM trust_logins WHERE id = %s", (link["login_id"],)
    ).fetchone()
    assert login == ("owner", owner_id, "owner.one@example.com")
    invite = conn.execute(
        "SELECT token_hash, created_by, expires_at - created_at FROM trust_invites"
        " WHERE login_id = %s",
        (link["login_id"],),
    ).fetchone()
    assert bytes(invite[0]) == hashlib.sha256(token.encode()).digest()
    assert invite[1] == staff.person_id
    assert invite[2].days == 7
    audit = conn.execute(
        "SELECT staff_id FROM trust_audit_log WHERE table_name = 'trust_invites'"
        " AND row_data ->> 'login_id' = %s",
        (link["login_id"],),
    ).fetchall()
    assert audit == [(staff.person_id,)]
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE row_data::text LIKE %s", (f"%{token}%",)
    ).fetchone() == (0,)


def test_inviting_again_reuses_the_login(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc)
    body = {"kind": "owner", "person_id": str(pmc.owners[0].owner_id), "email": "o@example.com"}

    first = client.post("/api/invites", json=body).json()
    second = client.post("/api/invites", json=body).json()

    assert first["login_id"] == second["login_id"]
    assert first["url"] != second["url"]


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_invite(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)

    answer = client.post(
        "/api/invites",
        json={"kind": "owner", "person_id": str(pmc.owners[1].owner_id), "email": "x@example.com"},
    )

    assert answer.status_code == (401 if caller == "nobody" else 403)


def test_staff_can_invite_only_their_own_pmcs_people(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc)
    other = make_pmc(conn)

    answer = client.post(
        "/api/invites",
        json={"kind": "owner", "person_id": str(other.owners[0].owner_id), "email": "x@ex.com"},
    )

    assert answer.status_code == 404
    assert answer.json()["detail"] == f"no owner {other.owners[0].owner_id} in this PMC"


def test_an_email_signs_in_as_one_person(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc)
    first = {"kind": "owner", "person_id": str(pmc.owners[0].owner_id), "email": "a@example.com"}
    client.post("/api/invites", json=first)

    same_email = client.post(
        "/api/invites", json={**first, "person_id": str(pmc.owners[1].owner_id)}
    )
    other_email = client.post("/api/invites", json={**first, "email": "b@example.com"})

    assert same_email.status_code == 409
    assert (
        same_email.json()["detail"] == "a@example.com already signs in to this PMC as someone else"
    )
    assert other_email.status_code == 409
    assert other_email.json()["detail"] == "this owner signs in as a@example.com"


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "vendor", "person_id": str(uuid.uuid4()), "email": "a@example.com"},
        {"kind": "owner", "person_id": "nope", "email": "a@example.com"},
        {"kind": "owner", "person_id": str(uuid.uuid4())},
        {"kind": "owner", "person_id": str(uuid.uuid4()), "email": "a"},
    ],
)
def test_a_malformed_invite_is_refused(client, conn, app_conn, pmc, body):
    join_as_staff(client, conn, app_conn, pmc)

    assert client.post("/api/invites", json=body).status_code == 422


def test_an_email_the_database_would_refuse_is_refused(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc)

    answer = client.post(
        "/api/invites",
        json={"kind": "owner", "person_id": str(pmc.owners[0].owner_id), "email": "not an email"},
    )

    assert answer.status_code == 422


# --- the person opens the link ------------------------------------------------------------


def test_the_link_shows_who_it_is_for(client, app_conn, pmc):
    token = invite_token(app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, "o@example.com")

    answer = client.post("/api/invites/view", json={"token": token})

    assert answer.status_code == 200
    details = answer.json()
    assert (details["kind"], details["email"], details["name"]) == (
        "owner",
        "o@example.com",
        "Owner 1",
    )
    assert details["pmc_name"].startswith("Synthetic PMC")


@pytest.mark.parametrize("state", ["accepted", "expired", "disabled", "made up"])
def test_a_link_that_cant_be_used_says_so(client, conn, app_conn, pmc, state):
    token = invite_token(app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, "o@example.com")
    if state == "accepted":
        client.post("/api/invites/accept", json={"token": token, "password": PASSWORD})
    elif state == "expired":
        conn.execute(
            "UPDATE trust_invites SET expires_at = created_at + interval '1 microsecond'"
            " WHERE token_hash = %s",
            (hashlib.sha256(token.encode()).digest(),),
        )
    elif state == "disabled":
        conn.execute("UPDATE trust_logins SET disabled_at = now() WHERE email = 'o@example.com'")
    else:
        token = "x" * 43

    for path, body in [
        ("/api/invites/view", {"token": token}),
        ("/api/invites/accept", {"token": token, "password": PASSWORD}),
        ("/api/invites/confirm", {"token": token, "code": "123456"}),
    ]:
        answer = client.post(path, json=body)
        assert answer.status_code == 404, path
        assert answer.json() == {"detail": "this invite link can't be used: ask for a new one"}


@pytest.mark.parametrize("token", ["", "short", "x" * 101])
def test_a_malformed_token_is_refused(client, token):
    assert client.post("/api/invites/view", json={"token": token}).status_code == 422


def test_an_owner_accepts_with_a_password_and_is_signed_in(client, conn, app_conn, pmc):
    token = invite_token(app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, "o@example.com")

    answer = client.post("/api/invites/accept", json={"token": token, "password": PASSWORD})

    assert answer.status_code == 200
    assert answer.json()["kind"] == "owner"
    assert client.get("/api/me").status_code == 200
    stored = conn.execute(
        "SELECT s.password_hash, s.totp_secret FROM trust_login_secrets AS s"
        " JOIN trust_logins AS l ON l.id = s.login_id WHERE l.email = 'o@example.com'"
    ).fetchone()
    assert stored[0].startswith("$argon2id$") and PASSWORD not in stored[0]
    assert stored[1] is None


def test_a_short_password_is_refused(client, app_conn, pmc):
    token = invite_token(app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, "o@example.com")

    answer = client.post("/api/invites/accept", json={"token": token, "password": "short pw"})

    assert answer.status_code == 422
    assert answer.json() == {"detail": "a password has 12 to 1024 characters"}
    assert client.post("/api/invites/view", json={"token": token}).status_code == 200


def test_a_new_invite_resets_the_password_and_ends_old_sessions(
    client, other_client, conn, app_conn, pmc
):
    owner = join_as_owner(client, app_conn, pmc)
    token = invite_token(app_conn, pmc.pmc_id, "owner", owner.person_id, owner.email)

    reset = other_client.post(
        "/api/invites/accept", json={"token": token, "password": "a brand new passphrase"}
    )

    assert reset.status_code == 200
    assert client.get("/api/me").status_code == 401  # the old browser is signed out
    assert other_client.get("/api/me").status_code == 200
    old = {"pmc_id": str(pmc.pmc_id), "email": owner.email, "password": PASSWORD}
    assert client.post("/api/sign-in", json=old).status_code == 401


def test_a_tenant_joins_the_same_way(client, conn, app_conn, pmc):
    tenant = join_as_tenant(client, conn, app_conn, pmc)

    me = client.get("/api/me").json()

    assert (me["kind"], me["person_id"]) == ("tenant", str(tenant.person_id))
    assert tenant_of(conn, pmc) == tenant.person_id


# --- staff enroll an authenticator --------------------------------------------------------


def test_staff_enroll_an_authenticator_before_the_invite_is_used(client, conn, app_conn, pmc):
    staff_id = add_staff(conn, pmc.pmc_id, "Staff 2")
    token = invite_token(app_conn, pmc.pmc_id, "staff", staff_id, "s2@example.com")

    enrolled = client.post("/api/invites/accept", json={"token": token, "password": PASSWORD})

    assert enrolled.status_code == 200
    uri = enrolled.json()["totp_uri"]
    assert uri.startswith("otpauth://totp/Occupella%20PMS:s2%40example.com?secret=")
    assert client.get("/api/me").status_code == 401  # not signed in yet
    assert client.post("/api/invites/view", json={"token": token}).status_code == 200

    wrong = client.post("/api/invites/confirm", json={"token": token, "code": "000000"})
    assert wrong.status_code == 401
    right = client.post("/api/invites/confirm", json={"token": token, "code": totp_from(uri).now()})
    assert right.status_code == 200
    assert right.json()["kind"] == "staff"
    assert client.post("/api/invites/view", json={"token": token}).status_code == 404


def test_confirming_before_setting_a_password_is_refused(client, conn, app_conn, pmc):
    staff_id = add_staff(conn, pmc.pmc_id, "Staff 3")
    token = invite_token(app_conn, pmc.pmc_id, "staff", staff_id, "s3@example.com")

    answer = client.post("/api/invites/confirm", json={"token": token, "code": "123456"})

    assert answer.status_code == 422
    assert answer.json() == {"detail": "set a password from this invite first"}


@pytest.mark.parametrize("code", ["12345", "abcdef", "1234567"])
def test_a_malformed_code_is_refused(client, code):
    answer = client.post("/api/invites/confirm", json={"token": "x" * 43, "code": code})

    assert answer.status_code == 422


# --- staff disable logins -----------------------------------------------------------------


def test_disabling_a_login_ends_its_sessions_and_sign_ins(
    client, other_client, conn, app_conn, pmc
):
    join_as_staff(client, conn, app_conn, pmc)
    owner = join_as_owner(other_client, app_conn, pmc)

    answer = client.post(f"/api/logins/{owner.login_id}/disable", json={})

    assert answer.status_code == 204
    assert other_client.get("/api/me").status_code == 401
    body = {"pmc_id": str(pmc.pmc_id), "email": owner.email, "password": PASSWORD}
    assert other_client.post("/api/sign-in", json=body).status_code == 401
    assert client.post(f"/api/logins/{owner.login_id}/disable", json={}).status_code == 404


def test_staff_cant_disable_themselves_or_another_pmcs_login(
    client, other_client, conn, app_conn, pmc
):
    staff = join_as_staff(client, conn, app_conn, pmc)
    other = make_pmc(conn)
    stranger = join_as_owner(other_client, app_conn, other)

    assert client.post(f"/api/logins/{staff.login_id}/disable", json={}).status_code == 422
    assert client.post(f"/api/logins/{stranger.login_id}/disable", json={}).status_code == 404
    assert other_client.get("/api/me").status_code == 200


def test_only_staff_disable_logins(client, other_client, conn, app_conn, pmc):
    owner = join_as_owner(client, app_conn, pmc)
    staff = join_as_staff(other_client, conn, app_conn, pmc)

    assert client.post(f"/api/logins/{staff.login_id}/disable", json={}).status_code == 403
    assert client.post(f"/api/logins/{owner.login_id}/disable", json={}).status_code == 403
    assert client.post("/api/logins/not-a-uuid/disable", json={}).status_code in (403, 422)
