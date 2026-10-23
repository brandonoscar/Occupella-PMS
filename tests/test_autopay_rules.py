"""Autopay in the database (migration 20261022000159): a tenant on the lease's autopay; its steps
in order (started once, then saved or failed once, stopped once after it started), one autopay
saved and not stopped a lease; a charge per autopay and due date, an online payment of its lease,
tenant and method, only while saved and not stopped; append-only, audited; the AI role reads none
of it. The API's side is in tests/api/test_autopay_api.py."""

from datetime import date

import psycopg
import pytest
from helpers import add_tenant, add_unit, make_pmc, open_lease

JAN_1, FEB_1 = date(2026, 1, 1), date(2026, 2, 1)


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def lease(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    return open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "1500.00", [tenant_of(conn, pmc)])


def autopay(conn, pmc, lease, tenant=None, method="card"):
    return conn.execute(
        "INSERT INTO trust_autopay (pmc_id, lease_id, tenant_id, method, processor)"
        " VALUES (%s, %s, %s, %s, 'fake') RETURNING id",
        (pmc.pmc_id, lease, tenant or tenant_of(conn, pmc), method),
    ).fetchone()[0]


def step(conn, pmc, autopay_id, kind, reference=None, label=None, reason=None):
    conn.execute(
        "INSERT INTO trust_autopay_steps (pmc_id, autopay_id, step, reference, label, reason)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (pmc.pmc_id, autopay_id, kind, reference, label, reason),
    )


def saved(conn, pmc, lease, **kw):
    made = autopay(conn, pmc, lease, **kw)
    step(conn, pmc, made, "started", reference=f"fake_setup_{made}")
    step(conn, pmc, made, "saved", reference=f"fake_saved_{made}", label="Card ••••4242")
    return made


def payment(conn, pmc, lease, method="card"):
    return conn.execute(
        "INSERT INTO trust_online_payments (pmc_id, lease_id, tenant_id, amount, method, processor)"
        " VALUES (%s, %s, %s, 10.00, %s, 'fake') RETURNING id",
        (pmc.pmc_id, lease, tenant_of(conn, pmc), method),
    ).fetchone()[0]


def charge(conn, pmc, autopay_id, due_on, payment_id):
    conn.execute(
        "INSERT INTO trust_autopay_charges (pmc_id, autopay_id, due_on, payment_id)"
        " VALUES (%s, %s, %s, %s)",
        (pmc.pmc_id, autopay_id, due_on, payment_id),
    )


def test_an_autopay_is_a_tenant_on_the_lease_by_card_or_bank(conn, pmc, lease):
    autopay(conn, pmc, lease, method="bank debit")
    stranger = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 9")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        autopay(conn, pmc, lease, tenant=stranger)
    with pytest.raises(psycopg.errors.CheckViolation):
        autopay(conn, pmc, lease, method="cash")


def test_steps_come_in_order(conn, pmc, lease):
    made = autopay(conn, pmc, lease)
    for kind, kw, match in (
        ("saved", dict(reference="r", label="Card"), "saved or fails once"),
        ("stopped", dict(reason="no"), "only an autopay that started stops"),
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match=match):
            step(conn, pmc, made, kind, **kw)
    step(conn, pmc, made, "started", reference="fake_setup_a")
    with pytest.raises(psycopg.errors.CheckViolation, match="starts once"):
        step(conn, pmc, made, "started", reference="fake_setup_b")
    step(conn, pmc, made, "failed", reason="declined")
    with pytest.raises(psycopg.errors.CheckViolation, match="saved or fails once"):
        step(conn, pmc, made, "saved", reference="r", label="Card")
    step(conn, pmc, made, "stopped", reason="the tenant stopped it")
    with pytest.raises(psycopg.errors.UniqueViolation):
        step(conn, pmc, made, "stopped", reason="again")
    with pytest.raises(psycopg.errors.CheckViolation):
        step(conn, pmc, autopay(conn, pmc, lease), "started")  # a start names its reference


def test_a_lease_has_one_autopay_on_at_a_time(conn, pmc, lease):
    first = saved(conn, pmc, lease)
    second = autopay(conn, pmc, lease)
    step(conn, pmc, second, "started", reference="fake_setup_2")
    with pytest.raises(psycopg.errors.CheckViolation, match="on autopay already"):
        step(conn, pmc, second, "saved", reference="r2", label="Card ••••1111")
    step(conn, pmc, first, "stopped", reason="the tenant stopped it")
    step(conn, pmc, second, "saved", reference="r2", label="Card ••••1111")


def test_a_charge_is_once_a_due_date_of_its_own_autopay_while_on(conn, pmc, lease):
    on = saved(conn, pmc, lease)
    charge(conn, pmc, on, JAN_1, payment(conn, pmc, lease))
    with pytest.raises(psycopg.errors.UniqueViolation):
        charge(conn, pmc, on, JAN_1, payment(conn, pmc, lease))
    with pytest.raises(psycopg.errors.CheckViolation, match="its own lease"):
        charge(conn, pmc, on, FEB_1, payment(conn, pmc, lease, method="bank debit"))
    step(conn, pmc, on, "stopped", reason="stopped")
    with pytest.raises(psycopg.errors.CheckViolation, match="saved and not stopped"):
        charge(conn, pmc, on, FEB_1, payment(conn, pmc, lease))


def test_rows_are_never_changed_and_every_one_is_audited(conn, pmc, lease):
    on = saved(conn, pmc, lease)
    charge(conn, pmc, on, JAN_1, payment(conn, pmc, lease))
    for table, rows in (("trust_autopay", 1), ("trust_autopay_steps", 2),
                        ("trust_autopay_charges", 1)):  # fmt: skip
        for change in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with pytest.raises(psycopg.errors.RestrictViolation), conn.transaction():
                conn.execute(change)
        assert conn.execute(
            "SELECT count(*) FROM trust_audit_log WHERE table_name = %s AND pmc_id = %s",
            (table, pmc.pmc_id),
        ).fetchone() == (rows,)


def test_the_ai_role_reads_none_of_it(ai_conn):
    for table in ("trust_autopay", "trust_autopay_steps", "trust_autopay_charges"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ai_conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
