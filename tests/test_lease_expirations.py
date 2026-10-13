"""The lease expirations report (migration 20261013000046): every lease of a PMC whose last day
is the as-of day or one of the 90 after it, grouped by days left (0-30, 31-60, 61-90), with its
renewal status as of the end of the day (UTC): none, drafted, sent, signed or renewed.

The golden case under tests/golden/cases/ pins the layout; these check the rules."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    LEASE_PDF,
    add_staff,
    add_tenant,
    add_unit,
    complete_offer,
    make_pmc,
    offer_lease,
    open_lease,
    send_offer,
    sign_offer,
)

TODAY = datetime.now(UTC).date()
DAY = timedelta(days=1)
YEAR_AGO = TODAY - timedelta(days=365)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def staff(conn, pmc):
    return add_staff(conn, pmc.pmc_id)


def lease_ending(conn, pmc, ends_on, name, rent="1000.00", tenants=None, owner=0):
    prop = pmc.owners[owner].property_id
    unit = add_unit(conn, pmc.pmc_id, prop, name)
    people = tenants or [add_tenant(conn, pmc.pmc_id, prop, f"Tenant of {name}")]
    return unit, open_lease(conn, pmc.pmc_id, unit, YEAR_AGO, ends_on, rent, people)


def report(conn, pmc, as_of=TODAY):
    return conn.execute(
        "SELECT line, item, property, unit, tenants, ends_on, days_left, bucket, monthly_rent,"
        " renewal FROM trust_report_lease_expirations(%s, %s)",
        (pmc.pmc_id, as_of),
    ).fetchall()


def leases(rows):
    """unit -> (days left, group, renewal) of each lease line."""
    return {r[3]: (r[6], r[7], r[9]) for r in rows if r[1] == "lease"}


def renew(conn, pmc, staff, unit, lease, ends_on, signers=1):
    return offer_lease(
        conn,
        pmc.pmc_id,
        unit,
        staff,
        ends_on + DAY,
        ends_on + timedelta(days=365),
        renews_lease_id=lease,
        signers=[(f"Signer {n}", f"signer{n}@example.com") for n in range(1, signers + 1)],
    )


def test_leases_ending_in_the_next_90_days_are_listed_by_days_left(conn, pmc):
    for days in (-1, 0, 30, 31, 60, 61, 90, 91):
        lease_ending(conn, pmc, TODAY + timedelta(days=days), f"Ends in {days:+}")
    lease_ending(conn, pmc, None, "Month to month")

    assert leases(report(conn, pmc)) == {
        "Ends in +0": (0, "0-30", "none"),
        "Ends in +30": (30, "0-30", "none"),
        "Ends in +31": (31, "31-60", "none"),
        "Ends in +60": (60, "31-60", "none"),
        "Ends in +61": (61, "61-90", "none"),
        "Ends in +90": (90, "61-90", "none"),
    }


def test_the_renewal_status_is_the_furthest_along_of_its_offers(conn, pmc, staff):
    ends = TODAY + timedelta(days=20)
    lease_ending(conn, pmc, ends, "None")
    drafted = lease_ending(conn, pmc, ends, "Drafted")
    sent = lease_ending(conn, pmc, ends, "Sent")
    signed = lease_ending(conn, pmc, ends, "Signed")
    renewed = lease_ending(conn, pmc, ends, "Renewed")
    two = lease_ending(conn, pmc, ends, "Two offers")

    renew(conn, pmc, staff, *drafted, ends)
    offer = renew(conn, pmc, staff, *sent, ends, signers=2)
    send_offer(conn, pmc.pmc_id, offer, staff)
    sign_offer(conn, pmc.pmc_id, offer, 1)  # one of two
    offer = renew(conn, pmc, staff, *signed, ends)
    send_offer(conn, pmc.pmc_id, offer, staff)
    sign_offer(conn, pmc.pmc_id, offer, 1)
    offer = renew(conn, pmc, staff, *renewed, ends)
    send_offer(conn, pmc.pmc_id, offer, staff)
    sign_offer(conn, pmc.pmc_id, offer, 1)
    complete_offer(conn, pmc.pmc_id, offer, staff)
    renew(conn, pmc, staff, *two, ends)
    send_offer(conn, pmc.pmc_id, renew(conn, pmc, staff, *two, ends), staff)

    assert {unit: status for unit, (_, _, status) in leases(report(conn, pmc)).items()} == {
        "None": "none",
        "Drafted": "drafted",
        "Sent": "sent",
        "Signed": "signed",
        "Renewed": "renewed",
        "Two offers": "sent",
    }


def test_the_status_is_as_of_the_end_of_the_day(conn, pmc, staff):
    unit, lease = lease_ending(conn, pmc, TODAY + timedelta(days=10), "Unit 1")
    send_offer(conn, pmc.pmc_id, renew(conn, pmc, staff, unit, lease, TODAY + 10 * DAY), staff)

    assert leases(report(conn, pmc, TODAY - DAY)) == {"Unit 1": (11, "0-30", "none")}
    assert leases(report(conn, pmc, TODAY)) == {"Unit 1": (10, "0-30", "sent")}


def test_each_step_counts_from_the_day_it_was_recorded(conn, pmc, staff):
    """An offer made four days ago, sent three days ago, signed two days ago and made the lease
    today."""
    unit, lease = lease_ending(conn, pmc, TODAY + 30 * DAY, "Unit 1")
    now = datetime.now(UTC)
    offer = conn.execute(
        "INSERT INTO trust_lease_offers (pmc_id, renews_lease_id, unit_id, starts_on, ends_on,"
        " monthly_rent, document_title, document, offered_by, offered_at)"
        " VALUES (%s, %s, %s, %s, %s, 1000, 'Lease', %s, %s, %s) RETURNING id",
        (
            pmc.pmc_id,
            lease,
            unit,
            TODAY + 31 * DAY,
            TODAY + 395 * DAY,
            LEASE_PDF,
            staff,
            now - 4 * DAY,
        ),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO trust_lease_offer_signers (pmc_id, offer_id, position, display_name, email)"
        " VALUES (%s, %s, 1, 'Signer 1', 'signer1@example.com')",
        (pmc.pmc_id, offer),
    )
    conn.execute(
        "INSERT INTO trust_signature_requests (pmc_id, offer_id, partner, partner_reference,"
        " sent_by, sent_at) VALUES (%s, %s, 'Test partner', 'ref-1', %s, %s)",
        (pmc.pmc_id, offer, staff, now - 3 * DAY),
    )
    conn.execute(
        "INSERT INTO trust_signatures (pmc_id, offer_id, position, signed_at, recorded_at)"
        " VALUES (%s, %s, 1, %s, %s)",
        (pmc.pmc_id, offer, now - 2 * DAY, now - 2 * DAY),
    )
    complete_offer(conn, pmc.pmc_id, offer, staff)

    statuses = [leases(report(conn, pmc, TODAY - n * DAY))["Unit 1"][2] for n in range(5, -1, -1)]

    assert statuses == ["none", "drafted", "sent", "signed", "signed", "renewed"]


def test_each_group_and_the_whole_list_total_their_rent_and_leases_with_no_offer_out(
    conn, pmc, staff
):
    lease_ending(conn, pmc, TODAY + 5 * DAY, "A", "1000.00")
    unit, lease = lease_ending(conn, pmc, TODAY + 6 * DAY, "B", "1200.50")
    send_offer(conn, pmc.pmc_id, renew(conn, pmc, staff, unit, lease, TODAY + 6 * DAY), staff)
    unit, lease = lease_ending(conn, pmc, TODAY + 70 * DAY, "C", "900.00")
    renew(conn, pmc, staff, unit, lease, TODAY + 70 * DAY)  # drafted: not out

    totals = [(r[1], r[7], r[8], r[9]) for r in report(conn, pmc) if r[1] != "lease"][2:]

    assert totals == [
        ("group total", "0-30", Decimal("2200.50"), "1 of 2 with no renewal offer out"),
        ("group total", "31-60", Decimal("0.00"), "0 of 0 with no renewal offer out"),
        ("group total", "61-90", Decimal("900.00"), "1 of 1 with no renewal offer out"),
        ("total", None, Decimal("3100.50"), "2 of 3 with no renewal offer out"),
    ]


def test_leases_are_soonest_first_then_by_property_and_unit_in_byte_order(conn, pmc):
    names = ("tenant b", "Tenant B", "a tenant", "Tenant a", "B tenant")
    tenants = [add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, n) for n in names]
    lease_ending(conn, pmc, TODAY + 9 * DAY, "unit a")
    lease_ending(conn, pmc, TODAY + 9 * DAY, "Unit B", tenants=tenants)
    lease_ending(conn, pmc, TODAY + 9 * DAY, "Unit A", owner=1)
    lease_ending(conn, pmc, TODAY + 3 * DAY, "Unit Z", owner=1)
    properties = dict(
        conn.execute(
            "SELECT id, display_name FROM trust_properties WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchall()
    )
    first, second = (properties[o.property_id] for o in pmc.owners)
    assert first < second  # byte order

    lines = [(r[2], r[3], r[4]) for r in report(conn, pmc) if r[1] == "lease"]

    assert lines == [
        (second, "Unit Z", "Tenant of Unit Z"),
        (first, "Unit B", "B tenant, Tenant B, Tenant a, a tenant, tenant b"),
        (first, "unit a", "Tenant of unit a"),
        (second, "Unit A", "Tenant of Unit A"),
    ]


def test_another_pmcs_leases_are_not_listed(conn, pmc):
    other = make_pmc(conn)
    lease_ending(conn, other, TODAY + DAY, "Theirs")

    assert leases(report(conn, pmc)) == {}
    assert [r[1] for r in report(conn, pmc)] == [
        "PMC",
        "as of",
        "group total",
        "group total",
        "group total",
        "total",
    ]


def test_the_report_needs_a_day_and_a_known_pmc(conn, pmc):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="as of a day"):
        report(conn, pmc, None)
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no PMC"):
        conn.execute(
            "SELECT * FROM trust_report_lease_expirations(gen_random_uuid(), %s)", (TODAY,)
        ).fetchall()


def test_the_ai_reads_the_renewal_status_and_nothing_else_of_an_offer(
    conn, ai_conn, app_conn, pmc, staff
):
    unit, lease = lease_ending(conn, pmc, TODAY + 15 * DAY, "Unit 1")
    send_offer(conn, pmc.pmc_id, renew(conn, pmc, staff, unit, lease, TODAY + 15 * DAY), staff)

    assert report(ai_conn, pmc) == report(app_conn, pmc) == report(conn, pmc)
    assert leases(report(ai_conn, pmc)) == {"Unit 1": (15, "0-30", "sent")}
    for table in ("trust_lease_offers", "trust_lease_offer_signers", "trust_signatures"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ai_conn.execute(f"SELECT 1 FROM {table}").fetchall()
