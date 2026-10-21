"""Property-based tests for vendor profiles (migration 20261016000073, src/pms/api/vendors.py).

Hypothesis saves a random run of trades, 1099 flags and payment methods for a company's two
vendors through the app's own `record_profile`, by either of two of its staff, mixed with tries
that name another company's vendor or another company's staff member, which are refused. After
the run:
  - each vendor's profile, as the vendors page reads it (`profiles_of`), is the last one saved,
    and a vendor never saved has none;
  - a save writes a row exactly when it changes the vendor's profile (from no profile, a change
    from the empty one), naming the staff member who made it, and the audit log has each row;
  - another company's vendor or staff member wrote nothing, and no row is ever rewritten.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import psycopg
import pytest
from helpers import acting_as, add_staff, make_pmc
from hypothesis import event, given
from hypothesis import strategies as st

from pms.api.vendors import Profile, profiles_of, record_profile

PROFILES = st.builds(
    Profile,
    trade=st.sampled_from([None, "Plumbing", "Roofing"]),
    gets_1099=st.booleans(),
    payment_method=st.sampled_from([None, "check", "EFT"]),
)
SAVES = st.lists(
    st.tuples(
        st.sampled_from(["vendor A", "vendor B", "their vendor"]),
        st.sampled_from(["staff 1", "staff 2", "their staff"]),
        PROFILES,
    ),
    min_size=1,
    max_size=10,
)


@given(saves=SAVES)
def check(conn, saves):
    ours, theirs = make_pmc(conn), make_pmc(conn)
    vendors = {
        "vendor A": ours.vendor_id,
        "vendor B": conn.execute(
            "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, 'Vendor B') RETURNING id",
            (ours.pmc_id,),
        ).fetchone()[0],
        "their vendor": theirs.vendor_id,
    }
    staff = {
        "staff 1": add_staff(conn, ours.pmc_id, "Staff 1"),
        "staff 2": add_staff(conn, ours.pmc_id, "Staff 2"),
        "their staff": add_staff(conn, theirs.pmc_id, "Staff 3"),
    }
    latest: dict = {}  # vendor -> Profile
    written = []  # (vendor, staff) of each row the model expects
    for vendor, who, profile in saves:
        with acting_as(conn, staff[who]):
            if vendor == "their vendor" or who == "their staff":
                event("another company's vendor or staff: refused")
                with pytest.raises(psycopg.errors.ForeignKeyViolation):
                    conn.execute(
                        "INSERT INTO trust_vendor_profiles"
                        " (pmc_id, vendor_id, trade, gets_1099, payment_method)"
                        " VALUES (%s, %s, %s, %s, %s)",
                        (
                            ours.pmc_id,
                            vendors[vendor],
                            profile.trade,
                            profile.gets_1099,
                            profile.payment_method,
                        ),
                    )
                continue
            changes = profile != latest.get(vendor, Profile())
            event("a change: written" if changes else "the same: nothing written")
            assert record_profile(conn, ours.pmc_id, vendors[vendor], profile) is changes
        if changes:
            latest[vendor] = profile
            written.append((vendors[vendor], staff[who]))

    assert profiles_of(conn, ours.pmc_id) == {vendors[v]: p for v, p in latest.items()}
    assert profiles_of(conn, theirs.pmc_id) == {}
    rows = conn.execute(
        "SELECT vendor_id, recorded_by FROM trust_vendor_profiles"
        " WHERE pmc_id IN (%s, %s) ORDER BY created_at, id",
        (ours.pmc_id, theirs.pmc_id),
    ).fetchall()
    assert [tuple(row) for row in rows] == written
    logged = conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_vendor_profiles'"
        " AND pmc_id IN (%s, %s)",
        (ours.pmc_id, theirs.pmc_id),
    ).fetchone()[0]
    assert logged == len(written)
    for change in ("UPDATE trust_vendor_profiles SET gets_1099 = NOT gets_1099",):
        with pytest.raises(psycopg.errors.RestrictViolation):
            conn.execute(change + " WHERE pmc_id = %s", (ours.pmc_id,))


def test_a_vendors_profile_is_the_last_change_saved_by_its_own_companys_staff(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
