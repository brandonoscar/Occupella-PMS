"""Staff's vendors (migration 0058): each vendor's W-9, insurance and contact details (migration
0064, recorded through `directory.py`), and recording a policy.
How a vendor's coverage stands on a day is trust_report_vendor_insurance's answer (current,
expiring within 30 days, expired, none on file), which this page reads, so the two agree.
A policy moves no money; a renewal is a new policy.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints, model_validator

from pms import exports
from pms.api.deps import Conn, Staff
from pms.api.directory import Contact, contacts_of
from pms.api.rent import today

router = APIRouter(prefix="/api/vendors")

Coverage = Literal[
    "general liability",
    "workers compensation",
    "commercial auto",
    "professional liability",
    "other",
]
Carrier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
PolicyNumber = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
Limit = Annotated[
    Decimal, Field(gt=Decimal(0), le=Decimal("1000000000"), max_digits=12, decimal_places=2)
]
CENT = Decimal("0.01")


class Policy(BaseModel):
    coverage: str
    carrier: str
    policy_number: str
    starts_on: date
    expires_on: date
    limit_amount: str | None
    standing: str  # current, expires today, expires in N days, expired


class VendorOut(BaseModel):
    id: UUID
    name: str
    w9_on_file: bool
    insurance: list[Policy]  # each coverage on file, as of the day
    contact: Contact = Contact()


def coverage(conn: psycopg.Connection[Any], pmc_id: UUID, on: date) -> list[VendorOut]:
    """Every vendor, with their coverage as trust_report_vendor_insurance has it on the day."""
    vendors: dict[UUID, VendorOut] = {}
    for item, name, vendor, kind, carrier, number, starts, ends, limit, standing in conn.execute(
        "SELECT item, vendor, vendor_id, coverage, carrier, policy_number, starts_on, expires_on,"
        " limit_amount, standing FROM trust_report_vendor_insurance(%s, %s)"
        " WHERE item IN ('policy', 'no insurance on file') ORDER BY line",
        (pmc_id, on),
    ).fetchall():
        found = vendors.setdefault(
            vendor, VendorOut(id=vendor, name=name, w9_on_file=False, insurance=[])
        )
        if item == "policy":
            found.insurance.append(
                Policy(
                    coverage=kind,
                    carrier=carrier,
                    policy_number=number,
                    starts_on=starts,
                    expires_on=ends,
                    limit_amount=None if limit is None else exports.text(limit.quantize(CENT)),
                    standing=standing,
                )
            )
    for (vendor,) in conn.execute(
        "SELECT DISTINCT d.vendor_id FROM trust_documents AS d WHERE d.pmc_id = %s"
        " AND d.kind = 'w9' AND NOT EXISTS (SELECT 1 FROM trust_document_removals AS r"
        " WHERE r.document_id = d.id)",
        (pmc_id,),
    ).fetchall():
        if vendor in vendors:
            vendors[vendor].w9_on_file = True
    for vendor, details in contacts_of(conn, pmc_id, "vendor_id").items():
        vendors[vendor].contact = details
    return list(vendors.values())


@router.get("")
def vendors(
    session: Staff, conn: Conn, on: Annotated[date | None, Query()] = None
) -> list[VendorOut]:
    """Every vendor of the company: W-9 on file, and each coverage as of the day (today, UTC,
    by default)."""
    return coverage(conn, session.pmc_id, on or today())


class NewPolicy(BaseModel):
    coverage: Coverage
    carrier: Carrier
    policy_number: PolicyNumber
    starts_on: date
    expires_on: date
    limit_amount: Limit | None = None

    @model_validator(mode="after")
    def runs_forward(self) -> NewPolicy:
        if self.expires_on < self.starts_on:
            raise ValueError("a policy expires no earlier than it starts")
        return self


class Created(BaseModel):
    id: UUID


@router.post("/{vendor_id}/insurance", status_code=201)
def record_policy(session: Staff, conn: Conn, vendor_id: UUID, policy: NewPolicy) -> Created:
    """Records a vendor's policy, a renewal as a new one."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_vendors WHERE pmc_id = %s AND id = %s", (session.pmc_id, vendor_id)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such vendor")
    row = conn.execute(
        "INSERT INTO trust_vendor_insurance (pmc_id, vendor_id, coverage, carrier,"
        " policy_number, starts_on, expires_on, limit_amount)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            session.pmc_id,
            vendor_id,
            policy.coverage,
            policy.carrier,
            policy.policy_number,
            policy.starts_on,
            policy.expires_on,
            policy.limit_amount,
        ),
    ).fetchone()
    assert row is not None
    return Created(id=row[0])
