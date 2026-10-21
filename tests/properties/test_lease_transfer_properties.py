"""Property-based tests for a lease's tenants moving to another unit (migration
20261018000091).

Hypothesis makes a tenant's lease at Property 1 and moves it one to three times, each move to a
unit of another owner's property (the last maybe back at Property 1). It charges each lease rent,
fees and credits, some while the tenant lives elsewhere, takes payments on the last lease, and at
the end returns the deposit. A model works out what each lease owes; the database must agree:
  - a payment pays the rent each lease the tenant moved from still owes first, oldest lease
    first, then the last lease's rent, then their other charges in the same order (rent first,
    migration 0093), each part into that lease's owner's account; the rest is prepaid rent;
  - every payment matched to a charge went into the owner's account of the charge's own
    property, so each owner is paid what was owed to them and no more;
  - the deposit's return keeps what every lease still owes, the same way, each for its own
    owner, or the whole deposit if that is less, and returns the rest;
  - every trust bank account ties out after every step.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
from helpers import add_unit, balance, bank_sums, charge, make_pmc, open_lease, transfer_batch
from hypothesis import event, given
from hypothesis import strategies as st

from pms.api import rent

STARTS = [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)]
AGREED = date(2026, 1, 20)
PAID_ON = date(2026, 4, 15)
LAST_DAY = date(2026, 6, 30)
RETURNED = datetime(2026, 7, 5, 12, tzinfo=UTC)
MONEY = st.integers(min_value=1, max_value=200_000).map(lambda c: Decimal(c) / 100)


@given(
    moves=st.integers(min_value=1, max_value=3),
    charges=st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=3),  # which lease (the last if past it)
            st.sampled_from(["rent", "fee", "credit"]),
            MONEY,
            st.integers(min_value=0, max_value=27),  # days into that lease's month
        ),
        max_size=8,
    ),
    payments=st.lists(MONEY, max_size=3),
    deposit=st.one_of(st.just(Decimal(0)), MONEY),
)
def check(conn, app, moves, charges, payments, deposit):
    pmc = make_pmc(conn, owners=3)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    # Each lease's property: 1, 2, 3, then back at 1.
    homes = [pmc.owners[n % 3] for n in range(moves + 1)]
    units = [
        add_unit(conn, pmc.pmc_id, home.property_id, f"Unit {n}") for n, home in enumerate(homes)
    ]
    if deposit:
        transfer_batch(
            conn, [(pmc.deposit_cash, pmc.tenant_deposit, deposit)], RETURNED.replace(month=1)
        )
    leases = [open_lease(conn, pmc.pmc_id, units[0], STARTS[0], None, "1000.00", [tenant])]
    for n in range(1, moves + 1):
        leases.append(
            app.execute(
                "SELECT trust_transfer_lease(%s, %s, %s, %s, NULL, 1000.00, NULL, %s)",
                (pmc.pmc_id, leases[-1], units[n], STARTS[n], AGREED),
            ).fetchone()[0]
        )
        assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}
    event(f"{moves} moves")

    # What each lease charged of rent and of other charges, and its credits; rent once per due
    # date.
    charged = [[Decimal(0), Decimal(0)] for _ in leases]
    credits = [Decimal(0)] * len(leases)
    rent_days = set()
    for which, kind, amount, days in charges:
        n = min(which, len(leases) - 1)
        due = STARTS[n].replace(day=1 + days)
        if kind == "rent" and (n, due) in rent_days:
            kind = "fee"
        if kind == "rent":
            rent_days.add((n, due))
        charge(conn, pmc.pmc_id, leases[n], due, amount, kind)
        if kind == "credit":
            credits[n] += amount
        else:
            charged[n][kind == "fee"] += amount
    paid = [[Decimal(0), Decimal(0)] for _ in leases]  # of rent, of other charges

    def still(n):
        """What lease n still owes of its rent and of its other charges, its credits coming off
        its rent first."""
        rent, fees = (charged[n][k] - paid[n][k] for k in (0, 1))
        return (max(rent - credits[n], Decimal(0)), max(fees - max(credits[n] - rent, 0), 0))

    def owing():
        return [sum(still(n), Decimal(0)) for n in range(len(leases))]

    def pay(amount):
        """The rent of every lease first, oldest lease first, then their other charges; what
        is left over."""
        left = amount
        for k in (0, 1):
            for n in range(len(leases)):
                part = min(left, still(n)[k])
                paid[n][k] += part
                left -= part
        return left

    def to_owners():
        """What each owner's account should hold: what was paid of their leases."""
        held = {home.account: Decimal(0) for home in homes}
        for n, home in enumerate(homes):
            held[home.account] += sum(paid[n], Decimal(0))
        return held

    prepaid = Decimal(0)
    for amount in payments:
        left = pay(amount)
        prepaid += left
        recorded = rent.record_payment(app, pmc.pmc_id, leases[-1], uuid.uuid4(), amount, PAID_ON)
        assert recorded.to_owner == amount - left and recorded.to_prepaid == left
        assert [
            app.execute("SELECT trust_lease_owed(%s)", (lease,)).fetchone()[0] for lease in leases
        ] == owing()
        assert {a: balance(conn, a) for a in to_owners()} == to_owners()
        assert balance(conn, pmc.prepaid_rent) == prepaid
        assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}
    event("payments: " + ("none" if not payments else "some prepaid" if prepaid else "all owed"))

    if deposit:
        app.execute("SELECT trust_end_lease(%s, %s, %s)", (pmc.pmc_id, leases[-1], LAST_DAY))
        keep = min(sum(owing(), Decimal(0)), deposit)
        assert pay(keep) == 0
        event("kept all" if keep == deposit else "kept none" if keep == 0 else "kept some")

        back = app.execute(
            "SELECT trust_return_deposit(%s, %s, NULL, 'back', %s)",
            (pmc.pmc_id, leases[-1], RETURNED),
        ).fetchone()[0]

        assert back == deposit - keep
        assert balance(conn, pmc.tenant_deposit) == 0
        assert [
            app.execute("SELECT trust_lease_owed(%s)", (lease,)).fetchone()[0] for lease in leases
        ] == owing()
        assert {a: balance(conn, a) for a in to_owners()} == to_owners()
        assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}

    # Every match paid a charge into the owner's account of the charge's own property.
    assert conn.execute(
        "SELECT count(*) FROM trust_charge_payments AS p"
        " JOIN trust_charges AS c ON c.id = p.charge_id"
        " JOIN trust_leases AS l ON l.id = c.lease_id"
        " JOIN trust_units AS u ON u.id = l.unit_id"
        " JOIN pgledger_transfers AS t ON t.id = p.transfer_id"
        " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id"
        " WHERE c.pmc_id = %s AND a.property_id IS DISTINCT FROM u.property_id",
        (pmc.pmc_id,),
    ).fetchone() == (0,)


def test_a_moved_tenant_s_money_pays_each_owner_what_was_owed_to_them(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app,
    ):
        app.execute("SET ROLE trust_app")
        check(conn, app)
