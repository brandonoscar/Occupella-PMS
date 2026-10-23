"""Text messages the PMS sends (migration 20261022000161; the founder, 2026-10-23), behind one
interface, as email is (src/pms/email.py).

A text goes only to a number its person agreed to be texted at, while their latest word says yes
(trust_text_consents; the database refuses any other): what staff write to a tenant, an owner or
a vendor, and, once a company turns them on, a rent reminder and a late notice to each tenant who
agreed. Each names the company and says how to stop. A reply of STOP says no from then on, START
after it yes again (reply()).

Each text is written to trust_texts in the transaction that causes it. After that commits,
deliver_pending() hands each one due to the server's sender and records every attempt
(trust_text_deliveries): the provider's id for it, or why not. One that failed is tried again, at
most MAX_ATTEMPTS times in all. One whose person said stop since, or that waited a day past its
time, is given up on. The PMS's own texts wait for the afternoon, UTC (WINDOW): it doesn't know
each property's time zone, and between 16:00 and midnight UTC is daytime across the
continental US (docs/law.md).

The server's PMS_TEXT_SENDER picks the sender: "fake" keeps each text in the outbox and sends it
nowhere (development and CI); "twilio" sends through Twilio's Messages API with
PMS_TWILIO_ACCOUNT_SID, PMS_TWILIO_AUTH_TOKEN and PMS_TWILIO_FROM (a number, or a messaging
service's id) from the environment, NOT VERIFIED without an account; unset sends nothing, and the
PMS writes no text.

usage: python -m pms.texts deliver    (DATABASE_URL, acting as trust_app)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool

from pms import email, notices

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
# The longest text: three parts of a plain one.
LONGEST = 480
# When the PMS's own texts go, UTC hours: noon to 8 pm Eastern, 9 am to 5 pm Pacific in summer.
WINDOW = (16, 24)
# How long past its time a text is still worth sending.
STALE = timedelta(days=1)
STOPPED = "they said stop since it was written, so it was not sent"
LATE = "it waited a day past its time, so it was not sent"
FINAL = (STOPPED, LATE)
# A reply that says stop, or starts again (the carriers' words; Twilio's too).
STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT"}
START_WORDS = {"START", "YES", "UNSTOP"}
SAY_STOP = "Reply STOP to stop."

# Whether the server sends texts (PMS_TEXT_SENDER is set): the PMS writes texts only while it
# does. The app's middleware sets it; code that calls a route itself sends none.
TEXTING: ContextVar[bool] = ContextVar("pms_texting", default=False)

Kind = Literal["tenant", "owner", "vendor"]
COLUMN: dict[Kind, str] = {"tenant": "tenant_id", "owner": "owner_id", "vendor": "vendor_id"}


class Refused(Exception):
    """The provider would not take a text."""


class Sender(Protocol):
    @property
    def name(self) -> str: ...

    def send(self, to: str, body: str) -> str:
        """Hands the text over; the provider's id for it."""
        ...


class FakeSender:
    """Keeps each text in the outbox and sends it nowhere: for development and tests."""

    name = "fake"

    def send(self, to: str, body: str) -> str:
        return f"fake-{secrets.token_hex(8)}"


@dataclass(frozen=True)
class TwilioSender:
    """Sends through Twilio's Messages API; checks the signature on what Twilio posts back.
    NOT VERIFIED against Twilio itself, which needs an account."""

    account_sid: str
    auth_token: str = field(repr=False)
    # A number (+1...) or a messaging service's id (MG...).
    from_: str
    name: str = "twilio"

    def send(self, to: str, body: str) -> str:
        form = {"To": to, "Body": body}
        form["MessagingServiceSid" if self.from_.startswith("MG") else "From"] = self.from_
        who = base64.b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode()
        request = urllib.request.Request(  # noqa: S310 (a fixed https URL)
            "https://api.twilio.com/2010-04-01/Accounts/"
            f"{urllib.parse.quote(self.account_sid)}/Messages.json",
            data=urllib.parse.urlencode(form).encode(),
            headers={"Authorization": f"Basic {who}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as answer:  # noqa: S310
                return str(json.load(answer)["sid"])
        except urllib.error.HTTPError as refused:
            try:
                said = json.load(refused).get("message", "")
            except ValueError:
                said = ""
            raise Refused(f"Twilio said {refused.code}: {said}".rstrip(": ")) from None

    def signed(self, url: str, params: Mapping[str, str], signature: str) -> bool:
        """Whether Twilio signed what it posted to the URL (its X-Twilio-Signature): the URL and
        each parameter's name and value in name order, under HMAC-SHA1 with the auth token."""
        data = url + "".join(name + params[name] for name in sorted(params))
        made = hmac.new(self.auth_token.encode(), data.encode(), hashlib.sha1).digest()
        return hmac.compare_digest(base64.b64encode(made).decode(), signature)


def sender_named(name: str, env: Mapping[str, str]) -> Sender | None:
    """The sender PMS_TEXT_SENDER names, set up from the server's environment; None for none."""
    if name == "":
        return None
    if name == "fake":
        return FakeSender()
    if name == "twilio":
        keys = ("PMS_TWILIO_ACCOUNT_SID", "PMS_TWILIO_AUTH_TOKEN", "PMS_TWILIO_FROM")
        missing = [key for key in keys if not env.get(key)]
        if missing:
            raise ValueError(f"PMS_TEXT_SENDER=twilio needs {' and '.join(missing)}")
        return TwilioSender(*(env[key] for key in keys))
    raise ValueError(f"PMS_TEXT_SENDER is 'fake', 'twilio' or unset, not {name!r}")


def us_mobile(typed: str) -> str | None:
    """A US number as +1 and ten digits, from however it was typed; None when it isn't one."""
    digits = re.sub(r"[^0-9]", "", typed)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if not re.fullmatch(r"[2-9][0-9]{2}[2-9][0-9]{6}", digits):
        return None
    return f"+1{digits}"


def shown(phone: str) -> str:
    """+15555550123 as (555) 555-0123."""
    return f"({phone[2:5]}) {phone[5:8]}-{phone[8:]}"


def next_in_window(now: datetime) -> datetime:
    """The first moment from now inside WINDOW."""
    start = now.replace(hour=WINDOW[0], minute=0, second=0, microsecond=0)
    if now < start:
        return start
    if now.hour < WINDOW[1]:
        return now
    return start + timedelta(days=1)


# --- a person's word on texts ------------------------------------------------------------------


@dataclass(frozen=True)
class Consent:
    id: UUID
    phone: str
    consented: bool
    how: str
    at: datetime
    by: str | None


def consent_of(
    conn: psycopg.Connection[Any], pmc_id: UUID, kind: Kind, person_id: UUID
) -> Consent | None:
    """A person's latest word on texts, or None."""
    keys = [person_id if k == kind else None for k in COLUMN]
    row = conn.execute(
        "SELECT c.id, c.phone, c.consented, c.how, c.created_at, s.display_name"
        " FROM trust_text_consents AS c LEFT JOIN trust_staff AS s ON s.id = c.recorded_by"
        " WHERE c.id = trust_text_consent_of(%s, %s, %s, %s)",
        (pmc_id, *keys),
    ).fetchone()
    return None if row is None else Consent(*row)


def record_consent(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    kind: Kind,
    person_id: UUID,
    phone: str,
    consented: bool,
    how: str,
) -> bool:
    """Records a person's word unless it is what their latest says already; True when written."""
    now = consent_of(conn, pmc_id, kind, person_id)
    if now is not None and now.phone == phone and now.consented == consented:
        return False
    conn.execute(
        f"INSERT INTO trust_text_consents (pmc_id, {COLUMN[kind]}, phone, consented, how,"  # noqa: S608
        " by_person) VALUES (%s, %s, %s, %s, %s, %s)",
        (pmc_id, person_id, phone, consented, how, how == "portal"),
    )
    return True


def reply(conn: psycopg.Connection[Any], phone: str, body: str) -> int:
    """Takes a reply from a number: STOP says no for everyone whose latest word says yes at it,
    in every company on this server (the number texted them all); START says yes again for those
    a STOP stopped. Anything else changes nothing. How many people's word changed."""
    word = body.strip().upper()
    stop, start = word in STOP_WORDS, word in START_WORDS
    if not (stop or start):
        return 0
    changed = 0
    for pmc_id, tenant, owner, vendor, consented, how in conn.execute(
        "SELECT l.pmc_id, l.tenant_id, l.owner_id, l.vendor_id, l.consented, l.how"
        " FROM (SELECT DISTINCT pmc_id, tenant_id, owner_id, vendor_id"
        "       FROM trust_text_consents WHERE phone = %(phone)s) AS p"
        " JOIN trust_text_consents AS l"
        "   ON l.id = trust_text_consent_of(p.pmc_id, p.tenant_id, p.owner_id, p.vendor_id)"
        " WHERE l.phone = %(phone)s",
        {"phone": phone},
    ).fetchall():
        if (stop and consented) or (start and how == "stop reply"):
            conn.execute(
                "INSERT INTO trust_text_consents (pmc_id, tenant_id, owner_id, vendor_id, phone,"
                " consented, how) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    pmc_id,
                    tenant,
                    owner,
                    vendor,
                    phone,
                    start,
                    "start reply" if start else "stop reply",
                ),
            )
            changed += 1
    return changed


# --- writing texts ------------------------------------------------------------------------------


def write(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    kind: str,
    to_kind: Kind,
    person_id: UUID,
    body: str,
    not_before: datetime,
    *,
    sent_by: UUID | None = None,
    lease_id: UUID | None = None,
    period: date | None = None,
    charge_id: UUID | None = None,
) -> UUID | None:
    """Writes one text, in the caller's transaction, to the number the person last agreed to;
    None when they haven't agreed, or it was written already (the PMS's own, once each)."""
    consent = consent_of(conn, pmc_id, to_kind, person_id)
    if consent is None or not consent.consented:
        return None
    row = conn.execute(
        f"INSERT INTO trust_texts (pmc_id, kind, {COLUMN[to_kind]}, consent_id, to_phone, body,"  # noqa: S608
        " sent_by, lease_id, period, charge_id, not_before)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT DO NOTHING RETURNING id",
        (
            pmc_id,
            kind,
            person_id,
            consent.id,
            consent.phone,
            body,
            sent_by,
            lease_id,
            period,
            charge_id,
            not_before,
        ),
    ).fetchone()
    return None if row is None else UUID(str(row[0]))


def message_body(company: str, text: str, staff: str) -> str:
    """What staff wrote, as it goes: the company's name first, who wrote it, and how to stop."""
    return f"{company}: {text} - {staff}. {SAY_STOP}"


@dataclass(frozen=True)
class Settings:
    # Days before rent is due that its reminder is texted; None texts none.
    rent_reminder_days: int | None = None
    late_notices: bool = False


def settings_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> Settings:
    """The company's latest text settings; all off when it has none."""
    row = conn.execute(
        "SELECT rent_reminder_days, late_notices FROM trust_text_settings WHERE pmc_id = %s"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (pmc_id,),
    ).fetchone()
    return Settings() if row is None else Settings(*row)


# Each tenant of each lease in effect on a due day whose rent is due then and who agreed to texts
# (as notices.REMINDERS, without the emails).
REMINDERS = f"""
SELECT l.id, lt.tenant_id, p.display_name, u.display_name, trust_rent_on(l.id, %(due)s)
FROM trust_leases AS l
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
JOIN trust_lease_tenants AS lt ON lt.lease_id = l.id
WHERE l.pmc_id = %(pmc)s
  AND l.starts_on <= %(due)s AND (l.ends_on IS NULL OR %(due)s <= l.ends_on)
  AND trust_rent_due_day(l.id, %(due)s) = extract(day FROM %(due)s::date)
  AND {notices.NO_HOLD}
  AND NOT EXISTS (SELECT 1 FROM trust_lease_departures AS d
                  WHERE d.lease_id = l.id AND d.tenant_id = lt.tenant_id AND d.left_on < %(due)s)
  AND NOT EXISTS (SELECT 1 FROM trust_texts AS x
                  WHERE x.kind = 'rent_reminder' AND x.tenant_id = lt.tenant_id
                    AND x.lease_id = l.id AND x.period = %(due)s)
ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C", l.id, lt.tenant_id
"""  # noqa: S608

LATE_FEES = f"""
SELECT f.fee_charge_id, l.id, lt.tenant_id, p.display_name, u.display_name, r.due_on, f.fee,
       trust_lease_owed(l.id)
FROM trust_late_fees AS f
JOIN trust_charges AS r ON r.id = f.rent_charge_id
JOIN trust_leases AS l ON l.id = r.lease_id
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
JOIN trust_lease_tenants AS lt ON lt.lease_id = l.id
WHERE f.pmc_id = %(pmc)s AND f.created_at = now() AND {notices.NO_HOLD}
  AND NOT EXISTS (SELECT 1 FROM trust_lease_departures AS d
                  WHERE d.lease_id = l.id AND d.tenant_id = lt.tenant_id AND d.left_on <= %(day)s)
ORDER BY r.due_on, l.id, lt.tenant_id
"""  # noqa: S608


def write_scheduled(
    conn: psycopg.Connection[Any], pmc_id: UUID, day: date, now: datetime | None = None
) -> int:
    """The rent reminders and late notices the company texts, due on a day, each once, to each
    tenant who agreed; in the caller's transaction, after its rent run. They wait for WINDOW."""
    wanted = settings_of(conn, pmc_id)
    if wanted.rent_reminder_days is None and not wanted.late_notices:
        return 0
    company = notices.company_of(conn, pmc_id)
    at = next_in_window(now or datetime.now(UTC))
    written = 0
    for ahead in range(1, (wanted.rent_reminder_days or 0) + 1):
        due = day + timedelta(days=ahead)
        for lease, tenant, prop, unit, rent in conn.execute(
            REMINDERS, {"pmc": pmc_id, "due": due, "day": day}
        ).fetchall():
            body = (
                f"{company}: rent for {prop}, {unit} is due {notices.us(due)},"
                f" {email.dollars(rent)}. {SAY_STOP}"
            )
            made = write(
                conn, pmc_id, "rent_reminder", "tenant", tenant, body[:LONGEST], at,
                lease_id=lease, period=due,
            )  # fmt: skip
            written += made is not None
    if wanted.late_notices:
        for charge, lease, tenant, prop, unit, due_on, fee, owed in conn.execute(
            LATE_FEES, {"pmc": pmc_id, "day": day}
        ).fetchall():
            body = (
                f"{company}: a late fee of {email.dollars(fee)} was charged for {prop}, {unit}'s"
                f" rent due {notices.us(due_on)}. Owed now: {email.dollars(owed)}. {SAY_STOP}"
            )
            made = write(
                conn, pmc_id, "late_notice", "tenant", tenant, body[:LONGEST], at,
                lease_id=lease, charge_id=charge,
            )  # fmt: skip
            written += made is not None
    return written


# --- delivery ----------------------------------------------------------------------------------

PENDING = """
SELECT t.id, t.pmc_id, t.to_phone, t.body, t.not_before,
       t.consent_id = trust_text_consent_of(t.pmc_id, t.tenant_id, t.owner_id, t.vendor_id)
FROM trust_texts AS t
WHERE t.not_before <= %(now)s
  AND NOT EXISTS (SELECT 1 FROM trust_text_deliveries AS d
                  WHERE d.text_id = t.id AND (d.error IS NULL OR d.error = ANY(%(final)s)))
  AND (SELECT count(*) FROM trust_text_deliveries AS d WHERE d.text_id = t.id) < %(attempts)s
ORDER BY t.not_before, t.created_at, t.id
LIMIT %(limit)s
"""


@dataclass(frozen=True)
class Delivered:
    sent: int
    failed: int


def deliver_pending(
    conn: psycopg.Connection[Any],
    sender: Sender,
    now: datetime | None = None,
    limit: int = 100,
) -> Delivered:
    """Hands each text due and not yet delivered to the sender, oldest first, each in a
    transaction of its own, and records the attempt. Two deliveries at once never send the same
    text: each takes the text's advisory lock and skips one another holds."""
    at = now or datetime.now(UTC)
    sent = failed = 0
    for text_id, pmc_id, to, body, not_before, still_agreed in conn.execute(
        PENDING,
        {"now": at, "final": list(FINAL), "attempts": MAX_ATTEMPTS, "limit": limit},
    ).fetchall():
        provider_id: str | None = None
        with conn.transaction():
            locked = conn.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))", (f"text {text_id}",)
            ).fetchone()
            delivered = conn.execute(
                "SELECT 1 FROM trust_text_deliveries WHERE text_id = %s AND error IS NULL",
                (text_id,),
            ).fetchone()
            if not (locked and locked[0]) or delivered:
                continue  # another delivery has it, or had it
            error: str | None
            if not still_agreed:
                error = STOPPED
            elif at - not_before > STALE:
                error = LATE
            else:
                try:
                    provider_id = sender.send(to, body)[:100]
                    error = None
                except Refused as refused:
                    error = str(refused)[:1000] or "the provider refused it"
                except OSError as broken:
                    error = f"{type(broken).__name__}: {broken}"[:1000]
            conn.execute(
                "INSERT INTO trust_text_deliveries (text_id, pmc_id, sender, provider_id, error)"
                " VALUES (%s, %s, %s, %s, %s)",
                (text_id, pmc_id, sender.name, provider_id, error),
            )
        if error is None:
            sent += 1
        else:
            failed += 1
    return Delivered(sent, failed)


def deliver(pool: ConnectionPool[Any], sender: Sender) -> None:
    """Delivers what is due in the outbox, from the API's pool."""
    with pool.connection() as conn:
        conn.autocommit = True
        try:
            deliver_pending(conn, sender)
        finally:
            conn.autocommit = False


class Deliverer:
    """Delivers what is due every `every` seconds, in a thread of its own, until stopped: the
    PMS's own texts wait for WINDOW, after the request or the run that wrote them."""

    def __init__(self, pool: ConnectionPool[Any], sender: Sender, every: float = 300) -> None:
        self.pool = pool
        self.sender = sender
        self.every = every
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="pms-texts", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def _loop(self) -> None:
        while not self._stop.wait(self.every):
            try:
                deliver(self.pool, self.sender)
            except Exception:
                log.exception("delivering texts failed; tried again in %s seconds", self.every)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["deliver"]:
        print("usage: python -m pms.texts deliver", file=sys.stderr)
        return 2
    sender = sender_named(os.environ.get("PMS_TEXT_SENDER", ""), os.environ)
    if sender is None:
        print("PMS_TEXT_SENDER is unset: nothing is sent", file=sys.stderr)
        return 0
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier("trust_app")))
        done = deliver_pending(conn, sender)
    print(f"sent {done.sent}, failed {done.failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
