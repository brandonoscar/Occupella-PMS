"""Signing in through a host app (POST /api/external-sign-in, pms.external,
docs/adr/0013-sign-in-through-a-host.md), and a server whose password sign-in is off
(PMS_PASSWORD_SIGN_IN).

The host's verifier here is a stand-in (Host): a token is the key of a person it holds. What it
vouches for is the host's to get right; what the PMS still checks is tested here: the company is
one the host says the person is staff of, the login isn't disabled, the company is active, an
owner or a tenant joins only through their invite at its own email, and the books' admins follow
the host's.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from dataclasses import dataclass, field
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from helpers import add_staff, make_pmc
from people import invite_token, join_as_staff, login_of, tenant_of

from pms import deployment, external
from pms.api import Settings, create_app, throttle

ISSUER = "occupella"


@dataclass
class Host:
    """A host app's verifier, as a stand-in: each token names a person it signed in."""

    issuer: str = ISSUER
    people: dict[str, external.Person] = field(default_factory=dict)
    broken: bool = False

    def verify(self, token: str) -> external.Person | None:
        if self.broken:
            raise RuntimeError(f"the host's keys couldn't be fetched for {token}")
        return self.people.get(token)

    def signs_in(
        self,
        token: str,
        email: str,
        staff_of: dict[UUID, external.Membership] | None = None,
        second_step: bool = True,
        name: str = "Staff A",
        subject: str | None = None,
    ) -> external.Person:
        made = external.Person(
            subject=subject or f"user-{uuid.uuid4().hex}",
            email=email,
            name=name,
            staff_of=staff_of or {},
            second_step=second_step,
        )
        self.people[token] = made
        return made


def admin_of(company: str = "Company A") -> external.Membership:
    return external.Membership(admin=True, company_name=company)


def staff_of(company: str = "Company A") -> external.Membership:
    return external.Membership(admin=False, company_name=company)


@pytest.fixture
def host():
    return Host()


@pytest.fixture
def hosted(settings, host):
    """A server a host app signs people in to, its own password sign-in off."""
    with TestClient(
        create_app(dataclasses.replace(settings, verifier=host, password_sign_in=False))
    ) as test_client:
        yield test_client


def email_for(word: str) -> str:
    return f"{word}.{uuid.uuid4().hex[:8]}@example.com"


def sign_in(client, token: str, **more):
    return client.post("/api/external-sign-in", json={"token": token, **more})


def roles(conn, pmc_id: UUID, staff_id: UUID) -> set[str]:
    return {
        role
        for role in ("admin", "approve", "disburse")
        if conn.execute(
            "SELECT trust_staff_holds(%s, %s, %s)", (pmc_id, staff_id, role)
        ).fetchone()[0]
    }


def staff_of_login(conn, login_id: UUID) -> UUID:
    return conn.execute("SELECT staff_id FROM trust_logins WHERE id = %s", (login_id,)).fetchone()[
        0
    ]


# --- no host --------------------------------------------------------------------------------


def test_a_server_with_no_host_signs_no_one_in_through_one(client):
    refused = sign_in(client, "anything")
    assert refused.status_code == 404
    assert refused.json()["detail"] == "this server signs in no one through another app"


def test_a_host_names_itself_in_the_letters_its_links_take(settings):
    with pytest.raises(ValueError, match="issuer"):
        create_app(dataclasses.replace(settings, verifier=Host(issuer="Occupella App")))


# --- bad input ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"token": ""},
        {"token": "x" * 8193},
        {"token": "x", "pmc_id": "not an id"},
        {"token": "x", "invite": "short"},
        {"token": "x", "invite": "y" * 101},
        {"token": 7},
    ],
)
def test_a_sign_in_the_endpoint_cant_read_is_refused(hosted, body):
    assert hosted.post("/api/external-sign-in", json=body).status_code == 422


def test_a_sign_in_not_sent_as_json_is_refused(hosted):
    sent = hosted.post(
        "/api/external-sign-in", content="token=x", headers={"Content-Type": "text/plain"}
    )
    assert sent.status_code == 415


def test_a_sign_in_from_another_site_is_refused(hosted):
    sent = hosted.post(
        "/api/external-sign-in", json={"token": "x"}, headers={"Origin": "https://evil.example"}
    )
    assert sent.status_code == 403


# --- refusals -------------------------------------------------------------------------------


def test_a_token_the_host_doesnt_know_is_refused_as_a_wrong_password_is(hosted):
    refused = sign_in(hosted, "nobody's")
    assert refused.status_code == 401
    assert refused.json()["detail"] == "the email or password is wrong"
    assert "set-cookie" not in refused.headers


def test_refused_tokens_count_as_failed_sign_ins(hosted):
    for _ in range(throttle.SIGN_IN_FAILURES):
        assert sign_in(hosted, "nobody's").status_code == 401
    waiting = sign_in(hosted, "nobody's")
    assert waiting.status_code == 429
    assert "Retry-After" in waiting.headers


def test_a_host_that_cant_check_a_token_is_told_to_wait_and_the_token_isnt_logged(
    hosted, host, caplog
):
    host.broken = True
    with caplog.at_level(logging.WARNING, logger="pms.api"):
        waiting = sign_in(hosted, "secret-token-value")
    assert waiting.status_code == 503
    assert waiting.headers["Retry-After"] == "30"
    assert "the host's verifier failed: RuntimeError" in caplog.text
    assert "secret-token-value" not in caplog.text


def test_a_person_the_host_names_with_no_usable_email_is_refused(hosted, host):
    host.signs_in("t", "not an email", {uuid.uuid4(): admin_of()})
    assert sign_in(hosted, "t").status_code == 401


def test_a_person_who_is_staff_of_nothing_here_is_refused(hosted, host):
    host.signs_in("t", email_for("nobody"))
    assert sign_in(hosted, "t").status_code == 401


# --- staff: the books opened by their admin -------------------------------------------------


def test_an_admins_first_sign_in_opens_the_books_and_makes_them_its_admin(hosted, host, conn):
    company = uuid.uuid4()
    email = email_for("admin")
    host.signs_in("t", email, {company: admin_of("Company A")}, name="Staff A")

    signed = sign_in(hosted, "t")

    assert signed.status_code == 200, signed.text
    me = signed.json()
    assert (me["kind"], me["email"], me["name"], me["pmc_id"], me["pmc_name"]) == (
        "staff",
        email,
        "Staff A",
        str(company),
        "Company A",
    )
    assert "admin" in me["roles"]
    # The company's starting chart and kinds of fee, as `python -m pms.admin setup` gives them.
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_expense_accounts WHERE pmc_id = %s", (company,)
        ).fetchone()[0]
        > 0
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_charge_types WHERE pmc_id = %s", (company,)
        ).fetchone()[0]
        > 0
    )
    # The role names no staff member but the host; the identity is on the audit log.
    assert conn.execute(
        "SELECT changed_by, host_issuer FROM trust_staff_roles WHERE pmc_id = %s", (company,)
    ).fetchall() == [(None, ISSUER)]
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_audit_log WHERE pmc_id = %s"
            " AND table_name = 'trust_external_identities'",
            (company,),
        ).fetchone()[0]
        == 1
    )
    # The session is the books' own: its cookie works, and it has no password to sign in with.
    assert hosted.get("/api/me").json()["pmc_id"] == str(company)
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_login_secrets WHERE pmc_id = %s", (company,)
        ).fetchone()[0]
        == 0
    )


def test_staff_who_arent_its_admin_cant_open_a_companys_books(hosted, host, conn):
    company = uuid.uuid4()
    host.signs_in("t", email_for("staff"), {company: staff_of()})

    refused = sign_in(hosted, "t")

    assert refused.status_code == 403
    assert "an admin of the company opens them" in refused.json()["detail"]
    assert conn.execute("SELECT 1 FROM trust_pmcs WHERE pmc_id = %s", (company,)).fetchone() is None


def test_staff_sign_in_through_the_hosts_second_step(hosted, host, conn):
    company = uuid.uuid4()
    host.signs_in("t", email_for("admin"), {company: admin_of()}, second_step=False)

    refused = sign_in(hosted, "t")

    assert refused.status_code == 403
    assert "two-step sign-in" in refused.json()["detail"]
    assert conn.execute("SELECT 1 FROM trust_pmcs WHERE pmc_id = %s", (company,)).fetchone() is None


def test_the_same_person_signing_in_again_is_the_same_login(hosted, host, conn):
    company = uuid.uuid4()
    host.signs_in("t", email_for("admin"), {company: admin_of()})
    first = sign_in(hosted, "t").json()
    hosted.post("/api/sign-out", json={})

    again = sign_in(hosted, "t")

    assert again.status_code == 200
    assert again.json()["person_id"] == first["person_id"]
    assert (
        conn.execute("SELECT count(*) FROM trust_staff WHERE pmc_id = %s", (company,)).fetchone()[0]
        == 1
    )


# --- staff of books open already ------------------------------------------------------------


def test_staff_join_as_the_staff_login_with_their_email(hosted, host, conn, app_conn):
    pmc = make_pmc(conn)
    staff_id = add_staff(conn, pmc.pmc_id, "Staff B")
    email = email_for("staff")
    invite_token(app_conn, pmc.pmc_id, "staff", staff_id, email)  # a login, never accepted
    host.signs_in("t", email, {pmc.pmc_id: staff_of()})

    signed = sign_in(hosted, "t")

    assert signed.status_code == 200, signed.text
    assert signed.json()["person_id"] == str(staff_id)
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_external_identities WHERE login_id = %s",
            (login_of(app_conn, "staff", staff_id),),
        ).fetchone()[0]
        == 1
    )


def test_new_staff_get_a_record_of_their_own(hosted, host, conn):
    pmc = make_pmc(conn)
    host.signs_in("t", email_for("staff"), {pmc.pmc_id: staff_of()}, name="Staff C")

    signed = sign_in(hosted, "t")

    assert signed.status_code == 200, signed.text
    assert (
        conn.execute(
            "SELECT display_name FROM trust_staff WHERE id = %s", (signed.json()["person_id"],)
        ).fetchone()[0]
        == "Staff C"
    )
    assert signed.json()["roles"] == []


def test_an_email_an_owner_signs_in_with_isnt_a_staff_members(hosted, host, conn, app_conn):
    pmc = make_pmc(conn)
    email = email_for("owner")
    invite_token(app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, email)
    host.signs_in("t", email, {pmc.pmc_id: staff_of()})

    refused = sign_in(hosted, "t")

    assert refused.status_code == 409
    assert "as an owner or a tenant" in refused.json()["detail"]


def test_a_disabled_staff_login_isnt_joined(hosted, host, conn, app_conn):
    pmc = make_pmc(conn)
    staff_id = add_staff(conn, pmc.pmc_id, "Staff D")
    email = email_for("staff")
    invite_token(app_conn, pmc.pmc_id, "staff", staff_id, email)
    conn.execute(
        "UPDATE trust_logins SET disabled_at = now() WHERE id = %s",
        (login_of(app_conn, "staff", staff_id),),
    )
    host.signs_in("t", email, {pmc.pmc_id: staff_of()})

    assert sign_in(hosted, "t").status_code == 401


def test_a_linked_login_disabled_signs_in_no_more(hosted, host, conn):
    pmc = make_pmc(conn)
    host.signs_in("t", email_for("staff"), {pmc.pmc_id: staff_of()})
    signed = sign_in(hosted, "t").json()
    conn.execute(
        "UPDATE trust_logins SET disabled_at = now() WHERE staff_id = %s", (signed["person_id"],)
    )

    assert sign_in(hosted, "t").status_code == 401


def test_staff_the_host_no_longer_counts_sign_in_no_more(hosted, host, conn):
    pmc = make_pmc(conn)
    person = host.signs_in("t", email_for("staff"), {pmc.pmc_id: staff_of()})
    assert sign_in(hosted, "t").status_code == 200
    host.people["t"] = dataclasses.replace(person, staff_of={})

    assert sign_in(hosted, "t").status_code == 401
    assert sign_in(hosted, "t", pmc_id=str(pmc.pmc_id)).status_code == 401


def test_a_company_not_active_signs_in_no_one_through_the_host(hosted, host, conn):
    pmc = make_pmc(conn)
    host.signs_in("t", email_for("staff"), {pmc.pmc_id: staff_of()})
    assert sign_in(hosted, "t").status_code == 200
    conn.execute(
        "INSERT INTO trust_company_statuses (pmc_id, status, reason)"
        " VALUES (%s, 'suspended', 'unpaid')",
        (pmc.pmc_id,),
    )

    refused = sign_in(hosted, "t")

    assert refused.status_code == 403
    assert "suspended" in refused.json()["detail"]


def test_a_new_staff_member_of_a_company_not_active_isnt_made(hosted, host, conn):
    pmc = make_pmc(conn)
    conn.execute(
        "INSERT INTO trust_company_statuses (pmc_id, status, reason) VALUES (%s, 'closed', 'left')",
        (pmc.pmc_id,),
    )
    host.signs_in("t", email_for("staff"), {pmc.pmc_id: staff_of()})

    assert sign_in(hosted, "t").status_code == 403
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_logins WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone()[0]
        == 0
    )


# --- which company --------------------------------------------------------------------------


def test_a_person_of_several_companies_says_which_and_only_theirs(hosted, host, conn):
    first, second, theirs_not = make_pmc(conn), make_pmc(conn), make_pmc(conn)
    host.signs_in("t", email_for("staff"), {first.pmc_id: staff_of(), second.pmc_id: staff_of()})

    asked = sign_in(hosted, "t")
    assert asked.status_code == 409
    assert asked.json()["detail"] == "you sign in to more than one company here: say which one"

    chosen = sign_in(hosted, "t", pmc_id=str(second.pmc_id))
    assert chosen.status_code == 200
    assert chosen.json()["pmc_id"] == str(second.pmc_id)

    # Another company's id is refused as any refusal is, telling nothing of it.
    assert sign_in(hosted, "t", pmc_id=str(theirs_not.pmc_id)).status_code == 401


# --- the host's admins are the books' admins ------------------------------------------------


def test_the_admin_role_follows_the_hosts_and_the_company_keeps_one(hosted, host, conn, caplog):
    company = uuid.uuid4()
    first = host.signs_in("first", email_for("admin"), {company: admin_of()})
    first_id = UUID(sign_in(hosted, "first").json()["person_id"])
    second = host.signs_in("second", email_for("staff"), {company: staff_of()})
    second_id = UUID(sign_in(hosted, "second").json()["person_id"])
    assert roles(conn, company, second_id) == set()

    # The host makes the second an admin: their next sign-in gives it.
    host.people["second"] = dataclasses.replace(second, staff_of={company: admin_of()})
    assert "admin" in sign_in(hosted, "second").json()["roles"]

    # The host takes it from the first: theirs is taken, another admin standing.
    host.people["first"] = dataclasses.replace(first, staff_of={company: staff_of()})
    assert sign_in(hosted, "first").json()["roles"] == []
    assert roles(conn, company, first_id) == set()

    # And from the second, the company's last: the books keep them its admin, and say why.
    host.people["second"] = dataclasses.replace(second, staff_of={company: staff_of()})
    with caplog.at_level(logging.WARNING, logger="pms.external"):
        kept = sign_in(hosted, "second")
    assert kept.status_code == 200
    assert "admin" in kept.json()["roles"]
    assert "they are its last" in caplog.text


# --- owners and tenants, through their invite -----------------------------------------------


def test_a_tenant_joins_through_their_invite_and_signs_in_again(
    hosted, host, client, conn, app_conn
):
    pmc = make_pmc(conn)
    tenant_id = tenant_of(conn, pmc)
    email = email_for("tenant")
    token = invite_token(app_conn, pmc.pmc_id, "tenant", tenant_id, email)
    host.signs_in("t", email, name="Tenant A")

    joined = sign_in(hosted, "t", invite=token)

    assert joined.status_code == 200, joined.text
    assert (joined.json()["kind"], joined.json()["person_id"]) == ("tenant", str(tenant_id))
    login_id = login_of(app_conn, "tenant", tenant_id)
    assert conn.execute(
        "SELECT accepted_at IS NOT NULL FROM trust_invites WHERE login_id = %s", (login_id,)
    ).fetchone()[0]
    # The invite is used up, and the tenant signs in again without it.
    assert sign_in(hosted, "t", invite=token).status_code == 404
    hosted.post("/api/sign-out", json={})
    again = sign_in(hosted, "t")
    assert again.status_code == 200
    assert again.json()["person_id"] == str(tenant_id)


def test_an_invite_is_used_only_at_its_own_email(hosted, host, conn, app_conn):
    pmc = make_pmc(conn)
    token = invite_token(app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, email_for("owner"))
    host.signs_in("t", email_for("someone-else"))

    assert sign_in(hosted, "t", invite=token).status_code == 401
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_external_identities WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone()[0]
        == 0
    )


def test_a_staff_invite_isnt_used_through_the_host(hosted, host, conn, app_conn):
    pmc = make_pmc(conn)
    staff_id = add_staff(conn, pmc.pmc_id, "Staff E")
    email = email_for("staff")
    token = invite_token(app_conn, pmc.pmc_id, "staff", staff_id, email)
    host.signs_in("t", email, {pmc.pmc_id: staff_of()})

    refused = sign_in(hosted, "t", invite=token)

    assert refused.status_code == 422
    assert "staff invite" in refused.json()["detail"]


def test_a_person_linked_to_one_login_isnt_linked_to_another(hosted, host, conn, app_conn):
    pmc = make_pmc(conn)
    email = email_for("owner")
    first = invite_token(app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, email)
    person = host.signs_in("t", email)
    assert sign_in(hosted, "t", invite=first).status_code == 200
    other = email_for("owner")
    second = invite_token(app_conn, pmc.pmc_id, "owner", pmc.owners[1].owner_id, other)
    host.people["t"] = dataclasses.replace(person, email=other)

    refused = sign_in(hosted, "t", invite=second)

    assert refused.status_code == 409
    assert "as someone else already" in refused.json()["detail"]


def test_a_new_invite_for_a_linked_login_is_used_by_the_same_person(hosted, host, conn, app_conn):
    pmc = make_pmc(conn)
    email = email_for("owner")
    owner_id = pmc.owners[0].owner_id
    host.signs_in("t", email)
    assert (
        sign_in(hosted, "t", invite=invite_token(app_conn, pmc.pmc_id, "owner", owner_id, email))
    ).status_code == 200

    again = sign_in(
        hosted, "t", invite=invite_token(app_conn, pmc.pmc_id, "owner", owner_id, email)
    )

    assert again.status_code == 200
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_external_identities WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone()[0]
        == 1
    )


# --- a server whose password sign-in is off -------------------------------------------------


def staff_signed_in(hosted, host, pmc) -> dict:
    """An admin of a company with books, signed in through the host."""
    host.signs_in("staff", email_for("staff"), {pmc.pmc_id: admin_of()})
    signed = sign_in(hosted, "staff")
    assert signed.status_code == 200, signed.text
    return signed.json()


def test_the_sign_in_page_is_told_password_sign_in_is_off(hosted, client):
    assert hosted.get("/api/sign-in").json()["password"] is False
    assert client.get("/api/sign-in").json()["password"] is True


@pytest.mark.parametrize(
    "path, body",
    [
        ("/api/sign-in", {"pmc_id": str(uuid.uuid4()), "email": "a@example.com", "password": "x"}),
        ("/api/password-reset", {"pmc_id": str(uuid.uuid4()), "email": "a@example.com"}),
        ("/api/invites/accept", {"token": "x" * 43, "password": "a long passphrase"}),
        ("/api/invites/confirm", {"token": "x" * 43, "code": "123456"}),
    ],
)
def test_nothing_sets_or_proves_a_password_when_password_sign_in_is_off(hosted, path, body):
    refused = hosted.post(path, json=body)
    assert refused.status_code == 403
    assert refused.json()["detail"] == (
        "this server signs people in through the app it is part of: sign in there"
    )


def test_resets_arent_offered_when_password_sign_in_is_off(settings, host):
    on = dataclasses.replace(settings, verifier=host, password_sign_in=False, email_sender="fake")
    with TestClient(create_app(on)) as browser:
        assert browser.get("/api/password-reset").json() == {"available": False}


@pytest.mark.parametrize("step", ["start", "confirm", "off"])
def test_an_owners_own_two_step_is_the_hosts_when_password_sign_in_is_off(
    hosted, host, conn, app_conn, step
):
    pmc = make_pmc(conn)
    email = email_for("owner")
    token = invite_token(app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, email)
    host.signs_in("owner", email)
    assert sign_in(hosted, "owner", invite=token).status_code == 200

    body = {} if step == "start" else {"code": "123456"}
    refused = hosted.post(f"/api/me/two-step/{step}", json=body)

    assert refused.status_code == 403


def test_staff_arent_invited_and_owners_invites_go_to_the_hosts_page(settings, host, conn):
    invited_at = "http://host.test/invited"
    on = dataclasses.replace(settings, verifier=host, password_sign_in=False, invite_url=invited_at)
    with TestClient(create_app(on)) as hosted:
        pmc = make_pmc(conn)
        staff_signed_in(hosted, host, pmc)
        staff_id = add_staff(conn, pmc.pmc_id, "Staff F")

        refused = hosted.post(
            "/api/invites",
            json={"kind": "staff", "person_id": str(staff_id), "email": email_for("staff")},
        )
        assert refused.status_code == 403
        assert "staff join through the app" in refused.json()["detail"]

        made = hosted.post(
            "/api/invites",
            json={
                "kind": "owner",
                "person_id": str(pmc.owners[0].owner_id),
                "email": email_for("owner"),
            },
        )
        assert made.status_code == 201, made.text
        assert made.json()["url"].startswith(f"{invited_at}#")


def test_invites_go_to_the_pms_own_page_without_an_invite_url(client, conn, app_conn, settings):
    pmc = make_pmc(conn)
    join_as_staff(client, conn, app_conn, pmc)
    made = client.post(
        "/api/invites",
        json={
            "kind": "owner",
            "person_id": str(pmc.owners[0].owner_id),
            "email": email_for("owner"),
        },
    )
    assert made.status_code == 201
    assert made.json()["url"].startswith(f"{settings.public_url}/invite#")


def test_a_login_the_host_signs_in_reads_as_active(hosted, host, conn, app_conn):
    pmc = make_pmc(conn)
    tenant_id = tenant_of(conn, pmc)
    tenant_email = email_for("tenant")
    tenant_invite = invite_token(app_conn, pmc.pmc_id, "tenant", tenant_id, tenant_email)
    owner_email = email_for("owner")
    owner_id = pmc.owners[0].owner_id
    owner_invite = invite_token(app_conn, pmc.pmc_id, "owner", owner_id, owner_email)
    host.signs_in("tenant", tenant_email, name="Tenant B")
    assert sign_in(hosted, "tenant", invite=tenant_invite).status_code == 200
    host.signs_in("owner", owner_email, name="Owner B")
    assert sign_in(hosted, "owner", invite=owner_invite).status_code == 200
    me = staff_signed_in(hosted, host, pmc)

    people = {p["person_id"]: p["status"] for p in hosted.get("/api/people").json()}
    assert people[me["person_id"]] == "active"
    assert people[str(tenant_id)] == "active"
    assert people[str(owner_id)] == "active"
    assert hosted.get(f"/api/tenants/{tenant_id}").json()["portal"] == "active"
    assert hosted.get(f"/api/owners/{owner_id}").json()["portal"] == "active"
    on_call = {o["staff_id"]: o["emailed"] for o in hosted.get("/api/maintenance/on-call").json()}
    assert on_call[me["person_id"]] is True


# --- settings -------------------------------------------------------------------------------


def test_password_sign_in_is_on_unless_set_off_and_never_a_typo(settings):
    base = {"DATABASE_URL": settings.database_url}
    assert Settings.from_env(base).password_sign_in is True
    assert Settings.from_env({**base, "PMS_PASSWORD_SIGN_IN": "OFF"}).password_sign_in is False
    assert Settings.from_env({**base, "PMS_PASSWORD_SIGN_IN": " on "}).password_sign_in is True
    with pytest.raises(ValueError, match="PMS_PASSWORD_SIGN_IN"):
        Settings.from_env({**base, "PMS_PASSWORD_SIGN_IN": "of"})
    assert Settings.from_env({**base, "PMS_INVITE_URL": " https://a.test/x "}).invite_url == (
        "https://a.test/x"
    )


@pytest.mark.parametrize(
    "url",
    [
        "ftp://host.test/invited",
        "https:///invited",
        "https://user:secret@host.test/invited",
        "https://host.test/invited?from=pms",
        "https://host.test/invited#here",
        "https://host.test/invited#",
    ],
)
def test_an_invite_url_a_browser_cant_take_a_token_at_is_refused(settings, url):
    with pytest.raises(deployment.PublicUrlRefused) as refused:
        create_app(dataclasses.replace(settings, invite_url=url))
    assert "secret" not in str(refused.value)


def test_an_invite_url_with_a_path_is_taken(settings):
    create_app(dataclasses.replace(settings, invite_url="https://host.test/invited"))
