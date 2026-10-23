"""Applying through an application link, and following the application (src/pms/api/leasing.py).
Applicants have no login: the link's token and the status token are what let them in, and both
travel in JSON bodies."""

import hashlib
import secrets
import threading
import time
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from helpers import (
    add_staff,
    add_unit,
    record_adverse_action,
    record_decision,
    record_screening,
    submit_application,
)

from pms import accounts, notices

NOT_OPEN = "this application link is closed or doesn't exist"


class Listing:
    """Unit 1 of Property 1 with an open application link at 1500 a month."""

    def __init__(self, conn, pmc):
        self.pmc = pmc
        self.unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
        self.staff = add_staff(conn, pmc.pmc_id)
        self.token = secrets.token_urlsafe(32)
        self.link = conn.execute(
            "INSERT INTO trust_application_links"
            " (pmc_id, unit_id, token_sha256, monthly_rent, opened_by)"
            " VALUES (%s, %s, %s, 1500.00, %s) RETURNING id",
            (pmc.pmc_id, self.unit, accounts.digest(self.token), self.staff),
        ).fetchone()[0]

    def close(self, conn):
        conn.execute(
            "INSERT INTO trust_application_link_closings (pmc_id, link_id, closed_by)"
            " VALUES (%s, %s, %s)",
            (self.pmc.pmc_id, self.link, self.staff),
        )


@pytest.fixture
def listing(conn, pmc):
    return Listing(conn, pmc)


def application(listing, **changes):
    return {
        "token": listing.token,
        "move_in_on": (date.today() + timedelta(days=20)).isoformat(),
        "monthly_income": "4500.00",
        "applicants": [
            {"name": "Applicant A", "email": "Applicant.A@Example.com"},
            {"name": "Applicant B", "email": "applicant.b@example.com"},
        ],
        **changes,
    }


def status_token(answer) -> str:
    return answer.json()["status_url"].split("#", 1)[1]


def test_an_applicant_sees_what_the_link_is_for(client, listing):
    answer = client.post("/api/apply/view", json={"token": listing.token})

    assert answer.status_code == 200
    seen = answer.json()
    assert seen["pmc"].startswith("Synthetic PMC")
    assert seen | {"pmc": "-"} == {
        "pmc": "-",
        "property": "Property 1",
        "unit": "Unit 1",
        "monthly_rent": "1500.00",
        # The company set no screening criteria in words (migration 0089).
        "criteria": None,
    }


def test_an_applicant_applies_and_follows_the_application(client, conn, listing):
    applied = client.post("/api/apply", json=application(listing))
    assert applied.status_code == 201, applied.text
    assert applied.json()["status_url"].startswith("http://testserver/application#")
    token = status_token(applied)
    stored = conn.execute(
        "SELECT a.id, a.monthly_income, array_agg(p.email ORDER BY p.position)"
        " FROM trust_applications AS a JOIN trust_applicants AS p ON p.application_id = a.id"
        " WHERE a.status_token_sha256 = %s GROUP BY a.id",
        (hashlib.sha256(token.encode()).digest(),),
    ).fetchone()
    application_id = stored[0]

    def status():
        return client.post("/api/application/status", json={"status_token": token}).json()

    seen = [status()["status"]]
    screening = record_screening(
        conn, listing.pmc.pmc_id, application_id, listing.staff, "conditional", ["Income"]
    )
    seen.append(status()["status"])
    with conn.transaction():
        record_decision(
            conn,
            listing.pmc.pmc_id,
            application_id,
            listing.staff,
            "conditional",
            screening,
            "A deposit of one more month",
        )
        record_adverse_action(conn, listing.pmc.pmc_id, application_id, listing.staff)
    last = status()

    assert stored[1:] == (4500, ["applicant.a@example.com", "applicant.b@example.com"])
    assert seen + [last["status"]] == ["received", "in review", "conditional"]
    (reasons, agency, given_on) = conn.execute(
        "SELECT reasons, agency, notice_given_on FROM trust_adverse_actions"
        " WHERE application_id = %s",
        (application_id,),
    ).fetchone()
    assert last == {
        "property": "Property 1",
        "unit": "Unit 1",
        "submitted_at": last["submitted_at"],
        "status": "conditional",
        "conditions": "A deposit of one more month",
        # The adverse action notice, as its PDF and email say it (q080).
        "notice": notices.notice_text(
            "conditional", "A deposit of one more month", reasons, agency, given_on
        ),
        # The company asks no application fee (migration 0160).
        "fee": {"due": None, "paid": None, "methods": []},
    }
    assert last["notice"].startswith(
        "Your application was approved on conditions: A deposit of one more month"
    )


def test_a_declined_applicant_reads_the_notice_and_an_approved_one_none(client, conn, listing):
    """The founder's review, q080: the status page gives the adverse action notice."""
    pmc_id = listing.pmc.pmc_id

    def applied_for():
        token = status_token(client.post("/api/apply", json=application(listing)))
        (found,) = conn.execute(
            "SELECT id FROM trust_applications WHERE status_token_sha256 = %s",
            (hashlib.sha256(token.encode()).digest(),),
        ).fetchone()
        return token, found

    declined, approved = applied_for(), applied_for()
    with conn.transaction():
        screening = record_screening(
            conn, pmc_id, declined[1], listing.staff, "decline", ["Income below twice the rent"]
        )
        record_decision(conn, pmc_id, declined[1], listing.staff, "declined", screening)
        record_adverse_action(conn, pmc_id, declined[1], listing.staff, ("Income too low",))
        record_decision(conn, pmc_id, approved[1], listing.staff, "approved")

    seen = [
        client.post("/api/application/status", json={"status_token": token}).json()
        for token, _ in (declined, approved)
    ]

    assert seen[0]["status"] == "declined"
    assert seen[0]["notice"].startswith("Your application was declined.\n\nWhy:\n- Income too low")
    assert "Test agency (synthetic)" in seen[0]["notice"]
    assert (seen[1]["status"], seen[1]["notice"]) == ("approved", None)


@pytest.mark.parametrize("which", ["closed", "made up"])
def test_a_closed_or_made_up_link_reads_the_same(client, conn, listing, which):
    if which == "closed":
        listing.close(conn)
    token = listing.token if which == "closed" else secrets.token_urlsafe(32)

    answers = [
        client.post("/api/apply/view", json={"token": token}),
        client.post("/api/apply", json=application(listing, token=token)),
    ]

    assert [(a.status_code, a.json()["detail"]) for a in answers] == [(404, NOT_OPEN)] * 2


def test_a_made_up_status_token_is_not_found(client):
    answer = client.post(
        "/api/application/status", json={"status_token": secrets.token_urlsafe(32)}
    )

    assert answer.status_code == 404


@pytest.mark.parametrize(
    "changes",
    [
        {"move_in_on": (date.today() - timedelta(days=1)).isoformat()},
        {"move_in_on": (date.today() + timedelta(days=367)).isoformat()},
        {"move_in_on": "not a day"},
        {"monthly_income": "-1.00"},
        {"monthly_income": "100.001"},
        {"monthly_income": "10000000.01"},
        {"applicants": []},
        {"applicants": [{"name": f"A{n}", "email": f"a{n}@example.com"} for n in range(7)]},
        {"applicants": [{"name": "   ", "email": "a@example.com"}]},
        {"applicants": [{"name": "A", "email": "not an email"}]},
        {
            "applicants": [
                {"name": "A", "email": "a@example.com"},
                {"name": "B", "email": "A@example.com"},
            ]
        },
        {"token": "short"},
    ],
)
def test_a_bad_application_is_refused_and_nothing_is_kept(client, conn, listing, changes):
    answer = client.post("/api/apply", json=application(listing, **changes))

    assert answer.status_code == 422, answer.text
    assert conn.execute(
        "SELECT count(*) FROM trust_applications WHERE link_id = %s", (listing.link,)
    ).fetchone() == (0,)


def test_a_link_takes_fifty_applications_a_day(client, conn, listing):
    for _ in range(49):
        submit_application(conn, listing.pmc.pmc_id, listing.link)
    # One from more than a day ago doesn't count.
    conn.execute(
        "INSERT INTO trust_applications"
        " (pmc_id, link_id, status_token_sha256, move_in_on, monthly_income, submitted_at)"
        " VALUES (%s, %s, %s, DATE '2026-11-01', 1, %s)",
        (
            listing.pmc.pmc_id,
            listing.link,
            secrets.token_bytes(32),
            datetime.now(UTC) - timedelta(days=1, minutes=1),
        ),
    )

    answers = [client.post("/api/apply", json=application(listing)) for _ in range(2)]

    assert [a.status_code for a in answers] == [201, 429]


def test_another_site_cant_apply_for_someone(client, listing):
    answer = client.post(
        "/api/apply", json=application(listing), headers={"Origin": "https://elsewhere.example.com"}
    )

    assert answer.status_code == 403


@pytest.mark.timing
def test_an_application_waits_for_a_closing_on_its_way_and_is_refused(
    client, conn, database_url, listing
):
    with psycopg.connect(database_url) as first:
        listing.close(first)  # not committed yet: it holds the link's row
        answer: dict = {}
        thread = threading.Thread(
            target=lambda: answer.update(it=client.post("/api/apply", json=application(listing)))
        )
        thread.start()
        for _ in range(1000):  # until the application's trigger waits for the closing
            if conn.execute(
                "SELECT 1 FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
                " AND query LIKE 'INSERT INTO trust_applications%%'"
            ).fetchone():
                break
            time.sleep(0.01)
        else:
            raise AssertionError("the application never waited for the closing")
        first.commit()
    thread.join(timeout=30)

    assert (answer["it"].status_code, answer["it"].json()["detail"]) == (404, NOT_OPEN)
