"""Idempotency keys: a posting retried with the same key posts once (trust_post_transfers)."""

import threading
import time
from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import balance, make_pmc, post, snapshot, transfer

RENT = Decimal("1500.00")


def count_transfers(conn):
    return conn.execute("SELECT count(*) FROM pgledger_transfers").fetchone()[0]


def transfers_into(conn, account):
    return conn.execute(
        "SELECT count(*) FROM pgledger_transfers WHERE to_account_id = %s", (account,)
    ).fetchone()[0]


def test_same_key_twice_posts_once_and_returns_the_original(conn, app_conn):
    pmc = make_pmc(conn)
    rent = [(pmc.operating_cash, pmc.owners[0].account, RENT)]
    first = post(app_conn, pmc.pmc_id, "rent-2026-01", rent)
    before = count_transfers(conn)

    again = post(app_conn, pmc.pmc_id, "rent-2026-01", rent)

    assert again == first
    assert count_transfers(conn) == before
    assert balance(conn, pmc.owners[0].account) == RENT


def test_a_retried_batch_comes_back_in_the_order_it_was_posted(conn, app_conn):
    pmc = make_pmc(conn, owners=3)
    batch = [
        (pmc.operating_cash, owner.account, Decimal(100 + n)) for n, owner in enumerate(pmc.owners)
    ]
    first = post(app_conn, pmc.pmc_id, "rent-batch", batch)

    assert post(app_conn, pmc.pmc_id, "rent-batch", batch) == first
    assert len(set(first)) == 3


@pytest.mark.parametrize(
    "change",
    ["amount", "target", "event_at", "metadata", "one more transfer"],
)
def test_same_key_with_different_contents_is_refused_and_nothing_written(conn, app_conn, change):
    pmc = make_pmc(conn, owners=2)
    first, second = pmc.owners
    when = datetime(2026, 1, 1, tzinfo=UTC)
    original = [(pmc.operating_cash, first.account, RENT)]
    post(app_conn, pmc.pmc_id, "rent", original, event_at=when, metadata={"memo": "January"})
    retry = {
        "requests": original,
        "event_at": when,
        "metadata": {"memo": "January"},
    }
    if change == "amount":
        retry["requests"] = [(pmc.operating_cash, first.account, RENT + 1)]
    elif change == "target":
        retry["requests"] = [(pmc.operating_cash, second.account, RENT)]
    elif change == "event_at":
        retry["event_at"] = datetime(2026, 1, 2, tzinfo=UTC)
    elif change == "metadata":
        retry["metadata"] = {"memo": "February"}
    else:
        retry["requests"] = [*original, (pmc.operating_cash, second.account, RENT)]
    accounts = [pmc.operating_cash, first.account, second.account]
    before = snapshot(conn, accounts)

    with pytest.raises(psycopg.errors.UniqueViolation, match="already used for a different"):
        post(app_conn, pmc.pmc_id, "rent", **retry)

    assert snapshot(conn, accounts) == before


def test_the_same_posting_written_differently_is_still_a_retry(conn, app_conn):
    pmc = make_pmc(conn)
    when = datetime(2026, 1, 1, tzinfo=UTC)
    first = post(
        app_conn,
        pmc.pmc_id,
        "rent",
        [(pmc.operating_cash, pmc.owners[0].account, Decimal("1500"))],
        event_at=when,
    )
    # Same amount with more decimals, and the same instant from a session in another time zone.
    app_conn.execute("SET TIME ZONE 'America/Los_Angeles'")

    again = post(
        app_conn,
        pmc.pmc_id,
        "rent",
        [(pmc.operating_cash, pmc.owners[0].account, Decimal("1500.00"))],
        event_at=when,
    )

    assert again == first


def test_a_failed_posting_does_not_use_up_its_key(conn, app_conn):
    pmc = make_pmc(conn)
    owner = pmc.owners[0].account
    payout = [(owner, pmc.operating_cash, Decimal("50.00"))]

    with pytest.raises(psycopg.errors.CheckViolation, match="below zero"):
        post(app_conn, pmc.pmc_id, "payout", payout)
    transfer(conn, pmc.operating_cash, owner, "50.00")
    posted = post(app_conn, pmc.pmc_id, "payout", payout)

    assert len(posted) == 1
    assert balance(conn, owner) == 0


def test_keys_belong_to_one_pmc(conn, app_conn):
    first, second = make_pmc(conn), make_pmc(conn)

    one = post(
        app_conn, first.pmc_id, "rent", [(first.operating_cash, first.owners[0].account, RENT)]
    )
    two = post(
        app_conn, second.pmc_id, "rent", [(second.operating_cash, second.owners[0].account, RENT)]
    )

    assert one != two
    assert balance(conn, second.owners[0].account) == RENT


@pytest.mark.parametrize("account", ["other PMC", "no trust kind"])
def test_a_posting_must_stay_in_the_pmc_its_key_is_filed_under(conn, app_conn, account):
    pmc, other = make_pmc(conn), make_pmc(conn)
    source = (
        other.operating_cash
        if account == "other PMC"
        else conn.execute("SELECT id FROM pgledger_create_account('stray', 'USD')").fetchone()[0]
    )
    before = count_transfers(conn)

    with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="not in PMC"):
        post(app_conn, pmc.pmc_id, "rent", [(source, pmc.owners[0].account, RENT)])

    assert count_transfers(conn) == before


@pytest.mark.parametrize("transfers", ["ARRAY[]::transfer_request[]", "NULL"])
def test_a_posting_needs_at_least_one_transfer(conn, app_conn, transfers):
    pmc = make_pmc(conn)
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="at least one transfer"):
        app_conn.execute(f"SELECT trust_post_transfers(%s, 'k', {transfers})", (pmc.pmc_id,))


@pytest.mark.parametrize(
    ("key", "error"),
    [
        ("", psycopg.errors.CheckViolation),
        ("k" * 201, psycopg.errors.CheckViolation),
        (None, psycopg.errors.NotNullViolation),
    ],
)
def test_a_key_must_be_present_and_short(conn, app_conn, key, error):
    pmc = make_pmc(conn)
    before = count_transfers(conn)

    with pytest.raises(error):
        post(app_conn, pmc.pmc_id, key, [(pmc.operating_cash, pmc.owners[0].account, RENT)])

    assert count_transfers(conn) == before


def wait_until_blocked(conn, pid):
    for _ in range(500):
        row = conn.execute(
            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", (pid,)
        ).fetchone()
        if row and row[0] == "Lock":
            return
        time.sleep(0.01)
    raise AssertionError("the second call never waited for the first")


@pytest.mark.timing
@pytest.mark.parametrize("first_call", ["commits", "rolls back"])
def test_two_calls_with_one_key_at_once_post_once(conn, database_url, first_call):
    pmc = make_pmc(conn)
    rent = [(pmc.operating_cash, pmc.owners[0].account, RENT)]
    results, errors = [], []

    def second_call(worker):
        try:
            results.append(post(worker, pmc.pmc_id, "rent", rent))
        except Exception as exc:  # reported below
            errors.append(repr(exc))

    with (
        psycopg.connect(database_url) as first,
        psycopg.connect(database_url, autocommit=True) as worker,
    ):
        worker.execute("SET ROLE trust_app")
        posted = post(first, pmc.pmc_id, "rent", rent)  # in an open transaction
        thread = threading.Thread(target=second_call, args=(worker,))
        thread.start()
        try:
            wait_until_blocked(conn, worker.info.backend_pid)
        finally:
            first.commit() if first_call == "commits" else first.rollback()
            thread.join(timeout=30)

    assert errors == []
    if first_call == "commits":
        assert results == [posted]
    else:
        assert results[0] != posted  # the first never happened, so the second really posted
    # Either way, exactly one rent payment reached the owner.
    assert transfers_into(conn, pmc.owners[0].account) == 1
    assert balance(conn, pmc.owners[0].account) == RENT
