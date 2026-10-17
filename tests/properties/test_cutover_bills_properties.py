"""Property-based tests for the unpaid bills a PMC carries over from its old system
(src/pms/api/switching.py, the "bills" file).

Hypothesis writes a bills file: bills against two owners' property accounts, some over the
owner's approval limit and some of those approved in the old system, some dated after today, and
sends it, sometimes twice. After it:
  - the owners page holds back from a draw exactly the bills dated by today, and shows as
    available the balance less the reserve and those bills, at least 0;
  - a bill over the owner's limit without the old system's approval waits for the owner, and
    every other bill waits to be set aside;
  - the file sent again adds nothing and keeps every bill.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
from helpers import add_agreement, make_pmc, post
from hypothesis import event, given
from hypothesis import strategies as st

from pms.accounts import Session
from pms.api import payables, switching

TODAY = datetime.now(UTC).date()
LIMIT = Decimal("500.00")
RESERVE = Decimal("100.00")
HELD = Decimal("2000.00")
BILLS = st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=1),  # which owner's account
        st.integers(min_value=1, max_value=100_000).map(lambda c: Decimal(c) / 100),
        st.booleans(),  # approved in the old system
        st.booleans(),  # dated after today
    ),
    min_size=1,
    max_size=8,
)


def names_of(conn, pmc, n):
    owner = pmc.owners[n]
    return conn.execute(
        "SELECT o.display_name, p.display_name FROM trust_owners AS o, trust_properties AS p"
        " WHERE o.id = %s AND p.id = %s",
        (owner.owner_id, owner.property_id),
    ).fetchone()


@given(bills=BILLS, resend=st.booleans())
def check_bills(conn, bills, resend):
    pmc = make_pmc(conn)
    session = Session(uuid.uuid4(), pmc.pmc_id, "staff", uuid.uuid4(), "s@example.com")
    vendor = conn.execute(
        "SELECT display_name FROM trust_vendors WHERE id = %s", (pmc.vendor_id,)
    ).fetchone()[0]
    for n, owner in enumerate(pmc.owners):
        add_agreement(
            conn,
            pmc.pmc_id,
            owner.account,
            TODAY - timedelta(days=60),
            reserve=str(RESERVE),
            approval_limit=str(LIMIT),
        )
        post(conn, pmc.pmc_id, f"rent-{n}", [(pmc.operating_cash, owner.account, HELD)])
    lines = ["Vendor,Owner,Property,Reference,Bill date,Due on,Amount,Memo,Approved by,Approved on"]
    for i, (n, amount, approved, ahead) in enumerate(bills):
        owner, prop = names_of(conn, pmc, n)
        dated = TODAY + timedelta(days=3) if ahead else TODAY - timedelta(days=10)
        by, on = (owner, dated) if approved else ("", "")
        lines.append(
            f"{vendor},{owner},{prop},OLD-{i},{dated},{dated + timedelta(days=10)},{amount},"
            f"Repair {i},{by},{on}"
        )
    new = switching.Import(kind="bills", content="\n".join(lines) + "\n")

    def send():
        with conn.transaction():
            return switching.import_file(session, conn, new)

    assert send() == switching.Counted(added=len(bills), kept=0)
    if resend:
        event("sent again")
        assert send() == switching.Counted(added=0, kept=len(bills))

    with conn.transaction():
        owners = {o.account: o for o in payables.owners(session, conn)}
        listed = {b.reference: b.status for b in payables.find_bills(conn, session.pmc_id)[0]}
    for n, owner in enumerate(pmc.owners):
        unpaid = sum((b[1] for b in bills if b[0] == n and not b[3]), Decimal(0))
        row = owners[owner.account]
        assert (Decimal(row.balance), Decimal(row.unpaid_bills)) == (HELD, unpaid)
        assert Decimal(row.available) == max(HELD - RESERVE - unpaid, Decimal(0))
    for i, (_, amount, approved, _) in enumerate(bills):
        waits = amount > LIMIT and not approved
        event("waits for the owner" if waits else "to set aside")
        assert listed[f"OLD-{i}"] == ("needs approval" if waits else "to set aside")


def test_unpaid_bills_carried_over_hold_back_draws_and_wait_for_their_owner(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_bills(conn)
