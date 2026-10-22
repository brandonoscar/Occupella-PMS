"""Property-based tests for a deposit's receipts and its return-by day (migration
20261022000120; the founder's review q040 and q039).

Hypothesis makes two leases, charges on each (fees and rent), documents on each (lease documents
and move-out photos), then links random charges to random documents as receipts and removes some
documents. A model says which links the database takes:
  - a receipt is a lease document of the fee's own lease, once per fee and document; anything
    else is refused, and a link made twice is refused as a repeat;
  - each lease's fees, as the move-out page reads them (`fees_of`), list exactly the receipts
    linked to them that were not removed, in the order they were linked.
And the day a deposit is due back is 14 days after the lease's last day, 10 while it owes nothing.
Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date, timedelta
from decimal import Decimal

import psycopg
from helpers import add_document, add_tenant, add_unit, charge, make_pmc, open_lease
from hypothesis import event, given
from hypothesis import strategies as st

from pms.api.moveout import fees_of, return_by

CHARGES = st.lists(
    st.tuples(st.integers(0, 1), st.sampled_from(["fee", "rent"])), min_size=1, max_size=5
)
DOCUMENTS = st.lists(
    st.tuples(st.integers(0, 1), st.sampled_from(["lease_document", "move_out_photo"])),
    min_size=1,
    max_size=5,
)
LINKS = st.lists(st.tuples(st.integers(0, 4), st.integers(0, 4)), max_size=10)
REMOVALS = st.lists(st.integers(0, 4), max_size=3)


@given(charges=CHARGES, documents=DOCUMENTS, links=LINKS, removals=REMOVALS)
def check(conn, charges, documents, links, removals):
    pmc = make_pmc(conn)
    prop = pmc.owners[0].property_id
    leases = []
    for n in range(2):
        tenant = add_tenant(conn, pmc.pmc_id, prop, f"Tenant {n}")
        unit = add_unit(conn, pmc.pmc_id, prop, f"Unit {n}")
        leases.append(open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "900", [tenant]))
    # Rent is charged once per lease and due date, so each charge has a day of its own.
    made = [
        (lease, kind, charge(conn, pmc.pmc_id, leases[lease], date(2026, 10, 1 + n), "150", kind))
        for n, (lease, kind) in enumerate(charges)
    ]
    docs = [
        (lease, kind, add_document(conn, pmc.pmc_id, kind, leases[lease], title=f"Doc {n}"))
        for n, (lease, kind) in enumerate(documents)
    ]
    linked: list[tuple[int, int]] = []
    for c, d in links:
        if c >= len(made) or d >= len(docs):
            continue
        c_lease, c_kind, charge_id = made[c]
        d_lease, d_kind, document_id = docs[d]
        allowed = c_kind == "fee" and d_kind == "lease_document" and c_lease == d_lease
        try:
            conn.execute(
                "INSERT INTO trust_deduction_receipts (pmc_id, charge_id, document_id)"
                " VALUES (%s, %s, %s)",
                (pmc.pmc_id, charge_id, document_id),
            )
            took = "linked"
        except psycopg.errors.UniqueViolation:
            took = "again"
        except psycopg.errors.CheckViolation:
            took = "refused"
        expected = ("again" if (c, d) in linked else "linked") if allowed else "refused"
        event(f"link: {expected}")
        assert took == expected
        if took == "linked":
            linked.append((c, d))
    removed = {d for d in removals if d < len(docs)}
    for d in removed:
        conn.execute(
            "INSERT INTO trust_document_removals (pmc_id, document_id) VALUES (%s, %s)",
            (pmc.pmc_id, docs[d][2]),
        )
    for n, lease in enumerate(leases):
        shown = {
            fee.id: [r.id for r in fee.receipts] for fee in fees_of(conn, lease, date(2026, 1, 1))
        }
        fees = [charge_id for c_lease, kind, charge_id in made if c_lease == n and kind == "fee"]
        assert set(shown) == set(fees)
        for c, (c_lease, kind, charge_id) in enumerate(made):
            if c_lease != n or kind != "fee":
                continue
            assert shown[charge_id] == [
                docs[d][2] for cc, d in linked if cc == c and d not in removed
            ]


def test_a_receipt_is_a_lease_document_of_its_fees_lease_listed_until_removed(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)


@given(
    ends_on=st.dates(date(2020, 1, 1), date(2035, 12, 31)),
    owes=st.integers(-100_000, 100_000).map(lambda c: Decimal(c) / 100),
)
def test_a_deposit_is_due_back_14_days_after_the_last_day_or_10_owing_nothing(ends_on, owes):
    due = return_by(ends_on, owes)
    assert due == ends_on + timedelta(days=14 if owes > 0 else 10)
    assert return_by(None, owes) is None
