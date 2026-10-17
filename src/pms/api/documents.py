"""Staff's documents (migration 0036): a vendor's W-9, a bill's receipt, a lease's move-out
photos and an owner's documents, uploaded as PDF, JPEG or PNG files of at most 5 MB.

A file is checked twice: here for its type's first bytes and its size, and by the database,
which keeps its bytes apart from the audited record and refuses a file that isn't what its
record says. A document staff remove is no longer listed or served; its record stays. An owner
reads their own documents in the owner portal (src/pms/api/owner.py). The PMS reads nothing out
of a file: a W-9's tax ID stays in it.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Response
from psycopg import sql
from pydantic import BaseModel, Field, StringConstraints

from pms.api.deps import Conn, Staff

router = APIRouter(prefix="/api/documents")

Kind = Literal["w9", "receipt", "move_out_photo", "owner_document"]
ContentType = Literal["application/pdf", "image/jpeg", "image/png"]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
MAX_BYTES = 5 * 1024 * 1024
# The first bytes of each type, as the database checks them too.
MAGIC = {
    "application/pdf": b"%PDF-",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
}
EXTENSION = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}
COLUMN: dict[Kind, str] = {
    "w9": "vendor_id",
    "receipt": "bill_id",
    "move_out_photo": "lease_id",
    "owner_document": "owner_id",
}

# What each kind of document may be about: the PMC's vendors, bills, leases and owners.
SUBJECTS: dict[Kind, str] = {
    "w9": "SELECT v.id, v.display_name FROM trust_vendors AS v WHERE v.pmc_id = %(pmc)s"
    ' ORDER BY v.display_name COLLATE "C", v.id',
    "receipt": "SELECT b.id, v.display_name || ' ' || b.reference || ', '"
    " || to_char(b.bill_date, 'YYYY-MM-DD') FROM trust_bills AS b"
    " JOIN trust_vendors AS v ON v.id = b.vendor_id WHERE b.pmc_id = %(pmc)s"
    ' ORDER BY b.bill_date DESC, v.display_name COLLATE "C", b.id',
    "move_out_photo": "SELECT l.id, p.display_name || ' / ' || u.display_name || ': '"
    " || (SELECT string_agg(t.display_name, ', ' ORDER BY t.display_name COLLATE \"C\")"
    "     FROM trust_lease_tenants AS lt JOIN trust_tenants AS t ON t.id = lt.tenant_id"
    "     WHERE lt.lease_id = l.id)"
    " FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id"
    " JOIN trust_properties AS p ON p.id = u.property_id WHERE l.pmc_id = %(pmc)s"
    ' ORDER BY p.display_name COLLATE "C", u.display_name COLLATE "C", l.starts_on, l.id',
    "owner_document": "SELECT o.id, o.display_name FROM trust_owners AS o"
    ' WHERE o.pmc_id = %(pmc)s ORDER BY o.display_name COLLATE "C", o.id',
}


class Subject(BaseModel):
    id: UUID
    label: str


class Document(BaseModel):
    id: UUID
    kind: Kind
    about_id: UUID
    about: str
    title: str
    content_type: ContentType
    size: int
    uploaded_by: str | None
    uploaded_at: datetime


class Library(BaseModel):
    documents: list[Document]
    # What a new document of each kind may be about.
    subjects: dict[Kind, list[Subject]]


DOCUMENTS = """
SELECT d.id, d.kind, coalesce(d.vendor_id, d.bill_id, d.lease_id, d.owner_id), d.title,
       d.content_type, d.size, s.display_name, d.created_at
FROM trust_documents AS d
LEFT JOIN trust_staff AS s ON s.id = d.uploaded_by
WHERE d.pmc_id = %(pmc)s
  AND NOT EXISTS (SELECT 1 FROM trust_document_removals AS r WHERE r.document_id = d.id)
ORDER BY d.created_at DESC, d.id
"""


@router.get("")
def library(session: Staff, conn: Conn) -> Library:
    """Every document not removed, newest first, and what new ones may be about."""
    params = {"pmc": session.pmc_id}
    subjects = {
        kind: [Subject(id=i, label=label) for i, label in conn.execute(query, params).fetchall()]
        for kind, query in SUBJECTS.items()
    }
    labels = {s.id: s.label for found in subjects.values() for s in found}
    documents = [
        Document(
            id=i,
            kind=kind,
            about_id=about,
            about=labels[about],
            title=title,
            content_type=content_type,
            size=size,
            uploaded_by=by,
            uploaded_at=at,
        )
        for i, kind, about, title, content_type, size, by, at in conn.execute(
            DOCUMENTS, params
        ).fetchall()
    ]
    return Library(documents=documents, subjects=subjects)


class NewDocument(BaseModel):
    kind: Kind
    about_id: UUID
    title: Title
    content_type: ContentType
    # The file, base64: 5 MB comes to about 7 million characters.
    content_base64: str = Field(min_length=4, max_length=7_000_000)


class Created(BaseModel):
    id: UUID


@router.post("", status_code=201)
def upload(session: Staff, conn: Conn, new: NewDocument) -> Created:
    """Keeps a file as a document about one of the PMC's vendors, bills, leases or owners."""
    return Created(id=keep(conn, session.pmc_id, new))


def keep(conn: psycopg.Connection[Any], pmc_id: UUID, new: NewDocument) -> UUID:
    """Keeps a file as a document of the PMC, checked: its type, its size and what it is
    about. A bill's invoice comes this way too, with the bill (src/pms/api/payables.py)."""
    try:
        content = base64.b64decode(new.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "the file isn't base64") from None
    if not 0 < len(content) <= MAX_BYTES:
        raise HTTPException(422, "a document is a file of at most 5 MB")
    if not content.startswith(MAGIC[new.content_type]):
        raise HTTPException(422, f"the file isn't a {EXTENSION[new.content_type].upper()}")
    subjects = conn.execute(SUBJECTS[new.kind], {"pmc": pmc_id}).fetchall()
    if new.about_id not in {i for i, _ in subjects}:
        raise HTTPException(404, "nothing of this PMC for the document to be about")
    insert = sql.SQL(
        "INSERT INTO trust_documents (pmc_id, kind, {}, title, content_type, size, sha256)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id"
    ).format(sql.Identifier(COLUMN[new.kind]))
    row = conn.execute(
        insert,
        (
            pmc_id,
            new.kind,
            new.about_id,
            new.title,
            new.content_type,
            len(content),
            hashlib.sha256(content).digest(),
        ),
    ).fetchone()
    assert row is not None
    conn.execute(
        "INSERT INTO trust_document_files (document_id, pmc_id, content) VALUES (%s, %s, %s)",
        (row[0], pmc_id, content),
    )
    return UUID(str(row[0]))


def file_name(title: str, content_type: str) -> str:
    """A download's name: the title in plain letters, digits and dashes, with its extension."""
    stem = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:80] or "document"
    return f"{stem}.{EXTENSION[content_type]}"


def served(
    conn: psycopg.Connection[Any], pmc_id: UUID, document_id: UUID, owner: UUID | None
) -> Response:
    """A document's file as a download, or 404: another PMC's, removed, or (for an owner) not
    one of their own documents."""
    row = conn.execute(
        "SELECT d.title, d.content_type, f.content FROM trust_documents AS d"
        " JOIN trust_document_files AS f ON f.document_id = d.id"
        " WHERE d.pmc_id = %s AND d.id = %s"
        " AND (%s::uuid IS NULL OR (d.kind = 'owner_document' AND d.owner_id = %s::uuid))"
        " AND NOT EXISTS (SELECT 1 FROM trust_document_removals AS r WHERE r.document_id = d.id)",
        (pmc_id, document_id, owner, owner),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such document")
    title, content_type, content = row
    return Response(
        bytes(content),
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file_name(title, content_type)}"',
            # Opened in the browser anyway, a file runs nothing.
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.get("/{document_id}/file")
def download(session: Staff, conn: Conn, document_id: UUID) -> Response:
    return served(conn, session.pmc_id, document_id, None)


@router.post("/{document_id}/remove", status_code=204)
def remove(session: Staff, conn: Conn, document_id: UUID) -> None:
    """No list shows the document again and no one downloads it; its record stays."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_documents WHERE pmc_id = %s AND id = %s",
            (session.pmc_id, document_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such document")
    try:
        conn.execute(
            "INSERT INTO trust_document_removals (document_id, pmc_id) VALUES (%s, %s)",
            (document_id, session.pmc_id),
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "the document was removed already") from None
