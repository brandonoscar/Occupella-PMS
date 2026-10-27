"""Fast-forward: a company lived through every night for months or years, its books checked after
each night (the founder, 2026-10-25: tests and simulations an enterprise could trust).

The demo PMC's books (pms.demo.books, synthetic) are built on a first day months back, with what
the PMS does by itself turned on: the monthly management fees, tenants' prepaid rent paying new
charges, and every email it writes (to its outbox: nothing is sent). Then, for each day in turn:

  1. the nightly run for the day (pms.nightly.run_pmc, writing email, the fake payments partner);
  2. what people do that day, through the API's own route functions, as the manager: each lease's
     tenants pay by their habit (on time, late, half on time and the rest late, two months at
     once, or on time with one payment in four bouncing three days later); on the 10th every
     owner is paid what is available; on the 15th the open bills are paid on Pay bills;
  3. the checks below, on the books as they stand at the day's end.

Every random choice comes from one seed, so a failing night replays exactly (--seed).

Each night:
  - what the night posted is dated by it: no transfer and no charge after the day (a part of the
    PMS reading the real clock instead of the day it runs for would show here);
  - no lease is charged rent twice for a due date, and no rent carries two late fees;
  - the delinquency report's total is what is open of the leases' charges (trust_open_charges);
  - the trial balance's debits are its credits, and each trust bank account's book cash is what
    it holds (the balance sheet), at the day's end.
Each month's first night, for the month before:
  - each owner's statement for it ends at their accounts' balances;
  - each owner's property account that collected rent the month before has its management fee
    for it, once, by the fees' day of the month.

usage: PYTHONPATH=src python -m tools.fast_forward [--months 13] [--seed 0]
       (a throwaway database on the server in $DATABASE_URL; dbmate on PATH)
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql

from pms import demo, nightly
from pms.api import directory, emails, outbox, payables, rent
from tools.check_rollback import Dbmate
from tools.scratch_db import scratch_database

URL = "http://localhost:8000"
MIGRATIONS = Path(__file__).resolve().parent.parent / "db/migrations"
CENT = Decimal("0.01")
HABITS = ("on time", "late", "half then the rest", "two months at once", "bounces now and then")
# The day of the month the monthly management fees are taken, and owners and bills paid.
FEES_DAY, OWNERS_DAY, BILLS_DAY = 5, 10, 15


class NightFailed(AssertionError):
    """A night whose books are wrong, with the day and the seed that replays it."""


@dataclass
class Life:
    """The simulated company and what its people will do."""

    built: demo.Demo
    rng: random.Random
    first: date
    habits: dict[UUID, str] = field(default_factory=dict)
    # Payments to bounce: the day it comes back, and the transfer.
    bounces: list[tuple[date, str]] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)

    def count(self, what: str, n: int = 1) -> None:
        self.totals[what] = self.totals.get(what, 0) + n


def rows(conn: psycopg.Connection[Any], query: str, params: tuple[Any, ...] = ()) -> list[Any]:
    return conn.execute(query, params).fetchall()


def end_of(day: date) -> datetime:
    """The last moment of a day, UTC: what the books hold at its end."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1, microseconds=-1)


@contextmanager
def as_manager(conn: psycopg.Connection[Any], life: Life) -> Iterator[Any]:
    with demo.Builder(conn, life.built.pmc_id).as_staff(life.built.manager) as session:
        yield session


def request_id(life: Life) -> UUID:
    return uuid.UUID(int=life.rng.getrandbits(128), version=4)


def start(conn: psycopg.Connection[Any], first: date, seed: int) -> Life:
    """The demo's books from `first`, everything the PMS does by itself on, and a habit for
    each lease."""
    built = demo.books(conn, first)
    # Replayable, not secret: one seed for every choice.
    life = Life(built, random.Random(seed), first)  # noqa: S311
    with as_manager(conn, life) as s:
        payables.set_automatic_fees(
            s, conn, payables.AutomaticFees(automatic=True, day_of_month=FEES_DAY)
        )
    with as_manager(conn, life) as s:
        rent.set_automatic_prepaid(s, conn, rent.AutomaticPrepaid(automatic=True))
    with as_manager(conn, life) as s:
        emails.save_email_settings(
            s,
            conn,
            emails.EmailSettings(
                rent_reminder_days=5,
                late_notices=True,
                owner_statement_day=3,
                work_order_notices=True,
                reserve_alerts=True,
            ),
        )
    # Somewhere for the emails to go (the reserved example.com: they go nowhere), and a bill
    # that comes every month.
    for n, tenant in enumerate(sorted(built.tenants.values())):
        with as_manager(conn, life) as s:
            directory.tenant_contact(
                s, conn, tenant, directory.Contact(email=f"tenant{n}@example.com")
            )
    for n, owner in enumerate(sorted(built.owners.values())):
        with as_manager(conn, life) as s:
            directory.owner_contact(
                s, conn, owner, directory.Contact(email=f"owner{n}@example.com")
            )
    # Its row only: adding one through the API enters the months due through the real today at
    # once, and the nightly run is to enter each month on its day here.
    with as_manager(conn, life):
        conn.execute(
            "INSERT INTO trust_recurring_bills (pmc_id, vendor_id, ledger_account_id,"
            " reference_prefix, amount, memo, day_of_month, starts_on)"
            " VALUES (%s, %s, %s, 'LAWN', 85.00, 'Lawn care', 8, %s)",
            (
                built.pmc_id,
                sorted(built.vendors.values())[0],
                sorted(built.accounts.values())[0],
                first,
            ),
        )
    for (_, _), lease in sorted(built.leases.items()):
        life.habits[lease] = life.rng.choice(HABITS)
    return life


def owed(conn: psycopg.Connection[Any], pmc: UUID, lease: UUID, day: date) -> Decimal:
    found = conn.execute(
        "SELECT coalesce(sum(open), 0) FROM trust_open_charges(%s, %s, %s)",
        (pmc, lease, end_of(day)),
    ).fetchone()
    assert found is not None
    return Decimal(found[0])


def monthly_rent(conn: psycopg.Connection[Any], lease: UUID, day: date) -> Decimal:
    found = conn.execute("SELECT trust_rent_on(%s, %s)", (lease, day)).fetchone()
    return Decimal(found[0]) if found and found[0] is not None else Decimal(0)


def in_effect(conn: psycopg.Connection[Any], lease: UUID, day: date) -> bool:
    found = conn.execute(
        "SELECT starts_on <= %s AND (ends_on IS NULL OR ends_on >= %s) FROM trust_leases"
        " WHERE id = %s",
        (day, day, lease),
    ).fetchone()
    return bool(found and found[0])


def pay(conn: psycopg.Connection[Any], life: Life, lease: UUID, amount: Decimal, day: date) -> str:
    with as_manager(conn, life) as s:
        paid = rent.pay(
            s,
            conn,
            lease,
            rent.NewPayment(
                request_id=request_id(life), amount=amount, received_on=day, method="check"
            ),
        )
    life.count("payments")
    return paid.transfers[0]


def tenants_pay(conn: psycopg.Connection[Any], life: Life, day: date) -> None:
    pmc = life.built.pmc_id
    for lease, habit in life.habits.items():
        if not in_effect(conn, lease, day):
            continue
        due = owed(conn, pmc, lease, day)
        month_rent = monthly_rent(conn, lease, day)
        if due <= 0 and not (habit == "two months at once" and day.day == 1):
            continue
        if (habit, day.day) in (("on time", 2), ("late", 12)):
            pay(conn, life, lease, due, day)
        elif habit == "half then the rest" and day.day in (2, 20) and due > 0:
            pay(conn, life, lease, due if day.day == 20 else (due / 2).quantize(CENT), day)
        elif habit == "two months at once" and day.day == 1 and day.month % 2 == 1:
            pay(conn, life, lease, due + month_rent, day)
        elif habit == "bounces now and then" and day.day == 3 and due > 0:
            transfer = pay(conn, life, lease, due, day)
            if life.rng.random() < 0.25:
                life.bounces.append((day + timedelta(days=3), transfer))


def bounces_come_back(conn: psycopg.Connection[Any], life: Life, day: date) -> None:
    for _, transfer in [b for b in life.bounces if b[0] == day]:
        with as_manager(conn, life) as s:
            rent.bounced(s, conn, transfer, rent.Day(on=day))
        life.count("bounces")
    life.bounces = [b for b in life.bounces if b[0] != day]


def owners_paid(conn: psycopg.Connection[Any], life: Life, day: date) -> None:
    """Each owner's property account paid everything available, each on its own: an account
    with nothing to pay is refused in words, as on Pay owners."""
    for account in sorted(set(life.built.accounts.values())):
        try:
            with as_manager(conn, life) as s:
                payables.pay_owners(
                    s,
                    conn,
                    payables.PayOwners(
                        request_id=request_id(life),
                        on=day,
                        method="EFT",
                        payouts=[payables.Payout(account=account)],
                    ),
                )
            life.count("owner payouts")
        except Exception as refused:
            if getattr(refused, "status_code", None) not in (409, 422):
                raise


def bills_paid(conn: psycopg.Connection[Any], life: Life, day: date) -> None:
    """Every open bill dated by the day, paid on Pay bills, each on its own: one waiting for its
    owner, or that its owner's balance is short of, is refused in words, as on the page."""
    found, _ = payables.find_bills(conn, life.built.pmc_id, "open")
    paid = 0
    for bill in sorted(found, key=lambda b: str(b.id)):
        if bill.bill_date > day:
            continue
        try:
            with as_manager(conn, life) as s:
                payables.pay_bills(
                    s, conn, payables.PayBills(on=day, method="check", bill_ids=[bill.id])
                )
            paid += 1
        except Exception as refused:
            if getattr(refused, "status_code", None) not in (409, 422):
                raise
    life.count("bills paid", paid)


def check_night(conn: psycopg.Connection[Any], life: Life, day: date, since: datetime) -> None:
    pmc = life.built.pmc_id

    def fail(what: str) -> None:
        raise NightFailed(f"{day}: {what}")

    later = rows(
        conn,
        "SELECT id, event_at FROM pgledger_transfers AS t"
        " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.from_account_id"
        " WHERE a.pmc_id = %s AND t.created_at >= %s AND t.event_at > %s",
        (pmc, since, end_of(day)),
    )
    if later:
        fail(f"{len(later)} transfers posted tonight are dated after it, e.g. {later[0]}")
    charged_later = rows(
        conn,
        "SELECT id, kind, due_on FROM trust_charges WHERE pmc_id = %s AND created_at >= %s"
        " AND due_on > %s",
        (pmc, since, day),
    )
    if charged_later:
        fail(f"{len(charged_later)} charges written tonight fall due after it: {charged_later[0]}")
    twice = rows(
        conn,
        "SELECT lease_id, due_on, count(*) FROM trust_charges WHERE pmc_id = %s AND kind = 'rent'"
        " GROUP BY lease_id, due_on HAVING count(*) > 1",
        (pmc,),
    )
    if twice:
        fail(f"rent charged twice for a due date: {twice[0]}")
    open_total = Decimal(0)
    for lease in life.habits:
        open_total += max(owed(conn, pmc, lease, day), Decimal(0))
    report = conn.execute(
        "SELECT coalesce(sum(total), 0) FROM trust_report_delinquency(%s, %s) WHERE item = 'lease'",
        (pmc, day),
    ).fetchone()
    assert report is not None
    if Decimal(report[0]) != open_total:
        fail(
            f"the delinquency report says {report[0]} is owed,"
            f" the leases' open charges {open_total}"
        )
    unbalanced = rows(
        conn,
        "SELECT coalesce(sum(debit), 0), coalesce(sum(credit), 0)"
        " FROM trust_report_trial_balance(%s, %s)"
        " HAVING coalesce(sum(debit), 0) <> coalesce(sum(credit), 0)",
        (pmc, day),
    )
    if unbalanced:
        fail(f"the trial balance has debits {unbalanced[0][0]} and credits {unbalanced[0][1]}")
    differences = rows(
        conn,
        "SELECT bank, amount FROM trust_report_balance_sheet(%s, %s)"
        " WHERE item = 'difference' AND amount <> 0",
        (pmc, day),
    )
    if differences:
        fail(f"a trust bank account's book cash isn't what it holds: {differences[0]}")


def check_month(conn: psycopg.Connection[Any], life: Life, first: date) -> None:
    """The month before `first`, checked on its first night."""
    pmc = life.built.pmc_id
    month = (first - timedelta(days=1)).replace(day=1)
    end = end_of(first - timedelta(days=1))
    for owner in sorted(life.built.owners.values()):
        lines = rows(
            conn,
            "SELECT balance FROM trust_report_owner_statement(%s, %s, %s, %s) ORDER BY line",
            (pmc, owner, datetime(month.year, month.month, 1, tzinfo=UTC), end),
        )
        # What the owner's property accounts held at the month's end, from the ledger itself.
        held = conn.execute(
            "SELECT coalesce(sum(CASE WHEN t.to_account_id = a.ledger_account_id THEN t.amount"
            " ELSE -t.amount END), 0)"
            " FROM trust_ledger_accounts AS a JOIN pgledger_transfers AS t"
            " ON a.ledger_account_id IN (t.from_account_id, t.to_account_id)"
            " WHERE a.pmc_id = %s AND a.owner_id = %s AND a.kind = 'owner_property'"
            " AND t.event_at <= %s",
            (pmc, owner, end),
        ).fetchone()
        assert held is not None
        last = next((Decimal(r[0]) for r in reversed(lines) if r[0] is not None), None)
        if last is not None and last != Decimal(held[0]):
            raise NightFailed(
                f"{first}: owner {owner}'s statement for {month:%Y-%m} ends at {last},"
                f" their property accounts held {held[0]}"
            )


def check_fees(conn: psycopg.Connection[Any], life: Life, last_day: date) -> None:
    """On a month's last night: each owner's property account under a management agreement in
    force through the month before had that month's fee taken by itself (on FEES_DAY, or a
    later night if the owner was short). The key (account, period) makes it once."""
    month = (last_day.replace(day=1) - timedelta(days=1)).replace(day=1)
    if month < life.first:
        return
    missed = rows(
        conn,
        "SELECT a.ledger_account_id FROM trust_management_agreements AS a"
        " WHERE a.pmc_id = %s AND a.starts_on <= %s"
        " AND NOT EXISTS (SELECT 1 FROM trust_management_fees AS f"
        " WHERE f.ledger_account_id = a.ledger_account_id AND f.period_start = %s)"
        " GROUP BY a.ledger_account_id",
        (life.built.pmc_id, month, month),
    )
    if missed:
        raise NightFailed(
            f"{last_day}: {len(missed)} owners' accounts never had {month:%Y-%m}'s management fee"
            f" taken, e.g. {missed[0][0]}"
        )


def live(conn: psycopg.Connection[Any], first: date, last: date, seed: int) -> Life:
    """Every night from `first` to `last`, people's day after each, and the checks."""
    token = outbox.SENDING.set(True)
    try:
        life = start(conn, first, seed)
        day = first
        while day <= last:
            since = datetime.now(UTC)
            ran = nightly.run_pmc(conn, life.built.pmc_id, day, URL, sending=True, partner="fake")
            for name in ("rents", "late_fees", "management_fees", "bills", "emails"):
                life.count(name, getattr(ran, name))
            bounces_come_back(conn, life, day)
            tenants_pay(conn, life, day)
            if day.day == OWNERS_DAY:
                owners_paid(conn, life, day)
            if day.day == BILLS_DAY:
                bills_paid(conn, life, day)
            check_night(conn, life, day, since)
            if day.day == 1 and day > first:
                check_month(conn, life, day)
            if (day + timedelta(days=1)).day == 1:
                check_fees(conn, life, day)
            day += timedelta(days=1)
        return life
    finally:
        outbox.SENDING.reset(token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--months", type=int, default=13)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    server = os.environ.get("DATABASE_URL")
    dbmate = shutil.which("dbmate")
    if not server or not dbmate:
        print("FAIL: needs DATABASE_URL and dbmate on PATH.")
        return 1
    today = datetime.now(UTC).date()
    first = (today.replace(day=1) - timedelta(days=31 * args.months)).replace(day=1)
    began = time.monotonic()
    with scratch_database(server, "pms_fast_forward") as url:
        migrated = Dbmate(dbmate, MIGRATIONS, url).run("--no-dump-schema", "up")
        if migrated.returncode != 0:
            print(f"FAIL: dbmate up failed:\n{migrated.stderr}")
            return 1
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier("trust_app")))
            try:
                life = live(conn, first, today, args.seed)
            except NightFailed as failed:
                print(f"FAIL (replay with --months {args.months} --seed {args.seed}): {failed}")
                return 1
    nights = (today - first).days + 1
    print(
        f"ok: {nights} nights from {first}, seed {args.seed}, in"
        f" {time.monotonic() - began:.0f} s: {life.totals}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
