"""What every endpoint needs: a connection in the request's transaction, and who is asking."""

from __future__ import annotations

import re
from collections.abc import Iterator
from decimal import Decimal
from typing import Annotated, Any

import psycopg
from fastapi import Depends, HTTPException, Request, Response
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from pms import accounts, exports

COOKIE = "pms_session"
MEDIA = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


# What holds the money when the ledger refuses to take an account below zero (migration 0003:
# "trust: <kind> account <id> would go below zero (balance <n>)"). The account's id means
# nothing to staff, so the refusal names whose money it is and how much it is short.
HOLDERS = {
    "owner_property": "the owner's account",
    "prepaid_rent": "the tenant's prepaid rent",
    "tenant_deposit": "the tenant's deposit",
    "vendor_payable": "the vendor's account",
    "pmc_income": "the company's fee account",
}
BELOW_ZERO = re.compile(r"^(\w+) account \S+ would go below zero \(balance (-\d+(?:\.\d+)?)\)$")
# trust_draw_owner's refusal (migrations 0016 and 0017) names the owner's account by its id too.
# Drawing everything from an account holding less than its reserve asks for a negative amount.
NUMBER = r"(-?\d+(?:\.\d+)?)"
TOO_MUCH = re.compile(
    rf"^{NUMBER} is available to draw from \S+ \(its balance less a reserve of {NUMBER} and"
    rf" unpaid bills of {NUMBER}\); {NUMBER} is too much$"
)
# trust_withdraw_pmc_fees' refusals (migration 0059) name the fee account and request key.
FEES_TOO_MUCH = re.compile(rf"^{NUMBER} of the company's fees is in \S+; {NUMBER} is too much$")
FEES_KEY_USED = re.compile(
    rf"^request key \S+ already took {NUMBER} of the company's fees from \S+$"
)
# trust_take_owner_fee's (migration 0082) names the owner's account by its id.
OWNER_FEE_KEY_USED = re.compile(rf"^request key \S+ already took a fee of {NUMBER} from \S+$")

# trust_staff_roles' check (migration 0034) names the staff member by their id.
LAST_ADMIN = re.compile(r"^staff member \S+ is the PMC's last admin$")
# The ledger's commit-time tie-out (migration 0005) and closed periods (migration 0006) name the
# trust bank account by its id.
TIE_OUT = re.compile(rf"^trust bank account \S+ does not tie out \(off by {NUMBER}\)$")
CLOSED = re.compile(
    r"^trust bank account \S+ is reconciled and closed through .*; a transfer dated"
)


def dollars(amount: str) -> str:
    return f"${Decimal(amount):,.2f}"


def refusal(error: psycopg.Error) -> str:
    """A database refusal as the API says it: its first line, without the "trust: " every
    trust_* refusal starts with, and the ledger's refusals about money in words."""
    text = str(error).splitlines()[0].removeprefix("trust: ")
    short = BELOW_ZERO.match(text)
    if short is not None and short[1] in HOLDERS:
        return f"not enough money: {HOLDERS[short[1]]} would be {dollars(short[2][1:])} short"
    if LAST_ADMIN.match(text):
        return "the company keeps an admin: give another staff member admin first"
    off = TIE_OUT.match(text)
    if off is not None:
        return (
            "money moves to another trust bank account only with its cash: this would leave"
            f" one off by {dollars(off[1].removeprefix('-'))}"
        )
    if CLOSED.match(text):
        return "that day is in a period already reconciled and closed: date it in the next open one"
    draw = TOO_MUCH.match(text)
    if draw is not None:
        available, reserve, unpaid, asked = draw.groups()
        held = f"the {dollars(reserve)} reserve"
        if Decimal(unpaid) > 0:
            held += f" and {dollars(unpaid)} of unpaid bills"
        if Decimal(available) == 0:
            return f"nothing is available to draw: what the account holds is kept for {held}"
        return (
            f"only {dollars(available)} is available to draw (the balance less {held});"
            f" {dollars(asked)} is more than that"
        )
    fees = FEES_TOO_MUCH.match(text)
    if fees is not None:
        held, asked = fees.groups()
        if Decimal(held) <= 0:
            return "none of the company's fees is in this trust bank account to take"
        return (
            f"only {dollars(held)} of the company's fees is in this trust bank account;"
            f" {dollars(asked)} is more than that"
        )
    used = FEES_KEY_USED.match(text)
    if used is not None:
        return (
            f"this request already took {dollars(used[1])} of the company's fees: start a new one"
        )
    taken = OWNER_FEE_KEY_USED.match(text)
    if taken is not None:
        return f"this request already took a fee of {dollars(taken[1])}: start a new one"
    return text


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


# The areas of staff work a custom role may allow (migration 0057), as staff read them.
AREAS = {
    "rent": "rent",
    "bills": "bills",
    "owners": "owners' money",
    "budgets": "budgets",
    "journal": "the journal",
    "month-end": "month-end",
    "switching": "switching",
    "setup": "setup",
    "move-out": "move-in and move-out",
    "documents": "documents",
    "leasing": "leasing",
    "maintenance": "maintenance",
    "people": "who can sign in",
    "email": "email to tenants, owners and vendors",
}
# The paths that change each area. A staff request that changes anything under one of them
# needs the area (tests/api/test_custom_roles.py checks every staff write is here or is said
# not to be an area's).
AREA_PATHS = (
    ("/api/rent/", "rent"),
    ("/api/bank-accounts/", "rent"),  # bank deposits of what came in
    ("/api/payables/bills", "bills"),
    ("/api/payables/recurring", "bills"),
    ("/api/payables/management-fees", "owners"),
    ("/api/payables/leasing-fees", "owners"),
    ("/api/payables/owner-fees", "owners"),
    ("/api/payables/draws", "owners"),
    ("/api/payables/contributions", "owners"),
    ("/api/payables/company-fees", "owners"),
    ("/api/budgets", "budgets"),
    ("/api/journal", "journal"),
    ("/api/reconcile/", "month-end"),
    ("/api/switching/", "switching"),
    ("/api/setup/", "setup"),
    ("/api/company", "setup"),
    ("/api/properties/", "setup"),
    ("/api/property-groups", "setup"),
    ("/api/units/", "setup"),
    ("/api/tenants/", "setup"),
    ("/api/owners/", "setup"),
    ("/api/vendors", "setup"),
    ("/api/move-out/", "move-out"),  # a tenant leaving a lease too
    ("/api/leases/", "setup"),  # who lives there and guarantees it
    ("/api/inspections", "move-out"),
    ("/api/documents", "documents"),
    ("/api/notes", "documents"),
    ("/api/leasing/", "leasing"),
    ("/api/maintenance/", "maintenance"),
    ("/api/work-orders", "maintenance"),
    ("/api/invites", "people"),
    ("/api/emails", "email"),
)
READS = ("GET", "HEAD", "OPTIONS")


def area_of(path: str) -> str | None:
    return next((area for prefix, area in AREA_PATHS if path.startswith(prefix)), None)


def may(conn: psycopg.Connection[Any], session: accounts.Session, area: str) -> bool:
    row = conn.execute(
        "SELECT trust_staff_may(%s, %s, %s)", (session.pmc_id, session.person_id, area)
    ).fetchone()
    return bool(row and row[0])


def _only(kind: str, who: str) -> Any:
    def check(request: Request, session: Current, conn: Conn) -> accounts.Session:
        if session.kind != kind:
            raise HTTPException(403, f"only {who} may do this")
        area = None if request.method in READS else area_of(request.url.path)
        if kind == "staff" and area is not None and not may(conn, session, area):
            raise HTTPException(
                403, f"your roles don't let you change {AREAS[area]}: an admin can add it"
            )
        return session

    return check


# Every staff request passes this check, and every staff write its area's.
staff_only = _only("staff", "the PMC's staff")
Staff = Annotated[accounts.Session, Depends(staff_only)]
Owner = Annotated[accounts.Session, Depends(_only("owner", "an owner"))]
Tenant = Annotated[accounts.Session, Depends(_only("tenant", "a tenant"))]


def download(data: bytes, name: str, ext: str) -> Response:
    disposition = f'attachment; filename="{name}.{ext}"'
    return Response(
        data,
        media_type=MEDIA.get(ext, "application/zip"),
        headers={"Content-Disposition": disposition},
    )


class Rows(BaseModel):
    """A report as a portal page shows it. Values are text, so no float ever rounds a cent."""

    title: str
    columns: list[str]
    rows: list[list[str | None]]

    @classmethod
    def of(cls, table: exports.Table) -> Rows:
        return cls(
            title=table.title,
            columns=list(table.columns),
            rows=[[None if v is None else exports.text(v) for v in row] for row in table.rows],
        )
