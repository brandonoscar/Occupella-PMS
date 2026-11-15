"""The database's own order of what the payments partner reports of a payment online
(migrations 20261023000150 and 20261102000150): a payment paid comes back as a return, never
as a failure. Since P-026 the app books a failure reported after paid as a return, so only the
tables' triggers stand between any other writer and a paid payment marked failed."""

import psycopg
import pytest
from helpers import add_unit, make_pmc, open_lease

TENANT = "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s"
EVENT = (
    "INSERT INTO {table} (pmc_id, payment_id, kind, partner_event, partner_fee, happened_at)"
    " VALUES (%s, %s, %s, %s, %s, now())"
)


def report(conn, table, pmc, payment, kind):
    conn.execute(
        EVENT.format(table=table),
        (pmc.pmc_id, payment, kind, f"evt_{kind}_{payment.hex}", 0 if kind == "paid" else None),
    )


def test_a_tenants_payment_paid_is_never_marked_failed(conn):
    pmc = make_pmc(conn)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    tenant = conn.execute(TENANT, (pmc.prepaid_rent,)).fetchone()[0]
    lease = open_lease(conn, pmc.pmc_id, unit, "2026-01-01", None, "1200.00", [tenant])
    payment = conn.execute(
        "INSERT INTO trust_online_payments (pmc_id, lease_id, tenant_id, amount, tenant_fee,"
        " fee_payer, method, partner, partner_ref)"
        " VALUES (%s, %s, %s, '1200.00', '0.00', 'company', 'ach', 'fake', 'fake_Synthetic01')"
        " RETURNING id",
        (pmc.pmc_id, lease, tenant),
    ).fetchone()[0]
    report(conn, "trust_online_payment_events", pmc, payment, "paid")
    with pytest.raises(psycopg.errors.CheckViolation, match="comes back, it does not fail"):
        report(conn, "trust_online_payment_events", pmc, payment, "failed")
    report(conn, "trust_online_payment_events", pmc, payment, "returned")


def test_an_owners_payment_paid_is_never_marked_failed(conn):
    pmc = make_pmc(conn)
    payment = conn.execute(
        "INSERT INTO trust_owner_online_payments (pmc_id, ledger_account_id, amount, method,"
        " partner, partner_ref) VALUES (%s, %s, '500.00', 'ach', 'fake', 'fake_Synthetic02')"
        " RETURNING id",
        (pmc.pmc_id, pmc.owners[0].account),
    ).fetchone()[0]
    report(conn, "trust_owner_online_payment_events", pmc, payment, "paid")
    with pytest.raises(psycopg.errors.CheckViolation, match="comes back, it does not fail"):
        report(conn, "trust_owner_online_payment_events", pmc, payment, "failed")
    report(conn, "trust_owner_online_payment_events", pmc, payment, "returned")
