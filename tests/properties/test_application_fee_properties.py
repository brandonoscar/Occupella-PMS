"""Property-based tests for application fees (src/pms/api/application_fees.py, migration
20261022000160).

Hypothesis starts any fee payments on applications of any number of applicants and sends any run
of processor events (succeeded, failed, some again):
  - an event is taken once, and only for a payment that started and hasn't ended;
  - an application's fee is recorded once, at what was asked, however many payments of it
    succeed (a second is kept as failed, for a refund);
  - what the company's fee account in trust gained is exactly the fees recorded.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import secrets
from datetime import date
from decimal import Decimal

import psycopg
from helpers import add_staff, add_unit, make_pmc
from hypothesis import event, given
from hypothesis import strategies as st

from pms import accounts
from pms.api import application_fees
from pms.payments.processor import Event

steps = st.lists(
    st.one_of(
        st.tuples(st.just("apply"), st.integers(1, 4)),
        st.tuples(st.just("start"), st.integers(0, 9)),
        st.tuples(
            st.just("event"), st.integers(0, 20), st.sampled_from(["succeeded", "failed"]),
            st.booleans(),
        ),
    ),
    min_size=1,
    max_size=16,
)  # fmt: skip


@given(run=steps, fee=st.integers(1, 20_000))
def check_fees(conn, run, fee):
    pmc = make_pmc(conn, owners=1)
    per_applicant = Decimal(fee) / 100
    conn.execute(
        "INSERT INTO trust_application_fee_settings (pmc_id, per_applicant) VALUES (%s, %s)",
        (pmc.pmc_id, per_applicant),
    )
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    link = conn.execute(
        "INSERT INTO trust_application_links (pmc_id, unit_id, token_sha256, monthly_rent,"
        " opened_by) VALUES (%s, %s, %s, 1500.00, %s) RETURNING id",
        (pmc.pmc_id, unit, accounts.digest(secrets.token_urlsafe(32)), add_staff(conn, pmc.pmc_id)),
    ).fetchone()[0]
    before = conn.execute(
        "SELECT balance FROM pgledger_accounts WHERE id = %s", (pmc.pmc_income,)
    ).fetchone()[0]
    applications: list[tuple[str, Decimal]] = []  # id, asked
    payments: list[tuple[str, str]] = []  # reference, application
    state: dict[str, str] = {}  # reference -> started, succeeded, failed (the model)
    recorded: dict[str, Decimal] = {}  # application -> fee recorded
    sent: list[Event] = []
    for step in run:
        if step[0] == "apply":
            (app,) = conn.execute(
                "INSERT INTO trust_applications (pmc_id, link_id, status_token_sha256, move_in_on,"
                " monthly_income) VALUES (%s, %s, %s, %s, 4500) RETURNING id",
                (pmc.pmc_id, link, accounts.digest(secrets.token_urlsafe(32)), date(2026, 11, 1)),
            ).fetchone()
            for n in range(step[1]):
                conn.execute(
                    "INSERT INTO trust_applicants (pmc_id, application_id, position, display_name,"
                    " email) VALUES (%s, %s, %s, %s, %s)",
                    (pmc.pmc_id, app, n + 1, f"Applicant {n}", f"a{n}@example.com"),
                )
            applications.append((str(app), per_applicant * step[1]))
        elif step[0] == "start" and applications:
            app, asked = applications[step[1] % len(applications)]
            reference = f"fake_fee_{secrets.token_hex(6)}"
            (fee_id,) = conn.execute(
                "INSERT INTO trust_application_fees (pmc_id, application_id, amount, method,"
                " processor) VALUES (%s, %s, %s, 'card', 'fake') RETURNING id",
                (pmc.pmc_id, app, asked),
            ).fetchone()
            conn.execute(
                "INSERT INTO trust_application_fee_steps (pmc_id, fee_id, step, reference)"
                " VALUES (%s, %s, 'started', %s)",
                (pmc.pmc_id, fee_id, reference),
            )
            payments.append((reference, app))
            state[reference] = "started"
        elif step[0] == "event" and payments:
            reference, app = payments[step[1] % len(payments)]
            if step[3] and sent:
                said, expected = sent[step[1] % len(sent)], False
            else:
                said = Event(f"evt-{len(sent)}-{reference}", step[2], reference, "pay", "no")
                expected = state[reference] == "started"
            with conn.transaction():
                taken = application_fees.take(conn, "fake", said)
            assert taken == expected
            if taken:
                sent.append(said)
                state[reference] = said.outcome
                if said.outcome == "succeeded" and app not in recorded:
                    recorded[app] = dict(applications)[app]
                    event("a fee recorded")
                elif said.outcome == "succeeded":
                    event("a second payment kept for a refund")
                else:
                    event("a payment failed")
            else:
                event("not taken")

    after = conn.execute(
        "SELECT balance FROM pgledger_accounts WHERE id = %s", (pmc.pmc_income,)
    ).fetchone()[0]
    assert after - before == sum(recorded.values(), Decimal(0))
    for app, _ in applications:
        paid = application_fees.paid_of(conn, app)
        assert paid == recorded.get(app)


def test_an_applications_fee_is_recorded_once_and_the_company_holds_exactly_it(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_fees(conn)
