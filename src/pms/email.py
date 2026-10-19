"""Email the PMS sends, behind one interface (docs/adr/0004-email.md, docs/adr/0007-email.md).

The emails: a bill over an owner's approval limit tells its owner it waits for them (nothing in
it decides anything); a portal invite, and a password reset an owner or a tenant asks for, each
with the link that sets a password; and what staff write to a tenant, an owner or a vendor.

Since migration 0090 (the founder's review, q081), sending a lease offer for signing emails each
signer their own signing link, which is held in memory the same way.

An invite's or a reset's link signs someone in, so it is never written to the outbox: the email
keeps a placeholder (LINK), and the link waits in this process's memory (hold_link) until the
email is handed to the sender. One not sent within LINK_MINUTES is given up on, once, and staff
send a new one.

Each email is written to trust_emails in the transaction that causes it, so it exists only if
its cause does. After that commits, deliver_pending() hands each email not yet delivered to the
server's sender and records every attempt (trust_email_deliveries): delivered, or why not. One
that failed is tried again on a later delivery, at most MAX_ATTEMPTS times in all. The outbox
keeps an email's text; it goes as that text and as HTML made from it (src/pms/email_html.py),
under the company's name and mailing address.

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
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Literal, Protocol
from uuid import UUID

import psycopg
from psycopg import sql

from pms import email_html

MAX_ATTEMPTS = 5
# Where an invite's or a reset's link goes in its body, and how long the link is held for it.
LINK = "{{link}}"
LINK_MINUTES = 10
LINK_LOST = "its sign-in link is held only until it is sent, and it was not sent: send a new one"


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str
    # The company's name, shown beside the server's From address.
    from_name: str = ""
    # The same email as HTML, sent beside the text when there is one.
    html: str = ""


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
        email["From"] = formataddr((message.from_name, self.from_address))
        email["To"] = message.to
        email["Subject"] = message.subject
        email.set_content(message.body)
        if message.html:
            email.add_alternative(message.html, subtype="html")
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


# --- the sign-in links of invites and resets, held in memory -------------------------------------

_links: dict[UUID, tuple[str, float]] = {}
_links_lock = threading.Lock()


def hold_link(email_id: UUID, link: str) -> None:
    """Holds an invite's or a reset's link until its email is handed to the sender."""
    now = time.monotonic()
    with _links_lock:
        for held, (_, since) in list(_links.items()):
            if now - since > LINK_MINUTES * 60:
                del _links[held]
        _links[email_id] = (link, now)


def _held_link(email_id: UUID) -> str | None:
    with _links_lock:
        held = _links.get(email_id)
    return None if held is None or time.monotonic() - held[1] > LINK_MINUTES * 60 else held[0]


def _drop_link(email_id: UUID) -> None:
    with _links_lock:
        _links.pop(email_id, None)


# --- invites, resets and messages -----------------------------------------------------------------

# Each kind of person an email goes to: their name, and the column naming them. No part of a
# query comes from a request.
PEOPLE = {
    "staff": ("SELECT display_name FROM trust_staff WHERE pmc_id = %s AND id = %s", "staff_id"),
    "owner": ("SELECT display_name FROM trust_owners WHERE pmc_id = %s AND id = %s", "owner_id"),
    "tenant": (
        "SELECT display_name FROM trust_tenants WHERE pmc_id = %s AND id = %s",
        "tenant_id",
    ),
    "vendor": (
        "SELECT display_name FROM trust_vendors WHERE pmc_id = %s AND id = %s",
        "vendor_id",
    ),
}
WHERE = {"staff": "Occupella PMS", "owner": "the owner portal", "tenant": "the tenant portal"}


def _names(
    conn: psycopg.Connection[Any], pmc_id: UUID, kind: str, person_id: UUID
) -> tuple[str, str]:
    """The person's name and their company's."""
    person = conn.execute(PEOPLE[kind][0], (pmc_id, person_id)).fetchone()
    company = conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc_id,)
    ).fetchone()
    if person is None or company is None:
        raise LookupError(f"no {kind} {person_id} in PMC {pmc_id}")
    return person[0], company[0]


def _write(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    kind: str,
    to_kind: str,
    person_id: UUID,
    to: str,
    subject: str,
    body: str,
    sent_by: UUID | None = None,
) -> UUID:
    column = PEOPLE[to_kind][1]
    row = conn.execute(
        "INSERT INTO trust_emails (pmc_id, kind, to_address, subject, body, sent_by, needs_link,"  # noqa: S608
        f" {column}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (pmc_id, kind, to, subject, body, sent_by, kind in ("invite", "password_reset"), person_id),
    ).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def write_invite(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    kind: str,
    person_id: UUID,
    to: str,
    link: str,
    expires_at: datetime,
) -> UUID:
    """Writes a portal invite's email, in the caller's transaction, and holds its link."""
    name, company = _names(conn, pmc_id, kind, person_id)
    extra = " You'll also set up an authenticator app." if kind == "staff" else ""
    body = (
        f"Hello {name},\n\n"
        f"{company} invites you to sign in to {WHERE[kind]}. Set your password with this link;"
        f" it works once, until {expires_at:%m/%d/%Y}:{extra}\n{LINK}\n\n"
        f"{company}\n"
    )
    email_id = _write(
        conn, pmc_id, "invite", kind, person_id, to, f"{company} invites you to sign in", body
    )
    hold_link(email_id, link)
    return email_id


def write_password_reset(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    kind: str,
    person_id: UUID,
    to: str,
    link: str,
    expires_at: datetime,
) -> UUID:
    """Writes the email a password reset sends, in the caller's transaction, and holds its link."""
    name, company = _names(conn, pmc_id, kind, person_id)
    body = (
        f"Hello {name},\n\n"
        f"Someone asked to set a new password for your {company} login ({to}). If it was you,"
        f" use this link; it works once, until {expires_at:%m/%d/%Y}:\n{LINK}\n\n"
        f"If it wasn't you, ignore this email: your password stays as it is.\n\n{company}\n"
    )
    email_id = _write(
        conn,
        pmc_id,
        "password_reset",
        kind,
        person_id,
        to,
        f"Set a new password for {company}",
        body,
    )
    hold_link(email_id, link)
    return email_id


def write_message(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    kind: str,
    person_id: UUID,
    to: str,
    subject: str,
    text: str,
    sent_by: UUID,
) -> UUID:
    """Writes what a staff member wrote to a tenant, an owner or a vendor, signed with their name
    and the company's, in the caller's transaction."""
    _, company = _names(conn, pmc_id, kind, person_id)
    staff = conn.execute(PEOPLE["staff"][0], (pmc_id, sent_by)).fetchone()
    if staff is None:
        raise LookupError(f"no staff {sent_by} in PMC {pmc_id}")
    body = f"{text}\n\n{staff[0]}\n{company}\n"
    return _write(conn, pmc_id, "message", kind, person_id, to, subject, body, sent_by)


# --- a bill waiting for its owner ------------------------------------------------------------

Written = Literal["written", "within the limit", "no active login", "written already"]

BILL = """
SELECT b.amount, g.approval_limit, v.display_name, p.display_name, b.memo, b.reference,
       b.bill_date, b.due_on, o.display_name, m.display_name,
       (SELECT l.email FROM trust_logins AS l
        JOIN trust_login_secrets AS s ON s.login_id = l.id
        WHERE l.pmc_id = b.pmc_id AND l.kind = 'owner' AND l.owner_id = o.id
          AND l.disabled_at IS NULL),
       mk.amount, mk.percent
FROM trust_bills AS b
JOIN trust_ledger_accounts AS t ON t.ledger_account_id = b.ledger_account_id
JOIN trust_owners AS o ON o.id = t.owner_id
JOIN trust_vendors AS v ON v.id = b.vendor_id
JOIN trust_properties AS p ON p.id = t.property_id
JOIN trust_pmcs AS m ON m.pmc_id = b.pmc_id
LEFT JOIN trust_bill_markups AS mk ON mk.bill_id = b.id
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
    amount, limit, vendor, prop, memo, reference, billed, due, owner, pmc, to, markup, pct = row
    if amount <= limit:
        return "within the limit"
    if to is None:
        return "no active login"
    # The company's markup on it, as the owner's agreement sets it (migration 0083).
    marked = (
        ""
        if markup is None
        else f"Our markup of {pct.normalize():f}% on it, {dollars(markup)}, as your management "
        "agreement sets, is taken with it.\n\n"
    )
    body = (
        f"Hello {owner},\n\n"
        f"{vendor} billed {dollars(amount)} for {prop}: {memo}. Reference {reference}, "
        f"dated {billed}, due {due}.\n\n"
        f"{marked}"
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


def write_signing_request(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    offer_id: UUID,
    position: int,
    name: str,
    to: str,
    what: str,
    link: str,
) -> UUID | None:
    """Writes the email that gives one signer of an offer their signing link, in the caller's
    transaction, and holds the link; None when that signer was emailed already."""
    company = conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc_id,)
    ).fetchone()
    assert company is not None
    body = (
        f"Hello {name},\n\n"
        f"{company[0]} sent you {what} to sign. Open it and sign with this link, which is yours"
        f" alone:\n{LINK}\n\n{company[0]}\n"
    )
    row = conn.execute(
        "INSERT INTO trust_emails (pmc_id, kind, offer_id, signer_position, to_address, subject,"
        " body, needs_link) VALUES (%s, 'signing_request', %s, %s, %s, %s, %s, true)"
        " ON CONFLICT (offer_id, signer_position) WHERE kind = 'signing_request' DO NOTHING"
        " RETURNING id",
        (pmc_id, offer_id, position, to, f"{company[0]}: {what} to sign"[:200], body),
    ).fetchone()
    if row is None:
        return None
    email_id = UUID(str(row[0]))
    hold_link(email_id, link)
    return email_id


# --- delivery ----------------------------------------------------------------------------------

PENDING = """
SELECT e.id, e.pmc_id, e.kind, e.to_address, e.subject, e.body, e.needs_link,
       e.created_at < now() - make_interval(mins => %(minutes)s), p.display_name,
       a.address_line_1, a.address_line_2,
       a.city || ', ' || a.state || ' ' || a.postal_code
FROM trust_emails AS e
JOIN trust_pmcs AS p ON p.pmc_id = e.pmc_id
-- The company's mailing address, as its latest profile says (none: no address).
LEFT JOIN LATERAL (SELECT c.address_line_1, c.address_line_2, c.city, c.state, c.postal_code
                   FROM trust_company_profiles AS c WHERE c.pmc_id = e.pmc_id
                   ORDER BY c.created_at DESC, c.id DESC LIMIT 1) AS a ON true
WHERE NOT EXISTS (SELECT 1 FROM trust_email_deliveries AS d
                  WHERE d.email_id = e.id AND (d.error IS NULL OR d.error = %(lost)s))
  AND (SELECT count(*) FROM trust_email_deliveries AS d WHERE d.email_id = e.id) < %(attempts)s
ORDER BY e.created_at, e.id
LIMIT %(limit)s
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
    for row in conn.execute(
        PENDING,
        {"minutes": LINK_MINUTES, "lost": LINK_LOST, "attempts": MAX_ATTEMPTS, "limit": limit},
    ).fetchall():
        email_id, pmc_id, kind, to, subject, body, needs_link, stale, company, *address = row
        link = _held_link(email_id) if needs_link else None
        if needs_link and link is None and not stale:
            continue  # its link is held by the process that wrote it, which sends it
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
            if needs_link and link is None:
                error = LINK_LOST  # given up on, once
            else:
                try:
                    text = body.replace(LINK, link) if link is not None else body
                    lines = tuple(line for line in address if line)
                    html = email_html.render(text, kind, subject, company, lines)
                    sender.send(Message(to, subject, text, company, html))
                    error = None
                except (OSError, smtplib.SMTPException) as refused:
                    error = f"{type(refused).__name__}: {refused}"[:1000]
            conn.execute(
                "INSERT INTO trust_email_deliveries (email_id, pmc_id, sender, error)"
                " VALUES (%s, %s, %s, %s)",
                (email_id, pmc_id, sender.name, error),
            )
        if error is None:
            _drop_link(email_id)
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
