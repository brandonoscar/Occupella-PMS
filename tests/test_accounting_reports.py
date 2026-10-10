"""The trial balance and the security deposit register (migration 20261010000021).

The golden cases under tests/golden/cases/ pin the full layouts; these check the rules behind
them.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import add_tenant, language_sorted_database, make_pmc, open_account, transfer_batch

JAN_31 = date(2026, 1, 31)
D = Decimal


def at(day, hour=12, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def trial(conn, pmc, as_of=JAN_31):
    return conn.execute(
        "SELECT line, item, bank, account, debit, credit FROM trust_report_trial_balance(%s, %s)",
        (pmc.pmc_id, as_of),
    ).fetchall()


def register(conn, pmc, as_of=JAN_31):
    return conn.execute(
        "SELECT line, item, bank, tenant, property, held"
        " FROM trust_report_security_deposits(%s, %s)",
        (pmc.pmc_id, as_of),
    ).fetchall()


OPERATING = "Synthetic operating trust account (operating)"
DEPOSITS = "Synthetic security_deposit trust account (security_deposit)"


@pytest.mark.parametrize("report", ["trust_report_trial_balance", "trust_report_security_deposits"])
@pytest.mark.parametrize("mistake", ["no date", "unknown PMC"])
def test_a_missing_date_or_an_unknown_pmc_is_refused(conn, pmc, report, mistake):
    pmc_id, as_of = (pmc.pmc_id, None) if mistake == "no date" else (uuid.uuid4(), JAN_31)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="as of a day|no PMC"):
        conn.execute(f"SELECT * FROM {report}(%s, %s)", (pmc_id, as_of))


# --- the trial balance ---------------------------------------------------------------------


def test_each_bank_lists_its_cash_against_what_it_holds_and_they_agree(conn, app_conn, pmc):
    owner = pmc.owners[0].account
    transfer_batch(conn, [(pmc.operating_cash, owner, "1000.00")], at(date(2026, 1, 3)))
    transfer_batch(conn, [(owner, pmc.pmc_income, "80.00")], at(date(2026, 1, 4)))
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "1500.00")], at(JAN_31, 23, 59))

    rows = trial(app_conn, pmc)

    assert [row[0] for row in rows] == list(range(1, len(rows) + 1))
    assert [row[1:] for row in rows[2:]] == [
        ("account", OPERATING, "book cash", D("1000.00"), D("0.00")),
        ("account", OPERATING, "owner_property: Owner 1 / Property 1", D("0.00"), D("920.00")),
        ("account", OPERATING, "pmc_income: " + pmc_name(conn, pmc), D("0.00"), D("80.00")),
        ("bank total", OPERATING, None, D("1000.00"), D("1000.00")),
        ("account", DEPOSITS, "book cash", D("1500.00"), D("0.00")),
        ("account", DEPOSITS, "tenant_deposit: Tenant 1", D("0.00"), D("1500.00")),
        ("bank total", DEPOSITS, None, D("1500.00"), D("1500.00")),
        ("total", None, None, D("2500.00"), D("2500.00")),
    ]


def pmc_name(conn, pmc):
    return conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]


def test_accounts_with_nothing_in_them_are_left_out_but_a_banks_cash_never_is(conn, pmc):
    rows = trial(conn, pmc)

    assert [row[1:] for row in rows[2:]] == [
        ("account", OPERATING, "book cash", D("0.00"), D("0.00")),
        ("bank total", OPERATING, None, D("0.00"), D("0.00")),
        ("account", DEPOSITS, "book cash", D("0.00"), D("0.00")),
        ("bank total", DEPOSITS, None, D("0.00"), D("0.00")),
        ("total", None, None, D("0.00"), D("0.00")),
    ]


def test_the_trial_balance_is_as_of_the_end_of_the_day(conn, pmc):
    owner = pmc.owners[0].account
    transfer_batch(conn, [(pmc.operating_cash, owner, "10.00")], at(JAN_31, 23, 59))
    transfer_batch(conn, [(pmc.operating_cash, owner, "5.00")], at(date(2026, 2, 1), 0, 0))

    assert trial(conn, pmc, date(2026, 1, 30))[-1][4:] == (D("0.00"), D("0.00"))
    assert trial(conn, pmc)[-1][4:] == (D("10.00"), D("10.00"))
    assert trial(conn, pmc, date(2026, 2, 1))[-1][4:] == (D("15.00"), D("15.00"))


def test_a_pmc_with_no_accounts_shows_a_zero_total(conn):
    pmc_id = conn.execute(
        "INSERT INTO trust_pmcs (display_name) VALUES ('Empty PMC') RETURNING pmc_id"
    ).fetchone()[0]

    rows = conn.execute(
        "SELECT item, debit, credit FROM trust_report_trial_balance(%s, %s)", (pmc_id, JAN_31)
    ).fetchall()
    assert rows == [("PMC", None, None), ("as of", None, None), ("total", D("0.00"), D("0.00"))]


def test_banks_and_accounts_list_in_the_same_order_on_any_server(database_url):
    with language_sorted_database(database_url) as conn:
        pmc = make_pmc(conn)
        for owner, name in zip(pmc.owners, ["owner a", "Owner b"], strict=True):
            conn.execute(
                "UPDATE trust_owners SET display_name = %s WHERE id = %s", (name, owner.owner_id)
            )
            transfer_batch(conn, [(pmc.operating_cash, owner.account, "1.00")], at(JAN_31))
        rows = trial(conn, pmc)

    assert [row[3] for row in rows if row[3] and row[3].startswith("owner_property")] == [
        "owner_property: Owner b / Property 2",
        "owner_property: owner a / Property 1",
    ]


# --- the security deposit register ---------------------------------------------------------


def test_each_deposit_is_listed_against_the_deposit_accounts_cash(conn, app_conn, pmc):
    second = add_tenant(conn, pmc.pmc_id, pmc.owners[1].property_id, "tenant 2")
    held = open_account(conn, pmc.pmc_id, pmc.deposit_bank_id, "tenant_deposit", tenant_id=second)
    transfer_batch(
        conn,
        [(pmc.deposit_cash, pmc.tenant_deposit, "1500.00"), (pmc.deposit_cash, held, "900.00")],
        at(date(2026, 1, 5)),
    )
    transfer_batch(conn, [(pmc.operating_cash, pmc.prepaid_rent, "50.00")], at(date(2026, 1, 5)))

    rows = register(app_conn, pmc)

    assert [row[0] for row in rows] == list(range(1, len(rows) + 1))
    assert [row[1:] for row in rows[2:]] == [
        ("deposit", DEPOSITS, "Tenant 1", "Property 1", D("1500.00")),
        ("deposit", DEPOSITS, "tenant 2", "Property 2", D("900.00")),
        ("deposits held", DEPOSITS, None, None, D("2400.00")),
        ("book cash", DEPOSITS, None, None, D("2400.00")),
        ("total held", None, None, None, D("2400.00")),
    ]


def test_a_returned_deposit_leaves_the_register(conn, pmc):
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "1500.00")], at(date(2026, 1, 5)))
    transfer_batch(conn, [(pmc.tenant_deposit, pmc.deposit_cash, "1500.00")], at(date(2026, 1, 20)))

    assert [row[1] for row in register(conn, pmc)[2:]] == [
        "deposits held",
        "book cash",
        "total held",
    ]
    assert register(conn, pmc, date(2026, 1, 10))[2][1:] == (
        "deposit",
        DEPOSITS,
        "Tenant 1",
        "Property 1",
        D("1500.00"),
    )


def test_an_empty_register_shows_zeros_not_blanks(conn, pmc):
    assert [row[1:] for row in register(conn, pmc)[2:]] == [
        ("deposits held", DEPOSITS, None, None, D("0.00")),
        ("book cash", DEPOSITS, None, None, D("0.00")),
        ("total held", None, None, None, D("0.00")),
    ]


def test_both_reports_read_the_same_in_any_time_zone_and_for_every_role(
    conn, app_conn, ai_conn, database_url, pmc
):
    transfer_batch(
        conn, [(pmc.deposit_cash, pmc.tenant_deposit, "700.00")], at(JAN_31, 23, 30)
    )  # Feb 1 in Auckland

    for read in (trial, register):
        readings = [read(app_conn, pmc), read(ai_conn, pmc)]
        for zone in ("UTC", "America/Los_Angeles", "Pacific/Auckland"):
            with psycopg.connect(database_url, autocommit=True) as session:
                session.execute(f"SET TIME ZONE '{zone}'")
                readings.append(read(session, pmc))
        assert all(reading == readings[0] for reading in readings)
    assert register(conn, pmc)[2][-1] == D("700.00")


def test_tenants_list_in_the_same_order_on_any_server(database_url):
    with language_sorted_database(database_url) as conn:
        pmc = make_pmc(conn)
        second = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "tenant 0")
        held = open_account(
            conn, pmc.pmc_id, pmc.deposit_bank_id, "tenant_deposit", tenant_id=second
        )
        transfer_batch(
            conn,
            [(pmc.deposit_cash, pmc.tenant_deposit, "1.00"), (pmc.deposit_cash, held, "1.00")],
            at(JAN_31),
        )
        rows = register(conn, pmc)

    assert [row[3] for row in rows if row[1] == "deposit"] == ["Tenant 1", "tenant 0"]


def test_the_register_is_as_of_the_end_of_the_day(conn, pmc):
    transfer_batch(
        conn, [(pmc.deposit_cash, pmc.tenant_deposit, "1500.00")], at(date(2026, 2, 1), 0, 0)
    )

    assert [row[1] for row in register(conn, pmc)].count("deposit") == 0
    assert [row[1] for row in register(conn, pmc, date(2026, 2, 1))].count("deposit") == 1


def test_a_deposit_account_with_no_tenants_yet_shows_zeros(conn):
    pmc_id = conn.execute(
        "INSERT INTO trust_pmcs (display_name) VALUES ('New PMC') RETURNING pmc_id"
    ).fetchone()[0]
    bank = conn.execute(
        "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
        " VALUES (%s, 'security_deposit', 'New deposit account') RETURNING id",
        (pmc_id,),
    ).fetchone()[0]
    open_account(conn, pmc_id, bank, "bank_cash")

    rows = conn.execute(
        "SELECT item, held FROM trust_report_security_deposits(%s, %s)", (pmc_id, JAN_31)
    ).fetchall()
    assert rows[2:] == [
        ("deposits held", D("0.00")),
        ("book cash", D("0.00")),
        ("total held", D("0.00")),
    ]
