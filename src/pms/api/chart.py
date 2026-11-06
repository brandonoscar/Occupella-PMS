"""Settings, Chart of accounts (the founder's chart of accounts pass, 2026-11-05): every account of
the company's chart by section, with its code, and the edits staff make to it.

The chart is the ledger's control accounts (one for each kind of money the trust holds), the
income statement's own income and expense lines, the company's charge types and its expense
accounts (migrations 20261106000200 to 20261106000230). Staff give any account another code,
rename or archive (and bring back) a charge type or an expense account, and say who keeps a
charge type's fees. Each edit is a new row, naming who made it; the database refuses a code
taken or outside its section, a name taken, and archiving what an open bill or a running monthly
charge still uses. Nothing here moves money or rewrites what was posted.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StringConstraints, model_validator

from pms.api.deps import Conn, Staff, refusal
from pms.api.setup import INCOME_LINES, SCHEDULE_E, ScheduleELine

router = APIRouter(prefix="/api/setup")

Section = Literal["asset", "liability", "income", "expense"]
Payee = Literal["owner", "company"]
# The chart's fixed lines (trust_chart_lines): a control account or an income statement line.
LineKey = Literal[
    "bank_cash",
    "owner_property",
    "tenant_deposit",
    "pet_deposit",
    "key_deposit",
    # The second pass's (migration 20261106000243): a memo of deposits owners hold outside
    # trust, money received not yet applied, and concessions, a contra to rent.
    "owner_held_deposits",
    "prepaid_rent",
    "unapplied_cash",
    "vendor_payable",
    "pmc_income",
    "rent",
    "concessions",
    "recurring_charges",
    "late_fees",
    "nsf_fees",
    "other_charges",
    "unassigned_bills",
    "leasing_fees",
    "management_fees",
    "deposit_interest",
]


class ChartAccount(BaseModel):
    section: Section
    code: str
    name: str
    type: str
    # Exactly one says which account it is: a fixed line, a charge type or an expense account.
    line: LineKey | None = None
    charge_type_id: UUID | None = None
    expense_account_id: UUID | None = None
    schedule_e_line: int | None = None
    # Who keeps an income line's money: a charge type's payee today; the owner for the rest.
    kept_by: Payee | None = None
    archived: bool = False


class Chart(BaseModel):
    accounts: list[ChartAccount]
    schedule_e_lines: list[ScheduleELine]


# Every account with its code today, assets first, then by code; the same as
# trust_report_chart_of_accounts.
CHART = """
WITH codes AS (SELECT * FROM trust_account_codes_of(%(pmc)s)),
accounts AS (
    SELECT l.section, c.code, l.name, l.account_type, l.line, NULL::uuid AS charge_type_id,
           NULL::uuid AS expense_account_id, l.schedule_e,
           CASE WHEN l.section = 'income' THEN 'owner' END AS kept_by, false AS archived
    FROM trust_chart_lines() AS l
    JOIN codes AS c ON c.line = l.line
    UNION ALL
    SELECT 'income', c.code, trust_charge_type_name(t.id), 'Charge type', NULL, t.id, NULL, NULL,
           trust_charge_type_payee(t.id), trust_charge_type_archived(t.id)
    FROM trust_charge_types AS t
    JOIN codes AS c ON c.charge_type_id = t.id
    WHERE t.pmc_id = %(pmc)s
    UNION ALL
    SELECT 'expense', c.code, trust_expense_account_name(e.id), 'Expense account', NULL, NULL,
           e.id, e.schedule_e_line, NULL, trust_expense_account_archived(e.id)
    FROM trust_expense_accounts AS e
    JOIN codes AS c ON c.expense_account_id = e.id
    WHERE e.pmc_id = %(pmc)s
)
SELECT a.*
FROM accounts AS a
ORDER BY array_position(ARRAY['asset', 'liability', 'income', 'expense'], a.section),
         a.code COLLATE "C", a.name COLLATE "C"
"""


@router.get("/chart-of-accounts")
def chart(session: Staff, conn: Conn) -> Chart:
    """The company's chart of accounts: every account by section and code."""
    return Chart(
        accounts=[
            ChartAccount(
                section=section,
                code=code,
                name=name,
                type=kind,
                line=line,
                charge_type_id=charge_type,
                expense_account_id=expense_account,
                schedule_e_line=schedule_e,
                kept_by=kept_by,
                archived=archived,
            )
            for (
                section,
                code,
                name,
                kind,
                line,
                charge_type,
                expense_account,
                schedule_e,
                kept_by,
                archived,
            ) in conn.execute(CHART, {"pmc": session.pmc_id}).fetchall()
        ],
        schedule_e_lines=[ScheduleELine(line=n, label=label) for n, label in SCHEDULE_E.items()],
    )


Code = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[1-9][0-9]{3}$")]


class NewCode(BaseModel):
    """An account's new code: name exactly one of a fixed line, a charge type, an expense
    account."""

    code: Code
    line: LineKey | None = None
    charge_type_id: UUID | None = None
    expense_account_id: UUID | None = None

    @model_validator(mode="after")
    def one_account(self) -> NewCode:
        named = (self.line, self.charge_type_id, self.expense_account_id)
        if sum(x is not None for x in named) != 1:
            raise ValueError("name one account: a line, a charge type or an expense account")
        return self


class Coded(BaseModel):
    code: str
    changed: bool


def found(conn: psycopg.Connection[Any], table: str, pmc_id: UUID, record: UUID, what: str) -> None:
    row = conn.execute(
        f"SELECT 1 FROM {table} WHERE pmc_id = %s AND id = %s",  # noqa: S608 - table is ours
        (pmc_id, record),
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"no such {what}")


def refused(error: psycopg.Error) -> HTTPException:
    return HTTPException(409, refusal(error))


@router.post("/codes", status_code=201)
def change_code(session: Staff, conn: Conn, new: NewCode) -> Coded:
    """Gives an account another code. Its old code is kept as a row of its own; nothing posted
    changes. The same code again changes nothing."""
    if new.charge_type_id is not None:
        found(conn, "trust_charge_types", session.pmc_id, new.charge_type_id, "charge type")
    if new.expense_account_id is not None:
        found(
            conn,
            "trust_expense_accounts",
            session.pmc_id,
            new.expense_account_id,
            "expense account",
        )
    now = conn.execute(
        "SELECT code FROM trust_account_codes_of(%s)"
        " WHERE line IS NOT DISTINCT FROM %s AND charge_type_id IS NOT DISTINCT FROM %s"
        " AND expense_account_id IS NOT DISTINCT FROM %s",
        (session.pmc_id, new.line, new.charge_type_id, new.expense_account_id),
    ).fetchone()
    if now is not None and now[0] == new.code:
        return Coded(code=new.code, changed=False)
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_account_codes"
                " (pmc_id, line, charge_type_id, expense_account_id, code)"
                " VALUES (%s, %s, %s, %s, %s)",
                (session.pmc_id, new.line, new.charge_type_id, new.expense_account_id, new.code),
            )
    except (psycopg.errors.UniqueViolation, psycopg.errors.CheckViolation) as error:
        raise refused(error) from None
    return Coded(code=new.code, changed=True)


ExpenseName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
TypeName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class NewExpenseName(BaseModel):
    name: ExpenseName


class NewTypeName(BaseModel):
    name: TypeName


class Archived(BaseModel):
    archived: bool


class NewPayee(BaseModel):
    payee: Payee


class Changed(BaseModel):
    changed: bool


def append(conn: psycopg.Connection[Any], statement: str, params: tuple[Any, ...]) -> None:
    """One row of the chart's history, its refusal in words."""
    try:
        with conn.transaction():
            conn.execute(statement, params)
    except (psycopg.errors.UniqueViolation, psycopg.errors.CheckViolation) as error:
        raise refused(error) from None


@router.post("/expense-accounts/{account_id}/name", status_code=201)
def rename_expense_account(
    session: Staff, conn: Conn, account_id: UUID, new: NewExpenseName
) -> Changed:
    """Renames an expense account: every report prints its new name, the past included."""
    found(conn, "trust_expense_accounts", session.pmc_id, account_id, "expense account")
    now = conn.execute("SELECT trust_expense_account_name(%s)", (account_id,)).fetchone()
    if now is not None and now[0] == new.name:
        return Changed(changed=False)
    append(
        conn,
        "INSERT INTO trust_expense_account_names (pmc_id, expense_account_id, name)"
        " VALUES (%s, %s, %s)",
        (session.pmc_id, account_id, new.name),
    )
    return Changed(changed=True)


@router.post("/expense-accounts/{account_id}/archive", status_code=201)
def archive_expense_account(session: Staff, conn: Conn, account_id: UUID, new: Archived) -> Changed:
    """Archives an expense account (no new bill goes in it; its history and past report lines
    stay), or brings it back. Refused while a bill not paid or a running recurring bill is in it."""
    found(conn, "trust_expense_accounts", session.pmc_id, account_id, "expense account")
    now = conn.execute("SELECT trust_expense_account_archived(%s)", (account_id,)).fetchone()
    if now is not None and now[0] == new.archived:
        return Changed(changed=False)
    append(
        conn,
        "INSERT INTO trust_expense_account_archivals (pmc_id, expense_account_id, archived)"
        " VALUES (%s, %s, %s)",
        (session.pmc_id, account_id, new.archived),
    )
    return Changed(changed=True)


@router.post("/charge-types/{type_id}/name", status_code=201)
def rename_charge_type(session: Staff, conn: Conn, type_id: UUID, new: NewTypeName) -> Changed:
    """Renames a charge type: every income statement prints its new name, the past included."""
    found(conn, "trust_charge_types", session.pmc_id, type_id, "charge type")
    if new.name.lower() in INCOME_LINES:
        raise HTTPException(422, f"{new.name} is a line of the income statement already")
    now = conn.execute("SELECT trust_charge_type_name(%s)", (type_id,)).fetchone()
    if now is not None and now[0] == new.name:
        return Changed(changed=False)
    append(
        conn,
        "INSERT INTO trust_charge_type_names (pmc_id, charge_type_id, name) VALUES (%s, %s, %s)",
        (session.pmc_id, type_id, new.name),
    )
    return Changed(changed=True)


@router.post("/charge-types/{type_id}/archive", status_code=201)
def archive_charge_type(session: Staff, conn: Conn, type_id: UUID, new: Archived) -> Changed:
    """Archives a charge type (no new fee or monthly charge takes it; its fees keep it), or
    brings it back. Refused while a monthly charge of it still runs."""
    found(conn, "trust_charge_types", session.pmc_id, type_id, "charge type")
    now = conn.execute("SELECT trust_charge_type_archived(%s)", (type_id,)).fetchone()
    if now is not None and now[0] == new.archived:
        return Changed(changed=False)
    append(
        conn,
        "INSERT INTO trust_charge_type_archivals (pmc_id, charge_type_id, archived)"
        " VALUES (%s, %s, %s)",
        (session.pmc_id, type_id, new.archived),
    )
    return Changed(changed=True)


@router.post("/charge-types/{type_id}/payee", status_code=201)
def change_payee(session: Staff, conn: Conn, type_id: UUID, new: NewPayee) -> Changed:
    """Who keeps the money of this type's fees, for fees charged from now on: the owner, or the
    company (paid into its fee account in trust, to be taken out as its other fees are). A fee
    charged before keeps the payee it was charged under."""
    found(conn, "trust_charge_types", session.pmc_id, type_id, "charge type")
    now = conn.execute("SELECT trust_charge_type_payee(%s)", (type_id,)).fetchone()
    if now is not None and now[0] == new.payee:
        return Changed(changed=False)
    append(
        conn,
        "INSERT INTO trust_charge_type_payees (pmc_id, charge_type_id, payee) VALUES (%s, %s, %s)",
        (session.pmc_id, type_id, new.payee),
    )
    return Changed(changed=True)
