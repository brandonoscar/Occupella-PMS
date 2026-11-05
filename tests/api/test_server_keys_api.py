"""The server's own Stripe keys serve only a server holding one company (payments.Resolver):
they are one Stripe account, paying out to one company's trust account, so on a server with more
companies a company without keys of its own takes no payment online until it enters its own, and
its admin sees that the server's partner isn't theirs. The fake partner, which moves no money,
serves any server. Every key here is made up, and spelled in pieces."""

import base64
import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from helpers import add_unit, charge, make_pmc, open_lease
from people import join_as_staff, join_as_tenant, tenant_of

from pms import payments
from pms.api import Settings, create_app

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
SECRET_KEY = "sk_" + "test_" + "Synthetic000000000000C3d4"
WEBHOOK = "whsec_" + "Synthetic0000000000000003"


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("PMS_DATA_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("PMS_STRIPE_SECRET_KEY", "sk_" + "test_" + "Synthetic000000000000Srv1")
    monkeypatch.setenv("PMS_STRIPE_WEBHOOK_SECRET", "whsec_" + "Synthetic0000000000000Srv")


@pytest.fixture
def two_companies(conn):
    """The test database holds many companies; two more make sure."""
    return make_pmc(conn), make_pmc(conn)


@pytest.fixture
def stripe_server(database_url):
    settings = Settings(
        database_url=database_url,
        public_url="http://testserver",
        cookie_secure=False,
        payments="stripe",
    )
    with TestClient(create_app(settings)) as staff, TestClient(create_app(settings)) as tenant:
        yield staff, tenant


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def test_many_companies_counts_more_than_one(app_conn, two_companies):
    assert payments.many_companies(app_conn)


def test_a_real_server_partner_serves_no_company_on_a_server_with_many(app_conn, two_companies):
    server = payments.StripePartner("sk_" + "test_x", "whsec_" + "x")
    resolver = payments.Resolver(server)
    assert resolver.server_for(app_conn) is None
    assert resolver.for_pmc(app_conn, two_companies[0].pmc_id) is None
    assert resolver.name_for(app_conn, two_companies[1].pmc_id) is None


def test_a_real_server_partner_serves_a_server_with_one_company(
    app_conn, two_companies, monkeypatch
):
    monkeypatch.setattr(payments, "many_companies", lambda conn: False)
    server = payments.StripePartner("sk_" + "test_x", "whsec_" + "x")
    resolver = payments.Resolver(server)
    assert resolver.server_for(app_conn) is server
    assert resolver.for_pmc(app_conn, two_companies[0].pmc_id) is server


def test_the_fake_partner_serves_any_server(app_conn, two_companies):
    fake = payments.FakePartner()
    resolver = payments.Resolver(fake)
    assert resolver.server_for(app_conn) is fake
    assert resolver.for_pmc(app_conn, two_companies[0].pmc_id) is fake


def test_no_server_partner_serves_nobody(app_conn, two_companies):
    assert payments.Resolver(None).server_for(app_conn) is None


def test_the_admin_sees_the_servers_keys_arent_theirs(stripe_server, conn, app_conn, two_companies):
    staff, _ = stripe_server
    pmc = two_companies[0]
    join_as_staff(staff, conn, app_conn, pmc)
    shown = ok(staff.get("/api/payments/keys"))
    assert (shown["using"], shown["server_partner"]) == ("none", None)
    assert ok(staff.get("/api/payments/settings"))["partner"] is None


def test_a_tenant_cant_pay_online_until_the_company_enters_its_keys(
    stripe_server, conn, app_conn, two_companies
):
    staff, tenant = stripe_server
    pmc = two_companies[0]
    join_as_staff(staff, conn, app_conn, pmc)
    ok(staff.post("/api/payments/settings", json={"online": True, "ach_fee_payer": "company"}), 204)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(
        conn, pmc.pmc_id, unit, TODAY - 60 * DAY, None, "1200.00", [tenant_of(conn, pmc)]
    )
    charge(conn, pmc.pmc_id, lease, TODAY - 10 * DAY, "1200.00")
    join_as_tenant(tenant, conn, app_conn, pmc)
    assert ok(tenant.get(f"/api/payments/leases/{lease}"))["why_not"] == "off"
    refused = tenant.post(f"/api/payments/leases/{lease}/checkout", json={"amount": "1200.00"})
    assert refused.status_code in (403, 409), refused.text

    # Its own keys: the company's own Stripe account takes its payments.
    ok(staff.post("/api/payments/keys", json={"secret_key": SECRET_KEY, "webhook_secret": WEBHOOK}))
    assert ok(staff.get("/api/payments/keys"))["using"] == "own"
    assert ok(staff.get("/api/payments/settings"))["partner"] == "stripe"
    assert ok(tenant.get(f"/api/payments/leases/{lease}"))["why_not"] is None
