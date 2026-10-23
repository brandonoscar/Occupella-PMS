"""Property-based tests for balance notices (src/pms/api/balance_notices.py, migration
20261023000201).

Hypothesis gives a company's leases any charges, pays any part of them, gives any tenant an email or
none, and sends a balance notice to every lease the PMS says owes:
  - the leases offered are exactly those that owe something, each with what it owes;
  - each notice lists every open charge once, and its lines come to what the lease owes;
  - each tenant with an email on file gets one email of it, the rest none, and it ends saying it is
    a reminder, not the legal notice.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

import psycopg
from helpers import (
    add_staff,
    add_tenant,
    add_unit,
    apply_payment,
    charge,
    make_pmc,
    open_lease,
    transfer_batch,
)
from hypothesis import event, given
from hypothesis import strategies as st

from pms.api import balance_notices, notice_templates

DAY = date(2026, 10, 23)

leases = st.lists(
    st.fixed_dictionaries(
        {
            # Each charge: its cents, and the cents of it paid.
            "charges": st.lists(
                st.tuples(st.integers(1, 200_000), st.integers(0, 200_000)), max_size=4
            ),
            "emails": st.lists(st.booleans(), min_size=1, max_size=3),
        }
    ),
    min_size=1,
    max_size=3,
)


@dataclass(frozen=True)
class Session:
    pmc_id: UUID
    person_id: UUID


@given(plan=leases)
def check_notices(conn, plan):
    pmc = make_pmc(conn)
    prop = pmc.owners[0].property_id
    staff = add_staff(conn, pmc.pmc_id)
    owed_by: dict[UUID, Decimal] = {}
    emailed_by: dict[UUID, int] = {}
    for n, lease_plan in enumerate(plan):
        tenants = []
        for k, has_email in enumerate(lease_plan["emails"]):
            tenant = add_tenant(conn, pmc.pmc_id, prop, f"Tenant {n}-{k}")
            if has_email:
                conn.execute(
                    "INSERT INTO trust_contacts (pmc_id, tenant_id, email) VALUES (%s, %s, %s)",
                    (pmc.pmc_id, tenant, f"tenant.{n}.{k}@example.com"),
                )
            tenants.append(tenant)
        unit = add_unit(conn, pmc.pmc_id, prop, f"Unit {n}")
        lease = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "1000.00", tenants)
        owed = Decimal(0)
        for m, (cents, paid_cents) in enumerate(lease_plan["charges"]):
            amount = Decimal(cents) / 100
            charged = charge(conn, pmc.pmc_id, lease, date(2026, 1 + m, 1), amount)
            paid = min(Decimal(paid_cents) / 100, amount)
            if paid > 0:
                (moved,) = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, paid)])
                apply_payment(conn, pmc.pmc_id, charged, moved, paid)
            owed += amount - paid
        owed_by[lease] = owed
        emailed_by[lease] = sum(lease_plan["emails"])

    offered = balance_notices.owing(conn, pmc.pmc_id, DAY)
    owing = {lease: owed for lease, owed in owed_by.items() if owed > 0}
    assert {o.lease_id: Decimal(o.owed) for o in offered} == owing
    event(f"leases owing: {len(owing)}")

    template = conn.execute(
        "INSERT INTO trust_notice_templates (pmc_id) VALUES (%s) RETURNING id", (pmc.pmc_id,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO trust_notice_template_versions (pmc_id, template_id, name, body)"
        " VALUES (%s, %s, 'Reminder', 'Dear {{tenant_names}}, you owe {{balance_due}}.')",
        (pmc.pmc_id, template),
    )
    found = notice_templates.one(conn, pmc.pmc_id, template, "notice")
    session = Session(pmc.pmc_id, staff)
    run = conn.execute(
        "INSERT INTO trust_balance_notice_runs (pmc_id, template_version_id, dated_on)"
        " VALUES (%s, %s, %s) RETURNING id",
        (pmc.pmc_id, found.version_id, DAY),
    ).fetchone()[0]
    for lease, owed in owing.items():
        sent = balance_notices.one_notice(conn, session, run, found, lease, DAY)
        body = conn.execute(
            "SELECT body FROM trust_notices WHERE id = %s", (sent.notice_id,)
        ).fetchone()[0]
        lines = re.findall(r"^  \d\d/\d\d/\d{4}  Rent  \$([\d,]+\.\d\d)$", body, re.M)
        opens = conn.execute(
            "SELECT count(*) FROM trust_open_charges(%s, %s, 'infinity') WHERE open > 0",
            (pmc.pmc_id, lease),
        ).fetchone()[0]
        assert len(lines) == opens
        assert sum(Decimal(x.replace(",", "")) for x in lines) == owed
        assert f"  Total  ${owed:,.2f}" in body
        assert body.endswith(balance_notices.REMINDER)
        emails = conn.execute(
            "SELECT count(*) FROM trust_emails WHERE notice_id = %s", (sent.notice_id,)
        ).fetchone()[0]
        assert emails == emailed_by[lease] == len(sent.emailed)


def test_a_balance_notice_lists_what_its_lease_owes_to_each_tenant_with_an_email(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_notices(conn)
