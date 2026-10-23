"""Email from the API: where an email's link points, and delivery after a request commits
(src/pms/email.py, docs/adr/0004-email.md)."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from psycopg_pool import ConnectionPool

from pms import email

# The portal's address for the request being served: the app's middleware sets it from the
# server's PMS_PUBLIC_URL. Code that calls a route itself (pms.demo) gets the default.
PUBLIC_URL: ContextVar[str] = ContextVar("pms_public_url", default="http://localhost:8000")
# Whether the server sends email (PMS_EMAIL_SENDER is set): the emails the PMS writes by itself
# (src/pms/notices.py) are written only while it does. Code that calls a route itself sends none.
SENDING: ContextVar[bool] = ContextVar("pms_sending", default=False)
# The server's payments partner's name (PMS_PAYMENTS), for what starts autopay payments (an
# admin's Run rent now); None for none. Code that calls a route itself starts none.
PARTNER: ContextVar[str | None] = ContextVar("pms_payments_partner", default=None)


def deliver(pool: ConnectionPool[Any], sender: email.Sender) -> None:
    """Delivers what waits in the outbox, each email in a transaction of its own."""
    with pool.connection() as conn:
        conn.autocommit = True
        try:
            email.deliver_pending(conn, sender)
        finally:
            conn.autocommit = False
