"""What an application collects about each applicant and their household (ADR 0010 phase 2, part
G; migration 0155), and where it goes once an applicant becomes a tenant.

The application form asks each applicant for a phone, their current address, their previous
landlord (a name, a phone and the days they rented) and employer if they have them, an emergency
contact and a proof of income (a PDF, JPEG or PNG); and of the household, others who would live
there (names), pets (an assistance animal marked, which is never charged pet rent, a fee or a
deposit) and vehicles. Nothing more: no question about anything fair housing protects, and never
a Social Security number or a date of birth (the screening partner keeps those). The API still
takes an application without them, as it did before; the form asks for them.

When an application's offer becomes its lease, each tenant who was an applicant (the signer with
the applicant's email and the tenant's name) is linked to them once (trust_tenant_applicants):
their email and phone become the tenant's contact details, their emergency contact the tenant's,
and the household the lease's occupants, pets and vehicles. The rest (the address they left,
their previous landlord, employer and proof of income) stays with the application and shows on
the tenant's page. Staff change a tenant's emergency contact there. The AI role reads none of it.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from pms.api import directory, documents
from pms.api.deps import Conn, Staff
from pms.api.directory import City, Phone, PostalCode, State, Street
from pms.api.household import Long, NewPet, NewVehicle

router = APIRouter(prefix="/api/tenants")

# At most this many of each in an application's household.
MOST = 10
INCOME_PROOF = "Proof of income"


class AddressIn(BaseModel):
    line_1: Street
    line_2: Street | None = None
    city: City
    state: State
    postal_code: PostalCode


class PreviousLandlordIn(BaseModel):
    name: Long
    phone: Phone | None = None
    from_on: date | None = None
    to_on: date | None = None

    @model_validator(mode="after")
    def in_order(self) -> PreviousLandlordIn:
        if self.from_on and self.to_on and self.from_on > self.to_on:
            raise ValueError("the days rented from a previous landlord run from the first")
        return self


class EmergencyContactIn(BaseModel):
    name: Long
    phone: Phone


class IncomeProofIn(BaseModel):
    content_type: documents.ContentType
    # The file, base64: 5 MB comes to about 7 million characters.
    content_base64: str = Field(min_length=4, max_length=7_000_000)


class DetailsIn(BaseModel):
    """What an applicant says of themselves, besides their name and email."""

    phone: Phone | None = None
    address: AddressIn | None = None
    previous_landlord: PreviousLandlordIn | None = None
    employer: Long | None = None
    emergency_contact: EmergencyContactIn | None = None
    income_proof: IncomeProofIn | None = None

    @model_validator(mode="after")
    def phone_and_address(self) -> DetailsIn:
        if (self.phone is None) != (self.address is None):
            raise ValueError("an applicant's phone and current address come together")
        if self.phone is None and (self.previous_landlord or self.employer):
            raise ValueError("a previous landlord or employer comes with a phone and address")
        return self


class HouseholdIn(BaseModel):
    """Who else would live there, their pets and their vehicles."""

    occupants: Annotated[list[Long], Field(max_length=MOST)] = []
    pets: Annotated[list[NewPet], Field(max_length=MOST)] = []
    vehicles: Annotated[list[NewVehicle], Field(max_length=MOST)] = []


def keep(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    application_id: UUID,
    details: dict[UUID, DetailsIn],
    household: HouseholdIn | None,
) -> None:
    """Keeps what an application gave with it, in the application's transaction: all of it or
    none."""
    for applicant_id, given in details.items():
        if given.phone is not None and given.address is not None:
            landlord = given.previous_landlord
            conn.execute(
                "INSERT INTO trust_applicant_details (applicant_id, pmc_id, phone, address_line_1,"
                " address_line_2, city, state, postal_code, previous_landlord,"
                " previous_landlord_phone, previous_from, previous_to, employer)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    applicant_id,
                    pmc_id,
                    given.phone,
                    given.address.line_1,
                    given.address.line_2,
                    given.address.city,
                    given.address.state,
                    given.address.postal_code,
                    None if landlord is None else landlord.name,
                    None if landlord is None else landlord.phone,
                    None if landlord is None else landlord.from_on,
                    None if landlord is None else landlord.to_on,
                    given.employer,
                ),
            )
        if given.emergency_contact is not None:
            conn.execute(
                "INSERT INTO trust_emergency_contacts (pmc_id, applicant_id, display_name, phone)"
                " VALUES (%s, %s, %s, %s)",
                (pmc_id, applicant_id, given.emergency_contact.name, given.emergency_contact.phone),
            )
        if given.income_proof is not None:
            documents.keep(
                conn,
                pmc_id,
                documents.NewDocument(
                    kind="income_proof",
                    about_id=applicant_id,
                    title=INCOME_PROOF,
                    **given.income_proof.model_dump(),
                ),
            )
    if household is None:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO trust_application_occupants (pmc_id, application_id, display_name)"
            " VALUES (%s, %s, %s)",
            [(pmc_id, application_id, name) for name in household.occupants],
        )
        cur.executemany(
            "INSERT INTO trust_application_pets (pmc_id, application_id, display_name, kind,"
            " breed, weight_lbs, assistance_animal) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                (pmc_id, application_id, p.name, p.kind, p.breed, p.weight_lbs, p.assistance_animal)
                for p in household.pets
            ],
        )
        cur.executemany(
            "INSERT INTO trust_application_vehicles (pmc_id, application_id, make, model, color,"
            " plate, plate_state) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                (pmc_id, application_id, v.make, v.model, v.color, v.plate, v.plate_state)
                for v in household.vehicles
            ],
        )


# --- what staff see ------------------------------------------------------------------------------


class Address(BaseModel):
    line_1: str
    line_2: str | None
    city: str
    state: str
    postal_code: str


class PreviousLandlord(BaseModel):
    name: str
    phone: str | None
    from_on: date | None
    to_on: date | None


class EmergencyContact(BaseModel):
    name: str
    phone: str
    recorded_at: datetime


class Details(BaseModel):
    """What an applicant gave of themselves: none of it from an application before migration
    0155, or one that came without it."""

    phone: str | None = None
    address: Address | None = None
    previous_landlord: PreviousLandlord | None = None
    employer: str | None = None
    emergency_contact: EmergencyContact | None = None
    # Their proof of income: documents served at /api/documents/<id>/file.
    income_proofs: list[UUID] = []


class Pet(BaseModel):
    name: str
    kind: str
    breed: str | None
    weight_lbs: str | None
    assistance_animal: bool


class Vehicle(BaseModel):
    make: str
    model: str | None
    color: str | None
    plate: str
    plate_state: str | None


class Household(BaseModel):
    occupants: list[str] = []
    pets: list[Pet] = []
    vehicles: list[Vehicle] = []


EMERGENCY = (
    "SELECT DISTINCT ON ({who}) {who}, display_name, phone, created_at"
    " FROM trust_emergency_contacts WHERE {who} = ANY(%s)"
    " ORDER BY {who}, created_at DESC, id DESC"
)


def details_of(conn: psycopg.Connection[Any], applicants: list[UUID]) -> dict[UUID, Details]:
    """Each applicant's details, as they gave them."""
    found = {i: Details() for i in applicants}
    for row in conn.execute(
        "SELECT applicant_id, phone, address_line_1, address_line_2, city, state, postal_code,"
        " previous_landlord, previous_landlord_phone, previous_from, previous_to, employer"
        " FROM trust_applicant_details WHERE applicant_id = ANY(%s)",
        (applicants,),
    ).fetchall():
        i, phone, line_1, line_2, city, state, postal, landlord, landlord_phone, since, to, job = (
            row
        )
        found[i].phone = phone
        found[i].address = Address(
            line_1=line_1, line_2=line_2, city=city, state=state, postal_code=postal
        )
        if landlord is not None:
            found[i].previous_landlord = PreviousLandlord(
                name=landlord, phone=landlord_phone, from_on=since, to_on=to
            )
        found[i].employer = job
    for i, name, phone, at in conn.execute(
        EMERGENCY.format(who="applicant_id"), (applicants,)
    ).fetchall():
        found[i].emergency_contact = EmergencyContact(name=name, phone=phone, recorded_at=at)
    for i, document in conn.execute(
        "SELECT d.applicant_id, d.id FROM trust_documents AS d"
        " WHERE d.applicant_id = ANY(%s) AND d.kind = 'income_proof' AND NOT EXISTS ("
        "   SELECT 1 FROM trust_document_removals AS r WHERE r.document_id = d.id)"
        " ORDER BY d.applicant_id, d.created_at, d.id",
        (applicants,),
    ).fetchall():
        found[i].income_proofs.append(document)
    return found


def households_of(conn: psycopg.Connection[Any], applications: list[UUID]) -> dict[UUID, Household]:
    """Each application's household, in the order it listed them."""
    found = {i: Household() for i in applications}
    for i, name in conn.execute(
        "SELECT application_id, display_name FROM trust_application_occupants"
        " WHERE application_id = ANY(%s) ORDER BY application_id, created_at, id",
        (applications,),
    ).fetchall():
        found[i].occupants.append(name)
    for i, name, kind, breed, weight, assistance in conn.execute(
        "SELECT application_id, display_name, kind, breed, weight_lbs, assistance_animal"
        " FROM trust_application_pets WHERE application_id = ANY(%s)"
        " ORDER BY application_id, created_at, id",
        (applications,),
    ).fetchall():
        found[i].pets.append(
            Pet(
                name=name,
                kind=kind,
                breed=breed,
                weight_lbs=None if weight is None else f"{weight}",
                assistance_animal=assistance,
            )
        )
    for i, make, model, color, plate, plate_state in conn.execute(
        "SELECT application_id, make, model, color, plate, plate_state"
        " FROM trust_application_vehicles WHERE application_id = ANY(%s)"
        " ORDER BY application_id, created_at, id",
        (applications,),
    ).fetchall():
        found[i].vehicles.append(
            Vehicle(make=make, model=model, color=color, plate=plate, plate_state=plate_state)
        )
    return found


# --- carried to the tenant and the lease ---------------------------------------------------------

# The tenants of an application's lease and the applicants each one was: the tenant signer with
# the applicant's email and the tenant's name.
BECAME = """
SELECT a.id, a.email, t.id, c.lease_id, o.application_id
FROM trust_lease_offers AS o
JOIN trust_lease_offer_completions AS c ON c.offer_id = o.id
JOIN trust_applicants AS a ON a.application_id = o.application_id
JOIN trust_lease_offer_signers AS s
  ON s.offer_id = o.id AND s.role = 'tenant' AND s.email = a.email
JOIN trust_lease_tenants AS lt ON lt.lease_id = c.lease_id
JOIN trust_tenants AS t ON t.id = lt.tenant_id AND t.display_name = s.display_name
WHERE o.pmc_id = %s AND o.id = %s
"""


def carry_over(conn: psycopg.Connection[Any], pmc_id: UUID, offer_id: UUID) -> None:
    """Once an application's offer became its lease: links each tenant to the applicant they were
    and carries their contact details and emergency contact to them, and the household to the
    lease. Once: a tenant or applicant already linked, or a lease with a household already, is
    left as it is. A tenant whose name two signers share is left unlinked."""
    pairs = conn.execute(BECAME, (pmc_id, offer_id)).fetchall()
    tenants = Counter(tenant for _, _, tenant, _, _ in pairs)
    applicants = Counter(applicant for applicant, _, _, _, _ in pairs)
    for applicant, email, tenant, lease, _ in pairs:
        if tenants[tenant] > 1 or applicants[applicant] > 1:
            continue
        linked = conn.execute(
            "INSERT INTO trust_tenant_applicants (tenant_id, pmc_id, applicant_id, lease_id)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING tenant_id",
            (tenant, pmc_id, applicant, lease),
        ).fetchone()
        if linked is None:
            continue
        details = details_of(conn, [applicant])[applicant]
        if directory.contact_of(conn, "tenant_id", tenant) == directory.Contact():
            directory.record_contact(
                conn,
                pmc_id,
                "tenant_id",
                tenant,
                directory.Contact(email=email, phone=details.phone),
            )
        if details.emergency_contact is not None:
            conn.execute(
                "INSERT INTO trust_emergency_contacts (pmc_id, tenant_id, display_name, phone)"
                " VALUES (%s, %s, %s, %s)",
                (pmc_id, tenant, details.emergency_contact.name, details.emergency_contact.phone),
            )
    made = conn.execute(
        "SELECT c.lease_id, o.application_id FROM trust_lease_offers AS o"
        " JOIN trust_lease_offer_completions AS c ON c.offer_id = o.id"
        " WHERE o.pmc_id = %s AND o.id = %s AND o.application_id IS NOT NULL",
        (pmc_id, offer_id),
    ).fetchone()
    if made is None:
        return
    lease, application = made
    already = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM trust_lease_occupants WHERE lease_id = %(l)s)"
        " OR EXISTS (SELECT 1 FROM trust_lease_pets WHERE lease_id = %(l)s)"
        " OR EXISTS (SELECT 1 FROM trust_lease_vehicles WHERE lease_id = %(l)s)",
        {"l": lease},
    ).fetchone()
    if already is not None and already[0]:
        return
    params = {"pmc": pmc_id, "lease": lease, "application": application}
    conn.execute(
        "INSERT INTO trust_lease_occupants (pmc_id, lease_id, display_name)"
        " SELECT %(pmc)s, %(lease)s, display_name FROM trust_application_occupants"
        " WHERE application_id = %(application)s ORDER BY created_at, id",
        params,
    )
    conn.execute(
        "INSERT INTO trust_lease_pets (pmc_id, lease_id, display_name, kind, breed, weight_lbs,"
        " assistance_animal) SELECT %(pmc)s, %(lease)s, display_name, kind, breed, weight_lbs,"
        " assistance_animal FROM trust_application_pets WHERE application_id = %(application)s"
        " ORDER BY created_at, id",
        params,
    )
    conn.execute(
        "INSERT INTO trust_lease_vehicles (pmc_id, lease_id, make, model, color, plate,"
        " plate_state) SELECT %(pmc)s, %(lease)s, make, model, color, plate, plate_state"
        " FROM trust_application_vehicles WHERE application_id = %(application)s"
        " ORDER BY created_at, id",
        params,
    )


# --- a tenant's page ---------------------------------------------------------------------------


class Applied(BaseModel):
    """The application a tenant made, as they gave it."""

    application_id: UUID
    submitted_at: datetime
    details: Details


class FromApplication(BaseModel):
    emergency_contact: EmergencyContact | None
    # Their application, when they became a tenant through one (migration 0155).
    application: Applied | None


def tenant_of(conn: psycopg.Connection[Any], pmc_id: UUID, tenant_id: UUID) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM trust_tenants WHERE pmc_id = %s AND id = %s", (pmc_id, tenant_id)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such tenant")


def emergency_contact_of(conn: psycopg.Connection[Any], tenant_id: UUID) -> EmergencyContact | None:
    row = conn.execute(EMERGENCY.format(who="tenant_id"), ([tenant_id],)).fetchone()
    return None if row is None else EmergencyContact(name=row[1], phone=row[2], recorded_at=row[3])


@router.get("/{tenant_id}/application")
def from_application(session: Staff, conn: Conn, tenant_id: UUID) -> FromApplication:
    """The tenant's emergency contact, and the application they became a tenant through: the
    address they left, their previous landlord, employer and proof of income."""
    tenant_of(conn, session.pmc_id, tenant_id)
    row = conn.execute(
        "SELECT x.applicant_id, a.application_id, p.submitted_at FROM trust_tenant_applicants AS x"
        " JOIN trust_applicants AS a ON a.id = x.applicant_id"
        " JOIN trust_applications AS p ON p.id = a.application_id"
        " WHERE x.tenant_id = %s",
        (tenant_id,),
    ).fetchone()
    applied = None
    if row is not None:
        applicant, application, submitted = row
        applied = Applied(
            application_id=application,
            submitted_at=submitted,
            details=details_of(conn, [applicant])[applicant],
        )
    return FromApplication(
        emergency_contact=emergency_contact_of(conn, tenant_id), application=applied
    )


@router.post("/{tenant_id}/emergency-contact")
def set_emergency_contact(
    session: Staff, conn: Conn, tenant_id: UUID, new: EmergencyContactIn
) -> FromApplication:
    """The tenant's emergency contact from now on; the same again writes nothing."""
    tenant_of(conn, session.pmc_id, tenant_id)
    held = emergency_contact_of(conn, tenant_id)
    if held is None or (held.name, held.phone) != (new.name, new.phone):
        conn.execute(
            "INSERT INTO trust_emergency_contacts (pmc_id, tenant_id, display_name, phone)"
            " VALUES (%s, %s, %s, %s)",
            (session.pmc_id, tenant_id, new.name, new.phone),
        )
    return from_application(session, conn, tenant_id)
