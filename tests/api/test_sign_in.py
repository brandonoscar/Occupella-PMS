"""Health, signing in and out, sessions and the browser guards (src/pms/api, src/pms/accounts)."""

import pytest
from fastapi.testclient import TestClient
from people import PASSWORD, join_as_owner, join_as_staff, next_code

from pms.api import Settings, create_app
from pms.api import app as api_app

# --- health -------------------------------------------------------------------------------


def test_health_answers_when_the_database_does(client):
    answer = client.get("/health")

    assert answer.status_code == 200
    assert answer.json() == {"status": "ok"}


def test_health_says_when_the_database_is_unavailable(monkeypatch):
    monkeypatch.setattr(api_app, "HEALTH_TIMEOUT", 0.2)
    nowhere = Settings(database_url="postgres://postgres@127.0.0.1:1/pms?connect_timeout=1")

    with TestClient(create_app(nowhere)) as client:
        answer = client.get("/health")

    assert answer.status_code == 503
    assert answer.json() == {"status": "database unavailable"}


# --- owners and tenants sign in with a password ----------------------------------------------


def test_an_owner_signs_in_and_reads_who_they_are(client, other_client, app_conn, pmc):
    owner = join_as_owner(other_client, app_conn, pmc)

    answer = client.post(
        "/api/sign-in",
        json={"pmc_id": str(pmc.pmc_id), "email": owner.email.upper(), "password": PASSWORD},
    )

    assert answer.status_code == 200
    me = client.get("/api/me").json()
    assert me["kind"] == "owner" and me["name"] == "Owner 1" and me["email"] == owner.email
    assert me["pmc_id"] == str(pmc.pmc_id) and me["pmc_name"].startswith("Synthetic PMC")
    cookie = answer.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie


def test_the_cookie_is_secure_unless_told_otherwise(database_url, app_conn, pmc):
    with TestClient(create_app(Settings(database_url=database_url))) as client:
        join_as_owner(client, app_conn, pmc)
        answer = client.post(
            "/api/sign-in",
            json={
                "pmc_id": str(pmc.pmc_id),
                "email": f"owner0.{pmc.owners[0].owner_id.hex[:8]}@example.com",
                "password": PASSWORD,
            },
        )

    assert "Secure" in answer.headers["set-cookie"]


@pytest.mark.parametrize("wrong", ["password", "email", "pmc", "disabled"])
def test_every_refused_sign_in_reads_the_same(client, other_client, conn, app_conn, pmc, wrong):
    owner = join_as_owner(other_client, app_conn, pmc)
    if wrong == "disabled":
        conn.execute("UPDATE trust_logins SET disabled_at = now() WHERE id = %s", (owner.login_id,))
    body = {
        "pmc_id": str(pmc.pmc_id if wrong != "pmc" else pmc.owners[0].owner_id),
        "email": owner.email if wrong != "email" else "nobody@example.com",
        "password": PASSWORD if wrong != "password" else "not the password",
    }

    answer = client.post("/api/sign-in", json=body)

    assert answer.status_code == 401
    assert answer.json() == {"detail": "the email or password is wrong"}
    assert "set-cookie" not in answer.headers


def test_ten_failures_lock_the_login_even_for_the_right_password(
    client, other_client, conn, app_conn, pmc
):
    owner = join_as_owner(other_client, app_conn, pmc)
    wrong = {"pmc_id": str(pmc.pmc_id), "email": owner.email, "password": "not the password"}
    for _ in range(10):
        assert client.post("/api/sign-in", json=wrong).status_code == 401

    right = client.post("/api/sign-in", json={**wrong, "password": PASSWORD})

    assert right.status_code == 401
    locked, failures = conn.execute(
        "SELECT locked_until > now() + interval '14 minutes', failed_sign_ins"
        " FROM trust_login_secrets WHERE login_id = %s",
        (owner.login_id,),
    ).fetchone()
    assert locked and failures == 0
    conn.execute(
        "UPDATE trust_login_secrets SET locked_until = now() - interval '1 second'"
        " WHERE login_id = %s",
        (owner.login_id,),
    )
    assert client.post("/api/sign-in", json={**wrong, "password": PASSWORD}).status_code == 200


def test_a_right_password_clears_the_failures(client, other_client, conn, app_conn, pmc):
    owner = join_as_owner(other_client, app_conn, pmc)
    body = {"pmc_id": str(pmc.pmc_id), "email": owner.email, "password": "nope, wrong"}
    client.post("/api/sign-in", json=body)
    client.post("/api/sign-in", json={**body, "password": PASSWORD})

    assert conn.execute(
        "SELECT failed_sign_ins FROM trust_login_secrets WHERE login_id = %s", (owner.login_id,)
    ).fetchone() == (0,)


# --- staff also need their authenticator --------------------------------------------------


def test_staff_sign_in_with_a_code_and_each_code_works_once(
    client, other_client, conn, app_conn, pmc
):
    staff = join_as_staff(other_client, conn, app_conn, pmc)
    body = {"pmc_id": str(pmc.pmc_id), "email": staff.email, "password": PASSWORD}

    missing = client.post("/api/sign-in", json=body)
    assert missing.status_code == 401
    assert missing.json() == {"detail": "enter the code from your authenticator app"}
    code = next_code(staff)
    assert client.post("/api/sign-in", json={**body, "code": code}).status_code == 200
    assert client.get("/api/me").json()["kind"] == "staff"

    again = client.post("/api/sign-in", json={**body, "code": code})
    assert again.status_code == 401
    assert "already used" in again.json()["detail"]


def test_a_wrong_code_counts_as_a_failure(client, other_client, conn, app_conn, pmc):
    staff = join_as_staff(other_client, conn, app_conn, pmc)
    body = {"pmc_id": str(pmc.pmc_id), "email": staff.email, "password": PASSWORD}

    assert client.post("/api/sign-in", json={**body, "code": "000000"}).status_code == 401
    assert conn.execute(
        "SELECT failed_sign_ins FROM trust_login_secrets WHERE login_id = %s", (staff.login_id,)
    ).fetchone() == (1,)


def test_staff_who_never_enrolled_are_told_to_finish(client, conn, app_conn, pmc):
    from helpers import add_staff
    from people import invite_token

    staff_id = add_staff(conn, pmc.pmc_id, "Half Done")
    token = invite_token(app_conn, pmc.pmc_id, "staff", staff_id, "half.done@example.com")
    client.post("/api/invites/accept", json={"token": token, "password": PASSWORD})

    answer = client.post(
        "/api/sign-in",
        json={"pmc_id": str(pmc.pmc_id), "email": "half.done@example.com", "password": PASSWORD},
    )

    assert answer.status_code == 422
    assert answer.json() == {"detail": "finish setting up from your invite link first"}


# --- sessions -----------------------------------------------------------------------------


def test_signing_out_ends_the_session(client, app_conn, pmc):
    join_as_owner(client, app_conn, pmc)
    assert client.get("/api/me").status_code == 200

    assert client.post("/api/sign-out", json={}).status_code == 204

    assert client.get("/api/me").status_code == 401


def test_signing_out_without_a_session_is_fine(client):
    assert client.post("/api/sign-out", json={}).status_code == 204


def test_an_expired_session_is_refused(client, conn, app_conn, pmc):
    owner = join_as_owner(client, app_conn, pmc)
    conn.execute(
        "UPDATE trust_sessions SET expires_at = created_at + interval '1 microsecond'"
        " WHERE login_id = %s",
        (owner.login_id,),
    )

    assert client.get("/api/me").status_code == 401


def test_a_session_lasts_twelve_hours(conn, client, app_conn, pmc):
    owner = join_as_owner(client, app_conn, pmc)

    assert (
        conn.execute(
            "SELECT expires_at - created_at FROM trust_sessions WHERE login_id = %s",
            (owner.login_id,),
        )
        .fetchone()[0]
        .total_seconds()
        == 12 * 3600
    )


def test_no_session_and_a_made_up_cookie_are_refused(client):
    assert client.get("/api/me").status_code == 401
    client.cookies.set("pms_session", "made-up-token")
    assert client.get("/api/me").json() == {"detail": "sign in first"}


# --- bad input and other sites ------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"pmc_id": "not-a-uuid", "email": "a@example.com", "password": "x"},
        {"pmc_id": "00000000-0000-4000-8000-000000000001", "email": "a@example.com"},
        {
            "pmc_id": "00000000-0000-4000-8000-000000000001",
            "email": "a@example.com",
            "password": "x",
            "code": "12345",
        },
        {
            "pmc_id": "00000000-0000-4000-8000-000000000001",
            "email": "a@example.com",
            "password": "x" * 1025,
        },
    ],
)
def test_a_malformed_sign_in_is_refused(client, body):
    assert client.post("/api/sign-in", json=body).status_code == 422


def test_a_request_from_another_site_is_refused(client):
    answer = client.post("/api/sign-in", json={}, headers={"Origin": "https://attacker.invalid"})

    assert answer.status_code == 403
    assert answer.json() == {"detail": "this request came from another site"}


def test_a_request_from_our_own_site_goes_through(client):
    answer = client.post("/api/sign-in", json={}, headers={"Origin": "http://testserver"})

    assert answer.status_code == 422  # past the guard, refused for its empty body


def test_a_change_must_be_sent_as_json(client):
    answer = client.post(
        "/api/sign-in",
        content="email=a&password=b",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert answer.status_code == 415


def test_api_answers_are_never_cached_or_sniffed(client):
    answer = client.get("/api/me")

    assert answer.headers["cache-control"] == "no-store"
    assert answer.headers["x-content-type-options"] == "nosniff"
    assert answer.headers["referrer-policy"] == "no-referrer"
