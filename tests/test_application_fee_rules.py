"""Application fees in the database (migration 20261022000160): a fee payment of an application,
whole cents; its steps in order (started once with the processor's reference, then succeeded or
failed once), an application's fee succeeding once, each processor event once; append-only and
audited; the AI role reads none of it. The API's side is in
tests/api/test_application_fees_api.py."""

import secrets
from datetime import date

import psycopg
import pytest
from helpers import add_staff, add_unit, make_pmc, transfer

from pms import accounts


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def application(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    link = conn.execute(
        "INSERT INTO trust_application_links (pmc_id, unit_id, token_sha256, monthly_rent,"
        " opened_by) VALUES (%s, %s, %s, 1500.00, %s) RETURNING id",
        (pmc.pmc_id, unit, accounts.digest(secrets.token_urlsafe(32)), add_staff(conn, pmc.pmc_id)),
    ).fetchone()[0]
    return conn.execute(
        "INSERT INTO trust_applications (pmc_id, link_id, status_token_sha256, move_in_on,"
        " monthly_income) VALUES (%s, %s, %s, %s, 4500) RETURNING id",
        (pmc.pmc_id, link, accounts.digest(secrets.token_urlsafe(32)), date(2026, 11, 1)),
    ).fetchone()[0]


def fee(conn, pmc, application, amount="35.00", method="card"):
    return conn.execute(
        "INSERT INTO trust_application_fees (pmc_id, application_id, amount, method, processor)"
        " VALUES (%s, %s, %s, %s, 'fake') RETURNING id",
        (pmc.pmc_id, application, amount, method),
    ).fetchone()[0]


def step(conn, pmc, fee_id, kind, reference=None, event=None, transfer_id=None, reason=None):
    conn.execute(
        "INSERT INTO trust_application_fee_steps (pmc_id, fee_id, step, reference,"
        " processor_event, transfer_id, reason) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (pmc.pmc_id, fee_id, kind, reference, event, transfer_id, reason),
    )


def money(conn, pmc):
    return transfer(conn, pmc.operating_cash, pmc.pmc_income, "1.00")


def test_a_fee_is_whole_cents_by_card_or_bank(conn, pmc, application):
    fee(conn, pmc, application, method="bank debit")
    for bad in ({"amount": "0"}, {"amount": "1.005"}, {"method": "cash"}):
        with pytest.raises(psycopg.errors.CheckViolation):
            fee(conn, pmc, application, **bad)


def test_steps_come_in_order_and_an_application_pays_once(conn, pmc, application):
    first, second = fee(conn, pmc, application), fee(conn, pmc, application)
    with pytest.raises(psycopg.errors.CheckViolation, match="ends once"):
        step(conn, pmc, first, "failed", event="e0", reason="no")
    step(conn, pmc, first, "started", reference="fake_fee_a")
    step(conn, pmc, second, "started", reference="fake_fee_b")
    with pytest.raises(psycopg.errors.CheckViolation, match="starts once"):
        step(conn, pmc, first, "started", reference="fake_fee_c")
    step(conn, pmc, first, "succeeded", event="e1", transfer_id=money(conn, pmc))
    with pytest.raises(psycopg.errors.CheckViolation, match="ends once"):
        step(conn, pmc, first, "failed", event="e2", reason="no")
    with pytest.raises(psycopg.errors.CheckViolation, match="paid already"):
        step(conn, pmc, second, "succeeded", event="e3", transfer_id=money(conn, pmc))
    with pytest.raises(psycopg.errors.UniqueViolation):
        step(conn, pmc, second, "failed", event="e1", reason="the same event again")
    step(conn, pmc, second, "failed", event="e4", reason="refund it")
    with pytest.raises(psycopg.errors.UniqueViolation):
        step(conn, pmc, fee(conn, pmc, application), "started", reference="fake_fee_a")
    with pytest.raises(psycopg.errors.CheckViolation):
        step(conn, pmc, fee(conn, pmc, application), "started")


def test_rows_are_never_changed_and_every_one_is_audited(conn, pmc, application):
    conn.execute(
        "INSERT INTO trust_application_fee_settings (pmc_id, per_applicant) VALUES (%s, 35)",
        (pmc.pmc_id,),
    )
    step(conn, pmc, fee(conn, pmc, application), "started", reference="fake_fee_z")
    for table in ("trust_application_fee_settings", "trust_application_fees",
                  "trust_application_fee_steps"):  # fmt: skip
        for change in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with pytest.raises(psycopg.errors.RestrictViolation), conn.transaction():
                conn.execute(change)
        assert conn.execute(
            "SELECT count(*) FROM trust_audit_log WHERE table_name = %s AND pmc_id = %s",
            (table, pmc.pmc_id),
        ).fetchone() == (1,)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_application_fee_settings (pmc_id, per_applicant) VALUES (%s, -1)",
            (pmc.pmc_id,),
        )


def test_the_ai_role_reads_none_of_it(ai_conn):
    for table in ("trust_application_fee_settings", "trust_application_fees",
                  "trust_application_fee_steps"):  # fmt: skip
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ai_conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608


def test_another_companys_application_takes_none(conn, pmc, application):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        fee(conn, make_pmc(conn), application)
