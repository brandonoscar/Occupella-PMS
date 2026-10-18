"""The emails the PMS writes by itself (docs/adr/0007-email.md, its second step; migration 0077),
each one a company turns on in Settings, Company setup (trust_email_settings, the latest row
holding; none at all until it does):

- a rent reminder to each tenant of a lease in effect on the next first of the month, from some
  days before it (once per tenant, lease and due day);
- a late notice to each tenant of a lease a late fee was just charged to (once per tenant and fee);
- last month's statement in brief to each owner with a property account, from a day of the month
  (once per owner and month);
- a work order to its vendor when it is assigned to them or their visit is scheduled (once per
  step);
- the adverse action notice to each applicant of an application declined or approved on
  conditions on a screening, when the decision is recorded (once per applicant and application).

Each is written to the outbox in the transaction that causes it (src/pms/email.py), and only while
the server sends email: none piles up to go out late on a server that sends none. A lease under a
payment hold (an eviction filed) gets no reminder or late notice. An email goes to a person's
contact email, else to their portal login's; one with neither gets none. A vendor's goes to their
contact email; an applicant's to the email they applied with. None of it moves money.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID

import psycopg

from pms import email

Kind = Literal["tenant", "owner", "vendor"]


@dataclass(frozen=True)
class Settings:
    # Days before the first of the month that rent reminders go; None sends none.
    rent_reminder_days: int | None = None
    late_notices: bool = False
    # The day of the month owners get last month's statement; None sends none.
    owner_statement_day: int | None = None
    work_order_notices: bool = False
    adverse_action_notices: bool = False


FIELDS = (
    "rent_reminder_days",
    "late_notices",
    "owner_statement_day",
    "work_order_notices",
    "adverse_action_notices",
)


def settings_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> Settings:
    """The company's latest settings; all off when it has none."""
    row = conn.execute(
        f"SELECT {', '.join(FIELDS)} FROM trust_email_settings WHERE pmc_id = %s"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (pmc_id,),
    ).fetchone()
    return Settings() if row is None else Settings(*row)


def address(conn: psycopg.Connection[Any], pmc_id: UUID, kind: Kind, person_id: UUID) -> str | None:
    """Where a tenant's, owner's or vendor's email goes: their contact email, else their portal
    login's while it can sign in (owners and tenants), else nowhere."""
    column = email.PEOPLE[kind][1]
    row = conn.execute(
        f"SELECT email FROM trust_contacts WHERE {column} = %s"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (person_id,),
    ).fetchone()
    if row is not None and row[0]:
        return str(row[0])
    if kind == "vendor":
        return None
    login = conn.execute(
        f"SELECT email FROM trust_logins WHERE pmc_id = %s AND {column} = %s"
        " AND disabled_at IS NULL",
        (pmc_id, person_id),
    ).fetchone()
    return None if login is None else str(login[0])


def can_sign_in(conn: psycopg.Connection[Any], pmc_id: UUID, kind: Kind, person_id: UUID) -> bool:
    column = email.PEOPLE[kind][1]
    return (
        conn.execute(
            f"SELECT 1 FROM trust_logins WHERE pmc_id = %s AND {column} = %s"
            " AND disabled_at IS NULL",
            (pmc_id, person_id),
        ).fetchone()
        is not None
    )


def company_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> str:
    row = conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"no PMC {pmc_id}")
    return str(row[0])


def us(day: date) -> str:
    return f"{day:%m/%d/%Y}"


def one_line(text: str) -> str:
    """Text fit for a subject: an email header holds no line break."""
    return " ".join(text.split())


def _insert(conn: psycopg.Connection[Any], pmc_id: UUID, kind: str, about: dict[str, Any]) -> bool:
    """Writes one automatic email; False when it was written already."""
    columns = ["pmc_id", "kind", *about]
    made = conn.execute(
        f"INSERT INTO trust_emails ({', '.join(columns)})"
        f" VALUES ({', '.join(['%s'] * len(columns))}) ON CONFLICT DO NOTHING RETURNING id",
        (pmc_id, kind, *about.values()),
    ).fetchone()
    return made is not None


def portal_line(signs_in: bool, public_url: str, what: str) -> str:
    return f"{what} in the tenant portal:\n{public_url.rstrip('/')}/\n\n" if signs_in else ""


# --- rent reminders and late notices -----------------------------------------------------------

# A lease is under a payment hold on a day from the hold's first day until its release ends it.
NO_HOLD = """
NOT EXISTS (SELECT 1 FROM trust_payment_holds AS h
            LEFT JOIN trust_payment_hold_releases AS r ON r.hold_id = h.id
            WHERE h.lease_id = l.id AND h.starts_on <= %(day)s
              AND (r.ends_on IS NULL OR %(day)s < r.ends_on))
"""

REMINDERS = f"""
SELECT l.id, lt.tenant_id, t.display_name, p.display_name, u.display_name, l.ends_on,
       trust_rent_on(l.id, %(due)s), trust_lease_owed(l.id)
FROM trust_leases AS l
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
JOIN trust_lease_tenants AS lt ON lt.lease_id = l.id
JOIN trust_tenants AS t ON t.id = lt.tenant_id
WHERE l.pmc_id = %(pmc)s
  AND l.starts_on <= %(due)s AND (l.ends_on IS NULL OR %(due)s <= l.ends_on)
  -- The lease's rent is due on that day of the month (migration 0081: the first unless it
  -- picked another).
  AND trust_rent_due_day(l.id, %(due)s) = extract(day FROM %(due)s::date)
  AND {NO_HOLD}
  -- Not a tenant who left the lease before the day (others staying on it).
  AND NOT EXISTS (SELECT 1 FROM trust_lease_departures AS d
                  WHERE d.lease_id = l.id AND d.tenant_id = lt.tenant_id AND d.left_on < %(due)s)
  AND NOT EXISTS (SELECT 1 FROM trust_emails AS e
                  WHERE e.kind = 'rent_reminder' AND e.tenant_id = lt.tenant_id
                    AND e.lease_id = l.id AND e.period = %(due)s)
ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C", l.id, t.display_name COLLATE "C",
         lt.tenant_id
"""


def next_due(day: date) -> date:
    """The first of the month after the day."""
    return (day.replace(day=1) + timedelta(days=32)).replace(day=1)


def write_rent_reminders(
    conn: psycopg.Connection[Any], pmc_id: UUID, day: date, days_before: int, public_url: str
) -> int:
    """Reminds each tenant of each lease in effect on its next rent due day (the first, unless
    the lease picked another), once the day is no more than `days_before` days before it."""
    company = company_of(conn, pmc_id)
    written = 0
    for ahead in range(1, days_before + 1):
        written += remind(conn, pmc_id, day + timedelta(days=ahead), day, public_url, company)
    return written


def remind(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    due: date,
    day: date,
    public_url: str,
    company: str,
) -> int:
    """The reminders of rent due on a day, to each tenant of each lease due then."""
    written = 0
    for lease, tenant, name, prop, unit, ends_on, rent, owed in conn.execute(
        REMINDERS, {"pmc": pmc_id, "due": due, "day": day}
    ).fetchall():
        to = address(conn, pmc_id, "tenant", tenant)
        if to is None:
            continue
        month = f"{due:%B}"
        part = (
            f"Your lease ends on {us(ends_on)}, so {month}'s rent is for its days to then.\n"
            if ends_on is not None and ends_on < next_due(due) - timedelta(days=1)
            else ""
        )
        before = f"Owed today, before it: {email.dollars(owed)}.\n" if owed > 0 else ""
        body = (
            f"Hello {name},\n\n"
            f"Your rent for {prop}, {unit} is due on {us(due)}: {email.dollars(rent)} a month.\n"
            f"{part}{before}\n"
            + portal_line(
                can_sign_in(conn, pmc_id, "tenant", tenant),
                public_url,
                "See your lease, its monthly charges and what you owe",
            )
            + f"{company}\n"
        )
        written += _insert(
            conn,
            pmc_id,
            "rent_reminder",
            {
                "tenant_id": tenant,
                "lease_id": lease,
                "period": due,
                "to_address": to,
                "subject": f"Rent reminder: due {us(due)}",
                "body": body,
            },
        )
    return written


# The late fees charged in this transaction (their created_at is its start, now()).
LATE_FEES = f"""
SELECT f.fee_charge_id, l.id, lt.tenant_id, t.display_name, p.display_name, u.display_name,
       r.due_on, f.unpaid, f.fee, trust_lease_owed(l.id)
FROM trust_late_fees AS f
JOIN trust_charges AS r ON r.id = f.rent_charge_id
JOIN trust_leases AS l ON l.id = r.lease_id
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
JOIN trust_lease_tenants AS lt ON lt.lease_id = l.id
JOIN trust_tenants AS t ON t.id = lt.tenant_id
WHERE f.pmc_id = %(pmc)s AND f.created_at = now() AND {NO_HOLD}
  AND NOT EXISTS (SELECT 1 FROM trust_lease_departures AS d
                  WHERE d.lease_id = l.id AND d.tenant_id = lt.tenant_id AND d.left_on <= %(day)s)
ORDER BY r.due_on, l.id, t.display_name COLLATE "C", lt.tenant_id
"""


def write_late_notices(
    conn: psycopg.Connection[Any], pmc_id: UUID, day: date, public_url: str
) -> int:
    """Tells each tenant of each lease a late fee was charged to in this transaction."""
    company = company_of(conn, pmc_id)
    written = 0
    for fee_charge, lease, tenant, name, prop, unit, due_on, unpaid, fee, owed in conn.execute(
        LATE_FEES, {"pmc": pmc_id, "day": day}
    ).fetchall():
        to = address(conn, pmc_id, "tenant", tenant)
        if to is None:
            continue
        body = (
            f"Hello {name},\n\n"
            f"Rent for {prop}, {unit} due on {us(due_on)} still had {email.dollars(unpaid)} unpaid"
            f" after its grace days, so a late fee of {email.dollars(fee)} was charged on"
            f" {us(day)}.\nOwed today: {email.dollars(owed)}.\n\n"
            + portal_line(
                can_sign_in(conn, pmc_id, "tenant", tenant),
                public_url,
                "See every charge and payment",
            )
            + f"{company}\n"
        )
        written += _insert(
            conn,
            pmc_id,
            "late_notice",
            {
                "tenant_id": tenant,
                "lease_id": lease,
                "charge_id": fee_charge,
                "to_address": to,
                "subject": f"A late fee of {email.dollars(fee)} was charged",
                "body": body,
            },
        )
    return written


# --- owner statements --------------------------------------------------------------------------

OWNERS = """
SELECT o.id, o.display_name
FROM trust_owners AS o
WHERE o.pmc_id = %(pmc)s
  AND NOT trust_is_archived(NULL, NULL, NULL, o.id)
  AND EXISTS (SELECT 1 FROM trust_ledger_accounts AS a
              WHERE a.owner_id = o.id AND a.kind = 'owner_property')
  AND NOT EXISTS (SELECT 1 FROM trust_emails AS e
                  WHERE e.kind = 'owner_statement' AND e.owner_id = o.id
                    AND e.period = %(period)s)
ORDER BY o.display_name COLLATE "C", o.id
"""
TOTALS = {
    "all properties: opening balance": "balance",
    "all properties: money in": "amount",
    "all properties: money out": "amount",
    "all properties: closing balance": "balance",
}


def write_owner_statements(
    conn: psycopg.Connection[Any], pmc_id: UUID, day: date, on_day: int, public_url: str
) -> int:
    """Sends each owner with a property account last month's statement in brief, from the day of
    the month `on_day`."""
    if day.day < on_day:
        return 0
    period = (day.replace(day=1) - timedelta(days=1)).replace(day=1)
    start = datetime.combine(period, time(0), UTC)
    end = datetime.combine(day.replace(day=1), time(0), UTC)
    company = company_of(conn, pmc_id)
    written = 0
    for owner, name in conn.execute(OWNERS, {"pmc": pmc_id, "period": period}).fetchall():
        to = address(conn, pmc_id, "owner", owner)
        if to is None:
            continue
        totals = {
            item: (amount if TOTALS[item] == "amount" else balance)
            for item, amount, balance in conn.execute(
                "SELECT item, amount, balance FROM trust_report_owner_statement(%s, %s, %s, %s)"
                " WHERE property IS NULL",
                (pmc_id, owner, start, end),
            ).fetchall()
            if item in TOTALS
        }
        month = f"{period:%B %Y}"
        more = (
            f"Sign in to see each property's lines and download the whole statement:\n"
            f"{public_url.rstrip('/')}/\n\n"
            if can_sign_in(conn, pmc_id, "owner", owner)
            else f"Ask {company} for the whole statement.\n\n"
        )
        body = (
            f"Hello {name},\n\n"
            f"Your statement for {month}, all your properties together:\n\n"
            f"  Opening balance  {email.dollars(totals['all properties: opening balance'])}\n"
            f"  Money in         {email.dollars(totals['all properties: money in'])}\n"
            f"  Money out        {email.dollars(abs(totals['all properties: money out']))}\n"
            f"  Closing balance  {email.dollars(totals['all properties: closing balance'])}\n\n"
            f"{more}{company}\n"
        )
        written += _insert(
            conn,
            pmc_id,
            "owner_statement",
            {
                "owner_id": owner,
                "period": period,
                "to_address": to,
                "subject": f"Your statement for {month}",
                "body": body,
            },
        )
    return written


# --- what the nightly run (and an admin's run by hand) writes --------------------------------


@dataclass(frozen=True)
class Written:
    rent_reminders: int = 0
    late_notices: int = 0
    owner_statements: int = 0


def write_scheduled(
    conn: psycopg.Connection[Any], pmc_id: UUID, day: date, public_url: str
) -> Written:
    """The rent reminders, late notices and owner statements due on a day, as the company's
    settings ask; in the caller's transaction, after its rent run. Each once."""
    wanted = settings_of(conn, pmc_id)
    return Written(
        rent_reminders=0
        if wanted.rent_reminder_days is None
        else write_rent_reminders(conn, pmc_id, day, wanted.rent_reminder_days, public_url),
        late_notices=write_late_notices(conn, pmc_id, day, public_url)
        if wanted.late_notices
        else 0,
        owner_statements=0
        if wanted.owner_statement_day is None
        else write_owner_statements(conn, pmc_id, day, wanted.owner_statement_day, public_url),
    )


# --- a work order's vendor ---------------------------------------------------------------------

STEP = """
WITH numbered AS (
    SELECT w.*, row_number() OVER (ORDER BY w.created_at, w.id) AS number
    FROM trust_work_orders AS w WHERE w.pmc_id = %(pmc)s
)
SELECT s.step, s.scheduled_for, s.note,
       coalesce(s.vendor_id,
                (SELECT a.vendor_id FROM trust_work_order_steps AS a
                 WHERE a.work_order_id = s.work_order_id AND a.step = 'assigned'
                 ORDER BY a.taken_at DESC, a.created_at DESC LIMIT 1)),
       w.number, w.summary, p.display_name, u.display_name,
       d.priority, d.description, d.due_on, d.entry
FROM trust_work_order_steps AS s
JOIN numbered AS w ON w.id = s.work_order_id
JOIN trust_properties AS p ON p.id = w.property_id
LEFT JOIN trust_units AS u ON u.id = w.unit_id
LEFT JOIN LATERAL (
    SELECT d.priority, d.description, d.due_on, d.entry
    FROM trust_work_order_details AS d WHERE d.work_order_id = w.id
    ORDER BY d.created_at DESC, d.id DESC LIMIT 1
) AS d ON true
WHERE s.pmc_id = %(pmc)s AND s.id = %(step)s
"""
ENTRY = {
    "allowed": "You may let yourself in.",
    "call first": "Call {company} first to arrange entry.",
    "not allowed": "Don't go in unless someone lets you in.",
}

Sent = Literal["written", "off", "not this step", "no email on file"]


def write_work_order(conn: psycopg.Connection[Any], pmc_id: UUID, step_id: UUID) -> Sent:
    """Tells a work order's vendor it was assigned to them or their visit scheduled, as the
    company's settings ask; in the caller's transaction, once per step."""
    if not settings_of(conn, pmc_id).work_order_notices:
        return "off"
    row = conn.execute(STEP, {"pmc": pmc_id, "step": step_id}).fetchone()
    if row is None:
        raise LookupError(f"no work order step {step_id} in PMC {pmc_id}")
    step, on, note, vendor, number, summary, prop, unit, priority, description, due, entry = row
    if step not in ("assigned", "scheduled") or vendor is None:
        return "not this step"
    to = address(conn, pmc_id, "vendor", vendor)
    if to is None:
        return "no email on file"
    company = company_of(conn, pmc_id)
    name = email.PEOPLE["vendor"][0]
    vendor_name = conn.execute(name, (pmc_id, vendor)).fetchone()
    assert vendor_name is not None
    where = prop if unit is None else f"{prop}, {unit}"
    what = (
        f"{company} assigned you work order #{number}."
        if step == "assigned"
        else f"{company} scheduled your visit for work order #{number} on {us(on)}."
    )
    lines = [
        f"Where: {where}",
        f"What: {summary}",
        *([f"Details: {description}"] if description else []),
        f"Priority: {priority or 'normal'}",
        *([f"Due by: {us(due)}"] if due else []),
        *([f"Visit: {us(on)}"] if on else []),
        *([ENTRY[entry].format(company=company)] if entry else []),
        *([f"Note: {note}"] if note else []),
    ]
    subject = (
        f"Work order #{number} at {where}: {summary}"
        if step == "assigned"
        else f"Work order #{number} scheduled for {us(on)}: {summary}"
    )
    body = f"Hello {vendor_name[0]},\n\n{what}\n\n" + "\n".join(lines) + f"\n\n{company}\n"
    _insert(
        conn,
        pmc_id,
        "work_order",
        {
            "vendor_id": vendor,
            "work_order_step_id": step_id,
            "to_address": to,
            "subject": one_line(subject)[:200],
            "body": body,
        },
    )
    return "written"


# --- an applicant's adverse action notice ------------------------------------------------------

NOTICE = """
SELECT d.decision, d.conditions, a.reasons, a.agency, a.notice_given_on,
       p.display_name, u.display_name
FROM trust_adverse_actions AS a
JOIN trust_application_decisions AS d ON d.application_id = a.application_id
JOIN trust_applications AS ap ON ap.id = a.application_id
JOIN trust_application_links AS k ON k.id = ap.link_id
JOIN trust_units AS u ON u.id = k.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
WHERE a.pmc_id = %s AND a.application_id = %s
"""


def notice_text(
    decision: str, conditions: str | None, reasons: list[str], agency: str, given_on: date
) -> str:
    """What an adverse action notice says (the same as its PDF, src/pms/api/leasing.py)."""
    outcome = (
        "Your application was declined."
        if decision == "declined"
        else f"Your application was approved on conditions: {conditions}"
    )
    why = "\n".join(f"- {reason}" for reason in reasons)
    return (
        f"{outcome}\n\nWhy:\n{why}\n\n"
        f"This decision rests in whole or in part on a report from {agency}.\n"
        f"{agency} did not make this decision and can't say why it was made.\n"
        f"You may ask {agency} for a free copy of the report within 60 days of this notice.\n"
        f"You may dispute with {agency} anything in the report you believe is wrong or"
        f" incomplete.\n\n"
        f"Notice given on {us(given_on)}.\n"
    )


def write_adverse_action(conn: psycopg.Connection[Any], pmc_id: UUID, application_id: UUID) -> int:
    """Emails an application's adverse action notice to each of its applicants, as the company's
    settings ask; in the caller's transaction, once per applicant."""
    if not settings_of(conn, pmc_id).adverse_action_notices:
        return 0
    row = conn.execute(NOTICE, (pmc_id, application_id)).fetchone()
    if row is None:
        return 0
    decision, conditions, reasons, agency, given_on, prop, unit = row
    company = company_of(conn, pmc_id)
    text = notice_text(decision, conditions, list(reasons), agency, given_on)
    written = 0
    for applicant, name, to in conn.execute(
        "SELECT id, display_name, email FROM trust_applicants"
        " WHERE pmc_id = %s AND application_id = %s ORDER BY position",
        (pmc_id, application_id),
    ).fetchall():
        written += _insert(
            conn,
            pmc_id,
            "adverse_action",
            {
                "applicant_id": applicant,
                "application_id": application_id,
                "to_address": to,
                "subject": one_line(f"About your rental application: {prop}, {unit}")[:200],
                "body": f"Hello {name},\n\nAbout your application for {prop}, {unit}:\n\n"
                f"{text}\n{company}\n",
            },
        )
    return written
