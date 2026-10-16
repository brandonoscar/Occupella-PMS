"""What posts by itself every night (src/pms/nightly.py, migration 0067): each PMC's rent and
recurring charges for the month, the late fees of rents past their grace, and the recurring bills
due, each once; recorded as a run naming nobody; one PMC that fails leaves the others to run; and
the API's scheduler, which runs it when the API starts and then every day at 06:00 UTC."""

import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from helpers import (
    add_agreement,
    add_late_fee_policy,
    add_recurring_bill,
    add_recurring_charge,
    add_unit,
    make_pmc,
    open_lease,
)

from pms import accounts, email, nightly
from pms.api import app as api_app

URL = "http://localhost:8000"


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


DAY = timedelta(days=1)
OCT_16 = date(2026, 10, 16)


def lease_since(conn, pmc, starts, rent="1000.00"):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    return open_lease(conn, pmc.pmc_id, unit, starts, None, rent, [tenant])


def charges(conn, lease):
    return conn.execute(
        "SELECT due_on, kind, amount::text FROM trust_charges WHERE lease_id = %s"
        " ORDER BY due_on, kind, amount",
        (lease,),
    ).fetchall()


def test_a_night_charges_the_months_rent_late_fees_and_bills_once(conn, app_conn, pmc):
    lease = lease_since(conn, pmc, date(2026, 9, 1))
    add_recurring_charge(conn, pmc.pmc_id, lease, "25.00", date(2026, 10, 1))
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, date(2026, 9, 1), 5, "50.00")
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, date(2026, 1, 1), approval_limit="10.00")
    add_recurring_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, "LAWN", "30.00", 3, date(2026, 9, 1)
    )
    owner = pmc.owners[0].owner_id
    invite = accounts.create_invite(
        conn, pmc.pmc_id, "owner", owner, f"owner.{owner.hex[:6]}@example.com", None
    )
    accounts.accept_invite(conn, invite.token, "a long synthetic passphrase")

    first = nightly.run_pmc(app_conn, pmc.pmc_id, OCT_16, URL)
    again = nightly.run_pmc(app_conn, pmc.pmc_id, OCT_16, URL)

    assert (first.rents, first.recurring, first.late_fees, first.bills) == (1, 1, 1, 2)
    assert (again.rents, again.recurring, again.late_fees, again.bills) == (0, 0, 0, 0)
    # October's rent and pet rent, due on its first day, and its rent's late fee.
    assert charges(conn, lease) == [
        (date(2026, 10, 1), "fee", "25.00"),
        (date(2026, 10, 1), "rent", "1000.00"),
        (date(2026, 10, 7), "fee", "50.00"),  # due the day after its grace
    ]
    # September's and October's bills, each over the owner's limit with its email to them.
    assert conn.execute(
        "SELECT b.reference, count(e.id) FROM trust_bills AS b"
        " LEFT JOIN trust_emails AS e ON e.bill_id = b.id"
        " WHERE b.pmc_id = %s GROUP BY b.reference ORDER BY b.reference",
        (pmc.pmc_id,),
    ).fetchall() == [("LAWN-2026-09", 1), ("LAWN-2026-10", 1)]
    runs = conn.execute(
        "SELECT due_on, late_fees_as_of, rents, recurring, late_fees, recorded_by"
        " FROM trust_rent_runs WHERE pmc_id = %s ORDER BY created_at",
        (pmc.pmc_id,),
    ).fetchall()
    assert runs == [
        (date(2026, 10, 1), OCT_16, 1, 1, 1, None),
        (date(2026, 10, 1), OCT_16, 0, 0, 0, None),
    ]


def test_a_lease_starting_later_in_the_month_is_charged_its_part_month(conn, app_conn, pmc):
    lease = lease_since(conn, pmc, date(2026, 10, 20), "3100.00")

    nightly.run_pmc(app_conn, pmc.pmc_id, OCT_16, URL)

    assert charges(conn, lease) == [(date(2026, 10, 20), "rent", "1200.00")]  # 12 of 31 days


def test_one_company_that_fails_leaves_the_others_to_run(conn, app_conn, pmc, caplog):
    lease = lease_since(conn, pmc, date(2026, 9, 1))
    other = make_pmc(conn).pmc_id
    nobody = uuid.uuid4()  # no such PMC: its run can't be recorded
    with caplog.at_level(logging.ERROR, logger="pms.nightly"):
        done = nightly.run_all(app_conn, OCT_16, URL, [nobody, pmc.pmc_id, other])

    assert [r.pmc_id for r in done] == [pmc.pmc_id, other]
    assert f"the nightly run of PMC {nobody} failed" in caplog.text
    assert charges(conn, lease) == [(date(2026, 10, 1), "rent", "1000.00")]


def test_every_company_runs_when_none_is_named(conn, app_conn, pmc, monkeypatch):
    ran = []
    monkeypatch.setattr(nightly, "run_pmc", lambda c, pmc_id, day, url: ran.append(pmc_id))

    nightly.run_all(app_conn, OCT_16, URL)

    assert pmc.pmc_id in ran
    assert ran == sorted(ran)


@pytest.mark.parametrize(
    ("now", "seconds"),
    [
        (datetime(2026, 10, 16, 5, 0, tzinfo=UTC), 3600),
        (datetime(2026, 10, 16, 6, 0, tzinfo=UTC), 24 * 3600),
        (datetime(2026, 10, 16, 23, 0, tzinfo=UTC), 7 * 3600),
    ],
)
def test_the_next_run_is_the_next_six_in_the_morning_utc(now, seconds):
    assert nightly.seconds_until(now) == seconds


@pytest.mark.timing
def test_the_scheduler_runs_at_start_then_waits_for_its_time(conn, database_url, pmc):
    lease = lease_since(conn, pmc, date(2026, 9, 1))
    clock = lambda: datetime(2026, 10, 16, 6, 0, tzinfo=UTC)  # noqa: E731
    runs = nightly.Scheduler(
        database_url, "trust_app", URL, email.sender_named("fake", {}), clock, pmcs=[pmc.pmc_id]
    )
    runs.start()
    assert runs.ran.wait(30)
    runs.stop()

    assert charges(conn, lease) == [(date(2026, 10, 1), "rent", "1000.00")]


@pytest.mark.timing
def test_a_scheduler_that_cannot_reach_the_database_tries_again(caplog):
    runs = nightly.Scheduler(
        "postgresql://nobody@127.0.0.1:1/none", "trust_app", URL, retry=0.01, at=time(6, 0)
    )
    with caplog.at_level(logging.ERROR, logger="pms.nightly"):
        runs.start()
        deadline = datetime.now(UTC) + timedelta(seconds=30)
        while caplog.text.count("the nightly run failed") < 2 and datetime.now(UTC) < deadline:
            runs._stop.wait(0.01)
        runs.stop()

    assert caplog.text.count("the nightly run failed") >= 2
    assert not runs.ran.is_set()


def test_the_command_runs_every_company_once(monkeypatch, capsys, database_url):
    seen = []

    def run_all(conn, day, url):
        seen.append((day, url))
        return [nightly.Ran(pmc_id=None, rents=2, recurring=1, late_fees=1, bills=3)]

    monkeypatch.setattr(nightly, "run_all", run_all)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("PMS_PUBLIC_URL", "https://pms.example.com")

    assert nightly.main([]) == 0
    assert seen == [(datetime.now(UTC).date(), "https://pms.example.com")]
    assert capsys.readouterr().out == "1 companies: 2 rents, 1 late fees, 3 bills\n"
    assert nightly.main(["now"]) == 2


def test_the_api_runs_the_scheduler_unless_told_not_to(monkeypatch, database_url):
    from fastapi.testclient import TestClient

    started = []

    class Fake:
        def __init__(self, url, role, public_url, sender):
            started.append(("made", role, public_url))

        def start(self):
            started.append("started")

        def stop(self):
            started.append("stopped")

    monkeypatch.setattr(nightly, "Scheduler", Fake)
    settings = api_app.Settings(database_url=database_url, nightly=True)
    with TestClient(api_app.create_app(settings)):
        assert started == [("made", "trust_app", "http://localhost:8000"), "started"]
    assert started[-1] == "stopped"

    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("PMS_NIGHTLY", raising=False)
    assert api_app.Settings.from_env().nightly is True
    monkeypatch.setenv("PMS_NIGHTLY", "off")
    assert api_app.Settings.from_env().nightly is False
