"""Staff's journal entries (migration 0053): a correction the other write paths don't cover,
proposed by one staff member and posted by another who holds the approve role.

An entry moves money between the PMC's ledger accounts, line by line, dated a day.
trust_propose_journal_entry records it; trust_post_journal_entry posts it through the ledger,
where every trust rule holds (no account but a bank's cash below zero, each trust bank account
ties out, nothing dated in an approved period); trust_decline_journal_entry declines it. The
database refuses a post by whoever proposed the entry, or by anyone without the approve role.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints, model_validator

from pms import exports
from pms.api.deps import Conn, Staff, refusal
from pms.api.staff import roles_of

router = APIRouter(prefix="/api/journal")

Amount = Annotated[
    Decimal, Field(gt=Decimal(0), le=Decimal("10000000"), max_digits=10, decimal_places=2)
]
Memo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
CENT = Decimal("0.01")
# The newest entries a page shows.
SHOWN = 200

# What each kind of ledger account holds, in words (the trial balance's names).
LABEL = (
    "SELECT a.ledger_account_id, a.kind, b.display_name,"
    " CASE a.kind"
    "  WHEN 'bank_cash' THEN 'cash'"
    "  WHEN 'owner_property' THEN o.display_name || ' / ' || p.display_name"
    "  WHEN 'prepaid_rent' THEN 'prepaid rent: ' || t.display_name"
    "  WHEN 'tenant_deposit' THEN 'deposit: ' || t.display_name"
    "  WHEN 'vendor_payable' THEN 'vendor: ' || v.display_name"
    "  WHEN 'pmc_income' THEN 'company fees'"
    " END"
    " FROM trust_ledger_accounts AS a"
    " JOIN trust_bank_accounts AS b ON b.id = a.bank_account_id"
    " LEFT JOIN trust_owners AS o ON o.id = a.owner_id"
    " LEFT JOIN trust_properties AS p ON p.id = a.property_id"
    " LEFT JOIN trust_tenants AS t ON t.id = a.tenant_id"
    " LEFT JOIN trust_vendors AS v ON v.id = a.vendor_id"
    " WHERE a.pmc_id = %s"
)
KINDS = (
    "bank_cash",
    "owner_property",
    "prepaid_rent",
    "tenant_deposit",
    "vendor_payable",
    "pmc_income",
)


def cents(value: Decimal) -> str:
    return exports.text(Decimal(value).quantize(CENT))


class Account(BaseModel):
    id: str
    kind: str
    bank: str
    label: str


class Line(BaseModel):
    from_account: str
    to_account: str
    amount: str


class Entry(BaseModel):
    id: UUID
    event_on: date
    memo: str
    proposed_by: str
    proposed_by_me: bool
    proposed_at: datetime
    lines: list[Line]
    status: Literal["waiting", "posted", "declined"]
    decided_by: str | None
    decided_at: datetime | None
    reason: str | None


class Journal(BaseModel):
    may_post: bool  # the signed-in staff member holds approve
    accounts: list[Account]
    entries: list[Entry]


def accounts_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> list[Account]:
    rows = conn.execute(LABEL, (pmc_id,)).fetchall()
    found = [Account(id=i, kind=k, bank=b, label=f"{b}: {what}") for i, k, b, what in rows]
    return sorted(
        found, key=lambda a: (a.bank.encode(), KINDS.index(a.kind), a.label.encode(), a.id)
    )


@router.get("")
def journal(session: Staff, conn: Conn) -> Journal:
    """The PMC's ledger accounts an entry may move money between, and its newest entries with
    their lines and what became of them."""
    rows = conn.execute(
        "SELECT e.id, e.event_on, e.memo, s.display_name, e.proposed_by = %s, e.proposed_at,"
        "  (SELECT coalesce(json_agg(json_build_array(l.from_account, l.to_account, l.amount::text)"
        "     ORDER BY l.line), '[]')"
        "   FROM trust_journal_entry_lines AS l WHERE l.entry_id = e.id),"
        "  d.decision, ds.display_name, d.decided_at, d.reason"
        " FROM trust_journal_entries AS e"
        " JOIN trust_staff AS s ON s.id = e.proposed_by"
        " LEFT JOIN trust_journal_entry_decisions AS d ON d.entry_id = e.id"
        " LEFT JOIN trust_staff AS ds ON ds.id = d.decided_by"
        " WHERE e.pmc_id = %s"
        " ORDER BY e.proposed_at DESC, e.id DESC LIMIT %s",
        (session.person_id, session.pmc_id, SHOWN),
    ).fetchall()
    entries = [
        Entry(
            id=i,
            event_on=on,
            memo=memo,
            proposed_by=by,
            proposed_by_me=mine,
            proposed_at=at,
            lines=[
                Line(from_account=f, to_account=t, amount=cents(Decimal(a))) for f, t, a in lines
            ],
            status=decision or "waiting",
            decided_by=decided_by,
            decided_at=decided_at,
            reason=reason,
        )
        for i, on, memo, by, mine, at, lines, decision, decided_by, decided_at, reason in rows
    ]
    return Journal(
        may_post="approve" in roles_of(conn, session),
        accounts=accounts_of(conn, session.pmc_id),
        entries=entries,
    )


class NewLine(BaseModel):
    from_account: str = Field(min_length=1, max_length=100)
    to_account: str = Field(min_length=1, max_length=100)
    amount: Amount

    @model_validator(mode="after")
    def two_accounts(self) -> NewLine:
        if self.from_account == self.to_account:
            raise ValueError("a line moves money from one account to another")
        return self


class NewEntry(BaseModel):
    event_on: date
    memo: Memo
    lines: list[NewLine] = Field(min_length=1, max_length=20)


class Created(BaseModel):
    id: UUID


@router.post("", status_code=201)
def propose(session: Staff, conn: Conn, entry: NewEntry) -> Created:
    """Proposes an entry; nothing moves until another staff member who holds approve posts it."""
    ours = {a.id for a in accounts_of(conn, session.pmc_id)}
    if any(line.from_account not in ours or line.to_account not in ours for line in entry.lines):
        raise HTTPException(404, "no such ledger account")
    row = conn.execute(
        "SELECT trust_propose_journal_entry(%s, %s, %s,"
        " array(SELECT (f, t, a)::transfer_request"
        "  FROM unnest(%s::text[], %s::text[], %s::numeric[]) WITH ORDINALITY AS l (f, t, a, n)"
        "  ORDER BY n))",
        (
            session.pmc_id,
            entry.event_on,
            entry.memo,
            [line.from_account for line in entry.lines],
            [line.to_account for line in entry.lines],
            [line.amount for line in entry.lines],
        ),
    ).fetchone()
    assert row is not None
    return Created(id=row[0])


def entry_of(conn: psycopg.Connection[Any], pmc_id: UUID, entry_id: UUID) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM trust_journal_entries WHERE pmc_id = %s AND id = %s", (pmc_id, entry_id)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such journal entry")


class Posted(BaseModel):
    transfers: int


@router.post("/{entry_id}/post")
def post(session: Staff, conn: Conn, entry_id: UUID) -> Posted:
    """Posts an entry another staff member proposed, holding the approve role (the database
    refuses anyone else: 403). Posting it again posts nothing more."""
    entry_of(conn, session.pmc_id, entry_id)
    try:
        row = conn.execute(
            "SELECT trust_post_journal_entry(%s, %s)", (session.pmc_id, entry_id)
        ).fetchone()
        # Each trust bank account ties out at commit: ask now, so a refusal is the answer.
        conn.execute("SET CONSTRAINTS trust_bank_tie_out IMMEDIATE")
    except psycopg.errors.CheckViolation as refused:  # declined, below zero, closed, tie-out
        raise HTTPException(409, refusal(refused)) from None
    assert row is not None
    return Posted(transfers=len(row[0]))


class Declining(BaseModel):
    reason: Reason


@router.post("/{entry_id}/decline", status_code=204)
def decline(session: Staff, conn: Conn, entry_id: UUID, declining: Declining) -> None:
    """Declines an entry not decided yet, with why; it is never posted."""
    entry_of(conn, session.pmc_id, entry_id)
    try:
        conn.execute(
            "SELECT trust_decline_journal_entry(%s, %s, %s)",
            (session.pmc_id, entry_id, declining.reason),
        )
    except psycopg.errors.UniqueViolation as refused:  # posted or declined already
        raise HTTPException(409, refusal(refused)) from None
