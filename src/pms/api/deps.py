"""What every endpoint needs: a connection in the request's transaction, and who is asking."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Any

import psycopg
from fastapi import Depends, HTTPException, Request, Response
from psycopg_pool import ConnectionPool

from pms import accounts

COOKIE = "pms_session"
MEDIA = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


def connection(request: Request) -> Iterator[psycopg.Connection[Any]]:
    # Committed when the request succeeds, rolled back when it raises.
    pool: ConnectionPool[Any] = request.app.state.pool
    with pool.connection() as conn:
        yield conn


# scope="function": the commit happens before the answer is sent, so a client never acts on a
# write that isn't committed yet.
Conn = Annotated[psycopg.Connection[Any], Depends(connection, scope="function")]


def current(request: Request, conn: Conn) -> accounts.Session:
    token = request.cookies.get(COOKIE)
    session = accounts.session_for(conn, token) if token else None
    if session is None:
        raise HTTPException(401, "sign in first")
    if session.kind == "staff":
        # The audit log records this staff member for every write the request causes.
        conn.execute("SELECT set_config('trust.staff_id', %s, true)", (str(session.person_id),))
    return session


Current = Annotated[accounts.Session, Depends(current)]


def _only(kind: str, who: str) -> Any:
    def check(session: Current) -> accounts.Session:
        if session.kind != kind:
            raise HTTPException(403, f"only {who} may do this")
        return session

    return check


Staff = Annotated[accounts.Session, Depends(_only("staff", "the PMC's staff"))]
Owner = Annotated[accounts.Session, Depends(_only("owner", "an owner"))]
Tenant = Annotated[accounts.Session, Depends(_only("tenant", "a tenant"))]


def download(data: bytes, name: str, ext: str) -> Response:
    disposition = f'attachment; filename="{name}.{ext}"'
    return Response(
        data,
        media_type=MEDIA.get(ext, "application/zip"),
        headers={"Content-Disposition": disposition},
    )
