"""Leasing basics, part two: lease offers, e-signing them, and completing them into leases.

Staff offer a lease to an approved application, or a renewal of a lease, with the PMC's own
lease document (a PDF uploaded as base64; the PMS writes no lease text), the security deposit it
asks and the monthly fees and credits it will owe besides rent. Leasing, Draft leases (ADR 0006)
keeps an offer as a draft until staff send it, or sends it at once: each signer signs on the
PMS's own signing page (ADR 0008, pms.leasing.esign, src/pms/api/signing.py) through a link of
their own, which staff give them (and the PMS emails while the server sends email); staff make
new links when one runs out. A signature is recorded as the signer signs, with its evidence, and
the offer's signing certificate is staff's to download. When everyone has signed, staff activate
it: trust_complete_lease_offer opens the lease, with the offer's monthly charges as its recurring
charges. Staff void an offer that is not a lease yet; it takes nothing more (migration 0071).
A renewal follows the lease it renews (migration 0152): once that lease ends before the day before
the renewal starts, was moved from or had its deposit returned, the renewal is no longer sent,
signed or made the lease, the offer says why, and staff void it; its tenants no longer see it.

Tenants see the renewal offers on their leases and get their own signing link, matched by the
email of their login.
"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from pms import accounts, exports, notice_letters
from pms import email as outgoing
from pms.api import company, notice_templates, outbox, rent, scope
from pms.api.deps import Conn, Staff, Tenant, refusal
from pms.api.leasing import public_url
from pms.api.rentals import Page, Paged, PerPage
from pms.api.signing import certificate_pdf
from pms.leasing import esign

router = APIRouter()
MAX_DOCUMENT = 5_000_000  # bytes, as the database checks
Rent = Annotated[Decimal, Field(gt=0, le=Decimal("1000000"), decimal_places=2)]
# Where an offer stands: kept as a draft, sent and signed by none, some or all of its signers,
# made the lease, or voided.
OfferStatus = Literal["draft", "sent", "partially signed", "signed", "leased", "void"]
# Which offers a list shows: those still to become a lease (draft, sent or signed), those that
# became leases, the void ones, or all.
OffersShown = Literal["open", "leased", "void", "all"]
VOIDED = "this lease offer was voided; it takes nothing more"
# A renewal that no longer follows the lease it renews, then why (trust_renewal_closed).
CLOSED = "this renewal can no longer be sent, signed or made a lease"


Role = Literal["tenant", "guarantor"]


class SignerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=254)
    # A guarantor signs and becomes the lease's guarantor, not a tenant (migration 0080).
    role: Role = "tenant"


class ChargeIn(BaseModel):
    """A fee the lease will owe every month besides rent (pet rent, parking), or a credit."""

    kind: Literal["fee", "credit"]
    memo: str = Field(min_length=1, max_length=200)
    amount: Rent


class OfferIn(BaseModel):
    starts_on: date
    ends_on: date | None = None
    monthly_rent: Rent
    # The security deposit the lease asks for, if any; held only once it is received.
    security_deposit: Rent | None = None
    recurring_charges: list[ChargeIn] = Field(default_factory=list, max_length=10)
    # The company's own lease PDF, base64 (5 MB comes to about 6.7 million characters), with its
    # title; or one of its lease templates (migration 0121), filled in from these terms.
    document_title: str | None = Field(default=None, min_length=1, max_length=200)
    document_base64: str | None = Field(default=None, min_length=8, max_length=6_700_000)
    template_id: UUID | None = None
    # Who signs; for a new lease, the application's applicants when left out.
    signers: list[SignerIn] | None = Field(default=None, min_length=1, max_length=6)
    # Send it for signing now, or keep it as a draft to send later.
    send: bool = True


class Void(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


# Where a signer stands on the signing page: nothing done yet, agreed to sign electronically,
# opened the lease, asked to sign on paper (the latest of these, through any of their links), or
# signed.
Progress = Literal["waiting", "agreed", "opened", "paper", "signed"]
PROGRESS: dict[str | None, Progress] = {
    None: "waiting",
    "consented": "agreed",
    "opened": "opened",
    "paper": "paper",
}


class SignerOut(BaseModel):
    position: int
    name: str
    email: str
    role: Role
    signed_at: datetime | None
    progress: Progress


class ChargeOut(BaseModel):
    kind: Literal["fee", "credit"]
    memo: str
    amount: str


class VoidOut(BaseModel):
    reason: str | None
    recorded_at: datetime
    # Who voided it, by name; None when the server's command line did.
    recorded_by: str | None


class OfferOut(BaseModel):
    id: UUID
    kind: Literal["new lease", "renewal"]
    application_id: UUID | None
    renews_lease_id: UUID | None
    property_id: UUID
    property: str
    unit_id: UUID
    unit: str
    starts_on: date
    ends_on: date | None
    monthly_rent: str
    security_deposit: str | None
    recurring_charges: list[ChargeOut]
    document_title: str
    offered_at: datetime
    sent: bool
    signers: list[SignerOut]
    lease_id: UUID | None
    status: OfferStatus
    void: VoidOut | None
    # A renewal, neither void nor a lease, that can no longer be sent, signed or made the lease:
    # why, in words ("the lease it renews ended on 10/15/2026"); None for any other offer.
    closed: str | None = None


class SigningLink(BaseModel):
    position: int
    name: str
    email: str
    url: str


class Offered(BaseModel):
    offer: OfferOut
    # Shown to staff to send to each signer; POST .../links makes new ones.
    links: list[SigningLink]
    # Whom the PMS emailed their link, while the server sends email (migration 0090).
    emailed: list[str] = []


class LeaseTenant(BaseModel):
    name: str
    # Their login's email, if they have a login.
    email: str | None


class LeaseOut(BaseModel):
    lease_id: UUID
    property: str
    unit: str
    starts_on: date
    ends_on: date | None
    monthly_rent: str
    tenants: list[LeaseTenant]


def document_of(offer: OfferIn) -> bytes:
    if not offer.document_title or not offer.document_title.strip():
        raise HTTPException(422, "the lease document has a title")
    try:
        document = base64.b64decode(offer.document_base64 or "", validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "the document isn't base64") from None
    if not document.startswith(b"%PDF-"):
        raise HTTPException(422, "the document is a PDF")
    if len(document) > MAX_DOCUMENT:
        raise HTTPException(422, "the document is at most 5 MB")
    return document


def signers_of(people: list[SignerIn]) -> list[tuple[str, str, Role]]:
    found = []
    for person in people:
        name, email = person.name.strip(), accounts.normalize_email(person.email)
        if not name:
            raise HTTPException(422, "every signer has a name")
        if not accounts.EMAIL.fullmatch(email):
            raise HTTPException(422, f"{email!r} isn't an email address")
        found.append((name, email, person.role))
    if len({email for _, email, _ in found}) < len(found):
        raise HTTPException(422, "each signer has their own email")
    if found and not any(role == "tenant" for _, _, role in found):
        raise HTTPException(422, "at least one signer is a tenant")
    return found


OFFERS = f"""
SELECT o.id, o.application_id, o.renews_lease_id, p.id, p.display_name, u.id, u.display_name,
       o.starts_on, o.ends_on, o.monthly_rent, o.security_deposit, o.document_title, o.offered_at,
       r.offer_id IS NOT NULL, c.lease_id, st.status,
       v.offer_id IS NOT NULL, v.reason, v.recorded_at, vs.display_name,
       CASE WHEN v.offer_id IS NULL THEN trust_renewal_closed(o.id) END, count(*) OVER ()
FROM trust_lease_offers AS o
JOIN trust_units AS u ON u.id = o.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
LEFT JOIN trust_signature_requests AS r ON r.offer_id = o.id
LEFT JOIN trust_lease_offer_completions AS c ON c.offer_id = o.id
LEFT JOIN trust_lease_offer_voids AS v ON v.offer_id = o.id
LEFT JOIN trust_staff AS vs ON vs.id = v.recorded_by
CROSS JOIN LATERAL (
    SELECT count(*) AS signers, count(g.position) AS signed
    FROM trust_lease_offer_signers AS s
    LEFT JOIN trust_signatures AS g ON g.offer_id = s.offer_id AND g.position = s.position
    WHERE s.offer_id = o.id
) AS n
CROSS JOIN LATERAL (
    SELECT CASE
        WHEN c.offer_id IS NOT NULL THEN 'leased'
        WHEN v.offer_id IS NOT NULL THEN 'void'
        WHEN r.offer_id IS NULL THEN 'draft'
        WHEN n.signed = 0 THEN 'sent'
        WHEN n.signed < n.signers THEN 'partially signed'
        ELSE 'signed'
    END AS status
) AS st
WHERE o.pmc_id = %(pmc)s AND (%(offer)s::uuid IS NULL OR o.id = %(offer)s::uuid)
  AND (%(leases)s::uuid[] IS NULL OR o.renews_lease_id = ANY(%(leases)s::uuid[]))
  AND (%(application)s::uuid IS NULL OR o.application_id = %(application)s::uuid)
  AND {scope.within("p.id")}
  AND CASE %(show)s
      WHEN 'open' THEN st.status IN ('draft', 'sent', 'partially signed', 'signed')
      WHEN 'all' THEN true
      ELSE st.status = %(show)s
  END
ORDER BY o.offered_at DESC, o.id
LIMIT %(limit)s OFFSET %(offset)s
"""  # noqa: S608


def offers_of(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    offer: UUID | None = None,
    leases: list[UUID] | None = None,
    *,
    application: UUID | None = None,
    show: OffersShown = "all",
    group: UUID | None = None,
    property_id: UUID | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> tuple[list[OfferOut], int]:
    """The PMC's offers shown (or one, or those renewing some leases or of an application),
    newest first, a page of them when given a limit; and how many there are in all."""
    rows = conn.execute(
        OFFERS,
        {
            "pmc": pmc_id,
            "offer": offer,
            "leases": leases,
            "application": application,
            "show": show,
            "limit": limit,
            "offset": offset,
            **scope.params(group, property_id),
        },
    ).fetchall()
    ids = [row[0] for row in rows]
    signers: dict[UUID, list[SignerOut]] = {i: [] for i in ids}
    for offer_id, position, name, email, role, signed_at, latest in conn.execute(
        "SELECT s.offer_id, s.position, s.display_name, s.email, s.role, g.signed_at,"
        " (SELECT e.kind FROM trust_signing_events AS e"
        "  JOIN trust_signing_links AS l ON l.id = e.link_id"
        "  WHERE l.offer_id = s.offer_id AND l.position = s.position"
        "  ORDER BY e.created_at DESC LIMIT 1)"
        " FROM trust_lease_offer_signers AS s"
        " LEFT JOIN trust_signatures AS g ON g.offer_id = s.offer_id AND g.position = s.position"
        " WHERE s.offer_id = ANY(%s) ORDER BY s.offer_id, s.position",
        (ids,),
    ).fetchall():
        signers[offer_id].append(
            SignerOut(
                position=position,
                name=name,
                email=email,
                role=role,
                signed_at=signed_at,
                progress="signed" if signed_at is not None else PROGRESS[latest],
            )
        )
    charges: dict[UUID, list[ChargeOut]] = {i: [] for i in ids}
    for offer_id, kind, memo, amount in conn.execute(
        "SELECT offer_id, kind, memo, amount FROM trust_lease_offer_charges"
        " WHERE offer_id = ANY(%s) ORDER BY offer_id, position",
        (ids,),
    ).fetchall():
        charges[offer_id].append(ChargeOut(kind=kind, memo=memo, amount=exports.text(amount)))
    found = [
        OfferOut(
            id=i,
            kind="new lease" if application else "renewal",
            application_id=application,
            renews_lease_id=renews,
            property_id=prop_id,
            property=prop,
            unit_id=unit_id,
            unit=unit,
            starts_on=starts,
            ends_on=ends,
            monthly_rent=exports.text(rent),
            security_deposit=None if deposit is None else exports.text(deposit),
            recurring_charges=charges[i],
            document_title=title,
            offered_at=at,
            sent=sent,
            signers=signers[i],
            lease_id=lease,
            status=status,
            void=VoidOut(reason=why, recorded_at=voided_at, recorded_by=voided_by)
            if voided
            else None,
            closed=closed,
        )
        for (
            i,
            application,
            renews,
            prop_id,
            prop,
            unit_id,
            unit,
            starts,
            ends,
            rent,
            deposit,
            title,
            at,
            sent,
            lease,
            status,
            voided,
            why,
            voided_at,
            voided_by,
            closed,
            _,
        ) in rows
    ]
    return found, rows[0][-1] if rows else 0


def offer_of(conn: psycopg.Connection[Any], pmc_id: UUID, offer: UUID) -> OfferOut:
    found, _ = offers_of(conn, pmc_id, offer)
    if not found:
        raise HTTPException(404, "no such lease offer")
    return found[0]


def links_of(
    conn: psycopg.Connection[Any],
    request: Request,
    pmc_id: UUID,
    offer: UUID,
    signers: list[SignerOut],
) -> list[SigningLink]:
    """A new signing link for each of the signers, in the request's transaction."""
    try:
        return [
            SigningLink(
                position=s.position,
                name=s.name,
                email=s.email,
                url=esign.new_link(conn, pmc_id, offer, s.position, public_url(request)),
            )
            for s in signers
        ]
    except psycopg.errors.CheckViolation as refused:
        # Voided, made the lease or signed since it was read.
        raise HTTPException(409, refusal(refused)) from None


def still_renews(found: OfferOut) -> None:
    """A 409 saying why, for a renewal that no longer follows the lease it renews."""
    if found.closed is not None:
        raise HTTPException(409, f"{CLOSED}: {found.closed}")


def sent_here(conn: psycopg.Connection[Any], offer: UUID) -> None:
    """A 409 unless the offer was sent to sign on the PMS's own page."""
    row = conn.execute(
        "SELECT partner FROM trust_signature_requests WHERE offer_id = %s", (offer,)
    ).fetchone()
    if row is None:
        raise HTTPException(409, "this offer is a draft: send it to its signers first")
    if row[0] != esign.PARTNER:
        raise HTTPException(
            409,
            "this offer was sent to an e-sign partner the PMS no longer uses: void it and"
            " send a new one",
        )


def send_offer(
    conn: psycopg.Connection[Any], request: Request, session: accounts.Session, offer: UUID
) -> Offered:
    """Sends an offer to its signers to sign on the PMS's page: records the request and makes
    each signer a link."""
    try:
        conn.execute(
            "INSERT INTO trust_signature_requests (pmc_id, offer_id, partner, partner_reference,"
            " sent_by) VALUES (%s, %s, %s, %s, %s)",
            (session.pmc_id, offer, esign.PARTNER, esign.reference(offer), session.person_id),
        )
    except psycopg.errors.CheckViolation as refused:
        # Voided since it was read.
        raise HTTPException(409, refusal(refused)) from None
    found = offer_of(conn, session.pmc_id, offer)
    links = links_of(conn, request, session.pmc_id, offer, found.signers)
    emailed = []
    if outbox.SENDING.get():
        what = f"the lease for {found.property}, {found.unit}"
        for link in links:
            if outgoing.write_signing_request(
                conn, session.pmc_id, offer, link.position, link.name, link.email, what, link.url
            ):
                emailed.append(link.email)
    return Offered(offer=found, links=links, emailed=emailed)


# The unit's property and name.
UNIT = """
SELECT p.display_name, u.display_name FROM trust_units AS u
JOIN trust_properties AS p ON p.id = u.property_id WHERE u.id = %s
"""


def from_template(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    template: notice_templates.Template,
    new: OfferIn,
    unit: UUID,
    people: list[tuple[str, str, Role]],
) -> bytes:
    """A lease template filled in from a draft lease's terms, as the PDF it sends for signing."""
    row = conn.execute(UNIT, (unit,)).fetchone()
    assert row is not None
    dollars = notice_templates.dollars
    us = notice_letters.us
    guarantors = [name for name, _, role in people if role == "guarantor"]
    charges = [
        f"{c.memo.strip()}, {dollars(c.amount)} a month"
        + (" (a credit)" if c.kind == "credit" else "")
        for c in new.recurring_charges
    ]
    today = rent.today()
    street = company.street_of(conn, pmc_id)
    values = {
        "tenant_names": notice_letters.names([n for n, _, role in people if role == "tenant"]),
        "guarantor_names": notice_letters.names(guarantors) if guarantors else "none",
        "property": row[0],
        "unit": row[1],
        "monthly_rent": dollars(new.monthly_rent),
        "security_deposit": dollars(new.security_deposit) if new.security_deposit else "none",
        "monthly_charges": "; ".join(charges) if charges else "none",
        "lease_start": us(new.starts_on),
        "lease_end": "month to month" if new.ends_on is None else us(new.ends_on),
        "lease_date": us(today),
        "company_name": company.name_of(conn, pmc_id),
        "company_address": street,
    }
    letterhead = values["company_name"] if not street else f"{values['company_name']}\n{street}"
    letter = notice_letters.Letter(
        template.name, letterhead, today, notice_letters.fill(template.body, values)
    )
    return notice_letters.render(letter, datetime.now(UTC))


def make_offer(
    request: Request,
    session: accounts.Session,
    conn: psycopg.Connection[Any],
    new: OfferIn,
    *,
    application: UUID | None,
    renews: UUID | None,
    unit: UUID,
    default_signers: list[SignerIn],
) -> Offered:
    if new.ends_on is not None and new.ends_on < new.starts_on:
        raise HTTPException(422, "a lease ends on or after the day it starts")
    if (new.template_id is None) == (new.document_base64 is None):
        raise HTTPException(
            422, "send the company's lease PDF or name a lease template, one of them"
        )
    people = signers_of(new.signers or default_signers)
    if not people:
        raise HTTPException(422, "name who signs")
    charges = [(c.kind, c.memo.strip(), c.amount) for c in new.recurring_charges]
    if not all(memo for _, memo, _ in charges):
        raise HTTPException(422, "every monthly charge says what it is for")
    template = None
    if new.template_id is not None:
        template = notice_templates.one(conn, session.pmc_id, new.template_id, "lease")
        title = template.name
        document = from_template(conn, session.pmc_id, template, new, unit, people)
    else:
        document = document_of(new)
        title = (new.document_title or "").strip()
    try:
        row = conn.execute(
            "INSERT INTO trust_lease_offers (pmc_id, application_id, renews_lease_id, unit_id,"
            " starts_on, ends_on, monthly_rent, security_deposit, document_title, document,"
            " offered_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                session.pmc_id,
                application,
                renews,
                unit,
                new.starts_on,
                new.ends_on,
                new.monthly_rent,
                new.security_deposit,
                title,
                document,
                session.person_id,
            ),
        ).fetchone()
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, refusal(refused)) from None
    assert row is not None
    offer_id = row[0]
    if template is not None:
        conn.execute(
            "INSERT INTO trust_lease_offer_templates (pmc_id, offer_id, template_version_id)"
            " VALUES (%s, %s, %s)",
            (session.pmc_id, offer_id, template.version_id),
        )
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO trust_lease_offer_signers (pmc_id, offer_id, position, display_name,"
            " email, role) VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (session.pmc_id, offer_id, n, name, email, role)
                for n, (name, email, role) in enumerate(people, 1)
            ],
        )
        cur.executemany(
            "INSERT INTO trust_lease_offer_charges (pmc_id, offer_id, position, kind, memo,"
            " amount) VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (session.pmc_id, offer_id, n, kind, memo, amount)
                for n, (kind, memo, amount) in enumerate(charges, 1)
            ],
        )
    if not new.send:
        return Offered(offer=offer_of(conn, session.pmc_id, offer_id), links=[])
    return send_offer(conn, request, session, offer_id)


# --- staff --------------------------------------------------------------------------------


@router.post("/api/leasing/applications/{application_id}/offer", status_code=201)
def offer_lease(
    request: Request, session: Staff, conn: Conn, application_id: UUID, new: OfferIn
) -> Offered:
    """A lease for an approved application, at the unit it applied for, sent for signing."""
    row = conn.execute(
        "SELECT l.unit_id FROM trust_applications AS a"
        " JOIN trust_application_links AS l ON l.id = a.link_id"
        " WHERE a.pmc_id = %s AND a.id = %s",
        (session.pmc_id, application_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such application")
    applicants = [
        SignerIn(name=name, email=email)
        for name, email in conn.execute(
            "SELECT display_name, email FROM trust_applicants WHERE application_id = %s"
            " ORDER BY position",
            (application_id,),
        ).fetchall()
    ]
    return make_offer(
        request,
        session,
        conn,
        new,
        application=application_id,
        renews=None,
        unit=row[0],
        default_signers=applicants,
    )


@router.get("/api/leasing/leases")
def leases(session: Staff, conn: Conn) -> list[LeaseOut]:
    """The PMC's leases that haven't ended, to renew; with their tenants' login emails."""
    rows = conn.execute(
        "SELECT l.id, p.display_name, u.display_name, l.starts_on, l.ends_on, l.monthly_rent,"
        " array(SELECT jsonb_build_object('name', t.display_name, 'email', g.email)"
        "       FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id"
        "       LEFT JOIN trust_logins AS g"
        "         ON g.tenant_id IN (SELECT trust_tenant_records(t.pmc_id, t.id))"
        '       WHERE lt.lease_id = l.id ORDER BY t.display_name COLLATE "C", t.id)'
        " FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id"
        " JOIN trust_properties AS p ON p.id = u.property_id"
        " WHERE l.pmc_id = %s AND (l.ends_on IS NULL OR l.ends_on >= current_date)"
        ' ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C", l.starts_on',
        (session.pmc_id,),
    ).fetchall()
    return [
        LeaseOut(
            lease_id=i,
            property=p,
            unit=u,
            starts_on=s,
            ends_on=e,
            monthly_rent=exports.text(rent),
            tenants=[LeaseTenant.model_validate(person) for person in people],
        )
        for i, p, u, s, e, rent, people in rows
    ]


@router.post("/api/leasing/leases/{lease_id}/renewal", status_code=201)
def offer_renewal(
    request: Request, session: Staff, conn: Conn, lease_id: UUID, new: OfferIn
) -> Offered:
    """The renewal of a lease, at its unit, sent to the signers staff name."""
    row = conn.execute(
        "SELECT unit_id FROM trust_leases WHERE pmc_id = %s AND id = %s",
        (session.pmc_id, lease_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such lease")
    if not new.signers:
        raise HTTPException(422, "name who signs the renewal")
    return make_offer(
        request,
        session,
        conn,
        new,
        application=None,
        renews=lease_id,
        unit=row[0],
        default_signers=[],
    )


@router.get("/api/leasing/offers")
def offers(
    session: Staff,
    conn: Conn,
    show: Annotated[OffersShown, Query()] = "open",
    application: Annotated[UUID | None, Query()] = None,
    renews: Annotated[UUID | None, Query()] = None,
    group: scope.Group = None,
    property_id: scope.Property = None,
    page: Page = 1,
    per_page: PerPage = 50,
) -> Paged[OfferOut]:
    """The PMC's lease offers, newest first, a page at a time: by default those still to become
    a lease (drafts, sent, signed); or the ones that became leases, the void ones, or all. Maybe
    of one application or renewing one lease (the lease page's End lease reads them), or of a
    property group or property."""
    found, total = offers_of(
        conn,
        session.pmc_id,
        None,
        None if renews is None else [renews],
        application=application,
        show=show,
        group=group,
        property_id=property_id,
        limit=per_page,
        offset=(page - 1) * per_page,
    )
    return Paged(items=found, total=total, page=page, per_page=per_page)


@router.get("/api/leasing/offers/{offer_id}")
def offer(session: Staff, conn: Conn, offer_id: UUID) -> OfferOut:
    """One of the PMC's lease offers, for its own page."""
    return offer_of(conn, session.pmc_id, offer_id)


@router.post("/api/leasing/offers/{offer_id}/send")
def send(request: Request, session: Staff, conn: Conn, offer_id: UUID) -> Offered:
    """Sends a draft offer to its signers to sign on the PMS's page: the links to give them."""
    found = offer_of(conn, session.pmc_id, offer_id)
    if found.status == "void":
        raise HTTPException(409, VOIDED)
    if found.status != "draft":
        raise HTTPException(409, "this offer was sent already; new links are under Resend")
    still_renews(found)
    return send_offer(conn, request, session, offer_id)


@router.post("/api/leasing/offers/{offer_id}/void")
def void(session: Staff, conn: Conn, offer_id: UUID, why: Void) -> OfferOut:
    """Voids an offer that is not a lease yet, and why: it is never sent, signed or made the
    lease after. Signers who have its links can no longer sign it here."""
    found = offer_of(conn, session.pmc_id, offer_id)
    if found.status == "void":
        raise HTTPException(409, "this offer is voided already")
    if found.status == "leased":
        raise HTTPException(409, "this offer is a lease already")
    try:
        conn.execute(
            "INSERT INTO trust_lease_offer_voids (pmc_id, offer_id, reason) VALUES (%s, %s, %s)",
            (session.pmc_id, offer_id, (why.reason or "").strip() or None),
        )
    except psycopg.errors.CheckViolation as refused:
        # Made the lease since it was read.
        raise HTTPException(409, refusal(refused)) from None
    return offer_of(conn, session.pmc_id, offer_id)


@router.post("/api/leasing/offers/{offer_id}/links")
def links(request: Request, session: Staff, conn: Conn, offer_id: UUID) -> list[SigningLink]:
    """A new link for each signer who hasn't signed, to send once more (the links sent before
    keep working until they run out, 30 days after they were made)."""
    found = offer_of(conn, session.pmc_id, offer_id)
    if found.status == "void":
        raise HTTPException(409, VOIDED)
    if found.status == "leased":
        raise HTTPException(409, "this offer is a lease already")
    still_renews(found)
    sent_here(conn, offer_id)
    waiting = [s for s in found.signers if s.signed_at is None]
    return links_of(conn, request, session.pmc_id, offer_id, waiting)


@router.get("/api/leasing/offers/{offer_id}/certificate")
def offer_certificate(session: Staff, conn: Conn, offer_id: UUID) -> Response:
    """The offer's signing certificate: what each signer did on the signing page, so far."""
    offer_of(conn, session.pmc_id, offer_id)
    sent_here(conn, offer_id)
    return certificate_pdf(conn, session.pmc_id, offer_id)


@router.post("/api/leasing/offers/{offer_id}/complete")
def complete(session: Staff, conn: Conn, offer_id: UUID) -> OfferOut:
    """Opens the offer's lease once everyone has signed (trust_complete_lease_offer), with the
    offer's monthly charges as its recurring charges."""
    found = offer_of(conn, session.pmc_id, offer_id)
    if found.status == "void":
        raise HTTPException(409, VOIDED)
    if found.status == "draft":
        raise HTTPException(409, "this offer is a draft: send it to its signers first")
    still_renews(found)
    try:
        conn.execute(
            "SELECT trust_complete_lease_offer(%s, %s, %s)",
            (session.pmc_id, offer_id, session.person_id),
        )
    except (psycopg.errors.CheckViolation, psycopg.errors.ExclusionViolation) as refused:
        raise HTTPException(409, refusal(refused)) from None
    keep_due_day(conn, session.pmc_id, offer_id)
    return offer_of(conn, session.pmc_id, offer_id)


# A renewal's lease, the day of the month the lease it renews had its rent due as the renewal
# starts, and the first month of the renewal with no rent charged yet.
RENEWED_DUE_DAY = """
SELECT c.lease_id, trust_rent_due_day(o.renews_lease_id, n.starts_on),
       (SELECT min(m)::date FROM generate_series(
            date_trunc('month', n.starts_on::timestamp),
            date_trunc('month', n.starts_on::timestamp) + interval '1 month', interval '1 month'
        ) AS m
        WHERE NOT EXISTS (
            SELECT 1 FROM trust_charges AS k
            WHERE k.lease_id = c.lease_id AND k.kind = 'rent'
              AND date_trunc('month', k.due_on::timestamp) >= m))
FROM trust_lease_offers AS o
JOIN trust_lease_offer_completions AS c ON c.offer_id = o.id
JOIN trust_leases AS n ON n.id = c.lease_id
WHERE o.pmc_id = %s AND o.id = %s AND o.renews_lease_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM trust_rent_due_days AS d WHERE d.lease_id = c.lease_id)
"""


def keep_due_day(conn: psycopg.Connection[Any], pmc_id: UUID, offer_id: UUID) -> None:
    """A renewal keeps the rent due day of the lease it renews (migration 0081), from its first
    month with no rent charged yet."""
    row = conn.execute(RENEWED_DUE_DAY, (pmc_id, offer_id)).fetchone()
    if row is None or row[1] == 1 or row[2] is None:
        return
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_rent_due_days (pmc_id, lease_id, starts_on, due_day)"
                " VALUES (%s, %s, %s, %s)",
                (pmc_id, row[0], row[2], row[1]),
            )
    except psycopg.errors.CheckViolation:
        pass  # a renewal too short to reach that month: it keeps the first


def document_response(conn: psycopg.Connection[Any], offer: UUID) -> Response:
    row = conn.execute("SELECT document FROM trust_lease_offers WHERE id = %s", (offer,)).fetchone()
    assert row is not None
    return Response(
        bytes(row[0]),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="lease.pdf"'},
    )


@router.get("/api/leasing/offers/{offer_id}/document")
def offer_document(session: Staff, conn: Conn, offer_id: UUID) -> Response:
    offer_of(conn, session.pmc_id, offer_id)
    return document_response(conn, offer_id)


# --- tenants ------------------------------------------------------------------------------


def tenant_leases(conn: psycopg.Connection[Any], session: accounts.Session) -> list[UUID]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT lease_id FROM trust_lease_tenants WHERE pmc_id = %s"
            " AND tenant_id IN (SELECT trust_tenant_records(%s, %s))",
            (session.pmc_id, session.pmc_id, session.person_id),
        ).fetchall()
    ]


def tenant_offer(
    conn: psycopg.Connection[Any], session: accounts.Session, offer_id: UUID
) -> OfferOut:
    """A renewal offer sent to sign on one of the tenant's leases, or 404 for any other (a draft,
    a void one or one that no longer follows the lease it renews is not the tenant's to see)."""
    found = [
        o
        for o in offers_of(conn, session.pmc_id, offer_id, tenant_leases(conn, session))[0]
        if o.status not in ("draft", "void") and o.closed is None
    ]
    if not found:
        raise HTTPException(404, "no such renewal offer of yours")
    return found[0]


@router.get("/api/tenant/renewals")
def tenant_renewals(session: Tenant, conn: Conn) -> list[OfferOut]:
    """Renewal offers sent to sign on the tenant's leases that aren't a lease yet, nor void, and
    still follow the lease they renew (one ended before the renewal, moved from or whose deposit
    was returned shows none)."""
    found, _ = offers_of(conn, session.pmc_id, None, tenant_leases(conn, session), show="open")
    return [o for o in found if o.status != "draft" and o.closed is None]


@router.post("/api/tenant/renewals/{offer_id}/signing-link")
def tenant_signing_link(
    request: Request, session: Tenant, conn: Conn, offer_id: UUID
) -> SigningLink:
    """The tenant's own link to sign a renewal: the signer with their login's email."""
    offer = tenant_offer(conn, session, offer_id)
    if offer.lease_id is not None:
        raise HTTPException(409, "this renewal is a lease already")
    mine = [s for s in offer.signers if s.email == session.email]
    if not mine:
        raise HTTPException(403, "you aren't one of this renewal's signers")
    if mine[0].signed_at is not None:
        raise HTTPException(409, "you have signed this renewal already")
    sent_here(conn, offer_id)
    (link,) = links_of(conn, request, session.pmc_id, offer_id, mine)
    return link


@router.get("/api/tenant/renewals/{offer_id}/document")
def tenant_renewal_document(session: Tenant, conn: Conn, offer_id: UUID) -> Response:
    tenant_offer(conn, session, offer_id)
    return document_response(conn, offer_id)
