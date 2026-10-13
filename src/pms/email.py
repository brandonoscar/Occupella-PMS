"""Email the PMS sends, behind one interface (docs/adr/0004-email.md).

One email so far: when a bill over an owner's approval limit is entered, its owner is told it
waits for them, with a link to sign in to the portal and approve or decline it. Nothing in the
email decides anything.

Each email is written to trust_emails in the transaction that causes it, so it exists only if
its cause does. After that commits, deliver_pending() hands each email not yet delivered to the
server's sender and records every attempt (trust_email_deliveries): delivered, or why not. One
that failed is tried again on a later delivery, at most MAX_ATTEMPTS times in all.

The server's PMS_EMAIL_SENDER picks the sender: "fake" keeps each email in the outbox and sends
it nowhere (development and CI); "smtp" sends through the PMC's own mail server, always with
STARTTLS, from PMS_SMTP_HOST, PMS_SMTP_PORT (587 by default), PMS_SMTP_USERNAME,
PMS_SMTP_PASSWORD and PMS_EMAIL_FROM; unset sends nothing, and emails wait in the outbox.

usage: python -m pms.email deliver    (DATABASE_URL, acting as trust_app)
"""

from __future__ import annotations

import os
import smtplib
import ssl
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from email.message import EmailMessage
from typing import Any, Literal, Protocol
from uuid import UUID

import psycopg
from psycopg import sql

MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str


class Sender(Protocol):
    @property
    def name(self) -> str: ...

    def send(self, message: Message) -> None: ...


class FakeSender:
    """Keeps each email in the outbox and sends it nowhere: for development and tests."""

    name = "fake"

    def send(self, message: Message) -> None:
        return None


@dataclass(frozen=True)
class SmtpSender:
    """Sends through the PMC's own mail server, always over STARTTLS."""

    host: str
    port: int
    username: str
    password: str
    from_address: str
    name: str = "smtp"

    def send(self, message: Message) -> None:
        email = EmailMessage()
        email["From"] = self.from_address
        email["To"] = message.to
        email["Subject"] = message.subject
        email.set_content(message.body)
        with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(email)


def sender_named(name: str, env: Mapping[str, str]) -> Sender | None:
    """The sender PMS_EMAIL_SENDER names, set up from the server's environment; None for none."""
    if name == "":
        return None
    if name == "fake":
        return FakeSender()
    if name == "smtp":
        missing = [key for key in ("PMS_SMTP_HOST", "PMS_EMAIL_FROM") if not env.get(key)]
        if missing:
            raise ValueError(f"PMS_EMAIL_SENDER=smtp needs {' and '.join(missing)}")
        return SmtpSender(
            host=env["PMS_SMTP_HOST"],
            port=int(env.get("PMS_SMTP_PORT") or 587),
            username=env.get("PMS_SMTP_USERNAME", ""),
            password=env.get("PMS_SMTP_PASSWORD", ""),
            from_address=env["PMS_EMAIL_FROM"],
        )
    raise ValueError(f"PMS_EMAIL_SENDER is 'fake', 'smtp' or unset, not {name!r}")


def dollars(amount: Decimal) -> str:
    return f"${amount:,.2f}"


# --- a bill waiting for its owner ------------------------------------------------------------

Written = Literal["written", "within the limit", "no active login", "written already"]

BILL = """
SELECT b.amount, g.approval_limit, v.display_name, p.display_name, b.memo, b.reference,
       b.bill_date, b.due_on, o.display_name, m.display_name,
       (SELECT l.email FROM trust_logins AS l
        JOIN trust_login_secrets AS s ON s.login_id = l.id
        WHERE l.pmc_id = b.pmc_id AND l.kind = 'owner' AND l.owner_id = o.id
          AND l.disabled_at IS NULL)
FROM trust_bills AS b
JOIN trust_ledger_accounts AS t ON t.ledger_account_id = b.ledger_account_id
JOIN trust_owners AS o ON o.id = t.owner_id
JOIN trust_vendors AS v ON v.id = b.vendor_id
JOIN trust_properties AS p ON p.id = t.property_id
JOIN trust_pmcs AS m ON m.pmc_id = b.pmc_id
-- The limit of the agreement in force on the bill's date, 0 without one, as
-- trust_set_aside_bill reads it.
CROSS JOIN LATERAL (
    SELECT coalesce((SELECT a.approval_limit FROM trust_management_agreements AS a
                     WHERE a.ledger_account_id = b.ledger_account_id
                       AND a.starts_on <= b.bill_date
                     ORDER BY a.starts_on DESC LIMIT 1), 0) AS approval_limit
) AS g
WHERE b.pmc_id = %s AND b.id = %s
"""


def write_bill_approval(
    conn: psycopg.Connection[Any], pmc_id: UUID, bill_id: UUID, public_url: str
) -> Written:
    """Writes the email asking a bill's owner to approve or decline it, when it is over their
    limit and they can sign in to the portal; in the caller's transaction, once per bill."""
    row = conn.execute(BILL, (pmc_id, bill_id)).fetchone()
    if row is None:
        raise LookupError(f"no bill {bill_id} in PMC {pmc_id}")
    amount, limit, vendor, prop, memo, reference, billed, due, owner, pmc, to = row
    if amount <= limit:
        return "within the limit"
    if to is None:
        return "no active login"
    body = (
        f"Hello {owner},\n\n"
        f"{vendor} billed {dollars(amount)} for {prop}: {memo}. Reference {reference}, "
        f"dated {billed}, due {due}.\n\n"
        f"It is over your approval limit of {dollars(limit)}, so it waits for you. Sign in to "
        f"approve or decline it:\n{public_url.rstrip('/')}/\n\n"
        f"{pmc}\n"
    )
    written = conn.execute(
        "INSERT INTO trust_emails (pmc_id, kind, bill_id, to_address, subject, body)"
        " VALUES (%s, 'bill_approval', %s, %s, %s, %s)"
        " ON CONFLICT (bill_id) WHERE kind = 'bill_approval' DO NOTHING RETURNING id",
        (
            pmc_id,
            bill_id,
            to,
            f"A bill waits for your approval: {vendor}, {dollars(amount)}",
            body,
        ),
    ).fetchone()
    return "written" if written else "written already"


# --- delivery ----------------------------------------------------------------------------------

PENDING = """
SELECT e.id, e.pmc_id, e.to_address, e.subject, e.body
FROM trust_emails AS e
WHERE NOT EXISTS (SELECT 1 FROM trust_email_deliveries AS d
                  WHERE d.email_id = e.id AND d.error IS NULL)
  AND (SELECT count(*) FROM trust_email_deliveries AS d WHERE d.email_id = e.id) < %s
ORDER BY e.created_at, e.id
LIMIT %s
"""


@dataclass(frozen=True)
class Delivered:
    sent: int
    failed: int


def deliver_pending(conn: psycopg.Connection[Any], sender: Sender, limit: int = 100) -> Delivered:
    """Hands each email not yet delivered to the sender, oldest first, each in a transaction of
    its own, and records the attempt. Two deliveries at once never send the same email: each
    takes the email's advisory lock and skips one another holds."""
    sent = failed = 0
    for email_id, pmc_id, to, subject, body in conn.execute(
        PENDING, (MAX_ATTEMPTS, limit)
    ).fetchall():
        with conn.transaction():
            locked = conn.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))", (str(email_id),)
            ).fetchone()
            delivered = conn.execute(
                "SELECT 1 FROM trust_email_deliveries WHERE email_id = %s AND error IS NULL",
                (email_id,),
            ).fetchone()
            if not (locked and locked[0]) or delivered:
                continue  # another delivery has it, or had it
            try:
                sender.send(Message(to, subject, body))
                error = None
            except (OSError, smtplib.SMTPException) as refused:
                error = f"{type(refused).__name__}: {refused}"[:1000]
            conn.execute(
                "INSERT INTO trust_email_deliveries (email_id, pmc_id, sender, error)"
                " VALUES (%s, %s, %s, %s)",
                (email_id, pmc_id, sender.name, error),
            )
        if error is None:
            sent += 1
        else:
            failed += 1
    return Delivered(sent, failed)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["deliver"]:
        print("usage: python -m pms.email deliver", file=sys.stderr)
        return 2
    sender = sender_named(os.environ.get("PMS_EMAIL_SENDER", ""), os.environ)
    if sender is None:
        print("PMS_EMAIL_SENDER is unset: nothing is sent", file=sys.stderr)
        return 0
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier("trust_app")))
        done = deliver_pending(conn, sender)
    print(f"sent {done.sent}, failed {done.failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
