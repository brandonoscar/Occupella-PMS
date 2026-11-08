"""An app that mounts the PMS sends owners' and tenants' invites itself (pms.external.Inviter,
Settings.inviter; docs/adr/0013-sign-in-through-a-host.md): each one is handed to it once the
invite's transaction has committed, instead of written to the PMS's outbox, and the answer says
who sends it. A host that fails leaves the invite standing, and staff are shown its link to give.

The host here is a stand-in (Host, Sent): its verifier knows the tokens it was told of, and its
inviter keeps what it was handed. Every name and address is synthetic.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from helpers import add_staff, make_pmc
from people import join_as_staff, tenant_of

from pms import accounts, email, external
from pms.api import create_app

INVITED_AT = "http://host.test/invited"


@dataclass
class Host:
    """A host app's verifier: each token names a person it signed in."""

    issuer: str = "occupella"
    people: dict[str, external.Person] = field(default_factory=dict)

    def verify(self, token: str) -> external.Person | None:
        return self.people.get(token)

    def signs_in(self, token: str, address: str, staff_of=None) -> None:
        self.people[token] = external.Person(
            subject=f"user-{uuid.uuid4().hex}",
            email=address,
            name="Staff A",
            staff_of=staff_of or {},
            second_step=True,
        )


@dataclass
class Sent:
    """A host app's inviter: keeps each invite it is handed, and whether the PMS had committed it
    by then (looked up on a connection of its own, as the host's own request would find it)."""

    database_url: str
    fails: bool = False
    handed: list[dict] = field(default_factory=list)

    def invite(
        self, *, pmc_id: UUID, kind: str, email: str, token: str, expires_at: datetime
    ) -> None:
        with psycopg.connect(self.database_url, autocommit=True) as other:
            committed = other.execute(
                "SELECT count(*) FROM trust_invites WHERE token_hash = %s",
                (accounts.digest(token),),
            ).fetchone()[0]
        self.handed.append(
            {
                "pmc_id": pmc_id,
                "kind": kind,
                "email": email,
                "token": token,
                "expires_at": expires_at,
                "committed": committed == 1,
            }
        )
        if self.fails:
            raise RuntimeError(f"the host's email couldn't send {token} to {email}")


@pytest.fixture
def host():
    return Host()


@pytest.fixture
def sent(database_url):
    return Sent(database_url)


def server(settings, host, sent, **more):
    return TestClient(
        create_app(
            dataclasses.replace(
                settings,
                verifier=host,
                inviter=sent,
                password_sign_in=False,
                invite_url=INVITED_AT,
                **more,
            )
        )
    )


@pytest.fixture
def hosted(settings, host, sent):
    """A server a host app signs people in to and sends owners' and tenants' invites for, which
    sends email of its own too: what the host sends never reaches its outbox."""
    with server(settings, host, sent) as test_client:
        test_client.app.state.email = email.FakeSender()
        yield test_client


def address(word: str) -> str:
    return f"{word}.{uuid.uuid4().hex[:8]}@example.com"


def staff_signed_in(client, host, pmc) -> None:
    host.signs_in("staff", address("staff"), {pmc.pmc_id: external.Membership(True, "Company A")})
    signed = client.post("/api/external-sign-in", json={"token": "staff"})
    assert signed.status_code == 200, signed.text


def invite(client, kind: str, person_id: UUID, to: str):
    return client.post(
        "/api/invites", json={"kind": kind, "person_id": str(person_id), "email": to}
    )


def invite_emails(conn, pmc) -> int:
    return conn.execute(
        "SELECT count(*) FROM trust_emails WHERE pmc_id = %s AND kind = 'invite'", (pmc.pmc_id,)
    ).fetchone()[0]


def invites_of(conn, pmc) -> int:
    return conn.execute(
        "SELECT count(*) FROM trust_invites WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]


@pytest.mark.parametrize("kind", ["owner", "tenant"])
def test_an_invite_goes_to_the_host_once_committed_and_never_to_the_outbox(
    hosted, host, sent, conn, kind
):
    pmc = make_pmc(conn)
    staff_signed_in(hosted, host, pmc)
    person_id = pmc.owners[0].owner_id if kind == "owner" else tenant_of(conn, pmc)
    to = address(kind)

    made = invite(hosted, kind, person_id, to.upper())

    assert made.status_code == 201, made.text
    answer = made.json()
    assert answer["sent_by"] == "host"
    assert answer["emailed"] is True
    (handed,) = sent.handed
    assert handed == {
        "pmc_id": pmc.pmc_id,
        "kind": kind,
        "email": to,
        "token": handed["token"],
        "expires_at": datetime.fromisoformat(answer["expires_at"]),
        "committed": True,
    }
    # The link staff see once is the host's page with the same invite in its fragment.
    assert answer["url"] == f"{INVITED_AT}#{handed['token']}"
    assert invite_emails(conn, pmc) == 0


def test_the_invite_the_host_sent_is_how_the_person_joins(hosted, host, sent, conn):
    pmc = make_pmc(conn)
    staff_signed_in(hosted, host, pmc)
    tenant_id = tenant_of(conn, pmc)
    to = address("tenant")
    assert invite(hosted, "tenant", tenant_id, to).status_code == 201

    host.signs_in("tenant", to)
    joined = hosted.post(
        "/api/external-sign-in", json={"token": "tenant", "invite": sent.handed[0]["token"]}
    )

    assert joined.status_code == 200, joined.text
    assert joined.json()["kind"] == "tenant"
    assert joined.json()["person_id"] == str(tenant_id)


def test_an_invite_refused_is_never_handed_to_the_host(hosted, host, sent, conn):
    pmc = make_pmc(conn)
    staff_signed_in(hosted, host, pmc)
    taken = address("owner")
    assert invite(hosted, "owner", pmc.owners[0].owner_id, taken).status_code == 201

    clash = invite(hosted, "owner", pmc.owners[1].owner_id, taken)

    assert clash.status_code == 409
    assert len(sent.handed) == 1


def test_an_invite_rolled_back_is_never_handed_to_the_host(hosted, host, sent, conn, monkeypatch):
    pmc = make_pmc(conn)
    staff_signed_in(hosted, host, pmc)
    made = accounts.create_invite

    def then_refused(*args, **kwargs):
        made(*args, **kwargs)  # the invite is written in the request's transaction...
        raise accounts.Conflict("refused after the invite was written")  # ...and rolled back

    monkeypatch.setattr(accounts, "create_invite", then_refused)
    refused = invite(hosted, "owner", pmc.owners[0].owner_id, address("owner"))

    assert refused.status_code == 409
    assert sent.handed == []
    assert invites_of(conn, pmc) == 0


def test_a_host_that_fails_leaves_the_invite_standing_and_its_link_shown(
    settings, host, sent, conn, caplog
):
    sent.fails = True
    pmc = make_pmc(conn)
    owner_id = pmc.owners[0].owner_id
    to = address("owner")
    with server(settings, host, sent) as hosted, caplog.at_level(logging.DEBUG):
        staff_signed_in(hosted, host, pmc)

        made = invite(hosted, "owner", owner_id, to)

        assert made.status_code == 201, made.text
        answer = made.json()
        # As when no email is sent: the link alone, for staff to give.
        assert answer["sent_by"] is None
        assert answer["emailed"] is False
        token = sent.handed[0]["token"]
        assert answer["url"] == f"{INVITED_AT}#{token}"
        assert invite_emails(conn, pmc) == 0

        host.signs_in("owner", to)
        joined = hosted.post("/api/external-sign-in", json={"token": "owner", "invite": token})
        assert joined.status_code == 200, joined.text
        assert joined.json()["person_id"] == str(owner_id)

    said = [record.getMessage() for record in caplog.records]
    assert "the host's inviter failed on an invite for the owner: RuntimeError" in said
    assert not [line for line in said if token in line or to in line]


def test_staff_invites_are_never_the_hosts(settings, sent, conn, app_conn):
    """A server whose password sign-in is on invites staff by its own email, inviter or not;
    with it off it refuses them (ADR 0013), and the host is handed nothing either way."""
    pmc = make_pmc(conn)
    on = dataclasses.replace(settings, inviter=sent)
    with TestClient(create_app(on)) as client:
        client.app.state.email = email.FakeSender()
        join_as_staff(client, conn, app_conn, pmc)
        made = invite(client, "staff", add_staff(conn, pmc.pmc_id, "Staff B"), address("staff"))
        assert made.status_code == 201, made.text
        assert made.json()["sent_by"] == "pms"
        assert invite_emails(conn, pmc) == 1
    assert sent.handed == []


@pytest.mark.parametrize("sends_email", [False, True])
def test_without_an_inviter_invites_are_as_they_were(client, conn, app_conn, sends_email):
    pmc = make_pmc(conn)
    join_as_staff(client, conn, app_conn, pmc)
    if sends_email:
        client.app.state.email = email.FakeSender()

    made = invite(client, "owner", pmc.owners[0].owner_id, address("owner"))

    assert made.status_code == 201, made.text
    assert made.json()["sent_by"] == ("pms" if sends_email else None)
    assert made.json()["emailed"] is sends_email
    assert invite_emails(conn, pmc) == int(sends_email)


def test_me_says_whether_owners_and_tenants_invites_are_emailed(
    settings, client, host, sent, conn, app_conn
):
    pmc = make_pmc(conn)
    join_as_staff(client, conn, app_conn, pmc)
    assert client.get("/api/me").json()["invites_on"] is False
    client.app.state.email = email.FakeSender()
    assert client.get("/api/me").json()["invites_on"] is True

    with server(settings, host, sent) as hosted:  # this server sends no email; its host does
        other = make_pmc(conn)
        staff_signed_in(hosted, host, other)
        me = hosted.get("/api/me").json()
        assert me["email_on"] is False
        assert me["invites_on"] is True


def test_an_invite_needs_a_staff_session_whoever_sends_it(hosted, sent, conn):
    pmc = make_pmc(conn)
    refused = invite(hosted, "owner", pmc.owners[0].owner_id, address("owner"))
    assert refused.status_code == 401
    assert sent.handed == []
