"""The price index a rent cap counts (src/pms/cpi.py, migration 20261115000100): the fake source
calls nothing and makes the same figures every run; the BLS one asks once for every series and
its answer is read from a recorded synthetic one (no test reaches the network: urlopen is
replaced); each company keeps what changed, and a cap reads a series' months as one fetch saw
them. A failed fetch is logged and changes nothing; the setting is off unless named."""

import io
import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import make_pmc

from pms import cpi, nightly, rent_cap
from pms.api import Settings

LA = rent_cap.LOS_ANGELES.series
SD = rent_cap.SAN_DIEGO.series
NOV_15 = datetime(2026, 11, 15, 12, tzinfo=UTC)

# The BLS API v1's answer, as its documentation shows the shape (NOT VERIFIED against a live
# call: api.bls.gov was out of reach when this was written). Made-up figures.
BLS_ANSWER = {
    "status": "REQUEST_SUCCEEDED",
    "responseTime": 31,
    "message": [],
    "Results": {
        "series": [
            {
                "seriesID": LA,
                "data": [
                    {"year": "2026", "period": "M04", "periodName": "April", "value": "309.000"},
                    {"year": "2026", "period": "M13", "periodName": "Annual", "value": "305.1"},
                    {"year": "2026", "period": "S01", "periodName": "1st Half", "value": "304"},
                    {"year": "2025", "period": "M05", "periodName": "May", "value": "-"},
                    {"year": "2025", "period": "M04", "periodName": "April", "value": "300"},
                ],
            },
            {"seriesID": SD, "data": []},
        ]
    },
}


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def bls_answering(monkeypatch, answer, asked):
    def urlopen(request, timeout):
        asked.append((request.full_url, request.get_method(), json.loads(request.data), timeout))
        if isinstance(answer, Exception):
            raise answer
        body = answer if isinstance(answer, bytes) else json.dumps(answer).encode()
        return Answer(body)

    monkeypatch.setattr(cpi.urllib.request, "urlopen", urlopen)


def test_the_bls_source_asks_once_for_every_series_and_reads_months_only(monkeypatch):
    asked = []
    bls_answering(monkeypatch, BLS_ANSWER, asked)

    got = cpi.BlsCPI().fetch(rent_cap.SERIES, 2022, 2026, date(2026, 11, 15))

    [(url, method, body, timeout)] = asked
    assert (url, method, timeout) == (
        "https://api.bls.gov/publicAPI/v1/timeseries/data/",
        "POST",
        20.0,
    )
    assert body == {"seriesid": list(rent_cap.SERIES), "startyear": "2022", "endyear": "2026"}
    # The year's average, a half year and a figure marked unavailable are left out.
    assert got == {LA: {(2026, 4): Decimal("309.000"), (2025, 4): Decimal("300.000")}, SD: {}}


@pytest.mark.parametrize(
    ("answer", "says"),
    [
        (OSError("timed out"), "did not answer: timed out"),
        (b"<html>busy</html>", "did not answer"),
        (
            {"status": "REQUEST_NOT_PROCESSED", "message": ["daily threshold reached"]},
            "refused the request: daily threshold reached",
        ),
        ({"status": "REQUEST_SUCCEEDED", "Results": {}}, "in a shape it shouldn't"),
        (
            {"status": "REQUEST_SUCCEEDED", "Results": {"series": [{"seriesID": LA}]}},
            "in a shape it shouldn't",
        ),
    ],
)
def test_a_bls_answer_that_isnt_one_is_a_failed_fetch(monkeypatch, answer, says):
    bls_answering(monkeypatch, answer, [])
    with pytest.raises(cpi.CPIFailed, match=says):
        cpi.BlsCPI().fetch([LA], 2025, 2026, date(2026, 11, 15))


@pytest.mark.parametrize("value", ["0", "0.000", "-1.5", "NaN", "Infinity"])
def test_a_figure_bls_couldnt_have_published_is_left_out(monkeypatch, value):
    answer = {
        "status": "REQUEST_SUCCEEDED",
        "Results": {
            "series": [
                {
                    "seriesID": LA,
                    "data": [
                        {"year": "2026", "period": "M04", "value": value},
                        {"year": "2025", "period": "M04", "value": "300"},
                    ],
                }
            ]
        },
    }
    bls_answering(monkeypatch, answer, [])

    got = cpi.BlsCPI().fetch([LA], 2025, 2026, date(2026, 11, 15))

    # A figure of nothing or less, or not a number at all, is no index: never a change of -100%.
    assert got == {LA: {(2025, 4): Decimal("300.000")}}


def test_the_fake_publishes_the_same_figures_each_run_and_nothing_not_out_yet():
    fake = cpi.FakeCPI()
    first = fake.fetch(rent_cap.SERIES, 2025, 2026, date(2026, 5, 14))

    assert first == fake.fetch(rent_cap.SERIES, 2025, 2026, date(2026, 5, 14))
    # April is out on May 15th: not yet on the 14th.
    assert (2026, 3) in first[LA] and (2026, 4) not in first[LA]
    assert (2026, 4) in fake.fetch([LA], 2025, 2026, date(2026, 5, 15))[LA]
    # San Diego's index is published in odd months only, as BLS publishes it.
    assert (2026, 3) in first[SD] and (2026, 2) not in first[SD]
    assert first[LA][(2025, 4)] == cpi.fake_value(0, 123) == Decimal("332.250")


def test_the_server_names_its_source_and_has_none_unless_it_names_one(monkeypatch):
    assert cpi.source_named("") is None
    assert cpi.source_named("  ") is None
    assert isinstance(cpi.source_named("fake"), cpi.FakeCPI)
    assert isinstance(cpi.source_named("bls"), cpi.BlsCPI)
    with pytest.raises(cpi.NoSource, match="no CPI source called 'acme'"):
        cpi.source_named("acme")

    monkeypatch.setenv("DATABASE_URL", "postgres://example.invalid/pms")
    monkeypatch.delenv("PMS_CPI", raising=False)
    assert Settings.from_env().cpi == ""
    monkeypatch.setenv("PMS_CPI", "bls")
    assert Settings.from_env().cpi == "bls"


class Counting:
    def __init__(self, fails=False):
        self.name = "failing" if fails else "fake"
        self.calls = 0
        self.fails = fails

    def fetch(self, series, first_year, last_year, today):
        self.calls += 1
        if self.fails:
            raise cpi.CPIFailed("the BLS API did not answer: timed out")
        return cpi.FakeCPI().fetch(series, first_year, last_year, today)


def test_a_process_asks_once_a_day_and_a_failing_source_a_few_times_at_most(caplog):
    daily = cpi.Daily()
    source = Counting()

    first = cpi.fetched_today(source, NOV_15, daily)
    again = cpi.fetched_today(source, NOV_15.replace(hour=23), daily)

    assert first is again and source.calls == 1
    assert set(first) == set(rent_cap.SERIES)
    assert (2022, 1) in first[LA]  # four years back
    cpi.fetched_today(source, datetime(2026, 11, 16, 1, tzinfo=UTC), daily)
    assert source.calls == 2

    failing = Counting(fails=True)
    with caplog.at_level(logging.WARNING, logger="pms.cpi"):
        tries = [cpi.fetched_today(failing, NOV_15, daily) for _ in range(5)]
    assert tries == [None] * 5
    assert failing.calls == cpi.ATTEMPTS_A_DAY
    assert "the CPI figures could not be fetched from failing: the BLS API did not" in caplog.text


def test_a_process_forgets_what_it_fetched_before_yesterday():
    daily = cpi.Daily()
    source = Counting()

    cpi.fetched_today(source, NOV_15, daily)
    cpi.fetched_today(source, datetime(2026, 11, 16, 1, tzinfo=UTC), daily)
    assert set(daily.got) == {("fake", date(2026, 11, 15)), ("fake", date(2026, 11, 16))}

    cpi.fetched_today(source, datetime(2026, 11, 17, 1, tzinfo=UTC), daily)

    # Yesterday's stays (a day's run may start before midnight UTC and end after); older goes.
    assert set(daily.got) == {("fake", date(2026, 11, 16)), ("fake", date(2026, 11, 17))}
    assert source.calls == 3


def month(m):
    return date(m[0], m[1], 1)


def rows(conn, pmc_id, table):
    return conn.execute(
        f"SELECT count(*) FROM {table} WHERE pmc_id = %s",  # noqa: S608 (a fixed name)
        (pmc_id,),
    ).fetchone()[0]


def test_a_company_keeps_what_changed_and_reads_one_fetchs_figures(conn, app_conn):
    pmc = make_pmc(conn)
    fetched = {LA: {(2025, 4): Decimal("300.000"), (2026, 4): Decimal("309.000")}}

    assert cpi.keep(app_conn, pmc.pmc_id, "fake", fetched) == 2
    # The same again writes nothing.
    assert cpi.keep(app_conn, pmc.pmc_id, "fake", fetched) == 0
    assert (
        rows(conn, pmc.pmc_id, "trust_cpi_fetches"),
        rows(conn, pmc.pmc_id, "trust_cpi_figures"),
    ) == (1, 2)
    figures = cpi.on_file(app_conn, pmc.pmc_id)
    assert figures(LA, [(2025, 4), (2026, 4)]) == fetched[LA]
    assert figures(SD, [(2025, 3)]) == {}

    # BLS rebased the series: every month changed, and a month it hadn't published came too.
    rebased = {
        LA: {
            (2025, 4): Decimal("101.000"),
            (2026, 4): Decimal("104.030"),
            (2026, 5): Decimal("104.5"),
        }
    }
    assert cpi.keep(app_conn, pmc.pmc_id, "fake", rebased) == 3
    assert figures(LA, [(2025, 4), (2026, 4)]) == {
        (2025, 4): Decimal("101.000"),
        (2026, 4): Decimal("104.030"),
    }
    # Another company keeps its own.
    other = make_pmc(conn)
    assert cpi.on_file(app_conn, other.pmc_id)(LA, [(2025, 4)]) == {}


def test_a_series_no_cap_reads_or_one_with_no_months_keeps_nothing(conn, app_conn):
    pmc = make_pmc(conn)

    kept = cpi.keep(
        app_conn,
        pmc.pmc_id,
        "fake",
        {"CUUR0000SA0": {(2026, 4): Decimal("320.000")}, LA: {}, SD: {}},
    )

    assert kept == 0
    assert (
        rows(conn, pmc.pmc_id, "trust_cpi_fetches"),
        rows(conn, pmc.pmc_id, "trust_cpi_figures"),
    ) == (0, 0)
    # Asked for no months, the company's figures are none, without a query.
    assert cpi.on_file(app_conn, pmc.pmc_id)(LA, []) == {}


def test_a_month_no_fetch_answered_with_the_others_is_never_mixed_in(conn, app_conn):
    pmc = make_pmc(conn)
    # An old fetch over 2023 to April 2025, then a newer one over 2025 alone, rebased.
    cpi.keep(
        app_conn, pmc.pmc_id, "fake", {LA: {(2023, 4): Decimal("280"), (2025, 4): Decimal("300")}}
    )
    cpi.keep(
        app_conn, pmc.pmc_id, "fake", {LA: {(2025, 4): Decimal("101"), (2025, 12): Decimal("103")}}
    )

    figures = cpi.on_file(app_conn, pmc.pmc_id)

    # April 2023 to April 2025 is covered by the old fetch only: both of its figures.
    assert figures(LA, [(2023, 4), (2025, 4)]) == {
        (2023, 4): Decimal("280"),
        (2025, 4): Decimal("300"),
    }
    # April to December 2025 is the newer one's.
    assert figures(LA, [(2025, 4), (2025, 12)]) == {
        (2025, 4): Decimal("101"),
        (2025, 12): Decimal("103"),
    }


def test_the_figures_are_append_only_and_of_their_fetchs_months(conn, app_conn):
    pmc = make_pmc(conn)
    cpi.keep(
        app_conn, pmc.pmc_id, "fake", {LA: {(2025, 4): Decimal("300"), (2025, 6): Decimal("302")}}
    )
    fetch = conn.execute(
        "SELECT id FROM trust_cpi_fetches WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]

    with pytest.raises(psycopg.errors.CheckViolation, match="a month its fetch answered for"):
        app_conn.execute(
            "INSERT INTO trust_cpi_figures (pmc_id, fetch_id, series_id, month, value)"
            " VALUES (%s, %s, %s, '2026-04-01', 310)",
            (pmc.pmc_id, fetch, LA),
        )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        app_conn.execute(
            "INSERT INTO trust_cpi_figures (pmc_id, fetch_id, series_id, month, value)"
            " VALUES (%s, %s, %s, '2025-05-01', 310)",
            (pmc.pmc_id, fetch, SD),
        )
    with pytest.raises(psycopg.Error):
        app_conn.execute("UPDATE trust_cpi_figures SET value = 1 WHERE pmc_id = %s", (pmc.pmc_id,))
    with pytest.raises(psycopg.Error):
        app_conn.execute("DELETE FROM trust_cpi_fetches WHERE pmc_id = %s", (pmc.pmc_id,))


def test_the_nightly_run_keeps_the_days_figures_for_each_company_it_ran(conn, app_conn):
    pmc = make_pmc(conn)
    source = Counting()

    done = nightly.run_all(
        app_conn,
        date(2026, 11, 15),
        "http://testserver",
        pmcs=[pmc.pmc_id],
        at=datetime(2031, 1, 2, tzinfo=UTC),
        cpi_source=source,
    )

    assert [r.pmc_id for r in done] == [pmc.pmc_id]
    assert source.calls == 1
    kept = cpi.on_file(app_conn, pmc.pmc_id)(LA, [(2029, 4), (2030, 4)])
    assert set(kept) == {(2029, 4), (2030, 4)}
    assert rows(conn, pmc.pmc_id, "trust_cpi_fetches") == len(rent_cap.SERIES)


def test_a_failed_fetch_never_fails_the_companys_run_and_keeps_nothing(conn, app_conn, caplog):
    pmc = make_pmc(conn)

    with caplog.at_level(logging.WARNING, logger="pms.cpi"):
        done = nightly.run_all(
            app_conn,
            date(2026, 11, 15),
            "http://testserver",
            pmcs=[pmc.pmc_id],
            at=datetime(2031, 2, 2, tzinfo=UTC),
            cpi_source=Counting(fails=True),
        )

    assert [r.pmc_id for r in done] == [pmc.pmc_id]
    assert rows(conn, pmc.pmc_id, "trust_cpi_fetches") == 0
    assert "could not be fetched" in caplog.text
    # Keeping that breaks is logged too, and the run still counts.
    assert cpi.keep_for(app_conn, pmc.pmc_id, cpi.FakeCPI(), {LA: {(2025, 4): Decimal("-1")}}) == 0
    assert "could not be kept" in caplog.text


def test_without_a_source_the_nightly_run_fetches_nothing(conn, app_conn):
    pmc = make_pmc(conn)

    nightly.run_all(app_conn, date(2026, 11, 15), "http://testserver", pmcs=[pmc.pmc_id])

    assert rows(conn, pmc.pmc_id, "trust_cpi_fetches") == 0


def test_the_app_hands_its_source_to_the_nightly_run_and_refuses_the_fake_over_https(
    database_url,
):
    from pms import deployment
    from pms.api import create_app

    local = create_app(
        Settings(database_url=database_url, public_url="http://testserver", cpi="fake")
    )
    assert isinstance(local.state.cpi, cpi.FakeCPI)
    assert (
        create_app(Settings(database_url=database_url, public_url="http://testserver")).state.cpi
        is None
    )
    with pytest.raises(deployment.FakePartnerRefused, match="PMS_CPI names the fake partner"):
        create_app(
            Settings(
                database_url=database_url, public_url="https://rentals.example.org", cpi="fake"
            )
        )
