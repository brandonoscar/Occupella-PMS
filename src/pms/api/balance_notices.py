"""Balance notices (migration 20261023000201; the founder, 2026-10-23): from the rent roll or the
delinquency report, staff pick leases that owe (or all of them) and email each tenant a balance
notice made from one of the company's own notice templates (src/pms/api/notice_templates.py),
listing what is open by charge, with a printable PDF of every notice of the send.

A balance notice is a reminder, not the legal notice for nonpayment, and it says so at its end. A
template with pay-or-quit or eviction words (LEGAL_WORDS) is refused for it: the company makes its
legal notice from its template on the lease's page and delivers it as its state requires. Most
states want that notice delivered by mail, posting or hand; email counts only where a statute
allows it with the tenant's written consent (EMAIL_WITH_CONSENT, NOT VERIFIED against the
statutes' text; docs/law.md), and the PMS records no such consent, so the page says how the legal
notice goes for each property's state. A mailing service is a later add-on.

Each tenant's email is a staff member's message naming its notice, so it shows on the tenant's
Email tab as a balance notice. Staff need the 'email' area to send (custom roles). The AI role
reads none of it. Nothing here moves money.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from pms import email, notice_letters, notices, texts
from pms.api import notice_templates, rent, scope
from pms.api.deps import Conn, Staff, download

router = APIRouter(prefix="/api/balance-notices")

# Said at the end of every balance notice, in the email and on paper.
REMINDER = (
    "This is a reminder of the balance on your account. It is not a notice to pay rent or quit,"
    " a notice of default or a notice of eviction."
)
# Words that belong in the legal notice for nonpayment, never in a reminder.
LEGAL_WORDS = re.compile(
    r"\b(quit|vacate|evict\w*|possession|terminat\w*|unlawful detainer|forcible|court|lawsuit"
    r"|legal action|notice to pay)\b",
    re.IGNORECASE,
)


# States where email may serve the legal notice for nonpayment only with the tenant's written
# consent, and from when: their statute as research found it, NOT VERIFIED against its text
# (docs/law.md, Balance notices).
class EmailRule(BaseModel):
    statute: str
    # In force from this day; None: since before any book the PMS keeps (its day not known here).
    since: date | None


EMAIL_WITH_CONSENT: dict[str, EmailRule] = {
    "FL": EmailRule(statute="Fla. Stat. 83.505", since=date(2025, 7, 1)),
    "TX": EmailRule(
        statute="Tex. Prop. Code 24.005, as amended by S.B. 38", since=date(2026, 1, 1)
    ),
    "MI": EmailRule(statute="Mich. Comp. Laws 600.5718(1)(d)", since=None),
}


class Delivery(BaseModel):
    """How the legal notice for nonpayment goes in the property's state, as the page says it."""

    state: str | None
    # Whether email may serve it there at all (with the tenant's written consent), or None when
    # the property's state isn't on file.
    email_may_count: bool | None
    words: str


def delivery(state: str | None, day: date) -> Delivery:
    if state is None:
        words = (
            "The property's state isn't on file. In most states the legal notice for nonpayment"
            " must be delivered by mail, posting or hand; this email is a reminder only."
        )
        return Delivery(state=None, email_may_count=None, words=words)
    rule = EMAIL_WITH_CONSENT.get(state)
    if rule is None or (rule.since is not None and day < rule.since):
        words = (
            f"In {state} the legal notice for nonpayment goes by mail, posting or hand as its"
            " statute says, not by email: this email is a reminder only. Print the notice to"
            " deliver it, or make your legal notice from your template on the lease's page."
        )
        return Delivery(state=state, email_may_count=False, words=words)
    words = (
        f"In {state} email may serve the legal notice for nonpayment only with the tenant's"
        f" written consent ({rule.statute}); the PMS records no such consent, and this email is a"
        " reminder only. Check the statute before relying on email."
    )
    return Delivery(state=state, email_may_count=True, words=words)


class Tenant(BaseModel):
    id: UUID
    name: str
    # Where their email goes, or None: no email on file.
    email: str | None


class Owing(BaseModel):
    lease_id: UUID
    property: str
    unit: str
    tenants: list[Tenant]
    owed: str
    delivery: Delivery


# The leases of the PMC that owe today, with their property's state (its latest address).
OWING = f"""
SELECT l.id, p.display_name, u.display_name,
       (SELECT a.state FROM trust_property_addresses AS a WHERE a.property_id = p.id
        ORDER BY a.created_at DESC, a.id DESC LIMIT 1)
FROM trust_leases AS l
JOIN trust_units AS u ON u.id = l.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
WHERE l.pmc_id = %(pmc)s AND {scope.within("p.id")}
  AND (%(lease)s::uuid IS NULL OR l.id = %(lease)s)
  AND EXISTS (SELECT 1 FROM trust_open_charges(%(pmc)s, l.id, 'infinity') AS o WHERE o.open > 0)
ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C", l.id
"""  # noqa: S608

# A lease's tenants who hadn't left it by today.
TENANTS = """
SELECT t.id, t.display_name FROM trust_lease_tenants AS lt
JOIN trust_tenants AS t ON t.id = lt.tenant_id
WHERE lt.lease_id = %(lease)s AND NOT EXISTS (
    SELECT 1 FROM trust_lease_departures AS d
    WHERE d.lease_id = lt.lease_id AND d.tenant_id = lt.tenant_id AND d.left_on <= %(day)s
)
ORDER BY t.display_name COLLATE "C", t.id
"""


def owing(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    day: date,
    group: UUID | None = None,
    property_id: UUID | None = None,
    lease_id: UUID | None = None,
) -> list[Owing]:
    found = []
    for lease, prop, unit, state in conn.execute(
        OWING, {"pmc": pmc_id, "lease": lease_id, **scope.params(group, property_id)}
    ).fetchall():
        tenants = [
            Tenant(id=t, name=n, email=notices.address(conn, pmc_id, "tenant", t))
            for t, n in conn.execute(TENANTS, {"lease": lease, "day": day}).fetchall()
        ]
        found.append(
            Owing(
                lease_id=lease,
                property=prop,
                unit=unit,
                tenants=tenants,
                owed=f"{rent.owed(conn, lease):.2f}",
                delivery=delivery(state, day),
            )
        )
    return found


@router.get("/owing")
def leases_owing(
    session: Staff,
    conn: Conn,
    group: scope.Group = None,
    property_id: scope.Property = None,
) -> list[Owing]:
    """The leases that owe today, each with its tenants, where their email goes and how the legal
    notice for nonpayment goes in its property's state: whom a balance notice may go to."""
    return owing(conn, session.pmc_id, rent.today(), group, property_id)


class NewRun(BaseModel):
    template_id: UUID
    dated_on: date
    lease_ids: Annotated[list[UUID], Field(min_length=1, max_length=500)]
    # Text a reminder of it, too, to each tenant who agreed to texts (while the server sends
    # texts; migration 20261023000204).
    text: bool = False


class Sent(BaseModel):
    lease_id: UUID
    notice_id: UUID
    property: str
    unit: str
    owed: str
    emailed: list[Tenant]
    # Tenants with no email on file: their notice is to print and deliver.
    without_email: list[str]
    # Tenants texted a reminder of it (those who agreed to texts, when staff asked).
    texted: list[str] = []
    delivery: Delivery


class Run(BaseModel):
    id: UUID
    sent: list[Sent]


def legal_words(body: str) -> list[str]:
    seen: list[str] = []
    for word in (found.group(0).lower() for found in LEGAL_WORDS.finditer(body)):
        if word not in seen:
            seen.append(word)
    return seen


@router.post("", status_code=201)
def send(session: Staff, conn: Conn, asked: NewRun) -> Run:
    """Makes a balance notice for each lease picked, from the template, dated the day given, and
    writes each of its tenants with an email on file an email of it; it goes out after the
    request commits (or waits in the outbox while the server sends none)."""
    if len(set(asked.lease_ids)) != len(asked.lease_ids):
        raise HTTPException(422, "a lease is picked twice")
    template = notice_templates.one(conn, session.pmc_id, asked.template_id, "notice")
    words = legal_words(template.body)
    if words:
        raise HTTPException(
            422,
            f"{template.name} reads like the legal notice for nonpayment ({', '.join(words)}):"
            " a balance notice is a reminder, so pick a template without those words",
        )
    run = conn.execute(
        "INSERT INTO trust_balance_notice_runs (pmc_id, template_version_id, dated_on)"
        " VALUES (%s, %s, %s) RETURNING id",
        (session.pmc_id, template.version_id, asked.dated_on),
    ).fetchone()
    assert run is not None
    text = asked.text and texts.TEXTING.get()
    sent = [one_notice(conn, session, run[0], template, lease, asked.dated_on, text)
            for lease in asked.lease_ids]  # fmt: skip
    return Run(id=run[0], sent=sent)


def one_notice(
    conn: psycopg.Connection[Any],
    session: Any,
    run_id: UUID,
    template: notice_templates.Template,
    lease_id: UUID,
    dated_on: date,
    text: bool = False,
) -> Sent:
    found = owing(conn, session.pmc_id, rent.today(), lease_id=lease_id)
    if not found:
        exists = conn.execute(
            "SELECT 1 FROM trust_leases WHERE pmc_id = %s AND id = %s", (session.pmc_id, lease_id)
        ).fetchone()
        if exists is None:
            raise HTTPException(404, "no such lease")
        raise HTTPException(409, "a lease picked owes nothing now: no balance notice goes to it")
    lease = found[0]
    _, made = notice_templates.filled(
        conn,
        session.pmc_id,
        session.person_id,
        notice_templates.NewNotice(lease_id=lease_id, template_id=template.id, dated_on=dated_on),
    )
    body = made.body
    if "{{open_charges}}" not in template.body.replace(" ", ""):
        body = f"{body.rstrip()}\n\n{notice_templates.open_lines(conn, session.pmc_id, lease_id)}"
    body = f"{body.rstrip()}\n\n{REMINDER}"
    notice = conn.execute(
        "INSERT INTO trust_notices (pmc_id, lease_id, template_version_id, dated_on, title,"
        " letterhead, body) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (session.pmc_id, lease_id, template.version_id, dated_on, made.title, made.letterhead,
         body),
    ).fetchone()  # fmt: skip
    assert notice is not None
    conn.execute(
        "INSERT INTO trust_balance_notices (notice_id, pmc_id, run_id, lease_id, owed)"
        " VALUES (%s, %s, %s, %s, %s)",
        (notice[0], session.pmc_id, run_id, lease_id, Decimal(lease.owed)),
    )
    subject = f"{made.title}: {lease.property}, {lease.unit}"[:200]
    emailed = [t for t in lease.tenants if t.email is not None]
    for tenant in emailed:
        assert tenant.email is not None
        email.write_notice(
            conn, session.pmc_id, tenant.id, tenant.email, subject, body, session.person_id,
            notice[0],
        )  # fmt: skip
    texted = [
        t.name
        for t in lease.tenants
        if text and texts.write_balance_notice(conn, session.pmc_id, notice[0], lease_id, t.id)
    ]
    return Sent(
        lease_id=lease_id,
        notice_id=notice[0],
        property=lease.property,
        unit=lease.unit,
        owed=lease.owed,
        emailed=emailed,
        without_email=[t.name for t in lease.tenants if t.email is None],
        texted=texted,
        delivery=lease.delivery,
    )


@router.get("/runs/{run_id}/pdf")
def run_pdf(session: Staff, conn: Conn, run_id: UUID) -> Response:
    """Every notice of a send, a letter each, to print and deliver: the same words whenever it is
    downloaded."""
    rows = conn.execute(
        "SELECT n.title, n.letterhead, n.dated_on, n.body FROM trust_balance_notices AS b"
        " JOIN trust_notices AS n ON n.id = b.notice_id"
        " JOIN trust_leases AS l ON l.id = b.lease_id"
        " JOIN trust_units AS u ON u.id = l.unit_id"
        " JOIN trust_properties AS p ON p.id = u.property_id"
        " WHERE b.pmc_id = %s AND b.run_id = %s"
        ' ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C", b.lease_id',
        (session.pmc_id, run_id),
    ).fetchall()
    if not rows:
        raise HTTPException(404, "no such send of balance notices")
    letters = [notice_letters.Letter(t, head, on, body) for t, head, on, body in rows]
    return download(
        notice_letters.render_all(letters, datetime.now(UTC)), f"balance-notices-{run_id}", "pdf"
    )
