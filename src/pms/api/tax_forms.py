"""1099 forms (migration 20261022000162; src/pms/tax_forms.py): a year's recipients of a 1099-NEC
(vendors) and a 1099-MISC (owners' rents) with what each form needs and what is missing, the
taxpayer IDs from their W-9s and the company's own (the number sealed; staff see its last four
digits), each recipient's copy as a PDF and the list a company uploads to file them. The list
holds whole taxpayer IDs, so only an admin downloads it. Recording a taxpayer ID is the custom
roles' area 'setup'. The AI role reads none of it. Nothing here moves money.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, StringConstraints

from pms import sealed, tax_forms
from pms.api.deps import Conn, Staff, download

router = APIRouter(prefix="/api/tax-forms")

Year = Annotated[int, Query(ge=2000, le=2100)]
About = Literal["vendor", "owner", "company"]
LegalName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Tin = Annotated[str, StringConstraints(strip_whitespace=True, min_length=9, max_length=11)]
FORMS: dict[str, tax_forms.Form] = {"nec": "1099-NEC", "misc": "1099-MISC"}


class TaxpayerIdOut(BaseModel):
    tin_type: str
    # The number as staff see it: its last four digits.
    tin_last4: str
    legal_name: str
    exempt: bool
    recorded_by: str | None
    recorded_at: datetime


class NewTaxpayerId(BaseModel):
    about: About
    about_id: UUID | None = None
    tin_type: Literal["EIN", "SSN", "ITIN"]
    tin: Tin
    legal_name: LegalName
    # An owner the company files no 1099 for (a corporation, say).
    exempt: bool = False


class PartyOut(BaseModel):
    kind: About
    id: UUID | None
    name: str
    taxpayer_id: TaxpayerIdOut | None
    address: list[str] | None
    # What the form still needs: "taxpayer ID", "mailing address".
    missing: list[str]


class RecipientOut(PartyOut):
    form: tax_forms.Form
    amount: str


class TaxForms(BaseModel):
    year: int
    threshold: str
    payer: PartyOut
    recipients: list[RecipientOut]
    # Whether this server can seal and open taxpayer IDs (PMS_FIELD_KEY).
    sealing: bool


def id_out(found: tax_forms.TaxpayerId | None) -> TaxpayerIdOut | None:
    if found is None:
        return None
    return TaxpayerIdOut(
        tin_type=found.tin_type,
        tin_last4=found.tin_last4,
        legal_name=found.legal_name,
        exempt=found.exempt,
        recorded_by=found.recorded_by,
        recorded_at=found.recorded_at,
    )


def party_out(party: tax_forms.Party) -> dict[str, Any]:
    return {
        "kind": party.kind,
        "id": party.id,
        "name": party.name,
        "taxpayer_id": id_out(party.taxpayer_id),
        "address": None if party.address is None else party.address.lines(),
        "missing": party.missing(),
    }


@router.get("")
def tax_forms_of(session: Staff, conn: Conn, year: Year) -> TaxForms:
    """The year's 1099 recipients, the company as their payer, and what each form still needs."""
    payer = tax_forms.payer_of(conn, session.pmc_id)
    found = tax_forms.recipients(conn, session.pmc_id, year)
    return TaxForms(
        year=year,
        threshold=f"{tax_forms.threshold(year):.2f}",
        payer=PartyOut(**party_out(payer)),
        recipients=[
            RecipientOut(**party_out(r.party), form=r.form, amount=f"{r.amount:.2f}") for r in found
        ],
        sealing=sealed.configured(),
    )


def whose(conn: psycopg.Connection[Any], pmc_id: UUID, about: About, about_id: UUID | None) -> None:
    if about == "company":
        if about_id is not None:
            raise HTTPException(422, "the company's own taxpayer ID names no one")
        return
    table = "trust_vendors" if about == "vendor" else "trust_owners"
    if about_id is None or (
        conn.execute(
            f"SELECT 1 FROM {table} WHERE pmc_id = %s AND id = %s",  # noqa: S608
            (pmc_id, about_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, f"no such {about}")


@router.post("/taxpayer-id", status_code=201)
def keep_taxpayer_id(session: Staff, conn: Conn, new: NewTaxpayerId) -> TaxpayerIdOut:
    """Keeps a taxpayer ID as its W-9 gives it: the number sealed before it reaches the database."""
    whose(conn, session.pmc_id, new.about, new.about_id)
    digits = re.sub(r"[ -]", "", new.tin)
    if not re.fullmatch(r"[0-9]{9}", digits):
        raise HTTPException(422, "a taxpayer ID is nine digits")
    if new.about == "company" and new.tin_type != "EIN":
        raise HTTPException(422, "the company files 1099s under its EIN")
    if new.exempt and new.about != "owner":
        raise HTTPException(422, "only an owner is marked exempt here; a vendor's profile says it")
    if not sealed.configured():
        raise HTTPException(409, "this server has no key to seal taxpayer IDs (PMS_FIELD_KEY)")
    tax_forms.record_taxpayer_id(
        conn,
        session.pmc_id,
        new.about,
        new.about_id,
        new.tin_type,
        digits,
        new.legal_name,
        new.exempt,
    )
    kept = id_out(tax_forms.taxpayer_id_of(conn, session.pmc_id, new.about, new.about_id))
    assert kept is not None
    return kept


def forms(conn: psycopg.Connection[Any], pmc_id: UUID, year: int, form: str) -> Any:
    if form not in FORMS:
        raise HTTPException(422, "the form is nec or misc")
    payer = tax_forms.payer_of(conn, pmc_id)
    if payer.missing():
        raise HTTPException(
            409, f"the company still needs its {' and '.join(payer.missing())} for its 1099s"
        )
    if not sealed.configured():
        raise HTTPException(409, "this server has no key to open taxpayer IDs (PMS_FIELD_KEY)")
    return payer, tax_forms.recipients(conn, pmc_id, year), FORMS[form]


@router.get("/{year}/copies.pdf")
def copies(session: Staff, conn: Conn, year: int, form: Annotated[str, Query()]) -> Response:
    """Each recipient's copy of the year's form (Copy B), a page each."""
    payer, found, which = forms(conn, session.pmc_id, year, form)
    try:
        pdf = tax_forms.copies_pdf(payer, found, which, year)
    except sealed.CannotOpen:
        raise HTTPException(409, "a taxpayer ID doesn't open with this server's key") from None
    return download(pdf, f"{which}-{year}-recipient-copies", "pdf")


@router.get("/{year}/filing.csv")
def filing(session: Staff, conn: Conn, year: int, form: Annotated[str, Query()]) -> Response:
    """The year's form as the list a company uploads to file it, whole taxpayer IDs and all: only
    an admin downloads it."""
    held = conn.execute(
        "SELECT trust_staff_holds(%s, %s, 'admin')", (session.pmc_id, session.person_id)
    ).fetchone()
    if held is None or not held[0]:
        raise HTTPException(403, "only an admin downloads the filing list: it holds whole tax IDs")
    payer, found, which = forms(conn, session.pmc_id, year, form)
    try:
        listed = tax_forms.filing_csv(payer, found, which, year)
    except sealed.CannotOpen:
        raise HTTPException(409, "a taxpayer ID doesn't open with this server's key") from None
    return download(listed, f"{which}-{year}-filing", "csv")
