"""The company a server signs in to (GET /api/sign-in): on a server that holds exactly one company
(a company's own server, or its own stack when hosted) the sign-in page needs no ?pmc= link, so
the server names that company; on a server with more it names none, saying nothing about any of
them. A fresh database with one company, made as `python -m pms.admin setup` makes it, stands in
for a company's own server; it also shows the server's own Stripe keys serving that company
(payments.Resolver.server_for) without a stand-in."""

import shutil

import psycopg
import pytest
from fastapi.testclient import TestClient
from helpers import MIGRATIONS, make_pmc
from people import join_as_staff

from pms import admin, payments
from pms.api import Settings, create_app
from tools.check_rollback import Dbmate
from tools.scratch_db import scratch_database


@pytest.fixture(scope="module")
def one_company_server(database_url):
    """A fresh, fully migrated database holding one company, and that company's id."""
    with scratch_database(database_url, "pms_one_company") as url:
        migrated = Dbmate(shutil.which("dbmate"), MIGRATIONS, url).run("--no-dump-schema", "up")
        assert migrated.returncode == 0, migrated.stderr
        yield url


def setup_company(url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", url)
    assert admin.main(["setup", "Synthetic Solo PM", "Staff Solo", "solo@example.com"]) == 0
    printed = capsys.readouterr().out.splitlines()
    return printed[0].removeprefix("company ")


def test_a_server_with_one_company_names_it(one_company_server, monkeypatch, capsys):
    settings = Settings(
        database_url=one_company_server, public_url="http://testserver", cookie_secure=False
    )
    with TestClient(create_app(settings)) as browser:
        # A server that holds no company yet names none.
        assert browser.get("/api/sign-in").json() == {
            "pmc_id": None,
            "name": None,
            "password": True,
        }
        company = setup_company(one_company_server, monkeypatch, capsys)
        assert browser.get("/api/sign-in").json() == {
            "pmc_id": company,
            "name": "Synthetic Solo PM",
            # People sign in here with a password (PMS_PASSWORD_SIGN_IN, ADR 0013).
            "password": True,
        }


def test_the_servers_own_stripe_keys_serve_a_server_with_one_company(one_company_server):
    server = payments.StripePartner("sk_" + "test_x", "whsec_" + "x")
    with psycopg.connect(one_company_server, autocommit=True) as conn:
        conn.execute("SET ROLE trust_app")
        # The test above made the company; run alone, this one makes it.
        conn.execute(
            "INSERT INTO trust_pmcs (display_name) SELECT 'Synthetic Solo PM'"
            " WHERE NOT EXISTS (SELECT 1 FROM trust_pmcs)"
        )
        companies = conn.execute("SELECT count(*) FROM trust_pmcs").fetchone()
        assert companies == (1,)
        assert not payments.many_companies(conn)
        assert payments.Resolver(server).server_for(conn) is server


def test_a_server_with_many_companies_names_none(client, conn):
    make_pmc(conn), make_pmc(conn)
    answer = client.get("/api/sign-in")
    assert answer.status_code == 200
    assert answer.json() == {"pmc_id": None, "name": None, "password": True}


def test_a_signed_in_person_gets_the_same_answer(client, conn, app_conn, pmc):
    make_pmc(conn)
    join_as_staff(client, conn, app_conn, pmc)
    assert client.get("/api/sign-in").json() == {"pmc_id": None, "name": None, "password": True}


def test_it_only_answers_reads(client):
    # Signing in is a POST to the same path, with a body: an empty one is refused as bad input.
    assert client.post("/api/sign-in", json={}).status_code == 422
    assert client.put("/api/sign-in", json={}).status_code == 405
