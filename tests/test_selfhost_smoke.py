"""selfhost/smoke.py: the self-host smoke run, against the test database."""

from datetime import date

import psycopg
import pytest

from selfhost import smoke


def test_smoke_seeds_posts_rent_and_reports_healthy(conn):
    seeded = smoke.seed(conn)
    assert len(seeded.doors) == smoke.DOORS == 50

    total = smoke.post_month_of_rent(conn, seeded, date(2026, 2, 1))
    assert total == sum(smoke.rent_for(door) for door in range(1, 51))
    assert smoke.health(conn) == []

    held = conn.execute(
        "SELECT sum(a.balance) FROM trust_ledger_accounts t"
        " JOIN pgledger_accounts a ON a.id = t.ledger_account_id"
        " WHERE t.pmc_id = %s AND t.kind <> 'bank_cash'",
        (seeded.pmc_id,),
    ).fetchone()[0]
    assert held == 2 * total  # a month of rent plus a deposit of the same size per door


def test_smoke_produces_an_owner_statement(conn):
    seeded = smoke.seed(conn)
    smoke.post_month_of_rent(conn, seeded, date(2026, 2, 1))

    statement = smoke.owner_statement(conn, seeded, date(2026, 2, 1))

    # Owner 01 holds doors 1, 11, 21, 31 and 41: one month of rent each, nothing before.
    doors = range(1, smoke.DOORS + 1, smoke.OWNERS)
    assert statement.owner == "Owner 01"
    assert statement.properties == 5
    assert statement.closing == statement.held == sum(smoke.rent_for(door) for door in doors)


def test_the_statement_month_ends_where_the_next_begins(conn):
    seeded = smoke.seed(conn, doors=2, owners=1)
    smoke.post_month_of_rent(conn, seeded, date(2026, 12, 1))
    smoke.post_month_of_rent(conn, seeded, date(2027, 1, 1))

    december = smoke.owner_statement(conn, seeded, date(2026, 12, 1))

    # December's statement closes before January's rent; the ledgers already hold both.
    assert december.closing == smoke.rent_for(1) + smoke.rent_for(2)
    assert december.held == 2 * december.closing


def test_health_reports_every_broken_invariant(conn):
    seeded = smoke.seed(conn, doors=2, owners=1)
    # Break the ledger behind the guards' back, inside a transaction that is rolled back.
    with conn.transaction(force_rollback=True):
        conn.execute("ALTER TABLE pgledger_accounts DISABLE TRIGGER USER")
        conn.execute("UPDATE pgledger_accounts SET balance = -1 WHERE id = %s", (seeded.doors[0],))
        problems = smoke.health(conn)
    assert any("held accounts below zero" in p for p in problems)
    assert any("balance isn't the sum" in p for p in problems)
    assert smoke.health(conn) == []


def test_main_runs_against_database_url(database_url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert smoke.main() == 0
    out = capsys.readouterr().out
    assert "50 doors" in out and "healthy" in out
    assert "owner statement: Owner 01, 2026-02, 5 properties, closing" in out


def test_main_fails_when_the_statement_disagrees_with_the_ledgers(
    database_url, monkeypatch, capsys
):
    real = smoke.owner_statement

    def off_by_a_cent(conn, seeded, month):
        statement = real(conn, seeded, month)
        statement.closing += smoke.Decimal("0.01")
        return statement

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(smoke, "owner_statement", off_by_a_cent)

    assert smoke.main() == 1
    out = capsys.readouterr().out
    assert "FAIL: Owner 01's statement closes at" in out and "but their ledgers hold" in out
    assert "healthy" not in out


def test_main_needs_database_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert smoke.main() == 1
    assert "DATABASE_URL is not set" in capsys.readouterr().out


def test_waiting_for_the_database_gives_up(monkeypatch):
    monkeypatch.setattr(smoke.time, "sleep", lambda seconds: None)
    with pytest.raises(psycopg.OperationalError):
        smoke.wait_for_database(
            "postgres://nobody@127.0.0.1:1/none?sslmode=disable&connect_timeout=1", attempts=2
        )
