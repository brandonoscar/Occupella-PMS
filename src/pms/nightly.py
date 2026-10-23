"""What posts by itself every night (docs/adr/0006-buildium-punch-list.md): for each PMC, this
month's rent and recurring charges (due on its first day, each lease in effect for its days and
each starting later in the month its first part month; a lease due on another day on that day),
the late fees of rents still open after their grace days (and housing authorities' late payment
fees, migration 0116), each lease's tenants' prepaid rent for what it owes for a company that
turned that on (migration 0117), the recurring bills due by today, each
with its email to an owner whose approval it waits for, for a company that turned it on, last
month's management fees from its day of the month, and the recurring work orders due by today
(trust_open_recurring_work_orders, migration 0085). Rent and late fees go through
trust_run_rent, which records the run; bills through trust_enter_recurring_bills; fees through
trust_take_monthly_management_fees.

Each PMC in its own transaction, as trust_app, naming nobody (a run by hand is an admin's, at
Rent roll, Run rent now). Each part charges or enters once per date, so a run again (a restart,
this command, an admin's catch-up) charges nothing new. Only the management fees move trust
money, inside trust, from each owner's account to the company's fee account, once per account and
month (the founder's answer of 2026-10-18 to q049); charges are what tenants owe, and bills wait
to be set aside and paid.

The API runs it when it starts and then every day at 06:00 UTC, unless PMS_NIGHTLY is "off".
`python -m pms.nightly` runs it once, for a server that schedules it itself. One run at a time,
whatever starts it (two API servers, or this command while the API runs): a run holds a database
lock while it lasts, and one that finds it held leaves the work to that run.

usage: python -m pms.nightly    (DATABASE_URL and PMS_PUBLIC_URL, acting as trust_app)
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql

from pms import email, notices
from pms.api import payables, rent

log = logging.getLogger("pms.nightly")

# When the run starts each day, UTC: after midnight in every US time zone.
AT = time(6, 0)
# How soon a run that failed (the database not up yet) is tried again, in seconds.
RETRY = 300.0
# The session advisory lock a run holds while it lasts (the outbox locks each email the same
# way, src/pms/email.py).
LOCK = "pms.nightly"


@dataclass(frozen=True)
class Ran:
    pmc_id: UUID
    rents: int
    recurring: int
    late_fees: int
    bills: int
    # Rent reminders, late notices and owner statements written (src/pms/notices.py).
    emails: int = 0
    # Last month's management fees taken (migration 0082).
    management_fees: int = 0
    # Work orders opened from recurring ones (migration 0085).
    work_orders: int = 0
    # Housing authorities' parts of rents charged their late payment fee (migration 0116).
    authority_late_fees: int = 0
    # Leases whose tenants' prepaid rent paid what they owe (migration 0117).
    prepaid_used: int = 0


def run_pmc(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    day: date,
    public_url: str,
    sending: bool = False,
) -> Ran:
    """One PMC's run for a day, in one transaction. While the server sends email, it also writes
    the rent reminders, late notices and owner statements the company's settings ask for."""
    with conn.transaction():
        made = conn.execute(
            "SELECT trust_run_rent(%s, %s, %s)", (pmc_id, day.replace(day=1), day)
        ).fetchone()
        assert made is not None
        run = conn.execute(
            "SELECT rents, recurring, late_fees FROM trust_rent_runs WHERE id = %s", (made[0],)
        ).fetchone()
        # The recurring bills due, each with the email to an owner whose approval it waits for.
        entered = payables.enter_recurring_bills(conn, pmc_id, day, public_url)
        fees = conn.execute(
            "SELECT trust_take_monthly_management_fees(%s, %s)", (pmc_id, day)
        ).fetchone()
        opened = conn.execute(
            "SELECT trust_open_recurring_work_orders(%s, %s)", (pmc_id, day)
        ).fetchone()
        authority = conn.execute(
            "SELECT trust_assess_authority_late_fees(%s, %s)", (pmc_id, day)
        ).fetchone()
        assert run is not None and fees is not None and opened is not None
        assert authority is not None
        prepaid = rent.use_prepaid_by_itself(conn, pmc_id, day)
        written = notices.write_scheduled(conn, pmc_id, day, public_url) if sending else None
    emails = 0 if written is None else sum(vars(written).values())
    return Ran(
        pmc_id,
        run[0],
        run[1],
        run[2],
        entered,
        emails,
        fees[0],
        opened[0],
        authority[0],
        prepaid,
    )


def run_all(
    conn: psycopg.Connection[Any],
    day: date,
    public_url: str,
    pmcs: Iterable[UUID] | None = None,
    sending: bool = False,
) -> list[Ran]:
    """Every PMC's run for a day (or only those named). One that fails is logged and left for
    the next run; the others still run. While another run holds the lock, this one runs
    nothing."""
    held = conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (LOCK,)).fetchone()
    conn.commit()
    if held is None or not held[0]:
        log.info("another nightly run is under way; this one leaves the work to it")
        return []
    try:
        if pmcs is None:
            pmcs = [row[0] for row in conn.execute("SELECT pmc_id FROM trust_pmcs ORDER BY pmc_id")]
            conn.commit()
        done = []
        for pmc_id in pmcs:
            try:
                done.append(run_pmc(conn, pmc_id, day, public_url, sending))
            except psycopg.Error:
                log.exception("the nightly run of PMC %s failed; the next run tries again", pmc_id)
        return done
    finally:
        if not conn.closed:
            conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (LOCK,))
            conn.commit()


def seconds_until(now: datetime, at: time = AT) -> float:
    """From now to the next time of day `at` (UTC), later than now."""
    next_run = datetime.combine(now.date(), at, UTC)
    if next_run <= now:
        next_run += timedelta(days=1)
    return (next_run - now).total_seconds()


class Scheduler:
    """Runs every PMC's run when started, then every day at `at` (UTC), in a thread of its own
    with a connection of its own, until stopped. A run that fails is tried again `retry`
    seconds later. After each run, what it wrote to the outbox goes to the email sender."""

    def __init__(
        self,
        database_url: str,
        role: str,
        public_url: str,
        sender: email.Sender | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        at: time = AT,
        retry: float = RETRY,
        pmcs: Sequence[UUID] | None = None,
    ) -> None:
        self.database_url = database_url
        self.role = role
        self.public_url = public_url
        self.sender = sender
        self.clock = clock
        self.at = at
        self.retry = retry
        self.pmcs = pmcs
        self.ran = threading.Event()  # set after each run that finished
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="pms-nightly", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def run_once(self) -> list[Ran]:
        with psycopg.connect(self.database_url) as conn:
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(self.role)))
            conn.commit()
            done = run_all(
                conn, self.clock().date(), self.public_url, self.pmcs, self.sender is not None
            )
            if self.sender is not None:
                conn.autocommit = True
                email.deliver_pending(conn, self.sender)
        self.ran.set()
        return done

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
                wait = seconds_until(self.clock(), self.at)
            except Exception:
                log.exception("the nightly run failed; it is tried again in %s seconds", self.retry)
                wait = self.retry
            if self._stop.wait(wait):
                return


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("usage: python -m pms.nightly", file=sys.stderr)
        return 2
    public_url = os.environ.get("PMS_PUBLIC_URL", "http://localhost:8000")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier("trust_app")))
        conn.commit()
        # What it writes to the outbox goes out with `python -m pms.email deliver`.
        sending = bool(os.environ.get("PMS_EMAIL_SENDER"))
        done = run_all(conn, datetime.now(UTC).date(), public_url, sending=sending)
    rents = sum(r.rents for r in done)
    late_fees = sum(r.late_fees for r in done)
    bills = sum(r.bills for r in done)
    emails = sum(r.emails for r in done)
    fees = sum(r.management_fees for r in done)
    orders = sum(r.work_orders for r in done)
    print(
        f"{len(done)} companies: {rents} rents, {late_fees} late fees, {bills} bills,"
        f" {emails} emails, {fees} management fees, {orders} work orders"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
