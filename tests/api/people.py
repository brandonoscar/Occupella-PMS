"""People who can sign in, for the API tests: invited, signed up and signed in on a client."""

import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pyotp
from helpers import add_staff

from pms import accounts

PASSWORD = "correct horse battery staple"


@dataclass
class Person:
    kind: str
    person_id: UUID
    email: str
    login_id: UUID | None = None
    totp: pyotp.TOTP | None = None


def invite_token(app_conn, pmc_id, kind, person_id, email, created_by=None) -> str:
    return accounts.create_invite(app_conn, pmc_id, kind, person_id, email, created_by).token


def login_of(app_conn, kind, person_id) -> UUID:
    return app_conn.execute(
        f"SELECT id FROM trust_logins WHERE {kind}_id = %s", (person_id,)
    ).fetchone()[0]


def join_as_owner(client, app_conn, pmc, n=0) -> Person:
    """Owner n of the PMC, invited and signed in on `client`."""
    owner_id = pmc.owners[n].owner_id
    email = f"owner{n}.{owner_id.hex[:8]}@example.com"
    token = invite_token(app_conn, pmc.pmc_id, "owner", owner_id, email)
    accepted = client.post("/api/invites/accept", json={"token": token, "password": PASSWORD})
    assert accepted.status_code == 200, accepted.text
    return Person("owner", owner_id, email, login_of(app_conn, "owner", owner_id))


def tenant_of(conn, pmc) -> UUID:
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]


def join_as_tenant(client, conn, app_conn, pmc) -> Person:
    tenant_id = tenant_of(conn, pmc)
    email = f"tenant.{tenant_id.hex[:8]}@example.com"
    token = invite_token(app_conn, pmc.pmc_id, "tenant", tenant_id, email)
    accepted = client.post("/api/invites/accept", json={"token": token, "password": PASSWORD})
    assert accepted.status_code == 200, accepted.text
    return Person("tenant", tenant_id, email, login_of(app_conn, "tenant", tenant_id))


def totp_from(uri: str) -> pyotp.TOTP:
    return pyotp.TOTP(parse_qs(urlsplit(uri).query)["secret"][0])


def join_as_staff(client, conn, app_conn, pmc, name="Staff 1") -> Person:
    """A staff member who accepted an invite, enrolled an authenticator and is signed in."""
    staff_id = add_staff(conn, pmc.pmc_id, name)
    email = f"staff.{staff_id.hex[:8]}@example.com"
    token = invite_token(app_conn, pmc.pmc_id, "staff", staff_id, email)
    enrolled = client.post("/api/invites/accept", json={"token": token, "password": PASSWORD})
    assert enrolled.status_code == 200, enrolled.text
    totp = totp_from(enrolled.json()["totp_uri"])
    confirmed = client.post("/api/invites/confirm", json={"token": token, "code": totp.now()})
    assert confirmed.status_code == 200, confirmed.text
    return Person("staff", staff_id, email, login_of(app_conn, "staff", staff_id), totp)


def next_code(person: Person) -> str:
    """A code for the next time step: the current one was used to sign in already."""
    return person.totp.at(int(time.time()) + person.totp.interval)
