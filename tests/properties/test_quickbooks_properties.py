"""Property-based tests for the QuickBooks journal (src/pms/quickbooks.py, migration 0102; the
founder's review q117).

Hypothesis posts a random run of transfers between a company's ledger accounts over January
(money coming into a trust bank account, moved between what it holds, paid out; any refused
for taking an account below zero is left out), names some accounts in QuickBooks (two of them
now and then under one name, as a company that keeps one QuickBooks account for both would),
and exports a random span of days. Then:
  - each journal entry is one transfer of those days (UTC), debiting the QuickBooks account the
    money left and crediting the one it reached, so it balances;
  - for each QuickBooks account, its debits less its credits are what its ledger accounts lost
    over those days (their entries' sum, turned round), so QuickBooks' balances move as the
    ledger's do;
  - the Desktop file's TRNS and SPL lines of each transaction sum to zero, and the Online file's
    debits and credits of each journal number are equal.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import csv
import io
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import psycopg
from helpers import make_pmc, transfer_batch
from hypothesis import event, given
from hypothesis import strategies as st

from pms import quickbooks

JAN_1 = date(2026, 1, 1)
CENTS = st.integers(min_value=1, max_value=200_000).map(lambda c: Decimal(c) / 100)
# Accounts by their place in the operating trust account: 0 is its cash.
MOVES = st.lists(
    st.tuples(st.integers(0, 4), st.integers(0, 4), CENTS, st.integers(0, 30), st.integers(0, 23)),
    min_size=1,
    max_size=12,
)
NAMES = st.lists(st.sampled_from([None, "Trust checking", "Shared:Pool"]), min_size=5, max_size=5)


@given(moves=MOVES, named=NAMES, first=st.integers(0, 30), span=st.integers(0, 30))
def check(conn, moves, named, first, span):
    pmc = make_pmc(conn)
    held = [
        pmc.operating_cash,
        pmc.owners[0].account,
        pmc.owners[1].account,
        pmc.pmc_income,
        pmc.prepaid_rent,
    ]
    for account, label in zip(held, named, strict=True):
        if label:
            conn.execute(
                "INSERT INTO trust_quickbooks_accounts (pmc_id, ledger_account_id, name)"
                " VALUES (%s, %s, %s)",
                (pmc.pmc_id, account, label),
            )
    for source, target, amount, day, hour in moves:
        if source == target:
            continue
        try:
            with conn.transaction():
                transfer_batch(
                    conn,
                    [(held[source], held[target], amount)],
                    datetime.combine(JAN_1 + timedelta(days=day), time(hour), UTC),
                )
        except psycopg.errors.CheckViolation:
            event("a move refused: it would take an account below zero")
    start, last = JAN_1 + timedelta(days=first), JAN_1 + timedelta(days=first + span)

    entries = quickbooks.journal(conn, pmc.pmc_id, start, last)

    shown = {a.ledger_account_id: a.in_quickbooks for a in quickbooks.accounts(conn, pmc.pmc_id)}
    moved = conn.execute(
        "SELECT e.account_id, sum(e.amount) FROM pgledger_entries AS e"
        " JOIN pgledger_transfers AS tr ON tr.id = e.transfer_id"
        " WHERE e.account_id = ANY(%s) AND tr.event_at >= %s AND tr.event_at < %s"
        " GROUP BY e.account_id",
        (
            held,
            datetime.combine(start, time(), UTC),
            datetime.combine(last + timedelta(days=1), time(), UTC),
        ),
    ).fetchall()
    expected: dict[str, Decimal] = defaultdict(Decimal)
    for account, total in moved:
        expected[shown[account]] -= total
    debits_less_credits: dict[str, Decimal] = defaultdict(Decimal)
    for e in entries:
        assert start <= e.day <= last and e.amount > 0
        debits_less_credits[e.debit] += e.amount
        debits_less_credits[e.credit] -= e.amount
    assert {k: v for k, v in debits_less_credits.items() if v} == {
        k: v for k, v in expected.items() if v
    }
    if any(e.debit == e.credit for e in entries):
        event("an entry between two accounts under one QuickBooks name")

    transaction: list[Decimal] = []
    for line in quickbooks.iif(entries).decode("cp1252").split("\r\n")[3:]:
        if line.startswith(("TRNS", "SPL")):
            transaction.append(Decimal(line.split("\t")[5]))
        elif line == "ENDTRNS":
            assert len(transaction) == 2 and sum(transaction) == 0
            transaction = []
    online: dict[str, Decimal] = defaultdict(Decimal)
    for row in list(csv.DictReader(io.StringIO(quickbooks.online_csv(entries).decode()))):
        online[row["Journal No."]] += Decimal(row["Debits"] or 0) - Decimal(row["Credits"] or 0)
    assert all(v == 0 for v in online.values()) and len(online) == len(entries)


def test_the_journal_moves_quickbooks_balances_as_the_ledger_moves(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
