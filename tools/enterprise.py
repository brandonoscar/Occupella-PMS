"""Enterprise size: a company of 20,000 units brought over from another system through the
switching tool, then timed as it works (the founder, 2026-10-25: tests an enterprise could trust).

`python -m tools.enterprise` makes, in a throwaway database on the server in $DATABASE_URL:

  1. the company's files as the switching tool takes them (src/pms/api/switching.py), all
     synthetic: owners, vendors, properties of 20 units, units, tenants (one in five homes with
     two), leases (some ending within the year), last month's rent still owed on a tenth of
     them, the old system's unpaid bills, and five years of history: each home's rent paid every
     month, each property's owner paid every month and each vendor paid every month, each kind
     in files of at most the tool's 20,000 rows;
  2. imports them as the manager does on Settings, Import (the route functions), then opens its
     two trust bank accounts with what each account held (the owners', tenants' prepaid rent and
     deposits, the company's fees), checked against the old book cash, and signs off the tie-out;
  3. runs tonight's nightly run on it (pms.nightly.run_pmc): the month's rent for every lease,
     the late fees of what is past its grace, everything else the run does;
  4. runs the reports staff open most on it (the rent roll, delinquency, the trial balance, the
     balance sheet, owners' balances, a month of the general ledger) and every owner's statement;
  5. puts 50 staff to work on it over HTTP for five minutes (or an hour: --seconds 3600), as the
     speed test's staff work (tools/speed.py), with the nightly run running again and again under
     them, and watches the API's answers and memory and the database's connections over the time;
  6. backs it up with scripts/backup.sh and restores the file into an empty database.

Each step is timed, and the run fails when one is over its ceiling in ci/thresholds.toml
([enterprise]), or when the books are wrong after it: the opening balances or the tie-out not
taken, rent not charged once to every lease in effect, the trial balance not balancing, a trust
bank account's book cash not what it holds, a staff request failing or a page's 95th percentile
over the speed test's ceiling, a nightly run failing under staff, the answers slowing or the
API's memory or the database's connections growing from the first part of the work to the last
past their ceilings, the restored copy not holding every transfer.

usage: PYTHONPATH=src python -m tools.enterprise [--units 20000] [--seed 0] [--staff 50]
           [--seconds 300] [--history-years 5]
       (a throwaway database on the server in $DATABASE_URL; dbmate and pg_dump 16 on PATH)
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import tomllib
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
from pms.api import setup, switching
from tools import speed
from tools.check_backup import backup
from tools.check_rollback import Dbmate
from tools.contention import as_app
from tools.scratch_db import scratch_database

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "db/migrations"
THRESHOLDS = ROOT / "ci/thresholds.toml"
PER_PROPERTY = 20
UNITS_PER_OWNER = 50
VENDORS = 200
OLD_BILLS = 300
HISTORY_YEARS = 5
WINDOWS = 6
URL = "http://localhost:8000"
OPERATING, DEPOSITS = "Enterprise operating account", "Enterprise deposits account"


class Wrong(AssertionError):
    """Books left wrong by a step, or a step over its ceiling."""


@dataclass
class Timings:
    steps: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        began = time.monotonic()
        yield
        self.steps[name] = time.monotonic() - began
        print(f"  {name}: {self.steps[name]:.1f} s", flush=True)


@dataclass
class Files:
    """The company's files, as CSV text, by kind, in the order they go in."""

    kinds: list[tuple[switching.Kind, list[str]]] = field(default_factory=list)
    operating: list[dict[str, str]] = field(default_factory=list)
    deposits: list[dict[str, str]] = field(default_factory=list)
    leases: int = 0


def csv_text(header: list[str], lines: list[list[str]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(lines)
    return out.getvalue()


def chunked(header: list[str], lines: list[list[str]], most: int = switching.MAX_ROWS) -> list[str]:
    """One kind's lines in files the switching tool takes whole: at most its rows, and well under
    its size."""
    files, start = [], 0
    while start < len(lines):
        size = most
        while True:
            text = csv_text(header, lines[start : start + size])
            if len(text) < 1_900_000 or size == 1:
                break
            size //= 2
        files.append(text)
        start += size
    return files


def money(cents: int) -> str:
    return f"{Decimal(cents) / 100:.2f}"


def month_before(first: date, months: int) -> date:
    """The first of the month `months` before the month starting on `first`."""
    index = first.year * 12 + first.month - 1 - months
    return date(index // 12, index % 12 + 1, 1)


def company(
    units: int, today: date, rng: random.Random, history_years: int = HISTORY_YEARS
) -> Files:
    """A synthetic company of `units` homes, as an old system's export would have it."""
    files = Files()
    owners = [f"Owner {n:04d}" for n in range(1, max(1, units // UNITS_PER_OWNER) + 1)]
    vendors = [f"Vendor {n:03d}" for n in range(1, VENDORS + 1)]
    this_month = today.replace(day=1)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    props: list[list[str]] = []
    unit_lines: list[list[str]] = []
    tenant_lines: list[list[str]] = []
    lease_lines: list[list[str]] = []
    charge_lines: list[list[str]] = []
    history: list[list[str]] = []
    owner_history: list[list[str]] = []
    months = [month_before(this_month, m) for m in range(1, 12 * history_years + 1)]
    for p in range(units // PER_PROPERTY):
        name = f"Property {p + 1:04d}"
        owner = owners[p % len(owners)]
        props.append([name, owner, OPERATING])
        files.operating.append(
            {
                "kind": "owner",
                "owner": owner,
                "property": name,
                "amount": money(rng.randint(50_000, 2_000_000)),
            }
        )
        for u in range(1, PER_PROPERTY + 1):
            unit = f"Unit {u:02d}"
            unit_lines.append([name, unit])
            people = [f"Tenant {p + 1:04d}-{u:02d}"]
            if rng.random() < 0.2:
                people.append(f"Tenant {p + 1:04d}-{u:02d}b")
            for n, person in enumerate(people):
                tenant_lines.append([name, person, f"t{p + 1:04d}{u:02d}{n}@example.com"])
            rent = rng.randint(900, 2400) * 100
            starts = last_month - timedelta(days=rng.randint(1, 1060))
            ends = (
                ""
                if rng.random() < 0.7
                else (today + timedelta(days=rng.randint(10, 365))).isoformat()
            )
            lease_lines.append(
                [name, unit, ";".join(people), starts.isoformat(), ends, money(rent)]
            )
            files.deposits.append(
                {"kind": "deposit", "property": name, "tenant": people[0], "amount": money(rent)}
            )
            if rng.random() < 0.05:
                files.operating.append(
                    {
                        "kind": "prepaid rent",
                        "property": name,
                        "tenant": people[0],
                        "amount": money(rng.randint(5_000, 50_000)),
                    }
                )
            if rng.random() < 0.1:
                charge_lines.append(
                    [
                        name,
                        unit,
                        last_month.isoformat(),
                        money(rent),
                        "rent",
                        "rent the old system had not collected",
                    ]
                )
            history.extend(
                [name, people[0], month.isoformat(), "Payment", "Rent", money(rent)]
                for month in months
            )
        owner_history.extend(
            [
                owner,
                name,
                month.isoformat(),
                "Owner draw",
                "Monthly draw",
                money(rng.randint(1, 9) * 100_000),
                OPERATING,
            ]
            for month in months
        )
    files.operating.append({"kind": "fees", "amount": money(rng.randint(100_000, 1_000_000))})
    bills = []
    for b in range(OLD_BILLS):
        p = rng.randrange(len(props))
        bills.append(
            [
                vendors[b % len(vendors)],
                props[p][1],
                props[p][0],
                f"OLD-{b + 1:04d}",
                last_month.isoformat(),
                (this_month + timedelta(days=14)).isoformat(),
                money(rng.randint(5_000, 90_000)),
                "a bill the old system had not paid",
                OPERATING,
            ]
        )
    files.leases = len(lease_lines)
    files.kinds = [
        (
            "owners",
            chunked(
                ["name", "email"],
                [[o, f"{o.lower().replace(' ', '')}@example.com"] for o in owners],
            ),
        ),
        ("vendors", chunked(["name"], [[v] for v in vendors])),
        ("properties", chunked(["property", "owner", "bank account"], props)),
        ("units", chunked(["property", "unit"], unit_lines)),
        ("tenants", chunked(["property", "tenant", "email"], tenant_lines)),
        (
            "leases",
            chunked(
                ["property", "unit", "tenants", "starts on", "ends on", "monthly rent"], lease_lines
            ),
        ),
        (
            "charges",
            chunked(["property", "unit", "due on", "amount", "kind", "memo"], charge_lines),
        ),
        (
            "bills",
            chunked(
                [
                    "vendor",
                    "owner",
                    "property",
                    "reference",
                    "bill date",
                    "due on",
                    "amount",
                    "memo",
                    "bank account",
                ],
                bills,
            ),
        ),
        (
            "tenant history",
            chunked(["property", "tenant", "date", "type", "description", "amount"], history),
        ),
        (
            "owner history",
            chunked(
                ["owner", "property", "date", "type", "description", "amount", "bank account"],
                owner_history,
            ),
        ),
        (
            "vendor history",
            chunked(
                ["vendor", "date", "type", "description", "amount"],
                [
                    [
                        v,
                        month.isoformat(),
                        "Bill paid",
                        "Monthly service",
                        money(rng.randint(5, 90) * 1000),
                    ]
                    for v in vendors
                    for month in months
                ],
            ),
        ),
    ]
    return files


def scalar(conn: psycopg.Connection[Any], query: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(query, params).fetchone()
    assert row is not None
    return row[0]


def opening_csv(lines: list[dict[str, str]]) -> str:
    header = ["kind", "owner", "property", "tenant", "vendor", "amount"]
    return csv_text(header, [[line.get(h, "") for h in header] for line in lines])


def bring_over(
    conn: psycopg.Connection[Any], built: demo.Demo, files: Files, today: date, t: Timings
) -> dict[str, UUID]:
    """The company's files in, as the manager brings them on Settings, Import."""
    b = demo.Builder(conn, built.pmc_id)
    banks: dict[str, UUID] = {}
    with b.as_staff(built.manager) as s:
        banks["operating"] = UUID(
            str(
                setup.add_bank_account(
                    s, conn, setup.NewBankAccount(kind="operating", name=OPERATING)
                ).id
            )
        )
    with b.as_staff(built.manager) as s:
        banks["deposits"] = UUID(
            str(
                setup.add_bank_account(
                    s, conn, setup.NewBankAccount(kind="security_deposit", name=DEPOSITS)
                ).id
            )
        )
    for kind, texts in files.kinds:
        with t.step(f"import {kind} ({len(texts)} file{'s' if len(texts) > 1 else ''})"):
            for text in texts:
                with b.as_staff(built.manager) as s:
                    switching.import_file(s, conn, switching.Import(kind=kind, content=text))
    opened = today.replace(day=1)
    for name, lines in (("operating", files.operating), ("deposits", files.deposits)):
        cash = sum((Decimal(line["amount"]) for line in lines), Decimal(0))
        with t.step(f"opening balances, {name} ({len(lines)} accounts)"):
            with b.as_staff(built.manager) as s:
                switching.open_balances(
                    s,
                    conn,
                    switching.Opening(
                        bank_account_id=banks[name],
                        opened_on=opened,
                        book_cash=cash,
                        content=opening_csv(lines),
                    ),
                )
            with b.as_staff(built.manager) as s:
                switching.sign_off(s, conn, switching.SignOff(bank_account_id=banks[name]))
    return banks


REPORTS: list[tuple[str, str]] = [
    ("rent roll", "SELECT count(*) FROM trust_report_rent_roll(%(pmc)s, %(day)s)"),
    ("delinquency", "SELECT count(*) FROM trust_report_delinquency(%(pmc)s, %(day)s)"),
    ("trial balance", "SELECT count(*) FROM trust_report_trial_balance(%(pmc)s, %(day)s)"),
    ("balance sheet", "SELECT count(*) FROM trust_report_balance_sheet(%(pmc)s, %(day)s)"),
    ("owners' balances", "SELECT count(*) FROM trust_report_owner_balances(%(pmc)s, %(day)s)"),
    (
        "general ledger, this month",
        "SELECT count(*) FROM trust_report_general_ledger(%(pmc)s, %(first)s, %(day)s)",
    ),
]


def check_books(conn: psycopg.Connection[Any], pmc: UUID, day: date, leases_in_effect: int) -> None:
    charged = scalar(
        conn,
        "SELECT count(DISTINCT lease_id) FROM trust_charges WHERE pmc_id = %s AND kind = 'rent'"
        " AND due_on >= %s",
        (pmc, day.replace(day=1)),
    )
    if charged < leases_in_effect:
        raise Wrong(
            f"this month's rent went to {charged} of the {leases_in_effect} leases in effect"
        )
    twice = scalar(
        conn,
        "SELECT count(*) FROM (SELECT 1 FROM trust_charges WHERE pmc_id = %s AND kind = 'rent'"
        " GROUP BY lease_id, due_on HAVING count(*) > 1) AS d",
        (pmc,),
    )
    if twice:
        raise Wrong(f"{twice} leases were charged rent twice for a due date")
    debits, credits = conn.execute(
        "SELECT coalesce(sum(debit), 0), coalesce(sum(credit), 0)"
        " FROM trust_report_trial_balance(%s, %s)",
        (pmc, day),
    ).fetchone() or (None, None)
    if debits != credits:
        raise Wrong(f"the trial balance has debits {debits} and credits {credits}")
    off = scalar(
        conn,
        "SELECT count(*) FROM trust_report_balance_sheet(%s, %s)"
        " WHERE item = 'difference' AND amount <> 0",
        (pmc, day),
    )
    if off:
        raise Wrong(f"{off} trust bank accounts' book cash isn't what they hold")


def limits(path: Path = THRESHOLDS) -> dict[str, float]:
    return {k: float(v) for k, v in tomllib.loads(path.read_text())["enterprise"].items()}


def over(t: Timings, ceilings: dict[str, float]) -> list[str]:
    """Each step over its ceiling: a `<words>_seconds_max` is the ceiling of every step whose
    name starts with those words."""
    found = []
    for key, limit in ceilings.items():
        if not key.endswith("_seconds_max"):
            continue
        prefix = key.removesuffix("_seconds_max").replace("_", " ")
        for name, spent in t.steps.items():
            if name.startswith(prefix) and spent > limit:
                found.append(f"{name}: {spent:.0f} s, over its ceiling of {limit:.0f} s")
    return found


def run(
    url: str,
    units: int,
    seed: int,
    t: Timings,
    crew_size: int = 50,
    seconds: float = 300,
    pause: float = 1.0,
    history_years: int = HISTORY_YEARS,
) -> dict[str, Any]:
    """Steps 1 to 5 on a migrated database; what was made and measured."""
    today = datetime.now(UTC).date()
    rng = random.Random(seed)  # noqa: S311 - replayable, not secret
    with t.step("make the files"):
        files = company(units, today, rng, history_years)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier("trust_app")))
        with t.step("the demo's books, for the company and its manager"):
            built = demo.books(conn, today)
        bring_over(conn, built, files, today, t)
        in_effect = scalar(
            conn,
            "SELECT count(*) FROM trust_leases WHERE pmc_id = %s AND starts_on <= %s"
            " AND (ends_on IS NULL OR ends_on >= %s)",
            (built.pmc_id, today.replace(day=1), today.replace(day=1)),
        )
        with t.step("nightly run tonight"):
            ran = nightly.run_pmc(conn, built.pmc_id, today, URL)
        check_books(conn, built.pmc_id, today, in_effect)
        params = {"pmc": built.pmc_id, "day": today, "first": today.replace(day=1)}
        for name, query in REPORTS:
            with t.step(f"report {name}"):
                conn.execute(query, params).fetchone()
        owners = [
            r[0]
            for r in conn.execute("SELECT id FROM trust_owners WHERE pmc_id = %s", (built.pmc_id,))
        ]
        with t.step(f"report every owner's statement ({len(owners)})"):
            for owner in owners:
                conn.execute(
                    "SELECT count(*) FROM trust_report_owner_statement(%s, %s, %s, %s)",
                    (
                        built.pmc_id,
                        owner,
                        datetime(today.year, today.month, 1, tzinfo=UTC),
                        datetime.now(UTC),
                    ),
                ).fetchone()
        with t.step(f"staff at work ({crew_size} at once, nightly runs under them)"):
            worked = work(conn, url, built, units, crew_size, seconds, pause, seed, today)
        check_books(conn, built.pmc_id, today, in_effect)
        size = scalar(conn, "SELECT pg_database_size(current_database())")
    if worked.night_failures:
        raise Wrong(f"a nightly run under staff's work failed: {worked.night_failures[0]}")
    if worked.tally.failures:
        raise Wrong(
            f"{len(worked.tally.failures)} staff requests failed, e.g. {worked.tally.failures[0]}"
        )
    slow = speed.slow(worked.tally, speed.ceilings())
    if slow:
        raise Wrong("staff's pages were slow: " + "; ".join(slow))
    answers = sum(len(v) for v in worked.tally.seconds.values())
    p95 = {
        k: round(speed.percentile(v, 0.95) * 1000) for k, v in sorted(worked.tally.seconds.items())
    }
    print(f"  staff: {answers} answers; p95 ms by page: {p95}")
    longest = max(worked.nights, default=0)
    print(f"  nightly runs under them: {len(worked.nights)}, longest {longest:.1f} s")
    for n, w in enumerate(worked.windows, 1):
        print(
            f"  part {n} of {len(worked.windows)}: p95 {w.p95_ms:.0f} ms, API memory"
            f" {w.rss_mb:.0f} MB, database connections {w.connections}"
        )
    growth = grown(worked.windows, limits())
    if growth:
        raise Wrong("over the work, " + "; ".join(growth))
    return {
        "units": units,
        "leases": files.leases,
        "in effect": in_effect,
        "rents": ran.rents,
        "late fees": ran.late_fees,
        "database MB": size // 1_000_000,
    }


@dataclass
class Window:
    """One part of staff's work: the answers' 95th percentile, and at its end the API's memory
    and the database's connections."""

    p95_ms: float
    rss_mb: float
    connections: int


@dataclass
class Worked:
    """Staff's work over the HTTP API, the nightly runs made while they worked, and how each part
    of the time went."""

    tally: speed.Tally
    nights: list[float] = field(default_factory=list)
    night_failures: list[str] = field(default_factory=list)
    windows: list[Window] = field(default_factory=list)


def rss_mb(pid: int) -> float:
    """A process's resident memory in MB, as Linux counts it (0 where /proc has none)."""
    try:
        status = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return 0.0
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return 0.0


def grown(windows: list[Window], limits: dict[str, float]) -> list[str]:
    """What grew from the first part of the work to the last past its ceiling: the answers'
    95th percentile (from at least 100 ms, so a fast start's noise isn't growth), the API's
    memory, the database's connections."""
    if len(windows) < 2:
        return []
    first, last = windows[0], windows[-1]
    found = []
    if last.p95_ms > limits["soak_p95_growth_max"] * max(first.p95_ms, 100.0):
        found.append(f"the answers' p95 went from {first.p95_ms:.0f} ms to {last.p95_ms:.0f} ms")
    if first.rss_mb and last.rss_mb > limits["soak_memory_growth_max"] * first.rss_mb:
        found.append(f"the API's memory went from {first.rss_mb:.0f} MB to {last.rss_mb:.0f} MB")
    if last.connections > limits["soak_connections_growth_max"] * max(first.connections, 1):
        found.append(
            f"the database's connections went from {first.connections} to {last.connections}"
        )
    return found


def work(
    conn: psycopg.Connection[Any],
    url: str,
    built: demo.Demo,
    units: int,
    crew_size: int,
    seconds: float,
    pause: float,
    seed: int,
    today: date,
) -> Worked:
    """`crew_size` staff signed in over HTTP using the company for `seconds` as the speed test's
    staff do (tools/speed.py: every page, then what staff open most, and payments), while the
    nightly run runs again and again on a connection of its own (charging nothing new once it
    has, as a run again does): many staff at once with the night's work under them."""
    pmc = built.pmc_id
    company = speed.Company(pmc, built.manager, units)
    company.properties = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM trust_properties WHERE pmc_id = %s ORDER BY id", (pmc,)
        )
    ]
    company.leases = [
        r[0]
        for r in conn.execute("SELECT id FROM trust_leases WHERE pmc_id = %s ORDER BY id", (pmc,))
    ]
    company.names = [
        r[0]
        for r in conn.execute(
            "SELECT display_name FROM trust_tenants WHERE pmc_id = %s ORDER BY id LIMIT 200", (pmc,)
        )
    ]
    company.ledger_accounts = int(
        scalar(conn, "SELECT count(*) FROM trust_ledger_accounts WHERE pmc_id = %s", (pmc,))
    )
    worked = Worked(speed.Tally())
    with speed.serving(url) as server:
        rngs = [random.Random(seed * 1000 + n) for n in range(crew_size)]  # noqa: S311
        crew = [
            speed.Staffer(conn, company, server.origin, n + 1, rng, today)
            for n, rng in enumerate(rngs)
        ]
        began = time.monotonic()
        until = began + seconds

        def nights() -> None:
            with as_app(url) as own:
                while time.monotonic() < until:
                    began = time.monotonic()
                    try:
                        nightly.run_pmc(own, pmc, today, URL)
                    except Exception as failed:  # any error here is a finding
                        worked.night_failures.append(repr(failed)[:300])
                    worked.nights.append(time.monotonic() - began)

        threads = [
            threading.Thread(target=s.run, args=(until, worked.tally, time.monotonic, pause))
            for s in crew
        ]
        threads.append(threading.Thread(target=nights))

        def watch() -> None:
            # At the end of each part of the time: that part's answers, the API's memory, the
            # database's connections. Lists only grow, so a part's answers are each list's tail.
            seen: dict[str, int] = {}
            with psycopg.connect(url, autocommit=True) as own:
                for n in range(1, WINDOWS + 1):
                    time.sleep(max(0.0, began + seconds * n / WINDOWS - time.monotonic()))
                    with worked.tally.lock:
                        part = [
                            took
                            for kind, times in worked.tally.seconds.items()
                            for took in times[seen.get(kind, 0) :]
                        ]
                        seen = {kind: len(times) for kind, times in worked.tally.seconds.items()}
                    connections = scalar(
                        own,
                        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()",
                    )
                    worked.windows.append(
                        Window(
                            speed.percentile(part, 0.95) * 1000 if part else 0.0,
                            rss_mb(server.pid),
                            int(connections),
                        )
                    )

        threads.append(threading.Thread(target=watch))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    return worked


def restore_copy(server: str, url: str, t: Timings) -> None:
    """Step 6: the database backed up with scripts/backup.sh and restored into an empty one."""
    with (
        tempfile.TemporaryDirectory() as folder,
        scratch_database(server, "pms_enterprise_copy") as copy,
    ):
        archive = Path(folder) / "books.dump"
        with t.step("back up"):
            dumped = backup(ROOT, "dump", archive, url)
        if dumped.returncode != 0:
            raise Wrong(f"the backup failed: {dumped.stderr[-500:]}")
        with t.step("restore"):
            restored = backup(ROOT, "restore", archive, copy)
        if restored.returncode != 0:
            raise Wrong(f"the restore failed: {restored.stderr[-500:]}")
        with psycopg.connect(url) as a, psycopg.connect(copy) as b:
            transfers = "SELECT count(*) FROM pgledger_transfers"
            if scalar(a, transfers) != scalar(b, transfers):
                raise Wrong("the restored copy doesn't hold every transfer")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--units", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--staff", type=int, default=50)
    parser.add_argument("--seconds", type=float, default=300)
    parser.add_argument("--pause", type=float, default=1.0)
    parser.add_argument("--history-years", type=int, default=HISTORY_YEARS)
    args = parser.parse_args(argv)
    server = os.environ.get("DATABASE_URL")
    dbmate = shutil.which("dbmate")
    if not server or not dbmate or not shutil.which("pg_dump"):
        print("FAIL: needs DATABASE_URL, and dbmate and pg_dump 16 on PATH.")
        return 1
    t = Timings()
    with scratch_database(server, "pms_enterprise") as url:
        with t.step("migrate an empty database"):
            migrated = Dbmate(dbmate, MIGRATIONS, url).run("--no-dump-schema", "up")
        if migrated.returncode != 0:
            print(f"FAIL: dbmate up failed:\n{migrated.stderr}")
            return 1
        try:
            made = run(
                url,
                args.units,
                args.seed,
                t,
                args.staff,
                args.seconds,
                args.pause,
                args.history_years,
            )
            restore_copy(server, url, t)
        except Wrong as wrong:
            print(f"FAIL: {wrong}")
            return 1
    print(f"made: {made}")
    late = over(t, limits())
    if late:
        print("FAIL: " + "; ".join(late))
        return 1
    print(f"ok: {args.units} units in {sum(t.steps.values()) / 60:.0f} minutes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
