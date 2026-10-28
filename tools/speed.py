"""How fast the API answers ten staff at once on a 1,000-unit company: the nightly speed test
(the founder's review q148).

`python -m tools.speed` builds, in a throwaway database on the server in $DATABASE_URL, the
demo's books (pms.demo, synthetic) and beside them, in trust bank accounts of their own,
properties of 20 units until the company has 1,000 units: every new unit leased for a year, its
deposit held, and each month's rent charged and paid but a tenth of this month's, all through
the API's own route functions as the manager. It then serves the API as `python -m pms.api`
does, in a process of its own whose pool of ten database connections everyone shares, and ten
staff members sign in over HTTP (invite, password and authenticator), each in a client of their
own. Each opens every page once, then at random what staff open most for the rest of the run:
the Dashboard and what needs attention, a page of properties, a property, a page of the rent
roll, a lease and its ledger, a page of tenants, a search, a page of the general ledger, a
report on screen, and now and then a report as a PDF; and they record payments. Between pages
each pauses as a person reading one would, between none and twice --pause seconds (none: as
fast as the server answers). Each answer's time is kept by what was asked. An answer that isn't
2xx fails the run, and so does anything whose 95th percentile is over its ceiling in
ci/thresholds.toml ([speed]).

usage: python -m tools.speed [--units 1000] [--staff 10] [--months 12] [--seconds 120]
                             [--pause 1] [--seed 0]
"""

from __future__ import annotations

import argparse
import dataclasses
import math
import os
import random
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import IO, Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx2
import psycopg
import pyotp

from pms import accounts, demo
from pms.api import rent, setup, staff
from tools.check_rollback import Dbmate
from tools.contention import as_app
from tools.scratch_db import scratch_database

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "db/migrations"
THRESHOLDS = ROOT / "ci/thresholds.toml"
PER_PROPERTY = 20
# How long the server may take to answer its health check once started, and to stop once asked.
STARTING = 60.0
STOPPING = 10.0
# Downloads are the one thing timed against their own ceiling; everything else is a page or a
# payment, against the pages' ceiling.
DOWNLOAD = "download"


@dataclass
class Company:
    """What the speed test built: the PMC, its manager and what staff open."""

    pmc_id: UUID
    manager: UUID
    units: int
    properties: list[UUID] = field(default_factory=list)
    leases: list[UUID] = field(default_factory=list)
    # Names to search for: properties and tenants.
    names: list[str] = field(default_factory=list)
    ledger_accounts: int = 0


def count(conn: psycopg.Connection[Any], query: str, pmc_id: UUID) -> int:
    return int(demo.one(conn, query, (pmc_id,)))


def build(conn: psycopg.Connection[Any], units: int, months: int, today: date) -> Company:
    """The demo's books, then properties of PER_PROPERTY units until the company has `units`,
    leased from the first of the month `months` back, each month's rent charged and paid (a
    tenth of this month's left owing). Through the routes, as the manager; on a connection
    acting as trust_app (autocommit)."""
    books = demo.books(conn, today)
    b = demo.Builder(conn, books.pmc_id)
    manager = books.manager
    # Accounts of their own, so a year back is open: the demo closed its own through a month.
    with b.as_staff(manager) as s:
        operating = demo.uid(
            setup.add_bank_account(
                s, conn, setup.NewBankAccount(kind="operating", name="Speed operating account")
            )
        )
        deposits = demo.uid(
            setup.add_bank_account(
                s,
                conn,
                setup.NewBankAccount(kind="security_deposit", name="Speed deposits account"),
            )
        )
    ours = dataclasses.replace(books, operating=operating)
    start = demo.first_of_month(today, months - 1)
    have = count(conn, "SELECT count(*) FROM trust_units WHERE pmc_id = %s", books.pmc_id)
    owners = list(demo.TERMS)
    leases: list[UUID] = []
    names: list[str] = []
    while have + len(leases) < units:
        n = len(leases) // PER_PROPERTY + 1
        owner = owners[n % len(owners)]
        prop = f"Speed Court {n:03d}"
        property_id = demo.new_property(b, ours, owner, prop, start, demo.TERMS[owner])
        names.append(prop)
        with b.as_staff(manager) as s:
            for u in range(1, min(PER_PROPERTY, units - have - len(leases)) + 1):
                unit = demo.uid(
                    setup.add_unit(
                        s, conn, setup.NewUnit(property_id=property_id, name=f"Unit {u:02d}")
                    )
                )
                name = f"Speed Tenant {len(leases) + 1:04d}"
                tenant = demo.uid(
                    setup.add_tenant(s, conn, setup.NewUnit(property_id=property_id, name=name))
                )
                monthly = Decimal(1000 + (len(leases) * 37) % 900)
                leases.append(
                    demo.uid(
                        setup.add_lease(
                            s,
                            conn,
                            setup.NewLease(
                                unit_id=unit,
                                starts_on=start,
                                monthly_rent=monthly,
                                tenant_ids=[tenant],
                            ),
                        )
                    )
                )
                rent.deposit(
                    s,
                    conn,
                    rent.NewDeposit(
                        request_id=uuid.uuid4(),
                        tenant_id=tenant,
                        amount=monthly,
                        received_on=start,
                        bank_account_id=deposits,
                    ),
                )
                names.append(name)
    for back in range(months - 1, -1, -1):
        month = demo.first_of_month(today, back)
        with b.as_staff(manager) as s:
            rent.run_rent(s, conn, rent.RunRent(due_on=month, late_fees_as_of=month))
        for i, lease in enumerate(leases):
            if back == 0 and i % 10 == 9:
                continue  # a tenth owe this month's rent
            day = demo.on_or_before(month + (1 + i % 4) * demo.DAY, today)
            with b.as_staff(manager) as s:
                rent.pay(s, conn, lease, demo.payment(conn, lease, day))
    company = Company(books.pmc_id, manager, units=have + len(leases), names=names)
    company.properties = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM trust_properties WHERE pmc_id = %s ORDER BY id", (books.pmc_id,)
        )
    ]
    company.leases = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM trust_leases WHERE pmc_id = %s ORDER BY id", (books.pmc_id,)
        )
    ]
    company.ledger_accounts = count(
        conn, "SELECT count(*) FROM trust_ledger_accounts WHERE pmc_id = %s", books.pmc_id
    )
    return company


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class ServerDown(Exception):
    """The API never answered its health check."""


@dataclass
class Server:
    origin: str
    log: IO[bytes]
    pid: int = 0

    def tail(self, lines: int = 30) -> str:
        self.log.flush()
        self.log.seek(0)
        return "\n".join(self.log.read().decode(errors="replace").splitlines()[-lines:])


@contextmanager
def serving(url: str, command: Sequence[str] | None = None) -> Iterator[Server]:
    """The API serving the database at `url` as `python -m pms.api` does, in a process of its
    own, until the block ends: the nightly run off, the cookie over plain HTTP on 127.0.0.1."""
    port = free_port()
    origin = f"http://127.0.0.1:{port}"
    env = {k: v for k, v in os.environ.items() if not k.startswith("PMS_")}
    env |= {
        # The URL goes to the server through the environment, never the command line.
        "DATABASE_URL": url,
        "PMS_HOST": "127.0.0.1",
        "PMS_PORT": str(port),
        "PMS_PUBLIC_URL": origin,
        "PMS_COOKIE_SECURE": "false",
        "PMS_NIGHTLY": "off",
        "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT / "src"), env.get("PYTHONPATH")])),
    }
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(  # noqa: S603 (this interpreter, our own module)
            list(command or [sys.executable, "-m", "pms.api"]),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        server = Server(origin, log, process.pid)
        try:
            up_by = time.monotonic() + STARTING
            while not healthy(origin):
                if process.poll() is not None or time.monotonic() > up_by:
                    raise ServerDown(f"the API didn't start:\n{server.tail()}")
                time.sleep(0.2)
            yield server
        finally:
            process.terminate()
            try:
                process.wait(timeout=STOPPING)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def healthy(origin: str) -> bool:
    try:
        return httpx2.get(f"{origin}/health", timeout=2).status_code == 200
    except httpx2.TransportError:
        return False


@dataclass
class Tally:
    """Each answer's time by what was asked, and every failure."""

    seconds: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    failures: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, kind: str, took: float, failure: str | None = None) -> None:
        with self.lock:
            self.seconds[kind].append(took)
            if failure is not None:
                self.failures.append(f"{kind}: {failure}")


Request = tuple[str, str, dict[str, Any] | None]


class Staffer:
    """One staff member signed in to the API in a client of their own."""

    def __init__(
        self,
        conn: psycopg.Connection[Any],
        company: Company,
        origin: str,
        n: int,
        rng: random.Random,
        today: date,
    ) -> None:
        self.company = company
        self.rng = rng
        self.today = today
        b = demo.Builder(conn, company.pmc_id)
        with b.as_staff(company.manager) as s:
            member = staff.add(s, conn, staff.NewMember(name=f"Speed Staff {n}")).id
            invite = accounts.create_invite(
                conn, company.pmc_id, "staff", member, f"speed.staff{n}@example.com", s.person_id
            )
        # Every change is JSON from the app's own origin, as the portal sends it.
        self.client = httpx2.Client(base_url=origin, headers={"Origin": origin}, timeout=300)
        # A throwaway password for a synthetic login in a throwaway database.
        password = secrets.token_urlsafe(16)
        accepted = self.client.post(
            "/api/invites/accept", json={"token": invite.token, "password": password}
        )
        accepted.raise_for_status()
        uri = accepted.json()["totp_uri"]
        code = pyotp.TOTP(parse_qs(urlsplit(uri).query)["secret"][0]).now()
        self.client.post(
            "/api/invites/confirm", json={"token": invite.token, "code": code}
        ).raise_for_status()

    def page(self, per_page: int, rows: int) -> int:
        return self.rng.randint(1, max(1, math.ceil(rows / per_page)))

    def requests(self) -> dict[str, tuple[int, Callable[[], Request]]]:
        """What staff open, each with its weight, as the portal asks for it."""
        c, rng = self.company, self.rng
        month = self.today.replace(day=1).isoformat()
        today = self.today.isoformat()

        def report() -> Request:
            name = rng.choice(["rent_roll", "delinquency"])
            return "GET", f"/api/reports/trust_report_{name}?format=json&arg={today}", None

        def download() -> Request:
            args = rng.choice([f"rent_roll?arg={today}", f"general_ledger?arg={month}&arg={today}"])
            return "GET", f"/api/reports/trust_report_{args}&format=pdf", None

        def payment() -> Request:
            body = {"request_id": str(uuid.uuid4()), "amount": "25.00", "received_on": today}
            return "POST", f"/api/rent/leases/{rng.choice(c.leases)}/payments", body

        return {
            "dashboard": (2, lambda: ("GET", "/api/dashboard", None)),
            "today": (2, lambda: ("GET", "/api/today", None)),
            "properties": (
                3,
                lambda: ("GET", f"/api/properties?page={self.page(50, len(c.properties))}", None),
            ),
            "property": (3, lambda: ("GET", f"/api/properties/{rng.choice(c.properties)}", None)),
            "rent roll": (
                4,
                lambda: ("GET", f"/api/leases?page={self.page(50, len(c.leases))}", None),
            ),
            "lease": (5, lambda: ("GET", f"/api/leases/{rng.choice(c.leases)}", None)),
            "lease ledger": (
                4,
                lambda: ("GET", f"/api/leases/{rng.choice(c.leases)}/ledger", None),
            ),
            "tenants": (
                3,
                lambda: ("GET", f"/api/tenants?page={self.page(50, len(c.leases))}", None),
            ),
            "search": (3, lambda: ("GET", "/api/search", {"q": rng.choice(c.names)})),
            "general ledger": (
                2,
                lambda: (
                    "GET",
                    f"/api/general-ledger?from={month}&to={today}"
                    f"&page={self.page(25, c.ledger_accounts)}",
                    None,
                ),
            ),
            "report": (2, report),
            "payment": (4, payment),
            DOWNLOAD: (1, download),
        }

    def ask(self, kind: str, request: Request, tally: Tally) -> None:
        method, path, body = request
        started = time.perf_counter()
        try:
            if method == "GET":
                answer = self.client.get(path, params=body)
            else:
                answer = self.client.post(path, json=body)
        except httpx2.HTTPError as error:
            tally.add(kind, time.perf_counter() - started, f"{type(error).__name__} on {path}")
            return
        took = time.perf_counter() - started
        failed = None if answer.is_success else f"{answer.status_code} on {method} {path}"
        tally.add(kind, took, failed)

    def run(
        self,
        until: float,
        tally: Tally,
        clock: Callable[[], float] = time.monotonic,
        pause: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Every page once, in an order of its own, then at random until `until`, pausing
        between none and twice `pause` seconds after each."""
        asks = self.requests()
        kinds = list(asks)
        first = self.rng.sample(kinds, len(kinds))
        weights = [asks[kind][0] for kind in kinds]
        try:
            for kind in first:
                self.ask(kind, asks[kind][1](), tally)
                sleep(self.rng.uniform(0, 2 * pause))
            while clock() < until:
                kind = self.rng.choices(kinds, weights)[0]
                self.ask(kind, asks[kind][1](), tally)
                sleep(self.rng.uniform(0, 2 * pause))
        finally:
            self.client.close()


@dataclass
class Result:
    company: Company
    tally: Tally
    built_in: float
    server_log: str = ""


def run(
    url: str, units: int, staff_count: int, months: int, seconds: float, seed: int, pause: float
) -> Result:
    """Builds the company on a migrated database, serves it, signs the staff in and has them
    work for `seconds`, pausing about `pause` seconds between pages."""
    today = datetime.now(UTC).date()
    started = time.monotonic()
    with as_app(url) as conn:
        company = build(conn, units, months, today)
        built_in = time.monotonic() - started
        tally = Tally()
        with serving(url) as server:
            # A run's choices, no secret: a seeded generator repeats them.
            rngs = [random.Random(seed * 1000 + n) for n in range(staff_count)]  # noqa: S311
            crew = [
                Staffer(conn, company, server.origin, n + 1, rng, today)
                for n, rng in enumerate(rngs)
            ]
            until = time.monotonic() + seconds
            threads = [
                threading.Thread(target=s.run, args=(until, tally, time.monotonic, pause))
                for s in crew
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            log = server.tail() if tally.failures else ""
    return Result(company, tally, built_in, log)


def ceilings(path: Path = THRESHOLDS) -> dict[str, float]:
    """The 95th percentiles in milliseconds nothing may go over: downloads', and the pages'
    (payments included)."""
    speed = tomllib.loads(path.read_text())["speed"]
    return {"page": float(speed["page_p95_ms_max"]), DOWNLOAD: float(speed["download_p95_ms_max"])}


def ceiling_of(kind: str, limits: dict[str, float]) -> float:
    return limits[DOWNLOAD] if kind == DOWNLOAD else limits["page"]


def percentile(values: list[float], share: float) -> float:
    """The nearest-rank percentile: the smallest value at least `share` of them are at or
    under."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def slow(tally: Tally, limits: dict[str, float]) -> list[str]:
    found = []
    for kind, times in sorted(tally.seconds.items()):
        p95 = percentile(times, 0.95) * 1000
        if p95 > ceiling_of(kind, limits):
            found.append(f"{kind}: p95 {p95:,.0f} ms is over {ceiling_of(kind, limits):,.0f} ms")
    return found


def report(result: Result, limits: dict[str, float], seconds: float) -> str:
    tally = result.tally
    asked = sum(len(times) for times in tally.seconds.values())
    lines = [
        f"{result.company.units:,} units, {len(result.company.leases):,} leases, built in"
        f" {result.built_in:,.0f} s; {asked:,} answers in {seconds:,.0f} s",
        f"{'asked':<15}{'count':>7}{'p50 ms':>9}{'p95 ms':>9}{'max ms':>9}{'ceiling':>9}",
    ]
    for kind, times in sorted(tally.seconds.items()):
        p50, p95 = (percentile(times, share) * 1000 for share in (0.5, 0.95))
        lines.append(
            f"{kind:<15}{len(times):>7,}{p50:>9,.0f}{p95:>9,.0f}{max(times) * 1000:>9,.0f}"
            f"{ceiling_of(kind, limits):>9,.0f}"
        )
    lines += [f"FAILED {failure}" for failure in tally.failures]
    lines += [f"SLOW {problem}" for problem in slow(tally, limits)]
    if not asked:
        lines.append("FAILED nothing was asked: the run timed nothing")
    if result.server_log:
        lines += ["the server's last lines:", result.server_log]
    return "\n".join(lines)


def passed(tally: Tally, limits: dict[str, float]) -> bool:
    asked = any(tally.seconds.values())
    return asked and not tally.failures and not slow(tally, limits)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--units", type=int, default=1000)
    parser.add_argument("--staff", type=int, default=10)
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--pause", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    server = os.environ.get("DATABASE_URL")
    dbmate = shutil.which("dbmate")
    if not server or not dbmate:
        print("FAIL: needs DATABASE_URL and dbmate on PATH.")
        return 1
    limits = ceilings()
    with scratch_database(server, "pms_speed") as url:
        migrated = Dbmate(dbmate, MIGRATIONS, url).run("--no-dump-schema", "up")
        if migrated.returncode != 0:
            print(f"FAIL: dbmate up failed:\n{migrated.stderr}")
            return 1
        try:
            result = run(
                url, args.units, args.staff, args.months, args.seconds, args.seed, args.pause
            )
        except ServerDown as down:
            print(f"FAIL: {down}")
            return 1
    print(report(result, limits, args.seconds))
    return 0 if passed(result.tally, limits) else 1


if __name__ == "__main__":
    sys.exit(main())
