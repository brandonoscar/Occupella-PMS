"""The company itself (the founder's punch list, ADR 0006, step 12; migration 0075): its name
(trust_pmcs, which the app may rename), its mailing address and the late fee and NSF fee terms
each property added from then on starts with (trust_company_profiles: append-only, the latest
row holding; saving the same again writes nothing), and its logo (a company_logo document, the
latest not removed).

Staff see and change it at Settings, Company setup. Owners and tenants see the name, the address
(where a notice to the manager goes) and the logo in their portal. A default changes no property
already there: each keeps its own terms (src/pms/api/setup.py applies the defaults to a new
one). Nothing here moves money, and the AI role reads none of it but the name.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from pms import exports
from pms.api import documents
from pms.api.deps import Conn, Current, Staff
from pms.api.directory import City, PostalCode, State, Street
from pms.api.setup import Money, Name, Percent

router = APIRouter(prefix="/api/company")

LogoType = Literal["image/png", "image/jpeg"]
ADDRESS = ("address_line_1", "address_line_2", "city", "state", "postal_code")
LATE = ("late_grace_days", "late_flat_fee", "late_percent", "late_maximum")


class Address(BaseModel):
    address_line_1: Street
    address_line_2: Street | None = None
    city: City
    state: State
    postal_code: PostalCode


class LateFeeTerms(BaseModel):
    grace_days: Annotated[int, Field(ge=0, le=365)]
    flat_fee: Money = Decimal(0)
    percent: Percent = Decimal(0)
    maximum: Money | None = None


class DefaultLateFees(BaseModel):
    grace_days: int
    flat_fee: str
    percent: str
    maximum: str | None


class Logo(BaseModel):
    id: UUID
    content_type: LogoType
    uploaded_at: datetime


class Company(BaseModel):
    name: str
    address: Address | None
    # The terms a property added from now on starts with; none, it starts with none.
    late_fees: DefaultLateFees | None
    nsf_fee: str | None
    logo: Logo | None


class Card(BaseModel):
    """What an owner or a tenant sees of their property manager."""

    name: str
    address: Address | None
    logo: bool


def profile(conn: psycopg.Connection[Any], pmc_id: UUID) -> dict[str, Any]:
    """The latest profile row's fields (all None when there is none)."""
    fields = (*ADDRESS, *LATE, "nsf_fee")
    row = conn.execute(
        f"SELECT {', '.join(fields)} FROM trust_company_profiles WHERE pmc_id = %s"  # noqa: S608
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (pmc_id,),
    ).fetchone()
    return dict(zip(fields, row or (None,) * len(fields), strict=True))


def address_of(found: dict[str, Any]) -> Address | None:
    if found["address_line_1"] is None:
        return None
    return Address(**{part: found[part] for part in ADDRESS})


def logo_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> Logo | None:
    row = conn.execute(
        "SELECT d.id, d.content_type, d.created_at FROM trust_documents AS d"
        " WHERE d.pmc_id = %s AND d.kind = 'company_logo'"
        " AND NOT EXISTS (SELECT 1 FROM trust_document_removals AS r WHERE r.document_id = d.id)"
        " ORDER BY d.created_at DESC, d.id DESC LIMIT 1",
        (pmc_id,),
    ).fetchone()
    return None if row is None else Logo(id=row[0], content_type=row[1], uploaded_at=row[2])


def name_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> str:
    row = conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc_id,)
    ).fetchone()
    assert row is not None
    return str(row[0])


def company_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> Company:
    found = profile(conn, pmc_id)
    return Company(
        name=name_of(conn, pmc_id),
        address=address_of(found),
        late_fees=None
        if found["late_grace_days"] is None
        else DefaultLateFees(
            grace_days=found["late_grace_days"],
            flat_fee=exports.text(found["late_flat_fee"]),
            percent=exports.text(found["late_percent"]),
            maximum=None if found["late_maximum"] is None else exports.text(found["late_maximum"]),
        ),
        nsf_fee=None if found["nsf_fee"] is None else exports.text(found["nsf_fee"]),
        logo=logo_of(conn, pmc_id),
    )


@router.get("")
def company(session: Staff, conn: Conn) -> Company:
    """The company: its name, address, the fee terms a new property starts with, and its logo."""
    return company_of(conn, session.pmc_id)


class NewCompany(BaseModel):
    name: Name
    address: Address | None = None
    late_fees: LateFeeTerms | None = None
    nsf_fee: Money | None = None


@router.post("")
def save_company(session: Staff, conn: Conn, new: NewCompany) -> Company:
    """Renames the company if its name changed, and keeps its address and default fee terms as a
    new profile row if they changed; saving the same again writes nothing."""
    if new.name != name_of(conn, session.pmc_id):
        conn.execute(
            "UPDATE trust_pmcs SET display_name = %s WHERE pmc_id = %s", (new.name, session.pmc_id)
        )
    address = new.address.model_dump() if new.address else dict.fromkeys(ADDRESS)
    late = new.late_fees
    wanted: dict[str, Any] = {
        **address,
        "late_grace_days": late.grace_days if late else None,
        "late_flat_fee": late.flat_fee if late else None,
        "late_percent": late.percent if late else None,
        "late_maximum": late.maximum if late else None,
        "nsf_fee": new.nsf_fee,
    }
    if wanted != profile(conn, session.pmc_id):
        columns = list(wanted)
        conn.execute(
            f"INSERT INTO trust_company_profiles (pmc_id, {', '.join(columns)})"  # noqa: S608
            f" VALUES (%s, {', '.join(['%s'] * len(columns))})",
            (session.pmc_id, *wanted.values()),
        )
    return company_of(conn, session.pmc_id)


class NewLogo(BaseModel):
    content_type: LogoType
    # The image, base64: 5 MB comes to about 7 million characters.
    content_base64: str = Field(min_length=4, max_length=7_000_000)


@router.post("/logo", status_code=201)
def upload_logo(session: Staff, conn: Conn, new: NewLogo) -> Logo:
    """The company's logo, a PNG or JPEG of at most 5 MB; it takes the last one's place."""
    try:
        content = base64.b64decode(new.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "the file isn't base64") from None
    if not 0 < len(content) <= documents.MAX_BYTES:
        raise HTTPException(422, "a logo is an image of at most 5 MB")
    if not content.startswith(documents.MAGIC[new.content_type]):
        kind = documents.EXTENSION[new.content_type].upper()
        raise HTTPException(422, f"the file isn't a {kind}")
    row = conn.execute(
        "INSERT INTO trust_documents (pmc_id, kind, title, content_type, size, sha256)"
        " VALUES (%s, 'company_logo', 'Company logo', %s, %s, %s) RETURNING id",
        (session.pmc_id, new.content_type, len(content), hashlib.sha256(content).digest()),
    ).fetchone()
    assert row is not None
    conn.execute(
        "INSERT INTO trust_document_files (document_id, pmc_id, content) VALUES (%s, %s, %s)",
        (row[0], session.pmc_id, content),
    )
    logo = logo_of(conn, session.pmc_id)
    assert logo is not None
    return logo


@router.post("/logo/remove", status_code=204)
def remove_logo(session: Staff, conn: Conn) -> None:
    """The company has no logo again; the record of the last one stays."""
    logo = logo_of(conn, session.pmc_id)
    if logo is None:
        raise HTTPException(404, "the company has no logo")
    conn.execute(
        "INSERT INTO trust_document_removals (document_id, pmc_id) VALUES (%s, %s)",
        (logo.id, session.pmc_id),
    )


@router.get("/logo")
def logo(session: Current, conn: Conn) -> Response:
    """The company's logo, to anyone signed in to it: its staff, owners and tenants."""
    found = logo_of(conn, session.pmc_id)
    if found is None:
        raise HTTPException(404, "the company has no logo")
    row = conn.execute(
        "SELECT content FROM trust_document_files WHERE document_id = %s", (found.id,)
    ).fetchone()
    assert row is not None
    return Response(
        bytes(row[0]),
        media_type=found.content_type,
        headers={"Content-Security-Policy": "default-src 'none'; sandbox"},
    )


@router.get("/card")
def card(session: Current, conn: Conn) -> Card:
    """The company's name, address and whether it has a logo, as owners and tenants see them."""
    return Card(
        name=name_of(conn, session.pmc_id),
        address=address_of(profile(conn, session.pmc_id)),
        logo=logo_of(conn, session.pmc_id) is not None,
    )
