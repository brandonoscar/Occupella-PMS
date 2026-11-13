"""Autopays saved at another payments account (migration 20261113000200, src/pms/autopay_homes.py,
docs/edge-cases.md P-065; the founder, 2026-11-13: stop them when the account differs).

A tenant's bank account is saved at one Stripe account, and only that account can charge it. A
company that moves to another account (the server's to its own keys, Stripe Connect, or another
account's keys) has the autopays saved at the old one stop by themselves before any payment
starts, each tenant emailed to set it up again; new keys of the same account change nothing."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from helpers import add_unit, charge, make_pmc, open_lease

from pms import autopay, autopay_homes, nightly, payments
from pms.api import online_payments as op

TODAY = datetime.now(UTC).date()
MONTH = TODAY.replace(day=1)
DAY = timedelta(days=1)
LONG_AGO = datetime.now(UTC) - timedelta(days=90)
URL = "https://rentals.example.com"


@pytest.fixture(autouse=True)
def one_company(monkeypatch):
    # The server's own Stripe account serves only a server holding one company (P-005); the tests'
    # database holds many.
    monkeypatch.setattr(payments, "many_companies", lambda conn: False)


class Stripe:
    """A partner acting for the account it is given (or failing to say which), charging nothing."""

    name = "stripe"

    def __init__(self, account="acct_new"):
        self.acts_for = account
        self.asked = 0

    def account(self):
        self.asked += 1
        if isinstance(self.acts_for, Exception):
            raise self.acts_for
        return self.acts_for


@pytest.fixture
def pmc(conn):
    made = make_pmc(conn, owners=1)
    conn.execute(
        "INSERT INTO trust_payment_settings (pmc_id, online, ach_fee_payer)"
        " VALUES (%s, true, 'company')",
        (made.pmc_id,),
    )
    yield made
    # Its autopays stop, so no later test's server sends a payment of them to its partner.
    conn.execute(
        "INSERT INTO trust_autopay_stops (pmc_id, autopay_id, why)"
        " SELECT a.pmc_id, a.id, 'tenant' FROM trust_autopay AS a WHERE a.pmc_id = %s"
        " ON CONFLICT (autopay_id) DO NOTHING",
        (made.pmc_id,),
    )


@pytest.fixture
def tenant(conn, pmc):
    found = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO trust_contacts (pmc_id, tenant_id, email) VALUES (%s, %s, %s)",
        (pmc.pmc_id, found, "tenant1@example.com"),
    )
    return found


@pytest.fixture
def lease(conn, pmc, tenant):
    """A lease due on the first, this month's 1,200.00 charged."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    made = open_lease(conn, pmc.pmc_id, unit, MONTH - 60 * DAY, None, "1200.00", [tenant])
    charge(conn, pmc.pmc_id, made, MONTH, "1200.00")
    return made


def saved(conn, pmc, lease, tenant, at=None, partner="stripe"):
    """An autopay of the lease's balance, its bank account saved long ago, noted at `at` (None:
    saved before accounts were noted)."""
    made = conn.execute(
        "INSERT INTO trust_autopay (pmc_id, lease_id, tenant_id, rule, amount, partner)"
        " VALUES (%s, %s, %s, 'balance', '2000.00', %s) RETURNING id",
        (pmc.pmc_id, lease, tenant, partner),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO trust_autopay_accounts (pmc_id, autopay_id, partner_customer,"
        " partner_method, label, partner_event, saved_at)"
        " VALUES (%s, %s, 'cus_test', 'pm_test', 'Test bank ••••0000', %s, %s)",
        (pmc.pmc_id, made, f"evt_{made.hex}", LONG_AGO),
    )
    if at is not None:
        autopay_homes.note(conn, pmc.pmc_id, made, at, "saved")
    return made


def night(conn, pmc, partner, day=TODAY):
    """The company's nightly run, its partner `partner`, sending email."""
    conn.autocommit = False
    try:
        return nightly.run_pmc(conn, pmc.pmc_id, day, URL, True, payments.Resolver(partner))
    finally:
        conn.commit()
        conn.autocommit = True


def stopped(conn, autopay_id):
    found = conn.execute(
        "SELECT why FROM trust_autopay_stops WHERE autopay_id = %s", (autopay_id,)
    ).fetchone()
    return None if found is None else found[0]


def home(conn, autopay_id):
    return conn.execute(
        "SELECT partner_account, noted FROM trust_autopay_account_homes WHERE autopay_id = %s",
        (autopay_id,),
    ).fetchone()


def started(conn, autopay_id):
    return conn.execute(
        "SELECT count(*) FROM trust_autopay_runs WHERE autopay_id = %s AND payment_id IS NOT NULL",
        (autopay_id,),
    ).fetchone()[0]


def test_an_autopay_saved_at_another_account_stops_before_it_pays_and_its_tenant_is_told(
    conn, pmc, lease, tenant
):
    mine = saved(conn, pmc, lease, tenant, at="acct_old")

    ran = night(conn, pmc, Stripe("acct_new"))

    assert stopped(conn, mine) == "account changed"
    assert started(conn, mine) == 0  # no payment asked of an account that can't charge it
    assert ran.autopay == 0 and ran.emails >= 1
    subject, body, to = conn.execute(
        "SELECT subject, body, to_address FROM trust_emails"
        " WHERE kind = 'autopay_stopped' AND autopay_id = %s",
        (mine,),
    ).fetchone()
    assert to == "tenant1@example.com" and subject.startswith("Autopay stopped for ")
    assert (
        "stopped: your property manager now takes payments online through another account,"
        " which can't use the bank account you saved. Set up autopay again to keep paying"
        " automatically. Nothing more will be taken from Test bank ••••0000." in body
    )
    # The next night it is stopped already: nothing again.
    night(conn, pmc, Stripe("acct_new"), TODAY + DAY)
    assert (
        conn.execute(
            "SELECT count(*) FROM trust_emails WHERE kind = 'autopay_stopped' AND autopay_id = %s",
            (mine,),
        ).fetchone()[0]
        == 1
    )


def test_new_keys_of_the_same_account_change_nothing(conn, pmc, lease, tenant):
    mine = saved(conn, pmc, lease, tenant, at="acct_same")

    night(conn, pmc, Stripe("acct_same"))

    assert stopped(conn, mine) is None
    assert started(conn, mine) == 1  # this month's payment started as before


def test_one_saved_before_accounts_were_noted_is_noted_at_the_first_look(conn, pmc, lease, tenant):
    mine = saved(conn, pmc, lease, tenant)

    night(conn, pmc, Stripe("acct_first"))
    assert home(conn, mine) == ("acct_first", "first look")
    assert stopped(conn, mine) is None

    # The company moves to another account later: it stops then.
    night(conn, pmc, Stripe("acct_later"), TODAY + DAY)
    assert stopped(conn, mine) == "account changed"
    assert home(conn, mine) == ("acct_first", "first look")  # noted once


def test_a_partner_that_cant_say_its_account_stops_and_notes_nothing(conn, pmc, lease, tenant):
    old = saved(conn, pmc, lease, tenant, at="acct_old")
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2")
    other = open_lease(conn, pmc.pmc_id, unit, MONTH - 60 * DAY, None, "900.00", [tenant])
    unnoted = saved(conn, pmc, other, tenant)

    night(conn, pmc, Stripe(payments.PartnerFailed("Stripe refused GET /account: HTTP 401")))

    assert stopped(conn, old) is None and home(conn, unnoted) is None


def test_another_partner_s_autopay_is_left_to_it(conn, pmc, lease, tenant):
    theirs = saved(conn, pmc, lease, tenant, at="fake", partner="fake")

    night(conn, pmc, Stripe("acct_new"))

    assert stopped(conn, theirs) is None and home(conn, theirs) == ("fake", "saved")


def test_the_account_is_noted_when_the_partner_reports_the_bank_account_saved(
    conn, pmc, lease, tenant
):
    mine = conn.execute(
        "INSERT INTO trust_autopay (pmc_id, lease_id, tenant_id, rule, amount, partner)"
        " VALUES (%s, %s, %s, 'balance', '2000.00', 'stripe') RETURNING id",
        (pmc.pmc_id, lease, tenant),
    ).fetchone()[0]
    report = payments.Saved(
        mine, payments.Account("cus_test", "pm_test"), "Test bank ••••0000", "evt_1", LONG_AGO
    )

    assert op.receive(conn, Stripe("acct_saved_at"), report).recorded
    assert home(conn, mine) == ("acct_saved_at", "saved")
    # Reported again: noted once.
    assert op.receive(conn, Stripe("acct_other"), report).recorded
    assert home(conn, mine) == ("acct_saved_at", "saved")
    # Another partner's report saves nothing and notes nothing.
    other = uuid4()
    assert not autopay_homes.note_saved(conn, other, Stripe()) and home(conn, other) is None


def test_stop_moved_needs_a_partner_and_its_account(conn, pmc, lease, tenant):
    mine = saved(conn, pmc, lease, tenant, at="acct_old")

    for partner, account in ((None, "acct_new"), (Stripe(), None)):
        moved = autopay_homes.stop_moved(conn, pmc.pmc_id, partner, account, TODAY)
        assert (moved.stopped, moved.emails) == (0, 0)
    assert autopay_homes.account_of(payments.FakePartner()) == "fake"
    assert autopay_homes.account_of(object()) is None
    assert stopped(conn, mine) is None


def test_stripe_is_asked_its_account_once_per_keys(monkeypatch):
    key = "sk_test_" + uuid4().hex
    calls = []

    def call(self, method, path, form, key=""):
        calls.append((method, path))
        return {"id": "acct_1Test", "object": "account"}

    monkeypatch.setattr(payments.StripePartner, "_call", call)
    first = payments.StripePartner(key, "whsec_" + "y" * 24)
    again = payments.StripePartner(key, "whsec_" + "z" * 24)

    assert first.account() == again.account() == "acct_1Test"
    assert calls == [("GET", "/account")]
    # The cache holds the keys' SHA-256, never the keys.
    assert all(key not in held for held in payments.ACCOUNTS)

    monkeypatch.setattr(payments.StripePartner, "_call", lambda *a, **k: {"object": "account"})
    with pytest.raises(payments.PartnerFailed, match="account came without"):
        payments.StripePartner("sk_test_" + uuid4().hex, "whsec_" + "y" * 24).account()


def test_stopped_autopays_leave_the_balance_to_pay_by_hand(conn, pmc, lease, tenant):
    mine = saved(conn, pmc, lease, tenant, at="acct_old")
    night(conn, pmc, Stripe("acct_new"))
    assert autopay.active(conn, pmc.pmc_id, TODAY) == []
    assert stopped(conn, mine) == "account changed"
    assert (
        Decimal("1200.00")
        == conn.execute(
            "SELECT sum(o.open) FROM trust_open_charges(%s, %s, 'infinity') AS o",
            (pmc.pmc_id, lease),
        ).fetchone()[0]
    )
