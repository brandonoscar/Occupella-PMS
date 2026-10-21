"""QuickBooks (src/pms/quickbooks.py, migration 0102; the founder's review q117): each ledger
account's name in QuickBooks (the company's latest, or the PMS's), and the ledger's transfers
over days (UTC) as journal entries in QuickBooks Desktop's IIF and QuickBooks Online's CSV, each
debiting the account the money left and crediting the one it reached."""

import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import make_pmc, transfer_batch

from pms import exports, quickbooks

JAN_31 = date(2026, 1, 31)


def at(day, hour=12):
    return datetime(2026, 1, day, hour, tzinfo=UTC)


def name(conn, pmc, account, value):
    conn.execute(
        "INSERT INTO trust_quickbooks_accounts (pmc_id, ledger_account_id, name)"
        " VALUES (%s, %s, %s)",
        (pmc.pmc_id, account, value),
    )


def names(conn, pmc):
    return {a.ledger_account_id: a.in_quickbooks for a in quickbooks.accounts(conn, pmc.pmc_id)}


def test_each_account_has_the_pms_name_until_the_company_names_it(conn, app_conn):
    pmc = make_pmc(conn)
    conn.execute(
        "UPDATE trust_owners SET display_name = 'Owner: One' WHERE id = %s",
        (pmc.owners[0].owner_id,),
    )
    before = names(app_conn, pmc)

    name(conn, pmc, pmc.operating_cash, "Checking:Trust")
    name(conn, pmc, pmc.operating_cash, "Trust checking")
    name(conn, pmc, pmc.pmc_income, "Trust:Fees")
    name(conn, pmc, pmc.pmc_income, None)  # back to the PMS's own
    after = names(app_conn, pmc)

    # A colon in a holder's name would nest it: it becomes a dash.
    assert before[pmc.owners[0].account] == "Owner funds:Owner- One - Property 1"
    assert before[pmc.tenant_deposit].startswith("Tenant deposits:")
    assert before[pmc.vendor_payable] == "Vendor bills set aside:Vendor 1"
    assert after[pmc.operating_cash] == "Trust checking"
    assert after[pmc.pmc_income] == before[pmc.pmc_income]


@pytest.mark.parametrize("bad", ["Trust:", ":Trust", "Trust::Cash", "Trust :Cash", "a\tb", " x"])
def test_a_name_quickbooks_cant_read_is_refused(conn, bad):
    pmc = make_pmc(conn)
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        name(conn, pmc, pmc.operating_cash, bad)


def test_a_name_is_for_its_own_companys_account_and_never_rewritten(conn):
    pmc, theirs = make_pmc(conn), make_pmc(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        name(conn, pmc, theirs.operating_cash, "Not mine")
    name(conn, pmc, pmc.operating_cash, "Trust checking")
    for change in (
        "UPDATE trust_quickbooks_accounts SET name = 'Other' WHERE pmc_id = %s",
        "DELETE FROM trust_quickbooks_accounts WHERE pmc_id = %s",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation), conn.transaction():
            conn.execute(change, (pmc.pmc_id,))


def test_the_journal_debits_where_money_left_and_credits_where_it_went(conn, app_conn):
    pmc = make_pmc(conn)
    owner = pmc.owners[0].account
    transfer_batch(conn, [(pmc.operating_cash, owner, "1500.00")], at(3))
    transfer_batch(conn, [(owner, pmc.pmc_income, "150.00")], at(31, 23))
    transfer_batch(conn, [(owner, pmc.operating_cash, "1000.00")], datetime(2026, 2, 1, tzinfo=UTC))
    name(conn, pmc, pmc.operating_cash, "Trust checking")
    shown = names(app_conn, pmc)

    entries = quickbooks.journal(app_conn, pmc.pmc_id, date(2026, 1, 1), JAN_31)

    # February's draw is a day past the days; January 31 runs to its last second (UTC).
    assert [(e.number, e.day, e.debit, e.credit, e.amount) for e in entries] == [
        ("20260103-1", date(2026, 1, 3), "Trust checking", shown[owner], Decimal("1500.00")),
        ("20260131-1", JAN_31, shown[owner], shown[pmc.pmc_income], Decimal("150.00")),
    ]
    with pytest.raises(exports.ExportError):
        quickbooks.journal(app_conn, pmc.pmc_id, JAN_31, date(2026, 1, 1))


def test_desktop_reads_balanced_general_journal_entries():
    entries = [
        quickbooks.Entry(
            "20260103-1",
            date(2026, 1, 3),
            "Trust checking",
            "Owner funds:A",
            Decimal("1500.00"),
            'Rent\tJan "1"',
        ),
        quickbooks.Entry(
            "20260131-1", JAN_31, "Owner funds:A", "Fees earned in trust:Co", Decimal("150.00"), ""
        ),
    ]

    lines = quickbooks.iif(entries).decode("cp1252").split("\r\n")

    assert lines[:3] == [
        "!TRNS\tTRNSID\tTRNSTYPE\tDATE\tACCNT\tAMOUNT\tDOCNUM\tMEMO",
        "!SPL\tSPLID\tTRNSTYPE\tDATE\tACCNT\tAMOUNT\tDOCNUM\tMEMO",
        "!ENDTRNS",
    ]
    assert lines[3:6] == [
        "TRNS\t\tGENERAL JOURNAL\t01/03/2026\tTrust checking\t1500.00\t20260103-1\tRent Jan  1 ",
        "SPL\t\tGENERAL JOURNAL\t01/03/2026\tOwner funds:A\t-1500.00\t20260103-1\tRent Jan  1 ",
        "ENDTRNS",
    ]
    # Every transaction is one TRNS and one SPL that sum to zero, then ENDTRNS.
    assert lines[6:] == [
        "TRNS\t\tGENERAL JOURNAL\t01/31/2026\tOwner funds:A\t150.00\t20260131-1\t",
        "SPL\t\tGENERAL JOURNAL\t01/31/2026\tFees earned in trust:Co\t-150.00\t20260131-1\t",
        "ENDTRNS",
        "",
    ]


def test_online_reads_two_lines_an_entry_under_its_journal_number():
    entries = [
        quickbooks.Entry(
            "20260103-1",
            date(2026, 1, 3),
            "Trust checking",
            "Owner funds:A",
            Decimal("1500.00"),
            "=SUM(A1)",
        ),
    ]

    rows = list(csv.reader(io.StringIO(quickbooks.online_csv(entries).decode())))

    assert rows == [
        ["Journal No.", "Journal Date", "Account Name", "Debits", "Credits", "Description", "Name"],
        ["20260103-1", "01/03/2026", "Trust checking", "1500.00", "", "'=SUM(A1)", ""],
        ["20260103-1", "01/03/2026", "Owner funds:A", "", "1500.00", "'=SUM(A1)", ""],
    ]
