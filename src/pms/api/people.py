"""Staff see who in their PMC can sign in: every staff member, owner and tenant, with the
email of their login and where it stands (no login, invited, active or disabled)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from pms.api.deps import Conn, Staff

router = APIRouter(prefix="/api")

PEOPLE = """
WITH people AS (
    SELECT 'staff' AS kind, id, display_name FROM trust_staff WHERE pmc_id = %(pmc)s
    UNION ALL
    SELECT 'owner', id, display_name FROM trust_owners WHERE pmc_id = %(pmc)s
    UNION ALL
    SELECT 'tenant', id, display_name FROM trust_tenants WHERE pmc_id = %(pmc)s
)
SELECT p.kind, p.id, p.display_name, l.email,
       CASE WHEN l.id IS NULL THEN 'no login'
            WHEN l.disabled_at IS NOT NULL THEN 'disabled'
            WHEN s.login_id IS NULL OR (p.kind = 'staff' AND s.totp_confirmed_at IS NULL)
                THEN 'invited'
            ELSE 'active' END
FROM people AS p
LEFT JOIN trust_logins AS l
  ON l.pmc_id = %(pmc)s AND p.id = coalesce(l.staff_id, l.owner_id, l.tenant_id)
LEFT JOIN trust_login_secrets AS s ON s.login_id = l.id
ORDER BY array_position(ARRAY['staff', 'owner', 'tenant'], p.kind),
         p.display_name COLLATE "C", p.id
"""


class Person(BaseModel):
    kind: Literal["staff", "owner", "tenant"]
    person_id: UUID
    name: str
    email: str | None
    status: Literal["no login", "invited", "active", "disabled"]


@router.get("/people")
def people(session: Staff, conn: Conn) -> list[Person]:
    rows = conn.execute(PEOPLE, {"pmc": session.pmc_id}).fetchall()
    return [Person(kind=k, person_id=i, name=n, email=e, status=s) for k, i, n, e, s in rows]
