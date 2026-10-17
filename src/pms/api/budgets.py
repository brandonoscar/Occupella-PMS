"""Staff's budgets (migration 0055): what an owner's property is expected to bring in and cost
each month, on the income statement's own lines: each income class, the management and leasing
fees, each expense account and bills in none. A line's latest row is its budget, so a change
is a new row; a month left as it was writes nothing. A budget moves no money. The Budgets page
(ADR 0006, step 12) is one grid of a property's lines by month, saved at once.

trust_report_budget_vs_actual lays an owner's budget beside their income statement; staff
download it through /api/reports like every report.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from pms import exports
from pms.api.deps import Conn, Staff

router = APIRouter(prefix="/api/budgets")

Line = Literal[
    "rent",
    "recurring charges",
    "late fees",
    "NSF fees",
    "other charges",
    "management fees",
    "leasing fees",
    "expense account",
    "bills not in an expense account",
]
# The lines an owner's property has, in the income statement's order; each expense account
# goes between the fees and bills in none.
INCOME: list[Line] = ["rent", "recurring charges", "late fees", "NSF fees", "other charges"]
FEES: list[Line] = ["management fees", "leasing fees"]
Amount = Annotated[
    Decimal, Field(ge=Decimal(0), le=Decimal("100000000"), max_digits=11, decimal_places=2)
]
Year = Annotated[int, Field(ge=1900, le=9998)]
CENT = Decimal("0.01")


def cents(value: Decimal) -> str:
    return exports.text(Decimal(value).quantize(CENT))


class BudgetLine(BaseModel):
    line: Line
    expense_account_id: UUID | None
    label: str  # as the income statement names it
    months: list[str]  # January to December
    year: str  # their sum


class Budget(BaseModel):
    account: str
    owner_id: UUID
    owner: str
    property: str
    year: int
    lines: list[BudgetLine]


def account_of(conn: psycopg.Connection[Any], pmc_id: UUID, account: str) -> tuple[Any, ...]:
    row = conn.execute(
        "SELECT o.id, o.display_name, p.display_name FROM trust_ledger_accounts AS t"
        " JOIN trust_owners AS o ON o.id = t.owner_id"
        " JOIN trust_properties AS p ON p.id = t.property_id"
        " WHERE t.pmc_id = %s AND t.ledger_account_id = %s AND t.kind = 'owner_property'",
        (pmc_id, account),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such owner's property account")
    return tuple(row)


def latest(conn: psycopg.Connection[Any], account: str, year: int) -> dict[Any, list[Decimal]]:
    """Each line's latest budget for each month of the year, keyed by (line, expense account)."""
    found: dict[Any, list[Decimal]] = {}
    for line, expense, month, amount in conn.execute(
        "SELECT DISTINCT ON (b.line, b.expense_account_id, b.month)"
        " b.line, b.expense_account_id, extract(month FROM b.month)::integer, b.amount"
        " FROM trust_budgets AS b"
        " WHERE b.ledger_account_id = %s AND extract(year FROM b.month) = %s"
        " ORDER BY b.line, b.expense_account_id, b.month, b.id DESC",
        (account, year),
    ).fetchall():
        found.setdefault((line, expense), [Decimal(0)] * 12)[month - 1] = amount
    return found


@router.get("")
def budget(
    session: Staff,
    conn: Conn,
    account: Annotated[str, Query(min_length=1, max_length=100)],
    year: Annotated[Year, Query()],
) -> Budget:
    """An owner's property account's budget for a year: every line it may have, month by
    month (a month never budgeted reads 0.00)."""
    owner_id, owner, place = account_of(conn, session.pmc_id, account)
    expense_accounts = conn.execute(
        "SELECT id, name FROM trust_expense_accounts WHERE pmc_id = %s"
        ' ORDER BY name COLLATE "C", id',
        (session.pmc_id,),
    ).fetchall()
    keys: list[tuple[Line, UUID | None, str]] = [
        *((line, None, f"income: {line}") for line in INCOME),
        *((line, None, f"expense: {line}") for line in FEES),
        *(("expense account", i, f"expense: {name}") for i, name in expense_accounts),
        ("bills not in an expense account", None, "expense: bills not in an expense account"),
    ]
    found = latest(conn, account, year)
    lines = []
    for line, expense, label in keys:
        months = found.get((line, expense), [Decimal(0)] * 12)
        lines.append(
            BudgetLine(
                line=line,
                expense_account_id=expense,
                label=label,
                months=[cents(m) for m in months],
                year=cents(sum(months, Decimal(0))),
            )
        )
    return Budget(
        account=account, owner_id=owner_id, owner=owner, property=place, year=year, lines=lines
    )


class LineBudget(BaseModel):
    line: Line
    expense_account_id: UUID | None = None
    months: list[Amount] = Field(min_length=12, max_length=12)

    @model_validator(mode="after")
    def names_its_expense_account(self) -> LineBudget:
        if (self.line == "expense account") != (self.expense_account_id is not None):
            raise ValueError("an expense account's line names the expense account, and only it")
        return self


class NewBudget(BaseModel):
    """The Budgets grid's one Save: any of an account's lines for a year, all or none."""

    account: str = Field(min_length=1, max_length=100)
    year: Year
    lines: list[LineBudget] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def each_line_once(self) -> NewBudget:
        keys = [(x.line, x.expense_account_id) for x in self.lines]
        if len(set(keys)) != len(keys):
            raise ValueError("each line is budgeted once")
        return self


class Changed(BaseModel):
    changed: int  # months whose budget this changed, across the lines


@router.post("")
def set_budget(session: Staff, conn: Conn, new: NewBudget) -> Changed:
    """Sets each line's budget for each month of a year, in one transaction; a month already at
    that amount is left as it is."""
    account_of(conn, session.pmc_id, new.account)
    named = {x.expense_account_id for x in new.lines if x.expense_account_id is not None}
    known = {
        i
        for (i,) in conn.execute(
            "SELECT id FROM trust_expense_accounts WHERE pmc_id = %s AND id = ANY(%s)",
            (session.pmc_id, list(named)),
        ).fetchall()
    }
    if named - known:
        raise HTTPException(404, "no such expense account")
    before = latest(conn, new.account, new.year)
    changed = [
        (n, x.line, x.expense_account_id, amount)
        for x in new.lines
        for n, amount in enumerate(x.months, start=1)
        if amount != before.get((x.line, x.expense_account_id), [Decimal(0)] * 12)[n - 1]
    ]
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO trust_budgets (pmc_id, ledger_account_id, month, line,"
            " expense_account_id, amount) VALUES (%s, %s, make_date(%s, %s, 1), %s, %s, %s)",
            [
                (session.pmc_id, new.account, new.year, n, line, expense, amount)
                for n, line, expense, amount in changed
            ],
        )
    return Changed(changed=len(changed))
