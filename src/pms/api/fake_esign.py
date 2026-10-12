"""The fake e-sign partner's own signing page (pms.leasing.esign.FakeESign), served only while
the server runs the fake (PMS_ESIGN_PARTNER=fake). A real partner hosts its own; this one lets
development and the browser tests sign without one."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from pms.leasing import esign

router = APIRouter(prefix="/api/fake-esign")
Token = Field(min_length=20, max_length=100)


class SigningToken(BaseModel):
    token: str = Token


class Signature(BaseModel):
    token: str = Token
    name: str = Field(min_length=1, max_length=200)


class Envelope(BaseModel):
    partner: str
    document_title: str
    signer: str
    document_sha256: str
    signed: bool


def fake(request: Request) -> esign.FakeESign:
    partner = request.app.state.esign
    assert isinstance(partner, esign.FakeESign)  # the router is mounted only for the fake
    return partner


@router.post("/view")
def view(request: Request, asked: SigningToken) -> Envelope:
    try:
        title, signer, sha256, signed = fake(request).view(asked.token)
    except esign.NotSigned as refused:
        raise HTTPException(404, str(refused)) from None
    return Envelope(
        partner=esign.FakeESign.name,
        document_title=title,
        signer=signer,
        document_sha256=sha256,
        signed=signed,
    )


@router.post("/sign", status_code=204)
def sign(request: Request, signature: Signature) -> None:
    try:
        fake(request).sign(signature.token, signature.name)
    except esign.NotSigned as refused:
        raise HTTPException(422, str(refused)) from None
