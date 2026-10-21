"""Property-based tests for the company's profile (migration 20261017000075,
src/pms/api/company.py and setup.with_default_fees).

Hypothesis saves a random run of mailing addresses (or none), default late fee terms (or none)
and default NSF fees (or none) through the app's own `save_company`, for this company and for
another, and adds properties in between. A model keeps the latest save of each company:
  - the company reads as its latest save (`company_of`), and another company's saves never
    show in it;
  - a save writes a row exactly when it changes the profile (no row reads as all none), and the
    audit log has each row;
  - a property added starts with the defaults standing when it was added, from that day: a late
    fee policy and an NSF fee policy exactly when the defaults have them, else none; a later
    save changes no property already there.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import psycopg
from helpers import make_pmc
from hypothesis import event, given
from hypothesis import strategies as st

from pms.api.company import Address, LateFeeTerms, NewCompany, company_of, save_company
from pms.api.setup import with_default_fees

CENTS = st.integers(min_value=0, max_value=10_000).map(lambda c: Decimal(c).scaleb(-2))
ADDRESSES = st.none() | st.builds(
    Address,
    address_line_1=st.sampled_from(["100 Example Street", "9 Sample Road"]),
    address_line_2=st.sampled_from([None, "Suite 2"]),
    city=st.just("Sampletown"),
    state=st.just("ZZ"),
    postal_code=st.just("00000"),
)
LATE = st.none() | st.builds(
    LateFeeTerms,
    grace_days=st.integers(min_value=0, max_value=10),
    flat_fee=CENTS,
    percent=st.sampled_from([Decimal(0), Decimal(5), Decimal("10.5")]),
    maximum=st.none() | CENTS,
)
ACTIONS = st.lists(
    st.one_of(
        st.tuples(st.just("save"), st.booleans(), ADDRESSES, LATE, st.none() | CENTS),
        st.tuples(st.just("add a property")),
    ),
    min_size=1,
    max_size=8,
)
NONE = (None, None, None)


@given(actions=ACTIONS)
def check(conn, actions):
    ours, theirs = make_pmc(conn), make_pmc(conn)
    name = {
        pmc.pmc_id: conn.execute(
            "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone()[0]
        for pmc in (ours, theirs)
    }
    latest = {ours.pmc_id: NONE, theirs.pmc_id: NONE}  # (address, late fee terms, NSF fee)
    rows = {ours.pmc_id: 0, theirs.pmc_id: 0}
    added = []  # (property, the defaults standing when it was added, that day)
    for action in actions:
        if action[0] == "add a property":
            prop = conn.execute(
                "INSERT INTO trust_properties (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
                (ours.pmc_id, f"Property {len(added) + 3}"),
            ).fetchone()[0]
            with_default_fees(conn, ours.pmc_id, prop)
            added.append((prop, latest[ours.pmc_id], datetime.now(UTC).date()))
            continue
        _, mine, address, late, nsf = action
        pmc_id = (ours if mine else theirs).pmc_id
        new = NewCompany(name=name[pmc_id], address=address, late_fees=late, nsf_fee=nsf)
        saved = save_company(SimpleNamespace(pmc_id=pmc_id), conn, new)
        changes = (address, late, nsf) != latest[pmc_id]
        event("a change: written" if changes else "the same: nothing written")
        if changes:
            latest[pmc_id] = (address, late, nsf)
            rows[pmc_id] += 1
        assert saved == company_of(conn, pmc_id)

    for pmc_id, (address, late, nsf) in latest.items():
        company = company_of(conn, pmc_id)
        assert company.address == address
        assert company.nsf_fee == (None if nsf is None else f"{nsf:.2f}")
        got = company.late_fees
        if late is None:
            assert got is None
        else:
            assert got is not None
            assert (got.grace_days, Decimal(got.flat_fee), Decimal(got.percent)) == (
                late.grace_days,
                late.flat_fee,
                late.percent,
            )
            assert got.maximum == (None if late.maximum is None else f"{late.maximum:.2f}")
        assert conn.execute(
            "SELECT count(*) FROM trust_company_profiles WHERE pmc_id = %s", (pmc_id,)
        ).fetchone() == (rows[pmc_id],)
        assert conn.execute(
            "SELECT count(*) FROM trust_audit_log WHERE pmc_id = %s"
            " AND table_name = 'trust_company_profiles'",
            (pmc_id,),
        ).fetchone() == (rows[pmc_id],)

    for prop, (_, late, nsf), today in added:
        policies = conn.execute(
            "SELECT starts_on, grace_days, flat_fee, percent, maximum"
            " FROM trust_late_fee_policies WHERE property_id = %s",
            (prop,),
        ).fetchall()
        expected = (
            []
            if late is None
            else [(today, late.grace_days, late.flat_fee, late.percent, late.maximum)]
        )
        assert [tuple(p) for p in policies] == expected
        fees = conn.execute(
            "SELECT starts_on, fee FROM trust_nsf_fee_policies WHERE property_id = %s", (prop,)
        ).fetchall()
        assert [tuple(f) for f in fees] == ([] if nsf is None else [(today, nsf)])
        event("added with late fees" if late else "added with no late fees")


def test_the_company_is_its_latest_save_and_a_new_property_starts_with_its_defaults(
    database_url,
):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
