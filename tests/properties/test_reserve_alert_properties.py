"""Property-based tests for an owner's account below its reserve (migration 20261022000150,
src/pms/notices.py; the founder, 2026-10-22, q054).

Hypothesis starts an owner's property account with a reserve and the alerts on, then moves money
in and out of it (contributions in, bills set aside out), sets a new reserve from a day, turns the
alerts off and on, and runs the day's emails on random days, in any order and as often as it
likes. A model keeps the account's balance, the
reserves from their days, the day of the latest drop or return and whether the account was below
its reserve at the last look that counted:
  - a look (alerts on, a day not before the latest drop or return) records a drop exactly when
    the account is below the reserve in force that day and was not at the last look, and a return
    exactly when it is no longer below and was;
  - the drops and returns alternate, numbered 1, 2, 3... from a drop, each holding what the account
    held and the reserve that day;
  - the owner gets one email per drop, naming it, and nothing else is written; another company's
    account is never looked at.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
from helpers import add_agreement, contribute, enter_bill, make_pmc, set_aside_bill
from hypothesis import event, given
from hypothesis import strategies as st

from pms import notices

URL = "https://pms.example"
FIRST_DAY = date(2026, 10, 1)
DAYS = st.integers(min_value=0, max_value=60).map(lambda n: FIRST_DAY + timedelta(days=n))
CENTS = st.integers(min_value=1, max_value=60_000).map(lambda n: Decimal(n) / 100)
ACTIONS = st.lists(
    st.one_of(
        st.tuples(st.just("in"), CENTS),
        st.tuples(st.just("in"), CENTS),
        st.tuples(st.just("out"), CENTS),
        st.tuples(st.just("reserve"), DAYS, st.integers(min_value=0, max_value=500)),
        st.tuples(st.just("alerts"), st.booleans()),
        st.tuples(st.just("run"), DAYS),
        st.tuples(st.just("run"), DAYS),
    ),
    min_size=1,
    max_size=16,
)


def turn(conn, pmc_id, on):
    conn.execute(
        "INSERT INTO trust_email_settings (pmc_id, late_notices, work_order_notices,"
        " adverse_action_notices, reserve_alerts) VALUES (%s, false, false, false, %s)",
        (pmc_id, on),
    )


@given(reserve=st.integers(min_value=0, max_value=500), actions=ACTIONS)
def check(conn, reserve, actions):
    ours, theirs = make_pmc(conn), make_pmc(conn)
    owner, account = ours.owners[0].owner_id, ours.owners[0].account
    conn.execute(
        "INSERT INTO trust_contacts (pmc_id, owner_id, email) VALUES (%s, %s, %s)",
        (ours.pmc_id, owner, "owner1@example.com"),
    )
    # Terms from before the first day, with a reserve; bills set aside need no approval. The
    # alerts start on.
    add_agreement(
        conn, ours.pmc_id, account, date(2026, 9, 1), reserve=str(reserve),
        approval_limit="1000000",
    )  # fmt: skip
    turn(conn, ours.pmc_id, True)
    # Their account is always below its reserve and their alerts are on: never looked at here.
    add_agreement(conn, theirs.pmc_id, theirs.owners[0].account, FIRST_DAY, reserve="1.00")
    turn(conn, theirs.pmc_id, True)

    held = Decimal(0)
    reserves: dict[date, Decimal] = {date(2026, 9, 1): Decimal(reserve)}  # from a day
    on = True
    last_event: date | None = None  # a look on a day before it changes nothing
    below_then = False
    expected: list[tuple] = []  # (number, kind, day, balance, reserve)
    for n, action in enumerate(actions):
        kind = action[0]
        if kind == "in":
            contribute(conn, ours.pmc_id, account, f"c-{n}", action[1], datetime.now(UTC))
            held += action[1]
        elif kind == "out" and action[1] <= held:
            # Set aside the day it is entered; within the owner's approval limit.
            bill = enter_bill(
                conn, ours.pmc_id, ours.vendor_id, account, f"B-{n}", FIRST_DAY, FIRST_DAY,
                action[1],
            )  # fmt: skip
            set_aside_bill(conn, ours.pmc_id, bill, datetime.now(UTC))
            held -= action[1]
        elif kind == "reserve" and action[1] not in reserves:
            add_agreement(
                conn, ours.pmc_id, account, action[1], reserve=str(action[2]),
                approval_limit="1000000",
            )  # fmt: skip
            reserves[action[1]] = Decimal(action[2])
        elif kind == "alerts":
            turn(conn, ours.pmc_id, action[1])
            on = action[1]
        elif kind == "run":
            day = action[1]
            with conn.transaction():
                notices.write_scheduled(conn, ours.pmc_id, day, URL)
            if not on or (last_event is not None and day < last_event):
                continue
            in_force = [d for d in reserves if d <= day]
            reserve = reserves[max(in_force)] if in_force else Decimal(0)
            below = held < reserve
            if below != below_then:
                expected.append(
                    (len(expected) + 1, "below" if below else "back", day, held, reserve)
                )
                last_event = day
                event("a drop" if below else "a return")
            below_then = below

    got = conn.execute(
        "SELECT number, kind, found_on, balance, reserve FROM trust_reserve_events"
        " WHERE pmc_id = %s ORDER BY number",
        (ours.pmc_id,),
    ).fetchall()
    assert got == expected
    emails = conn.execute(
        "SELECT r.number, e.owner_id, e.to_address FROM trust_emails AS e"
        " JOIN trust_reserve_events AS r ON r.id = e.reserve_event_id"
        " WHERE e.pmc_id = %s AND e.kind = 'reserve_low' ORDER BY r.number",
        (ours.pmc_id,),
    ).fetchall()
    assert emails == [
        (number, owner, "owner1@example.com") for number, kind, *_ in expected if kind == "below"
    ]
    assert conn.execute(
        "SELECT count(*) FROM trust_emails WHERE pmc_id = %s", (ours.pmc_id,)
    ).fetchone() == (len(emails),)
    assert conn.execute(
        "SELECT count(*) FROM trust_reserve_events WHERE pmc_id = %s", (theirs.pmc_id,)
    ).fetchone() == (0,)


def test_an_owner_hears_once_per_drop_below_the_reserve_whatever_moves_and_whenever_it_runs(
    database_url,
):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
