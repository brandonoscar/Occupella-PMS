"""A company's status (migration 20261105000100, `python -m pms.admin status`): what the server's
operator sets. Suspended: nobody signs in, a session already open stops answering, its API keys
stop and no password reset is sent, while its nightly run goes on; closed: the same, and its
nightly run stops; active again undoes either. Append-only, the latest row holding, with a
reason; nothing of the company is deleted."""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from helpers import make_pmc
from people import PASSWORD, accept, invite_token, join_as_staff, join_as_tenant, tenant_of

from pms import accounts, admin, nightly
from pms.api import create_app

URL = "http://testserver"


def set_status(database_url, monkeypatch, capsys, pmc, status, reason="synthetic reason"):
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert admin.main(["status", str(pmc.pmc_id), status, reason]) == 0
    assert capsys.readouterr().out == f"{pmc.pmc_id} is {status}\n"


def status_of(app_conn, pmc):
    return accounts.company_status(app_conn, pmc.pmc_id)


def sign_in(client, pmc, person):
    return client.post(
        "/api/sign-in",
        json={"pmc_id": str(pmc.pmc_id), "email": person.email, "password": PASSWORD},
    )


# --- the record ---------------------------------------------------------------------------------


def test_a_company_is_active_until_its_operator_says_otherwise(
    app_conn, pmc, database_url, monkeypatch, capsys
):
    assert status_of(app_conn, pmc) == "active"
    set_status(database_url, monkeypatch, capsys, pmc, "suspended")
    assert status_of(app_conn, pmc) == "suspended"
    set_status(database_url, monkeypatch, capsys, pmc, "closed")
    assert status_of(app_conn, pmc) == "closed"
    set_status(database_url, monkeypatch, capsys, pmc, "active", "back after review")
    assert status_of(app_conn, pmc) == "active"
    # Another company is untouched.
    assert status_of(app_conn, make_pmc(app_conn)) == "active"


def test_statuses_are_append_only_and_need_a_reason(conn, pmc):
    conn.execute(
        "INSERT INTO trust_company_statuses (pmc_id, status, reason) VALUES (%s, 'suspended', 'x')",
        (pmc.pmc_id,),
    )
    with pytest.raises(psycopg.errors.RestrictViolation):
        conn.execute(
            "UPDATE trust_company_statuses SET status = 'active' WHERE pmc_id = %s", (pmc.pmc_id,)
        )
    with pytest.raises(psycopg.errors.RestrictViolation):
        conn.execute("DELETE FROM trust_company_statuses WHERE pmc_id = %s", (pmc.pmc_id,))
    for status, reason in (("suspended", "  "), ("paused", "a reason")):
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO trust_company_statuses (pmc_id, status, reason) VALUES (%s, %s, %s)",
                (pmc.pmc_id, status, reason),
            )


def test_the_command_refuses_what_isnt_a_company_or_a_status(
    pmc, database_url, monkeypatch, capsys
):
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert admin.main(["status", str(uuid.uuid4()), "closed", "gone"]) == 2
    assert capsys.readouterr().err == "refused: no company has that id\n"
    with pytest.raises(SystemExit):
        admin.main(["status", str(pmc.pmc_id), "paused", "a reason"])
    assert admin.main(["status", str(pmc.pmc_id), "suspended", "   "]) == 2
    assert "refused:" in capsys.readouterr().err


# --- signing in, sessions and keys --------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "detail"),
    [
        ("suspended", "this company's account is suspended: ask whoever runs this server"),
        ("closed", "this company's account is closed"),
    ],
)
def test_nobody_signs_in_to_a_company_that_isnt_active(
    client, other_client, conn, app_conn, pmc, database_url, monkeypatch, capsys, status, detail
):
    tenant = join_as_tenant(client, conn, app_conn, pmc)
    assert client.get("/api/me").status_code == 200

    set_status(database_url, monkeypatch, capsys, pmc, status)

    # The session already open stops answering; a new sign-in is refused, with or without the
    # right password, the same way.
    assert client.get("/api/me").status_code == 401
    for password in (PASSWORD, "the wrong password entirely"):
        refused = other_client.post(
            "/api/sign-in",
            json={"pmc_id": str(pmc.pmc_id), "email": tenant.email, "password": password},
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["detail"] == detail

    set_status(database_url, monkeypatch, capsys, pmc, "active", "put right")
    assert sign_in(other_client, pmc, tenant).status_code == 200


@pytest.mark.parametrize(
    ("status", "detail"),
    [
        ("suspended", "this company's account is suspended: ask whoever runs this server"),
        ("closed", "this company's account is closed"),
    ],
)
def test_an_invite_of_a_company_that_isnt_active_is_refused_in_words(
    client, conn, app_conn, pmc, database_url, monkeypatch, capsys, status, detail
):
    # The AI pen test of 11/06: an invite made before the company was suspended still opened,
    # and accepting it set the password, then failed making the session (a server error).
    tenant = tenant_of(conn, pmc)
    token = invite_token(app_conn, pmc.pmc_id, "tenant", tenant, "tenant.status@example.com")
    set_status(database_url, monkeypatch, capsys, pmc, status)

    answers = [
        client.post("/api/invites/view", json={"token": token}),
        client.post("/api/invites/accept", json=accept(token)),
    ]

    assert [a.status_code for a in answers] == [403, 403]
    assert {a.json()["detail"] for a in answers} == {detail}
    assert conn.execute(
        "SELECT count(*) FROM trust_login_secrets AS s JOIN trust_logins AS l ON l.id = s.login_id"
        " WHERE l.tenant_id = %s",
        (tenant,),
    ).fetchone() == (0,)
    set_status(database_url, monkeypatch, capsys, pmc, "active", "put right")
    assert client.post("/api/invites/accept", json=accept(token)).status_code == 200


def test_a_suspended_company_sends_no_password_reset(
    app_conn, conn, client, pmc, database_url, monkeypatch, capsys
):
    tenant = join_as_tenant(client, conn, app_conn, pmc)
    assert accounts.request_reset(app_conn, pmc.pmc_id, tenant.email) is not None
    set_status(database_url, monkeypatch, capsys, pmc, "suspended")
    assert accounts.request_reset(app_conn, pmc.pmc_id, tenant.email) is None


def test_a_suspended_companys_api_keys_stop(
    settings, client, conn, app_conn, pmc, database_url, monkeypatch, capsys
):
    join_as_staff(client, conn, app_conn, pmc, name="Admin 1")
    made = client.post("/api/ai/keys", json={"name": "Assistant"})
    assert made.status_code == 201, made.text
    headers = {"Authorization": f"Bearer {made.json()['secret']}"}
    with TestClient(create_app(settings)) as software:
        assert software.get("/api/ai/me", headers=headers).status_code == 200
        set_status(database_url, monkeypatch, capsys, pmc, "suspended")
        assert software.get("/api/ai/me", headers=headers).status_code == 401


# --- the nightly run ----------------------------------------------------------------------------


def test_a_closed_companys_nightly_run_stops_and_a_suspended_ones_goes_on(
    app_conn, database_url, monkeypatch, capsys
):
    suspended, closed = make_pmc(app_conn), make_pmc(app_conn)
    set_status(database_url, monkeypatch, capsys, suspended, "suspended")
    set_status(database_url, monkeypatch, capsys, closed, "closed")
    ran = []
    monkeypatch.setattr(
        nightly, "run_pmc", lambda c, pmc_id, day, url, sending, partner: ran.append(pmc_id)
    )

    nightly.run_all(app_conn, None, URL)

    assert suspended.pmc_id in ran
    assert closed.pmc_id not in ran
