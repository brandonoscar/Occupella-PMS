"""Leasing basics, part two: lease offers, e-signing them, and completing them into leases.

Staff offer a lease to an approved application, or a renewal of a lease, with the PMC's own
lease document (a PDF uploaded as base64; the PMS writes no lease text). The offer goes to the
server's e-sign partner (pms.leasing.esign) for its signers, and staff send each signer their
link. Signatures are recorded as the partner reports them. When everyone has signed, staff
complete the offer: trust_complete_lease_offer opens the lease.

Tenants see the renewal offers on their leases and get their own signing link, matched by the
email of their login.
"""

from __future__ import annotations

import base64
import binascii
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from pms import accounts, exports
from pms.api.deps import Conn, Staff, Tenant
from pms.leasing import esign

router = APIRouter()
MAX_DOCUMENT = 5_000_000  # bytes, as the database checks
Rent = Annotated[Decimal, Field(gt=0, le=Decimal("1000000"), decimal_places=2)]


class SignerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=254)


class OfferIn(BaseModel):
    starts_on: date
    ends_on: date | None = None
    monthly_rent: Rent
    document_title: str = Field(min_length=1, max_length=200)
    # The PDF, base64: 5 MB comes to about 6.7 million characters.
    document_base64: str = Field(min_length=8, max_length=6_700_000)
    # Who signs; for a new lease, the application's applicants when left out.
    signers: list[SignerIn] | None = Field(default=None, min_length=1, max_length=6)


class SignerOut(BaseModel):
    position: int
    name: str
    email: str
    signed_at: datetime | None


class OfferOut(BaseModel):
    id: UUID
    kind: Literal["new lease", "renewal"]
    application_id: UUID | None
    renews_lease_id: UUID | None
    property: str
    unit: str
    starts_on: date
    ends_on: date | None
    monthly_rent: str
    document_title: str
    offered_at: datetime
    sent: bool
    signers: list[SignerOut]
    lease_id: UUID | None


class SigningLink(BaseModel):
    position: int
    name: str
    email: str
    url: str


class Offered(BaseModel):
    offer: OfferOut
    # Shown to staff to send to each signer; GET .../links gives them again.
    links: list[SigningLink]


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


def partner_of(request: Request) -> esign.Partner:
    partner: esign.Partner | None = request.app.state.esign
    if partner is None:
        raise HTTPException(503, "no e-sign partner is set up on this server")
    return partner


def document_of(offer: OfferIn) -> bytes:
    try:
        document = base64.b64decode(offer.document_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "the document isn't base64") from None
    if not document.startswith(b"%PDF-"):
        raise HTTPException(422, "the document is a PDF")
    if len(document) > MAX_DOCUMENT:
        raise HTTPException(422, "the document is at most 5 MB")
    return document


def signers_of(people: list[SignerIn]) -> list[tuple[str, str]]:
    found = []
    for person in people:
        name, email = person.name.strip(), accounts.normalize_email(person.email)
        if not name:
            raise HTTPException(422, "every signer has a name")
        if not accounts.EMAIL.fullmatch(email):
            raise HTTPException(422, f"{email!r} isn't an email address")
        found.append((name, email))
    if len({email for _, email in found}) < len(found):
        raise HTTPException(422, "each signer has their own email")
    return found


OFFERS = """
SELECT o.id, o.application_id, o.renews_lease_id, p.display_name, u.display_name,
       o.starts_on, o.ends_on, o.monthly_rent, o.document_title, o.offered_at,
       r.offer_id IS NOT NULL, c.lease_id
FROM trust_lease_offers AS o
JOIN trust_units AS u ON u.id = o.unit_id
JOIN trust_properties AS p ON p.id = u.property_id
LEFT JOIN trust_signature_requests AS r ON r.offer_id = o.id
LEFT JOIN trust_lease_offer_completions AS c ON c.offer_id = o.id
WHERE o.pmc_id = %(pmc)s AND (%(offer)s::uuid IS NULL OR o.id = %(offer)s::uuid)
  AND (%(leases)s::uuid[] IS NULL OR o.renews_lease_id = ANY(%(leases)s::uuid[]))
ORDER BY o.offered_at DESC, o.id
"""


def offers_of(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    offer: UUID | None = None,
    leases: list[UUID] | None = None,
) -> list[OfferOut]:
    rows = conn.execute(OFFERS, {"pmc": pmc_id, "offer": offer, "leases": leases}).fetchall()
    ids = [row[0] for row in rows]
    signers: dict[UUID, list[SignerOut]] = {i: [] for i in ids}
    for offer_id, position, name, email, signed_at in conn.execute(
        "SELECT s.offer_id, s.position, s.display_name, s.email, g.signed_at"
        " FROM trust_lease_offer_signers AS s"
        " LEFT JOIN trust_signatures AS g ON g.offer_id = s.offer_id AND g.position = s.position"
        " WHERE s.offer_id = ANY(%s) ORDER BY s.offer_id, s.position",
        (ids,),
    ).fetchall():
        signers[offer_id].append(
            SignerOut(position=position, name=name, email=email, signed_at=signed_at)
        )
    return [
        OfferOut(
            id=i,
            kind="new lease" if application else "renewal",
            application_id=application,
            renews_lease_id=renews,
            property=prop,
            unit=unit,
            starts_on=starts,
            ends_on=ends,
            monthly_rent=exports.text(rent),
            document_title=title,
            offered_at=at,
            sent=sent,
            signers=signers[i],
            lease_id=lease,
        )
        for i, application, renews, prop, unit, starts, ends, rent, title, at, sent, lease in rows
    ]


def offer_of(conn: psycopg.Connection[Any], pmc_id: UUID, offer: UUID) -> OfferOut:
    found = offers_of(conn, pmc_id, offer)
    if not found:
        raise HTTPException(404, "no such lease offer")
    return found[0]


def links_of(partner: esign.Partner, reference: str, offer: OfferOut) -> list[SigningLink]:
    return [
        SigningLink(
            position=s.position,
            name=s.name,
            email=s.email,
            url=partner.signing_link(reference, s.position),
        )
        for s in offer.signers
    ]


def reference_of(conn: psycopg.Connection[Any], offer: UUID) -> str:
    row = conn.execute(
        "SELECT partner_reference FROM trust_signature_requests WHERE offer_id = %s", (offer,)
    ).fetchone()
    if row is None:
        raise HTTPException(409, "this offer was never sent for signing")
    return str(row[0])


def record_signatures(
    conn: psycopg.Connection[Any], partner: esign.Partner, offer: OfferOut
) -> None:
    """Records the signatures the partner reports that aren't recorded yet."""
    if not offer.sent or offer.lease_id is not None:
        return
    signed = partner.signed(reference_of(conn, offer.id))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO trust_signatures (pmc_id, offer_id, position, signed_at)"
            " SELECT o.pmc_id, o.id, %s, %s FROM trust_lease_offers AS o WHERE o.id = %s"
            " ON CONFLICT (offer_id, position) DO NOTHING",
            [(position, at, offer.id) for position, at in signed.items()],
        )


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
    partner = partner_of(request)
    if new.ends_on is not None and new.ends_on < new.starts_on:
        raise HTTPException(422, "a lease ends on or after the day it starts")
    document = document_of(new)
    people = signers_of(new.signers or default_signers)
    if not people:
        raise HTTPException(422, "name who signs")
    try:
        row = conn.execute(
            "INSERT INTO trust_lease_offers (pmc_id, application_id, renews_lease_id, unit_id,"
            " starts_on, ends_on, monthly_rent, document_title, document, offered_by)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                session.pmc_id,
                application,
                renews,
                unit,
                new.starts_on,
                new.ends_on,
                new.monthly_rent,
                new.document_title.strip(),
                document,
                session.person_id,
            ),
        ).fetchone()
    except psycopg.errors.CheckViolation as refused:
        raise HTTPException(409, str(refused).splitlines()[0].removeprefix("trust: ")) from None
    assert row is not None
    offer_id = row[0]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO trust_lease_offer_signers (pmc_id, offer_id, position, display_name,"
            " email) VALUES (%s, %s, %s, %s, %s)",
            [
                (session.pmc_id, offer_id, n, name, email)
                for n, (name, email) in enumerate(people, 1)
            ],
        )
    # The partner keeps an envelope even if this transaction then fails; a real partner voids
    # what it never hears about again.
    reference = partner.send(
        new.document_title.strip(),
        document,
        tuple(esign.Signer(n, name, email) for n, (name, email) in enumerate(people, 1)),
    )
    conn.execute(
        "INSERT INTO trust_signature_requests (pmc_id, offer_id, partner, partner_reference,"
        " sent_by) VALUES (%s, %s, %s, %s, %s)",
        (session.pmc_id, offer_id, partner.name, reference, session.person_id),
    )
    offer = offer_of(conn, session.pmc_id, offer_id)
    return Offered(offer=offer, links=links_of(partner, reference, offer))


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
        "       LEFT JOIN trust_logins AS g ON g.tenant_id = t.id"
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
    show: Annotated[Literal["open", "all"], Query()] = "open",
) -> list[OfferOut]:
    """The PMC's lease offers: by default those not yet a lease."""
    found = offers_of(conn, session.pmc_id)
    return found if show == "all" else [o for o in found if o.lease_id is None]


@router.get("/api/leasing/offers/{offer_id}/links")
def links(request: Request, session: Staff, conn: Conn, offer_id: UUID) -> list[SigningLink]:
    """Each signer's link again, to send once more."""
    offer = offer_of(conn, session.pmc_id, offer_id)
    return links_of(partner_of(request), reference_of(conn, offer_id), offer)


@router.post("/api/leasing/offers/{offer_id}/signatures")
def check_signatures(request: Request, session: Staff, conn: Conn, offer_id: UUID) -> OfferOut:
    """Records the signatures the partner has for the offer since it was last asked."""
    offer = offer_of(conn, session.pmc_id, offer_id)
    record_signatures(conn, partner_of(request), offer)
    return offer_of(conn, session.pmc_id, offer_id)


@router.post("/api/leasing/offers/{offer_id}/complete")
def complete(request: Request, session: Staff, conn: Conn, offer_id: UUID) -> OfferOut:
    """Opens the offer's lease once everyone has signed (trust_complete_lease_offer)."""
    offer = offer_of(conn, session.pmc_id, offer_id)
    record_signatures(conn, partner_of(request), offer)
    try:
        conn.execute(
            "SELECT trust_complete_lease_offer(%s, %s, %s)",
            (session.pmc_id, offer_id, session.person_id),
        )
    except (psycopg.errors.CheckViolation, psycopg.errors.ExclusionViolation) as refused:
        raise HTTPException(409, str(refused).splitlines()[0].removeprefix("trust: ")) from None
    return offer_of(conn, session.pmc_id, offer_id)


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
            "SELECT lease_id FROM trust_lease_tenants WHERE pmc_id = %s AND tenant_id = %s",
            (session.pmc_id, session.person_id),
        ).fetchall()
    ]


def tenant_offer(
    conn: psycopg.Connection[Any], session: accounts.Session, offer_id: UUID
) -> OfferOut:
    """A renewal offer on one of the tenant's leases, or 404 for any other."""
    found = offers_of(conn, session.pmc_id, offer_id, tenant_leases(conn, session))
    if not found:
        raise HTTPException(404, "no such renewal offer of yours")
    return found[0]


def as_signed_now(conn: psycopg.Connection[Any], request: Request, offer: OfferOut) -> OfferOut:
    """The offer with the signatures its partner reports that staff haven't recorded yet, so a
    tenant who just signed sees it. Nothing is written: staff record them (check_signatures)."""
    partner: esign.Partner | None = request.app.state.esign
    if partner is None or not offer.sent:
        return offer
    signed = partner.signed(reference_of(conn, offer.id))
    return offer.model_copy(
        update={
            "signers": [
                s if s.signed_at else s.model_copy(update={"signed_at": signed.get(s.position)})
                for s in offer.signers
            ]
        }
    )


@router.get("/api/tenant/renewals")
def tenant_renewals(request: Request, session: Tenant, conn: Conn) -> list[OfferOut]:
    """Renewal offers on the tenant's leases that aren't a lease yet."""
    found = offers_of(conn, session.pmc_id, None, tenant_leases(conn, session))
    return [as_signed_now(conn, request, o) for o in found if o.lease_id is None]


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
    (signer,) = mine
    url = partner_of(request).signing_link(reference_of(conn, offer_id), signer.position)
    return SigningLink(position=signer.position, name=signer.name, email=signer.email, url=url)


@router.get("/api/tenant/renewals/{offer_id}/document")
def tenant_renewal_document(session: Tenant, conn: Conn, offer_id: UUID) -> Response:
    tenant_offer(conn, session, offer_id)
    return document_response(conn, offer_id)
