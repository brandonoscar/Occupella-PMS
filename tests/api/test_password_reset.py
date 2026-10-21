"""A forgotten password, reset by email, and invites sent by email (docs/adr/0007-email.md):
an owner or a tenant asks at sign-in and gets a link that sets a new password; staff reset
through an admin's new invite. The answer never says whether an email has a login. An invite
staff make is emailed too, its link held only until it is sent. Every name and address here is
synthetic."""

import pytest
from helpers import make_pmc
from people import PASSWORD, join_as_owner, join_as_staff, join_as_tenant, tenant_of

from pms import accounts, email

ANSWER = (
    "If that email signs in here as an owner or a tenant, a link to set a new password is on"
    " its way."
)
NEW_PASSWORD = "another long synthetic passphrase"


class Kept:
    name = "fake"

    def __init__(self):
        self.sent: list[email.Message] = []

    def send(self, message):
        self.sent.append(message)


@pytest.fixture
def kept(client):
    sender = Kept()
    client.app.state.email = sender
    return sender


def ask(client, pmc, address):
    return client.post("/api/password-reset", json={"pmc_id": str(pmc.pmc_id), "email": address})


def resets(conn, pmc):
    return conn.execute(
        "SELECT to_address, owner_id, tenant_id, needs_link FROM trust_emails"
        " WHERE pmc_id = %s AND kind = 'password_reset' ORDER BY created_at",
        (pmc.pmc_id,),
    ).fetchall()


def link_in(message):
    return next(line for line in message.body.splitlines() if "/invite#" in line).strip()


# --- resetting ----------------------------------------------------------------------------


def test_sign_in_says_whether_a_password_can_be_reset(client):
    assert client.get("/api/password-reset").json() == {"available": False}
    client.app.state.email = email.FakeSender()
    assert client.get("/api/password-reset").json() == {"available": True}


def test_an_owner_resets_a_forgotten_password_from_the_emailed_link(
    client, other_client, conn, app_conn, pmc, kept
):
    owner = join_as_owner(other_client, app_conn, pmc)
    answer = ask(client, pmc, owner.email.upper())  # any case
    assert (answer.status_code, answer.json()) == (202, {"detail": ANSWER})
    assert resets(conn, pmc) == [(owner.email, owner.person_id, None, True)]

    (message,) = [m for m in kept.sent if m.to == owner.email]
    assert "Set a new password" in message.subject
    link = link_in(message)
    token = link.split("#", 1)[1]
    stored = conn.execute(
        "SELECT body FROM trust_emails WHERE pmc_id = %s AND kind = 'password_reset'",
        (pmc.pmc_id,),
    ).fetchone()[0]
    assert token not in stored

    # The old password works until the link is used; then the new one does.
    signed = {"pmc_id": str(pmc.pmc_id), "email": owner.email, "password": PASSWORD}
    assert client.post("/api/sign-in", json=signed).status_code == 200
    accepted = client.post("/api/invites/accept", json={"token": token, "password": NEW_PASSWORD})
    assert accepted.status_code == 200, accepted.text
    assert client.post("/api/sign-in", json=signed).status_code == 401
    renewed = {**signed, "password": NEW_PASSWORD}
    assert client.post("/api/sign-in", json=renewed).status_code == 200


def test_a_tenant_resets_too(client, other_client, conn, app_conn, pmc, kept):
    tenant = join_as_tenant(other_client, conn, app_conn, pmc)
    assert ask(client, pmc, tenant.email).status_code == 202
    assert resets(conn, pmc) == [(tenant.email, None, tenant.person_id, True)]


@pytest.mark.parametrize("who", ["nobody", "staff", "disabled", "other company"])
def test_no_reset_goes_out_and_the_answer_is_the_same(
    client, other_client, conn, app_conn, pmc, kept, who
):
    if who == "nobody":
        address = "no.login@example.com"
    elif who == "staff":
        address = join_as_staff(other_client, conn, app_conn, pmc).email
    elif who == "disabled":
        owner = join_as_owner(other_client, app_conn, pmc)
        accounts.disable_login(app_conn, pmc.pmc_id, owner.login_id)
        address = owner.email
    else:
        other = make_pmc(conn)
        address = join_as_owner(other_client, app_conn, other).email
    answer = ask(client, pmc, address)
    assert (answer.status_code, answer.json()) == (202, {"detail": ANSWER})
    assert resets(conn, pmc) == []


def test_at_most_three_resets_an_hour(client, other_client, conn, app_conn, pmc, kept):
    owner = join_as_owner(other_client, app_conn, pmc)
    for _ in range(4):
        assert ask(client, pmc, owner.email).status_code == 202
    assert len(resets(conn, pmc)) == accounts.RESETS_PER_HOUR


def test_a_server_that_sends_no_email_writes_no_reset(client, other_client, conn, app_conn, pmc):
    owner = join_as_owner(other_client, app_conn, pmc)
    assert ask(client, pmc, owner.email).status_code == 202
    assert resets(conn, pmc) == []


@pytest.mark.parametrize(
    "body",
    [
        {"email": "a@example.com"},
        {"pmc_id": "not-an-id", "email": "a@example.com"},
        {"pmc_id": "00000000-0000-0000-0000-000000000000"},
        {"pmc_id": "00000000-0000-0000-0000-000000000000", "email": "x" * 321},
    ],
)
def test_bad_input_is_refused(client, body):
    assert client.post("/api/password-reset", json=body).status_code == 422


# --- invites by email -----------------------------------------------------------------------


def test_an_invite_is_emailed_with_its_link_and_shown_once(client, conn, app_conn, pmc, kept):
    join_as_staff(client, conn, app_conn, pmc, name="Admin 1")
    tenant = tenant_of(conn, pmc)
    made = client.post(
        "/api/invites",
        json={"kind": "tenant", "person_id": str(tenant), "email": "Tenant.New@example.com"},
    )
    assert made.status_code == 201, made.text
    invite = made.json()
    assert invite["emailed"] is True

    (message,) = [m for m in kept.sent if m.to == "tenant.new@example.com"]
    assert link_in(message) == invite["url"]
    row = conn.execute(
        "SELECT kind, tenant_id, needs_link, body FROM trust_emails WHERE pmc_id = %s"
        " AND kind = 'invite'",
        (pmc.pmc_id,),
    ).fetchone()
    assert row[:3] == ("invite", tenant, True)
    assert invite["url"] not in row[3]


def test_without_email_an_invite_is_only_shown(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc, name="Admin 1")
    made = client.post(
        "/api/invites",
        json={"kind": "owner", "person_id": str(pmc.owners[0].owner_id), "email": "o@example.com"},
    )
    assert made.json()["emailed"] is False
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_emails WHERE pmc_id = %s AND kind = 'invite'",
            (pmc.pmc_id,),
        ).fetchone()[0]
        == 0
    )


def test_me_says_whether_the_server_sends_email(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc)
    assert client.get("/api/me").json()["email_on"] is False
    client.app.state.email = email.FakeSender()
    assert client.get("/api/me").json()["email_on"] is True
