"""The old system's past transactions, as staff read them (migration 0107; the founder's review
q129): a tenant's, an owner's or a vendor's lines from before the switch, newest first, a page at a
time, under "Before the switch" on their page. History is never posted; it is brought in at
Settings, Import (src/pms/api/switching.py).
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pms.api import rent
from pms.api.deps import Conn, Staff
from pms.api.rentals import Page, Paged, PerPage

router = APIRouter(prefix="/api/history")


class HistoryLine(BaseModel):
    posted_on: date
    # The old system's word for it (Rent, Payment, Owner draw...).
    type: str
    description: str
    # As the old system printed it.
    amount: str
    reference: str | None
    # A tenant's unit as the old system named it; an owner's property.
    unit: str | None
    property: str | None


# Each person's lines (%(pmc)s, %(person)s), with the property of an owner's account.
WHOSE = {
    "tenant": ("trust_tenants", "h.tenant_id = %(person)s"),
    "vendor": ("trust_vendors", "h.vendor_id = %(person)s"),
    "owner": ("trust_owners", "a.owner_id = %(person)s"),
}


@router.get("")
def history(
    session: Staff,
    conn: Conn,
    tenant_id: UUID | None = None,
    owner_id: UUID | None = None,
    vendor_id: UUID | None = None,
    page: Page = 1,
    per_page: PerPage = 50,
) -> Paged[HistoryLine]:
    """One tenant's, owner's or vendor's lines from before the switch, newest first."""
    asked = {"tenant": tenant_id, "owner": owner_id, "vendor": vendor_id}
    given = [(who, person) for who, person in asked.items() if person is not None]
    if len(given) != 1:
        raise HTTPException(422, "name one tenant, owner or vendor")
    who, person = given[0]
    table, where = WHOSE[who]
    if (
        conn.execute(
            f"SELECT 1 FROM {table} WHERE pmc_id = %s AND id = %s",  # noqa: S608
            (session.pmc_id, person),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, f"no such {who}")
    rows = conn.execute(
        "SELECT h.posted_on, h.kind, h.description, h.amount, h.reference, h.unit,"  # noqa: S608
        " p.display_name, count(*) OVER ()"
        " FROM trust_history_lines AS h"
        " LEFT JOIN trust_ledger_accounts AS a ON a.ledger_account_id = h.owner_account"
        " LEFT JOIN trust_properties AS p ON p.id = a.property_id"
        f" WHERE h.pmc_id = %(pmc)s AND {where}"
        " ORDER BY h.posted_on DESC, h.file_id, h.line_no DESC"
        " LIMIT %(per_page)s OFFSET %(offset)s",
        {
            "pmc": session.pmc_id,
            "person": person,
            "per_page": per_page,
            "offset": (page - 1) * per_page,
        },
    ).fetchall()
    return Paged(
        items=[
            HistoryLine(
                posted_on=posted,
                type=kind,
                description=description,
                amount=rent.cents(amount),
                reference=reference,
                unit=unit,
                property=prop,
            )
            for posted, kind, description, amount, reference, unit, prop, _ in rows
        ],
        total=rows[0][-1] if rows else 0,
        page=page,
        per_page=per_page,
    )
