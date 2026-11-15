"""The database's own rules for Stripe Connect (migration 20261114000100,
docs/adr/0014-stripe-connect.md): a Stripe account is one company's; what Stripe says of an
account, and a payment started on one, are only of an account the company connected, and only a
payment through Connect is started on one. The app keeps to these on its own; the triggers hold
whatever writes. Every account id is synthetic."""

import psycopg
import pytest
from helpers import acting_as, add_unit, admin_of, make_pmc, open_lease

# The PMC's tenant, by their prepaid rent account.
TENANT = "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s"


def connected(conn, pmc, account):
    with acting_as(conn, admin_of(conn, pmc.pmc_id)):
        conn.execute(
            "INSERT INTO trust_connected_accounts (pmc_id, account_id, partner, livemode)"
            " VALUES (%s, %s, 'fake_connect', false)",
            (pmc.pmc_id, account),
        )


def an_autopay(conn, pmc, partner):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    tenant = conn.execute(TENANT, (pmc.prepaid_rent,)).fetchone()[0]
    lease = open_lease(conn, pmc.pmc_id, unit, "2026-01-01", None, "1200.00", [tenant])
    return conn.execute(
        "INSERT INTO trust_autopay (pmc_id, lease_id, tenant_id, rule, amount, partner, method)"
        " VALUES (%s, %s, %s, 'fixed', '100.00', %s, 'ach') RETURNING id",
        (pmc.pmc_id, lease, tenant, partner),
    ).fetchone()[0]


def test_a_stripe_account_one_company_connected_is_never_another_companys(conn):
    one, two = make_pmc(conn), make_pmc(conn)
    connected(conn, one, "fake_acct_SyntheticShared01")
    with pytest.raises(psycopg.errors.CheckViolation, match="another company's"):
        connected(conn, two, "fake_acct_SyntheticShared01")
    # The company that connected it may connect it again (after disconnecting, say).
    connected(conn, one, "fake_acct_SyntheticShared01")
    rows = conn.execute(
        "SELECT pmc_id FROM trust_connected_accounts WHERE account_id = %s",
        ("fake_acct_SyntheticShared01",),
    ).fetchall()
    assert rows == [(one.pmc_id,), (one.pmc_id,)]


def test_what_stripe_says_is_kept_only_of_an_account_the_company_connected(conn):
    one, two = make_pmc(conn), make_pmc(conn)
    connected(conn, one, "fake_acct_SyntheticOne0001")
    state = (
        "INSERT INTO trust_connected_account_states (pmc_id, account_id, charges_enabled,"
        " payouts_enabled, details_submitted, reported_at) VALUES (%s, %s, true, true, true, now())"
    )
    conn.execute(state, (one.pmc_id, "fake_acct_SyntheticOne0001"))
    for pmc, account in (
        (one, "fake_acct_SyntheticNever001"),  # never connected by anyone
        (two, "fake_acct_SyntheticOne0001"),  # another company's
    ):
        with pytest.raises(psycopg.errors.CheckViolation, match="isn't one this company connected"):
            conn.execute(state, (pmc.pmc_id, account))


def test_a_payment_starts_only_on_the_companys_account_and_only_through_connect(conn):
    pmc = make_pmc(conn)
    connected(conn, pmc, "fake_acct_SyntheticOwn00001")
    started = (
        "INSERT INTO trust_connected_payments (pmc_id, account_id, autopay_id) VALUES (%s, %s, %s)"
    )
    through_connect = an_autopay(conn, pmc, "fake_connect")
    with pytest.raises(psycopg.errors.CheckViolation, match="isn't one this company connected"):
        conn.execute(started, (pmc.pmc_id, "fake_acct_SyntheticNever001", through_connect))
    # One through the server's own partner (or the company's keys) is never on a connected account.
    own_keys = an_autopay(conn, pmc, "fake")
    with pytest.raises(psycopg.errors.CheckViolation, match="only a payment through Connect"):
        conn.execute(started, (pmc.pmc_id, "fake_acct_SyntheticOwn00001", own_keys))
    conn.execute(started, (pmc.pmc_id, "fake_acct_SyntheticOwn00001", through_connect))
    rows = conn.execute(
        "SELECT autopay_id FROM trust_connected_payments WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchall()
    assert rows == [(through_connect,)]


def test_a_dispute_opens_only_on_a_payment_that_came_back_and_closes_only_once_open(conn):
    # Whatever reads Stripe's reports, the table refuses a dispute on a payment the bank never
    # took back, and a close (won or lost) of a dispute that never opened.
    pmc = make_pmc(conn)
    payment = conn.execute(
        "INSERT INTO trust_owner_online_payments (pmc_id, ledger_account_id, amount, method,"
        " partner, partner_ref, platform_fee)"
        " VALUES (%s, %s, '500.00', 'ach', 'fake_connect', 'fake_connect_py_Synthetic01', '0.50')"
        " RETURNING id",
        (pmc.pmc_id, pmc.owners[0].account),
    ).fetchone()[0]
    dispute = (
        "INSERT INTO trust_connect_disputes (pmc_id, owner_payment_id, dispute_id, kind, fee,"
        " partner_event, happened_at) VALUES (%s, %s, 'du_Synthetic01', %s, %s, %s, now())"
    )
    with pytest.raises(psycopg.errors.CheckViolation, match="didn't come back, so no dispute"):
        conn.execute(dispute, (pmc.pmc_id, payment, "opened", "15.00", "evt_Synthetic01"))
    with pytest.raises(psycopg.errors.CheckViolation, match="isn't open, so it can't close"):
        conn.execute(dispute, (pmc.pmc_id, payment, "won", "15.00", "evt_Synthetic02"))
    with pytest.raises(psycopg.errors.CheckViolation, match="isn't open, so it can't close"):
        conn.execute(dispute, (pmc.pmc_id, payment, "lost", None, "evt_Synthetic03"))
    assert conn.execute(
        "SELECT count(*) FROM trust_connect_disputes WHERE owner_payment_id = %s", (payment,)
    ).fetchone() == (0,)
