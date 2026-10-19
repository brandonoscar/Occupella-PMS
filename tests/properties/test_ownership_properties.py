"""Property-based tests for a property owned in shares (migration 20261019000094, the founder's
review q044).

Hypothesis gives Property 1 two to four owners in random shares from January 1 (each to four
decimals, together 100), maybe new shares from May 1 between some of them, and a lease there with
random rent, fees and credits. It takes payments in February and June, maybe bounces one, uses the
prepaid rent and maybe returns a deposit at move-out. A model splits each amount as the shares in
force on its day say: each owner's percent of it rounded down to the cent, the cents left over
one each to the largest remainders, the account first by id on a tie. After every step:
  - each owner's account moved by exactly the model's part of what went to the owners, and the
    parts come to it, each within a cent of the owner's exact share;
  - a bounce takes back from each owner exactly what the payment gave them;
  - what went to the owners is what the lease owed, matched to its charges, the rest prepaid rent;
  - the deposit kept at move-out goes to the owners by their shares on that day, piece by piece;
  - every trust bank account ties out.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
from helpers import (
    add_unit,
    balance,
    bank_sums,
    charge,
    co_own,
    make_pmc,
    open_lease,
    set_ownership,
    transfer_batch,
)
from hypothesis import event, given
from hypothesis import strategies as st

from pms.api import rent

JAN_1, MAY_1, JUN_30 = date(2026, 1, 1), date(2026, 5, 1), date(2026, 6, 30)
PAID_ON = [date(2026, 2, 10), date(2026, 6, 10)]
USED_ON = date(2026, 6, 20)
RETURNED = datetime(2026, 7, 5, 12, tzinfo=UTC)
MONEY = st.integers(min_value=1, max_value=200_000).map(lambda c: Decimal(c) / 100)
WHOLE = 1_000_000  # 100% in ten-thousandths of a percent


def percents(cuts):
    """The percents between cut points of 100%, in ten-thousandths."""
    ends = [0, *sorted(cuts), WHOLE]
    return [Decimal(b - a) / 10_000 for a, b in zip(ends, ends[1:], strict=False)]


def shares_of(size):
    """`size` percents above 0, to four decimals, together 100."""
    cut = st.integers(min_value=1, max_value=WHOLE - 1)
    return st.lists(cut, min_size=size - 1, max_size=size - 1, unique=True).map(percents)


@st.composite
def ownership(draw):
    owners = draw(st.integers(min_value=2, max_value=4))
    first = draw(shares_of(owners))
    later = None
    if draw(st.booleans()):
        who = draw(st.lists(st.integers(0, owners - 1), min_size=1, max_size=owners, unique=True))
        later = list(zip(who, draw(shares_of(len(who))), strict=True))
    return owners, first, later


def at(day):
    return datetime(day.year, day.month, day.day, 12, tzinfo=UTC)


def split(amount, shares):
    """The model: `amount` split by `shares` ({account: percent}), to the cent."""
    cents = int(amount * 100)
    exact = {a: Decimal(cents) * p / 100 for a, p in shares.items()}
    whole = {a: int(e) for a, e in exact.items()}
    spare = cents - sum(whole.values())
    for a in sorted(shares, key=lambda a: (whole[a] - exact[a], a))[:spare]:
        whole[a] += 1
    return {a: Decimal(w) / 100 for a, w in whole.items() if w > 0}


def owed(app, lease):
    return app.execute("SELECT trust_lease_owed(%s)", (lease,)).fetchone()[0]


@given(
    owned=ownership(),
    charges=st.lists(
        st.tuples(
            st.sampled_from(["rent", "fee", "credit"]),
            MONEY,
            st.integers(min_value=0, max_value=5),  # months into the lease
        ),
        max_size=6,
    ),
    payments=st.lists(st.tuples(MONEY, st.sampled_from(PAID_ON)), max_size=3),
    bounce=st.one_of(st.none(), st.integers(min_value=0, max_value=2)),
    deposit=st.one_of(st.just(Decimal(0)), MONEY),
)
def check(conn, app, owned, charges, payments, bounce, deposit):
    count, first, later = owned
    pmc = make_pmc(conn, owners=count)
    # Owner 1's account at Property 1, and each other owner's as a co-owner of it.
    accounts = [pmc.owners[0].account] + [co_own(conn, pmc, owner=n) for n in range(1, count)]
    prop = pmc.owners[0].property_id
    set_ownership(conn, pmc.pmc_id, prop, JAN_1, list(zip(accounts, first, strict=True)))
    in_force = [(JAN_1, dict(zip(accounts, first, strict=True)))]
    if later is not None:
        set_ownership(conn, pmc.pmc_id, prop, MAY_1, [(accounts[n], p) for n, p in later])
        in_force.append((MAY_1, {accounts[n]: p for n, p in later}))
    event(f"{count} owners" + (", new shares in May" if later else ""))

    def shares(day):
        return [s for start, s in in_force if start <= day][-1]

    def held():
        return {a: balance(conn, a) for a in accounts}

    def gave(before, after):
        return {a: after[a] - before[a] for a in accounts if after[a] != before[a]}

    def check_parts(parts, amount, day):
        """The model's parts, together `amount`, each within a cent of its exact share."""
        assert parts == split(amount, shares(day))
        assert sum(parts.values(), Decimal(0)) == amount
        for a, part in parts.items():
            assert abs(part - amount * shares(day)[a] / 100) < Decimal("0.01")

    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, prop)
    lease = open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "1000.00", [tenant])
    if deposit:
        transfer_batch(conn, [(pmc.deposit_cash, pmc.tenant_deposit, deposit)], at(JAN_1))
    rent_days = set()
    for kind, amount, months in charges:
        due = JAN_1.replace(month=1 + months)
        if kind == "rent" and due in rent_days:
            kind = "fee"
        if kind == "rent":
            rent_days.add(due)
        charge(conn, pmc.pmc_id, lease, due, amount, kind)

    # Payments: what the lease owed goes to the owners by their shares on the day, the rest is
    # prepaid rent; each owner's part is matched to the lease's charges in full.
    recorded = []
    for amount, day in payments:
        before, owing, prepaid = held(), owed(app, lease), balance(conn, pmc.prepaid_rent)
        paid = rent.record_payment(app, pmc.pmc_id, lease, uuid.uuid4(), amount, day)
        to_owners = min(amount, max(owing, Decimal(0)))
        assert (paid.to_owner, paid.to_prepaid) == (to_owners, amount - to_owners)
        parts = gave(before, held())
        check_parts(parts, to_owners, day)
        assert owed(app, lease) == owing - to_owners
        assert balance(conn, pmc.prepaid_rent) == prepaid + amount - to_owners
        matched = app.execute(
            "SELECT coalesce(sum(p.amount), 0) FROM trust_charge_payments AS p"
            " WHERE p.transfer_id = ANY(%s)",
            (paid.transfers,),
        ).fetchone()[0]
        assert matched == to_owners
        assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}
        recorded.append((paid, parts, day))
    event(f"{len(payments)} payments")

    # A bounce takes back from each owner what the payment gave them.
    if bounce is not None and bounce < len(recorded):
        paid, parts, day = recorded[bounce]
        before, owing = held(), owed(app, lease)
        returned = rent.bounce(app, pmc.pmc_id, paid.transfers[0], USED_ON)
        assert gave(before, held()) == {a: -part for a, part in parts.items()}
        assert owed(app, lease) == owing + paid.to_owner + (returned.nsf_fee or 0)
        assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}
        event("bounced one")

    # Prepaid rent used pays the owners by their shares on the day it is used.
    prepaid, owing = balance(conn, pmc.prepaid_rent), owed(app, lease)
    if prepaid > 0 and owing > 0:
        before = held()
        used = rent.use_prepaid(app, pmc.pmc_id, lease, uuid.uuid4(), USED_ON)
        assert used.to_owner == min(prepaid, owing)
        check_parts(gave(before, held()), used.to_owner, USED_ON)
        assert owed(app, lease) == owing - used.to_owner
        assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}
        event("used prepaid rent")

    # At move-out what the lease owes is kept, its rent first, then its other charges, each piece
    # going to the owners by their shares on the day.
    if deposit:
        app.execute("SELECT trust_end_lease(%s, %s, %s)", (pmc.pmc_id, lease, JUN_30))
        rent_open, fees_open = conn.execute(
            "SELECT coalesce(sum(o.open) FILTER (WHERE trust_pay_rank(c) < 2), 0),"
            "       coalesce(sum(o.open) FILTER (WHERE trust_pay_rank(c) = 2), 0)"
            " FROM trust_open_charges(%s, %s, 'infinity') AS o"
            " JOIN trust_charges AS c ON c.id = o.charge_id",
            (pmc.pmc_id, lease),
        ).fetchone()
        keep = min(rent_open + fees_open, deposit)
        pieces = [min(keep, rent_open), keep - min(keep, rent_open)]
        before = held()
        back = app.execute(
            "SELECT trust_return_deposit(%s, %s, NULL, 'back', %s)",
            (pmc.pmc_id, lease, RETURNED),
        ).fetchone()[0]
        assert back == deposit - keep
        day = RETURNED.date()
        want: dict[str, Decimal] = {}
        for piece in pieces:
            for a, part in split(piece, shares(day)).items():
                want[a] = want.get(a, Decimal(0)) + part
        assert gave(before, held()) == want
        assert sum(want.values(), Decimal(0)) == keep
        assert balance(conn, pmc.tenant_deposit) == 0
        assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}
        event("kept all" if keep == deposit else "kept none" if keep == 0 else "kept some")

    # Only the owners' accounts at Property 1 were paid: their own properties took nothing.
    assert all(balance(conn, owner.account) == 0 for owner in pmc.owners[1:])


def test_a_co_owned_propertys_money_splits_by_its_shares_to_the_cent(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app,
    ):
        app.execute("SET ROLE trust_app")
        check(conn, app)
