"""The switching tool: a PMC moving from another system brings its roster in from CSV files,
opens each trust bank account with the balances it held there, lists the checks and deposits the
bank had not cleared at the cutover, and signs off the tie-out (trust_report_cutover). What the
old system paid each vendor in the cutover's year comes over too, for the year's 1099s, and the
vendors' bills it had not paid yet, which hold back owner draws from the first day. The owners,
vendors and tenants files may carry their contact details too. The years before the cutover may
come too, as history (migration 0107): each tenant's, owner's and vendor's past lines as the old
system printed them, kept beside the ledger and never posted.

Each file goes in whole or not at all, and names are matched exactly within the PMC, so sending
a file again adds nothing twice. Records go in through the same inserts and write paths staff use
at /setup; the opening balances through trust_post_opening_balances, which checks them against
the old system's book cash. Nothing here moves money beyond posting those opening balances.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, StringConstraints, ValidationError

from pms import email, exports, statements
from pms.api import directory, outbox
from pms.api.deps import Conn, Staff, refusal

router = APIRouter(prefix="/api/switching")

Content = Annotated[str, StringConstraints(min_length=1, max_length=2_000_000)]
MAX_ROWS = 20000
CENT = Decimal("0.01")


def cents(value: Decimal) -> str:
    return exports.text(Decimal(value).quantize(CENT))


def one(conn: psycopg.Connection[Any], query: str, params: tuple[Any, ...]) -> Any:
    row = conn.execute(query, params).fetchone()
    return None if row is None else row[0]


class Bad(Exception):
    """A row that can't go in: the message says which line and why."""


@dataclass(frozen=True)
class Row:
    line: int
    cells: dict[str, str]

    def get(self, column: str, required: bool = True) -> str:
        value = self.cells.get(column, "").strip()
        if required and not value:
            raise Bad(f"line {self.line}: no {column}")
        if len(value) > 200:
            raise Bad(f"line {self.line}: the {column} is longer than 200 characters")
        return value

    def day(self, column: str, required: bool = True) -> date | None:
        text = self.get(column, required)
        if not text:
            return None
        try:
            return statements.read_date(text, f"line {self.line}")
        except statements.StatementError as bad:
            raise Bad(str(bad)) from None

    def money(self, column: str) -> Decimal:
        try:
            return statements.read_amount(self.get(column), f"line {self.line}")
        except statements.StatementError as bad:
            raise Bad(str(bad)) from None


def rows(content: str, needs: tuple[str, ...]) -> list[Row]:
    """A CSV file's rows, by lower-case column name (spaces and underscores alike)."""
    reader = csv.reader(io.StringIO(content.lstrip("\ufeff")))
    found = [r for r in reader if any(c.strip() for c in r)]
    if not found:
        raise Bad("the file is empty")
    headers = [h.strip().lower().replace("_", " ") for h in found[0]]
    missing = [n for n in needs if n not in headers]
    if missing:
        raise Bad(f"line 1: no {', '.join(missing)} column")
    if len(found) - 1 > MAX_ROWS:
        raise Bad(f"a file has at most {MAX_ROWS} rows")
    return [
        Row(n, {h: (r[i] if i < len(r) else "") for i, h in enumerate(headers)})
        for n, r in enumerate(found[1:], start=2)
    ]


# --- finding and adding by name ----------------------------------------------------------------


class Names:
    """Finds a PMC's records by exact name, adding the ones it may add; counts both."""

    def __init__(self, conn: psycopg.Connection[Any], pmc_id: UUID) -> None:
        self.conn, self.pmc = conn, pmc_id
        self.added = self.kept = 0

    def counted(self, found: Any, add: Callable[[], Any]) -> Any:
        if found is not None:
            self.kept += 1
            return found
        self.added += 1
        return add()

    def owner(self, name: str, add: bool = False, line: int = 0) -> UUID:
        found = one(
            self.conn,
            "SELECT id FROM trust_owners WHERE pmc_id = %s AND display_name = %s",
            (self.pmc, name),
        )
        if add:
            return UUID(
                str(
                    self.counted(
                        found,
                        lambda: one(
                            self.conn,
                            "INSERT INTO trust_owners (pmc_id, display_name) VALUES (%s, %s)"
                            " RETURNING id",
                            (self.pmc, name),
                        ),
                    )
                )
            )
        if found is None:
            raise Bad(f"line {line}: no owner {name!r}; import the owners first")
        return UUID(str(found))

    def vendor(self, name: str, add: bool = False, line: int = 0) -> UUID:
        found = one(
            self.conn,
            "SELECT id FROM trust_vendors WHERE pmc_id = %s AND display_name = %s",
            (self.pmc, name),
        )
        if add:
            return UUID(
                str(
                    self.counted(
                        found,
                        lambda: one(
                            self.conn,
                            "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, %s)"
                            " RETURNING id",
                            (self.pmc, name),
                        ),
                    )
                )
            )
        if found is None:
            raise Bad(f"line {line}: no vendor {name!r}; import the vendors first")
        return UUID(str(found))

    def property(self, name: str, line: int, add: bool = False) -> UUID:
        found = one(
            self.conn,
            "SELECT id FROM trust_properties WHERE pmc_id = %s AND display_name = %s",
            (self.pmc, name),
        )
        if found is None and add:
            return UUID(
                str(
                    one(
                        self.conn,
                        "INSERT INTO trust_properties (pmc_id, display_name)"
                        " VALUES (%s, %s) RETURNING id",
                        (self.pmc, name),
                    )
                )
            )
        if found is None:
            raise Bad(f"line {line}: no property {name!r}; import the properties first")
        return UUID(str(found))

    def unit(self, prop: UUID, name: str, line: int) -> UUID:
        found = one(
            self.conn,
            "SELECT id FROM trust_units WHERE property_id = %s AND display_name = %s",
            (prop, name),
        )
        if found is None:
            raise Bad(f"line {line}: no unit {name!r} at that property; import the units first")
        return UUID(str(found))

    def tenant(self, prop: UUID, name: str, add: bool = False, line: int = 0) -> UUID:
        found = one(
            self.conn,
            "SELECT id FROM trust_tenants WHERE property_id = %s AND display_name = %s",
            (prop, name),
        )
        if add:
            return UUID(
                str(
                    self.counted(
                        found,
                        lambda: one(
                            self.conn,
                            "INSERT INTO trust_tenants (pmc_id, property_id, display_name)"
                            " VALUES (%s, %s, %s) RETURNING id",
                            (self.pmc, prop, name),
                        ),
                    )
                )
            )
        if found is None:
            raise Bad(f"line {line}: no tenant {name!r} at that property")
        return UUID(str(found))

    def bank(self, name: str, kind: str, line: int) -> UUID:
        banks = self.conn.execute(
            "SELECT id, display_name FROM trust_bank_accounts WHERE pmc_id = %s AND kind = %s",
            (self.pmc, kind),
        ).fetchall()
        chosen = [b for b, n in banks if n == name or not name]
        if len(chosen) != 1:
            raise Bad(
                f"line {line}: name one of the PMC's {kind.replace('_', ' ')} trust bank"
                " accounts in a 'bank account' column"
            )
        return UUID(str(chosen[0]))


# --- importing the roster ----------------------------------------------------------------------

Kind = Literal[
    "owners",
    "vendors",
    "properties",
    "units",
    "tenants",
    "leases",
    "charges",
    "bills",
    "tenant history",
    "owner history",
    "vendor history",
]
NEEDS: dict[str, tuple[str, ...]] = {
    "owners": ("name",),
    "vendors": ("name",),
    "properties": ("property", "owner"),
    "units": ("property", "unit"),
    "tenants": ("property", "tenant"),
    "leases": ("property", "unit", "tenants", "starts on", "monthly rent"),
    "charges": ("property", "unit", "due on", "amount"),
    "bills": ("vendor", "owner", "property", "reference", "bill date", "due on", "amount", "memo"),
    "tenant history": ("property", "tenant", "date", "type", "description", "amount"),
    "owner history": ("owner", "property", "date", "type", "description", "amount"),
    "vendor history": ("vendor", "date", "type", "description", "amount"),
}
# History files (migration 0107): whose lines each holds.
HISTORY = {"tenant history": "tenant", "owner history": "owner", "vendor history": "vendor"}


# The contact columns an owners, vendors or tenants file may have, each optional, and the
# detail each fills (q127). A row with any of them sets the person's contact details to those, as
# staff saving them on the person's page does (the same again writes nothing); a row with none
# leaves what is on file.
CONTACT_COLUMNS = {
    "email": "email",
    "phone": "phone",
    "other phone": "alternate_phone",
    "address": "address_line_1",
    "address line 2": "address_line_2",
    "city": "city",
    "state": "state",
    "zip": "postal_code",
}


def import_contact(names: Names, person: directory.Person, person_id: UUID, row: Row) -> None:
    given = {
        field: row.get(column, required=False) or None for column, field in CONTACT_COLUMNS.items()
    }
    if not any(given.values()):
        return
    try:
        details = directory.Contact(**given)
    except ValidationError as bad:
        error = bad.errors()[0]
        field = str(error["loc"][0]) if error["loc"] else ""
        column = next((c for c, f in CONTACT_COLUMNS.items() if f == field), None)
        why = (
            f"the {column} isn't one the PMS can keep"
            if column
            else str(error["msg"]).removeprefix("Value error, ")
        )
        raise Bad(f"line {row.line}: {why}") from None
    directory.record_contact(names.conn, names.pmc, person, person_id, details)


def import_row(names: Names, kind: str, row: Row) -> None:
    conn, pmc = names.conn, names.pmc
    if kind == "owners":
        import_contact(names, "owner_id", names.owner(row.get("name"), add=True), row)
    elif kind == "vendors":
        import_contact(names, "vendor_id", names.vendor(row.get("name"), add=True), row)
    elif kind == "properties":
        prop = names.property(row.get("property"), row.line, add=True)
        owner = names.owner(row.get("owner"), line=row.line)
        bank = names.bank(row.get("bank account", required=False), "operating", row.line)
        names.counted(
            one(
                conn,
                "SELECT ledger_account_id FROM trust_ledger_accounts WHERE owner_id = %s"
                " AND property_id = %s AND bank_account_id = %s",
                (owner, prop, bank),
            ),
            lambda: one(
                conn,
                "SELECT trust_open_ledger_account(%s, %s, 'owner_property', %s, %s)",
                (pmc, bank, owner, prop),
            ),
        )
    elif kind == "units":
        prop = names.property(row.get("property"), row.line)
        unit = row.get("unit")
        names.counted(
            one(
                conn,
                "SELECT id FROM trust_units WHERE property_id = %s AND display_name = %s",
                (prop, unit),
            ),
            lambda: one(
                conn,
                "INSERT INTO trust_units (pmc_id, property_id, display_name)"
                " VALUES (%s, %s, %s) RETURNING id",
                (pmc, prop, unit),
            ),
        )
    elif kind == "tenants":
        prop = names.property(row.get("property"), row.line)
        import_contact(names, "tenant_id", names.tenant(prop, row.get("tenant"), add=True), row)
    elif kind == "leases":
        import_lease(names, row)
    elif kind == "bills":
        import_bill(names, row)
    else:
        import_charge(names, row)


def import_lease(names: Names, row: Row) -> None:
    conn, pmc = names.conn, names.pmc
    prop = names.property(row.get("property"), row.line)
    unit = names.unit(prop, row.get("unit"), row.line)
    tenants = [
        names.tenant(prop, n.strip(), add=True) for n in row.get("tenants").split(";") if n.strip()
    ]
    starts, ends = row.day("starts on"), row.day("ends on", required=False)
    rent = row.money("monthly rent")
    if rent <= 0:
        raise Bad(f"line {row.line}: a lease's monthly rent is more than 0")
    if ends is not None and starts is not None and ends < starts:
        raise Bad(f"line {row.line}: a lease ends on or after the day it starts")
    same = one(
        conn,
        "SELECT id FROM trust_leases WHERE unit_id = %s AND starts_on = %s"
        " AND ends_on IS NOT DISTINCT FROM %s AND monthly_rent = %s",
        (unit, starts, ends, rent),
    )
    if same is not None:
        names.kept += 1
        return
    try:
        with conn.transaction():
            conn.execute(
                "SELECT trust_open_lease(%s, %s, %s, %s, %s, %s::uuid[])",
                (pmc, unit, starts, ends, rent, tenants),
            )
    except psycopg.errors.ExclusionViolation as refused:
        raise Bad(f"line {row.line}: {refusal(refused)}") from None
    names.added += 1


def import_charge(names: Names, row: Row) -> None:
    conn, pmc = names.conn, names.pmc
    prop = names.property(row.get("property"), row.line)
    unit = names.unit(prop, row.get("unit"), row.line)
    due = row.day("due on")
    amount = row.money("amount")
    kind = (row.get("kind", required=False) or "rent").lower()
    if kind not in ("rent", "fee") or amount <= 0:
        raise Bad(f"line {row.line}: a charge carried over is rent or a fee, of more than 0")
    lease = one(
        conn,
        "SELECT id FROM trust_leases WHERE unit_id = %s AND starts_on <= %s"
        " ORDER BY starts_on DESC LIMIT 1",
        (unit, due),
    )
    if lease is None:
        raise Bad(f"line {row.line}: no lease of that unit had started by {due}")
    memo = row.get("memo", required=False) or None
    if (
        one(
            conn,
            "SELECT 1 FROM trust_charges WHERE lease_id = %s AND due_on = %s AND kind = %s"
            " AND amount = %s AND memo IS NOT DISTINCT FROM %s",
            (lease, due, kind, amount, memo),
        )
        is not None
    ):
        names.kept += 1
        return
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (pmc, lease, due, kind, amount, memo),
            )
    except (psycopg.errors.UniqueViolation, psycopg.errors.CheckViolation) as refused:
        raise Bad(f"line {row.line}: {refusal(refused)}") from None
    names.added += 1


def import_bill(names: Names, row: Row) -> None:
    """A vendor's bill the old system had not paid nor set aside: entered against the owner's
    account for that property as staff enter one, with its old reference and dates, so draws hold
    it back. Approved in the old system (an 'approved by' and 'approved on'), it needs no new
    approval; over the owner's limit without one, the owner is asked as for a new bill."""
    conn, pmc = names.conn, names.pmc
    vendor = names.vendor(row.get("vendor"), line=row.line)
    prop = names.property(row.get("property"), row.line)
    owner = names.owner(row.get("owner"), line=row.line)
    bank = names.bank(row.get("bank account", required=False), "operating", row.line)
    account = one(
        conn,
        "SELECT ledger_account_id FROM trust_ledger_accounts WHERE owner_id = %s"
        " AND property_id = %s AND bank_account_id = %s",
        (owner, prop, bank),
    )
    if account is None:
        raise Bad(
            f"line {row.line}: that owner has no account for that property in this trust bank"
            " account; import the properties first"
        )
    reference, memo = row.get("reference"), row.get("memo")
    billed, due = row.day("bill date"), row.day("due on")
    amount = row.money("amount")
    if amount <= 0:
        raise Bad(f"line {row.line}: a bill is for more than 0")
    if billed is not None and due is not None and due < billed:
        raise Bad(f"line {row.line}: a bill is due on or after its date")
    approved_by = row.get("approved by", required=False)
    approved_on = row.day("approved on", required=bool(approved_by))
    if approved_on is not None and not approved_by:
        raise Bad(f"line {row.line}: say who approved it in an 'approved by' column")
    same = conn.execute(
        "SELECT ledger_account_id, bill_date, due_on, amount, memo FROM trust_bills"
        " WHERE pmc_id = %s AND vendor_id = %s AND reference = %s",
        (pmc, vendor, reference),
    ).fetchone()
    if same is not None:
        if tuple(same) != (account, billed, due, amount, memo):
            raise Bad(f"line {row.line}: this vendor has another bill with that reference")
        names.kept += 1
        return
    bill = one(
        conn,
        "INSERT INTO trust_bills (pmc_id, vendor_id, ledger_account_id, reference, bill_date,"
        " due_on, amount, memo) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (pmc, vendor, account, reference, billed, due, amount, memo),
    )
    if approved_by:
        conn.execute(
            "INSERT INTO trust_bill_approvals (pmc_id, bill_id, approved_on, approved_by)"
            " VALUES (%s, %s, %s, %s)",
            (pmc, bill, approved_on, approved_by),
        )
    else:
        # Over the owner's limit: the email asking them to decide it (ADR 0004).
        email.write_bill_approval(conn, pmc, bill, outbox.PUBLIC_URL.get())
    names.added += 1


class Import(BaseModel):
    kind: Kind
    content: Content


class Counted(BaseModel):
    added: int
    kept: int


@router.post("/import")
def import_file(session: Staff, conn: Conn, new: Import) -> Counted:
    """Brings one kind of record in from a CSV file, whole: what is there already is kept. A
    history file's lines are counted, and the same file again keeps them all."""
    names = Names(conn, session.pmc_id)
    try:
        if new.kind in HISTORY:
            return import_history(
                names, HISTORY[new.kind], new.content, rows(new.content, NEEDS[new.kind])
            )
        for row in rows(new.content, NEEDS[new.kind]):
            import_row(names, new.kind, row)
    except Bad as bad:
        raise HTTPException(422, str(bad)) from None
    return Counted(added=names.added, kept=names.kept)


def history_party(names: Names, who: str, row: Row) -> tuple[str, Any]:
    """The column and the record a history line is about: a tenant at a property (added if the
    roster has none, as one who left before the switch), an owner's account for a property, or a
    vendor (added if the roster has none)."""
    if who == "tenant":
        prop = names.property(row.get("property"), row.line)
        return "tenant_id", names.tenant(prop, row.get("tenant"), add=True)
    if who == "vendor":
        return "vendor_id", names.vendor(row.get("vendor"), add=True)
    prop = names.property(row.get("property"), row.line)
    owner = names.owner(row.get("owner"), line=row.line)
    bank = names.bank(row.get("bank account", required=False), "operating", row.line)
    account = one(
        names.conn,
        "SELECT ledger_account_id FROM trust_ledger_accounts WHERE owner_id = %s"
        " AND property_id = %s AND bank_account_id = %s",
        (owner, prop, bank),
    )
    if account is None:
        raise Bad(
            f"line {row.line}: that owner has no account for that property; import the"
            " properties first"
        )
    return "owner_account", account


def import_history(names: Names, who: str, content: str, found: list[Row]) -> Counted:
    """A file of the old system's past lines, whole: never posted, kept as history under each
    person (migration 0107). The same file again adds nothing."""
    conn, pmc = names.conn, names.pmc
    digest = hashlib.sha256(content.encode()).digest()
    if one(
        conn,
        "SELECT 1 FROM trust_history_files WHERE pmc_id = %s AND file_sha256 = %s",
        (pmc, digest),
    ):
        return Counted(added=0, kept=len(found))
    file_id = one(
        conn,
        "INSERT INTO trust_history_files (pmc_id, kind, file_sha256, line_count)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (pmc, who, digest, len(found)),
    )
    for row in found:
        column, party = history_party(names, who, row)
        kind = row.get("type")
        if len(kind) > 50:
            raise Bad(f"line {row.line}: the type is longer than 50 characters")
        reference = row.get("reference", required=False) or None
        if reference is not None and len(reference) > 100:
            raise Bad(f"line {row.line}: the reference is longer than 100 characters")
        unit = (row.get("unit", required=False) or None) if who == "tenant" else None
        try:
            with conn.transaction():
                conn.execute(
                    f"INSERT INTO trust_history_lines (pmc_id, file_id, line_no, {column},"  # noqa: S608
                    " posted_on, kind, description, amount, reference, unit)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        pmc,
                        file_id,
                        row.line,
                        party,
                        row.day("date"),
                        kind,
                        row.get("description"),
                        row.money("amount"),
                        reference,
                        unit,
                    ),
                )
        except psycopg.errors.CheckViolation as refused:  # a day to come
            raise Bad(refusal(refused)) from None
    return Counted(added=len(found), kept=0)


# --- opening balances ----------------------------------------------------------------------------


class Opening(BaseModel):
    bank_account_id: UUID
    opened_on: date
    # The old system's book cash for this trust bank account at the start of opened_on.
    book_cash: Annotated[Decimal, Field(gt=0, lt=Decimal("1000000000"), decimal_places=2)]
    content: Content


OPENING_KINDS = {"owner", "prepaid rent", "deposit", "vendor", "fees"}


def ledger_account(names: Names, bank: UUID, row: Row) -> str:
    """The ledger account in the trust bank account a balance row names, opened if need be."""
    conn, pmc = names.conn, names.pmc
    kind = row.get("kind").lower()
    if kind not in OPENING_KINDS:
        raise Bad(f"line {row.line}: the kind is one of {', '.join(sorted(OPENING_KINDS))}")
    if kind == "owner":
        prop = names.property(row.get("property"), row.line)
        owner = names.owner(row.get("owner"), line=row.line)
        found = one(
            conn,
            "SELECT ledger_account_id FROM trust_ledger_accounts WHERE owner_id = %s"
            " AND property_id = %s AND bank_account_id = %s",
            (owner, prop, bank),
        )
        if found is None:
            raise Bad(
                f"line {row.line}: that owner has no account for that property in this "
                "trust bank account; import the properties first"
            )
        return str(found)
    if kind == "fees":
        found = one(
            conn,
            "SELECT ledger_account_id FROM trust_ledger_accounts"
            " WHERE bank_account_id = %s AND kind = 'pmc_income'",
            (bank,),
        )
        if found is None:
            raise Bad(f"line {row.line}: the PMC's fees are held in an operating trust account")
        return str(found)
    if kind == "vendor":
        vendor = names.vendor(row.get("vendor"), line=row.line)
        found = one(
            conn,
            "SELECT ledger_account_id FROM trust_ledger_accounts"
            " WHERE bank_account_id = %s AND kind = 'vendor_payable' AND vendor_id = %s",
            (bank, vendor),
        )
        return str(
            found or one(conn, "SELECT trust_open_vendor_account(%s, %s, %s)", (pmc, bank, vendor))
        )
    held = "prepaid_rent" if kind == "prepaid rent" else "tenant_deposit"
    tenant = names.tenant(
        names.property(row.get("property"), row.line), row.get("tenant"), line=row.line
    )
    found = one(
        conn,
        "SELECT ledger_account_id FROM trust_ledger_accounts"
        # A security deposit, the only kind the old system's balances bring (migration 0092).
        " WHERE bank_account_id = %s AND kind = %s AND tenant_id = %s AND deposit_kind IS NULL",
        (bank, held, tenant),
    )
    if found is not None:
        return str(found)
    try:
        with conn.transaction():
            return str(
                one(
                    conn,
                    "SELECT trust_open_ledger_account(%s, %s, %s, NULL, NULL, %s)",
                    (pmc, bank, held, tenant),
                )
            )
    except psycopg.errors.CheckViolation as refused:  # a deposit in an operating account
        raise Bad(f"line {row.line}: {refusal(refused)}") from None


class Opened(BaseModel):
    accounts: int


def bank_of(conn: psycopg.Connection[Any], pmc_id: UUID, bank: UUID) -> None:
    if (
        one(conn, "SELECT 1 FROM trust_bank_accounts WHERE pmc_id = %s AND id = %s", (pmc_id, bank))
        is None
    ):
        raise HTTPException(404, "no such trust bank account")


@router.post("/opening-balances", status_code=201)
def open_balances(session: Staff, conn: Conn, new: Opening) -> Opened:
    """Opens a trust bank account with what each account in it held in the old system at the
    start of the cutover date, checked against the old book cash. Once per account."""
    bank_of(conn, session.pmc_id, new.bank_account_id)
    names = Names(conn, session.pmc_id)
    balances: dict[str, Decimal] = {}
    try:
        for row in rows(new.content, ("kind", "amount")):
            account = ledger_account(names, new.bank_account_id, row)
            if account in balances:
                raise Bad(f"line {row.line}: that account has a balance on another line already")
            balances[account] = row.money("amount")
    except Bad as bad:
        raise HTTPException(422, str(bad)) from None
    name = one(conn, "SELECT display_name FROM trust_staff WHERE id = %s", (session.person_id,))
    try:
        conn.execute(
            "SELECT trust_post_opening_balances(%s, %s, %s, %s, %s, %s)",
            (
                session.pmc_id,
                new.bank_account_id,
                new.opened_on,
                Jsonb({a: str(v) for a, v in balances.items()}),
                new.book_cash,
                name,
            ),
        )
    except psycopg.errors.InvalidParameterValue as bad:  # an amount, a date still to come
        raise HTTPException(422, refusal(bad)) from None
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as refused:
        raise HTTPException(409, refusal(refused)) from None
    return Opened(accounts=len(balances))


# --- vendor payments carried over ---------------------------------------------------------------


class VendorPayments(BaseModel):
    # The cutover: the old system's payments in the year before this day. A January 1 cutover
    # carries over the whole year before it.
    paid_before: date
    content: Content


@router.post("/vendor-payments", status_code=201)
def carry_vendor_payments(session: Staff, conn: Conn, new: VendorPayments) -> Counted:
    """What the old system paid each vendor in the cutover's year before it, for the year's
    1099s: a CSV file with vendor and paid. Sending the same file again adds nothing; another
    amount for a vendor whose year was carried over already is refused."""
    year = (new.paid_before - timedelta(days=1)).year if new.paid_before > date.min else 0
    if not 1900 <= year <= 9998:
        raise HTTPException(422, "the cutover is a day from 1900-01-02 to 9999-01-01")
    names = Names(conn, session.pmc_id)
    added = kept = 0
    try:
        for row in rows(new.content, ("vendor", "paid")):
            name = row.get("vendor")
            vendor = names.vendor(name, line=row.line)
            amount = row.money("paid")
            if amount <= 0:
                raise Bad(f"line {row.line}: what was paid is more than 0")
            # Another request carrying the same vendor's year over at once is waited for.
            if conn.execute(
                "INSERT INTO trust_vendor_payments_carried_over"
                " (pmc_id, vendor_id, year, paid_before, amount) VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (vendor_id, year) DO NOTHING RETURNING year",
                (session.pmc_id, vendor, year, new.paid_before, amount),
            ).fetchone():
                added += 1
                continue
            before = conn.execute(
                "SELECT paid_before, amount FROM trust_vendor_payments_carried_over"
                " WHERE vendor_id = %s AND year = %s",
                (vendor, year),
            ).fetchone()
            assert before is not None
            if (before[0], before[1]) != (new.paid_before, amount):
                raise Bad(
                    f"line {row.line}: {name}'s {year} was carried over already:"
                    f" {cents(before[1])} paid before {before[0]}"
                )
            kept += 1
    except Bad as bad:
        raise HTTPException(422, str(bad)) from None
    return Counted(added=added, kept=kept)


# --- items carried over and the sign-off ----------------------------------------------------------


class Items(BaseModel):
    bank_account_id: UUID
    content: Content


@router.post("/items", status_code=201)
def carry_items(session: Staff, conn: Conn, new: Items) -> Counted:
    """The old system's checks (negative) and deposits the bank had not cleared at the cutover:
    a CSV file with date, amount, description and an optional reference."""
    bank_of(conn, session.pmc_id, new.bank_account_id)
    added = 0
    try:
        for row in rows(new.content, ("date", "amount", "description")):
            amount = row.money("amount")
            if amount == 0:
                raise Bad(f"line {row.line}: an item moved some money")
            try:
                with conn.transaction():
                    conn.execute(
                        "INSERT INTO trust_cutover_items (pmc_id, bank_account_id, issued_on,"
                        " amount, description, reference, entered_by)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (
                            session.pmc_id,
                            new.bank_account_id,
                            row.day("date"),
                            amount,
                            row.get("description"),
                            row.get("reference", required=False) or None,
                            session.person_id,
                        ),
                    )
            except psycopg.errors.CheckViolation as refused:  # no opening, signed off, too late
                raise HTTPException(409, f"line {row.line}: {refusal(refused)}") from None
            added += 1
    except Bad as bad:
        raise HTTPException(422, str(bad)) from None
    return Counted(added=added, kept=0)


class SignOff(BaseModel):
    bank_account_id: UUID


@router.post("/sign-off", status_code=201)
def sign_off(session: Staff, conn: Conn, new: SignOff) -> None:
    """Signs off the cutover's tie-out as the staff member: the items carried over are frozen."""
    bank_of(conn, session.pmc_id, new.bank_account_id)
    try:
        conn.execute(
            "INSERT INTO trust_cutover_signoffs (pmc_id, bank_account_id, signed_by)"
            " VALUES (%s, %s, %s)",
            (session.pmc_id, new.bank_account_id, session.person_id),
        )
    except psycopg.errors.CheckViolation as refused:  # no opening balances, signed off
        raise HTTPException(409, refusal(refused)) from None


# --- where each trust bank account stands ---------------------------------------------------------


class TieOutRow(BaseModel):
    line: int
    item: str
    detail: str | None
    amount: str | None


class Cutover(BaseModel):
    bank_account_id: UUID
    name: str
    kind: str
    # True while it can still open with balances: no posting and no reconciliation yet.
    can_open: bool
    opened_on: date | None
    signed_off: bool
    tie_out: list[TieOutRow]


@router.get("")
def cutovers(session: Staff, conn: Conn) -> list[Cutover]:
    """Each trust bank account's cutover: whether it can still open, and its tie-out."""
    found = []
    for bank, name, kind, opened, closed, signed in conn.execute(
        "SELECT b.id, b.display_name, b.kind, o.opened_on, b.closed_through,"
        " EXISTS (SELECT 1 FROM trust_cutover_signoffs AS s WHERE s.bank_account_id = b.id)"
        " FROM trust_bank_accounts AS b"
        " LEFT JOIN trust_opening_balances AS o ON o.bank_account_id = b.id"
        ' WHERE b.pmc_id = %s ORDER BY b.kind, b.display_name COLLATE "C", b.id',
        (session.pmc_id,),
    ).fetchall():
        posted = one(
            conn,
            "SELECT 1 FROM trust_ledger_accounts AS t JOIN pgledger_entries AS e"
            " ON e.account_id = t.ledger_account_id WHERE t.bank_account_id = %s LIMIT 1",
            (bank,),
        )
        tie_out = (
            []
            if opened is None
            else [
                TieOutRow(line=n, item=i, detail=d, amount=None if a is None else cents(a))
                for n, i, d, a in conn.execute(
                    "SELECT * FROM trust_report_cutover(%s, %s)", (session.pmc_id, bank)
                )
            ]
        )
        found.append(
            Cutover(
                bank_account_id=bank,
                name=name,
                kind=kind,
                can_open=opened is None and closed is None and posted is None,
                opened_on=opened,
                signed_off=signed,
                tie_out=tie_out,
            )
        )
    return found
