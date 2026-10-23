"""1099 forms (migration 20261022000162; the founder, 2026-10-23): who gets which form for a year,
each recipient's copy as a PDF, and the list a company uploads to file them.

1099-NEC: each vendor whose latest profile marks them for a 1099 (migration 0073), paid the year's
threshold or more through bills paid (with what the old system paid them in the cutover's year),
as the 1099 summary counts it (migration 0113), box 1. 1099-MISC: each owner whose year's rents,
as the owner year-end package counts them (migration 0039), come to the threshold or more, box 1,
unless their taxpayer ID marks them exempt. The threshold is the 1099 summary's: 600.00 through
2025, 2000.00 from 2026 (its inflation adjustment from 2027 not known here).

A form needs the recipient's name and taxpayer ID from their W-9 (trust_taxpayer_ids, sealed) and
their mailing address (their contact details), and the company's as the payer; one missing any of
them is listed with what is missing and left out of the copies and the list.

The recipient's copy is a substitute statement (Copy B) with the recipient's number cut to its
last four digits; the list is CSV with each form's payer, recipient (the whole number) and box 1,
laid out for the IRS's IRIS portal, NOT VERIFIED against its current template. The PMS files
nothing itself; it reads what was paid and moves no money.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import psycopg
from fpdf import FPDF

from pms import exports, sealed

Form = Literal["1099-NEC", "1099-MISC"]
Kind = Literal["vendor", "owner", "company"]
CENT = Decimal("0.01")

BOX_1: dict[Form, str] = {"1099-NEC": "Nonemployee compensation", "1099-MISC": "Rents"}
# The legend the IRS puts on a recipient's copy (Copy B).
LEGEND = (
    "This is important tax information and is being furnished to the IRS. If you are required to"
    " file a return, a negligence penalty or other sanction may be imposed on you if this income"
    " is taxable and the IRS determines that it has not been reported."
)


def threshold(year: int) -> Decimal:
    """The year's 1099 threshold, as the 1099 summary has it (migration 0113)."""
    return Decimal("600.00") if year <= 2025 else Decimal("2000.00")


def context(pmc_id: UUID, kind: Kind, person_id: UUID | None) -> str:
    """What a sealed taxpayer ID is about: a copy on another row doesn't open."""
    return f"taxpayer id:{pmc_id}:{kind}:{person_id or 'company'}"


@dataclass(frozen=True)
class TaxpayerId:
    id: UUID
    tin_type: str
    tin_last4: str
    legal_name: str
    exempt: bool
    tin_sealed: bytes
    recorded_by: str | None
    recorded_at: datetime


COLUMN: dict[Kind, str] = {"vendor": "vendor_id", "owner": "owner_id", "company": ""}


def taxpayer_id_of(
    conn: psycopg.Connection[Any], pmc_id: UUID, kind: Kind, person_id: UUID | None
) -> TaxpayerId | None:
    """A person's (or the company's) latest taxpayer ID, or None."""
    whose = (
        "t.vendor_id IS NULL AND t.owner_id IS NULL"
        if kind == "company"
        else f"t.{COLUMN[kind]} = %(person)s"
    )
    row = conn.execute(
        "SELECT t.id, t.tin_type, t.tin_last4, t.legal_name, t.exempt, t.tin_sealed,"  # noqa: S608
        " s.display_name, t.created_at FROM trust_taxpayer_ids AS t"
        " LEFT JOIN trust_staff AS s ON s.id = t.recorded_by"
        f" WHERE t.pmc_id = %(pmc)s AND {whose} ORDER BY t.created_at DESC, t.id DESC LIMIT 1",
        {"pmc": pmc_id, "person": person_id},
    ).fetchone()
    if row is None:
        return None
    found, kind, last4, name, exempt, blob, by, at = row
    return TaxpayerId(found, kind, last4, name, exempt, bytes(blob), by, at)


def record_taxpayer_id(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    kind: Kind,
    person_id: UUID | None,
    tin_type: str,
    tin: str,
    legal_name: str,
    exempt: bool,
) -> None:
    """Keeps a taxpayer ID as its W-9 gives it, the number sealed (sealed.NoKey without a key)."""
    conn.execute(
        "INSERT INTO trust_taxpayer_ids (pmc_id, vendor_id, owner_id, tin_type, tin_sealed,"
        " tin_last4, legal_name, exempt) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            pmc_id,
            person_id if kind == "vendor" else None,
            person_id if kind == "owner" else None,
            tin_type,
            sealed.seal(tin, context(pmc_id, kind, person_id)),
            tin[-4:],
            legal_name,
            exempt,
        ),
    )


def tin_shown(tin_type: str, digits: str) -> str:
    """An EIN as 00-1234567, an SSN or ITIN as 000-12-3456."""
    if tin_type == "EIN":
        return f"{digits[:2]}-{digits[2:]}"
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


def tin_cut(tin_type: str, last4: str) -> str:
    """The number as a recipient's copy may show it: its last four digits."""
    return f"XX-XXX{last4}" if tin_type == "EIN" else f"XXX-XX-{last4}"


@dataclass(frozen=True)
class Address:
    line_1: str
    line_2: str | None
    city: str
    state: str
    postal_code: str

    def lines(self) -> list[str]:
        place = f"{self.city}, {self.state} {self.postal_code}"
        return [line for line in (self.line_1, self.line_2, place) if line]


def contact_address(
    conn: psycopg.Connection[Any], pmc_id: UUID, kind: Kind, person_id: UUID
) -> Address | None:
    row = conn.execute(
        "SELECT address_line_1, address_line_2, city, state, postal_code"  # noqa: S608
        f" FROM trust_contacts WHERE pmc_id = %s AND {COLUMN[kind]} = %s"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (pmc_id, person_id),
    ).fetchone()
    return None if row is None or row[0] is None else Address(*row)


def company_address(conn: psycopg.Connection[Any], pmc_id: UUID) -> Address | None:
    row = conn.execute(
        "SELECT address_line_1, address_line_2, city, state, postal_code"
        " FROM trust_company_profiles WHERE pmc_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
        (pmc_id,),
    ).fetchone()
    return None if row is None or row[0] is None else Address(*row)


@dataclass(frozen=True)
class Party:
    """The payer or a recipient as a form names them."""

    pmc_id: UUID
    kind: Kind
    id: UUID | None
    name: str
    taxpayer_id: TaxpayerId | None
    address: Address | None

    def missing(self) -> list[str]:
        gaps = []
        if self.taxpayer_id is None:
            gaps.append("taxpayer ID")
        if self.address is None:
            gaps.append("mailing address")
        return gaps


@dataclass(frozen=True)
class Recipient:
    party: Party
    form: Form
    amount: Decimal


def payer_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> Party:
    name = conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc_id,)
    ).fetchone()
    if name is None:
        raise LookupError(f"no PMC {pmc_id}")
    return Party(
        pmc_id,
        "company",
        None,
        str(name[0]),
        taxpayer_id_of(conn, pmc_id, "company", None),
        company_address(conn, pmc_id),
    )


# Each vendor marked for a 1099 by their latest profile, with what they were paid in the year:
# bills paid (by the transfer's day, UTC) and what the old system paid them in the cutover's year,
# as trust_report_vendor_payments counts it.
VENDORS = """
WITH here AS (
    SELECT b.vendor_id, sum(tr.amount) AS total
    FROM trust_bill_payments AS p
    JOIN trust_bills AS b ON b.id = p.bill_id
    JOIN pgledger_transfers AS tr ON tr.id = p.transfer_id
    WHERE p.pmc_id = %(pmc)s AND p.step = 'paid'
      AND tr.event_at >= %(start)s AND tr.event_at < %(end)s
    GROUP BY b.vendor_id
),
before AS (
    SELECT c.vendor_id, c.amount FROM trust_vendor_payments_carried_over AS c
    WHERE c.pmc_id = %(pmc)s AND c.year = %(year)s
),
profiles AS (
    SELECT DISTINCT ON (pr.vendor_id) pr.vendor_id, pr.gets_1099
    FROM trust_vendor_profiles AS pr WHERE pr.pmc_id = %(pmc)s
    ORDER BY pr.vendor_id, pr.created_at DESC, pr.id DESC
)
SELECT v.id, v.display_name, coalesce(h.total, 0) + coalesce(c.amount, 0)
FROM trust_vendors AS v
JOIN profiles AS pf ON pf.vendor_id = v.id AND pf.gets_1099
LEFT JOIN here AS h ON h.vendor_id = v.id
LEFT JOIN before AS c ON c.vendor_id = v.id
WHERE v.pmc_id = %(pmc)s AND coalesce(h.total, 0) + coalesce(c.amount, 0) >= %(threshold)s
ORDER BY v.display_name COLLATE "C", v.id
"""

OWNERS = """
SELECT o.id, o.display_name FROM trust_owners AS o
WHERE o.pmc_id = %s
  AND EXISTS (SELECT 1 FROM trust_ledger_accounts AS a
              WHERE a.owner_id = o.id AND a.kind = 'owner_property')
ORDER BY o.display_name COLLATE "C", o.id
"""


def recipients(conn: psycopg.Connection[Any], pmc_id: UUID, year: int) -> list[Recipient]:
    """The year's 1099-NEC recipients, then its 1099-MISC ones, each with box 1."""
    start = datetime(year, 1, 1, tzinfo=UTC)
    end = datetime(year + 1, 1, 1, tzinfo=UTC)
    at_least = threshold(year)
    found: list[Recipient] = []
    for vendor, name, paid in conn.execute(
        VENDORS,
        {"pmc": pmc_id, "start": start, "end": end, "year": year, "threshold": at_least},
    ).fetchall():
        party = Party(
            pmc_id,
            "vendor",
            vendor,
            name,
            taxpayer_id_of(conn, pmc_id, "vendor", vendor),
            contact_address(conn, pmc_id, "vendor", vendor),
        )
        found.append(Recipient(party, "1099-NEC", Decimal(paid).quantize(CENT)))
    for owner, name in conn.execute(OWNERS, (pmc_id,)).fetchall():
        rents = next(
            (
                Decimal(amount)
                for _, _, item, amount in conn.execute(
                    "SELECT * FROM trust_report_owner_year_end(%s, %s, %s)", (pmc_id, owner, year)
                ).fetchall()
                if item == "1099-MISC rents (box 1)"
            ),
            Decimal(0),
        )
        tin = taxpayer_id_of(conn, pmc_id, "owner", owner)
        if rents < at_least or (tin is not None and tin.exempt):
            continue
        party = Party(
            pmc_id, "owner", owner, name, tin, contact_address(conn, pmc_id, "owner", owner)
        )
        found.append(Recipient(party, "1099-MISC", rents.quantize(CENT)))
    return found


def ready(payer: Party, found: list[Recipient], form: Form) -> list[Recipient]:
    """The form's recipients with everything a form needs; none while the payer lacks anything."""
    if payer.missing():
        return []
    return [r for r in found if r.form == form and not r.party.missing()]


def _latin1(value: str) -> str:
    return value.encode("latin-1", "replace").decode("latin-1")


def _name(party: Party) -> str:
    return party.taxpayer_id.legal_name if party.taxpayer_id else party.name


def copies_pdf(payer: Party, found: list[Recipient], form: Form, year: int) -> bytes:
    """Each recipient's copy (Copy B) of the form, a page each: a substitute statement with the
    payer's whole number and the recipient's last four digits."""
    pdf = FPDF(unit="pt", format="letter")
    pdf.set_auto_page_break(False)
    pdf.set_creator("Occupella PMS")
    for recipient in ready(payer, found, form):
        party = recipient.party
        assert payer.taxpayer_id is not None and payer.address is not None
        assert party.taxpayer_id is not None and party.address is not None
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.set_xy(54, 54)
        pdf.cell(0, 20, _latin1(f"Form {form}  {year}"))
        pdf.set_font("Helvetica", "", 10)
        pdf.set_xy(54, 76)
        pdf.cell(0, 14, "Copy B  For Recipient")
        rows = [
            ("PAYER", [_name(payer), *payer.address.lines()]),
            ("PAYER'S TIN", [tin_from(payer)]),
            ("RECIPIENT", [_name(party), *party.address.lines()]),
            (
                "RECIPIENT'S TIN",
                [tin_cut(party.taxpayer_id.tin_type, party.taxpayer_id.tin_last4)],
            ),
            (f"BOX 1  {BOX_1[form].upper()}", [f"${recipient.amount:,.2f}"]),
        ]
        y = 110.0
        for label, lines in rows:
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_xy(54, y)
            pdf.cell(0, 12, _latin1(label))
            pdf.set_font("Helvetica", "", 11)
            for line in lines:
                y += 14
                pdf.set_xy(54, y)
                pdf.cell(0, 14, _latin1(line))
            y += 24
        pdf.set_font("Helvetica", "", 8)
        pdf.set_xy(54, y + 10)
        pdf.multi_cell(504, 11, _latin1(LEGEND))
        pdf.set_xy(54, y + 50)
        pdf.multi_cell(
            504,
            11,
            _latin1(
                f"Department of the Treasury - Internal Revenue Service. {payer.name} furnishes"
                " this statement in place of the IRS's printed form."
            ),
        )
    return bytes(pdf.output())


def tin_from(party: Party) -> str:
    """A party's whole number, opened from its seal (sealed.NoKey without the server's key)."""
    tin = party.taxpayer_id
    assert tin is not None
    digits = sealed.open_sealed(tin.tin_sealed, context(party.pmc_id, party.kind, party.id))
    return tin_shown(tin.tin_type, digits)


LIST_COLUMNS = (
    "Form",
    "Tax year",
    "Payer TIN type",
    "Payer TIN",
    "Payer name",
    "Payer address",
    "Payer address 2",
    "Payer city",
    "Payer state",
    "Payer ZIP",
    "Recipient TIN type",
    "Recipient TIN",
    "Recipient name",
    "Recipient address",
    "Recipient address 2",
    "Recipient city",
    "Recipient state",
    "Recipient ZIP",
    "Box 1",
    "Box 1 amount",
)


def filing_csv(payer: Party, found: list[Recipient], form: Form, year: int) -> bytes:
    """The form's recipients as the list a company uploads to file them: whole numbers, as text
    a spreadsheet keeps (never a formula)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(LIST_COLUMNS)
    for recipient in ready(payer, found, form):
        party = recipient.party
        assert payer.taxpayer_id is not None and payer.address is not None
        assert party.taxpayer_id is not None and party.address is not None
        row = (
            form,
            year,
            payer.taxpayer_id.tin_type,
            tin_from(payer),
            _name(payer),
            payer.address.line_1,
            payer.address.line_2 or "",
            payer.address.city,
            payer.address.state,
            payer.address.postal_code,
            party.taxpayer_id.tin_type,
            tin_from(party),
            _name(party),
            party.address.line_1,
            party.address.line_2 or "",
            party.address.city,
            party.address.state,
            party.address.postal_code,
            BOX_1[form],
            exports.text(recipient.amount),
        )
        writer.writerow(
            [
                "'" + v if isinstance(v, str) and v.startswith(exports.FORMULA_START) else v
                for v in row
            ]
        )
    return out.getvalue().encode()
