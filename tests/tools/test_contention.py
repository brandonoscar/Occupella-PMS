"""The nightly soak (tools/contention.py): many workers on the demo's books at once, then the
books checked. Its run fails on an error the API would answer with a 500, and on books left
wrong."""

import random
import time

import psycopg
import pytest
from fastapi import HTTPException

from pms import demo
from tools import contention


@pytest.mark.timing
def test_a_short_soak_leaves_the_books_right(database_url):
    tally, wrong = contention.soak(database_url, workers=4, seconds=3, seed=1)

    assert tally.failures == []
    assert wrong == []
    ok = {op for (op, outcome), n in tally.done.items() if outcome == "ok" and n > 0}
    assert {"pay", "bill", "contribute", "run rent"} <= ok


def idle_worker(ops):
    worker = contention.Worker.__new__(contention.Worker)
    worker.rng = random.Random(0)
    worker.operations = lambda: {name: (1, op) for name, op in ops.items()}
    worker.conn = psycopg.Connection.__new__(psycopg.Connection)
    worker.conn.close = lambda: None
    return worker


def test_an_error_the_api_would_answer_with_a_500_fails_the_run():
    def deadlock():
        raise psycopg.errors.DeadlockDetected("deadlock detected\nDETAIL: process 1 waits")

    def short():
        raise HTTPException(409, "not enough money")

    tally = contention.Tally()
    idle_worker({"pay": deadlock, "draw": short}).run(time.monotonic() + 0.05, tally)

    assert tally.done["pay", "failed"] > 0
    assert tally.done["draw", "refused"] > 0
    assert set(tally.failures) == {"pay: DeadlockDetected: deadlock detected"}
    shown = contention.report(tally, [])
    assert "FAILED" in shown and "x pay: DeadlockDetected: deadlock detected" in shown


def test_books_left_wrong_are_found(conn, app_conn):
    books = demo.books(app_conn)
    assert contention.problems(app_conn, books.pmc_id) == []
    account = next(iter(books.accounts.values()))
    (before,) = conn.execute(
        "SELECT balance FROM pgledger_accounts WHERE id = %s", (account,)
    ).fetchone()
    # Behind every trigger's back, as only a bug could: the owner's account below zero, its
    # balance no longer what its transfers add up to, and its trust bank account off.
    conn.execute("SET session_replication_role = replica")
    try:
        conn.execute("UPDATE pgledger_accounts SET balance = -1 WHERE id = %s", (account,))
        wrong = contention.problems(app_conn, books.pmc_id)
    finally:
        conn.execute("UPDATE pgledger_accounts SET balance = %s WHERE id = %s", (before, account))
        conn.execute("RESET session_replication_role")

    assert wrong[0] == f"account {account}'s balance isn't what its transfers add up to"
    assert wrong[1].startswith("trust bank account ") and "doesn't tie out (off by" in wrong[1]
    assert wrong[2] == f"owner_property account {account} is below zero (-1)"
    assert len(wrong) == 3
    assert "WRONG" in contention.report(contention.Tally(), wrong)


@pytest.mark.timing
def test_the_command_soaks_a_database_of_its_own(database_url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", database_url)

    assert contention.main(["--workers", "2", "--seconds", "1", "--seed", "3"]) == 0

    shown = capsys.readouterr().out
    assert shown.startswith("operation    ok  refused  failed\n")
    assert "FAILED" not in shown and "WRONG" not in shown


def test_the_command_needs_a_server(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert contention.main([]) == 1
    assert "needs DATABASE_URL and dbmate" in capsys.readouterr().out


def test_a_soak_that_moved_nothing_tested_nothing():
    nothing = contention.Tally()
    refused = contention.Tally()
    refused.add("draw", "refused")
    went = contention.Tally()
    went.add("pay", "ok")

    assert not contention.passed(nothing, [])
    assert not contention.passed(refused, [])
    assert "the soak tested nothing" in contention.report(refused, [])
    assert contention.passed(went, [])
    assert "tested nothing" not in contention.report(went, [])
    assert not contention.passed(went, ["trust bank account b doesn't tie out (off by 1)"])


def test_migrations_that_fail_fail_the_command(database_url, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DATABASE_URL", database_url)
    (tmp_path / "20260101000000_broken.sql").write_text(
        "-- migrate:up\nSELECT no_such_function();\n-- migrate:down\n"
    )
    monkeypatch.setattr(contention, "MIGRATIONS", tmp_path)

    assert contention.main(["--seconds", "0"]) == 1
    assert "dbmate up failed" in capsys.readouterr().out
