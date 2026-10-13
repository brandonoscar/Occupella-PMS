"""The demo PMC (src/pms/demo.py): built through the API's own routes, it opens on last month's
three-way reconciliation waiting for the manager's approval, with a check outstanding, and its
owner with three properties has a statement that rolls them up."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest

from pms import demo
from pms.api import reconcile

DAY = timedelta(days=1)


@pytest.fixture
def built(app_conn):
    return demo.build(app_conn)


def utc(day):
    return datetime.combine(day, time(), UTC)


def report(conn, query, *params):
    return conn.execute(query, params).fetchall()


def test_last_month_waits_for_the_manager_with_the_roof_check_outstanding(conn, built):
    _, last_month, this_month = built.months
    rows = report(
        conn,
        "SELECT item, detail, amount FROM trust_report_bank_reconciliation(%s, %s, %s)",
        built.pmc_id,
        built.operating,
        this_month - DAY,
    )
    figures = {item: amount for item, _, amount in rows if amount is not None}

    assert figures["outstanding withdrawals"] == Decimal("-650.00")
    assert figures["difference: adjusted bank balance - trust journal"] == 0
    assert [detail for item, detail, _ in rows if item == "outstanding withdrawal"] == [
        f"{this_month - DAY} Bill RF-2207 paid"
    ]
    assert conn.execute(
        "SELECT prepared_by, through FROM trust_reconciliation_preparations WHERE id = %s",
        (built.preparation,),
    ).fetchone() == (built.bookkeeper, this_month - DAY)
    # Approved through the month before last, for both trust bank accounts; not last month.
    assert {
        closed
        for (closed,) in conn.execute(
            "SELECT closed_through FROM trust_bank_accounts WHERE pmc_id = %s", (built.pmc_id,)
        )
    } == {utc(last_month)}


def test_the_manager_approves_last_month_and_it_ties_three_ways(conn, app_conn, built):
    with demo.Builder(app_conn, built.pmc_id).as_staff(built.manager) as session:
        approved = reconcile.approve(
            session,
            app_conn,
            built.operating,
            reconcile.Approval(preparation_id=built.preparation),
        ).id

    rows = report(
        conn,
        "SELECT item, detail, amount FROM trust_report_three_way_reconciliation(%s, %s)",
        built.pmc_id,
        approved,
    )
    by_item = {item: (detail, amount) for item, detail, amount in rows if item != "ledger"}

    assert by_item["prepared by"][0] == "Demo Bookkeeper"
    assert by_item["approved by"][0].startswith("Demo Manager at ")
    assert by_item["difference: trust journal - beneficiary ledgers"][1] == 0
    # The bank's side ties once the roof check, not yet cleared, is taken off.
    assert report(
        conn,
        "SELECT amount FROM trust_report_bank_reconciliation(%s, %s, %s)"
        " WHERE item = 'difference: adjusted bank balance - trust journal'",
        built.pmc_id,
        built.operating,
        built.months[2] - DAY,
    ) == [(0,)]


def test_the_owner_with_three_properties_has_them_on_one_statement(conn, built):
    _, last_month, this_month = built.months
    rows = report(
        conn,
        "SELECT property, item, amount, balance FROM trust_report_owner_statement(%s, %s, %s, %s)",
        built.pmc_id,
        built.owners["Demo Owner A"],
        utc(last_month),
        utc(this_month),
    )

    assert sorted({p for p, *_ in rows if p}) == ["Birch Flats", "Cedar House", "Elm Court"]
    # Each property keeps its 250.00 reserve after the month's draw.
    closing = {p: balance for p, item, _, balance in rows if item == "closing balance"}
    assert set(closing.values()) == {Decimal("250.00")}
    assert [b for _, item, _, b in rows if item == "all properties: closing balance"] == [750]


def test_two_months_of_the_work_staff_do(conn, built):
    month_ago, last_month, this_month = built.months
    count = lambda query: conn.execute(query, (built.pmc_id,)).fetchone()[0]  # noqa: E731

    # Rent for six leases each month, pet rent twice, one late fee, one bounce.
    assert count("SELECT count(*) FROM trust_charges WHERE pmc_id = %s AND kind = 'rent'") == 12
    assert (
        count(
            "SELECT count(*) FROM trust_charges WHERE pmc_id = %s"
            " AND recurring_charge_id IS NOT NULL"
        )
        == 2
    )
    assert count(
        "SELECT array_agg(c.due_on) FROM trust_charges AS c WHERE c.pmc_id = %s"
        " AND c.kind = 'fee' AND c.recurring_charge_id IS NULL"
    ) == [month_ago + 6 * DAY]  # the late fee, due the day it was assessed
    assert count("SELECT count(*) FROM trust_payment_reversals WHERE pmc_id = %s") == 1
    # Every lease is paid up.
    assert (
        count(
            "SELECT sum(CASE WHEN c.kind = 'credit' THEN -c.amount ELSE c.amount END)"
            " - (SELECT coalesce(sum(p.amount), 0) FROM trust_charge_payments AS p"
            "    WHERE p.pmc_id = c.pmc_id)"
            " + (SELECT coalesce(sum(r.amount), 0) FROM trust_payment_reversals AS r"
            "    WHERE r.pmc_id = c.pmc_id)"
            " FROM trust_charges AS c WHERE c.pmc_id = %s GROUP BY c.pmc_id"
        )
        == 0
    )
    # Two bills paid, a management fee for each property each month, last month's counting
    # the pet rent: 8% of 1395.00 + 35.00 + 1610.00.
    assert (
        count("SELECT count(*) FROM trust_bill_payments WHERE pmc_id = %s AND step = 'paid'") == 2
    )
    assert count("SELECT count(*) FROM trust_management_fees WHERE pmc_id = %s") == 8
    assert conn.execute(
        "SELECT fee FROM trust_management_fees WHERE ledger_account_id = %s AND period_start = %s",
        (built.accounts["Birch Flats"], last_month),
    ).fetchone() == (Decimal("243.20"),)


def test_every_write_but_the_first_staff_member_and_their_role_names_a_staff_member(conn, built):
    assert conn.execute(
        "SELECT table_name, count(*) FROM trust_audit_log"
        " WHERE pmc_id = %s AND staff_id IS NULL GROUP BY table_name ORDER BY table_name",
        (built.pmc_id,),
    ).fetchall() == [("trust_pmcs", 1), ("trust_staff", 1), ("trust_staff_roles", 1)]


def test_another_demo_goes_in_beside_the_first(conn, app_conn, built):
    second = demo.build(app_conn)

    assert second.pmc_id != built.pmc_id
    for made in (built, second):
        assert conn.execute(
            "SELECT count(*) FROM trust_reconciliation_preparations WHERE pmc_id = %s",
            (made.pmc_id,),
        ).fetchone() == (4,)


def test_the_command_prints_a_sign_in_link_for_the_manager_an_owner_and_a_tenant(
    conn, database_url, monkeypatch, capsys
):
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("PMS_PUBLIC_URL", "https://pms.example/")

    assert demo.main([]) == 0

    out = capsys.readouterr().out.splitlines()
    pmc_id = out[0].split()[2].rstrip(":")
    links = [line for line in out if line.startswith("Sign in as ")]
    assert [line.split(":")[0] for line in links] == [
        "Sign in as the manager, who approves last month's reconciliation",
        "Sign in as Demo Owner A, with three properties",
        "Sign in as Taylor Demo, a tenant of Cedar House",
    ]
    assert all("https://pms.example/invite#" in line for line in links)
    assert conn.execute(
        "SELECT kind, count(*) FROM trust_invites AS i JOIN trust_logins AS l"
        " ON l.id = i.login_id WHERE l.pmc_id = %s GROUP BY kind ORDER BY kind",
        (pmc_id,),
    ).fetchall() == [("owner", 1), ("staff", 1), ("tenant", 1)]
