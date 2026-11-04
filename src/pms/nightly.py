"""What posts by itself every night (docs/adr/0006-buildium-punch-list.md): for each PMC, this
month's rent and recurring charges (due on its first day, each lease in effect for its days and
each starting later in the month its first part month; a lease due on another day on that day),
the late fees of rents still open after their grace days (and housing authorities' late payment
fees, migration 0116), each lease's tenants' prepaid rent for what it owes for a company that
turned that on (migration 0117), the recurring bills due by today, each
with its email to an owner whose approval it waits for, for a company that turned it on, last
month's management fees from its day of the month, the recurring work orders due by today
(trust_open_recurring_work_orders, migration 0085), and on its draw day every owner's draw,
prepared for staff to pay (trust_prepare_owner_draws, migration 0153). Before the rent, each
fixed-term lease that ran out with no renewal signed goes month to month at the same rent
(trust_roll_over_leases, migration 0154). Rent and late fees go through
trust_run_rent, which records the run; bills through trust_enter_recurring_bills; fees through
trust_take_monthly_management_fees. Then, for a company that turned them on, the emails of the
day (src/pms/notices.py), owners' accounts fallen below their reserve among them. A PMC whose run
fails is left for the next run, and its admins are emailed what failed (q149).

Each PMC in its own transaction, as trust_app, naming nobody (a run by hand is an admin's, at
Rent roll, Run rent now). Each part charges or enters once per date, so a run again (a restart,
this command, an admin's catch-up) charges nothing new. Only the management fees move trust
money, inside trust, from each owner's account to the company's fee account, once per account and
month (the founder's answer of 2026-10-18 to q049); charges are what tenants owe, and bills wait
for staff to pay them.

Each company's run is for its own day (src/pms/days.py, migration 20261104000900): the date in its
time zone. The API runs every company when it starts, then looks a minute past each hour and runs
each company whose day has begun since (just after its midnight), unless PMS_NIGHTLY is "off".
`python -m pms.nightly` runs every company once, each for its day, for a server that schedules it
itself. One run at a time,
whatever starts it (two API servers, or this command while the API runs): a run holds a database
lock while it lasts, and one that finds it held leaves the work to that run.

With a payments partner (PMS_PAYMENTS), each tenant's autopay payment due starts too, after the
rent is charged, and goes to the partner once the run commits (src/pms/autopay.py).

usage: python -m pms.nightly    (DATABASE_URL and PMS_PUBLIC_URL, acting as trust_app)
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql

from pms import autopay, days, email, notices, payments
from pms.api import payables, rent

log = logging.getLogger("pms.nightly")

# The Scheduler's own argument is named `payments`, so it names the module's helper by this.
resolver_of = payments.Resolver.of

# How long past each hour the scheduler looks for companies whose day has begun: every US time
# zone's midnight is on an hour, UTC.
PAST_THE_HOUR = timedelta(minutes=1)
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
    # Autopay payments started (migration 0151).
    autopay: int = 0
    # Whether the month's owner draws were prepared, on the company's draw day (migration 0153).
    draws_prepared: int = 0
    # Fixed-term leases that ran out with no renewal and went month to month (migration 0154).
    leases_rolled_over: int = 0


def run_pmc(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    day: date,
    public_url: str,
    sending: bool = False,
    partner: str | payments.Resolver | None = None,
) -> Ran:
    """One PMC's run for a day, in one transaction. While the server sends email, it also writes
    the rent reminders, late notices and owner statements the company's settings ask for. With a
    payments partner (its name), the autopay payments due start too, after the rent is charged
    (src/pms/autopay.py); they go to the partner once this commits."""
    with conn.transaction():
        # First, each fixed-term lease that ran out with no renewal signed goes month to month
        # (migration 0154), so the month's rent below charges it.
        rolled = conn.execute("SELECT trust_roll_over_leases(%s, %s)", (pmc_id, day)).fetchone()
        assert rolled is not None
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
        # On the draw day, every owner's draw, after the fees and bills above; nothing is paid
        # until staff pay them on Pay owners.
        draws = conn.execute("SELECT trust_prepare_owner_draws(%s, %s)", (pmc_id, day)).fetchone()
        assert draws is not None
        prepaid = rent.use_prepaid_by_itself(conn, pmc_id, day)
        # The company's partner: its own keys, else the server's.
        name = partner.name_for(conn, pmc_id) if isinstance(partner, payments.Resolver) else partner
        started = autopay.start_due(conn, pmc_id, day, name, sending, public_url)
        written = notices.write_scheduled(conn, pmc_id, day, public_url) if sending else None
    emails = started.emails + (0 if written is None else sum(vars(written).values()))
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
        started.payments,
        int(draws[0] is not None),
        rolled[0],
    )


def run_all(
    conn: psycopg.Connection[Any],
    day: date | None,
    public_url: str,
    pmcs: Iterable[UUID] | None = None,
    sending: bool = False,
    partner: str | payments.Resolver | None = None,
    at: datetime | None = None,
) -> list[Ran]:
    """Every PMC's run (or only those named) for a day, or with none for each one's own day at
    `at` (now by default). One that fails is logged and left for the next run, and while the
    server sends email its admins are told (once a day); the others still run. While another run
    holds the lock, this one runs nothing."""
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
            on = day or days.day_at(conn, pmc_id, at or datetime.now(UTC))
            conn.commit()
            try:
                done.append(run_pmc(conn, pmc_id, on, public_url, sending, partner))
            except Exception as failed:
                log.exception("the nightly run of PMC %s failed; the next run tries again", pmc_id)
                if sending:
                    alert(conn, pmc_id, on, failed, public_url)
        return done
    finally:
        if not conn.closed:
            conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (LOCK,))
            conn.commit()


def alert(
    conn: psycopg.Connection[Any], pmc_id: UUID, day: date, failed: Exception, public_url: str
) -> None:
    """Tells the PMC's admins its run failed, in a transaction of its own; when even that fails
    (the database down), the log is all there is."""
    what = f"{type(failed).__name__}: {str(failed).splitlines()[0] if str(failed) else ''}"
    try:
        with conn.transaction():
            email.write_admin_alert(conn, pmc_id, "nightly_failed", day, what, public_url)
    except Exception:
        log.exception("the admins of PMC %s could not be told its nightly run failed", pmc_id)


def seconds_until(now: datetime, past: timedelta = PAST_THE_HOUR) -> float:
    """From now to the next look: `past` after an hour (UTC), later than now."""
    look = now.replace(minute=0, second=0, microsecond=0) + past
    if look <= now:
        look += timedelta(hours=1)
    return (look - now).total_seconds()


def days_at(
    conn: psycopg.Connection[Any], at: datetime, pmcs: Sequence[UUID] | None = None
) -> dict[UUID, date]:
    """Each company's day at a moment (or only those named), in its own time zone."""
    rows = conn.execute(
        "SELECT pmc_id, trust_pmc_day(pmc_id, %(at)s) FROM trust_pmcs"
        " WHERE %(only)s::uuid[] IS NULL OR pmc_id = ANY(%(only)s) ORDER BY pmc_id",
        {"at": at, "only": None if pmcs is None else list(pmcs)},
    ).fetchall()
    conn.commit()
    return {pmc_id: day for pmc_id, day in rows}


class Scheduler:
    """Runs every PMC's run when started, each for its own day, then a minute past each hour the
    run of each PMC whose day has begun since, in a thread of its own with a connection of its
    own, until stopped. A look that fails (the database not up) is tried again `retry` seconds
    later; a company whose own run failed waits for its next day, as its admins are told. After
    each look, what waits for the payments partner and the email sender goes to them."""

    def __init__(
        self,
        database_url: str,
        role: str,
        public_url: str,
        sender: email.Sender | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        retry: float = RETRY,
        pmcs: Sequence[UUID] | None = None,
        payments: payments.Partner | payments.Resolver | None = None,
    ) -> None:
        self.database_url = database_url
        self.role = role
        self.public_url = public_url
        self.sender = sender
        self.clock = clock
        self.retry = retry
        self.pmcs = pmcs
        self.payments = None if payments is None else resolver_of(payments)
        # The day each company's run last began, in this process.
        self.days: dict[UUID, date] = {}
        self.ran = threading.Event()  # set after each run that finished
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="pms-nightly", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def run_once(self, begun_only: bool = False) -> list[Ran]:
        """Every company's run for its day at the clock's time, or with `begun_only` only those
        whose day has begun since their last run here."""
        at = self.clock()
        with psycopg.connect(self.database_url) as conn:
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(self.role)))
            conn.commit()
            due = days_at(conn, at, self.pmcs)
            if begun_only:
                due = {pmc: day for pmc, day in due.items() if self.days.get(pmc) != day}
            done = (
                run_all(
                    conn,
                    None,
                    self.public_url,
                    list(due),
                    self.sender is not None,
                    self.payments,
                    at=at,
                )
                if due
                else []
            )
            self.days.update(due)
            conn.autocommit = True
            if self.payments is not None:
                autopay.submit_waiting(conn, self.payments, now=self.clock())
            if self.sender is not None:
                email.deliver_pending(conn, self.sender)
        self.ran.set()
        return done

    def _loop(self) -> None:
        begun_only = False  # when started, every company runs
        while not self._stop.is_set():
            try:
                self.run_once(begun_only)
                begun_only = True
                wait = seconds_until(self.clock())
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
        # The server's partner, and each company's own keys entered in Settings.
        partner = payments.Resolver(
            payments.partner_named(os.environ.get("PMS_PAYMENTS", ""), os.environ)
        )
        done = run_all(conn, None, public_url, sending=sending, partner=partner)
        conn.autocommit = True
        autopay.submit_waiting(conn, partner)
    rents = sum(r.rents for r in done)
    late_fees = sum(r.late_fees for r in done)
    bills = sum(r.bills for r in done)
    emails = sum(r.emails for r in done)
    fees = sum(r.management_fees for r in done)
    orders = sum(r.work_orders for r in done)
    autopays = sum(r.autopay for r in done)
    print(
        f"{len(done)} companies: {rents} rents, {late_fees} late fees, {bills} bills,"
        f" {emails} emails, {fees} management fees, {orders} work orders,"
        f" {autopays} autopay payments"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
