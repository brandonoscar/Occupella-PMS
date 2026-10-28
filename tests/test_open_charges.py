"""Credits come off the oldest open charges (migration 20261013000049).

A credit takes its amount off what a lease owes. trust_open_charges says what is open of each
charge at a moment: its amount, less payments dated before then that matched it, plus what of
them bounced back before then, less its share of the lease's credits due before the moment's day
(UTC), which come off the open charges oldest first. The late fee run counts a rent as unpaid by
what is open of it at the end of its last day of grace, and the rent page shows what is open
(tests/api/test_rent.py).
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_late_fee_policy,
    add_recurring_charge,
    add_unit,
    apply_payment,
    assess_late_fees,
    charge,
    charge_recurring_due,
    charge_rent_due,
    make_pmc,
    open_lease,
    reverse_payment,
    transfer_batch,
)

JAN_1, FEB_1, MAR_1 = date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)
FOREVER = "infinity"


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def lease_on(conn, pmc, name="Unit 1"):
    """A lease of a new unit of Property 1 at 1500.00 from Jan 1."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, name)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    return open_lease(conn, pmc.pmc_id, unit, JAN_1, None, "1500.00", [tenant])


def pay(conn, pmc, charge_id, amount, when):
    (paid,) = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[0].account, amount)], when)
    apply_payment(conn, pmc.pmc_id, charge_id, paid, amount)
    return paid


def open_at(conn, pmc, moment, lease=None):
    """charge -> what is open of it at the moment."""
    return dict(
        conn.execute(
            "SELECT charge_id, open FROM trust_open_charges(%s, %s, %s)",
            (pmc.pmc_id, lease, moment),
        ).fetchall()
    )


def late_fees(conn, rent):
    return conn.execute(
        "SELECT unpaid, fee FROM trust_late_fees WHERE rent_charge_id = %s", (rent,)
    ).fetchall()


# --- late fees ------------------------------------------------------------------------------


@pytest.fixture
def late(conn, pmc):
    """A lease with 5 days' grace and a late fee of 50.00 plus 10% of what is unpaid, and
    February's rent charged: (lease, rent)."""
    lease = lease_on(conn, pmc)
    add_late_fee_policy(conn, pmc.pmc_id, pmc.owners[0].property_id, JAN_1, 5, "50", "10")
    rent = charge(conn, pmc.pmc_id, lease, FEB_1, "1500.00")
    return lease, rent


def test_a_lease_whose_credit_and_payment_cover_its_rent_is_not_late(conn, pmc, late):
    """The bug: a $100.00 monthly concession and $1400.00 paid left the lease owing nothing, yet
    the rent was charged a late fee."""
    lease, rent = late
    add_recurring_charge(conn, pmc.pmc_id, lease, "100.00", JAN_1, "Concession", "credit")
    charge_recurring_due(conn, pmc.pmc_id, FEB_1)
    pay(conn, pmc, rent, "1400.00", at(date(2026, 2, 2)))

    assert conn.execute("SELECT trust_lease_owed(%s)", (lease,)).fetchone()[0] == Decimal("0.00")
    assert assess_late_fees(conn, pmc.pmc_id, MAR_1) == 0
    assert late_fees(conn, rent) == []


@pytest.mark.parametrize(
    ("credit_due", "fees"),
    [
        (date(2026, 2, 6), []),  # the last day of grace: it counts
        (date(2026, 2, 7), [(Decimal("100.00"), Decimal("60.00"))]),  # after: 50 + 10% of 100
    ],
)
def test_a_credit_counts_if_due_by_the_last_day_of_grace(conn, pmc, late, credit_due, fees):
    lease, rent = late
    charge(conn, pmc.pmc_id, lease, credit_due, "100.00", "credit")
    pay(conn, pmc, rent, "1400.00", at(date(2026, 2, 2)))

    assess_late_fees(conn, pmc.pmc_id, MAR_1)

    assert late_fees(conn, rent) == fees


def test_credits_come_off_the_oldest_open_charges_first(conn, pmc, late):
    lease, rent = late
    january = charge(conn, pmc.pmc_id, lease, JAN_1, "1500.00")  # older, unpaid
    charge(conn, pmc.pmc_id, lease, FEB_1, "100.00", "credit")
    pay(conn, pmc, rent, "1400.00", at(date(2026, 2, 2)))

    assess_late_fees(conn, pmc.pmc_id, MAR_1)

    # Of two rents, the credit comes off January's: February's last 100.00 stays unpaid.
    assert late_fees(conn, rent) == [(Decimal("100.00"), Decimal("60.00"))]
    assert open_at(conn, pmc, FOREVER)[january] == Decimal("1400.00")


def test_credits_come_off_the_rent_before_an_older_fee(conn, pmc, late):
    lease, rent = late
    fee = charge(conn, pmc.pmc_id, lease, date(2026, 1, 20), "50.00", "fee")  # older, unpaid
    charge(conn, pmc.pmc_id, lease, FEB_1, "100.00", "credit")
    pay(conn, pmc, rent, "1400.00", at(date(2026, 2, 2)))

    assess_late_fees(conn, pmc.pmc_id, MAR_1)

    # Rent first (migration 0093): the credit closes the rent's last 100.00, so it is not late,
    # and the older fee stays open.
    assert late_fees(conn, rent) == []
    assert open_at(conn, pmc, FOREVER)[fee] == Decimal("50.00")


# --- what is open ---------------------------------------------------------------------------


def test_a_charge_is_open_by_its_payments_and_bounces_before_the_moment(conn, pmc):
    lease = lease_on(conn, pmc)
    rent = charge(conn, pmc.pmc_id, lease, FEB_1, "1500.00")
    paid = pay(conn, pmc, rent, "1500.00", at(date(2026, 2, 2)))
    (back,) = transfer_batch(
        conn, [(pmc.owners[0].account, pmc.operating_cash, "400.00")], at(date(2026, 2, 4))
    )
    reverse_payment(conn, pmc.pmc_id, rent, paid, back, "400.00")

    assert [
        open_at(conn, pmc, at(day))[rent]
        for day in (date(2026, 2, 2), date(2026, 2, 3), date(2026, 2, 4), date(2026, 2, 5))
    ] == [Decimal("1500.00"), Decimal("0.00"), Decimal("0.00"), Decimal("400.00")]
    assert open_at(conn, pmc, FOREVER)[rent] == Decimal("400.00")


def test_credits_due_before_the_moments_day_count(conn, pmc):
    lease = lease_on(conn, pmc)
    rent = charge(conn, pmc.pmc_id, lease, FEB_1, "1500.00")
    charge(conn, pmc.pmc_id, lease, date(2026, 2, 10), "100.00", "credit")

    assert open_at(conn, pmc, at(date(2026, 2, 10), 23))[rent] == Decimal("1500.00")
    assert open_at(conn, pmc, at(date(2026, 2, 11), 0))[rent] == Decimal("1400.00")
    assert open_at(conn, pmc, FOREVER)[rent] == Decimal("1400.00")


def test_a_credit_runs_on_to_later_charges_and_never_past_what_is_open(conn, pmc):
    lease = lease_on(conn, pmc)
    january = charge(conn, pmc.pmc_id, lease, JAN_1, "1500.00")
    february = charge(conn, pmc.pmc_id, lease, FEB_1, "1500.00")
    march = charge(conn, pmc.pmc_id, lease, MAR_1, "1500.00")
    pay(conn, pmc, january, "1500.00", at(JAN_1))  # paid already: takes none of the credit
    charge(conn, pmc.pmc_id, lease, JAN_1, "2000.00", "credit")

    assert open_at(conn, pmc, FOREVER) == {
        january: Decimal("0.00"),
        february: Decimal("0.00"),
        march: Decimal("1000.00"),
    }


def test_a_credit_comes_off_the_oldest_and_leaves_later_charges_open(conn, pmc):
    lease = lease_on(conn, pmc)
    january = charge(conn, pmc.pmc_id, lease, JAN_1, "1500.00")
    february = charge(conn, pmc.pmc_id, lease, FEB_1, "1500.00")
    charge(conn, pmc.pmc_id, lease, FEB_1, "100.00", "credit")

    assert open_at(conn, pmc, FOREVER) == {
        january: Decimal("1400.00"),
        february: Decimal("1500.00"),
    }


def test_each_leases_credits_are_its_own(conn, pmc):
    credited, other = lease_on(conn, pmc, "Unit 1"), lease_on(conn, pmc, "Unit 2")
    # The other lease's charges fall due first: they take none of this lease's credit.
    mine = [charge(conn, pmc.pmc_id, credited, FEB_1, "750.00", "fee") for _ in range(3)]
    theirs = [charge(conn, pmc.pmc_id, other, JAN_1, "750.00", "fee") for _ in range(3)]
    charge(conn, pmc.pmc_id, credited, FEB_1, "100.00", "credit")

    opened = open_at(conn, pmc, FOREVER)
    assert sorted(opened[c] for c in mine) == [
        Decimal("650.00"),
        Decimal("750.00"),
        Decimal("750.00"),
    ]
    assert [opened[c] for c in theirs] == [Decimal("750.00")] * 3
    assert sorted(open_at(conn, pmc, FOREVER, other)) == sorted(theirs)


def test_credits_are_not_open_charges(conn, pmc):
    lease = lease_on(conn, pmc)
    credit = charge(conn, pmc.pmc_id, lease, FEB_1, "100.00", "credit")

    assert credit not in open_at(conn, pmc, FOREVER)


def test_another_pmcs_charges_are_never_open_in_this_one(conn, pmc):
    other = make_pmc(conn)
    theirs = lease_on(conn, other)
    charge(conn, other.pmc_id, theirs, FEB_1, "1500.00")

    assert open_at(conn, pmc, FOREVER) == open_at(conn, pmc, FOREVER, theirs) == {}


def test_the_app_reads_what_is_open_and_the_ai_may_not(conn, app_conn, ai_conn, pmc):
    lease = lease_on(conn, pmc)
    rent = charge(conn, pmc.pmc_id, lease, FEB_1, "1500.00")

    assert open_at(app_conn, pmc, FOREVER) == {rent: Decimal("1500.00")}
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        open_at(ai_conn, pmc, FOREVER)


def test_the_rent_run_and_a_credit_together(conn, pmc):
    """Rent and a recurring credit charged by the run: what is open is the rent less the credit."""
    lease = lease_on(conn, pmc)
    add_recurring_charge(conn, pmc.pmc_id, lease, "100.00", JAN_1, "Concession", "credit")
    charge_rent_due(conn, pmc.pmc_id, FEB_1)
    charge_recurring_due(conn, pmc.pmc_id, FEB_1)

    assert list(open_at(conn, pmc, FOREVER, lease).values()) == [Decimal("1400.00")]


def test_one_leases_open_charges_read_its_own_charges_alone(conn, pmc):
    """Migration 20261028000200: given a lease, trust_open_charges finds its charges by the
    lease's index, so it reads them and not every charge of the PMC; given none, every lease's,
    and each lease's are the same either way."""
    leases = [lease_on(conn, pmc, f"Unit {n}") for n in range(1, 41)]
    charged = {
        lease: [charge(conn, pmc.pmc_id, lease, month, "1500.00") for month in (JAN_1, FEB_1)]
        + [charge(conn, pmc.pmc_id, lease, FEB_1, "40.00", "fee")]
        for lease in leases
    }
    one = leases[17]
    pay(conn, pmc, charged[one][0], "1500.00", at(FEB_1))

    every = open_at(conn, pmc, FOREVER)
    for lease in leases:
        own = open_at(conn, pmc, FOREVER, lease)
        assert own == {c: left for c, left in every.items() if c in own}
    assert sum(len(open_at(conn, pmc, FOREVER, lease)) for lease in leases) == len(every)

    def charges_read():
        return conn.execute(
            "SELECT coalesce(seq_tup_read, 0) + coalesce(idx_tup_fetch, 0)"
            " FROM pg_stat_xact_user_tables WHERE relname = 'trust_charges'"
        ).fetchone()[0]

    with conn.transaction():
        # On a table this small a scan of it all costs less, and the planner would rightly
        # take one; turned off, what is left is whether the lease's own index can be used.
        conn.execute("SET LOCAL enable_seqscan = off")
        before = charges_read()
        assert open_at(conn, pmc, FOREVER, one) == {
            charged[one][0]: Decimal("0.00"),
            charged[one][1]: Decimal("1500.00"),
            charged[one][2]: Decimal("40.00"),
        }
        # Its three, not the PMC's 120.
        assert charges_read() - before <= 2 * 3


def test_its_callers_look_up_its_few_rows_by_key(conn):
    """Its callers join what it gives to trust_charges by id. Told it gives a lease's few rows
    (ROWS 20; the planner's default for such a function is 1,000), the planner looks each up by
    its key rather than hashing every charge of the database: on a company of 20,000 units, a
    payment took 38 ms with the hash and 10 ms with the lookups (migration 20261028000200)."""
    rows = conn.execute(
        "SELECT prorows FROM pg_proc"
        " WHERE oid = to_regprocedure('trust_open_charges(uuid, uuid, timestamptz)')"
    ).fetchone()[0]

    assert rows == 20
