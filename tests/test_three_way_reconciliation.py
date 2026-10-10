"""trust_report_three_way_reconciliation: bank statement, trust journal and beneficiary ledgers
for one approved reconciliation, with every difference listed (migration 20261009000009; ledger
lines in byte order since 20261009000011).

The golden case under tests/golden/cases/ pins the full layout; these check the rules behind it.
"""

from datetime import UTC, datetime
from decimal import Decimal

import psycopg
from helpers import approve, language_sorted_database, make_pmc, transfer_batch

JAN = datetime(2026, 1, 1, tzinfo=UTC)
JAN_3 = datetime(2026, 1, 3, tzinfo=UTC)
JAN_31 = datetime(2026, 1, 31, tzinfo=UTC)
FEB = datetime(2026, 2, 1, tzinfo=UTC)
FEB_5 = datetime(2026, 2, 5, tzinfo=UTC)


def report(conn, pmc_id, reconciliation_id):
    return conn.execute(
        "SELECT line, item, detail, amount FROM trust_report_three_way_reconciliation(%s, %s)",
        (pmc_id, reconciliation_id),
    ).fetchall()


def figures(rows):
    """The report's amount lines (not the ledger lines), by item."""
    return {item: amount for _, item, _, amount in rows if item != "ledger" and amount is not None}


def ledger_lines(rows):
    return [(detail, amount) for _, item, detail, amount in rows if item == "ledger"]


def test_a_move_split_across_the_period_end_shows_in_both_accounts(conn, database_url):
    # A deposit kept for damages: the owner's share is dated Jan 31, but its cash moves between
    # the trust bank accounts on Feb 1, in the same transaction. Both legs tie out at commit,
    # yet at the end of January each account's books and ledgers differ by the 320.00.
    pmc = make_pmc(conn)
    owner = pmc.owners[0].account
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "1500.00")], event_at=JAN_3)
    with psycopg.connect(database_url) as tx:
        transfer_batch(tx, [(pmc.tenant_deposit, owner, "320.00")], event_at=JAN_31)
        transfer_batch(tx, [(pmc.operating_cash, pmc.deposit_cash, "320.00")], event_at=FEB)

    deposit = report(conn, pmc.pmc_id, approve(conn, pmc.pmc_id, pmc.deposit_bank_id, JAN, FEB))
    operating = report(conn, pmc.pmc_id, approve(conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB))

    assert figures(deposit) == {
        "bank statement balance": 0,
        "trust journal (book cash)": Decimal("1500.00"),
        "beneficiary ledgers": Decimal("1180.00"),
        "difference: bank statement - trust journal": Decimal("-1500.00"),
        "difference: trust journal - beneficiary ledgers": Decimal("320.00"),
    }
    assert ledger_lines(deposit) == [("tenant_deposit: Tenant 1", Decimal("1180.00"))]
    assert figures(operating)["difference: trust journal - beneficiary ledgers"] == Decimal(
        "-320.00"
    )
    assert ledger_lines(operating) == [("owner_property: Owner 1 / Property 1", Decimal("320.00"))]


def test_only_ledgers_with_activity_before_the_period_end_are_listed(conn):
    pmc = make_pmc(conn)
    first, second = pmc.owners
    transfer_batch(conn, [(pmc.operating_cash, first.account, "100.00")], event_at=JAN_3)
    transfer_batch(conn, [(first.account, pmc.operating_cash, "100.00")], event_at=JAN_31)
    transfer_batch(conn, [(pmc.operating_cash, second.account, "50.00")], event_at=FEB_5)

    rows = report(conn, pmc.pmc_id, approve(conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB))

    # Owner 1 came and went in January (listed at zero); Owner 2's first money is February's.
    assert ledger_lines(rows) == [("owner_property: Owner 1 / Property 1", Decimal("0.00"))]
    assert [line for line, *_ in rows] == list(range(1, 12))


def test_an_account_with_no_postings_reports_zero_everywhere(conn):
    pmc = make_pmc(conn)

    rows = report(conn, pmc.pmc_id, approve(conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB))

    assert set(figures(rows).values()) == {0}
    assert ledger_lines(rows) == []


def test_a_reconciliation_is_reported_only_within_its_pmc(conn):
    pmc, other = make_pmc(conn), make_pmc(conn)
    reconciliation = approve(conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB)

    assert report(conn, other.pmc_id, reconciliation) == []
    assert len(report(conn, pmc.pmc_id, reconciliation)) == 10


def test_the_report_reads_the_same_in_any_time_zone(conn, database_url):
    pmc = make_pmc(conn)
    reconciliation = approve(conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB)

    readings = []
    for zone in ("UTC", "America/Los_Angeles", "Pacific/Auckland"):
        with psycopg.connect(database_url, autocommit=True) as session:
            session.execute(f"SET TIME ZONE '{zone}'")
            readings.append(report(session, pmc.pmc_id, reconciliation))

    assert readings[0] == readings[1] == readings[2]
    assert readings[0][2][2] == "2026-01-01 00:00:00 to 2026-02-01 00:00:00 UTC"


def test_the_app_role_can_run_the_report(conn, app_conn):
    pmc = make_pmc(conn)
    transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "75.00")], event_at=JAN_3)
    reconciliation = approve(conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB)

    assert report(app_conn, pmc.pmc_id, reconciliation) == report(conn, pmc.pmc_id, reconciliation)


def test_ledger_lines_list_in_the_same_order_on_any_server(database_url):
    # Servers that sort text by language rules put "owner a" before "Owner b"; byte order puts
    # "Owner b" first ("O" is 0x4F, "o" is 0x6F). The report must use byte order on both.
    with language_sorted_database(database_url) as conn:
        pmc = make_pmc(conn)
        for owner, name in zip(pmc.owners, ["owner a", "Owner b"], strict=True):
            conn.execute(
                "UPDATE trust_owners SET display_name = %s WHERE id = %s", (name, owner.owner_id)
            )
            transfer_batch(conn, [(pmc.operating_cash, owner.account, "10.00")], JAN_3)
        reconciliation = approve(conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB)
        rows = report(conn, pmc.pmc_id, reconciliation)

    assert [detail for detail, _ in ledger_lines(rows)] == [
        "owner_property: Owner b / Property 2",
        "owner_property: owner a / Property 1",
    ]
