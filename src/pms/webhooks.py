"""Webhooks (ADR 0011's third piece, migration 20261105000500): each event a company's database
writes (trust_events) goes to each of its webhooks, an https address an admin added in
Settings, AI, signed with that webhook's secret.

What goes: the event's id, kind, time, company and the ids it is about, as JSON, nothing more;
the receiver reads the rest through the API, as its key allows. Each request carries
`Occupella-Event-Id` (the same on a retry, so a repeat is harmless) and
`Occupella-Signature: t=<unix seconds>,v1=<hex>`, HMAC-SHA256 under the webhook's secret of
`<t>.` and the body. A delivery is a 2xx answer; anything else is tried again after a wait
(BACKOFF), MAX_ATTEMPTS times in all, and an event older than KEPT is no longer tried.

The address is checked each time, not only when it is added (DNS can change): https only, no
credentials in it, and every address its host resolves to public, never the server's own
network, loopback, link-local (a cloud's metadata service) or any other internal range. The
request goes to the address that was checked, so a second lookup can't lead elsewhere, the
certificate checked against the host's name; no redirect is followed and the answer's body is
not kept. Secrets are sealed under PMS_DATA_KEY (src/pms/sealed.py): without it the server
keeps no webhook and sends none.

Deliveries run in the API's process every INTERVAL seconds (Courier, unless PMS_WEBHOOKS is
"off"), or by `python -m pms.webhooks deliver`; two processes never send one event to one
webhook at once (an advisory lock).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import os
import socket
import ssl
import sys
import threading
import time
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from psycopg import sql

from pms import sealed

log = logging.getLogger("pms.webhooks")

MAX_ATTEMPTS = 6
# The wait before each attempt after the first, by how many were made.
BACKOFF = [
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=12),
]
KEPT = timedelta(days=7)
INTERVAL = 30.0
TIMEOUT = 10.0
USER_AGENT = "Occupella-PMS-Webhooks/1"
KEEPS = "webhook secrets"
NAMES_NOT_OUT = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


class Refused(Exception):
    """An address a webhook may not go to, in words."""


Resolver = Callable[..., Sequence[tuple[Any, ...]]]
Sender = Callable[[str, bytes, dict[str, str]], int]


def parts_of(url: str) -> tuple[str, int, str]:
    """The host, port and path of a webhook's address, or Refused saying why it is none."""
    if len(url) > 500:
        raise Refused("a webhook's address is at most 500 characters")
    found = urlsplit(url.strip())
    if found.scheme != "https":
        raise Refused("a webhook's address starts with https://")
    if found.username is not None or found.password is not None:
        raise Refused("a webhook's address carries no user name or password")
    if found.fragment:
        raise Refused("a webhook's address has no #fragment")
    host = (found.hostname or "").rstrip(".").lower()
    if not host:
        raise Refused("a webhook's address names a host")
    try:
        port = found.port or 443
    except ValueError:
        raise Refused("a webhook's address has a port from 1 to 65535") from None
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        inside = not public(literal)
    else:
        inside = host == "localhost" or host.endswith(NAMES_NOT_OUT) or "." not in host
    if inside:
        raise Refused("a webhook goes to a public address, never the server's own network")
    path = found.path or "/"
    return host, port, path + (f"?{found.query}" if found.query else "")


def public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether an address is on the public internet: none of the private, shared, loopback,
    link-local, multicast, reserved or unspecified ranges, nor an IPv4 one of those inside IPv6."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global and not (
        address.is_multicast
        or address.is_reserved
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_private
    )


def addresses_of(host: str, port: int, resolve: Resolver = socket.getaddrinfo) -> list[str]:
    """Every address the host resolves to, all public, or Refused."""
    try:
        found = resolve(host.strip("[]"), port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as missing:
        raise Refused(f"the webhook's host doesn't resolve: {missing}") from None
    addresses = sorted({str(entry[4][0]) for entry in found})
    if not addresses:
        raise Refused("the webhook's host doesn't resolve")
    if not all(public(ipaddress.ip_address(a.split("%", 1)[0])) for a in addresses):
        raise Refused("the webhook's host leads to an internal address: nothing was sent")
    return addresses


class _Pinned(http.client.HTTPSConnection):
    """HTTPS to the address that was checked, the certificate checked against the host's name."""

    def __init__(self, host: str, address: str, port: int, context: ssl.SSLContext) -> None:
        super().__init__(host, port, timeout=TIMEOUT, context=context)
        self._address = address
        self._context_used = context

    def connect(self) -> None:
        raw = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context_used.wrap_socket(raw, server_hostname=self.host)


def post(
    url: str, body: bytes, headers: dict[str, str], resolve: Resolver = socket.getaddrinfo
) -> int:
    """Posts the body to a checked public address; the receiver's status."""
    host, port, path = parts_of(url)
    address = addresses_of(host, port, resolve)[0]
    connection = _Pinned(host, address, port, ssl.create_default_context())
    try:
        connection.request("POST", path, body=body, headers=headers)
        answer = connection.getresponse()
        answer.read(65536)  # not kept
        return int(answer.status)
    finally:
        connection.close()


def signature(secret: str, at: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{at}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={at},v1={mac}"


def body_of(event_id: int, kind: str, at: Any, pmc_id: UUID, subject: dict[str, Any]) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "kind": kind,
            "at": at.isoformat(),
            "company": str(pmc_id),
            "subject": subject,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


# Each webhook standing, of a company that is active, with each event of its company since it
# was added and no older than KEPT, written by a transaction every reader has seen finish, not
# delivered to it yet, tried fewer than MAX_ATTEMPTS times and past its wait.
PENDING = """
SELECT s.id, s.pmc_id, s.url, s.secret_sealed, e.id, e.kind, e.subject, e.created_at
FROM trust_event_subscriptions AS s
JOIN trust_events AS e ON e.pmc_id = s.pmc_id AND e.created_at >= s.created_at
LEFT JOIN trust_event_deliveries AS d ON d.subscription_id = s.id AND d.event_id = e.id
WHERE NOT EXISTS (
        SELECT 1 FROM trust_event_subscription_revocations AS r WHERE r.subscription_id = s.id)
  AND trust_pmc_status(s.pmc_id) = 'active'
  AND e.created_at > now() - %(kept)s::interval
  AND e.xact < pg_snapshot_xmin(pg_current_snapshot())
GROUP BY s.id, e.id
HAVING NOT coalesce(bool_or(d.status BETWEEN 200 AND 299), false)
   AND count(d.id) < %(attempts)s
   AND (count(d.id) = 0
        OR max(d.attempted_at) + (%(backoff)s::interval[])[count(d.id)] <= now())
ORDER BY s.id, e.xact, e.id
LIMIT %(limit)s
"""


def deliver_pending(
    conn: psycopg.Connection[Any],
    key: bytes,
    send: Sender | None = None,
    clock: Callable[[], float] = time.time,
    limit: int = 200,
) -> int:
    """Sends what waits, each attempt recorded; how many were delivered. `conn` is autocommit."""
    send = send or post
    rows = conn.execute(
        PENDING,
        {
            "kept": KEPT,
            "attempts": MAX_ATTEMPTS,
            "backoff": BACKOFF,
            "limit": limit,
        },
    ).fetchall()
    delivered = 0
    for sub, pmc, url, secret_sealed, event, kind, subject, at in rows:
        with conn.transaction():
            locked = conn.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"trust_event_delivery:{sub}:{event}",),
            ).fetchone()
            done = conn.execute(
                "SELECT 1 FROM trust_event_deliveries WHERE subscription_id = %s AND event_id = %s"
                " AND status BETWEEN 200 AND 299",
                (sub, event),
            ).fetchone()
            if not (locked and locked[0]) or done:
                continue  # another process has it, or had it
            status: int | None = None
            error: str | None = None
            try:
                secret = sealed.unseal(bytes(secret_sealed), str(sub), key)
                body = body_of(event, kind, at, pmc, subject)
                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                    "Occupella-Event-Id": str(event),
                    "Occupella-Signature": signature(secret, int(clock()), body),
                }
                status = send(url, body, headers)
                if not 100 <= status <= 599:
                    error, status = f"the receiver answered {status}", None
                elif not 200 <= status <= 299:
                    error = f"the receiver answered {status}"
            except (Refused, sealed.Unreadable) as refused:
                error = str(refused)
            except (OSError, http.client.HTTPException) as failed:
                error = f"{type(failed).__name__}: {failed}"
            except Exception as strange:  # noqa: BLE001 - recorded, never left in the way
                log.exception("webhook %s, event %s: could not be sent", sub, event)
                error = f"{type(strange).__name__}: {strange}"
            conn.execute(
                "INSERT INTO trust_event_deliveries"
                " (pmc_id, subscription_id, event_id, status, error) VALUES (%s, %s, %s, %s, %s)",
                (pmc, sub, event, status, None if error is None else error[:1000]),
            )
            if error is None:
                delivered += 1
    return delivered


class Courier:
    """Delivers what waits every `interval` seconds, in a thread of its own with a connection of
    its own, until stopped; a look that fails is tried at the next."""

    def __init__(self, database_url: str, role: str, interval: float = INTERVAL) -> None:
        self.database_url = database_url
        self.role = role
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="pms-webhooks", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def once(self) -> int:
        try:
            key = sealed.key_from(os.environ, KEEPS)
        except sealed.NoKey:
            return 0  # no secret can be read, so no webhook is kept or sent
        with psycopg.connect(self.database_url, autocommit=True) as conn:
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(self.role)))
            return deliver_pending(conn, key)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.once()
            except Exception:  # noqa: BLE001 - the next look tries again
                log.exception("webhooks: delivering what waits failed")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pms.webhooks")
    parser.add_argument("command", choices=["deliver"])
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        key = sealed.key_from(os.environ, KEEPS)
    except sealed.NoKey as missing:
        print(missing, file=sys.stderr)
        return 1
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        role = os.environ.get("PMS_DB_ROLE", "trust_app")
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        print(f"delivered {deliver_pending(conn, key)} event(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
