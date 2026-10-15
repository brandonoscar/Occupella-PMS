"""Journal entries: one staff member proposes an adjusting entry and another, who holds the
approve role, posts it, through the ledger and every trust rule it keeps. Migration
20261015000053.

Each step names a staff member (trust.staff_id), as the API does on every staff request; the
server's command line, which names nobody, can't propose, post or decline one.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    acting_as,
    approve,
    balance,
    decline_entry,
    in_background,
    make_pmc,
    post_entry,
    propose_entry,
    staff_named,
    transfer_batch,
    wait_until_blocked,
)

JAN_1, JAN_15, FEB_1, FEB_10 = (
    date(2026, 1, 1),
    date(2026, 1, 15),
    date(2026, 2, 1),
    date(2026, 2, 10),
)
MEMO = "Rent deposited to the wrong property"


def at(day, hour=0):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def refused(match, error=psycopg.errors.CheckViolation):
    return pytest.raises(error, match=match)


@pytest.fixture
def pmc(conn):
    pmc = make_pmc(conn)
    # Owner 1's rent came in: 1000.00 held for them in the operating trust account.
    transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, "1000.00")], at(JAN_1, 12))
    return pmc


@pytest.fixture
def bookkeeper(conn, pmc):
    """Proposes entries; holds no role."""
    return staff_named(conn, pmc.pmc_id, "Bookkeeper 1")


@pytest.fixture
def manager(conn, pmc):
    """Posts them: holds the approve role."""
    return staff_named(conn, pmc.pmc_id, "Manager 1", ["approve"])


def move(pmc, amount="25.00"):
    """One line: money held for Owner 1 that was Owner 2's."""
    return [(pmc.owners[0].account, pmc.owners[1].account, amount)]


def decision(conn, entry):
    return conn.execute(
        "SELECT decision, decided_by, transfer_ids, reason FROM trust_journal_entry_decisions"
        " WHERE entry_id = %s",
        (entry,),
    ).fetchone()


def transfers_of(conn, entry):
    return conn.execute(
        "SELECT count(*) FROM pgledger_transfers WHERE metadata ->> 'journal_entry_id' = %s",
        (str(entry),),
    ).fetchone()[0]


def balances(conn, pmc):
    return [balance(conn, account) for account in pmc.accounts()]


def test_an_entry_posts_only_when_another_staff_member_approves_it(
    conn, app_conn, pmc, bookkeeper, manager
):
    entry = propose_entry(app_conn, pmc.pmc_id, bookkeeper, FEB_10, MEMO, move(pmc))

    assert conn.execute(
        "SELECT event_on, memo, proposed_by FROM trust_journal_entries WHERE id = %s", (entry,)
    ).fetchone() == (FEB_10, MEMO, bookkeeper)
    assert conn.execute(
        "SELECT line, from_account, to_account, amount FROM trust_journal_entry_lines"
        " WHERE entry_id = %s",
        (entry,),
    ).fetchall() == [(1, pmc.owners[0].account, pmc.owners[1].account, Decimal("25.00"))]
    assert balance(conn, pmc.owners[0].account) == Decimal("1000.00")  # nothing moved yet
    assert decision(conn, entry) is None

    (transfer,) = post_entry(app_conn, pmc.pmc_id, entry, manager)

    assert balance(conn, pmc.owners[0].account) == Decimal("975.00")
    assert balance(conn, pmc.owners[1].account) == Decimal("25.00")
    assert conn.execute(
        "SELECT event_at, metadata FROM pgledger_transfers WHERE id = %s", (transfer,)
    ).fetchone() == (
        at(FEB_10),
        {"memo": f"Journal entry: {MEMO}", "journal_entry_id": str(entry)},
    )
    assert decision(conn, entry) == ("posted", manager, [transfer], None)


def test_posting_it_again_returns_what_it_posted(conn, app_conn, pmc, bookkeeper, manager):
    entry = propose_entry(app_conn, pmc.pmc_id, bookkeeper, FEB_10, MEMO, move(pmc))
    first = post_entry(app_conn, pmc.pmc_id, entry, manager)

    assert post_entry(app_conn, pmc.pmc_id, entry, manager) == first
    assert transfers_of(conn, entry) == 1
    assert balance(conn, pmc.owners[1].account) == Decimal("25.00")


def test_its_lines_post_in_order_in_one_go(conn, app_conn, pmc, bookkeeper, manager):
    lines = [
        (pmc.owners[0].account, pmc.owners[1].account, "10.00"),
        (pmc.owners[1].account, pmc.pmc_income, "4.00"),
        (pmc.owners[0].account, pmc.owners[1].account, "0.01"),
    ]
    entry = propose_entry(app_conn, pmc.pmc_id, bookkeeper, FEB_10, MEMO, lines)

    posted = post_entry(app_conn, pmc.pmc_id, entry, manager)

    assert [
        conn.execute(
            "SELECT from_account_id, to_account_id, amount FROM pgledger_transfers WHERE id = %s",
            (transfer,),
        ).fetchone()
        for transfer in posted
    ] == [(f, t, Decimal(a)) for f, t, a in lines]
    assert [balance(conn, a) for a in (pmc.owners[1].account, pmc.pmc_income)] == [
        Decimal("6.01"),
        Decimal("4.00"),
    ]


def test_the_staff_member_who_proposed_it_does_not_post_it(conn, app_conn, pmc, manager):
    entry = propose_entry(app_conn, pmc.pmc_id, manager, FEB_10, MEMO, move(pmc))

    with refused(
        "who proposed a journal entry does not post it", psycopg.errors.InsufficientPrivilege
    ):
        post_entry(app_conn, pmc.pmc_id, entry, manager)

    assert transfers_of(conn, entry) == 0
    assert decision(conn, entry) is None


def test_posting_it_takes_the_approve_role(conn, app_conn, pmc, bookkeeper):
    clerk = staff_named(conn, pmc.pmc_id, "Clerk 1", ["disburse"])
    entry = propose_entry(app_conn, pmc.pmc_id, bookkeeper, FEB_10, MEMO, move(pmc))

    with refused(
        "Clerk 1 may not post a journal entry .the approve role.",
        psycopg.errors.InsufficientPrivilege,
    ):
        post_entry(app_conn, pmc.pmc_id, entry, clerk)

    assert transfers_of(conn, entry) == 0
    assert decision(conn, entry) is None


def test_every_step_names_a_staff_member(conn, app_conn, pmc, bookkeeper):
    with refused("name the staff member who proposes", psycopg.errors.InvalidParameterValue):
        propose_entry(app_conn, pmc.pmc_id, None, FEB_10, MEMO, move(pmc))
    entry = propose_entry(app_conn, pmc.pmc_id, bookkeeper, FEB_10, MEMO, move(pmc))
    with refused("name the staff member who posts", psycopg.errors.InvalidParameterValue):
        post_entry(app_conn, pmc.pmc_id, entry, None)
    with refused("name the staff member who declines", psycopg.errors.InvalidParameterValue):
        decline_entry(app_conn, pmc.pmc_id, entry, None)

    assert decision(conn, entry) is None
    assert conn.execute(
        "SELECT count(*) FROM trust_journal_entries WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (1,)


def test_a_declined_entry_is_never_posted(conn, app_conn, pmc, bookkeeper, manager):
    entry = propose_entry(app_conn, pmc.pmc_id, bookkeeper, FEB_10, MEMO, move(pmc))
    # Whoever proposed it may withdraw it.
    decline_entry(app_conn, pmc.pmc_id, entry, bookkeeper, "Entered twice")

    with refused("was declined; it is never posted"):
        post_entry(app_conn, pmc.pmc_id, entry, manager)
    with refused("was decided already", psycopg.errors.UniqueViolation):
        decline_entry(app_conn, pmc.pmc_id, entry, manager)

    assert decision(conn, entry) == ("declined", bookkeeper, None, "Entered twice")
    assert transfers_of(conn, entry) == 0


def test_a_posted_entry_is_not_declined(conn, app_conn, pmc, bookkeeper, manager):
    entry = propose_entry(app_conn, pmc.pmc_id, bookkeeper, FEB_10, MEMO, move(pmc))
    posted = post_entry(app_conn, pmc.pmc_id, entry, manager)

    with refused("was decided already", psycopg.errors.UniqueViolation):
        decline_entry(app_conn, pmc.pmc_id, entry, manager)

    assert decision(conn, entry) == ("posted", manager, posted, None)


def test_an_entry_or_a_decision_is_of_its_own_pmc(conn, app_conn, pmc, bookkeeper, manager):
    other = make_pmc(conn)
    stranger = staff_named(conn, other.pmc_id, "Manager 1", ["approve"])

    with refused("of its own PMC only", psycopg.errors.InvalidParameterValue):
        propose_entry(
            app_conn,
            pmc.pmc_id,
            bookkeeper,
            FEB_10,
            MEMO,
            [(pmc.owners[0].account, other.owners[0].account, "1.00")],
        )
    with refused("of its own PMC only", psycopg.errors.InvalidParameterValue):
        propose_entry(
            app_conn, pmc.pmc_id, bookkeeper, FEB_10, MEMO, [("nope", pmc.pmc_income, "1.00")]
        )
    # Staff of another PMC is no one here.
    with refused(
        "trust_journal_entries_pmc_id_proposed_by_fkey", psycopg.errors.ForeignKeyViolation
    ):
        propose_entry(app_conn, pmc.pmc_id, stranger, FEB_10, MEMO, move(pmc))

    entry = propose_entry(app_conn, pmc.pmc_id, bookkeeper, FEB_10, MEMO, move(pmc))
    with refused("no journal entry .* in PMC", psycopg.errors.InvalidParameterValue):
        post_entry(app_conn, other.pmc_id, entry, stranger)
    with refused("no journal entry .* in PMC", psycopg.errors.InvalidParameterValue):
        decline_entry(app_conn, other.pmc_id, entry, stranger)
    assert decision(conn, entry) is None


@pytest.mark.parametrize(
    ("lines", "error", "match"),
    [
        ([], psycopg.errors.InvalidParameterValue, "1 to 20 lines, not 0"),
        ([("a", "b", "1.00")] * 21, psycopg.errors.InvalidParameterValue, "1 to 20 lines, not 21"),
        ([("o0", "o1", "0.00")], psycopg.errors.CheckViolation, "amount_check"),
        ([("o0", "o1", "-1.00")], psycopg.errors.CheckViolation, "amount_check"),
        ([("o0", "o1", "1.001")], psycopg.errors.CheckViolation, "amount_check"),
        ([("o0", "o1", "10000000.01")], psycopg.errors.CheckViolation, "amount_check"),
        ([("o0", "o0", "1.00")], psycopg.errors.CheckViolation, "trust_journal_entry_lines_check"),
    ],
)
def test_an_entry_has_one_to_twenty_lines_of_whole_cents(
    conn, app_conn, pmc, bookkeeper, lines, error, match
):
    names = {"o0": pmc.owners[0].account, "o1": pmc.owners[1].account}
    named = [
        (names.get(f, pmc.owners[0].account), names.get(t, pmc.owners[1].account), a)
        for f, t, a in lines
    ]

    with refused(match, error):
        propose_entry(app_conn, pmc.pmc_id, bookkeeper, FEB_10, MEMO, named)

    assert conn.execute(
        "SELECT count(*) FROM trust_journal_entries WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


@pytest.mark.parametrize(("memo", "match"), [("  ", "memo_check"), ("x" * 501, "memo_check")])
def test_an_entry_says_what_it_is_for(app_conn, pmc, bookkeeper, memo, match):
    with refused(match):
        propose_entry(app_conn, pmc.pmc_id, bookkeeper, FEB_10, memo, move(pmc))


def test_an_entry_is_dated_a_day(conn, app_conn, pmc, bookkeeper):
    with refused("dated a day; none was given", psycopg.errors.InvalidParameterValue):
        propose_entry(app_conn, pmc.pmc_id, bookkeeper, None, MEMO, move(pmc))
    assert conn.execute(
        "SELECT count(*) FROM trust_journal_entries WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_an_entry_that_would_take_an_owner_below_zero_is_not_posted(
    conn, app_conn, pmc, bookkeeper, manager
):
    entry = propose_entry(app_conn, pmc.pmc_id, bookkeeper, FEB_10, MEMO, move(pmc, "1000.01"))
    before = balances(conn, pmc)

    with refused("owner_property account .* below zero"):
        post_entry(app_conn, pmc.pmc_id, entry, manager)

    assert balances(conn, pmc) == before
    assert decision(conn, entry) is None
    decline_entry(app_conn, pmc.pmc_id, entry, manager, "Too much")  # it is still undecided


def test_an_entry_dated_in_an_approved_period_is_not_posted(
    conn, app_conn, pmc, bookkeeper, manager
):
    approve(conn, pmc.pmc_id, pmc.operating_bank_id, at(JAN_1), at(FEB_1), "-1000.00")
    entry = propose_entry(app_conn, pmc.pmc_id, bookkeeper, JAN_15, MEMO, move(pmc))
    before = balances(conn, pmc)

    with refused("reconciled and closed"):
        post_entry(app_conn, pmc.pmc_id, entry, manager)

    assert balances(conn, pmc) == before
    assert decision(conn, entry) is None


def test_money_moves_to_another_trust_bank_account_only_with_its_cash(
    conn, app_conn, pmc, bookkeeper, manager
):
    # Owner 1's money that was the tenant's deposit: held in the operating trust account, it
    # belongs in the security-deposit one.
    alone = propose_entry(
        app_conn,
        pmc.pmc_id,
        bookkeeper,
        FEB_10,
        MEMO,
        [(pmc.owners[0].account, pmc.tenant_deposit, "300.00")],
    )
    with refused("does not tie out"):
        post_entry(app_conn, pmc.pmc_id, alone, manager)
    assert decision(conn, alone) is None

    with_cash = propose_entry(
        app_conn,
        pmc.pmc_id,
        bookkeeper,
        FEB_10,
        "Deposit held in the operating account",
        [
            (pmc.owners[0].account, pmc.operating_cash, "300.00"),
            (pmc.deposit_cash, pmc.tenant_deposit, "300.00"),
        ],
    )
    post_entry(app_conn, pmc.pmc_id, with_cash, manager)

    assert [
        balance(conn, a)
        for a in (pmc.owners[0].account, pmc.operating_cash, pmc.deposit_cash, pmc.tenant_deposit)
    ] == [Decimal("700.00"), Decimal("-700.00"), Decimal("-300.00"), Decimal("300.00")]


def test_journal_entries_are_append_only(conn, app_conn, pmc, bookkeeper, manager):
    entry = propose_entry(app_conn, pmc.pmc_id, bookkeeper, FEB_10, MEMO, move(pmc))
    post_entry(app_conn, pmc.pmc_id, entry, manager)

    for table in (
        "trust_journal_entries",
        "trust_journal_entry_lines",
        "trust_journal_entry_decisions",
    ):
        for query in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with refused("append-only", psycopg.errors.RestrictViolation):
                conn.execute(query)


def test_the_app_writes_entries_only_through_their_functions(app_conn, ai_conn, pmc, bookkeeper):
    with (
        refused("permission denied", psycopg.errors.InsufficientPrivilege),
        acting_as(app_conn, bookkeeper),
    ):
        app_conn.execute(
            "INSERT INTO trust_journal_entries (pmc_id, event_on, memo, proposed_by)"
            " VALUES (%s, %s, %s, %s)",
            (pmc.pmc_id, FEB_10, MEMO, bookkeeper),
        )
    # The AI never moves money: it reads entries and proposes none.
    with refused("permission denied", psycopg.errors.InsufficientPrivilege):
        propose_entry(ai_conn, pmc.pmc_id, bookkeeper, FEB_10, MEMO, move(pmc))
    assert ai_conn.execute(
        "SELECT count(*) FROM trust_journal_entries WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_every_step_is_in_the_audit_log_with_who_took_it(conn, app_conn, pmc, bookkeeper, manager):
    entry = propose_entry(app_conn, pmc.pmc_id, bookkeeper, FEB_10, MEMO, move(pmc))
    post_entry(app_conn, pmc.pmc_id, entry, manager)

    assert conn.execute(
        "SELECT table_name, staff_id FROM trust_audit_log"
        " WHERE row_data ->> 'entry_id' = %s OR row_data ->> 'id' = %s ORDER BY id",
        (str(entry), str(entry)),
    ).fetchall() == [
        ("trust_journal_entries", bookkeeper),
        ("trust_journal_entry_lines", bookkeeper),
        ("trust_journal_entry_decisions", manager),
    ]


@pytest.mark.timing
def test_a_post_and_a_decline_wait_for_each_other(conn, database_url, pmc, bookkeeper, manager):
    entry = propose_entry(conn, pmc.pmc_id, bookkeeper, FEB_10, MEMO, move(pmc))
    with psycopg.connect(database_url) as first:
        posted = post_entry(first, pmc.pmc_id, entry, manager)
        thread, pid, outcome = in_background(
            database_url, lambda worker: decline_entry(worker, pmc.pmc_id, entry, bookkeeper)
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join()

    assert "was decided already" in str(outcome["error"])
    assert decision(conn, entry) == ("posted", manager, posted, None)
