"""Staff's vendors (migration 0058): each vendor's W-9, insurance and contact details (migration
0064, recorded through `directory.py`), and recording a policy; a vendor's own page with their
bills and the work orders they were last assigned (ADR 0005), and renaming one. Since migration
0073 (ADR 0006): a vendor's trade, whether they are a 1099 vendor and how they are paid
(`trust_vendor_profiles`, the latest row holding; no tax ID is kept), the list a page at a time,
and a vendor added with all of it at once.
How a vendor's coverage stands on a day is trust_report_vendor_insurance's answer (current,
expiring within 30 days, expired, none on file), which this page reads, so the two agree.
A policy or a profile moves no money; a renewal is a new policy.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints, model_validator

from pms import exports
from pms.api.deps import Conn, Staff
from pms.api.directory import Contact, archive, contacts_of, found, record_contact
from pms.api.rent import cents, today
from pms.api.rentals import Archiving, Page, Paged, PerPage, Rename
from pms.api.setup import Name
from pms.api.work_orders import assigned_to

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


Trade = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Method = Literal["check", "EFT"]


class Profile(BaseModel):
    trade: Trade | None = None
    gets_1099: bool = False
    payment_method: Method | None = None


class VendorOut(BaseModel):
    id: UUID
    name: str
    w9_on_file: bool
    insurance: list[Policy]  # each coverage on file, as of the day
    contact: Contact = Contact()
    # Their latest profile; None until one is recorded.
    profile: Profile | None = None
    # Archived: off the list, its pickers and search, history kept (migration 0110).
    archived: bool = False

    @property
    def standing(self) -> str:
        """How their insurance stands, by the coverage that runs out first."""
        if not self.insurance:
            return "no insurance on file"
        return min(self.insurance, key=lambda p: (p.expires_on, p.coverage)).standing


def profiles_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> dict[UUID, Profile]:
    """The latest profile of each of the PMC's vendors who have one."""
    return {
        vendor: Profile(trade=trade, gets_1099=gets_1099, payment_method=method)
        for vendor, trade, gets_1099, method in conn.execute(
            "SELECT DISTINCT ON (vendor_id) vendor_id, trade, gets_1099, payment_method"
            " FROM trust_vendor_profiles WHERE pmc_id = %s"
            " ORDER BY vendor_id, created_at DESC, id DESC",
            (pmc_id,),
        ).fetchall()
    }


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
    for vendor, profile in profiles_of(conn, pmc_id).items():
        vendors[vendor].profile = profile
    for (vendor,) in conn.execute(
        "SELECT id FROM trust_vendors WHERE pmc_id = %s"
        " AND trust_is_archived(NULL, NULL, NULL, NULL, id)",
        (pmc_id,),
    ).fetchall():
        vendors[vendor].archived = True
    return list(vendors.values())


class Created(BaseModel):
    id: UUID


class VendorRow(BaseModel):
    id: UUID
    name: str
    trade: str | None
    phone: str | None
    email: str | None
    w9_on_file: bool
    # By the coverage that runs out first: current, expires in N days, expired, or none on file.
    insurance: str


@router.get("")
def vendors(
    session: Staff,
    conn: Conn,
    on: Annotated[date | None, Query()] = None,
    archived: bool = False,
    page: Page = 1,
    per_page: PerPage = 50,
) -> Paged[VendorRow]:
    """The company's vendors a page at a time, by name: trade, phone, email, W-9 on file and
    how their insurance stands on the day (today, UTC, by default). Archived ones only when
    asked."""
    every = [
        VendorRow(
            id=v.id,
            name=v.name,
            trade=v.profile.trade if v.profile else None,
            phone=v.contact.phone,
            email=v.contact.email,
            w9_on_file=v.w9_on_file,
            insurance=v.standing,
        )
        for v in coverage(conn, session.pmc_id, on or today())
        if v.archived == archived
    ]
    start = (page - 1) * per_page
    return Paged(
        items=every[start : start + per_page], total=len(every), page=page, per_page=per_page
    )


class NewVendor(Profile):
    name: Name
    contact: Contact = Contact()


@router.post("", status_code=201)
def add_vendor(session: Staff, conn: Conn, new: NewVendor) -> Created:
    """A vendor with their trade, whether they get a 1099, how they are paid and how to reach
    them, all at once."""
    row = conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
        (session.pmc_id, new.name),
    ).fetchone()
    assert row is not None
    record_profile(conn, session.pmc_id, row[0], new)
    record_contact(conn, session.pmc_id, "vendor_id", row[0], new.contact)
    return Created(id=row[0])


def record_profile(
    conn: psycopg.Connection[Any], pmc_id: UUID, vendor_id: UUID, profile: Profile
) -> bool:
    """Records a vendor's profile unless it is what they have already (or, with none yet, says
    nothing); True when it did."""
    latest = profiles_of_one(conn, vendor_id)
    asked = Profile(
        trade=profile.trade, gets_1099=profile.gets_1099, payment_method=profile.payment_method
    )
    if asked == (latest or Profile()):
        return False
    conn.execute(
        "INSERT INTO trust_vendor_profiles (pmc_id, vendor_id, trade, gets_1099, payment_method)"
        " VALUES (%s, %s, %s, %s, %s)",
        (pmc_id, vendor_id, asked.trade, asked.gets_1099, asked.payment_method),
    )
    return True


def profiles_of_one(conn: psycopg.Connection[Any], vendor_id: UUID) -> Profile | None:
    row = conn.execute(
        "SELECT trade, gets_1099, payment_method FROM trust_vendor_profiles WHERE vendor_id = %s"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (vendor_id,),
    ).fetchone()
    return None if row is None else Profile(trade=row[0], gets_1099=row[1], payment_method=row[2])


class Changed(BaseModel):
    changed: bool


@router.post("/{vendor_id}/profile")
def change_profile(session: Staff, conn: Conn, vendor_id: UUID, profile: Profile) -> Changed:
    """A vendor's trade, whether they get a 1099 and how they are paid, as they are now; the
    same again writes nothing."""
    known(conn, session.pmc_id, vendor_id)
    return Changed(changed=record_profile(conn, session.pmc_id, vendor_id, profile))


def known(conn: psycopg.Connection[Any], pmc_id: UUID, vendor_id: UUID) -> None:
    """404 unless the vendor is the PMC's."""
    if (
        conn.execute(
            "SELECT 1 FROM trust_vendors WHERE pmc_id = %s AND id = %s", (pmc_id, vendor_id)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such vendor")


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


@router.post("/{vendor_id}/insurance", status_code=201)
def record_policy(session: Staff, conn: Conn, vendor_id: UUID, policy: NewPolicy) -> Created:
    """Records a vendor's policy, a renewal as a new one."""
    known(conn, session.pmc_id, vendor_id)
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


class VendorBill(BaseModel):
    id: UUID
    reference: str
    owner: str
    property: str
    bill_date: date
    amount: str
    paid: bool


class VendorWorkOrder(BaseModel):
    id: UUID
    number: int
    property: str
    unit: str | None
    summary: str
    status: str
    opened_at: datetime


class VendorPage(VendorOut):
    # How their insurance stands, by the coverage that runs out first.
    insurance_standing: str
    bills: list[VendorBill]
    work_orders: list[VendorWorkOrder]


@router.get("/{vendor_id}")
def vendor_page(session: Staff, conn: Conn, vendor_id: UUID) -> VendorPage:
    """A vendor: their W-9, coverage today, contact details, bills newest first and the work
    orders they were last assigned."""
    found = next((v for v in coverage(conn, session.pmc_id, today()) if v.id == vendor_id), None)
    if found is None:
        raise HTTPException(404, "no such vendor")
    bills = [
        VendorBill(
            id=i, reference=ref, owner=o, property=p, bill_date=on, amount=cents(amt), paid=paid
        )
        for i, ref, o, p, on, amt, paid in conn.execute(
            "SELECT b.id, b.reference, o.display_name, p.display_name, b.bill_date, b.amount,"
            " EXISTS (SELECT 1 FROM trust_bill_payments AS x WHERE x.bill_id = b.id"
            "         AND x.step = 'paid')"
            " FROM trust_bills AS b"
            " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = b.ledger_account_id"
            " JOIN trust_owners AS o ON o.id = a.owner_id"
            " JOIN trust_properties AS p ON p.id = a.property_id"
            " WHERE b.vendor_id = %s ORDER BY b.bill_date DESC, b.created_at DESC, b.id",
            (vendor_id,),
        ).fetchall()
    ]
    orders = [
        VendorWorkOrder(
            id=w.id,
            number=w.number,
            property=w.property.name,
            unit=w.unit.name if w.unit else None,
            summary=w.summary,
            status=w.status,
            opened_at=w.opened_at,
        )
        for w in assigned_to(conn, session.pmc_id, vendor_id)
    ]
    return VendorPage(
        **found.model_dump(), insurance_standing=found.standing, bills=bills, work_orders=orders
    )


@router.post("/{vendor_id}/archive", status_code=204)
def archive_vendor(session: Staff, conn: Conn, vendor_id: UUID, body: Archiving) -> None:
    """Archives a vendor (refused while a bill of theirs is unpaid, money is set aside for them,
    a work order last assigned to them is open, or a recurring bill or job of theirs runs) or
    brings them back."""
    found(conn, "trust_vendors", session.pmc_id, vendor_id, "vendor")
    archive(conn, session.pmc_id, "vendor_id", vendor_id, body.archived)


@router.post("/{vendor_id}/name", status_code=204)
def rename_vendor(session: Staff, conn: Conn, vendor_id: UUID, body: Rename) -> None:
    """Renames a vendor; the audit log keeps the name they had."""
    if (
        conn.execute(
            "UPDATE trust_vendors SET display_name = %s WHERE pmc_id = %s AND id = %s RETURNING id",
            (body.name, session.pmc_id, vendor_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such vendor")
