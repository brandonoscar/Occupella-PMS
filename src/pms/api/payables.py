"""Staff's bills and owners' money: vendor bills (entered, maybe split across expense accounts
and with the vendor's invoice, approved by the owner when over the agreement's limit, set aside
from the owner's account, paid, several at once by Pay bills), a bill's own page, monthly
recurring bills (each month's entered by the nightly run, src/pms/nightly.py), the PMC's
management and leasing fees (the management fees maybe taken each month by the nightly run, as the
company chooses), other fees taken from an owner, owner draws and owner contributions, and the
company's earned fees taken out of trust.

Every step goes through the database's write paths (trust_set_aside_bill, trust_pay_bill,
trust_post_management_fee, trust_post_leasing_fee, trust_take_owner_fee, trust_draw_owner,
trust_record_owner_contribution, trust_withdraw_pmc_fees), each once per
bill, period, lease or request key, so a retry does nothing twice. A draw, a bill payment or
the company's fees taken out record what the bank paid out; the PMS sends no money itself.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints

from pms import email, exports
from pms.api import documents, outbox, scope
from pms.api.deps import Conn, Staff, refusal
from pms.api.rentals import Page, Paged, PerPage

router = APIRouter(prefix="/api/payables")

Amount = Annotated[Decimal, Field(gt=0, le=Decimal("10000000"), decimal_places=2)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Account = Annotated[str, Field(min_length=1, max_length=100)]
CENT = Decimal("0.01")


def cents(value: Decimal) -> str:
    return exports.text(Decimal(value).quantize(CENT))


def percent_text(value: Decimal) -> str:
    """A percent as staff typed it: 10, 12.5."""
    return f"{Decimal(value).normalize():f}"


def today() -> date:
    return datetime.now(UTC).date()


def at(day: date) -> datetime:
    return datetime.combine(day, time(0), UTC)


def not_after_today(day: date) -> None:
    if day > today():
        raise HTTPException(422, "money is recorded on the day it moved, not a later one")


def one(conn: psycopg.Connection[Any], query: str, params: tuple[Any, ...]) -> Any:
    row = conn.execute(query, params).fetchone()
    assert row is not None
    return row[0]


def owner_account(conn: psycopg.Connection[Any], pmc_id: UUID, account: str) -> UUID:
    """The trust bank account an owner's property account of the PMC is held in; 404 if none."""
    row = conn.execute(
        "SELECT bank_account_id FROM trust_ledger_accounts WHERE pmc_id = %s"
        " AND ledger_account_id = %s AND kind = 'owner_property'",
        (pmc_id, account),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no such owner account")
    return UUID(str(row[0]))


# --- bills ---------------------------------------------------------------------------------------

Status = Literal["needs approval", "declined", "to set aside", "set aside", "paid"]
EmailStatus = Literal["sent", "waiting", "failed"]
PayoutMethod = Literal["check", "EFT"]

# An email's state: delivered, or waiting for a delivery (none tried yet, or fewer than
# pms.email.MAX_ATTEMPTS failed), or given up on.
EMAIL_STATUS = f"""
CASE WHEN EXISTS (SELECT 1 FROM trust_email_deliveries AS d
                  WHERE d.email_id = {{e}}.id AND d.error IS NULL) THEN 'sent'
     WHEN (SELECT count(*) FROM trust_email_deliveries AS d
           WHERE d.email_id = {{e}}.id) >= {email.MAX_ATTEMPTS} THEN 'failed'
     ELSE 'waiting' END
"""  # noqa: S608


class Bill(BaseModel):
    id: UUID
    vendor_id: UUID
    vendor: str
    reference: str
    account: str
    owner: str
    property_id: UUID
    property: str
    bill_date: date
    due_on: date
    amount: str
    memo: str
    work_order_id: UUID | None
    # The expense account it is in (its latest), if it is in one and not split.
    expense_account_id: UUID | None = None
    expense_account: str | None = None
    # Split across expense accounts by lines (on the bill's page).
    split: bool = False
    approval_limit: str
    approved_by: str | None
    # Who declined it for the owner (the owner in the portal, or as staff recorded it), and why.
    declined_by: str | None = None
    decline_reason: str | None = None
    # The email asking the owner to decide it (ADR 0004): None when none was written.
    owner_email: EmailStatus | None = None
    status: Status
    # Neither paid nor declined, and due before today (UTC).
    overdue: bool = False
    # The company's markup on it (migration 0083): its amount and the agreement's percent.
    markup: str | None = None
    markup_percent: str | None = None
    # The invoice shared across properties it is a part of, if it is one.
    shared_bill_id: UUID | None = None


# Every bill of the PMC with its status, then narrowed by what is shown, the vendor, the day it
# is due by and the property group or property.
BILLS = (
    """
WITH listed AS (
    SELECT b.id, b.vendor_id, v.display_name AS vendor, b.reference, b.ledger_account_id,
           o.display_name AS owner, p.id AS property_id, p.display_name AS property, b.bill_date,
           b.due_on, b.amount, b.memo, b.work_order_id,
           CASE WHEN ex.split THEN NULL ELSE ex.expense_account_id END AS expense_id,
           CASE WHEN ex.split THEN NULL ELSE ex.name END AS expense,
           coalesce(ex.split, false) AS split,
           coalesce((SELECT g.approval_limit FROM trust_management_agreements AS g
                     WHERE g.ledger_account_id = b.ledger_account_id AND g.starts_on <= b.bill_date
                     ORDER BY g.starts_on DESC LIMIT 1), 0) AS approval_limit,
           ap.approved_by, dc.declined_by, dc.reason,
           CASE WHEN em.id IS NOT NULL THEN """  # noqa: S608
    + EMAIL_STATUS.format(e="em")
    + """ END AS owner_email,
           EXISTS (SELECT 1 FROM trust_bill_payments AS s WHERE s.bill_id = b.id
                   AND s.step = 'set_aside') AS aside,
           EXISTS (SELECT 1 FROM trust_bill_payments AS s WHERE s.bill_id = b.id
                   AND s.step = 'paid') AS paid,
           mk.amount AS markup, mk.percent AS markup_percent, sh.shared_bill_id
    FROM trust_bills AS b
    JOIN trust_vendors AS v ON v.id = b.vendor_id
    JOIN trust_ledger_accounts AS a ON a.ledger_account_id = b.ledger_account_id
    JOIN trust_owners AS o ON o.id = a.owner_id
    JOIN trust_properties AS p ON p.id = a.property_id
    LEFT JOIN trust_bill_approvals AS ap ON ap.bill_id = b.id
    LEFT JOIN trust_bill_declines AS dc ON dc.bill_id = b.id
    LEFT JOIN trust_emails AS em ON em.kind = 'bill_approval' AND em.bill_id = b.id
    LEFT JOIN trust_bill_markups AS mk ON mk.bill_id = b.id
    LEFT JOIN trust_shared_bill_parts AS sh ON sh.bill_id = b.id
    LEFT JOIN LATERAL (
        SELECT e.id AS expense_account_id, e.name,
               EXISTS (SELECT 1 FROM trust_bill_splits AS sp WHERE sp.categorized_id = x.id)
                   AS split
        FROM trust_bill_expense_accounts AS x
        JOIN trust_expense_accounts AS e ON e.id = x.expense_account_id
        WHERE x.bill_id = b.id ORDER BY x.id DESC LIMIT 1
    ) AS ex ON true
    WHERE b.pmc_id = %(pmc)s
      AND (%(bill)s::uuid IS NULL OR b.id = %(bill)s::uuid)
      AND (%(vendor)s::uuid IS NULL OR b.vendor_id = %(vendor)s::uuid)
      AND (%(due_by)s::date IS NULL OR b.due_on <= %(due_by)s::date)
      AND {within}
), statused AS (
    SELECT l.*,
           CASE WHEN l.paid THEN 'paid'
                WHEN l.aside THEN 'set aside'
                WHEN l.declined_by IS NOT NULL THEN 'declined'
                WHEN l.amount > l.approval_limit AND l.approved_by IS NULL THEN 'needs approval'
                ELSE 'to set aside' END AS status
    FROM listed AS l
)
SELECT s.id, s.vendor_id, s.vendor, s.reference, s.ledger_account_id, s.owner, s.property_id,
       s.property, s.bill_date, s.due_on, s.amount, s.memo, s.work_order_id, s.expense_id,
       s.expense, s.split, s.approval_limit, s.approved_by, s.declined_by, s.reason,
       s.owner_email, s.status, s.markup, s.markup_percent, s.shared_bill_id, count(*) OVER ()
FROM statused AS s
WHERE CASE %(show)s WHEN 'open' THEN s.status NOT IN ('paid', 'declined')
                    WHEN 'all' THEN true
                    ELSE s.status = %(show)s END
ORDER BY s.due_on, s.bill_date, s.vendor COLLATE "C", s.reference COLLATE "C", s.id
LIMIT %(limit)s OFFSET %(offset)s
"""
).format(within=scope.within("p.id"))


BillsShown = Literal["open", "paid", "declined", "all"]


def find_bills(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    show: BillsShown = "all",
    *,
    bill: UUID | None = None,
    vendor: UUID | None = None,
    due_by: date | None = None,
    group: UUID | None = None,
    property_id: UUID | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> tuple[list[Bill], int]:
    """The PMC's bills shown, by due date, a page of them when given a limit, and how many
    there are in all."""
    day = today()
    rows = conn.execute(
        BILLS,
        {
            "pmc": pmc_id,
            "bill": bill,
            "show": show,
            "vendor": vendor,
            "due_by": due_by,
            "limit": limit,
            "offset": offset,
            **scope.params(group, property_id),
        },
    ).fetchall()
    found = [
        Bill(
            id=i,
            vendor_id=vendor_id,
            vendor=vendor_name,
            reference=reference,
            account=account,
            owner=owner,
            property_id=prop_id,
            property=prop,
            bill_date=billed,
            due_on=due,
            amount=cents(amount),
            memo=memo,
            work_order_id=order,
            expense_account_id=expense_id,
            expense_account=expense,
            split=split,
            approval_limit=cents(limit_),
            approved_by=approved_by,
            declined_by=declined_by,
            decline_reason=decline_reason,
            owner_email=owner_email,
            status=status,
            overdue=status not in ("paid", "declined") and due < day,
            markup=None if markup is None else cents(markup),
            markup_percent=None if markup_percent is None else percent_text(markup_percent),
            shared_bill_id=shared,
        )
        for (
            i,
            vendor_id,
            vendor_name,
            reference,
            account,
            owner,
            prop_id,
            prop,
            billed,
            due,
            amount,
            memo,
            order,
            expense_id,
            expense,
            split,
            limit_,
            approved_by,
            declined_by,
            decline_reason,
            owner_email,
            status,
            markup,
            markup_percent,
            shared,
            _,
        ) in rows
    ]
    return found, rows[0][-1] if rows else 0


@router.get("/bills")
def bills(
    session: Staff,
    conn: Conn,
    show: Annotated[BillsShown, Query()] = "open",
    vendor: Annotated[UUID | None, Query()] = None,
    due_by: Annotated[date | None, Query()] = None,
    group: scope.Group = None,
    property_id: scope.Property = None,
    page: Page = 1,
    per_page: PerPage = 50,
) -> Paged[Bill]:
    """The PMC's vendor bills, by due date, a page at a time: by default those still to pay
    (not paid, nor declined by the owner); or the paid ones, the declined ones, or all. Maybe
    of one vendor, due by a day, or of a property group or property."""
    found, total = find_bills(
        conn,
        session.pmc_id,
        show,
        vendor=vendor,
        due_by=due_by,
        group=group,
        property_id=property_id,
        limit=per_page,
        offset=(page - 1) * per_page,
    )
    return Paged(items=found, total=total, page=page, per_page=per_page)


class BillLine(BaseModel):
    # None: the bill is in no expense account yet.
    expense_account_id: UUID | None
    expense_account: str | None
    amount: str


class BillEvent(BaseModel):
    on: date
    what: str


class BillEmail(BaseModel):
    to: str
    subject: str
    written_at: datetime
    status: EmailStatus
    attempts: int
    # Why the last delivery failed, while it isn't sent.
    error: str | None


class BillFile(BaseModel):
    id: UUID
    title: str
    content_type: str
    size: int
    uploaded_at: datetime


class SharedPart(BaseModel):
    bill_id: UUID
    part: int
    reference: str
    owner: str
    property: str
    amount: str
    status: Status


class SharedBill(BaseModel):
    """The vendor's invoice a bill is a share of, and every share of it."""

    id: UUID
    reference: str
    amount: str
    parts: list[SharedPart]


class BillPage(BaseModel):
    bill: Bill
    # The invoice shared across properties it is a part of (migration 0083).
    shared: SharedBill | None = None
    # Its expense accounts: its lines when it is split, else the whole bill in its account.
    lines: list[BillLine]
    files: list[BillFile]
    email: BillEmail | None
    # What happened to it, oldest first: entered, emailed, decided, set aside, paid.
    history: list[BillEvent]


@router.get("/bills/{bill_id}")
def bill_page(session: Staff, conn: Conn, bill_id: UUID) -> BillPage:
    """A bill's own page: the bill, its expense account lines, its invoices and receipts, the
    email asking the owner to decide it, and its history."""
    found, _ = find_bills(conn, session.pmc_id, bill=bill_id)
    if not found:
        raise HTTPException(404, "no such bill")
    bill = found[0]
    head = conn.execute(
        "SELECT x.id, e.id, e.name FROM trust_bill_expense_accounts AS x"
        " JOIN trust_expense_accounts AS e ON e.id = x.expense_account_id"
        " WHERE x.bill_id = %s ORDER BY x.id DESC LIMIT 1",
        (bill_id,),
    ).fetchone()
    lines = (
        [
            BillLine(expense_account_id=i, expense_account=name, amount=cents(amount))
            for i, name, amount in conn.execute(
                "SELECT e.id, e.name, s.amount FROM trust_bill_splits AS s"
                " JOIN trust_expense_accounts AS e ON e.id = s.expense_account_id"
                " WHERE s.categorized_id = %s ORDER BY s.id",
                (head[0],),
            ).fetchall()
        ]
        if head is not None
        else []
    )
    if not lines:
        lines = [
            BillLine(
                expense_account_id=head[1] if head else None,
                expense_account=head[2] if head else None,
                amount=bill.amount,
            )
        ]
    files = [
        BillFile(id=i, title=title, content_type=kind, size=size, uploaded_at=at)
        for i, title, kind, size, at in conn.execute(
            "SELECT d.id, d.title, d.content_type, d.size, d.created_at FROM trust_documents AS d"
            " WHERE d.pmc_id = %s AND d.bill_id = %s AND NOT EXISTS ("
            "   SELECT 1 FROM trust_document_removals AS r WHERE r.document_id = d.id)"
            " ORDER BY d.created_at, d.id",
            (session.pmc_id, bill_id),
        ).fetchall()
    ]
    row = conn.execute(
        "SELECT e.to_address, e.subject, e.created_at, "  # noqa: S608
        + EMAIL_STATUS.format(e="e")
        + ", (SELECT count(*) FROM trust_email_deliveries AS d WHERE d.email_id = e.id),"
        " (SELECT d.error FROM trust_email_deliveries AS d WHERE d.email_id = e.id"
        "  ORDER BY d.attempted_at DESC LIMIT 1)"
        " FROM trust_emails AS e WHERE e.kind = 'bill_approval' AND e.bill_id = %s",
        (bill_id,),
    ).fetchone()
    sent = (
        None
        if row is None
        else BillEmail(
            to=row[0],
            subject=row[1],
            written_at=row[2],
            status=row[3],
            attempts=row[4],
            error=None if row[3] == "sent" else row[5],
        )
    )
    return BillPage(
        bill=bill,
        shared=None if bill.shared_bill_id is None else shared_of(conn, session.pmc_id, bill),
        lines=lines,
        files=files,
        email=sent,
        history=history_of(conn, bill, sent),
    )


def shared_of(conn: psycopg.Connection[Any], pmc_id: UUID, bill: Bill) -> SharedBill:
    head = conn.execute(
        "SELECT id, reference, amount FROM trust_shared_bills WHERE id = %s",
        (bill.shared_bill_id,),
    ).fetchone()
    assert head is not None
    parts = []
    for part, bill_id in conn.execute(
        "SELECT part, bill_id FROM trust_shared_bill_parts WHERE shared_bill_id = %s ORDER BY part",
        (bill.shared_bill_id,),
    ).fetchall():
        (each,), _ = find_bills(conn, pmc_id, bill=bill_id)
        parts.append(
            SharedPart(
                bill_id=bill_id,
                part=part,
                reference=each.reference,
                owner=each.owner,
                property=each.property,
                amount=each.amount,
                status=each.status,
            )
        )
    return SharedBill(id=head[0], reference=head[1], amount=cents(head[2]), parts=parts)


def history_of(
    conn: psycopg.Connection[Any], bill: Bill, sent: BillEmail | None
) -> list[BillEvent]:
    """A bill's steps, oldest first, each on its day (UTC)."""
    events = [
        BillEvent(
            on=one(
                conn,
                "SELECT (created_at AT TIME ZONE 'UTC')::date FROM trust_bills WHERE id = %s",
                (bill.id,),
            ),
            what="Entered",
        )
    ]
    if sent is not None:
        words = {"sent": "sent", "waiting": "waiting to be sent", "failed": "not sent"}
        events.append(
            BillEvent(
                on=sent.written_at.astimezone(UTC).date(),
                what=f"Email to {sent.to} asking the owner to decide it: {words[sent.status]}",
            )
        )
    approved = conn.execute(
        "SELECT approved_on, approved_by FROM trust_bill_approvals WHERE bill_id = %s", (bill.id,)
    ).fetchone()
    if approved is not None:
        events.append(BillEvent(on=approved[0], what=f"Approved by {approved[1]}"))
    declined = conn.execute(
        "SELECT declined_on, declined_by, reason FROM trust_bill_declines WHERE bill_id = %s",
        (bill.id,),
    ).fetchone()
    if declined is not None:
        events.append(BillEvent(on=declined[0], what=f"Declined by {declined[1]}: {declined[2]}"))
    for step, on, method in conn.execute(
        "SELECT s.step, (t.event_at AT TIME ZONE 'UTC')::date, m.method"
        " FROM trust_bill_payments AS s JOIN pgledger_transfers AS t ON t.id = s.transfer_id"
        " LEFT JOIN trust_payout_methods AS m ON m.transfer_id = s.transfer_id"
        " WHERE s.bill_id = %s ORDER BY t.event_at, s.step DESC",
        (bill.id,),
    ).fetchall():
        if step == "set_aside":
            events.append(
                BillEvent(on=on, what=f"{dollars(bill.amount)} set aside from the owner's money")
            )
            if bill.markup is not None:
                events.append(
                    BillEvent(
                        on=on,
                        what=f"Markup of {dollars(bill.markup)} ({bill.markup_percent}%) taken"
                        " into the company's fees",
                    )
                )
        else:
            how = f" by {method}" if method else ""
            events.append(BillEvent(on=on, what=f"Paid{how}"))
    return sorted(events, key=lambda event: event.on)


def dollars(amount: str) -> str:
    return f"${Decimal(amount):,.2f}"


class Line(BaseModel):
    expense_account_id: UUID
    amount: Amount


Lines = Annotated[list[Line], Field(min_length=2, max_length=20)]


class Attachment(BaseModel):
    title: documents.Title
    content_type: documents.ContentType
    # The file, base64, as Files takes it.
    content_base64: str = Field(min_length=4, max_length=7_000_000)


class NewBill(BaseModel):
    vendor_id: UUID
    account: Account
    reference: Text
    bill_date: date
    due_on: date
    amount: Amount
    memo: Text
    work_order_id: UUID | None = None
    # One expense account for the whole bill, or lines splitting it across several.
    expense_account_id: UUID | None = None
    lines: Lines | None = None
    # The vendor's invoice, kept with the bill as its receipt.
    invoice: Attachment | None = None
    # Add the company's markup, at the percent the owner's agreement sets (migration 0083).
    markup: bool = False


class Created(BaseModel):
    id: UUID


def expense_account_of(conn: psycopg.Connection[Any], pmc_id: UUID, account: UUID) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM trust_expense_accounts WHERE pmc_id = %s AND id = %s", (pmc_id, account)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such expense account")


def vendor_of(conn: psycopg.Connection[Any], pmc_id: UUID, vendor: UUID) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM trust_vendors WHERE pmc_id = %s AND id = %s", (pmc_id, vendor)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such vendor")


def lines_of(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    amount: Decimal,
    account: UUID | None,
    lines: list[Line] | None,
) -> list[Line] | None:
    """A bill's expense accounts as given, checked: one account, or lines in different accounts
    that come to the bill's amount; None for neither."""
    if account is not None and lines is not None:
        raise HTTPException(422, "give a bill one expense account or lines, not both")
    if lines is None:
        if account is None:
            return None
        lines = [Line(expense_account_id=account, amount=amount)]
    accounts = [line.expense_account_id for line in lines]
    if len(set(accounts)) != len(accounts):
        raise HTTPException(422, "a bill's lines are each in a different expense account")
    total = sum((line.amount for line in lines), Decimal(0))
    if total != amount:
        raise HTTPException(
            422, f"the lines come to {dollars(str(total))}, not the bill's {dollars(str(amount))}"
        )
    for chosen in accounts:
        expense_account_of(conn, pmc_id, chosen)
    return lines


@router.post("/bills", status_code=201)
def enter_bill(session: Staff, conn: Conn, new: NewBill) -> Created:
    """A vendor's bill against an owner's property account, once per vendor reference, maybe
    in an expense account or split across several, maybe with the vendor's invoice."""
    vendor_of(conn, session.pmc_id, new.vendor_id)
    owner_account(conn, session.pmc_id, new.account)
    lines = lines_of(conn, session.pmc_id, new.amount, new.expense_account_id, new.lines)
    if new.due_on < new.bill_date:
        raise HTTPException(422, "a bill is due on or after its date")
    try:
        bill = one(
            conn,
            "INSERT INTO trust_bills (pmc_id, vendor_id, ledger_account_id, reference, bill_date,"
            " due_on, amount, memo, work_order_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " RETURNING id",
            (
                session.pmc_id,
                new.vendor_id,
                new.account,
                new.reference,
                new.bill_date,
                new.due_on,
                new.amount,
                new.memo,
                new.work_order_id,
            ),
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "this vendor has a bill with that reference already") from None
    except (psycopg.errors.CheckViolation, psycopg.errors.ForeignKeyViolation) as refused:
        # a work order of another property, a cancelled one, or none of this PMC's
        raise HTTPException(409, refusal(refused)) from None
    if lines is not None:
        put_in(conn, session.pmc_id, bill, lines)
    if new.markup:
        mark_up(conn, session.pmc_id, bill)
    if new.invoice is not None:
        documents.keep(
            conn,
            session.pmc_id,
            documents.NewDocument(kind="receipt", about_id=bill, **new.invoice.model_dump()),
        )
    # Over the owner's limit: the email asking them to decide it, which goes out once this
    # commits (ADR 0004).
    email.write_bill_approval(conn, session.pmc_id, bill, outbox.PUBLIC_URL.get())
    return Created(id=bill)


def mark_up(conn: psycopg.Connection[Any], pmc_id: UUID, bill: UUID) -> None:
    """The company's markup on a bill, at the percent the owner's agreement in force on its date
    sets (the database works it out)."""
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_bill_markups (bill_id, pmc_id) VALUES (%s, %s)", (bill, pmc_id)
            )
    except psycopg.errors.CheckViolation:
        raise HTTPException(
            409, "the owner's agreement sets no markup on bills dated then"
        ) from None


def marks_up(conn: psycopg.Connection[Any], account: str, day: date) -> bool:
    """Whether the owner's agreement in force on a bill's date lets the company add a markup."""
    row = conn.execute(
        "SELECT bill_markup_percent FROM trust_management_agreements"
        " WHERE ledger_account_id = %s AND starts_on <= %s ORDER BY starts_on DESC LIMIT 1",
        (account, day),
    ).fetchone()
    return row is not None and row[0] > 0


class Share(BaseModel):
    account: Account
    amount: Amount


class NewSharedBill(BaseModel):
    vendor_id: UUID
    reference: Text
    bill_date: date
    due_on: date
    memo: Text
    # Each owner's property account's share, which together are the invoice.
    shares: Annotated[list[Share], Field(min_length=2, max_length=20)]
    # One expense account for every share.
    expense_account_id: UUID | None = None
    # Add the company's markup to each share whose owner's agreement sets one.
    markup: bool = False
    # The vendor's invoice, kept with the first share.
    invoice: Attachment | None = None


class SharedCreated(BaseModel):
    id: UUID
    bills: list[UUID]


@router.post("/shared-bills", status_code=201)
def enter_shared_bill(session: Staff, conn: Conn, new: NewSharedBill) -> SharedCreated:
    """One vendor invoice shared across properties (q062): a bill for each owner's property
    account's share, under the invoice's reference with /1, /2..., each following every bill
    rule on its own (its owner's approval and email, set aside from its owner's money)."""
    vendor_of(conn, session.pmc_id, new.vendor_id)
    accounts = [share.account for share in new.shares]
    if len(set(accounts)) != len(accounts):
        raise HTTPException(422, "each share is a different owner's property account")
    for account in accounts:
        owner_account(conn, session.pmc_id, account)
    if new.expense_account_id is not None:
        expense_account_of(conn, session.pmc_id, new.expense_account_id)
    if new.due_on < new.bill_date:
        raise HTTPException(422, "a bill is due on or after its date")
    total = sum((share.amount for share in new.shares), Decimal(0))
    try:
        shared = one(
            conn,
            "INSERT INTO trust_shared_bills (pmc_id, vendor_id, reference, amount)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (session.pmc_id, new.vendor_id, new.reference, total),
        )
        bills = []
        for part, share in enumerate(new.shares, 1):
            bill = one(
                conn,
                "INSERT INTO trust_bills (pmc_id, vendor_id, ledger_account_id, reference,"
                " bill_date, due_on, amount, memo) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                " RETURNING id",
                (
                    session.pmc_id,
                    new.vendor_id,
                    share.account,
                    f"{new.reference}/{part}",
                    new.bill_date,
                    new.due_on,
                    share.amount,
                    new.memo,
                ),
            )
            conn.execute(
                "INSERT INTO trust_shared_bill_parts (shared_bill_id, part, pmc_id, bill_id)"
                " VALUES (%s, %s, %s, %s)",
                (shared, part, session.pmc_id, bill),
            )
            bills.append(bill)
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "this vendor has a bill with that reference already") from None
    marked = 0
    for share, bill in zip(new.shares, bills, strict=True):
        if new.expense_account_id is not None:
            put_in(
                conn,
                session.pmc_id,
                bill,
                [Line(expense_account_id=new.expense_account_id, amount=share.amount)],
            )
        if new.markup and marks_up(conn, share.account, new.bill_date):
            mark_up(conn, session.pmc_id, bill)
            marked += 1
        email.write_bill_approval(conn, session.pmc_id, bill, outbox.PUBLIC_URL.get())
    if new.markup and marked == 0:
        raise HTTPException(409, "no owner's agreement here sets a markup on bills dated then")
    if new.invoice is not None:
        documents.keep(
            conn,
            session.pmc_id,
            documents.NewDocument(kind="receipt", about_id=bills[0], **new.invoice.model_dump()),
        )
    return SharedCreated(id=shared, bills=bills)


def put_in(conn: psycopg.Connection[Any], pmc_id: UUID, bill: UUID, lines: list[Line]) -> None:
    """A bill's expense accounts: its new latest row, and the lines splitting it when there are
    several."""
    row = one(
        conn,
        "INSERT INTO trust_bill_expense_accounts (pmc_id, bill_id, expense_account_id)"
        " VALUES (%s, %s, %s) RETURNING id",
        (pmc_id, bill, lines[0].expense_account_id),
    )
    if len(lines) > 1:
        for line in lines:
            conn.execute(
                "INSERT INTO trust_bill_splits"
                " (pmc_id, bill_id, categorized_id, expense_account_id, amount)"
                " VALUES (%s, %s, %s, %s, %s)",
                (pmc_id, bill, row, line.expense_account_id, line.amount),
            )


class InAccount(BaseModel):
    # One expense account for the whole bill, or lines splitting it across several.
    expense_account_id: UUID | None = None
    lines: Lines | None = None


@router.post("/bills/{bill_id}/expense-account", status_code=201)
def bill_expense_account(session: Staff, conn: Conn, bill_id: UUID, put: InAccount) -> None:
    """Puts a bill in an expense account, or splits it across several: one entered before it
    had one, or one in the wrong account. Its latest is its account; the earlier rows stay, as
    every row does."""
    amount = conn.execute(
        "SELECT amount FROM trust_bills WHERE pmc_id = %s AND id = %s", (session.pmc_id, bill_id)
    ).fetchone()
    if amount is None:
        raise HTTPException(404, "no such bill")
    lines = lines_of(conn, session.pmc_id, amount[0], put.expense_account_id, put.lines)
    if lines is None:
        raise HTTPException(422, "give an expense account or lines")
    put_in(conn, session.pmc_id, bill_id, lines)


def bill_of(conn: psycopg.Connection[Any], pmc_id: UUID, bill: UUID) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM trust_bills WHERE pmc_id = %s AND id = %s", (pmc_id, bill)
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such bill")


class Approval(BaseModel):
    approved_on: date
    # Who approved it for the owner, as staff record it (the owner, or someone they named).
    approved_by: Text


@router.post("/bills/{bill_id}/approval", status_code=201)
def approve(session: Staff, conn: Conn, bill_id: UUID, approval: Approval) -> None:
    """Records the owner's approval of a bill over the agreement's approval limit."""
    bill_of(conn, session.pmc_id, bill_id)
    try:
        with conn.transaction():  # a savepoint: a refusal leaves the request's transaction usable
            conn.execute(
                "INSERT INTO trust_bill_approvals (pmc_id, bill_id, approved_on, approved_by)"
                " VALUES (%s, %s, %s, %s)",
                (session.pmc_id, bill_id, approval.approved_on, approval.approved_by),
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, decided(conn, bill_id)) from None


def decided(conn: psycopg.Connection[Any], bill_id: UUID) -> str:
    """Why a second decision on a bill is refused."""
    approved = conn.execute(
        "SELECT 1 FROM trust_bill_approvals WHERE bill_id = %s", (bill_id,)
    ).fetchone()
    return "this bill is approved already" if approved else "the owner declined this bill"


class Decline(BaseModel):
    declined_on: date
    # Who declined it for the owner, as staff record it (the owner, or someone they named).
    declined_by: Text
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


@router.post("/bills/{bill_id}/decline", status_code=201)
def decline(session: Staff, conn: Conn, bill_id: UUID, declined: Decline) -> None:
    """Records the owner's decline of a bill: it is never set aside or paid, and holds nothing
    back from the owner's draws from that day."""
    bill_of(conn, session.pmc_id, bill_id)
    try:
        with conn.transaction():  # a savepoint, as above
            conn.execute(
                "INSERT INTO trust_bill_declines"
                " (pmc_id, bill_id, declined_on, declined_by, reason)"
                " VALUES (%s, %s, %s, %s, %s)",
                (
                    session.pmc_id,
                    bill_id,
                    declined.declined_on,
                    declined.declined_by,
                    declined.reason,
                ),
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, decided(conn, bill_id)) from None
    except psycopg.errors.CheckViolation as refused:  # set aside already
        raise HTTPException(409, refusal(refused)) from None


class Day(BaseModel):
    on: date


class Moved(BaseModel):
    transfer_id: str


def vendor_account(conn: psycopg.Connection[Any], pmc_id: UUID, bill: UUID) -> None:
    """Opens the bill's vendor's account in the owner account's trust bank account, if new."""
    row = conn.execute(
        "SELECT b.vendor_id, a.bank_account_id FROM trust_bills AS b"
        " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = b.ledger_account_id"
        " WHERE b.id = %s",
        (bill,),
    ).fetchone()
    assert row is not None
    vendor, bank = row
    if not conn.execute(
        "SELECT 1 FROM trust_ledger_accounts WHERE bank_account_id = %s AND kind = 'vendor_payable'"
        " AND vendor_id = %s",
        (bank, vendor),
    ).fetchone():
        conn.execute("SELECT trust_open_vendor_account(%s, %s, %s)", (pmc_id, bank, vendor))


@router.post("/bills/{bill_id}/set-aside", status_code=201)
def set_aside(session: Staff, conn: Conn, bill_id: UUID, step: Day) -> Moved:
    """Moves the bill's amount from the owner's account to the vendor's, once."""
    not_after_today(step.on)
    bill_of(conn, session.pmc_id, bill_id)
    vendor_account(conn, session.pmc_id, bill_id)
    try:
        moved = one(
            conn, "SELECT trust_set_aside_bill(%s, %s, %s)", (session.pmc_id, bill_id, at(step.on))
        )
    except psycopg.errors.CheckViolation as refused:  # no approval, not enough money, closed
        raise HTTPException(409, refusal(refused)) from None
    return Moved(transfer_id=moved)


class Paying(BaseModel):
    on: date
    # How it was sent; recorded, nothing sent (the PMS writes no bank file).
    method: PayoutMethod = "check"


def pay_one(
    conn: psycopg.Connection[Any], pmc_id: UUID, bill_id: UUID, on: date, method: PayoutMethod
) -> str:
    """Records that the trust bank account paid the vendor a bill set aside, once, and how; the
    same again returns that payment, keeping how it was first recorded as sent."""
    moved = one(conn, "SELECT trust_pay_bill(%s, %s, %s)", (pmc_id, bill_id, at(on)))
    conn.execute(
        "INSERT INTO trust_payout_methods (pmc_id, transfer_id, method) VALUES (%s, %s, %s)"
        " ON CONFLICT (transfer_id) DO NOTHING",
        (pmc_id, moved, method),
    )
    return str(moved)


@router.post("/bills/{bill_id}/pay", status_code=201)
def pay(session: Staff, conn: Conn, bill_id: UUID, step: Paying) -> Moved:
    """Records that the trust bank account paid the vendor the bill, once, by check or EFT."""
    not_after_today(step.on)
    bill_of(conn, session.pmc_id, bill_id)
    try:
        moved = pay_one(conn, session.pmc_id, bill_id, step.on, step.method)
    except psycopg.errors.InvalidParameterValue as refused:  # not set aside, or before that
        raise HTTPException(409, refusal(refused)) from None
    except psycopg.errors.CheckViolation as refused:  # a closed period
        raise HTTPException(409, refusal(refused)) from None
    return Moved(transfer_id=moved)


class PayBills(BaseModel):
    on: date
    # How they were sent; recorded, nothing sent (the PMS writes no bank file).
    method: PayoutMethod
    bill_ids: Annotated[list[UUID], Field(min_length=1, max_length=200)]


class BillPaid(BaseModel):
    bill_id: UUID
    vendor: str
    reference: str
    amount: str
    transfer_id: str


@router.post("/bills/pay", status_code=201)
def pay_bills(session: Staff, conn: Conn, run: PayBills) -> list[BillPaid]:
    """Pay bills (Buildium's): each bill chosen set aside from its owner's money, if it wasn't,
    and paid from its trust bank account, all of them or none, each recorded with how it was
    sent. Sent again, it pays nothing twice. A refusal names the bill it is about."""
    not_after_today(run.on)
    if len(set(run.bill_ids)) != len(run.bill_ids):
        raise HTTPException(422, "pay each bill once")
    names = {
        bill: (vendor, reference, amount)
        for bill, vendor, reference, amount in conn.execute(
            "SELECT b.id, v.display_name, b.reference, b.amount FROM trust_bills AS b"
            " JOIN trust_vendors AS v ON v.id = b.vendor_id"
            " WHERE b.pmc_id = %s AND b.id = ANY(%s)",
            (session.pmc_id, run.bill_ids),
        ).fetchall()
    }
    paid = []
    for bill in run.bill_ids:
        if bill not in names:
            raise HTTPException(404, "no such bill")
        vendor, reference, amount = names[bill]
        vendor_account(conn, session.pmc_id, bill)
        try:
            one(conn, "SELECT trust_set_aside_bill(%s, %s, %s)", (session.pmc_id, bill, at(run.on)))
            moved = pay_one(conn, session.pmc_id, bill, run.on, run.method)
        except (psycopg.errors.CheckViolation, psycopg.errors.InvalidParameterValue) as refused:
            raise HTTPException(409, f"{vendor} {reference}: {refusal(refused)}") from None
        paid.append(
            BillPaid(
                bill_id=bill,
                vendor=vendor,
                reference=reference,
                amount=cents(amount),
                transfer_id=moved,
            )
        )
    return paid


# --- recurring bills ------------------------------------------------------------------------


class Recurring(BaseModel):
    id: UUID
    vendor: str
    account: str
    owner: str
    property: str
    reference_prefix: str
    amount: str
    memo: str
    day_of_month: int
    starts_on: date
    ends_on: date | None
    expense_account_id: UUID | None = None
    expense_account: str | None = None


@router.get("/recurring")
def recurring(session: Staff, conn: Conn) -> list[Recurring]:
    return [
        Recurring(
            id=i,
            vendor=vendor,
            account=account,
            owner=owner,
            property=prop,
            reference_prefix=prefix,
            amount=cents(amount),
            memo=memo,
            day_of_month=day_of_month,
            starts_on=starts,
            ends_on=ends,
            expense_account_id=expense_id,
            expense_account=expense,
        )
        for (
            i,
            vendor,
            account,
            owner,
            prop,
            prefix,
            amount,
            memo,
            day_of_month,
            starts,
            ends,
            expense_id,
            expense,
        ) in (
            conn.execute(
                "SELECT r.id, v.display_name, r.ledger_account_id, o.display_name,"
                " p.display_name, r.reference_prefix, r.amount, r.memo, r.day_of_month,"
                " r.starts_on, e.ends_on, ex.id, ex.name FROM trust_recurring_bills AS r"
                " JOIN trust_vendors AS v ON v.id = r.vendor_id"
                " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = r.ledger_account_id"
                " JOIN trust_owners AS o ON o.id = a.owner_id"
                " JOIN trust_properties AS p ON p.id = a.property_id"
                " LEFT JOIN trust_recurring_bill_ends AS e ON e.recurring_bill_id = r.id"
                " LEFT JOIN LATERAL (SELECT x.id, x.name"
                "  FROM trust_recurring_bill_expense_accounts AS y"
                "  JOIN trust_expense_accounts AS x ON x.id = y.expense_account_id"
                "  WHERE y.recurring_bill_id = r.id ORDER BY y.id DESC LIMIT 1) AS ex ON true"
                ' WHERE r.pmc_id = %s ORDER BY v.display_name COLLATE "C",'
                ' r.reference_prefix COLLATE "C", r.id',
                (session.pmc_id,),
            ).fetchall()
        )
    ]


class NewRecurring(BaseModel):
    vendor_id: UUID
    account: Account
    reference_prefix: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    amount: Amount
    memo: Text
    day_of_month: Annotated[int, Field(ge=1, le=28)]
    starts_on: date
    expense_account_id: UUID | None = None


@router.post("/recurring", status_code=201)
def add_recurring(session: Staff, conn: Conn, new: NewRecurring) -> Created:
    """A bill that comes every month on a day (1 to 28), from a first day."""
    vendor_of(conn, session.pmc_id, new.vendor_id)
    owner_account(conn, session.pmc_id, new.account)
    if new.expense_account_id is not None:
        expense_account_of(conn, session.pmc_id, new.expense_account_id)
    try:
        recurring = Created(
            id=one(
                conn,
                "INSERT INTO trust_recurring_bills (pmc_id, vendor_id, ledger_account_id,"
                " reference_prefix, amount, memo, day_of_month, starts_on)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    session.pmc_id,
                    new.vendor_id,
                    new.account,
                    new.reference_prefix,
                    new.amount,
                    new.memo,
                    new.day_of_month,
                    new.starts_on,
                ),
            )
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            409, "this vendor has a recurring bill with that reference already"
        ) from None
    if new.expense_account_id is not None:
        recurring_put_in(conn, session.pmc_id, recurring.id, new.expense_account_id)
    return recurring


def recurring_of(conn: psycopg.Connection[Any], pmc_id: UUID, recurring: UUID) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM trust_recurring_bills WHERE pmc_id = %s AND id = %s",
            (pmc_id, recurring),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no such recurring bill")


def recurring_put_in(
    conn: psycopg.Connection[Any], pmc_id: UUID, recurring: UUID | str, account: UUID
) -> None:
    conn.execute(
        "INSERT INTO trust_recurring_bill_expense_accounts"
        " (pmc_id, recurring_bill_id, expense_account_id) VALUES (%s, %s, %s)",
        (pmc_id, recurring, account),
    )


class RecurringInAccount(BaseModel):
    expense_account_id: UUID


@router.post("/recurring/{recurring_id}/expense-account", status_code=201)
def recurring_expense_account(
    session: Staff, conn: Conn, recurring_id: UUID, put: RecurringInAccount
) -> None:
    """Puts a recurring bill in an expense account: each month's bill entered from now on is
    in it. Bills entered before keep theirs; put each in one as a bill."""
    recurring_of(conn, session.pmc_id, recurring_id)
    expense_account_of(conn, session.pmc_id, put.expense_account_id)
    recurring_put_in(conn, session.pmc_id, recurring_id, put.expense_account_id)


class End(BaseModel):
    ends_on: date


@router.post("/recurring/{recurring_id}/end", status_code=201)
def end_recurring(session: Staff, conn: Conn, recurring_id: UUID, end: End) -> None:
    """Stops a recurring bill: no month after the day is entered."""
    recurring_of(conn, session.pmc_id, recurring_id)
    try:
        conn.execute(
            "INSERT INTO trust_recurring_bill_ends (pmc_id, recurring_bill_id, ends_on)"
            " VALUES (%s, %s, %s)",
            (session.pmc_id, recurring_id, end.ends_on),
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "this recurring bill has ended already") from None


# --- owners' money ----------------------------------------------------------------------------


class OwnerMoney(BaseModel):
    account: str
    owner_id: UUID
    owner: str
    property_id: UUID
    property: str
    bank_account: str
    balance: str
    reserve: str
    unpaid_bills: str
    # What a draw may take today: the balance less the reserve and unpaid bills, at least 0.
    available: str
    has_agreement: bool


OWNERS = """
SELECT a.ledger_account_id, o.id, o.display_name, p.id, p.display_name, b.display_name, l.balance,
       coalesce(g.reserve, 0),
       -- Bills dated by today and not set aside by then, each with its markup
       -- (trust_unpaid_bills, which the app may not call, written out).
       coalesce((SELECT sum(x.amount + coalesce(mk.amount, 0)) FROM trust_bills AS x
                 LEFT JOIN trust_bill_markups AS mk ON mk.bill_id = x.id
                 WHERE x.ledger_account_id = a.ledger_account_id AND x.bill_date <= %(today)s
                   AND NOT EXISTS (
                       SELECT 1 FROM trust_bill_payments AS s
                       JOIN pgledger_transfers AS t ON t.id = s.transfer_id
                       WHERE s.bill_id = x.id AND s.step = 'set_aside'
                         AND t.event_at < (%(today)s + 1)::timestamp AT TIME ZONE 'UTC')
                   AND NOT EXISTS (
                       SELECT 1 FROM trust_bill_declines AS d
                       WHERE d.bill_id = x.id AND d.declined_on <= %(today)s)), 0),
       g.id IS NOT NULL
FROM trust_ledger_accounts AS a
JOIN trust_owners AS o ON o.id = a.owner_id
JOIN trust_properties AS p ON p.id = a.property_id
JOIN trust_bank_accounts AS b ON b.id = a.bank_account_id
JOIN pgledger_accounts AS l ON l.id = a.ledger_account_id
LEFT JOIN LATERAL (
    SELECT g.id, g.reserve FROM trust_management_agreements AS g
    WHERE g.ledger_account_id = a.ledger_account_id AND g.starts_on <= %(today)s
    ORDER BY g.starts_on DESC LIMIT 1
) AS g ON true
WHERE a.pmc_id = %(pmc)s AND a.kind = 'owner_property'
  AND (%(owner)s::uuid IS NULL OR a.owner_id = %(owner)s)
  AND (%(owners)s::uuid[] IS NULL OR a.owner_id = ANY(%(owners)s::uuid[]))
  AND (%(bank)s::uuid IS NULL OR a.bank_account_id = %(bank)s::uuid)
  AND {within}
ORDER BY o.display_name COLLATE "C", p.display_name COLLATE "C", a.ledger_account_id
""".format(within=scope.within("a.property_id"))  # noqa: S608


@router.get("/owners")
def owners(
    session: Staff, conn: Conn, group: scope.Group = None, property_id: scope.Property = None
) -> list[OwnerMoney]:
    """Each owner's property account today (maybe only a group's properties or one property's):
    what it holds, keeps back and may pay out."""
    return owner_money(conn, session.pmc_id, group=group, property_id=property_id)


def owner_money(
    conn: psycopg.Connection[Any],
    pmc: UUID,
    owner: UUID | None = None,
    owners: list[UUID] | None = None,
    group: UUID | None = None,
    property_id: UUID | None = None,
    bank: UUID | None = None,
) -> list[OwnerMoney]:
    """The PMC's owners' property accounts today (or one owner's, or some owners'; maybe only
    those of a group's properties, one property or one trust bank account), as `owners` lists
    them."""
    rows = conn.execute(
        OWNERS,
        {
            "pmc": pmc,
            "today": today(),
            "owner": owner,
            "owners": owners,
            "bank": bank,
            **scope.params(group, property_id),
        },
    ).fetchall()
    return [
        OwnerMoney(
            account=account,
            owner_id=owner_id,
            owner=name,
            property_id=property_id,
            property=prop,
            bank_account=bank,
            balance=cents(held),
            reserve=cents(reserve),
            unpaid_bills=cents(bills_),
            available=cents(max(held - reserve - bills_, Decimal(0))),
            has_agreement=agreed,
        )
        for account, owner_id, name, property_id, prop, bank, held, reserve, bills_, agreed in rows
    ]


class FeeRun(BaseModel):
    period_start: date
    period_end: date
    on: date
    # One owner account, or every one with an agreement in force at the period's end.
    account: Account | None = None


class Fee(BaseModel):
    account: str
    owner: str
    property: str
    fee: str | None
    refused: str | None


class FeeDue(BaseModel):
    account: str
    owner: str
    property: str
    # Taken: the fee taken for the period already. Otherwise what taking it today would take,
    # or why it can't be taken.
    taken: bool
    fee: str | None
    refused: str | None


FEE_ACCOUNTS = (
    "SELECT a.ledger_account_id, o.display_name, p.display_name"
    " FROM trust_ledger_accounts AS a JOIN trust_owners AS o ON o.id = a.owner_id"
    " JOIN trust_properties AS p ON p.id = a.property_id"
    " WHERE a.pmc_id = %s AND a.kind = 'owner_property'"
    "   AND (%s::text IS NULL OR a.ledger_account_id = %s::text)"
    "   AND EXISTS (SELECT 1 FROM trust_management_agreements AS g"
    "     WHERE g.ledger_account_id = a.ledger_account_id AND g.starts_on <= %s)"
    ' ORDER BY o.display_name COLLATE "C", p.display_name COLLATE "C", a.ledger_account_id'
)
FEE_REFUSALS = (
    psycopg.errors.CheckViolation,
    psycopg.errors.ExclusionViolation,
    psycopg.errors.InvalidParameterValue,
)


@router.get("/management-fees")
def management_fees_due(
    session: Staff,
    conn: Conn,
    period_start: Annotated[date, Query()],
    period_end: Annotated[date, Query()],
) -> list[FeeDue]:
    """Each owner's account with a management agreement in force at the period's end: the fee
    taken for the period already, or what Collect fees would take today, worked out by
    trust_post_management_fee itself and rolled back, so it moves nothing."""
    if period_end <= period_start:
        raise HTTPException(422, "a fee period ends after it starts")
    due = []
    for account, owner, prop in conn.execute(
        FEE_ACCOUNTS, (session.pmc_id, None, None, period_end)
    ).fetchall():
        taken = conn.execute(
            "SELECT fee FROM trust_management_fees WHERE ledger_account_id = %s"
            " AND period_start = %s AND period_end = %s",
            (account, period_start, period_end),
        ).fetchone()
        if taken is not None:
            due.append(
                FeeDue(
                    account=account,
                    owner=owner,
                    property=prop,
                    taken=True,
                    fee=cents(taken[0]),
                    refused=None,
                )
            )
            continue
        fee: Decimal | None = None
        refused: str | None = None
        try:
            with conn.transaction():  # a savepoint, always rolled back: nothing is taken
                fee = one(
                    conn,
                    "SELECT trust_post_management_fee(%s, %s, %s, %s, %s)",
                    (session.pmc_id, account, period_start, period_end, at(today())),
                )
                raise psycopg.Rollback()
        except FEE_REFUSALS as refusing:
            refused = refusal(refusing)
        due.append(
            FeeDue(
                account=account,
                owner=owner,
                property=prop,
                taken=False,
                fee=None if fee is None else cents(fee),
                refused=refused,
            )
        )
    return due


@router.post("/management-fees")
def management_fees(session: Staff, conn: Conn, run: FeeRun) -> list[Fee]:
    """Takes the management fee of a period from each owner's account (or one), once per
    period: its percent of the rent and recurring fees (pet rent, parking) collected in it, at
    least the minimum, plus a flat fee. An account the fee can't be taken from says why, and the
    others are taken."""
    not_after_today(run.on)
    if run.period_end <= run.period_start:
        raise HTTPException(422, "a fee period ends after it starts")
    if run.account is not None:
        owner_account(conn, session.pmc_id, run.account)
    rows = conn.execute(
        FEE_ACCOUNTS, (session.pmc_id, run.account, run.account, run.period_end)
    ).fetchall()
    taken = []
    for account, owner, prop in rows:
        try:
            with conn.transaction():  # a savepoint: one refusal leaves the others standing
                fee = one(
                    conn,
                    "SELECT trust_post_management_fee(%s, %s, %s, %s, %s)",
                    (session.pmc_id, account, run.period_start, run.period_end, at(run.on)),
                )
            taken.append(
                Fee(account=account, owner=owner, property=prop, fee=cents(fee), refused=None)
            )
        except FEE_REFUSALS as refused:
            taken.append(
                Fee(account=account, owner=owner, property=prop, fee=None, refused=refusal(refused))
            )
    return taken


class AutomaticFees(BaseModel):
    # Whether the nightly run takes last month's management fees by itself, from the day of the
    # month (migration 0082; the founder's answer of 2026-10-18 to q049).
    automatic: bool
    day_of_month: Annotated[int, Field(ge=1, le=28)]


class AutomaticFeesSet(AutomaticFees):
    # When it was last changed, and by whom (None: never, or by the command line).
    changed_at: datetime | None
    changed_by: str | None


FEE_SETTINGS = (
    "SELECT s.automatic, s.day_of_month, s.created_at, st.display_name"
    " FROM trust_management_fee_settings AS s"
    " LEFT JOIN trust_staff AS st ON st.id = s.recorded_by"
    " WHERE s.pmc_id = %s ORDER BY s.created_at DESC, s.id DESC LIMIT 1"
)


def fee_settings(conn: psycopg.Connection[Any], pmc_id: UUID) -> AutomaticFeesSet:
    row = conn.execute(FEE_SETTINGS, (pmc_id,)).fetchone()
    if row is None:
        return AutomaticFeesSet(automatic=False, day_of_month=1, changed_at=None, changed_by=None)
    return AutomaticFeesSet(
        automatic=row[0], day_of_month=row[1], changed_at=row[2], changed_by=row[3]
    )


@router.get("/management-fees/automatic")
def automatic_fees(session: Staff, conn: Conn) -> AutomaticFeesSet:
    """Whether the company's management fees are taken each month by themselves, and from which
    day; off until a company turns it on."""
    return fee_settings(conn, session.pmc_id)


@router.post("/management-fees/automatic")
def set_automatic_fees(session: Staff, conn: Conn, wanted: AutomaticFees) -> AutomaticFeesSet:
    """Turns taking the management fees each month by itself on or off, from a day of the month
    (1 to 28). Saving what holds already writes nothing."""
    now = fee_settings(conn, session.pmc_id)
    if (now.automatic, now.day_of_month) != (wanted.automatic, wanted.day_of_month):
        conn.execute(
            "INSERT INTO trust_management_fee_settings (pmc_id, automatic, day_of_month)"
            " VALUES (%s, %s, %s)",
            (session.pmc_id, wanted.automatic, wanted.day_of_month),
        )
    return fee_settings(conn, session.pmc_id)


class OwnerFee(BaseModel):
    request_id: UUID
    account: Account
    # An inspection fee, a lease-up fee, or another fee the owner's agreement allows (q051).
    kind: Literal["inspection", "lease_up", "other"]
    amount: Amount
    memo: Text
    on: date


@router.post("/owner-fees", status_code=201)
def take_owner_fee(session: Staff, conn: Conn, new: OwnerFee) -> Moved:
    """Takes a fee from an owner's property account into the company's fee account in the same
    trust bank account (trust_take_owner_fee, staff who hold disburse), once per request id. The
    owner's balance must hold it; the money stays in trust until the company takes its fees out.
    """
    not_after_today(new.on)
    owner_account(conn, session.pmc_id, new.account)
    try:
        moved = one(
            conn,
            "SELECT trust_take_owner_fee(%s, %s, %s, %s, %s, %s, %s)",
            (
                session.pmc_id,
                new.account,
                new.kind,
                new.amount,
                new.memo,
                f"owner-fee/{new.request_id}",
                at(new.on),
            ),
        )
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        raise HTTPException(409, refusal(refused)) from None
    return Moved(transfer_id=moved)


class LeasingFee(BaseModel):
    lease_id: UUID
    account: Account
    on: date


class Taken(BaseModel):
    amount: str


@router.post("/leasing-fees", status_code=201)
def leasing_fee(session: Staff, conn: Conn, new: LeasingFee) -> Taken:
    """Takes the leasing fee for a lease from an owner's account of its property, once."""
    not_after_today(new.on)
    owner_account(conn, session.pmc_id, new.account)
    try:
        fee = one(
            conn,
            "SELECT trust_post_leasing_fee(%s, %s, %s, %s)",
            (session.pmc_id, new.lease_id, new.account, at(new.on)),
        )
    except psycopg.errors.InvalidParameterValue as refused:
        raise HTTPException(422, refusal(refused)) from None
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        raise HTTPException(409, refusal(refused)) from None
    return Taken(amount=cents(fee))


class Payout(BaseModel):
    account: Account
    # Leave out to pay everything available.
    amount: Amount | None = None


class PayOwners(BaseModel):
    # Made by the page once per payout run, so sending it again pays nothing twice.
    request_id: UUID
    on: date
    # How the payouts were sent; recorded, nothing sent (the PMS writes no bank file).
    method: PayoutMethod
    payouts: Annotated[list[Payout], Field(min_length=1, max_length=500)]


class OwnerPaid(BaseModel):
    account: str
    owner: str
    property: str
    amount: str
    transfer_id: str


@router.post("/draws/batch", status_code=201)
def pay_owners(session: Staff, conn: Conn, run: PayOwners) -> list[OwnerPaid]:
    """Pay owners (Buildium's owner draw): a draw from each property account chosen, each at
    most what it has available (the balance less the reserve and unpaid bills), all of them or
    none, each recorded with how it was sent. Sent again, it pays nothing twice. A refusal
    names the owner and property it is about."""
    not_after_today(run.on)
    chosen = [payout.account for payout in run.payouts]
    if len(set(chosen)) != len(chosen):
        raise HTTPException(422, "pay each owner's property account once")
    names = {
        account: (owner, prop)
        for account, owner, prop in conn.execute(
            "SELECT a.ledger_account_id, o.display_name, p.display_name"
            " FROM trust_ledger_accounts AS a JOIN trust_owners AS o ON o.id = a.owner_id"
            " JOIN trust_properties AS p ON p.id = a.property_id"
            " WHERE a.pmc_id = %s AND a.kind = 'owner_property' AND a.ledger_account_id = ANY(%s)",
            (session.pmc_id, chosen),
        ).fetchall()
    }
    paid = []
    for payout in run.payouts:
        if payout.account not in names:
            raise HTTPException(404, "no such owner account")
        owner, prop = names[payout.account]
        key = f"draw/{run.request_id}/{payout.account}"
        try:
            drawn = one(
                conn,
                "SELECT trust_draw_owner(%s, %s, %s, %s, %s)",
                (session.pmc_id, payout.account, key, payout.amount, at(run.on)),
            )
        except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
            raise HTTPException(409, f"{owner}, {prop}: {refusal(refused)}") from None
        transfer = one(
            conn,
            "SELECT transfer_id FROM trust_owner_draws WHERE pmc_id = %s AND request_key = %s",
            (session.pmc_id, key),
        )
        # Sent again, the payout keeps the way it was first recorded as sent.
        conn.execute(
            "INSERT INTO trust_payout_methods (pmc_id, transfer_id, method) VALUES (%s, %s, %s)"
            " ON CONFLICT (transfer_id) DO NOTHING",
            (session.pmc_id, transfer, run.method),
        )
        paid.append(
            OwnerPaid(
                account=payout.account,
                owner=owner,
                property=prop,
                amount=cents(drawn),
                transfer_id=transfer,
            )
        )
    return paid


class Contribution(BaseModel):
    request_id: UUID
    account: Account
    amount: Amount
    on: date
    memo: Text | None = None


@router.post("/contributions", status_code=201)
def contribute(session: Staff, conn: Conn, new: Contribution) -> Moved:
    """Records money an owner put into their property account (to cover a bill, say)."""
    not_after_today(new.on)
    owner_account(conn, session.pmc_id, new.account)
    try:
        moved = one(
            conn,
            "SELECT trust_record_owner_contribution(%s, %s, %s, %s, %s, %s)",
            (
                session.pmc_id,
                new.account,
                f"contribution/{new.request_id}",
                new.amount,
                at(new.on),
                new.memo,
            ),
        )
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        raise HTTPException(409, refusal(refused)) from None
    return Moved(transfer_id=moved)


# --- the company's fees -----------------------------------------------------------------------


class CompanyFees(BaseModel):
    bank_account_id: UUID
    bank_account: str
    # The company's earned fees still in the trust bank account at the day's end.
    held: str
    # The day the oldest of them was taken from an owner (they come out oldest first), and how
    # many days it has been there: earned fees left in trust are the company's own money mixed
    # with its clients' (commingling).
    since: date | None
    days_held: int | None
    # The day to take them out by: TAKE_WITHIN after the oldest came in.
    take_by: date | None


# Earned fees are the company's own money: every state forbids mixing them with what it holds
# for others (commingling). The strictest deadline found to take them out is California's,
# 25 days (10 CCR 2835); Texas gives 30 (22 TAC 535.146), Oregon a month, and West Virginia
# forbids commingling with no number of days (W. Va. Code 30-40-18(f)). Taking them out within
# 25 days meets each of them.
TAKE_WITHIN = timedelta(days=25)


COMPANY_FEES = """
WITH moves AS (
    SELECT a.ledger_account_id, e.id, e.amount, tr.event_at
    FROM trust_ledger_accounts AS a
    JOIN pgledger_entries AS e ON e.account_id = a.ledger_account_id
    JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id
    WHERE a.pmc_id = %(pmc)s AND a.kind = 'pmc_income'
      AND tr.event_at < (%(on)s + 1)::timestamp AT TIME ZONE 'UTC'
),
taken AS (
    SELECT m.ledger_account_id, -sum(m.amount) AS amount
    FROM moves AS m WHERE m.amount < 0
    GROUP BY m.ledger_account_id
),
earned AS (
    SELECT m.ledger_account_id, m.event_at,
           sum(m.amount) OVER (
               PARTITION BY m.ledger_account_id ORDER BY m.event_at, m.id
           ) AS through
    FROM moves AS m WHERE m.amount > 0
)
SELECT b.id, b.display_name,
       coalesce((SELECT sum(m.amount) FROM moves AS m
                 WHERE m.ledger_account_id = a.ledger_account_id), 0),
       (SELECT min(x.event_at) FROM earned AS x
        WHERE x.ledger_account_id = a.ledger_account_id
          AND x.through > coalesce((SELECT t.amount FROM taken AS t
                                    WHERE t.ledger_account_id = a.ledger_account_id), 0))
FROM trust_ledger_accounts AS a
JOIN trust_bank_accounts AS b ON b.id = a.bank_account_id
WHERE a.pmc_id = %(pmc)s AND a.kind = 'pmc_income'
ORDER BY b.display_name COLLATE "C", b.id
"""


def held_fees(conn: psycopg.Connection[Any], pmc_id: UUID, on: date) -> list[CompanyFees]:
    found = []
    for bank, name, held, oldest in conn.execute(
        COMPANY_FEES, {"pmc": pmc_id, "on": on}
    ).fetchall():
        since = oldest.astimezone(UTC).date() if held > 0 and oldest is not None else None
        found.append(
            CompanyFees(
                bank_account_id=bank,
                bank_account=name,
                held=cents(held),
                since=since,
                days_held=(on - since).days if since is not None else None,
                take_by=since + TAKE_WITHIN if since is not None else None,
            )
        )
    return found


@router.get("/company-fees")
def company_fees(
    session: Staff, conn: Conn, on: Annotated[date | None, Query()] = None
) -> list[CompanyFees]:
    """The company's earned fees in each operating trust bank account at a day's end (today,
    UTC, by default), and since when the oldest has waited to be taken out."""
    return held_fees(conn, session.pmc_id, on or today())


class FeeWithdrawal(BaseModel):
    # Made by the page once per withdrawal, so sending it again takes nothing twice.
    request_id: UUID
    bank_account_id: UUID
    # Leave out to take everything the fee account holds.
    amount: Amount | None = None
    on: date


@router.post("/company-fees", status_code=201)
def take_company_fees(session: Staff, conn: Conn, new: FeeWithdrawal) -> Taken:
    """Records the company's earned fees paid out of a trust bank account to the company's own
    account: at most what the fee account holds."""
    not_after_today(new.on)
    if (
        conn.execute(
            "SELECT 1 FROM trust_ledger_accounts"
            " WHERE pmc_id = %s AND bank_account_id = %s AND kind = 'pmc_income'",
            (session.pmc_id, new.bank_account_id),
        ).fetchone()
        is None
    ):
        raise HTTPException(404, "no trust bank account of the company holds its fees by that id")
    try:
        taken = one(
            conn,
            "SELECT trust_withdraw_pmc_fees(%s, %s, %s, %s, %s)",
            (
                session.pmc_id,
                new.bank_account_id,
                f"company-fees/{new.request_id}",
                new.amount,
                at(new.on),
            ),
        )
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        raise HTTPException(409, refusal(refused)) from None
    return Taken(amount=cents(taken))
