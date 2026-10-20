"""Downloads from the pages that show them (the founder's review q110): a lease's ledger from its
Ledger tab, the month-end reconciliation of a day and an approved period's three-way
reconciliation, each the report Reports exports (`GET /api/reports/{name}`), with the page's own
arguments, under the session's PMC."""

import csv
import io
from datetime import UTC, date, datetime, timedelta

import pytest
from helpers import add_tenant, add_unit, approve, import_statement, make_pmc, open_lease
from people import join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
REPORTS = "/api/reports/trust_report_"


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def rows(answer):
    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"].startswith("text/csv")
    return list(csv.reader(io.StringIO(answer.text)))


def test_staff_download_a_leases_ledger_in_each_format(client, conn, staff, pmc):
    prop = pmc.owners[0].property_id
    lease = open_lease(
        conn,
        pmc.pmc_id,
        add_unit(conn, pmc.pmc_id, prop, "Unit 1"),
        TODAY - timedelta(days=40),
        None,
        "1200.00",
        [add_tenant(conn, pmc.pmc_id, prop, "Tenant 1")],
    )
    args = {"arg": [str(lease), str(TODAY)]}

    table = rows(client.get(f"{REPORTS}tenant_ledger", params={**args, "format": "csv"}))
    pdf = client.get(f"{REPORTS}tenant_ledger", params={**args, "format": "pdf"})
    xlsx = client.get(f"{REPORTS}tenant_ledger", params={**args, "format": "xlsx"})

    assert table[0][:3] == ["line", "on_date", "item"]
    assert (pdf.status_code, pdf.content[:5]) == (200, b"%PDF-")
    assert (xlsx.status_code, xlsx.content[:2]) == (200, b"PK")
    assert pdf.headers["content-disposition"].endswith('tenant_ledger.pdf"')


def test_staff_download_the_month_end_and_an_approved_periods_three_way(client, conn, staff, pmc):
    bank = pmc.operating_bank_id
    reconciliation = approve(conn, pmc.pmc_id, bank, date(2026, 1, 1), date(2026, 2, 1))
    import_statement(
        conn,
        pmc.pmc_id,
        bank,
        staff.person_id,
        date(2026, 2, 1),
        date(2026, 2, 28),
        [(date(2026, 2, 10), "10.00", "Deposit", None)],
    )

    day = rows(
        client.get(f"{REPORTS}bank_reconciliation", params={"arg": [str(bank), "2026-02-28"]})
    )
    three_way = client.get(
        f"{REPORTS}three_way_reconciliation", params={"arg": str(reconciliation), "format": "pdf"}
    )

    assert day[0] == ["line", "item", "detail", "amount"] and len(day) > 1
    assert (three_way.status_code, three_way.content[:5]) == (200, b"%PDF-")


def test_another_companys_reconciliation_downloads_none_of_its_figures(client, conn, staff, pmc):
    elsewhere = make_pmc(conn)
    theirs = approve(
        conn, elsewhere.pmc_id, elsewhere.operating_bank_id, date(2026, 1, 1), date(2026, 2, 1)
    )

    answer = client.get(f"{REPORTS}three_way_reconciliation", params={"arg": str(theirs)})

    # The report reads only the session's company: theirs is no reconciliation of ours, so the
    # download has its header and not a figure of theirs.
    assert rows(answer) == [["line", "item", "detail", "amount"]]


@pytest.mark.parametrize("args", [["not-a-lease", "2026-01-01"], ["2026-01-01"]])
def test_bad_arguments_are_refused(client, staff, args):
    answer = client.get(f"{REPORTS}tenant_ledger", params={"arg": args})

    assert answer.status_code == 422


def test_only_staff_download_them(client, conn, app_conn, pmc):
    join_as_tenant(client, conn, app_conn, pmc)

    answer = client.get(
        f"{REPORTS}bank_reconciliation", params={"arg": [str(pmc.pmc_id), "2026-01-01"]}
    )

    assert answer.status_code == 403
