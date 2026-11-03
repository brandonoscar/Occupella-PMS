"""A tenant's notice to move out (the portals' second version, migration 20261103000100): the
last day they will live there, with any note, given from the tenant portal; the latest of a
lease's holds. It ends nothing and moves no money: staff end the lease as before. Read here for
the tenant portal (src/pms/api/tenant.py), the lease page and the move-outs (staff), and the
owner's view of their units (src/pms/api/owner_portal.py). The AI role reads none of it.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg
from pydantic import BaseModel


class MoveOutNotice(BaseModel):
    leaving_on: date
    note: str | None
    given_by: str
    given_at: datetime


NOTICE = """
SELECT n.leaving_on, n.note, t.display_name, n.created_at
FROM trust_move_out_notices AS n JOIN trust_tenants AS t ON t.id = n.tenant_id
WHERE n.pmc_id = %s AND n.lease_id = %s
ORDER BY n.created_at DESC, n.id DESC LIMIT 1
"""


def notice_of(conn: psycopg.Connection[Any], pmc_id: UUID, lease: UUID) -> MoveOutNotice | None:
    """The latest notice to move out given for a lease, if any."""
    row = conn.execute(NOTICE, (pmc_id, lease)).fetchone()
    if row is None:
        return None
    leaving_on, note, by, at = row
    return MoveOutNotice(leaving_on=leaving_on, note=note, given_by=by, given_at=at)
