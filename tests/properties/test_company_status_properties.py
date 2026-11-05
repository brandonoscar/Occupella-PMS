"""Property-based tests for a company's status (migration 20261105000100, `python -m pms.admin
status`).

Hypothesis picks a sequence of statuses the server's operator sets for one company, and after
each it reads the company's status, tries to sign in to it with the right password, and lists the
companies the nightly run would run.

Invariants (ci/registry.toml maps them to this test):
  - a company's status is the latest it was set to, active before any;
  - a person signs in only while it is active, and is refused the same way whatever the password
    while it is suspended or closed;
  - the nightly run passes over a company only while it is closed;
  - another company's status never changes with it.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import psycopg
import pytest
from helpers import make_pmc
from hypothesis import event, given
from hypothesis import strategies as st

from pms import accounts, nightly

STATUSES = st.lists(st.sampled_from(["active", "suspended", "closed"]), min_size=1, max_size=6)
PASSWORD = "correct horse battery staple"


def tenant_login(conn, pmc):
    """A tenant of the company who set a password, and the email they sign in with."""
    tenant = conn.execute(
        "SELECT id FROM trust_tenants WHERE pmc_id = %s LIMIT 1", (pmc.pmc_id,)
    ).fetchone()[0]
    email = f"tenant.{tenant.hex[:8]}@example.com"
    invite = accounts.create_invite(conn, pmc.pmc_id, "tenant", tenant, email, None)
    accounts.accept_invite(conn, invite.token, PASSWORD)
    return email


@given(statuses=STATUSES)
def check(conn, app_conn, statuses):
    pmc, other = make_pmc(conn), make_pmc(conn)
    email = tenant_login(app_conn, pmc)
    assert accounts.company_status(app_conn, pmc.pmc_id) == "active"

    for status in statuses:
        app_conn.execute(
            "INSERT INTO trust_company_statuses (pmc_id, status, reason)"
            " VALUES (%s, %s, 'synthetic')",
            (pmc.pmc_id, status),
        )
        event(status)
        assert accounts.company_status(app_conn, pmc.pmc_id) == status
        assert accounts.company_status(app_conn, other.pmc_id) == "active"

        if status == "active":
            signed = accounts.sign_in(app_conn, pmc.pmc_id, email, PASSWORD, None)
            assert signed.session.pmc_id == pmc.pmc_id
        else:
            for password in (PASSWORD, "a wrong password"):
                with pytest.raises(accounts.CompanyNotActive) as refused:
                    accounts.sign_in(app_conn, pmc.pmc_id, email, password, None)
                assert str(refused.value) == accounts.NOT_ACTIVE[status]

        runs = nightly.companies(app_conn)
        assert (pmc.pmc_id in runs) == (status != "closed")
        assert other.pmc_id in runs


def test_a_companys_status_is_its_latest_and_decides_who_signs_in_and_what_runs(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        check(conn, app_conn)
