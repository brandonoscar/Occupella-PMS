"""Property-based tests for the emails the PMS writes by itself (migration 20261017000077,
src/pms/notices.py).

Hypothesis changes the company's settings (rent reminders some days before the first, owner
statements from a day of the month, or neither) and runs the day's emails on random days of
October to December 2026, in any order and as often as it likes, for a lease of two tenants with
emails and two owners, one of them with an email. A model keeps the settings in force at each run:
  - a tenant has a rent reminder for a due day exactly when some run fell no more than the
    reminder days before it, with reminders on; each at most once, to the tenant's address;
  - an owner with an email has a month's statement exactly when some run fell in the next month
    on or after the statement day, with statements on; each at most once; the owner without an
    email has none;
  - nothing else is written, and another company's settings write nothing for this one.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date, timedelta

import psycopg
from helpers import add_tenant, add_unit, make_pmc, open_lease
from hypothesis import event, given
from hypothesis import strategies as st

from pms import notices

URL = "https://pms.example"
FIRST_DAY = date(2026, 10, 1)
DAYS = st.integers(min_value=0, max_value=91).map(lambda n: FIRST_DAY + timedelta(days=n))
ACTIONS = st.lists(
    st.one_of(
        st.tuples(
            st.just("settings"),
            st.booleans(),  # this company's, or another's
            st.none() | st.integers(min_value=1, max_value=15),
            st.none() | st.integers(min_value=1, max_value=28),
        ),
        st.tuples(st.just("run"), DAYS),
    ),
    min_size=1,
    max_size=10,
)


def save(conn, pmc_id, reminder_days, statement_day):
    conn.execute(
        "INSERT INTO trust_email_settings (pmc_id, rent_reminder_days, late_notices,"
        " owner_statement_day, work_order_notices, adverse_action_notices)"
        " VALUES (%s, %s, false, %s, false, false)",
        (pmc_id, reminder_days, statement_day),
    )


@given(actions=ACTIONS)
def check(conn, actions):
    ours, theirs = make_pmc(conn), make_pmc(conn)
    prop = ours.owners[0].property_id
    tenants = [add_tenant(conn, ours.pmc_id, prop, f"Tenant {n}") for n in (2, 3)]
    for n, tenant in enumerate(tenants, 2):
        conn.execute(
            "INSERT INTO trust_contacts (pmc_id, tenant_id, email) VALUES (%s, %s, %s)",
            (ours.pmc_id, tenant, f"tenant{n}@example.com"),
        )
    lease = open_lease(
        conn, ours.pmc_id, add_unit(conn, ours.pmc_id, prop), date(2026, 9, 1), None, "900.00",
        tenants,
    )  # fmt: skip
    owner = ours.owners[0].owner_id
    conn.execute(
        "INSERT INTO trust_contacts (pmc_id, owner_id, email) VALUES (%s, %s, %s)",
        (ours.pmc_id, owner, "owner1@example.com"),
    )

    settings = (None, None)  # (reminder days, statement day) in force for this company
    reminders: set[tuple] = set()  # (tenant, due day)
    statements: set[tuple] = set()  # (owner, month)
    for action in actions:
        if action[0] == "settings":
            _, mine, reminder_days, statement_day = action
            save(conn, (ours if mine else theirs).pmc_id, reminder_days, statement_day)
            if mine:
                settings = (reminder_days, statement_day)
            continue
        day = action[1]
        with conn.transaction():
            notices.write_scheduled(conn, ours.pmc_id, day, URL)
        reminder_days, statement_day = settings
        due = notices.next_due(day)
        if reminder_days is not None and (due - day).days <= reminder_days:
            reminders |= {(tenant, due) for tenant in tenants}
            event("reminders due")
        if statement_day is not None and day.day >= statement_day:
            statements.add((owner, (day.replace(day=1) - timedelta(days=1)).replace(day=1)))
            event("statements due")

    written = conn.execute(
        "SELECT kind, tenant_id, owner_id, lease_id, period, to_address FROM trust_emails"
        " WHERE pmc_id = %s",
        (ours.pmc_id,),
    ).fetchall()
    got_reminders = [(t, p) for kind, t, _, _, p, _ in written if kind == "rent_reminder"]
    got_statements = [(o, p) for kind, _, o, _, p, _ in written if kind == "owner_statement"]
    assert sorted(got_reminders) == sorted(reminders)  # each once
    assert sorted(got_statements) == sorted(statements)
    assert len(written) == len(reminders) + len(statements)
    for kind, tenant, _, lease_id, _, to in written:
        if kind == "rent_reminder":
            assert lease_id == lease
            assert to == f"tenant{tenants.index(tenant) + 2}@example.com"
        else:
            assert to == "owner1@example.com"
    assert conn.execute(
        "SELECT count(*) FROM trust_emails WHERE pmc_id = %s", (theirs.pmc_id,)
    ).fetchone() == (0,)


def test_each_automatic_email_is_written_once_exactly_when_its_settings_and_day_call_for_it(
    database_url,
):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
