"""The word list (src/pms/wording.py): a report's words as people read them, on screen and in
every download, while the report functions write the ledger's own words (the founder,
2026-10-26)."""

import io
import re
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader

from pms import exports, wording


@pytest.mark.parametrize(
    ("written", "read"),
    [
        ("bank_cash: Operating trust", "Bank: Operating trust"),
        ("owner_property: Owner 1 / Property 1", "Owner: Owner 1 / Property 1"),
        ("pmc_income: Synthetic PMC", "Company fees: Synthetic PMC"),
        ("prepaid_rent: Tenant 1", "Prepaid rent: Tenant 1"),
        ("tenant_deposit: Tenant 1", "Deposit: Tenant 1"),
        ("vendor_payable: Vendor 1", "Vendor: Vendor 1"),
        # Only an account's name starts with its kind: a memo that says one is left alone.
        ("Owner draw: bank_cash", "Owner draw: bank_cash"),
        ("Deposits (security_deposit)", "Deposits (security deposit)"),
        ("Operating (operating)", "Operating (operating)"),
        ("vendor_payable: Vendor 1 - Bill RF-1 set aside", "Vendor: Vendor 1 - Bill RF-1"),
        ("Bill RF-1/2 set aside", "Bill RF-1/2"),
        ("Bill RF-1 paid", "Bill RF-1 paid"),
        ("PMC", "Company"),
        ("posting", "transaction"),
        ("set aside for vendors' bills", "held for vendors' bills"),
        ("bills not yet set aside", "bills held, nothing paid yet"),
        ("all properties: bills not yet set aside", "all properties: bills held, nothing paid yet"),
        # A word inside a name stays: only the whole cell is the heading word.
        ("PMC Holdings", "PMC Holdings"),
        ("Management fee 2026-08-01 to 2026-08-31", "Management fee 08/01/2026 to 08/31/2026"),
        ("2026-03-10, end of day UTC", "03/10/2026, end of day UTC"),
        # An owner's statement runs to the next midnight, which it doesn't include.
        ("2026-09-01 00:00:00 to 2026-10-01 00:00:00 UTC", "09/01/2026 to 09/30/2026 UTC"),
        (
            "2026-09-01 12:00:00 to 2026-10-01 00:00:00 UTC",
            "09/01/2026 12:00:00 to 10/01/2026 00:00:00 UTC",
        ),
        # Not a day: a reference, a longer number.
        ("RF-2026-08-01", "RF-2026-08-01"),
        ("12026-08-01", "12026-08-01"),
    ],
)
def test_a_report_cell_reads_in_the_portals_words(written, read):
    assert wording.plain_text(written) == read


def test_anything_but_text_is_left_as_it_is():
    for value in (None, Decimal("-12.50"), 3, True, date(2026, 3, 1), {"a": 1}):
        assert wording.plain(value) == value


def test_a_day_or_a_time_is_written_out_in_utc():
    assert wording.as_text(date(2026, 3, 1)) == "03/01/2026"
    moment = datetime(2026, 3, 1, 20, 15, 7, tzinfo=timezone(timedelta(hours=-5)))
    assert wording.as_text(moment) == "03/02/2026 01:15:07 UTC"
    assert wording.as_text(Decimal("1.00")) == Decimal("1.00")


def test_a_column_reads_as_a_heading():
    assert wording.heading("monthly_rent") == "Monthly rent"
    assert wording.heading("item") == "Item"
    assert wording.heading("days_1_30") == "1-30 days"
    assert wording.heading("set_aside") == "Held for the vendor"


TABLE = exports.Table(
    "general ledger (p1, 2026-03-01, 2026-03-31)",
    ("line", "item", "account", "posted_at", "amount"),
    (
        (1, "PMC", "Synthetic PMC", None, None),
        (2, "entry", "owner_property: Owner 1 / Property 1", date(2026, 3, 5), Decimal("10.00")),
    ),
)


def test_a_table_reads_in_plain_words_with_its_figures_and_days_kept():
    plain = wording.plain_table(TABLE)
    assert plain.title == "general ledger (p1, 03/01/2026, 03/31/2026)"
    assert plain.columns == TABLE.columns
    assert plain.rows == (
        (1, "Company", "Synthetic PMC", None, None),
        (2, "entry", "Owner: Owner 1 / Property 1", date(2026, 3, 5), Decimal("10.00")),
    )
    assert wording.days_as_text(plain).rows[1][3] == "03/05/2026"
    assert wording.headed(plain).columns == ("Line", "Item", "Account", "Posted at", "Amount")


def test_the_audit_log_keeps_the_rows_the_database_wrote():
    log = exports.Table(
        "audit log (p1, 2026-03-01, 2026-03-31)",
        ("line", "item", "detail"),
        ((1, "PMC", "kind: bank_cash, starts_on: 2026-03-01"),),
    )
    plain = wording.plain_table(log)
    assert plain.rows == log.rows
    assert plain.title == "audit log (p1, 03/01/2026, 03/31/2026)"


def test_every_download_reads_in_plain_words():
    moment = datetime(2026, 3, 31, 9, 0, tzinfo=UTC)
    csv = exports.export(TABLE, "csv", moment).decode()
    assert csv.splitlines() == [
        "Line,Item,Account,Posted at,Amount",
        "1,Company,Synthetic PMC,,",
        "2,entry,Owner: Owner 1 / Property 1,03/05/2026,10.00",
    ]
    pdf = PdfReader(io.BytesIO(exports.export(TABLE, "pdf", moment))).pages[0].extract_text()
    assert "Generated 03/31/2026 09:00:00 UTC" in pdf
    assert "Posted at" in pdf
    assert "Owner: Owner 1 / Property 1" in pdf
    assert "03/05/2026" in pdf
    sheet = load_workbook(io.BytesIO(exports.export(TABLE, "xlsx", moment))).active
    assert [c.value for c in sheet[1]] == ["Line", "Item", "Account", "Posted at", "Amount"]
    assert sheet["B2"].value == "Company"
    # A day stays a day in Excel, shown as people read it.
    assert sheet["D3"].value == datetime(2026, 3, 5)
    assert sheet["D3"].number_format == "mm/dd/yyyy"


def test_the_portals_own_pages_still_read_the_report_as_written():
    """A report's rows as the owner's and tenant's pages parse them (deps.Rows) and as the
    golden cases pin them are never translated: only what people read is."""
    from pms.api.deps import Rows

    assert Rows.of(TABLE).rows[1][2] == "owner_property: Owner 1 / Property 1"


# --- the word list against the schema -----------------------------------------------------


def test_every_kind_of_ledger_account_has_its_words(conn):
    (check,) = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        # Its third shape since migration 20261106000240 (unapplied cash).
        " WHERE conname = 'trust_ledger_accounts_kind_shape_v3'"
    ).fetchone()
    assert set(re.findall(r"'([a-z_]+)'::text", check)) == set(wording.KINDS)


def test_every_reports_column_has_a_heading_without_a_name_from_the_code(conn):
    columns = {
        column
        for (column,) in conn.execute(
            "SELECT DISTINCT unnest(p.proargnames[p.pronargs + 1:]) FROM pg_proc AS p"
            " WHERE p.proname LIKE 'trust\\_report\\_%' AND p.proretset"
        )
    }
    assert "set_aside" in columns
    assert [c for c in sorted(columns) if "_" in wording.heading(c)] == []
