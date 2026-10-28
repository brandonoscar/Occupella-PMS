"""Faults: the work cut off at its worst moment, as a server that died or a network that dropped
would cut it (the founder, 2026-10-25: tests an enterprise could trust).

Each test does a piece of the PMS's work in a transaction on a connection of its own, as the app,
and once the work is done and before it commits, kills that connection from outside
(pg_terminate_backend), as Postgres sees a crashed client. Then: nothing of the work stands (the
transaction was all or nothing), and the work done again, as the page or the next night does it,
lands once, exactly as if nothing had failed. A payment and Pay owners sent again carry the
request id the page made the first time, so the retry is the same request.

Timing: each waits for the other connection to reach its sleep, so they run 50 times nightly.
"""

import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg import sql

from pms import demo, nightly
from pms.api import payables, rent, switching

URL = "http://testserver"


def as_app(database_url: str) -> psycopg.Connection[Any]:
    conn = psycopg.connect(database_url, autocommit=True)
    conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier("trust_app")))
    return conn


def cut_off(database_url: str, admin: psycopg.Connection[Any], work: Callable[[Any], None]) -> None:
    """`work` in a transaction on a connection of its own; killed once it has done everything
    and before it commits. Returns once that connection is gone."""
    victim = as_app(database_url)
    pid = victim.info.backend_pid
    done, died = threading.Event(), []

    def run() -> None:
        try:
            with victim.transaction():
                work(victim)
                done.set()
                victim.execute("SELECT pg_sleep(60)")
        except psycopg.OperationalError as gone:
            died.append(gone)
        finally:
            done.set()

    thread = threading.Thread(target=run)
    thread.start()
    assert done.wait(60), "the work never finished"
    # Wait for the sleep: the work is done and the transaction not committed.
    for _ in range(600):
        state = admin.execute(
            "SELECT query FROM pg_stat_activity WHERE pid = %s", (pid,)
        ).fetchone()
        if state and "pg_sleep" in state[0]:
            break
        threading.Event().wait(0.05)
    admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
    thread.join(60)
    assert died, "the connection outlived its kill"
    victim.close()


def count(conn: psycopg.Connection[Any], query: str, *params: Any) -> int:
    row = conn.execute(query, params).fetchone()
    assert row is not None
    return int(row[0])


def transfers_of(conn: psycopg.Connection[Any], pmc: uuid.UUID) -> int:
    return count(
        conn,
        "SELECT count(*) FROM pgledger_transfers AS t JOIN trust_ledger_accounts AS a"
        " ON a.ledger_account_id = t.from_account_id WHERE a.pmc_id = %s",
        pmc,
    )


def owing_lease(conn: psycopg.Connection[Any], built: demo.Demo) -> uuid.UUID:
    """A lease that owes: this month's rent charged first, as tonight's run charges it."""
    nightly.run_pmc(conn, built.pmc_id, datetime.now(UTC).date(), URL)
    for lease in sorted(built.leases.values()):
        if rent.owed(conn, lease) > 0:
            return lease
    raise AssertionError("the demo has no lease that owes")


@pytest.mark.timing
def test_a_payment_cut_off_before_it_commits_leaves_nothing_and_its_retry_lands_once(
    database_url, conn, app_conn
):
    built = demo.books(app_conn)
    day = datetime.now(UTC).date()
    lease = owing_lease(app_conn, built)
    owed = rent.owed(app_conn, lease)
    payment = demo.payment(app_conn, lease, day)
    before = transfers_of(conn, built.pmc_id)

    def pay(victim: Any) -> None:
        with demo.Builder(victim, built.pmc_id).as_staff(built.manager) as s:
            rent.pay(s, victim, lease, payment)

    cut_off(database_url, conn, pay)
    assert (transfers_of(conn, built.pmc_id), rent.owed(app_conn, lease)) == (before, owed)

    # The page sends the same request again; then again, as a retry after a lost answer would.
    with demo.Builder(app_conn, built.pmc_id).as_staff(built.manager) as s:
        first = rent.pay(s, app_conn, lease, payment)
    with demo.Builder(app_conn, built.pmc_id).as_staff(built.manager) as s:
        again = rent.pay(s, app_conn, lease, payment)
    assert first.transfers == again.transfers
    assert rent.owed(app_conn, lease) == Decimal(0)
    assert transfers_of(conn, built.pmc_id) == before + len(first.transfers)


@pytest.mark.timing
def test_a_nightly_run_cut_off_midway_leaves_the_night_undone_and_the_next_run_does_it_all_once(
    database_url, conn, app_conn
):
    # Two companies built alike: one night runs whole on the first, and is cut off on the second
    # and run again. They must end the same.
    whole, cut = demo.books(app_conn), demo.books(app_conn)
    day = datetime.now(UTC).date()
    nightly.run_pmc(app_conn, whole.pmc_id, day, URL)

    def charges(pmc: uuid.UUID) -> list[tuple[str, int, Decimal]]:
        return [
            (row[0], row[1], row[2])
            for row in conn.execute(
                "SELECT kind, count(*), sum(amount) FROM trust_charges WHERE pmc_id = %s"
                " GROUP BY kind ORDER BY kind",
                (pmc,),
            )
        ]

    untouched = charges(cut.pmc_id)
    before = transfers_of(conn, cut.pmc_id)
    runs = count(conn, "SELECT count(*) FROM trust_rent_runs WHERE pmc_id = %s", cut.pmc_id)
    cut_off(database_url, conn, lambda victim: nightly.run_pmc(victim, cut.pmc_id, day, URL))
    assert charges(cut.pmc_id) == untouched
    assert transfers_of(conn, cut.pmc_id) == before
    assert count(conn, "SELECT count(*) FROM trust_rent_runs WHERE pmc_id = %s", cut.pmc_id) == runs

    nightly.run_pmc(app_conn, cut.pmc_id, day, URL)
    nightly.run_pmc(app_conn, cut.pmc_id, day, URL)  # and once more: it charges nothing new
    assert charges(cut.pmc_id) == charges(whole.pmc_id)


@pytest.mark.timing
def test_pay_owners_cut_off_pays_no_one_and_its_retry_pays_each_once(database_url, conn, app_conn):
    built = demo.books(app_conn)
    day = datetime.now(UTC).date()
    # A tenant pays this month's rent, so the owner has money to pay out.
    lease = owing_lease(app_conn, built)
    with demo.Builder(app_conn, built.pmc_id).as_staff(built.manager) as s:
        rent.pay(s, app_conn, lease, demo.payment(app_conn, lease, day))
    # The accounts with money a draw may take, as the owners page shows them: Pay owners is all
    # or none, so an account with nothing available would refuse the whole run.
    accounts = sorted(
        m.account for m in payables.owner_money(app_conn, built.pmc_id) if Decimal(m.available) > 0
    )
    run = payables.PayOwners(
        request_id=uuid.uuid4(),
        on=day,
        method="EFT",
        payouts=[payables.Payout(account=a) for a in accounts],
    )

    def balances() -> list[Decimal]:
        return [
            Decimal(
                count(
                    conn, "SELECT round(balance * 100) FROM pgledger_accounts_view WHERE id = %s", a
                )
            )
            / 100
            for a in accounts
        ]

    held = balances()
    assert accounts, "nothing to pay out"

    def pay_owners(victim: Any) -> None:
        with demo.Builder(victim, built.pmc_id).as_staff(built.manager) as s:
            payables.pay_owners(s, victim, run)

    cut_off(database_url, conn, pay_owners)
    assert balances() == held

    with demo.Builder(app_conn, built.pmc_id).as_staff(built.manager) as s:
        first = payables.pay_owners(s, app_conn, run)
    after = balances()
    with demo.Builder(app_conn, built.pmc_id).as_staff(built.manager) as s:
        again = payables.pay_owners(s, app_conn, run)
    assert [p.model_dump() for p in again] == [p.model_dump() for p in first]
    assert balances() == after
    assert all(b < h for b, h in zip(after, held, strict=True))


@pytest.mark.timing
def test_an_import_cut_off_brings_in_nothing_and_its_retry_brings_each_tenant_once(
    database_url, conn, app_conn
):
    built = demo.books(app_conn)
    prop = conn.execute(
        "SELECT display_name FROM trust_properties WHERE pmc_id = %s ORDER BY display_name LIMIT 1",
        (built.pmc_id,),
    ).fetchone()
    assert prop is not None
    names = [f"Arriving Tenant {n:03d}" for n in range(200)]
    content = "property,tenant\n" + "".join(f"{prop[0]},{name}\n" for name in names)
    imported = switching.Import(kind="tenants", content=content)

    def tenants() -> int:
        return count(
            conn,
            "SELECT count(*) FROM trust_tenants"
            " WHERE pmc_id = %s AND display_name LIKE 'Arriving%%'",
            built.pmc_id,
        )

    def bring_in(victim: Any) -> None:
        with demo.Builder(victim, built.pmc_id).as_staff(built.manager) as s:
            switching.import_file(s, victim, imported)

    cut_off(database_url, conn, bring_in)
    assert tenants() == 0

    with demo.Builder(app_conn, built.pmc_id).as_staff(built.manager) as s:
        assert switching.import_file(s, app_conn, imported).added == len(names)
    with demo.Builder(app_conn, built.pmc_id).as_staff(built.manager) as s:
        assert switching.import_file(s, app_conn, imported).added == 0
    assert tenants() == len(names)
