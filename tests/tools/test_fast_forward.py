"""Fast-forward (tools/fast_forward.py): the demo company lived through every night of the last
13 months, people paying, bouncing, being paid and paying bills by their habits, and its books
checked after each night. A failing night names its day and the seed that replays it."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tools import fast_forward


def test_every_night_of_thirteen_months_leaves_the_books_right(app_conn):
    today = datetime.now(UTC).date()
    first = (today.replace(day=1) - timedelta(days=31 * 13)).replace(day=1)
    life = fast_forward.live(app_conn, first, today, seed=0)
    print(life.totals)
    # It lived: rent was charged and paid, some late, one bounced, owners and fees were paid.
    for what in (
        "rents",
        "payments",
        "late_fees",
        "bounces",
        "owner payouts",
        "management_fees",
        "bills",
        "bills paid",
        "emails",
    ):
        assert life.totals.get(what, 0) > 0, (what, life.totals)


# The checks catch what they say they do: each test lives a few nights, then breaks one thing
# the way a bug would, and the night's check must name it.


def few_nights(app_conn):
    today = datetime.now(UTC).date()
    first = (today.replace(day=1) - timedelta(days=40)).replace(day=1)
    return fast_forward.live(app_conn, first, first + timedelta(days=4), seed=0), first + timedelta(
        4
    )


def test_a_charge_dated_after_the_night_fails_it(app_conn):
    life, day = few_nights(app_conn)
    since = datetime.now(UTC)
    lease = sorted(life.habits)[0]
    with fast_forward.as_manager(app_conn, life):
        app_conn.execute(
            "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo)"
            " VALUES (%s, %s, %s, 'fee', 10.00, 'a fee from a clock that ran ahead')",
            (life.built.pmc_id, lease, day + timedelta(days=1)),
        )
    with pytest.raises(fast_forward.NightFailed, match="fall due after it"):
        fast_forward.check_night(app_conn, life, day, since)


def test_a_payment_dated_after_the_night_fails_it(app_conn):
    life, day = few_nights(app_conn)
    since = datetime.now(UTC)
    lease = sorted(life.habits)[0]
    fast_forward.pay(app_conn, life, lease, Decimal("5.00"), day + timedelta(days=2))
    with pytest.raises(fast_forward.NightFailed, match="dated after it"):
        fast_forward.check_night(app_conn, life, day, since)


def test_a_delinquency_report_that_disagrees_with_the_leases_fails_the_night(app_conn, monkeypatch):
    life, day = few_nights(app_conn)
    real = fast_forward.owed
    monkeypatch.setattr(fast_forward, "owed", lambda *a: real(*a) + Decimal("1.00"))
    with pytest.raises(fast_forward.NightFailed, match="delinquency report"):
        fast_forward.check_night(app_conn, life, day, datetime.now(UTC))


def test_the_command_lives_on_a_database_of_its_own(database_url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert fast_forward.main(["--months", "1", "--seed", "2"]) == 0
    assert capsys.readouterr().out.startswith("ok: ")


def test_the_command_needs_a_server(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert fast_forward.main([]) == 1
    assert "needs DATABASE_URL" in capsys.readouterr().out


def test_a_failing_night_fails_the_command_and_says_how_to_replay_it(
    database_url, monkeypatch, capsys
):
    monkeypatch.setenv("DATABASE_URL", database_url)

    def broken(conn, life, day, since):
        raise fast_forward.NightFailed(f"{day}: a check that failed")

    monkeypatch.setattr(fast_forward, "check_night", broken)
    assert fast_forward.main(["--months", "1", "--seed", "4"]) == 1
    assert "replay with --months 1 --seed 4" in capsys.readouterr().out
