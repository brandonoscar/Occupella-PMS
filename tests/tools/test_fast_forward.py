"""Fast-forward (tools/fast_forward.py): the demo company lived through every night of the last
13 months, people paying, bouncing, being paid and paying bills by their habits, and its books
checked after each night. A failing night names its day and the seed that replays it."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

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


def found_by(monkeypatch, fragment, found):
    """The night's queries, but the one containing `fragment` finds `found`: what the database
    would hold if a bug its own rules refuse had got past them."""
    real = fast_forward.rows
    monkeypatch.setattr(
        fast_forward, "rows", lambda conn, q, p=(): found if fragment in q else real(conn, q, p)
    )


@pytest.mark.parametrize(
    ("fragment", "found", "named"),
    [
        ("HAVING count(*) > 1", [("a lease", "a day", 2)], "rent charged twice"),
        ("trust_report_trial_balance", [(Decimal("10.00"), Decimal("9.00"))], "trial balance"),
        ("trust_report_balance_sheet", [("a bank", Decimal("1.00"))], "book cash"),
    ],
)
def test_what_the_database_refuses_still_fails_the_night(
    app_conn, monkeypatch, fragment, found, named
):
    life, day = few_nights(app_conn)
    found_by(monkeypatch, fragment, found)
    with pytest.raises(fast_forward.NightFailed, match=named):
        fast_forward.check_night(app_conn, life, day, datetime.now(UTC))


def test_an_owner_statement_that_disagrees_with_the_ledger_fails_the_month(app_conn, monkeypatch):
    life, _ = few_nights(app_conn)
    found_by(monkeypatch, "trust_report_owner_statement", [(Decimal("123456.78"),)])
    with pytest.raises(fast_forward.NightFailed, match="statement for"):
        fast_forward.check_month(app_conn, life, life.first)


def test_a_month_whose_management_fee_was_never_taken_fails_its_last_night(app_conn):
    # Five nights in, the fee day (the 5th) of the month after has not come: on that month's
    # last night this month's fee is still not taken.
    life, _ = few_nights(app_conn)
    next_month = (life.first + timedelta(days=32)).replace(day=1)
    last_night = (next_month + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    with pytest.raises(fast_forward.NightFailed, match="management fee"):
        fast_forward.check_fees(app_conn, life, last_night)


def a_bill(app_conn, life, day):
    """A plumber's bill dated `day`, open, as staff enter one."""
    with fast_forward.as_manager(app_conn, life) as s:
        fast_forward.payables.enter_bill(
            s,
            app_conn,
            fast_forward.payables.NewBill(
                vendor_id=sorted(life.built.vendors.values())[0],
                account=sorted(life.built.accounts.values())[0],
                reference=f"FF-{day:%Y%m%d}",
                bill_date=day,
                due_on=day,
                amount=Decimal("12.00"),
                memo="A test bill",
            ),
        )


def test_nobody_pays_or_is_paid_before_their_lease_or_bill(app_conn):
    life, day = few_nights(app_conn)
    a_bill(app_conn, life, day)
    before = dict(life.totals)
    long_ago = date(2000, 1, 3)
    fast_forward.tenants_pay(app_conn, life, long_ago)
    fast_forward.bills_paid(app_conn, life, long_ago)
    assert life.totals == {**before, "bills paid": before.get("bills paid", 0)}


def test_a_refusal_in_words_is_passed_over_and_anything_else_stops_the_run(app_conn, monkeypatch):
    life, day = few_nights(app_conn)
    a_bill(app_conn, life, day)

    def refused(*a):
        raise HTTPException(409, "not enough available")

    def broken(*a):
        raise RuntimeError("a bug")

    monkeypatch.setattr(fast_forward.payables, "pay_bills", refused)
    fast_forward.bills_paid(app_conn, life, date(2100, 1, 1))

    monkeypatch.setattr(fast_forward.payables, "pay_bills", broken)
    with pytest.raises(RuntimeError, match="a bug"):
        fast_forward.bills_paid(app_conn, life, date(2100, 1, 1))
    monkeypatch.setattr(fast_forward.payables, "pay_owners", broken)
    with pytest.raises(RuntimeError, match="a bug"):
        fast_forward.owners_paid(app_conn, life, day)


def test_the_command_says_so_when_the_migrations_fail(database_url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", database_url)

    class Refusing:
        def __init__(self, *args):
            pass

        def run(self, *args):
            return SimpleNamespace(returncode=1, stderr="a migration that failed")

    monkeypatch.setattr(fast_forward, "Dbmate", Refusing)
    assert fast_forward.main(["--months", "1"]) == 1
    assert "dbmate up failed:\na migration that failed" in capsys.readouterr().out


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
