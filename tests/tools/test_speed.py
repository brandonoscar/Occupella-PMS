"""The nightly speed test (tools/speed.py; the founder's review q148): a company of many units
served as `python -m pms.api` serves it, staff signed in over HTTP at once, every answer timed.
Its run fails on an answer that isn't 2xx and on anything slower than its ceiling."""

import random
import sys

import httpx2
import pytest

from tools import speed

LIMITS = {"page": 1000.0, speed.DOWNLOAD: 30000.0}


@pytest.mark.timing
def test_staff_signed_in_at_once_open_every_page_of_a_company_of_many_units(database_url, conn):
    result = speed.run(database_url, units=30, staff_count=2, months=2, seconds=1, seed=1, pause=0)

    company = result.company
    assert result.tally.failures == []
    # The demo's seven units and properties of 20 until there are 30: two more properties.
    assert company.units == 30 and len(company.properties) == 4 + 2
    assert set(result.tally.seconds) == {kind for kind, _ in WEIGHTS}
    assert speed.passed(result.tally, {"page": 60_000.0, speed.DOWNLOAD: 600_000.0})
    # Each payment staff recorded over HTTP is in the books, naming who recorded it.
    recorded = conn.execute(
        "SELECT count(*) FROM trust_audit_log AS a JOIN trust_staff AS s ON s.id = a.staff_id"
        " WHERE a.table_name = 'pgledger_transfers' AND s.display_name LIKE 'Speed Staff %%'"
    ).fetchone()[0]
    assert recorded >= len(result.tally.seconds["payment"])
    # Every month's rent was paid in full by the 23 new leases but a tenth of this month's.
    owing = conn.execute(
        "SELECT count(*) FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id"
        " JOIN trust_properties AS p ON p.id = u.property_id"
        " WHERE p.display_name LIKE 'Speed Court %%' AND EXISTS ("
        "   SELECT 1 FROM trust_open_charges(l.pmc_id, l.id, 'infinity') AS c WHERE c.open > 0)"
    ).fetchone()[0]
    assert owing == 2


WEIGHTS = [
    ("dashboard", 2),
    ("today", 2),
    ("properties", 3),
    ("property", 3),
    ("rent roll", 4),
    ("lease", 5),
    ("lease ledger", 4),
    ("tenants", 3),
    ("search", 3),
    ("general ledger", 2),
    ("report", 2),
    ("payment", 4),
    (speed.DOWNLOAD, 1),
]


def staffer(handler, kinds=None):
    """A staff member's client answered by `handler`, on a company of nothing much."""
    member = speed.Staffer.__new__(speed.Staffer)
    member.company = speed.Company(
        speed.uuid.uuid4(),
        speed.uuid.uuid4(),
        units=1,
        properties=[speed.uuid.uuid4()],
        leases=[speed.uuid.uuid4()],
        names=["Speed Court 001"],
        ledger_accounts=30,
    )
    member.rng = random.Random(0)
    member.today = speed.date(2026, 10, 20)
    member.client = httpx2.Client(
        base_url="http://speed.test", transport=httpx2.MockTransport(handler)
    )
    return member


def test_staff_ask_for_each_page_as_the_portal_does():
    asked = []

    def handler(request):
        asked.append((request.method, request.url.path, request.url.query.decode()))
        return httpx2.Response(200, json={})

    member = staffer(handler)
    tally = speed.Tally()
    ticks = iter(range(1000))
    paused = []
    # Every page once, then 40 more at random, pausing after each as a person would.
    member.run(40, tally, clock=lambda: next(ticks), pause=1.5, sleep=paused.append)

    assert [(kind, weight) for kind, (weight, _) in member.requests().items()] == WEIGHTS
    assert tally.failures == [] and set(tally.seconds) == {kind for kind, _ in WEIGHTS}
    assert len(asked) == len(paused) == len(WEIGHTS) + 40
    assert all(0 <= seconds <= 3 for seconds in paused) and len(set(paused)) > 1
    paths = {path for _, path, _ in asked}
    lease = member.company.leases[0]
    assert {
        "/api/dashboard",
        "/api/today",
        f"/api/leases/{lease}",
        f"/api/leases/{lease}/ledger",
        f"/api/rent/leases/{lease}/payments",
        "/api/search",
    } <= paths
    queries = {query for _, path, query in asked if path == "/api/general-ledger"}
    # Thirty ledger accounts are two pages of 25.
    assert queries <= {f"from=2026-10-01&to=2026-10-20&page={n}" for n in (1, 2)}
    assert ("POST", f"/api/rent/leases/{lease}/payments") in {(m, p) for m, p, _ in asked}


def test_an_answer_that_isnt_2xx_or_never_comes_is_a_failure():
    def handler(request):
        if request.url.path == "/api/dashboard":
            return httpx2.Response(500)
        raise httpx2.ConnectError("refused", request=request)

    member = staffer(handler)
    tally = speed.Tally()
    member.ask("dashboard", ("GET", "/api/dashboard", None), tally)
    member.ask("today", ("GET", "/api/today", None), tally)

    assert tally.failures == [
        "dashboard: 500 on GET /api/dashboard",
        "today: ConnectError on /api/today",
    ]
    assert len(tally.seconds["dashboard"]) == len(tally.seconds["today"]) == 1


def test_the_95th_percentile_is_the_nearest_rank():
    times = [n / 1000 for n in range(1, 101)]  # 1 ms to 100 ms

    assert speed.percentile(times, 0.95) == 0.095
    assert speed.percentile(times, 0.5) == 0.05
    assert speed.percentile([0.2], 0.95) == 0.2


def result_of(tally):
    company = speed.Company(speed.uuid.uuid4(), speed.uuid.uuid4(), units=1000, leases=[1, 2])
    return speed.Result(company, tally, built_in=42.0)


def test_anything_slower_than_its_ceiling_fails_the_run():
    tally = speed.Tally()
    for _ in range(19):
        tally.add("lease", 0.2)
        tally.add(speed.DOWNLOAD, 20.0)
    tally.add("lease", 1.5)  # one in twenty: the 95th percentile is still 200 ms
    tally.add(speed.DOWNLOAD, 40.0)
    fast = speed.report(result_of(tally), LIMITS, 120)
    tally.add("lease", 1.5)  # two in twenty-one: now it is 1,500 ms

    shown = speed.report(result_of(tally), LIMITS, 120)

    assert "SLOW" not in fast and speed.passed(tally, {"page": 2000.0, speed.DOWNLOAD: 30000.0})
    assert "1,000 units, 2 leases, built in 42 s; 41 answers in 120 s" in shown
    assert "lease               21      200    1,500    1,500    1,000" in shown
    assert "SLOW lease: p95 1,500 ms is over 1,000 ms" in shown
    assert "download" in shown and "SLOW download" not in shown
    assert not speed.passed(tally, LIMITS)


def test_a_failure_or_nothing_asked_fails_the_run():
    nothing = speed.Tally()
    failed = speed.Tally()
    failed.add("payment", 0.01, "500 on POST /api/rent/leases/x/payments")
    result = result_of(failed)
    result.server_log = "Traceback (most recent call last):"

    assert not speed.passed(nothing, LIMITS)
    assert "FAILED nothing was asked: the run timed nothing" in speed.report(
        result_of(nothing), LIMITS, 1
    )
    assert not speed.passed(failed, LIMITS)
    shown = speed.report(result, LIMITS, 1)
    assert "FAILED payment: 500 on POST /api/rent/leases/x/payments" in shown
    assert shown.endswith("the server's last lines:\nTraceback (most recent call last):")


def test_the_ceilings_come_from_the_thresholds_file(tmp_path):
    shipped = speed.ceilings()
    other = tmp_path / "thresholds.toml"
    other.write_text("[speed]\npage_p95_ms_max = 500\ndownload_p95_ms_max = 9000\n")

    assert set(shipped) == {"page", speed.DOWNLOAD} and shipped["page"] <= 1000
    assert speed.ceilings(other) == {"page": 500.0, speed.DOWNLOAD: 9000.0}
    assert speed.ceiling_of("lease", speed.ceilings(other)) == 500.0
    assert speed.ceiling_of(speed.DOWNLOAD, speed.ceilings(other)) == 9000.0


def test_a_server_that_doesnt_start_is_said_with_its_last_lines(database_url):
    crash = [sys.executable, "-c", "print('no such app'); raise SystemExit(3)"]

    with pytest.raises(speed.ServerDown) as down, speed.serving(database_url, crash):
        pass

    assert "the API didn't start" in str(down.value) and "no such app" in str(down.value)


STUBBORN = """
import http.server, os, signal
signal.signal(signal.SIGTERM, signal.SIG_IGN)
class Healthy(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *args):
        pass
http.server.HTTPServer(("127.0.0.1", int(os.environ["PMS_PORT"])), Healthy).serve_forever()
"""


@pytest.mark.timing
def test_a_server_that_wont_stop_when_asked_is_stopped(database_url, monkeypatch):
    monkeypatch.setattr(speed, "STOPPING", 0.5)

    with speed.serving(database_url, [sys.executable, "-c", STUBBORN]) as server:
        assert speed.healthy(server.origin)

    assert not speed.healthy(server.origin)


def test_the_command_needs_a_server(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert speed.main([]) == 1
    assert "needs DATABASE_URL and dbmate" in capsys.readouterr().out


def test_the_command_runs_on_a_database_of_its_own(database_url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", database_url)
    seen = {}

    def timed(url, units, staff_count, months, seconds, seed, pause):
        with speed.as_app(url) as conn:
            seen["tables"] = conn.execute(
                "SELECT count(*) FROM pg_tables WHERE tablename LIKE 'trust_%'"
            ).fetchone()[0]
        seen["asked"] = (units, staff_count, months, seconds, seed, pause)
        tally = speed.Tally()
        tally.add("lease", 0.05)
        return result_of(tally)

    monkeypatch.setattr(speed, "run", timed)

    assert speed.main(["--seconds", "3", "--seed", "7"]) == 0
    assert seen["asked"] == (1000, 10, 12, 3.0, 7, 1.0)
    assert seen["tables"] > 50  # migrated
    assert "lease" in capsys.readouterr().out


def test_a_server_down_or_migrations_that_fail_fail_the_command(
    database_url, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("DATABASE_URL", database_url)

    def down(*args):
        raise speed.ServerDown("the API didn't start:\nno such app")

    monkeypatch.setattr(speed, "run", down)
    assert speed.main([]) == 1
    assert "FAIL: the API didn't start" in capsys.readouterr().out

    (tmp_path / "20260101000000_broken.sql").write_text(
        "-- migrate:up\nSELECT no_such_function();\n-- migrate:down\n"
    )
    monkeypatch.setattr(speed, "MIGRATIONS", tmp_path)
    assert speed.main([]) == 1
    assert "dbmate up failed" in capsys.readouterr().out
