"""Property-based tests for checks (src/pms/checks.py, migration 20261022000157).

Hypothesis makes any amounts and any runs of checks printed and voided:
  - an amount in words reads back as the amount, dollars and cents;
  - in the database, a check number is used once an account, a payout is on at most one check not
    voided, every run comes to its count and total, and what waits for a check is every payout by
    check on none.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import psycopg
from helpers import contribute, draw_owner, make_pmc
from hypothesis import event, given
from hypothesis import strategies as st

from pms import checks
from pms.api import payouts

NUMBERS = {word: n for n, word in enumerate(checks.ONES)} | {
    word: 10 * n for n, word in enumerate(checks.TENS) if word
}
SCALES = {"thousand": 10**3, "million": 10**6, "billion": 10**9}


def read_back(words: str) -> int:
    """Words as a check writes them, as a number: the reader a bank's teller is."""
    total = group = 0
    for word in words.split():
        if word == "hundred":
            group *= 100
        elif word in SCALES:
            total += group * SCALES[word]
            group = 0
        else:
            group += sum(NUMBERS[part] for part in word.split("-"))
    return total + group


@given(cents=st.integers(min_value=1, max_value=10**14 - 1))
def test_an_amount_in_words_reads_back_as_the_amount(cents):
    words, _, part = checks.amount_in_words(Decimal(cents) / 100).rpartition(" and ")
    assert read_back(words.lower()) == cents // 100
    assert part == f"{cents % 100:02d}/100"
    assert checks.figures(Decimal(cents) / 100).replace("*", "").replace(",", "") == (
        f"{cents // 100}.{cents % 100:02d}"
    )


steps = st.lists(
    st.one_of(
        st.tuples(st.just("pay"), st.sampled_from(["check", "EFT"]), st.integers(1, 5000)),
        st.tuples(
            st.just("print"),
            st.lists(st.integers(0, 30), min_size=1, max_size=4),
            st.integers(1, 12),
        ),
        st.tuples(st.just("void"), st.integers(0, 30)),
    ),
    min_size=1,
    max_size=14,
)


@given(run=steps)
def check_runs(conn, run):
    pmc = make_pmc(conn, owners=1)
    owner = pmc.owners[0]
    paid: list[tuple[str, str, Decimal]] = []  # transfer, method, amount
    live: dict[str, str] = {}  # payout -> the check not voided it is on (the model)
    used: set[int] = set()
    printed: list[str] = []
    voided: set[str] = set()
    for step in run:
        if step[0] == "pay":
            amount = Decimal(step[2]) / 100
            contribute(conn, pmc.pmc_id, owner.account, f"in/{uuid.uuid4()}", amount)
            key = f"draw/{uuid.uuid4()}"
            draw_owner(conn, pmc.pmc_id, owner.account, key, amount, datetime.now(UTC))
            (transfer,) = conn.execute(
                "SELECT transfer_id FROM trust_owner_draws WHERE pmc_id = %s AND request_key = %s",
                (pmc.pmc_id, key),
            ).fetchone()
            conn.execute(
                "INSERT INTO trust_payout_methods (pmc_id, transfer_id, method)"
                " VALUES (%s, %s, %s)",
                (pmc.pmc_id, transfer, step[1]),
            )
            paid.append((transfer, step[1], amount))
        elif step[0] == "print" and paid:
            picked = list(dict.fromkeys(paid[i % len(paid)] for i in step[1]))
            numbers = range(step[2], step[2] + len(picked))
            ok = all(how == "check" and t not in live for t, how, _ in picked) and not (
                used & set(numbers)
            )
            made = []
            try:
                with conn.transaction():
                    (run_id,) = conn.execute(
                        "INSERT INTO trust_check_runs (pmc_id, bank_account_id, checks, total)"
                        " VALUES (%s, %s, %s, %s) RETURNING id",
                        (pmc.pmc_id, pmc.operating_bank_id, len(picked),
                         sum(a for _, _, a in picked)),
                    ).fetchone()  # fmt: skip
                    for number, (transfer, _, amount) in zip(numbers, picked, strict=True):
                        made.append(
                            conn.execute(
                                "INSERT INTO trust_checks (pmc_id, run_id, bank_account_id,"
                                " check_number, transfer_id, amount)"
                                " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                                (pmc.pmc_id, run_id, pmc.operating_bank_id, number, transfer,
                                 amount),
                            ).fetchone()[0]
                        )  # fmt: skip
            except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation):
                assert not ok
                event("a run refused")
            else:
                assert ok
                event("a run printed")
                used |= set(numbers)
                printed += made
                live.update({t: c for (t, _, _), c in zip(picked, made, strict=True)})
        elif step[0] == "void" and printed:
            check_id = printed[step[1] % len(printed)]
            try:
                conn.execute(
                    "INSERT INTO trust_check_voids (pmc_id, check_id, reason)"
                    " VALUES (%s, %s, 'Spoiled')",
                    (pmc.pmc_id, check_id),
                )
            except psycopg.errors.UniqueViolation:
                assert check_id in voided
            else:
                assert check_id not in voided
                event("a check voided")
                voided.add(check_id)
                live = {t: c for t, c in live.items() if c != check_id}

    numbers = [
        n
        for (n,) in conn.execute(
            "SELECT check_number FROM trust_checks WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchall()
    ]
    assert sorted(numbers) == sorted(used) and len(set(numbers)) == len(numbers)
    on_checks = conn.execute(
        "SELECT c.transfer_id, c.id FROM trust_checks AS c WHERE c.pmc_id = %s"
        " AND NOT EXISTS (SELECT 1 FROM trust_check_voids AS v WHERE v.check_id = c.id)",
        (pmc.pmc_id,),
    ).fetchall()
    assert dict(on_checks) == live and len(on_checks) == len(live)
    for run_id, count, total, n, summed in conn.execute(
        "SELECT r.id, r.checks, r.total, count(c.id), sum(c.amount) FROM trust_check_runs AS r"
        " JOIN trust_checks AS c ON c.run_id = r.id WHERE r.pmc_id = %s GROUP BY r.id",
        (pmc.pmc_id,),
    ).fetchall():
        assert (count, total) == (n, summed), run_id
    waiting = payouts.waiting(conn, pmc.pmc_id, pmc.operating_bank_id, "check")
    assert {p.transfer_id for p in waiting} == {t for t, how, _ in paid if how == "check"} - set(
        live
    )


def test_a_number_is_used_once_and_a_payout_is_on_one_check_not_voided(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_runs(conn)
