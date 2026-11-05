"""A company's own Stripe keys (migration 20261104000800; the founder's review of 11/03, item 4,
and his call of 11/04: in Settings, the server's keys from the environment still working).

An admin enters the company's secret key and its webhook's signing secret in Company setup. The
app checks they look like Stripe's, seals each under the server's key (src/pms/sealed.py) and keeps
only the secret key's last four and whether it's a test or a live key, to show. Neither key ever
comes back: not in an answer, a refusal, a log or the audit log. With them, the company's tenants
and owners pay through its own Stripe account, and its Stripe webhook reports to an address of its
own (/api/payments/webhook/stripe/<company>). Taken away, the company goes back to the server's
partner (PMS_PAYMENTS), if any. Payments started under keys since changed still report in (the
webhook takes every secret the company has used); a tenant's saved autopay bank account stays with
the Stripe account it was saved at, so after a change the tenant sets autopay up again.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from pms import payments, sealed
from pms.api import outbox
from pms.api.deps import Conn, Staff
from pms.api.online_payments import resolver_of
from pms.api.staff import roles_of

router = APIRouter(prefix="/api/payments/keys")

# Longer than any key Stripe writes; a body past it is refused before anything reads it.
LONGEST = 400
NO_DATA_KEY = "This server can't keep keys. Ask whoever runs it to set PMS_DATA_KEY."


class KeysView(BaseModel):
    # Whose account the company's payments go through now: its own keys, the server's partner,
    # or none (no payment online). "unreadable": it has keys of its own that don't open here.
    using: Literal["own", "server", "none", "unreadable"]
    # The server's partner, when the company has no keys of its own: "stripe" or "fake".
    server_partner: str | None
    mode: Literal["test", "live"] | None
    # The secret key's last four characters: all that is ever shown of it.
    ends_in: str | None
    # Where the company's own Stripe webhook sends its reports, and the events it must send.
    webhook_url: str
    webhook_events: list[str]
    changed_at: datetime | None
    changed_by: str | None
    # Whether this server can keep keys at all (it has a PMS_DATA_KEY), and whether the staff
    # member asking may change them (an admin).
    can_keep: bool
    may_change: bool


class NewKeys(BaseModel):
    # Plain strings, checked by hand: a refusal never echoes them back.
    secret_key: str
    webhook_secret: str


def latest(conn: psycopg.Connection[Any], pmc_id: UUID) -> Any:
    return conn.execute(
        "SELECT k.secret_key_sealed IS NOT NULL, k.mode, k.secret_key_last4, k.created_at,"
        " s.display_name FROM trust_payment_keys AS k"
        " JOIN trust_staff AS s ON s.id = k.recorded_by"
        " WHERE k.pmc_id = %s ORDER BY k.created_at DESC, k.id DESC LIMIT 1",
        (pmc_id,),
    ).fetchone()


def is_admin(conn: psycopg.Connection[Any], session: Any) -> bool:
    return "admin" in roles_of(conn, session)


def view(conn: psycopg.Connection[Any], request: Request, session: Any) -> KeysView:
    resolver = resolver_of(request)
    row = latest(conn, session.pmc_id)
    own = resolver.own(conn, session.pmc_id)
    server = resolver.server_for(conn)
    using: Literal["own", "server", "none", "unreadable"] = (
        "unreadable"
        if own is False
        else "own"
        if own is not None
        else "server"
        if server is not None
        else "none"
    )
    has = row is not None and row[0]
    try:
        sealed.key_from(os.environ, "payments keys")
        can_keep = True
    except sealed.NoKey:
        can_keep = False
    return KeysView(
        using=using,
        server_partner=None if server is None else server.name,
        mode=row[1] if has else None,
        ends_in=row[2] if has else None,
        webhook_url=f"{outbox.PUBLIC_URL.get()}/api/payments/webhook/stripe/{session.pmc_id}",
        webhook_events=list(payments.STRIPE_EVENTS),
        changed_at=None if row is None else row[3],
        changed_by=None if row is None else row[4],
        can_keep=can_keep,
        may_change=is_admin(conn, session),
    )


def refuse_unless_admin(conn: psycopg.Connection[Any], session: Any) -> None:
    if not is_admin(conn, session):
        raise HTTPException(403, "Only an admin can change the payments keys.")


@router.get("")
def get_keys(session: Staff, conn: Conn, request: Request) -> KeysView:
    return view(conn, request, session)


@router.post("")
def save_keys(session: Staff, conn: Conn, request: Request, new: NewKeys) -> KeysView:
    """Keeps the company's keys, sealed; the company's payments go through them from now on."""
    refuse_unless_admin(conn, session)
    secret_key, webhook_secret = new.secret_key.strip(), new.webhook_secret.strip()
    if len(secret_key) > LONGEST or len(webhook_secret) > LONGEST:
        raise HTTPException(422, "That's longer than any Stripe key.")
    try:
        mode, last4 = payments.check_keys(secret_key, webhook_secret)
    except payments.BadKeys as bad:
        raise HTTPException(422, str(bad)) from None
    try:
        key = sealed.key_from(os.environ, "payments keys")
    except sealed.NoKey:
        raise HTTPException(409, NO_DATA_KEY) from None
    made = conn.execute("SELECT gen_random_uuid()").fetchone()
    assert made is not None
    row: UUID = made[0]
    sealed_secret, sealed_webhook = payments.seal_keys(row, secret_key, webhook_secret, key)
    conn.execute(
        "INSERT INTO trust_payment_keys (id, pmc_id, secret_key_sealed, webhook_secret_sealed,"
        " secret_key_last4, mode) VALUES (%s, %s, %s, %s, %s, %s)",
        (row, session.pmc_id, sealed_secret, sealed_webhook, last4, mode),
    )
    return view(conn, request, session)


@router.post("/remove", status_code=204)
def remove_keys(session: Staff, conn: Conn) -> Response:
    """Takes the company's keys away: its payments go through the server's partner, if any."""
    refuse_unless_admin(conn, session)
    row = latest(conn, session.pmc_id)
    if row is not None and row[0]:
        conn.execute(
            "INSERT INTO trust_payment_keys (pmc_id) VALUES (%s)", (session.pmc_id,)
        )  # no keys: taken away
    return Response(status_code=204)
