"""The Consumer Price Index figures a rent cap counts (src/pms/rent_cap.py, migration
20261115000100, docs/law.md "A year's rent increase capped by inflation").

The server's PMS_CPI picks where they come from (pms.api.Settings): "bls", the Bureau of Labor
Statistics' Public Data API v1, keyless and allowed 25 requests a day, so a process asks once a
day for every series a cap reads, in one request, and keeps the answer for every company; "fake"
for development and CI, which calls nothing; or unset for none, and a cap then says it couldn't
check. The nightly run fetches (src/pms/nightly.py) and keeps each company's figures in its own
books (`keep`): a fetch, then the months whose figure is new or changed, so a night that changed
nothing writes nothing. A failed fetch is logged and leaves the last figures.

A rent cap reads a series' months all of one fetch (`on_file`): BLS can rebase a series (it moved
several to December 2024 = 100 in May 2026), and two figures from fetches on either side of that
would compare two bases. The BLS answer's shape is read from its published documentation, which
this session could not reach (NOT VERIFIED): it is read defensively, and anything else is a
failed fetch.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import UUID

import psycopg

from pms import rent_cap
from pms.rent_cap import Month

log = logging.getLogger("pms.cpi")

# A series' figures by month.
Fetched = dict[str, dict[Month, Decimal]]
# How many years back a fetch asks for: Oregon's average counts the 24 months before September of
# the year before, so an increase next year reaches back three years and a few months.
YEARS_BACK = 4
# How many times a day a process asks after a failure (BLS allows 25 requests a day).
ATTEMPTS_A_DAY = 3
THOUSANDTH = Decimal("0.001")


class CPIFailed(Exception):
    """The source could not be reached or answered with something it shouldn't."""


class Source(Protocol):
    name: str

    def fetch(self, series: Sequence[str], first_year: int, last_year: int, today: date) -> Fetched:
        """Each series' monthly figures from January of `first_year` through what is published
        by `today`."""
        ...


# The fake publishes each month on the 15th of the next, as BLS does about then.
FAKE_RELEASE_DAY = 15
# A series the fake publishes in odd months only, as BLS publishes San Diego's (so California's
# March rule is the one that counts it).
FAKE_ODD_MONTHS = frozenset({rent_cap.SAN_DIEGO.series})


class FakeCPI:
    """A stand-in for development and tests: it calls nothing. Each series rises by a fixed
    amount a month from a level of its own, so every figure is the same on every run."""

    name = "fake"

    def fetch(self, series: Sequence[str], first_year: int, last_year: int, today: date) -> Fetched:
        out: Fetched = {}
        for n, name in enumerate(series):
            months: dict[Month, Decimal] = {}
            for year in range(first_year, last_year + 1):
                for month in range(1, 13):
                    released = (
                        date(year + 1, 1, FAKE_RELEASE_DAY)
                        if month == 12
                        else date(year, month + 1, FAKE_RELEASE_DAY)
                    )
                    if released > today:
                        continue
                    if name in FAKE_ODD_MONTHS and month % 2 == 0:
                        continue
                    since = (year - 2015) * 12 + month - 1
                    months[(year, month)] = fake_value(n, since)
            out[name] = months
        return out


def fake_value(n: int, months_since_2015: int) -> Decimal:
    """The fake's figure: a level of its own for the `n`th series, up 0.75 a month."""
    return (Decimal(240) + Decimal(10) * n + Decimal("0.75") * months_since_2015).quantize(
        THOUSANDTH
    )


class BlsCPI:
    """The Bureau of Labor Statistics' Public Data API, version 1: no key, 25 requests a day, up
    to 25 series and 10 years in one. Its figures are public domain."""

    name = "bls"
    url = "https://api.bls.gov/publicAPI/v1/timeseries/data/"

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    def fetch(self, series: Sequence[str], first_year: int, last_year: int, today: date) -> Fetched:
        body = json.dumps(
            {"seriesid": list(series), "startyear": str(first_year), "endyear": str(last_year)}
        ).encode()
        asked = urllib.request.Request(  # noqa: S310 (a fixed https URL)
            self.url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(asked, timeout=self.timeout) as answer:  # noqa: S310
                read = json.load(answer)
        except (OSError, ValueError) as failed:
            raise CPIFailed(f"the BLS API did not answer: {failed}") from None
        return bls_figures(read)


def bls_figures(body: Any) -> Fetched:
    """Each series' monthly figures in the BLS API's answer: its periods M01 to M12 only (M13 is
    the year's average), each value a number; a value BLS marks unavailable ("-") is left out."""
    if not isinstance(body, dict) or body.get("status") != "REQUEST_SUCCEEDED":
        said = body.get("message") if isinstance(body, dict) else None
        words = "; ".join(str(m) for m in said) if isinstance(said, list) else ""
        raise CPIFailed(f"the BLS API refused the request: {words or 'no reason given'}"[:500])
    try:
        found = body["Results"]["series"]
        out: Fetched = {}
        for entry in found:
            months: dict[Month, Decimal] = {}
            for row in entry["data"]:
                period = str(row["period"])
                if len(period) != 3 or period[0] != "M" or not "01" <= period[1:] <= "12":
                    continue
                try:
                    value = Decimal(str(row["value"]).strip())
                except InvalidOperation:
                    continue
                if not value.is_finite() or value <= 0:
                    continue
                months[(int(row["year"]), int(period[1:]))] = value.quantize(THOUSANDTH)
            out[str(entry["seriesID"])] = months
        return out
    except (KeyError, TypeError, ValueError, ArithmeticError) as failed:
        raise CPIFailed(f"the BLS API answered in a shape it shouldn't: {failed}") from None


SOURCES: dict[str, type[FakeCPI] | type[BlsCPI]] = {"fake": FakeCPI, "bls": BlsCPI}


class NoSource(Exception):
    """A source was named that isn't one."""


def source_named(name: str) -> Source | None:
    """The source called `name` ("bls" or "fake"), or None for an empty name: none."""
    chosen = name.strip()
    if not chosen:
        return None
    if chosen not in SOURCES:
        raise NoSource(f"no CPI source called {chosen!r}; known: {', '.join(SOURCES)}")
    return SOURCES[chosen]()


@dataclass
class Daily:
    """What a process fetched today from each source, and how many times it asked."""

    got: dict[tuple[str, date], Fetched] = field(default_factory=dict)
    asked: dict[tuple[str, date], int] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


DAILY = Daily()


def fetched_today(
    source: Source, now: datetime | None = None, daily: Daily = DAILY
) -> Fetched | None:
    """Every series a cap reads, from `source`, asked at most once a UTC day in this process once
    it answered, and at most ATTEMPTS_A_DAY times while it fails; None when it failed (logged)."""
    at = now or datetime.now(UTC)
    day = at.date()
    key = (source.name, day)
    with daily.lock:
        if key in daily.got:
            return daily.got[key]
        if daily.asked.get(key, 0) >= ATTEMPTS_A_DAY:
            return None
        daily.asked[key] = daily.asked.get(key, 0) + 1
        for old in [k for k in daily.got if k[1] < day - timedelta(days=1)]:
            del daily.got[old]
        try:
            got = source.fetch(rent_cap.SERIES, day.year - YEARS_BACK, day.year, day)
        except CPIFailed as failed:
            log.warning("the CPI figures could not be fetched from %s: %s", source.name, failed)
            return None
        daily.got[key] = got
        return got


LATEST = """
SELECT DISTINCT ON (g.month) g.month, g.value
FROM trust_cpi_figures AS g
JOIN trust_cpi_fetches AS f ON f.id = g.fetch_id
WHERE g.pmc_id = %(pmc)s AND g.series_id = %(series)s
ORDER BY g.month, f.created_at DESC, f.id DESC
"""

COVERING = """
SELECT 1 FROM trust_cpi_fetches
WHERE pmc_id = %(pmc)s AND series_id = %(series)s
  AND first_month <= %(first)s AND last_month >= %(last)s
ORDER BY created_at DESC, id DESC
LIMIT 1
"""


def first_of(month: Month) -> date:
    return date(month[0], month[1], 1)


def keep(conn: psycopg.Connection[Any], pmc_id: UUID, source: str, fetched: Fetched) -> int:
    """Keeps in the company's books what `fetched` holds that they don't: for each series, a
    fetch over the months it answered and each month whose figure is new or changed. Returns the
    figures written; a series answered the same as on file writes nothing."""
    written = 0
    for series in sorted(fetched):
        months = fetched[series]
        if series not in rent_cap.SERIES or not months:
            continue
        on_file = {
            (day.year, day.month): value
            for day, value in conn.execute(LATEST, {"pmc": pmc_id, "series": series}).fetchall()
        }
        changed = sorted(m for m, value in months.items() if on_file.get(m) != value)
        window = {
            "pmc": pmc_id,
            "series": series,
            "first": first_of(min(months)),
            "last": first_of(max(months)),
        }
        if not changed and conn.execute(COVERING, window).fetchone() is not None:
            continue
        fetch = conn.execute(
            "INSERT INTO trust_cpi_fetches (pmc_id, source, series_id, first_month, last_month)"
            " VALUES (%(pmc)s, %(source)s, %(series)s, %(first)s, %(last)s) RETURNING id",
            {**window, "source": source},
        ).fetchone()
        assert fetch is not None
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO trust_cpi_figures (pmc_id, fetch_id, series_id, month, value)"
                " VALUES (%s, %s, %s, %s, %s)",
                [(pmc_id, fetch[0], series, first_of(m), months[m]) for m in changed],
            )
        written += len(changed)
    return written


ONE_FETCH = """
WITH f AS (
    SELECT id, created_at FROM trust_cpi_fetches
    WHERE pmc_id = %(pmc)s AND series_id = %(series)s
      AND first_month <= %(first)s AND last_month >= %(last)s
    ORDER BY created_at DESC, id DESC
    LIMIT 1
)
SELECT DISTINCT ON (g.month) g.month, g.value
FROM f
JOIN trust_cpi_fetches AS h ON h.pmc_id = %(pmc)s AND h.series_id = %(series)s
    AND (h.created_at, h.id) <= (f.created_at, f.id)
JOIN trust_cpi_figures AS g ON g.fetch_id = h.id
WHERE g.month = ANY(%(months)s)
ORDER BY g.month, h.created_at DESC, h.id DESC
"""


def on_file(conn: psycopg.Connection[Any], pmc_id: UUID) -> rent_cap.Figures:
    """The company's figures as a cap reads them: a series' months as its latest fetch over all
    of them saw them (what that fetch wrote, or the figure on file it found the same)."""

    def figures(series: str, months: Sequence[Month]) -> Mapping[Month, Decimal]:
        if not months:
            return {}
        rows = conn.execute(
            ONE_FETCH,
            {
                "pmc": pmc_id,
                "series": series,
                "first": first_of(min(months)),
                "last": first_of(max(months)),
                "months": [first_of(m) for m in months],
            },
        ).fetchall()
        return {(day.year, day.month): Decimal(value) for day, value in rows}

    return figures


def keep_for(
    conn: psycopg.Connection[Any], pmc_id: UUID, source: Source, fetched: Fetched | None
) -> int:
    """`keep` in a transaction of its own, for the nightly run: a failure is logged and never
    reaches the company's run."""
    if fetched is None:
        return 0
    try:
        with conn.transaction():
            return keep(conn, pmc_id, source.name, fetched)
    except Exception:
        log.exception(
            "the CPI figures of PMC %s could not be kept; the next run tries again", pmc_id
        )
        return 0
