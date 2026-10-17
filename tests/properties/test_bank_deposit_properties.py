"""Property-based tests for bank deposits (migration 20261018000078, src/pms/api/bank_deposits.py).

Hypothesis moves money in and out of a company's two trust bank accounts on random days and
records deposits of random sets of those moves on random days. A model keeps every move and what
each deposit took:
  - a deposit's items are written, all of them, exactly when each brought money into that
    deposit's account on or before its day and none is in a deposit already; otherwise nothing;
  - each move is in at most one deposit;
  - a deposit's total is what its items brought into its account's cash, and what is left to
    deposit is every move in not yet in a deposit, each once.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
from helpers import make_pmc, post
from hypothesis import event, given
from hypothesis import strategies as st

from pms.api.bank_deposits import deposits_of, undeposited_of

FIRST = date(2026, 1, 1)
DAYS = st.integers(min_value=0, max_value=6).map(lambda n: FIRST + timedelta(days=n))
CENTS = st.integers(min_value=1, max_value=50_000).map(lambda c: Decimal(c).scaleb(-2))
ACTIONS = st.lists(
    st.one_of(
        st.tuples(st.just("in"), st.sampled_from(["operating", "deposit"]), CENTS, DAYS),
        st.tuples(st.just("out"), CENTS, DAYS),
        st.tuples(
            st.just("deposit"),
            st.sampled_from(["operating", "deposit"]),
            st.lists(st.integers(min_value=0, max_value=20), min_size=1, max_size=4, unique=True),
            DAYS,
        ),
    ),
    min_size=1,
    max_size=14,
)


@given(actions=ACTIONS)
def check(conn, actions):
    pmc = make_pmc(conn)
    bank = {"operating": pmc.operating_bank_id, "deposit": pmc.deposit_bank_id}
    moves: list[tuple[str, str, Decimal, date, bool]] = []  # (id, account, amount, day, in)
    held = Decimal(0)  # the owner's balance, which a move out may not take below zero
    deposited: dict[str, uuid.UUID] = {}
    totals: dict[uuid.UUID, Decimal] = {}
    for action in actions:
        if action[0] == "in":
            _, account, amount, day = action
            cash, to = (
                (pmc.operating_cash, pmc.owners[0].account)
                if account == "operating"
                else (pmc.deposit_cash, pmc.tenant_deposit)
            )
            (moved,) = post(conn, pmc.pmc_id, f"in-{uuid.uuid4()}", [(cash, to, amount)], at(day))
            moves.append((moved, account, amount, day, True))
            held += amount if account == "operating" else 0
            continue
        if action[0] == "out":
            _, amount, day = action
            if amount > held:
                continue
            (moved,) = post(
                conn,
                pmc.pmc_id,
                f"out-{uuid.uuid4()}",
                [(pmc.owners[0].account, pmc.operating_cash, amount)],
                at(day),
            )
            moves.append((moved, "operating", amount, day, False))
            held -= amount
            continue
        _, account, picks, day = action
        chosen = [moves[i] for i in picks if i < len(moves)]
        if not chosen:
            continue
        fits = all(
            is_in and where == account and on <= day and moved not in deposited
            for moved, where, _, on, is_in in chosen
        )
        try:
            with conn.transaction():
                made = conn.execute(
                    "INSERT INTO trust_bank_deposits (pmc_id, bank_account_id, deposited_on)"
                    " VALUES (%s, %s, %s) RETURNING id",
                    (pmc.pmc_id, bank[account], day),
                ).fetchone()[0]
                for moved, *_ in chosen:
                    conn.execute(
                        "INSERT INTO trust_bank_deposit_items (deposit_id, pmc_id, transfer_id)"
                        " VALUES (%s, %s, %s)",
                        (made, pmc.pmc_id, moved),
                    )
        except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation):
            assert not fits
            event("refused")
            continue
        assert fits
        event("deposited")
        for moved, *_ in chosen:
            deposited[moved] = made
        totals[made] = sum((amount for _, _, amount, _, _ in chosen), Decimal(0))

    assert (
        dict(
            conn.execute(
                "SELECT transfer_id, deposit_id FROM trust_bank_deposit_items WHERE pmc_id = %s",
                (pmc.pmc_id,),
            ).fetchall()
        )
        == deposited
    )
    for account, bank_id in bank.items():
        shown = {d.id: Decimal(d.total) for d in deposits_of(conn, pmc.pmc_id, bank_id)}
        assert shown == {
            d: t for d, t in totals.items() if moves_of(moves, deposited, d) == account
        }
        left = sorted(t for u in undeposited_of(conn, pmc.pmc_id, bank_id) for t in u.transfer_ids)
        assert left == sorted(
            moved
            for moved, where, _, _, is_in in moves
            if is_in and where == account and moved not in deposited
        )


def at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 12, tzinfo=UTC)


def moves_of(moves, deposited, deposit_id) -> str:
    return next(where for moved, where, *_ in moves if deposited.get(moved) == deposit_id)


def test_a_deposit_holds_exactly_money_in_by_its_day_each_once(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
