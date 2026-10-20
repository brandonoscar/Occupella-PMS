"""The PMS's own signing page (ADR 0008, migration 0097; pms.leasing.esign): a signer of a lease
offer has no login, only their link (/sign#<token>). The token travels in JSON bodies, never in
a URL the server logs, and a token that matches no link reads as not found.

Through it the signer reads the consumer disclosures and agrees to sign electronically, or asks
to sign on paper (ESIGN, 15 U.S.C. 7001(c)); opens the lease, only once agreed; and signs it,
typing their name as it is on the offer and maybe drawing their signature, on the SHA-256 of the
lease they opened. Each step is recorded with the address and browser it came from. Once signed,
the signer downloads the lease and its signing certificate (pms.leasing.certificate) to keep.
Nothing here moves money, and the AI role reads none of it.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from pms import exports
from pms.api import company
from pms.api.deps import Conn, download, refusal
from pms.leasing import certificate, esign

router = APIRouter(prefix="/api/signing")

Token = Annotated[str, Field(min_length=20, max_length=100)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NOT_FOUND = "this signing link is not one of ours"
# A link's standing for its signer: open to sign, signed, asked to sign on paper, run out (30
# days after it was made), or of an offer since voided.
Status = Literal["open", "signed", "paper", "expired", "void"]
MAX_DRAWING = 100_000  # bytes, as the database checks


class SigningToken(BaseModel):
    token: Token


class Consent(BaseModel):
    token: Token
    # The SHA-256 of the disclosures the signer read, so they agree to the words they saw.
    disclosure_sha256: Sha256


class Signature(BaseModel):
    token: Token
    typed_name: str = Field(min_length=1, max_length=200)
    # The signature drawn on the page, a PNG in base64; none when the signer only typed.
    drawing_base64: str | None = Field(default=None, max_length=140_000)
    # The SHA-256 of the lease the signer opened.
    document_sha256: Sha256


class Cosigner(BaseModel):
    name: str
    role: Literal["tenant", "guarantor"]
    signed: bool


class SigningView(BaseModel):
    company: str
    document_title: str
    home: str
    starts_on: date
    ends_on: date | None
    monthly_rent: str
    signer: str
    role: Literal["tenant", "guarantor"]
    document_sha256: str
    disclosure: str
    disclosure_sha256: str
    status: Status
    consented: bool
    opened: bool
    signed_at: datetime | None
    signers: list[Cosigner]


@dataclass(frozen=True)
class Link:
    id: UUID
    pmc_id: UUID
    offer_id: UUID
    position: int
    status: Status
    consented: bool
    opened: bool
    signed_at: datetime | None


LINK = """
SELECT l.id, l.pmc_id, l.offer_id, l.position,
       l.created_at > now() - make_interval(days => %(days)s),
       v.offer_id IS NOT NULL, g.signed_at,
       EXISTS (SELECT 1 FROM trust_signing_events AS e
               WHERE e.link_id = l.id AND e.kind = 'consented'),
       EXISTS (SELECT 1 FROM trust_signing_events AS e
               WHERE e.link_id = l.id AND e.kind = 'opened'),
       EXISTS (SELECT 1 FROM trust_signing_events AS e
               WHERE e.link_id = l.id AND e.kind = 'paper')
FROM trust_signing_links AS l
LEFT JOIN trust_lease_offer_voids AS v ON v.offer_id = l.offer_id
LEFT JOIN trust_signatures AS g ON g.offer_id = l.offer_id AND g.position = l.position
WHERE l.token_sha256 = %(digest)s
"""


def link_of(conn: psycopg.Connection[Any], token: str) -> Link:
    row = conn.execute(
        LINK, {"days": esign.LINK_DAYS, "digest": esign.token_digest(token)}
    ).fetchone()
    if row is None:
        raise HTTPException(404, NOT_FOUND)
    link_id, pmc_id, offer_id, position, fresh, void, signed_at, consented, opened, paper = row
    status: Status
    if void:
        status = "void"
    elif not fresh:
        status = "expired"
    elif signed_at is not None:
        status = "signed"
    elif paper:
        status = "paper"
    else:
        status = "open"
    return Link(link_id, pmc_id, offer_id, position, status, consented, opened, signed_at)


def open_link(conn: psycopg.Connection[Any], token: str) -> Link:
    """The link, if it still signs; a 409 naming why it doesn't."""
    link = link_of(conn, token)
    if link.status == "void":
        raise HTTPException(409, "this lease offer was withdrawn; it takes no signature")
    if link.status == "expired":
        raise HTTPException(409, "this signing link has run out; ask for a new one")
    if link.status == "signed":
        raise HTTPException(409, "you have signed this lease already")
    if link.status == "paper":
        raise HTTPException(409, "you asked to sign this lease on paper")
    return link


def disclosure_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> str:
    found = company.profile(conn, pmc_id)
    address = company.address_of(found)
    where = None
    if address is not None:
        street = ", ".join(p for p in (address.address_line_1, address.address_line_2) if p)
        where = f"{street}, {address.city}, {address.state} {address.postal_code}"
    return esign.disclosure(company.name_of(conn, pmc_id), where, found["reply_to"])


def origin(request: Request) -> tuple[str | None, str | None]:
    """The address the request came from (as the server saw it; behind a proxy, the proxy's),
    and its browser, as the evidence of a step keeps them."""
    ip = None
    if request.client is not None:
        try:
            ip = str(ipaddress.ip_address(request.client.host))
        except ValueError:
            ip = None
    agent = request.headers.get("user-agent", "").strip()[:500] or None
    return ip, agent


def record(
    conn: psycopg.Connection[Any],
    request: Request,
    link: Link,
    kind: Literal["consented", "opened", "paper"],
    disclosure: str | None = None,
) -> None:
    ip, agent = origin(request)
    try:
        conn.execute(
            "INSERT INTO trust_signing_events (pmc_id, link_id, kind, disclosure, ip, user_agent)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (link_id, kind) DO NOTHING",
            (link.pmc_id, link.id, kind, disclosure, ip, agent),
        )
    except psycopg.errors.CheckViolation as refused:
        # Voided, run out or signed since it was read.
        raise HTTPException(409, refusal(refused)) from None


VIEW = """
SELECT o.document_title, p.display_name || ', ' || u.display_name, o.starts_on, o.ends_on,
       o.monthly_rent, encode(sha256(o.document), 'hex'), s.display_name, s.role
FROM trust_lease_offers AS o
JOIN trust_units AS u ON u.id = o.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
JOIN trust_lease_offer_signers AS s ON s.offer_id = o.id AND s.position = %s
WHERE o.id = %s
"""


def view_of(conn: psycopg.Connection[Any], link: Link) -> SigningView:
    row = conn.execute(VIEW, (link.position, link.offer_id)).fetchone()
    assert row is not None  # a link names a signer of its offer
    title, home, starts, ends, rent, sha256, name, role = row
    text = disclosure_of(conn, link.pmc_id)
    return SigningView(
        company=company.name_of(conn, link.pmc_id),
        document_title=title,
        home=home,
        starts_on=starts,
        ends_on=ends,
        monthly_rent=exports.text(rent),
        signer=name,
        role=role,
        document_sha256=sha256,
        disclosure=text,
        disclosure_sha256=esign.disclosure_digest(text),
        status=link.status,
        consented=link.consented,
        opened=link.opened,
        signed_at=link.signed_at,
        signers=[
            Cosigner(name=n, role=r, signed=s)
            for n, r, s in conn.execute(
                "SELECT s.display_name, s.role, g.offer_id IS NOT NULL"
                " FROM trust_lease_offer_signers AS s"
                " LEFT JOIN trust_signatures AS g"
                " ON g.offer_id = s.offer_id AND g.position = s.position"
                " WHERE s.offer_id = %s ORDER BY s.position",
                (link.offer_id,),
            ).fetchall()
        ],
    )


@router.post("/view")
def view(conn: Conn, asked: SigningToken) -> SigningView:
    """The lease offer a link signs, the disclosures to agree to, and where the signer stands."""
    return view_of(conn, link_of(conn, asked.token))


@router.post("/consent")
def consent(request: Request, conn: Conn, agreed: Consent) -> SigningView:
    """The signer agrees to receive and sign this lease electronically, having read the
    disclosures: the words they read are kept with it."""
    link = open_link(conn, agreed.token)
    text = disclosure_of(conn, link.pmc_id)
    if esign.disclosure_digest(text) != agreed.disclosure_sha256:
        raise HTTPException(409, "the disclosures changed since you read them: read them again")
    record(conn, request, link, "consented", text)
    return view_of(conn, link_of(conn, agreed.token))


@router.post("/paper")
def paper(request: Request, conn: Conn, asked: SigningToken) -> SigningView:
    """The signer would rather sign on paper (or withdraws their agreement to sign
    electronically): the link signs nothing after."""
    link = open_link(conn, asked.token)
    record(conn, request, link, "paper")
    return view_of(conn, link_of(conn, asked.token))


def lease_pdf(conn: psycopg.Connection[Any], offer: UUID) -> bytes:
    row = conn.execute("SELECT document FROM trust_lease_offers WHERE id = %s", (offer,)).fetchone()
    assert row is not None
    return bytes(row[0])


@router.post("/document")
def document(request: Request, conn: Conn, asked: SigningToken) -> Response:
    """The lease, once the signer agreed to sign electronically; the first time is recorded as
    their opening it. A signer who signed downloads it again to keep."""
    link = link_of(conn, asked.token)
    if link.status != "signed":
        link = open_link(conn, asked.token)
        if not link.consented:
            raise HTTPException(409, "agree to sign electronically first, or ask for paper")
        record(conn, request, link, "opened")
    return download(lease_pdf(conn, link.offer_id), "lease", "pdf")


@router.post("/sign")
def sign(request: Request, conn: Conn, signature: Signature) -> SigningView:
    """The signer signs the lease they opened: their name as it is on the offer, and maybe the
    signature they drew."""
    link = open_link(conn, signature.token)
    if not (link.consented and link.opened):
        raise HTTPException(409, "agree to sign electronically and open the lease first")
    name = conn.execute(
        "SELECT display_name FROM trust_lease_offer_signers WHERE offer_id = %s AND position = %s",
        (link.offer_id, link.position),
    ).fetchone()
    assert name is not None
    if signature.typed_name.strip().casefold() != str(name[0]).strip().casefold():
        raise HTTPException(422, "type your name as it is on the lease")
    drawing = None
    if signature.drawing_base64 is not None:
        try:
            drawing = base64.b64decode(signature.drawing_base64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(422, "the signature drawn isn't base64") from None
        if len(drawing) > MAX_DRAWING or not certificate.drawing_fits(drawing):
            raise HTTPException(422, "the signature drawn is a PNG of at most 100 KB")
    ip, agent = origin(request)
    try:
        conn.execute(
            "INSERT INTO trust_signature_evidence (pmc_id, offer_id, position, link_id,"
            " typed_name, drawing, document_sha256, ip, user_agent)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                link.pmc_id,
                link.offer_id,
                link.position,
                link.id,
                signature.typed_name.strip(),
                drawing,
                bytes.fromhex(signature.document_sha256),
                ip,
                agent,
            ),
        )
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        # The lease changed (never: offers are append-only), or voided or signed since.
        raise HTTPException(409, refusal(refused)) from None
    return view_of(conn, link_of(conn, signature.token))


# --- the certificate ----------------------------------------------------------------------------

OFFER = """
SELECT n.display_name, o.document_title, p.display_name || ', ' || u.display_name, o.starts_on,
       o.ends_on, o.monthly_rent, encode(sha256(o.document), 'hex'), r.sent_at,
       coalesce(st.display_name, 'the server'), v.offer_id IS NOT NULL, c.offer_id IS NOT NULL
FROM trust_lease_offers AS o
JOIN trust_pmcs AS n ON n.pmc_id = o.pmc_id
JOIN trust_units AS u ON u.id = o.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
JOIN trust_signature_requests AS r ON r.offer_id = o.id
LEFT JOIN trust_staff AS st ON st.id = r.sent_by
LEFT JOIN trust_lease_offer_voids AS v ON v.offer_id = o.id
LEFT JOIN trust_lease_offer_completions AS c ON c.offer_id = o.id
WHERE o.pmc_id = %s AND o.id = %s
"""
# Each signer and the link whose steps the certificate shows: the one they signed through, else
# the one they last did something through, else none.
SIGNERS = """
SELECT s.position, s.display_name, s.email, s.role,
       (SELECT min(l.created_at) FROM trust_signing_links AS l
        WHERE l.offer_id = s.offer_id AND l.position = s.position),
       coalesce(ev.link_id, (
           SELECT e.link_id FROM trust_signing_events AS e
           JOIN trust_signing_links AS l ON l.id = e.link_id
           WHERE l.offer_id = s.offer_id AND l.position = s.position
           ORDER BY e.created_at DESC LIMIT 1
       )),
       ev.signed_at, ev.ip, ev.user_agent, ev.typed_name, ev.drawing
FROM trust_lease_offer_signers AS s
LEFT JOIN trust_signature_evidence AS ev ON ev.offer_id = s.offer_id AND ev.position = s.position
WHERE s.offer_id = %s
ORDER BY s.position
"""


def lease_term(starts: date, ends: date | None, rent: Any) -> str:
    to = f"{ends:%m/%d/%Y}" if ends is not None else "Month to month"
    return f"{starts:%m/%d/%Y} - {to}, ${rent:,.2f} a month"


def certificate_of(
    conn: psycopg.Connection[Any], pmc_id: UUID, offer_id: UUID
) -> certificate.Certificate:
    row = conn.execute(OFFER, (pmc_id, offer_id)).fetchone()
    if row is None:
        raise HTTPException(404, "no such lease offer sent to sign")
    name, title, home, starts, ends, rent, sha256, sent_at, sent_by, void, leased = row
    signers = []
    for (
        _position,
        signer,
        email,
        role,
        first,
        link,
        signed_at,
        ip,
        agent,
        typed,
        drawing,
    ) in conn.execute(SIGNERS, (offer_id,)).fetchall():
        steps = {
            kind: certificate.Step(at, None if at_ip is None else str(at_ip), ua)
            for kind, at, at_ip, ua in conn.execute(
                "SELECT kind, created_at, ip, user_agent FROM trust_signing_events"
                " WHERE link_id = %s",
                (link,),
            ).fetchall()
        }
        disclosure = conn.execute(
            "SELECT disclosure FROM trust_signing_events WHERE link_id = %s AND kind = 'consented'",
            (link,),
        ).fetchone()
        signers.append(
            certificate.Signer(
                name=signer,
                email=email,
                role=role,
                first_link_at=first,
                consented=steps.get("consented"),
                opened=steps.get("opened"),
                paper=steps.get("paper"),
                signed=None
                if signed_at is None
                else certificate.Step(signed_at, None if ip is None else str(ip), agent),
                typed_name=typed,
                drawing=None if drawing is None else bytes(drawing),
                disclosure=None if disclosure is None else disclosure[0],
            )
        )
    done = sum(1 for s in signers if s.signed is not None)
    status = f"Signed by {done} of {len(signers)}"
    if void:
        status += "; the offer was voided"
    elif leased:
        status += "; the lease is made"
    return certificate.Certificate(
        company=name,
        document_title=title,
        home=home,
        terms=lease_term(starts, ends, rent),
        document_sha256=sha256,
        sent_at=sent_at,
        sent_by=sent_by,
        status=status,
        signers=tuple(signers),
    )


def certificate_pdf(conn: psycopg.Connection[Any], pmc_id: UUID, offer_id: UUID) -> Response:
    made = certificate.render(certificate_of(conn, pmc_id, offer_id), datetime.now(UTC))
    return download(made, "signing-certificate", "pdf")


@router.post("/certificate")
def signer_certificate(conn: Conn, asked: SigningToken) -> Response:
    """The signing certificate, once the signer has signed: theirs to keep with the lease."""
    link = link_of(conn, asked.token)
    if link.status != "signed":
        raise HTTPException(409, "the certificate is yours once you have signed")
    return certificate_pdf(conn, link.pmc_id, link.offer_id)
