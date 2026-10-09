"""An approved reconciliation is locked: the record can't change, and its trust bank account is
closed through the period's end, so no transfer dated inside the period can be posted.

Migration 20261009000006. A late correction is dated in the next open period.
"""

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import approve, make_pmc, post, snapshot, transfer_batch

JAN = datetime(2026, 1, 1, tzinfo=UTC)
JAN_15 = datetime(2026, 1, 15, tzinfo=UTC)
FEB = datetime(2026, 2, 1, tzinfo=UTC)
MAR = datetime(2026, 3, 1, tzinfo=UTC)
TICK = timedelta(microseconds=1)  # timestamptz's resolution
RENT = Decimal("1500.00")
CLOSED = "reconciled and closed"


def reconciliations(conn, pmc):
    return conn.execute(
        "SELECT period_start, period_end FROM trust_reconciliations WHERE pmc_id = %s"
        " ORDER BY period_end",
        (pmc.pmc_id,),
    ).fetchall()


def closed_through(conn, bank_id):
    return conn.execute(
        "SELECT closed_through FROM trust_bank_accounts WHERE id = %s", (bank_id,)
    ).fetchone()[0]


def keys_filed(conn, pmc):
    return conn.execute(
        "SELECT idempotency_key FROM trust_idempotency_keys WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchall()


def test_approval_records_the_book_balance_at_the_period_end(conn):
    pmc = make_pmc(conn)
    owner = pmc.owners[0].account
    for when, requests in [
        (datetime(2026, 1, 10, tzinfo=UTC), [(pmc.operating_cash, owner, RENT)]),
        (datetime(2026, 1, 20, tzinfo=UTC), [(owner, pmc.pmc_income, "120.00")]),  # no cash
        (datetime(2026, 1, 28, tzinfo=UTC), [(owner, pmc.operating_cash, "310.40")]),  # payout
        (datetime(2026, 1, 25, tzinfo=UTC), [(pmc.deposit_cash, pmc.tenant_deposit, "900.00")]),
        (FEB, [(pmc.operating_cash, owner, "980.25")]),  # the next period's first instant
    ]:
        transfer_batch(conn, requests, event_at=when)

    approved = approve(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        JAN,
        FEB,
        statement_balance="1189.60",
        prepared_by="Preparer 7",
        approved_by="Approver 3",
    )

    row = conn.execute(
        "SELECT pmc_id, bank_account_id, period_start, period_end, statement_balance,"
        " book_balance, prepared_by, approved_by, approved_at <= now()"
        " FROM trust_reconciliations WHERE id = %s",
        (approved,),
    ).fetchone()
    assert row == (
        pmc.pmc_id,
        pmc.operating_bank_id,
        JAN,
        FEB,
        Decimal("1189.60"),
        RENT - Decimal("310.40"),  # in January only, and only the operating account's cash
        "Preparer 7",
        "Approver 3",
        True,
    )
    assert closed_through(conn, pmc.operating_bank_id) == FEB
    assert closed_through(conn, pmc.deposit_bank_id) is None


def test_an_account_with_no_postings_has_a_zero_book_balance(conn):
    pmc = make_pmc(conn)
    approved = approve(conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB)

    assert conn.execute(
        "SELECT book_balance FROM trust_reconciliations WHERE id = %s", (approved,)
    ).fetchone() == (0,)


@pytest.mark.parametrize("role", ["owner", "app"])
@pytest.mark.parametrize("when", [JAN_15, FEB - TICK, JAN - timedelta(days=400)])
def test_a_posting_dated_inside_a_closed_period_is_refused(conn, app_conn, role, when):
    # Closing through February 1 closes everything before it, not just January.
    pmc = make_pmc(conn)
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB)
    before = snapshot(conn, pmc.accounts())
    late_rent = [(pmc.operating_cash, pmc.owners[0].account, RENT)]

    with pytest.raises(psycopg.errors.CheckViolation, match=CLOSED):
        if role == "owner":
            transfer_batch(conn, late_rent, event_at=when)
        else:
            post(app_conn, pmc.pmc_id, "late-rent", late_rent, event_at=when)

    assert snapshot(conn, pmc.accounts()) == before
    assert keys_filed(conn, pmc) == []


@pytest.mark.parametrize("when", [FEB, FEB + TICK, None])  # None: dated now
def test_a_posting_dated_at_or_after_the_period_end_is_accepted(conn, app_conn, when):
    pmc = make_pmc(conn)
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB)

    posted = post(
        app_conn, pmc.pmc_id, "rent", [(pmc.operating_cash, pmc.owners[0].account, RENT)], when
    )

    assert len(posted) == 1


def test_closing_one_trust_bank_account_leaves_the_other_open(conn):
    pmc = make_pmc(conn)
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB)

    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "900.00")], event_at=JAN_15)

    assert closed_through(conn, pmc.deposit_bank_id) is None


@pytest.mark.parametrize("closed_side", ["from", "to"])
def test_a_transfer_is_refused_when_either_side_is_closed(conn, closed_side):
    # Moving cash between the two trust bank accounts, dated in a period only one has closed.
    pmc = make_pmc(conn)
    transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, "900.00")], event_at=JAN)
    approve(conn, pmc.pmc_id, pmc.deposit_bank_id, JAN, FEB)
    move = (
        (pmc.deposit_cash, pmc.operating_cash)
        if closed_side == "from"
        else (pmc.operating_cash, pmc.deposit_cash)
    )
    before = snapshot(conn, pmc.accounts())

    with pytest.raises(psycopg.errors.CheckViolation, match=CLOSED):
        transfer_batch(conn, [(*move, "10.00")], event_at=JAN_15)

    assert snapshot(conn, pmc.accounts()) == before


def test_a_batch_with_one_transfer_into_a_closed_account_is_refused_whole(conn):
    pmc = make_pmc(conn)
    owner = pmc.owners[0].account
    approve(conn, pmc.pmc_id, pmc.deposit_bank_id, JAN, FEB)
    before = snapshot(conn, pmc.accounts())

    with pytest.raises(psycopg.errors.CheckViolation, match=CLOSED):
        transfer_batch(
            conn,
            [(pmc.operating_cash, owner, RENT), (pmc.deposit_cash, pmc.tenant_deposit, RENT)],
            event_at=JAN_15,
        )

    assert snapshot(conn, pmc.accounts()) == before


def test_a_retried_key_returns_its_original_after_the_period_closes(conn, app_conn):
    # A retry posts nothing new, so the closed period doesn't stop it.
    pmc = make_pmc(conn)
    rent = [(pmc.operating_cash, pmc.owners[0].account, RENT)]
    first = post(app_conn, pmc.pmc_id, "rent-jan", rent, event_at=JAN_15)
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB)
    before = snapshot(conn, pmc.accounts())

    assert post(app_conn, pmc.pmc_id, "rent-jan", rent, event_at=JAN_15) == first
    assert snapshot(conn, pmc.accounts()) == before


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (JAN_15, MAR),  # overlaps January
        (FEB + TICK, MAR),  # leaves a gap
        (JAN, MAR),  # covers January again
    ],
)
def test_periods_follow_one_another_with_no_gap_or_overlap(conn, start, end):
    pmc = make_pmc(conn)
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="next period .* starts at"):
        approve(conn, pmc.pmc_id, pmc.operating_bank_id, start, end)

    assert reconciliations(conn, pmc) == [(JAN, FEB)]
    assert closed_through(conn, pmc.operating_bank_id) == FEB
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, FEB, MAR)
    assert reconciliations(conn, pmc) == [(JAN, FEB), (FEB, MAR)]
    assert closed_through(conn, pmc.operating_bank_id) == MAR


def test_a_period_is_approved_only_once_it_has_ended(database_url, conn):
    pmc, other = make_pmc(conn), make_pmc(conn)
    approval = "SELECT trust_approve_reconciliation(%s, %s, %s, {end}, 0, 'P', 'A')"
    with psycopg.connect(database_url) as tx:  # now() is the same instant all transaction
        with pytest.raises(psycopg.errors.InvalidParameterValue, match="has not come yet"):
            tx.execute(
                approval.format(end="now() + interval '1 microsecond'"),
                (pmc.pmc_id, pmc.operating_bank_id, JAN),
            )
        tx.rollback()
        tx.execute(approval.format(end="now()"), (other.pmc_id, other.operating_bank_id, JAN))
        tx.commit()

    assert reconciliations(conn, pmc) == []
    assert len(reconciliations(conn, other)) == 1


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_approval_runs_only_at_read_committed(database_url, conn, isolation):
    # Above READ COMMITTED, the book balance would come from a snapshot that misses postings
    # committed while the approval waited for its lock.
    pmc = make_pmc(conn)
    with psycopg.connect(database_url) as tx:
        tx.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
        with pytest.raises(psycopg.errors.InvalidTransactionState, match="READ COMMITTED"):
            approve(tx, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB)
        tx.rollback()

    assert reconciliations(conn, pmc) == []


@pytest.mark.parametrize("bank", ["unknown", "another PMC's"])
def test_approval_needs_a_trust_bank_account_of_the_pmc(conn, bank):
    pmc, other = make_pmc(conn), make_pmc(conn)
    bank_id = uuid.uuid4() if bank == "unknown" else other.operating_bank_id

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no trust bank account"):
        approve(conn, pmc.pmc_id, bank_id, JAN, FEB)

    assert reconciliations(conn, pmc) == reconciliations(conn, other) == []
    assert closed_through(conn, other.operating_bank_id) is None


@pytest.mark.parametrize(
    ("change", "constraint"),
    [
        ({"period_end": JAN}, "trust_reconciliations_check"),
        ({"period_end": JAN - TICK}, "trust_reconciliations_check"),
        ({"prepared_by": ""}, "prepared_by_check"),
        ({"approved_by": ""}, "approved_by_check"),
    ],
)
def test_approval_needs_a_period_and_both_names(conn, change, constraint):
    pmc = make_pmc(conn)
    request = {"period_start": JAN, "period_end": FEB, **change}

    with pytest.raises(psycopg.errors.CheckViolation, match=constraint):
        approve(conn, pmc.pmc_id, pmc.operating_bank_id, **request)

    assert reconciliations(conn, pmc) == []
    assert closed_through(conn, pmc.operating_bank_id) is None


def test_the_app_approves_through_the_function_and_cannot_reopen_a_period(conn, app_conn):
    pmc = make_pmc(conn)
    approve(app_conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB)

    for query in [
        "UPDATE trust_bank_accounts SET closed_through = NULL WHERE id = %(bank)s",
        "UPDATE trust_reconciliations SET period_end = %(jan)s WHERE bank_account_id = %(bank)s",
        "INSERT INTO trust_reconciliations (pmc_id, bank_account_id, period_start, period_end,"
        " statement_balance, book_balance, prepared_by, approved_by)"
        " VALUES (%(pmc)s, %(bank)s, %(feb)s, %(mar)s, 0, 0, 'P', 'A')",
    ]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app_conn.execute(
                query,
                {
                    "bank": pmc.operating_bank_id,
                    "pmc": pmc.pmc_id,
                    "jan": JAN,
                    "feb": FEB,
                    "mar": MAR,
                },
            )

    assert reconciliations(conn, pmc) == [(JAN, FEB)]
    assert closed_through(conn, pmc.operating_bank_id) == FEB


@pytest.mark.parametrize("value", [None, JAN, MAR])
def test_no_role_moves_the_closing_date_by_hand(conn, value):
    # Even the owner: reopening January, or closing February unreconciled, takes a
    # reconciliation, and those are append-only.
    pmc = make_pmc(conn)
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB)

    with pytest.raises(psycopg.errors.RestrictViolation, match="newest approved"):
        conn.execute(
            "UPDATE trust_bank_accounts SET closed_through = %s WHERE id = %s",
            (value, pmc.operating_bank_id),
        )

    assert closed_through(conn, pmc.operating_bank_id) == FEB


def test_a_new_trust_bank_account_starts_open(conn, app_conn):
    pmc = make_pmc(conn)

    with pytest.raises(psycopg.errors.RestrictViolation, match="newest approved"):
        app_conn.execute(
            "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name, closed_through)"
            " VALUES (%s, 'operating', 'Synthetic trust account', %s)",
            (pmc.pmc_id, MAR),
        )


def test_a_closed_trust_bank_account_can_still_be_renamed(conn, app_conn):
    pmc = make_pmc(conn)
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB)

    app_conn.execute(
        "UPDATE trust_bank_accounts SET display_name = 'Renamed trust account' WHERE id = %s",
        (pmc.operating_bank_id,),
    )

    assert closed_through(conn, pmc.operating_bank_id) == FEB


# --- approval and posting at the same time ----------------------------------------------------


def wait_until_blocked(conn, pid):
    for _ in range(1000):
        row = conn.execute(
            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", (pid,)
        ).fetchone()
        if row and row[0] == "Lock":
            return
        time.sleep(0.01)
    raise AssertionError("the second transaction never waited for the first")


def in_background(database_url, work):
    """Run work(connection) on a new autocommit connection in a thread; return the thread,
    the connection's backend pid and a dict that receives the result or the error."""
    outcome: dict = {}
    ready = threading.Event()
    pid: list[int] = []

    def run():
        with psycopg.connect(database_url, autocommit=True) as worker:
            worker.execute("SET lock_timeout = '20s'")
            pid.append(worker.execute("SELECT pg_backend_pid()").fetchone()[0])
            ready.set()
            try:
                outcome["result"] = work(worker)
            except Exception as exc:  # asserted by the caller
                outcome["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    assert ready.wait(timeout=30)
    return thread, pid[0], outcome


@pytest.mark.timing
def test_a_posting_waits_for_an_approval_in_progress_and_is_then_refused(conn, database_url):
    pmc = make_pmc(conn)
    late_rent = [(pmc.operating_cash, pmc.owners[0].account, RENT)]
    before = snapshot(conn, pmc.accounts())

    with psycopg.connect(database_url) as approval:
        approve(approval, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB)  # not committed yet
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: post(worker, pmc.pmc_id, "late", late_rent, event_at=JAN_15),
        )
        wait_until_blocked(conn, pid)
        approval.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert CLOSED in str(outcome["error"])
    assert snapshot(conn, pmc.accounts()) == before


@pytest.mark.timing
def test_an_approval_waits_for_a_posting_in_flight_and_counts_it(conn, database_url):
    pmc = make_pmc(conn)
    rent = [(pmc.operating_cash, pmc.owners[0].account, RENT)]

    with psycopg.connect(database_url) as posting:
        transfer_batch(posting, rent, event_at=JAN_15)  # not committed yet
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: approve(worker, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB),
        )
        wait_until_blocked(conn, pid)
        posting.commit()
    thread.join(timeout=30)

    assert "error" not in outcome, outcome
    book = conn.execute(
        "SELECT book_balance FROM trust_reconciliations WHERE id = %s", (outcome["result"],)
    ).fetchone()[0]
    assert book == RENT


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_a_posting_from_an_older_snapshot_cannot_slip_into_a_period_closed_since(
    conn, database_url, isolation
):
    # The posting's snapshot predates the approval, so it can't see the new closing date; the
    # lock on the trust bank account fails it instead of letting it through.
    pmc = make_pmc(conn)
    before = snapshot(conn, pmc.accounts())

    with psycopg.connect(database_url) as posting:
        posting.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
        posting.execute("SELECT 1")  # the snapshot is taken here
        approve(conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB)
        with pytest.raises(psycopg.errors.SerializationFailure):
            transfer_batch(
                posting, [(pmc.operating_cash, pmc.owners[0].account, RENT)], event_at=JAN_15
            )
        posting.rollback()

    assert snapshot(conn, pmc.accounts()) == before
