"""The general ledger over a period of whole days, UTC (migration 20261010000023).

The golden case under tests/golden/cases/ pins the full layout; these check the rules behind it.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import language_sorted_database, make_pmc, transfer_batch

JAN_1, JAN_31 = date(2026, 1, 1), date(2026, 1, 31)
D = Decimal
Z = D("0.00")
OPERATING = "Synthetic operating trust account (operating)"
DEPOSITS = "Synthetic security_deposit trust account (security_deposit)"
OWNER_1 = "owner_property: Owner 1 / Property 1"


def at(day, hour=12, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def ledger(conn, pmc, start=JAN_1, end=JAN_31):
    return conn.execute(
        "SELECT line, item, bank, account, posted_at, description, debit, credit, balance"
        " FROM trust_report_general_ledger(%s, %s, %s)",
        (pmc.pmc_id, start, end),
    ).fetchall()


def lines_of(rows, account, bank=OPERATING):
    """(item, posted_at, description, debit, credit, balance) of one account's lines."""
    return [r[1:2] + r[4:] for r in rows if r[2] == bank and r[3] == account]


@pytest.mark.parametrize(
    ("mistake", "start", "end"),
    [
        ("no first day", None, JAN_31),
        ("no last day", JAN_1, None),
        ("a period that ends before it starts", JAN_31, JAN_1),
        ("a period that ends the day before it starts", date(2026, 1, 2), JAN_1),
    ],
)
def test_a_period_must_run_forward_from_a_day(conn, pmc, mistake, start, end):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="runs from a day"):
        ledger(conn, pmc, start, end)


def test_an_unknown_pmc_is_refused(conn):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no PMC"):
        conn.execute(
            "SELECT * FROM trust_report_general_ledger(%s, %s, %s)", (uuid.uuid4(), JAN_1, JAN_31)
        )


def test_each_account_runs_from_its_opening_to_its_closing_balance(conn, app_conn, pmc):
    owner = pmc.owners[0].account
    transfer_batch(conn, [(pmc.operating_cash, owner, "1000.00")], at(date(2025, 12, 20)))
    transfer_batch(conn, [(pmc.operating_cash, owner, "500.00")], at(date(2026, 1, 5)))
    transfer_batch(conn, [(owner, pmc.pmc_income, "80.00")], at(date(2026, 1, 9)))
    transfer_batch(conn, [(owner, pmc.operating_cash, "300.00")], at(date(2026, 2, 1)))

    rows = ledger(app_conn, pmc)

    assert lines_of(rows, OWNER_1) == [
        ("opening balance", None, None, None, None, D("1000.00")),
        (
            "entry",
            "2026-01-05 12:00:00",
            "bank_cash: Synthetic operating trust account",
            Z,
            D("500.00"),
            D("1500.00"),
        ),
        (
            "entry",
            "2026-01-09 12:00:00",
            "pmc_income: " + pmc_name(conn, pmc),
            D("80.00"),
            Z,
            D("1420.00"),
        ),
        ("closing balance", None, None, D("80.00"), D("500.00"), D("1420.00")),
    ]
    # Book cash reads as money the bank holds: a deposit is a debit and grows it.
    assert lines_of(rows, "book cash") == [
        ("opening balance", None, None, None, None, D("1000.00")),
        ("entry", "2026-01-05 12:00:00", OWNER_1, D("500.00"), Z, D("1500.00")),
        ("closing balance", None, None, D("500.00"), Z, D("1500.00")),
    ]


def test_the_period_is_whole_days_in_utc(conn, pmc):
    owner = pmc.owners[0].account
    for when in [
        datetime(2025, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),  # before: in the opening
        datetime(2026, 1, 1, tzinfo=UTC),  # the first instant
        datetime(2026, 1, 31, 23, 59, 59, 999999, tzinfo=UTC),  # the last instant
        datetime(2026, 2, 1, tzinfo=UTC),  # after
    ]:
        transfer_batch(conn, [(pmc.operating_cash, owner, "1.00")], when)

    owner_lines = lines_of(ledger(conn, pmc), OWNER_1)

    assert [line[0] for line in owner_lines] == [
        "opening balance",
        "entry",
        "entry",
        "closing balance",
    ]
    assert owner_lines[0][-1] == D("1.00")
    assert owner_lines[-1][-1] == D("3.00")


def test_a_one_day_period_holds_that_day(conn, pmc):
    owner = pmc.owners[0].account
    transfer_batch(conn, [(pmc.operating_cash, owner, "7.00")], at(JAN_31, 0, 0))

    owner_lines = lines_of(ledger(conn, pmc, JAN_31, JAN_31), OWNER_1)

    assert [line[0] for line in owner_lines] == ["opening balance", "entry", "closing balance"]


def test_entries_on_one_day_run_in_the_order_they_were_dated(conn, pmc):
    owner = pmc.owners[0].account
    transfer_batch(conn, [(pmc.operating_cash, owner, "100.00")], at(date(2026, 1, 10), 9))
    transfer_batch(conn, [(owner, pmc.pmc_income, "10.00")], at(date(2026, 1, 10), 17))
    # Backdated: posted last, dated first, so it runs first.
    transfer_batch(conn, [(pmc.operating_cash, owner, "1.00")], at(date(2026, 1, 10), 8))

    entries = [line for line in lines_of(ledger(conn, pmc), OWNER_1) if line[0] == "entry"]

    assert [(line[1], line[-1]) for line in entries] == [
        ("2026-01-10 08:00:00", D("1.00")),
        ("2026-01-10 09:00:00", D("101.00")),
        ("2026-01-10 17:00:00", D("91.00")),
    ]


def test_entries_at_one_instant_run_in_the_order_they_were_posted(conn, pmc):
    owner = pmc.owners[0].account
    noon = at(date(2026, 1, 10))
    transfer_batch(
        conn,
        [(pmc.operating_cash, owner, "100.00"), (owner, pmc.pmc_income, "30.00")],
        noon,
    )
    transfer_batch(conn, [(pmc.operating_cash, owner, "5.00")], noon)

    entries = [line for line in lines_of(ledger(conn, pmc), OWNER_1) if line[0] == "entry"]

    assert [(line[3], line[4], line[5]) for line in entries] == [
        (Z, D("100.00"), D("100.00")),
        (D("30.00"), Z, D("70.00")),
        (Z, D("5.00"), D("75.00")),
    ]


def test_an_entry_names_its_other_side_and_memo(conn, pmc):
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "900.00")], at(JAN_31))
    conn.execute(
        'SELECT pgledger_create_transfer(%s, %s, 25, %s, \'{"memo": "Key deposit"}\')',
        (pmc.deposit_cash, pmc.tenant_deposit, at(JAN_31, 13)),
    )

    descriptions = [
        line[2]
        for line in lines_of(ledger(conn, pmc), "tenant_deposit: Tenant 1", DEPOSITS)
        if line[0] == "entry"
    ]

    assert descriptions == [
        "bank_cash: Synthetic security_deposit trust account",
        "bank_cash: Synthetic security_deposit trust account - Key deposit",
    ]


def test_accounts_without_a_balance_or_entries_are_left_out_but_book_cash_is_not(conn, pmc):
    transfer_batch(conn, [(pmc.operating_cash, pmc.owners[1].account, "5.00")], at(JAN_31))

    rows = ledger(conn, pmc)

    assert sorted({(r[2], r[3]) for r in rows if r[1] == "opening balance"}) == [
        (OPERATING, "book cash"),
        (OPERATING, "owner_property: Owner 2 / Property 2"),
        (DEPOSITS, "book cash"),
    ]
    assert lines_of(rows, "book cash", DEPOSITS) == [
        ("opening balance", None, None, None, None, Z),
        ("closing balance", None, None, Z, Z, Z),
    ]


def test_a_balance_overdrawn_as_of_a_day_reads_below_zero(conn, pmc):
    owner = pmc.owners[0].account
    transfer_batch(conn, [(pmc.operating_cash, owner, "100.00")], at(date(2026, 1, 20)))
    # Backdated before the money that funded it arrived.
    transfer_batch(conn, [(owner, pmc.pmc_income, "40.00")], at(date(2026, 1, 10)))

    owner_lines = lines_of(ledger(conn, pmc), OWNER_1)

    assert [line[-1] for line in owner_lines] == [Z, D("-40.00"), D("60.00"), D("60.00")]


def test_debits_equal_credits_over_the_period(conn, pmc):
    owner = pmc.owners[0].account
    transfer_batch(conn, [(pmc.operating_cash, owner, "1000.00")], at(date(2026, 1, 3)))
    transfer_batch(conn, [(owner, pmc.vendor_payable, "250.00")], at(date(2026, 1, 4)))
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "900.00")], at(date(2026, 1, 5)))

    rows = ledger(conn, pmc)

    assert rows[-1][1:] == ("total", None, None, None, None, D("2150.00"), D("2150.00"), None)
    closings = [r for r in rows if r[1] == "closing balance"]
    assert sum(r[6] for r in closings) == sum(r[7] for r in closings) == D("2150.00")
    assert [row[0] for row in rows] == list(range(1, len(rows) + 1))


def test_each_closing_balance_is_the_trial_balances(conn, pmc):
    owner = pmc.owners[0].account
    transfer_batch(conn, [(pmc.operating_cash, owner, "1000.00")], at(date(2025, 11, 3)))
    transfer_batch(conn, [(owner, pmc.pmc_income, "90.00")], at(date(2026, 1, 4)))
    transfer_batch(conn, [(pmc.operating_cash, pmc.prepaid_rent, "300.00")], at(JAN_31, 23, 59))
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "1500.00")], at(date(2026, 2, 1)))

    closing = {
        (r[2], r[3]): r[8] for r in ledger(conn, pmc) if r[1] == "closing balance" and r[8] != 0
    }
    trial = conn.execute(
        "SELECT bank, account, debit, credit FROM trust_report_trial_balance(%s, %s)"
        " WHERE item = 'account' AND (debit <> 0 OR credit <> 0)",
        (pmc.pmc_id, JAN_31),
    ).fetchall()

    assert closing == {(bank, account): debit + credit for bank, account, debit, credit in trial}


def test_the_report_reads_the_same_in_any_time_zone_and_for_every_role(
    conn, app_conn, ai_conn, database_url, pmc
):
    owner = pmc.owners[0].account
    transfer_batch(
        conn, [(pmc.operating_cash, owner, "10.00")], at(JAN_31, 23, 30)
    )  # Feb in Auckland
    transfer_batch(conn, [(pmc.operating_cash, owner, "20.00")], at(JAN_1, 0, 30))  # Dec in LA

    readings = [ledger(app_conn, pmc), ledger(ai_conn, pmc)]
    for zone in ("UTC", "America/Los_Angeles", "Pacific/Auckland"):
        with psycopg.connect(database_url, autocommit=True) as session:
            session.execute(f"SET TIME ZONE '{zone}'")
            readings.append(ledger(session, pmc))

    assert all(reading == readings[0] for reading in readings)
    assert [line[1] for line in lines_of(readings[0], OWNER_1) if line[0] == "entry"] == [
        "2026-01-01 00:30:00",
        "2026-01-31 23:30:00",
    ]


def test_banks_and_accounts_list_in_the_same_order_on_any_server(database_url):
    with language_sorted_database(database_url) as conn:
        pmc = make_pmc(conn)
        for owner, name in zip(pmc.owners, ["owner a", "Owner b"], strict=True):
            conn.execute(
                "UPDATE trust_owners SET display_name = %s WHERE id = %s", (name, owner.owner_id)
            )
            transfer_batch(conn, [(pmc.operating_cash, owner.account, "1.00")], at(JAN_31))
        rows = ledger(conn, pmc)

    # By language rules "owner a" sorts first; by byte, "Owner b" does, on every server.
    owners = [r[3] for r in rows if r[1] == "opening balance" and r[3].startswith("owner_")]
    assert owners == [
        "owner_property: Owner b / Property 2",
        "owner_property: owner a / Property 1",
    ]


def pmc_name(conn, pmc):
    return conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]


def test_the_period_reads_in_the_second_line(conn, pmc):
    rows = ledger(conn, pmc, JAN_1, JAN_1 + timedelta(days=30))

    assert rows[:2] == [
        (1, "PMC", None, pmc_name(conn, pmc), None, None, None, None, None),
        (
            2,
            "period",
            None,
            "2026-01-01 to 2026-01-31, whole days UTC",
            None,
            None,
            None,
            None,
            None,
        ),
    ]
