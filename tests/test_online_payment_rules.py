"""Online payments in the database (migration 20261022000158): a payment is a tenant on the
lease paying it a whole number of cents by card or bank debit; its steps come in order (started
once with the processor's reference, then one end, a return only after a success), each processor
event once; append-only and audited; the AI role reads none of it. The API's side is in
tests/api/test_online_payments_api.py."""

import uuid
from datetime import date

import psycopg
import pytest
from helpers import add_tenant, add_unit, make_pmc, open_lease, transfer

JAN_1 = date(2026, 1, 1)


def tenant_of(conn, pmc):
    """The PMC's tenant: the one its prepaid rent account is for."""
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


def payment(conn, pmc, lease, tenant=None, amount="10.00", method="card"):
    return conn.execute(
        "INSERT INTO trust_online_payments (pmc_id, lease_id, tenant_id, amount, method, processor)"
        " VALUES (%s, %s, %s, %s, %s, 'fake') RETURNING id",
        (pmc.pmc_id, lease, tenant or tenant_of(conn, pmc), amount, method),
    ).fetchone()[0]


def step(conn, pmc, payment_id, kind, reference=None, event=None, transfer=None, reason=None):
    conn.execute(
        "INSERT INTO trust_online_payment_steps (pmc_id, payment_id, step, reference,"
        " processor_event, transfer_id, reason) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (pmc.pmc_id, payment_id, kind, reference, event, transfer, reason),
    )


def a_transfer(conn, pmc):
    return transfer(conn, pmc.operating_cash, pmc.owners[0].account, "1.00")


def test_a_payment_is_a_tenant_on_the_lease_paying_whole_cents_by_card_or_bank(conn, pmc, lease):
    payment(conn, pmc, lease, method="bank debit")
    stranger = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 9")
    for bad, error in (
        ({"tenant": stranger}, psycopg.errors.ForeignKeyViolation),
        ({"amount": "0"}, psycopg.errors.CheckViolation),
        ({"amount": "1.005"}, psycopg.errors.CheckViolation),
        ({"method": "cash"}, psycopg.errors.CheckViolation),
    ):
        with pytest.raises(error):
            payment(conn, pmc, lease, **bad)


def test_steps_come_in_order_and_each_event_once(conn, pmc, lease):
    paid, failed = payment(conn, pmc, lease), payment(conn, pmc, lease)
    with pytest.raises(psycopg.errors.CheckViolation, match="ends once"):
        step(conn, pmc, paid, "succeeded", event="e0", transfer=a_transfer(conn, pmc))
    step(conn, pmc, paid, "started", reference="fake_a")
    step(conn, pmc, failed, "started", reference="fake_b")
    for refused, match in (
        (dict(kind="started", reference="fake_c"), "starts once"),
        (dict(kind="returned", event="e1", transfer=a_transfer(conn, pmc)), "succeeded comes back"),
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match=match):
            step(conn, pmc, paid, **refused)
    step(conn, pmc, paid, "succeeded", event="e2", transfer=a_transfer(conn, pmc), reference="pi")
    with pytest.raises(psycopg.errors.CheckViolation, match="ends once"):
        step(conn, pmc, paid, "failed", event="e3", reason="no")
    with pytest.raises(psycopg.errors.UniqueViolation):
        step(conn, pmc, failed, "failed", event="e2", reason="the same event again")
    step(conn, pmc, failed, "failed", event="e4", reason="declined")
    step(conn, pmc, paid, "returned", event="e5", transfer=a_transfer(conn, pmc))
    with pytest.raises(psycopg.errors.CheckViolation):
        step(conn, pmc, failed, "refused", event="e6", reason="no")
    # A checkout's reference names one payment.
    with pytest.raises(psycopg.errors.UniqueViolation):
        step(conn, pmc, payment(conn, pmc, lease), "started", reference="fake_a")


@pytest.mark.parametrize(
    "shape",
    [
        dict(kind="started"),
        dict(kind="succeeded", event="e1"),
        dict(kind="failed", event="e1"),
        dict(kind="started", reference="r", reason="no"),
    ],
)
def test_each_step_carries_what_it_says(conn, pmc, lease, shape):
    paid = payment(conn, pmc, lease)
    if shape["kind"] != "started":
        step(conn, pmc, paid, "started", reference=f"fake_{uuid.uuid4().hex}")
    with pytest.raises(psycopg.errors.CheckViolation):
        step(conn, pmc, paid, **shape)


def test_rows_are_never_changed_and_every_one_is_audited(conn, pmc, lease):
    conn.execute(
        "INSERT INTO trust_online_payment_settings (pmc_id, cards, bank_debits)"
        " VALUES (%s, true, false)",
        (pmc.pmc_id,),
    )
    paid = payment(conn, pmc, lease)
    step(conn, pmc, paid, "started", reference="fake_z")
    for table in ("trust_online_payment_settings", "trust_online_payments",
                  "trust_online_payment_steps"):  # fmt: skip
        for change in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with pytest.raises(psycopg.errors.RestrictViolation), conn.transaction():
                conn.execute(change)
        assert conn.execute(
            "SELECT count(*) FROM trust_audit_log WHERE table_name = %s AND pmc_id = %s",
            (table, pmc.pmc_id),
        ).fetchone() == (1,)


def test_the_ai_role_reads_none_of_it(ai_conn):
    for table in ("trust_online_payment_settings", "trust_online_payments",
                  "trust_online_payment_steps"):  # fmt: skip
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ai_conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608


def test_another_companys_lease_takes_none(conn, pmc, lease):
    other = make_pmc(conn)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        payment(conn, other, lease, tenant=tenant_of(conn, pmc))
