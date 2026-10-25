"""Property-based tests for what a template makes (migration 20261022000121): a company's
templates are each for a notice or a lease from when they are made.

Hypothesis makes a few templates of either use, each with one or two versions, then makes notices
on a lease and fills offers from random versions. A model says which the database takes:
  - a notice comes only from a version of a notice template;
  - an offer's document comes only from a version of a lease template, once per offer; a second
    is refused as a repeat, whichever version it names;
and every version a lease template's blanks name is one the PMS fills from a draft lease, and
filling it leaves no blank.
Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date

import psycopg
from helpers import add_staff, add_tenant, add_unit, make_pmc, offer_lease, open_lease
from hypothesis import event, given
from hypothesis import strategies as st

from pms import notice_letters

TEMPLATES = st.lists(
    st.tuples(st.sampled_from(["notice", "lease"]), st.integers(1, 2)), min_size=1, max_size=4
)
USES = st.lists(
    st.tuples(st.sampled_from(["notice", "offer"]), st.integers(0, 7), st.integers(0, 1)),
    max_size=10,
)
LEASE_TEXT = st.lists(
    st.one_of(
        st.sampled_from(sorted(notice_letters.LEASE_FIELDS)).map(lambda n: f"{{{{ {n} }}}}"),
        st.text(st.characters(exclude_characters="{}"), max_size=12),
    ),
    max_size=8,
).map("".join)


def an_offer(conn, pmc, staff, n):
    """The renewal of a lease ending 12/31/2026, from 01/01/2027."""
    prop = pmc.owners[0].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, f"Unit {n}")
    tenant = add_tenant(conn, pmc.pmc_id, prop, f"Tenant {n}")
    lease = open_lease(
        conn, pmc.pmc_id, unit, date(2026, 1, 1), date(2026, 12, 31), "900", [tenant]
    )
    return lease, offer_lease(
        conn, pmc.pmc_id, unit, staff, date(2027, 1, 1), renews_lease_id=lease
    )


@given(templates=TEMPLATES, uses=USES)
def check(conn, templates, uses):
    pmc = make_pmc(conn)
    staff = add_staff(conn, pmc.pmc_id)
    versions: list[tuple[str, object]] = []
    for n, (used_for, count) in enumerate(templates):
        made = conn.execute(
            "INSERT INTO trust_notice_templates (pmc_id, used_for) VALUES (%s, %s) RETURNING id",
            (pmc.pmc_id, used_for),
        ).fetchone()[0]
        for v in range(count):
            versions.append(
                (
                    used_for,
                    conn.execute(
                        "INSERT INTO trust_notice_template_versions (pmc_id, template_id, name,"
                        " body) VALUES (%s, %s, %s, 'Text') RETURNING id",
                        (pmc.pmc_id, made, f"Template {n}.{v}"),
                    ).fetchone()[0],
                )
            )
    leases_offers = [an_offer(conn, pmc, staff, n) for n in range(2)]
    made_from: set[int] = set()
    notices = 0
    for kind, v, target in uses:
        if v >= len(versions):
            continue
        used_for, version = versions[v]
        lease, offer = leases_offers[target]
        try:
            if kind == "notice":
                conn.execute(
                    "INSERT INTO trust_notices (pmc_id, lease_id, template_version_id, dated_on,"
                    " title, letterhead, body) VALUES (%s, %s, %s, '2026-10-22', 'T', 'Co', 'x')",
                    (pmc.pmc_id, lease, version),
                )
            else:
                conn.execute(
                    "INSERT INTO trust_lease_offer_templates (pmc_id, offer_id,"
                    " template_version_id) VALUES (%s, %s, %s)",
                    (pmc.pmc_id, offer, version),
                )
            took = "made"
        except psycopg.errors.CheckViolation:
            took = "wrong use"
        except psycopg.errors.UniqueViolation:
            took = "again"
        if kind == "notice":
            expected = "made" if used_for == "notice" else "wrong use"
        elif used_for != "lease":
            expected = "wrong use"
        else:
            expected = "again" if target in made_from else "made"
        event(f"{kind}: {expected}")
        assert took == expected
        if took == "made" and kind == "notice":
            notices += 1
        elif took == "made":
            made_from.add(target)
    assert conn.execute(
        "SELECT count(*) FROM trust_notices WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (notices,)
    assert conn.execute(
        "SELECT count(*) FROM trust_lease_offer_templates WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (len(made_from),)


def test_a_notice_and_an_offer_each_come_from_a_template_for_their_use(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)


@given(text=LEASE_TEXT)
def test_a_lease_templates_blanks_are_all_filled(text):
    assert notice_letters.unknown(text, notice_letters.LEASE_FIELDS) == []
    values = {name: f"<{name}>" for name in notice_letters.LEASE_FIELDS}
    filled = notice_letters.fill(text, values)
    assert notice_letters.unknown(filled, notice_letters.LEASE_FIELDS) == []
    assert "{{" not in filled
