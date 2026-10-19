"""Many staff at once on one company's books: the nightly soak that the timing tests, each a
pair of transactions, don't reach.

`python -m tools.contention` builds the demo PMC's books (pms.demo, synthetic) in a throwaway
database on the server in $DATABASE_URL, then has each worker, on a connection of its own as
trust_app, call the API's own route functions as the manager, at random, on the same few leases
and owners' accounts: take a payment, bounce one, enter a bill and set it aside and pay it, pay
an owner, record a contribution, take the company's fees out of trust and run rent. A refusal
in words (an owner short of money, nothing to draw) is part of the load. A deadlock, a
serialization failure or any other error, the kind the API would answer with a 500, fails the
run, and so does books left wrong at the end: an account whose balance isn't what its transfers
add up to, a trust bank account that doesn't tie out, or an account other than a bank's cash
below zero.

usage: python -m tools.contention [--workers 16] [--seconds 120] [--seed 0]
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import sys
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from fastapi import HTTPException
from psycopg import sql

from pms import accounts, demo
from pms.api import payables, rent
from tools.check_rollback import Dbmate
from tools.scratch_db import scratch_database

MIGRATIONS = Path(__file__).resolve().parent.parent / "db/migrations"

# What the API answers in words (4xx). Anything else it would answer with a 500.
REFUSED = (HTTPException, accounts.Refused, psycopg.errors.InsufficientPrivilege)


def as_app(url: str) -> psycopg.Connection[Any]:
    conn = psycopg.connect(url, autocommit=True)
    conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier("trust_app")))
    return conn


@dataclass
class Tally:
    """What the workers did: each operation's count by outcome, and every failure."""

    done: Counter[tuple[str, str]] = field(default_factory=Counter)
    failures: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, op: str, outcome: str, failure: str | None = None) -> None:
        with self.lock:
            self.done[op, outcome] += 1
            if failure is not None:
                self.failures.append(f"{op}: {failure}")


class Worker:
    """One staff member's browser: a connection of its own, one transaction a request."""

    def __init__(self, url: str, books: demo.Demo, rng: random.Random, today: date) -> None:
        self.conn = as_app(url)
        self.builder = demo.Builder(self.conn, books.pmc_id)
        self.books = books
        self.rng = rng
        self.today = today
        self.payments: list[str] = []
        self.bills = 0

    @contextmanager
    def request(self) -> Iterator[accounts.Session]:
        with self.builder.as_staff(self.books.manager) as session:
            yield session

    def amount(self, low: int, high: int) -> Decimal:
        return Decimal(self.rng.randint(low * 100, high * 100)) / 100

    def pay(self) -> None:
        lease = self.rng.choice(list(self.books.leases.values()))
        new = rent.NewPayment(
            request_id=uuid.uuid4(), amount=self.amount(1, 900), received_on=self.today
        )
        with self.request() as s:
            paid = rent.pay(s, self.conn, lease, new)
        self.payments.append(paid.transfers[0])

    def bounce(self) -> None:
        if not self.payments:
            self.pay()
            return
        transfer = self.payments.pop(self.rng.randrange(len(self.payments)))
        with self.request() as s:
            rent.bounced(s, self.conn, transfer, rent.Day(on=self.today))

    def bill(self) -> None:
        self.bills += 1
        new = payables.NewBill(
            vendor_id=self.rng.choice(list(self.books.vendors.values())),
            account=self.rng.choice(list(self.books.accounts.values())),
            reference=f"SOAK-{uuid.uuid4().hex[:12]}",
            bill_date=self.today,
            due_on=self.today,
            amount=self.amount(1, 200),
            memo="Soak",
            expense_account_id=self.rng.choice(list(self.books.expense_accounts.values())),
        )
        with self.request() as s:
            bill = payables.enter_bill(s, self.conn, new).id
        with self.request() as s:
            payables.set_aside(s, self.conn, bill, payables.Day(on=self.today))
        with self.request() as s:
            payables.pay(s, self.conn, bill, payables.Paying(on=self.today))

    def draw(self) -> None:
        payout = payables.Payout(
            account=self.rng.choice(list(self.books.accounts.values())),
            amount=self.amount(1, 300),
        )
        run = payables.PayOwners(
            request_id=uuid.uuid4(), on=self.today, method="EFT", payouts=[payout]
        )
        with self.request() as s:
            payables.pay_owners(s, self.conn, run)

    def contribute(self) -> None:
        new = payables.Contribution(
            request_id=uuid.uuid4(),
            account=self.rng.choice(list(self.books.accounts.values())),
            amount=self.amount(1, 300),
            on=self.today,
        )
        with self.request() as s:
            payables.contribute(s, self.conn, new)

    def take_fees(self) -> None:
        taken = payables.FeeWithdrawal(
            request_id=uuid.uuid4(), bank_account_id=self.books.operating, on=self.today
        )
        with self.request() as s:
            payables.take_company_fees(s, self.conn, taken)

    def run_rent(self) -> None:
        month = self.today.replace(day=1)
        with self.request() as s:
            rent.run_rent(s, self.conn, rent.RunRent(due_on=month, late_fees_as_of=self.today))

    def operations(self) -> dict[str, tuple[int, Callable[[], None]]]:
        """Each operation with its weight: mostly money coming in and going out."""
        return {
            "pay": (6, self.pay),
            "bounce": (2, self.bounce),
            "bill": (3, self.bill),
            "draw": (3, self.draw),
            "contribute": (2, self.contribute),
            "take fees": (1, self.take_fees),
            "run rent": (1, self.run_rent),
        }

    def run(self, until: float, tally: Tally) -> None:
        ops = self.operations()
        names = list(ops)
        weights = [ops[name][0] for name in names]
        try:
            while time.monotonic() < until:
                name = self.rng.choices(names, weights)[0]
                try:
                    ops[name][1]()
                except REFUSED:
                    tally.add(name, "refused")
                except Exception as error:  # every other error is a finding
                    first = str(error).splitlines()[0] if str(error) else ""
                    tally.add(name, "failed", f"{type(error).__name__}: {first}")
                else:
                    tally.add(name, "ok")
        finally:
            self.conn.close()


def problems(conn: psycopg.Connection[Any], pmc_id: UUID) -> list[str]:
    """What is wrong with the PMC's books: each one a line."""
    found = []
    for (account,) in conn.execute(
        """
        SELECT a.id FROM pgledger_accounts AS a
        JOIN trust_ledger_accounts AS t ON t.ledger_account_id = a.id
        WHERE t.pmc_id = %s
          AND a.balance <> coalesce((SELECT sum(x.amount) FROM pgledger_transfers AS x
                                     WHERE x.to_account_id = a.id), 0)
                          - coalesce((SELECT sum(x.amount) FROM pgledger_transfers AS x
                                      WHERE x.from_account_id = a.id), 0)
        ORDER BY a.id
        """,
        (pmc_id,),
    ):
        found.append(f"account {account}'s balance isn't what its transfers add up to")
    for bank, off in conn.execute(
        """
        SELECT t.bank_account_id, sum(a.balance) FROM trust_ledger_accounts AS t
        JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id
        WHERE t.pmc_id = %s GROUP BY t.bank_account_id HAVING sum(a.balance) <> 0
        ORDER BY t.bank_account_id
        """,
        (pmc_id,),
    ):
        found.append(f"trust bank account {bank} doesn't tie out (off by {off})")
    for account, kind, balance in conn.execute(
        """
        SELECT a.id, t.kind, a.balance FROM trust_ledger_accounts AS t
        JOIN pgledger_accounts AS a ON a.id = t.ledger_account_id
        WHERE t.pmc_id = %s AND t.kind <> 'bank_cash' AND a.balance < 0
        ORDER BY a.id
        """,
        (pmc_id,),
    ):
        found.append(f"{kind} account {account} is below zero ({balance})")
    return found


def soak(url: str, workers: int, seconds: float, seed: int) -> tuple[Tally, list[str]]:
    """Builds the demo's books on a migrated database, runs the workers, then checks them."""
    today = datetime.now(UTC).date()
    with as_app(url) as conn:
        books = demo.books(conn, today)
    tally = Tally()
    until = time.monotonic() + seconds
    # A soak's choices, no secret: a seeded generator repeats them.
    rngs = [random.Random(seed * 1000 + n) for n in range(workers)]  # noqa: S311
    crew = [Worker(url, books, rng, today) for rng in rngs]
    threads = [threading.Thread(target=w.run, args=(until, tally)) for w in crew]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    with as_app(url) as conn:
        return tally, problems(conn, books.pmc_id)


def report(tally: Tally, wrong: list[str]) -> str:
    lines = ["operation    ok  refused  failed"]
    for op in sorted({op for op, _ in tally.done}):
        ok, refused, failed = (tally.done[op, o] for o in ("ok", "refused", "failed"))
        lines.append(f"{op:<10} {ok:>4} {refused:>8} {failed:>7}")
    lines += [f"FAILED {n} x {failure}" for failure, n in Counter(tally.failures).most_common()]
    lines += [f"WRONG {problem}" for problem in wrong]
    if not any(outcome == "ok" for _, outcome in tally.done):
        lines.append("FAILED nothing went through: the soak tested nothing")
    return "\n".join(lines)


def passed(tally: Tally, wrong: list[str]) -> bool:
    went_through = any(outcome == "ok" for _, outcome in tally.done)
    return went_through and not tally.failures and not wrong


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    server = os.environ.get("DATABASE_URL")
    dbmate = shutil.which("dbmate")
    if not server or not dbmate:
        print("FAIL: needs DATABASE_URL and dbmate on PATH.")
        return 1
    with scratch_database(server, "pms_contention") as url:
        migrated = Dbmate(dbmate, MIGRATIONS, url).run("--no-dump-schema", "up")
        if migrated.returncode != 0:
            print(f"FAIL: dbmate up failed:\n{migrated.stderr}")
            return 1
        tally, wrong = soak(url, args.workers, args.seconds, args.seed)
    print(report(tally, wrong))
    return 0 if passed(tally, wrong) else 1


if __name__ == "__main__":
    sys.exit(main())
