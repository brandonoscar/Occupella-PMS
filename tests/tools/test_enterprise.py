"""Enterprise size (tools/enterprise.py; the founder, 2026-10-25): a company of many units
brought over through the switching tool, its night run, its reports, staff at work on it for a
long time, backed up and restored. Each step has a ceiling, and the books must be right after."""

import csv
import io
import os
from datetime import date

import pytest

from pms import demo, nightly
from pms.api import switching
from tools import enterprise


@pytest.mark.timing
def test_a_company_comes_over_works_and_comes_back_from_its_backup(
    database_url, monkeypatch, capsys
):
    monkeypatch.setenv("DATABASE_URL", database_url)

    assert (
        enterprise.main(
            [
                "--units",
                "40",
                "--staff",
                "2",
                "--seconds",
                "6",
                "--pause",
                "0",
                "--history-years",
                "1",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "import tenant history (1 file)" in out
    assert "opening balances, deposits (40 accounts)" in out
    assert "part 6 of 6" in out and "restore:" in out
    assert "ok: 40 units" in out


def rows(text):
    return list(csv.reader(io.StringIO(text)))


def test_the_files_are_the_company_as_the_switching_tool_takes_them():
    files = enterprise.company(200, date(2026, 10, 25), enterprise.random.Random(3), 2)
    kinds = dict(files.kinds)

    assert list(kinds) == [
        "owners",
        "vendors",
        "properties",
        "units",
        "tenants",
        "leases",
        "charges",
        "bills",
        "tenant history",
        "owner history",
        "vendor history",
    ]
    for kind, texts in files.kinds:
        for text in texts:
            assert len(rows(text)) - 1 <= switching.MAX_ROWS and len(text) < 1_900_000, kind
    units = [r for t in kinds["units"] for r in rows(t)[1:]]
    assert len(units) == files.leases == 200
    # Each home's rent paid every month of two years, each owner paid and each vendor too.
    history = [r for t in kinds["tenant history"] for r in rows(t)[1:]]
    assert len(history) == 200 * 24
    assert {r[2] for r in history} == {
        enterprise.month_before(date(2026, 10, 1), m).isoformat() for m in range(1, 25)
    }
    properties = len(rows(kinds["properties"][0])) - 1
    assert len(rows(kinds["owner history"][0])) - 1 == properties * 24
    assert len(rows(kinds["vendor history"][0])) - 1 == enterprise.VENDORS * 24
    # A deposit for each home; the operating account holds each property's owner and the fees.
    assert len(files.deposits) == 200
    assert sum(1 for line in files.operating if line["kind"] == "owner") == properties


def test_a_kind_with_more_than_a_file_takes_goes_in_several():
    lines = [[f"Tenant {n}", "x" * 100] for n in range(25_000)]

    files = enterprise.chunked(["tenant", "memo"], lines)

    # 20,000 rows would be over 1.9 MB, so the first file halves; the rest fit whole.
    assert [len(rows(f)) - 1 for f in files] == [10_000, 15_000]
    assert all(len(f) < 1_900_000 for f in files)
    assert enterprise.chunked(["tenant"], [[str(n)] for n in range(5)], most=2) == [
        "tenant\n0\n1\n",
        "tenant\n2\n3\n",
        "tenant\n4\n",
    ]


def test_months_before_cross_the_year():
    assert enterprise.month_before(date(2026, 2, 1), 1) == date(2026, 1, 1)
    assert enterprise.month_before(date(2026, 2, 1), 2) == date(2025, 12, 1)
    assert enterprise.month_before(date(2026, 1, 1), 60) == date(2021, 1, 1)


def test_each_step_over_its_ceiling_fails_the_run():
    t = enterprise.Timings()
    t.steps = {"import units (1 file)": 5.0, "import tenants (2 files)": 31.0, "restore": 9.0}

    assert enterprise.over(
        t, {"import_seconds_max": 30.0, "restore_seconds_max": 10.0, "soak_p95_growth_max": 1.0}
    ) == ["import tenants (2 files): 31 s, over its ceiling of 30 s"]


def test_answers_memory_or_connections_growing_over_the_work_fail_the_run():
    limits = {
        "soak_p95_growth_max": 2.0,
        "soak_memory_growth_max": 1.5,
        "soak_connections_growth_max": 2.0,
    }
    steady = [enterprise.Window(300, 140, 7), enterprise.Window(500, 150, 9)]
    assert enterprise.grown(steady, limits) == []
    assert enterprise.grown(steady[:1], limits) == []
    # A start under 100 ms counts as 100, so a fast start's noise isn't growth.
    assert (
        enterprise.grown([enterprise.Window(20, 140, 7), enterprise.Window(190, 140, 7)], limits)
        == []
    )

    worse = [enterprise.Window(300, 140, 7), enterprise.Window(700, 300, 20)]
    assert enterprise.grown(worse, limits) == [
        "the answers' p95 went from 300 ms to 700 ms",
        "the API's memory went from 140 MB to 300 MB",
        "the database's connections went from 7 to 20",
    ]


def test_a_process_memory_is_read_from_proc():
    assert enterprise.rss_mb(os.getpid()) > 1
    assert enterprise.rss_mb(2**22 + 1) == 0.0  # no such process


def test_a_lease_in_effect_left_without_its_rent_fails_the_books(app_conn):
    built = demo.books(app_conn)
    day = enterprise.datetime.now(enterprise.UTC).date()
    nightly.run_pmc(app_conn, built.pmc_id, day, enterprise.URL)
    charged = enterprise.scalar(
        app_conn,
        "SELECT count(DISTINCT lease_id) FROM trust_charges WHERE pmc_id = %s AND kind = 'rent'"
        " AND due_on >= %s",
        (built.pmc_id, day.replace(day=1)),
    )

    enterprise.check_books(app_conn, built.pmc_id, day, charged)
    with pytest.raises(enterprise.Wrong, match="this month's rent went to"):
        enterprise.check_books(app_conn, built.pmc_id, day, charged + 1)


def test_the_ceilings_come_from_the_thresholds_file(tmp_path):
    every = enterprise.limits()
    assert {
        "import_seconds_max",
        "opening_seconds_max",
        "nightly_run_seconds_max",
        "report_seconds_max",
        "back_up_seconds_max",
        "restore_seconds_max",
        "soak_p95_growth_max",
        "soak_memory_growth_max",
        "soak_connections_growth_max",
    } <= set(every)

    path = tmp_path / "thresholds.toml"
    path.write_text("[enterprise]\nrestore_seconds_max = 7\n")
    assert enterprise.limits(path) == {"restore_seconds_max": 7.0}


def test_the_command_needs_a_server(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert enterprise.main([]) == 1
    assert "needs DATABASE_URL" in capsys.readouterr().out


def test_books_left_wrong_or_a_step_over_its_ceiling_fail_the_command(
    database_url, monkeypatch, capsys
):
    monkeypatch.setenv("DATABASE_URL", database_url)
    asked = {}

    def wrong(url, units, seed, t, staff, seconds, pause, years):
        asked["run"] = (units, seed, staff, seconds, pause, years)
        raise enterprise.Wrong("the trial balance has debits 1 and credits 2")

    monkeypatch.setattr(enterprise, "run", wrong)
    assert enterprise.main(["--units", "100", "--seed", "4"]) == 1
    assert asked["run"] == (100, 4, 50, 300.0, 1.0, 5)
    assert "FAIL: the trial balance has debits 1 and credits 2" in capsys.readouterr().out

    def slow(url, units, seed, t, *rest):
        t.steps["restore"] = 12.0
        return {}

    monkeypatch.setattr(enterprise, "run", slow)
    monkeypatch.setattr(enterprise, "restore_copy", lambda server, url, t: None)
    monkeypatch.setattr(enterprise, "limits", lambda: {"restore_seconds_max": 10.0})
    assert enterprise.main([]) == 1
    assert "FAIL: restore: 12 s, over its ceiling of 10 s" in capsys.readouterr().out


def test_migrations_that_fail_fail_the_command(database_url, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DATABASE_URL", database_url)
    (tmp_path / "20260101000000_broken.sql").write_text(
        "-- migrate:up\nSELECT no_such_function();\n-- migrate:down\n"
    )
    monkeypatch.setattr(enterprise, "MIGRATIONS", tmp_path)

    assert enterprise.main([]) == 1
    assert "dbmate up failed" in capsys.readouterr().out
