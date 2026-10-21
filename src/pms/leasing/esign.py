"""E-signing a lease offer on the PMS's own signing page (ADR 0008, migration 0097).

An offer sent to sign here has its signature request from PARTNER. Each signer signs through a
link of their own (/sign#<token>), the token kept only as its SHA-256: staff get one per signer
when they send the offer or ask for links again, and a tenant gets their own from the portal. A
link works for 30 days while the offer is neither void nor a lease and its signer hasn't signed
(trust_signing_link_open). Through it the signer reads the consumer disclosures and agrees to
sign electronically (ESIGN, 15 U.S.C. 7001(c)), opens the lease, and signs it: their name typed,
maybe their signature drawn, on the lease's SHA-256. The database checks each step; this module
makes the links and words the disclosures.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any
from uuid import UUID

import psycopg

# trust_signature_requests.partner of an offer sent to sign here.
PARTNER = "PMS e-sign"
# How long a link works after it is made (trust_signing_link_open says the same).
LINK_DAYS = 30


def reference(offer: UUID) -> str:
    """The signature request's reference of an offer sent to sign here."""
    return f"pms-esign-{offer}"


def token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def new_link(
    conn: psycopg.Connection[Any], pmc_id: UUID, offer: UUID, position: int, public_url: str
) -> str:
    """A new link for the signer at `position`, in the caller's transaction. Links made before
    keep working until they run out."""
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO trust_signing_links (pmc_id, offer_id, position, token_sha256)"
        " VALUES (%s, %s, %s, %s)",
        (pmc_id, offer, position, token_digest(token)),
    )
    return f"{public_url.rstrip('/')}/sign#{token}"


def disclosure(company: str, address: str | None, email: str | None) -> str:
    """What a signer reads and agrees to before signing electronically: the consumer disclosures
    of ESIGN, 15 U.S.C. 7001(c)(1)(B) and (C), for this lease alone, naming how to reach the
    company (its mailing address and the address replies to its emails go to, if on file)."""
    reach = " or ".join(f"at {where}" for where in (email, address) if where)
    write = f"write to {company} {reach}" if reach else f"contact {company}"
    return "\n\n".join(
        (
            f"{company} asks you to receive and sign this lease electronically. Please read"
            " this before you agree.",
            "Paper instead. You may have this lease on paper and sign it on paper instead, at no"
            f' charge. To ask, choose "I\'d rather sign on paper" on this page, or {write}.',
            "What you agree to. Your agreement covers this lease only: reading it, signing it"
            " and its signing certificate, here.",
            "Taking it back. You may withdraw your agreement at any time before you sign, the"
            f' same way ("I\'d rather sign on paper", or {write}). Then this link signs nothing'
            f" and {company} gives you the lease on paper. Withdrawing doesn't undo a lease you"
            f" have signed. If your email address changes, tell {company}.",
            f"A paper copy. After you sign, you may ask {company} for a paper copy of the signed"
            " lease, at no charge.",
            "What you need. A device with a current web browser that opens PDF files, and a way"
            " to save or print a PDF to keep your copy.",
            "By agreeing, you confirm you can read this page and open PDF files on this device.",
        )
    )


def disclosure_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
