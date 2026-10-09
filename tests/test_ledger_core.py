"""The pgledger invariants the trust ledger depends on, and the append-only guards on top."""

import random
import threading
import time
from decimal import Decimal

import psycopg
import pytest
from helpers import balance, make_pmc, running_balance_breaks, transfer, transfer_batch

LEDGER_HISTORY = [
    "pgledger_transfers",
    "pgledger_entries",
    "trust_ledger_accounts",
    "trust_idempotency_keys",
]
REWRITES = {
    "UPDATE": "UPDATE {table} SET created_at = created_at",
    "DELETE": "DELETE FROM {table}",
    "TRUNCATE": "TRUNCATE {table} CASCADE",
}


def post_sample_transfers(conn):
    """Rent in, a management fee, a vendor bill paid, a deposit in, and one batch."""
    pmc = make_pmc(conn, owners=2)
    first, second = pmc.owners
    transfer(conn, pmc.operating_cash, first.account, "1500.00")
    transfer(conn, pmc.operating_cash, second.account, "980.25")
    transfer(conn, first.account, pmc.pmc_income, "120.00")
    transfer(conn, second.account, pmc.operating_cash, "310.40")
    transfer(conn, pmc.deposit_cash, pmc.tenant_deposit, "2000.00")
    transfer_batch(
        conn,
        [
            (first.account, second.account, "45.10"),
            (second.account, pmc.pmc_income, "78.42"),
            (pmc.pmc_income, pmc.operating_cash, "100.00"),
        ],
    )
    return pmc


def transfers_whose_entries_do_not_balance(conn):
    """Transfers without exactly one -amount entry on the from account and one +amount entry on
    the to account, summing to zero."""
    return conn.execute(
        """
        SELECT t.id
        FROM pgledger_transfers t
        LEFT JOIN pgledger_entries e ON e.transfer_id = t.id
        GROUP BY t.id, t.from_account_id, t.to_account_id, t.amount
        HAVING count(e.id) <> 2
            OR coalesce(sum(e.amount), 1) <> 0
            OR count(*) FILTER (
                WHERE e.account_id = t.from_account_id AND e.amount = -t.amount) <> 1
            OR count(*) FILTER (
                WHERE e.account_id = t.to_account_id AND e.amount = t.amount) <> 1
        """
    ).fetchall()


def accounts_whose_balance_is_not_the_sum_of_entries(conn):
    return conn.execute(
        """
        SELECT a.id, a.balance, coalesce(sum(e.amount), 0)
        FROM pgledger_accounts a
        LEFT JOIN pgledger_entries e ON e.account_id = a.id
        GROUP BY a.id, a.balance
        HAVING a.balance <> coalesce(sum(e.amount), 0)
        """
    ).fetchall()


def test_every_transfer_writes_entries_that_sum_to_zero(conn):
    post_sample_transfers(conn)

    assert conn.execute("SELECT count(*) FROM pgledger_transfers").fetchone()[0] > 0
    assert transfers_whose_entries_do_not_balance(conn) == []


def test_every_account_balance_equals_the_sum_of_its_entries(conn):
    pmc = post_sample_transfers(conn)

    assert accounts_whose_balance_is_not_the_sum_of_entries(conn) == []
    # Every transfer moves the same amount out and in, so all balances together net to zero.
    assert conn.execute("SELECT sum(balance) FROM pgledger_accounts").fetchone()[0] == 0
    # Spot-check one account by hand: 1500.00 rent in, 120.00 fee out, 45.10 to the other owner.
    assert balance(conn, pmc.owners[0].account) == Decimal("1334.90")


def test_every_entry_carries_the_running_balance_and_account_version(conn):
    post_sample_transfers(conn)
    accounts = [row[0] for row in conn.execute("SELECT id FROM pgledger_accounts")]

    assert running_balance_breaks(conn, accounts) == []


@pytest.mark.parametrize("statement", REWRITES)
@pytest.mark.parametrize("table", LEDGER_HISTORY)
def test_ledger_history_cannot_be_rewritten_even_by_the_owner(conn, table, statement):
    post_sample_transfers(conn)
    before = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
        conn.execute(REWRITES[statement].format(table=table))

    assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == before


@pytest.mark.parametrize("statement", REWRITES)
@pytest.mark.parametrize("table", LEDGER_HISTORY)
def test_app_role_has_no_grant_to_rewrite_ledger_history(conn, app_conn, table, statement):
    post_sample_transfers(conn)
    before = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    # Refused by missing privilege, before the trigger is even reached.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute(REWRITES[statement].format(table=table))

    assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == before


@pytest.mark.timing
def test_concurrent_transfers_all_finish_and_balances_add_up(conn, database_url):
    threads, rounds = 8, 40
    pmc = make_pmc(conn, owners=4)
    accounts = [owner.account for owner in pmc.owners]
    for account in accounts:
        transfer(conn, pmc.operating_cash, account, "1000000.00")

    barrier = threading.Barrier(threads)
    errors = []
    posted = []

    def worker(seed):
        rng = random.Random(seed)
        try:
            with psycopg.connect(database_url, autocommit=True) as worker_conn:
                worker_conn.execute("SET ROLE trust_app")
                worker_conn.execute("SET lock_timeout = '20s'")
                barrier.wait(timeout=30)
                for _ in range(rounds):
                    a, b, c = rng.sample(accounts, 3)
                    # A cycle a -> b -> c -> a in one batch: without consistent lock ordering,
                    # two workers holding opposite ends of it deadlock.
                    batch = [
                        (a, b, Decimal(rng.randint(1, 1000)) / 100),
                        (b, c, Decimal(rng.randint(1, 1000)) / 100),
                        (c, a, Decimal(rng.randint(1, 1000)) / 100),
                    ]
                    posted.extend(transfer_batch(worker_conn, batch))
        except Exception as exc:  # collected and asserted below
            errors.append(repr(exc))

    workers = [threading.Thread(target=worker, args=(seed,)) for seed in range(threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join(timeout=120)

    assert not any(t.is_alive() for t in workers), "a worker never finished"
    assert errors == []
    assert len(posted) == len(set(posted)) == threads * rounds * 3

    # Money only moved between the four owners, so their total is unchanged.
    total = conn.execute(
        "SELECT sum(balance) FROM pgledger_accounts WHERE id = ANY(%s)", (accounts,)
    ).fetchone()[0]
    assert total == Decimal("4000000.00")
    assert balance(conn, pmc.operating_cash) == Decimal("-4000000.00")
    assert transfers_whose_entries_do_not_balance(conn) == []
    assert accounts_whose_balance_is_not_the_sum_of_entries(conn) == []


@pytest.mark.timing
def test_transfers_on_unrelated_accounts_do_not_wait_for_each_other(conn, database_url):
    first, second = make_pmc(conn), make_pmc(conn)
    with psycopg.connect(database_url) as holder:
        # An open transaction holds the row locks on the first PMC's two accounts...
        transfer(holder, first.operating_cash, first.owners[0].account, "1.00")
        with psycopg.connect(database_url, autocommit=True) as other:
            # ...and a transfer between two other accounts must not wait for it.
            other.execute("SET lock_timeout = '2s'")
            transfer(other, second.operating_cash, second.owners[0].account, "1.00")
        holder.rollback()

    assert balance(conn, second.owners[0].account) == 1
    assert balance(conn, first.owners[0].account) == 0


def accounts_locked_by_a_blocked_batch(conn, database_url, accounts, source, held):
    """Hold `held`'s row lock, start a batch from `source` that touches every account, and
    return the accounts the batch had already locked when it stopped to wait for `held`."""
    batch = [(source, target, "1.00") for target in reversed(accounts) if target != source]
    errors = []

    def post(worker):
        try:
            transfer_batch(worker, batch)
        except Exception as exc:  # reported below
            errors.append(repr(exc))

    with (
        psycopg.connect(database_url) as holder,
        psycopg.connect(database_url, autocommit=True) as worker,
    ):
        holder.execute("SELECT 1 FROM pgledger_accounts WHERE id = %s FOR UPDATE", (held,))
        worker_pid = worker.execute("SELECT pg_backend_pid()").fetchone()[0]
        thread = threading.Thread(target=post, args=(worker,))
        thread.start()
        try:
            for _ in range(500):
                waiting = conn.execute(
                    "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", (worker_pid,)
                ).fetchone()
                if waiting and waiting[0] == "Lock":
                    break
                time.sleep(0.01)
            else:
                raise AssertionError("the batch never waited for the held account")
            locked = set()
            for account in accounts:
                if account == held:
                    continue
                try:
                    conn.execute(
                        "SELECT 1 FROM pgledger_accounts WHERE id = %s FOR UPDATE NOWAIT",
                        (account,),
                    )
                except psycopg.errors.LockNotAvailable:
                    locked.add(account)
        finally:
            holder.rollback()
            thread.join(timeout=30)
    assert errors == []
    return locked


@pytest.mark.timing
def test_batches_lock_accounts_in_sorted_order_before_changing_anything(conn, database_url):
    # pgledger avoids deadlocks by locking every account a batch touches in one global order
    # (sorted ids) before changing any balance. The concurrency test above only catches a
    # broken order when a deadlock happens to occur; this checks the order directly.
    pmc = make_pmc(conn, owners=8)
    accounts = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM unnest(%s::text[]) AS id ORDER BY id",
            (
                [
                    pmc.operating_cash,
                    pmc.deposit_cash,
                    pmc.pmc_income,
                    pmc.tenant_deposit,
                    *(owner.account for owner in pmc.owners),
                ],
            ),
        )
    ]
    for held in (accounts[-1], accounts[0], accounts[len(accounts) // 2]):
        expected = set(accounts[: accounts.index(held)])
        locked = accounts_locked_by_a_blocked_batch(
            conn, database_url, accounts, pmc.operating_cash, held
        )
        assert locked == expected
