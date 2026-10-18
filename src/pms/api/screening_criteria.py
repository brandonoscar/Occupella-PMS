"""A company's screening criteria (migration 0089; the founder's review, q085; docs/law.md): the
same written criteria for every applicant. Staff set them in Company setup: the income, as times
the rent, that is approved and that is approved on conditions (less is declined), and the criteria
in words, which the apply page shows every applicant. The screening partner applies them
(src/pms/leasing/screening.py) and each screening records which it applied; a decision against the
latest screening's recommendation says why (src/pms/api/leasing.py).

Append-only, the latest row holding, naming who changed it; saving the same again writes nothing.
Without any, the partner's own (three times the rent approved, twice on conditions) apply.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from pms.api.deps import Conn, Staff
from pms.leasing import screening

router = APIRouter(prefix="/api/leasing/screening-criteria")

Multiple = Annotated[Decimal, Field(ge=0, le=10, decimal_places=2)]
Written = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class NewCriteria(BaseModel):
    approve_multiple: Multiple
    conditional_multiple: Multiple
    written: Written | None = None


class Criteria(BaseModel):
    # None until the company sets its own: the partner's then apply.
    id: UUID | None
    approve_multiple: str
    conditional_multiple: str
    written: str | None
    recorded_at: datetime | None
    recorded_by: str | None


LATEST = """
SELECT c.id, c.approve_multiple, c.conditional_multiple, c.written, c.created_at, s.display_name
FROM trust_screening_criteria AS c
LEFT JOIN trust_staff AS s ON s.id = c.recorded_by
WHERE c.pmc_id = %s
ORDER BY c.created_at DESC, c.id DESC
LIMIT 1
"""


def in_force(conn: psycopg.Connection[Any], pmc_id: UUID) -> Criteria:
    """The company's criteria now, or the partner's own when it has set none."""
    row = conn.execute(LATEST, (pmc_id,)).fetchone()
    if row is None:
        default = screening.DEFAULT
        return Criteria(
            id=None,
            approve_multiple=f"{default.approve_multiple:.2f}",
            conditional_multiple=f"{default.conditional_multiple:.2f}",
            written=None,
            recorded_at=None,
            recorded_by=None,
        )
    return Criteria(
        id=row[0],
        approve_multiple=f"{row[1]:.2f}",
        conditional_multiple=f"{row[2]:.2f}",
        written=row[3],
        recorded_at=row[4],
        recorded_by=row[5],
    )


def for_partner(criteria: Criteria) -> screening.Criteria:
    return screening.Criteria(
        approve_multiple=Decimal(criteria.approve_multiple),
        conditional_multiple=Decimal(criteria.conditional_multiple),
    )


@router.get("")
def criteria(session: Staff, conn: Conn) -> Criteria:
    """The company's screening criteria in force."""
    return in_force(conn, session.pmc_id)


@router.post("", status_code=201)
def set_criteria(session: Staff, conn: Conn, new: NewCriteria) -> Criteria:
    """New screening criteria from now on; the same again writes nothing."""
    if new.approve_multiple < 1:
        raise HTTPException(422, "approval asks for at least the rent: 1.00 times or more")
    if new.conditional_multiple > new.approve_multiple:
        raise HTTPException(422, "conditions start at no more than what is approved outright")
    now = in_force(conn, session.pmc_id)
    if now.id is not None and (
        Decimal(now.approve_multiple),
        Decimal(now.conditional_multiple),
        now.written,
    ) == (new.approve_multiple, new.conditional_multiple, new.written):
        return now
    conn.execute(
        "INSERT INTO trust_screening_criteria"
        " (pmc_id, approve_multiple, conditional_multiple, written) VALUES (%s, %s, %s, %s)",
        (session.pmc_id, new.approve_multiple, new.conditional_multiple, new.written),
    )
    return in_force(conn, session.pmc_id)
