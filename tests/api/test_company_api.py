"""The company itself (src/pms/api/company.py, migration 0075, ADR 0006 step 12): staff rename
it, keep its address and the fee terms a new property starts with, and its logo; owners and
tenants see its name, address and logo. Always the session's PMC. Every address is synthetic."""

import base64
from datetime import UTC, date, datetime

import psycopg
import pytest
from helpers import PDF, PNG, make_pmc
from people import join_as_owner, join_as_staff, join_as_tenant


def on_this_day() -> None:
    """Today's dates (UTC), worked out again before each test (tests/conftest.py,
    pytest_runtest_setup), so a run past midnight compares them with the database's."""
    global TODAY
    TODAY = datetime.now(UTC).date()


on_this_day()
ADDRESS = {
    "address_line_1": "100 Example Street",
    "address_line_2": "Suite 2",
    "city": "Sampletown",
    "state": "ZZ",
    "postal_code": "00000",
}


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def save(client, **body):
    return client.post("/api/company", json={"name": "Synthetic PMC 1", **body})


def logo(client, content=PNG, content_type="image/png"):
    body = {"content_type": content_type, "content_base64": base64.b64encode(content).decode()}
    return client.post("/api/company/logo", json=body)


def rows(conn, pmc):
    """The profile rows saved here: past the one make_pmc writes (its time zone, UTC)."""
    return conn.execute(
        "SELECT count(*) - 1 FROM trust_company_profiles WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]


ZONES = [
    {"id": "America/New_York", "name": "Eastern"},
    {"id": "America/Chicago", "name": "Central"},
    {"id": "America/Denver", "name": "Mountain"},
    {"id": "America/Phoenix", "name": "Arizona"},
    {"id": "America/Los_Angeles", "name": "Pacific"},
    {"id": "America/Anchorage", "name": "Alaska"},
    {"id": "Pacific/Honolulu", "name": "Hawaii"},
    {"id": "America/Puerto_Rico", "name": "Atlantic (Puerto Rico)"},
    {"id": "UTC", "name": "UTC"},
]


def test_a_company_starts_with_its_name_alone(client, staff, pmc, conn):
    name = conn.execute(
        "SELECT display_name FROM trust_pmcs WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]
    assert ok(client.get("/api/company")) == {
        "name": name,
        "address": None,
        "late_fees": None,
        "nsf_fee": None,
        "reply_to": None,
        "terms_url": None,
        "privacy_url": None,
        "logo": None,
        "time_zone": "UTC",
        "time_zones": ZONES,
        "date_order": "month_first",
    }


def test_a_company_reads_its_files_dates_in_its_own_order(client, staff, pmc, conn):
    """docs/edge-cases.md F-061: a company outside the US writes 05/01/2026 for 5 January. It
    picks day first in Company setup, and its files' dates read that way when they don't prove
    the order themselves. Month first, as the US writes them, until it picks."""
    assert ok(save(client, date_order="month_first"))["date_order"] == "month_first"
    assert rows(conn, pmc) == 0  # the order it has already: nothing written
    assert ok(save(client, date_order="day_first"))["date_order"] == "day_first"
    assert ok(save(client))["date_order"] == "day_first"  # left out, it stays
    assert rows(conn, pmc) == 1
    answer = save(client, date_order="sideways")
    assert answer.status_code == 422
    ok(client.post("/api/switching/import", json={"kind": "owners", "content": "Name\nOwner A\n"}))
    ok(
        client.post(
            "/api/switching/import",
            json={"kind": "properties", "content": "Property,Owner\nProperty A,Owner A\n"},
        )
    )
    ok(
        client.post(
            "/api/switching/import",
            json={"kind": "units", "content": "Property,Unit\nProperty A,Unit 1\n"},
        )
    )
    ok(
        client.post(
            "/api/switching/import",
            json={
                "kind": "leases",
                "content": "Property,Unit,Tenants,Starts On,Monthly Rent\n"
                "Property A,Unit 1,Tenant A,05/01/2026,1500.00\n",
            },
        )
    )
    assert conn.execute(
        "SELECT l.starts_on FROM trust_leases AS l JOIN trust_units AS u ON u.id = l.unit_id"
        " WHERE u.pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchone()[0] == date(2026, 1, 5)


def test_a_company_that_picked_no_time_zone_is_in_new_york(client, conn, app_conn):
    """A company without a profile row (src/pms/days.py): America/New_York, and its day there."""
    pmc = make_pmc(conn, time_zone=None)
    join_as_staff(client, conn, app_conn, pmc)
    assert ok(client.get("/api/company"))["time_zone"] == "America/New_York"
    me = ok(client.get("/api/me"))
    assert me["time_zone"] == "America/New_York"
    eastern = conn.execute("SELECT (now() AT TIME ZONE 'America/New_York')::date").fetchone()[0]
    assert me["today"] == str(eastern)
    # Picking New York, the zone it is in already, is no change: nothing is written.
    ok(save(client, time_zone="America/New_York"))
    assert conn.execute(
        "SELECT count(*) FROM trust_company_profiles WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (0,)


def test_a_company_picks_its_time_zone_and_its_day_follows_it(client, staff, pmc, conn):
    saved = ok(save(client, time_zone="Pacific/Honolulu"))
    assert saved["time_zone"] == "Pacific/Honolulu"
    assert rows(conn, pmc) == 1
    hawaii = conn.execute("SELECT (now() AT TIME ZONE 'Pacific/Honolulu')::date").fetchone()[0]
    me = ok(client.get("/api/me"))
    assert (me["time_zone"], me["today"]) == ("Pacific/Honolulu", str(hawaii))
    # The same again writes nothing, and a save that leaves it out keeps it.
    ok(save(client, time_zone="Pacific/Honolulu"))
    assert ok(save(client))["time_zone"] == "Pacific/Honolulu"
    assert rows(conn, pmc) == 1
    assert ok(save(client, time_zone="America/Chicago"))["time_zone"] == "America/Chicago"
    assert rows(conn, pmc) == 2
    # The audit log names who changed it.
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_company_profiles'"
        " AND staff_id = %s AND row_data ->> 'time_zone' = 'America/Chicago'",
        (staff.person_id,),
    ).fetchone() == (1,)


def test_replies_to_the_companys_emails_go_to_its_own_email(client, staff, pmc, conn):
    saved = ok(save(client, reply_to=" Office@Example.com "))
    assert saved["reply_to"] == "Office@Example.com"
    assert rows(conn, pmc) == 1
    ok(save(client, reply_to="Office@Example.com"))
    assert rows(conn, pmc) == 1  # the same again writes nothing
    assert ok(save(client))["reply_to"] is None
    assert rows(conn, pmc) == 2


TERMS = "https://example.com/terms"
PRIVACY = "https://example.com/privacy"


def test_the_companys_terms_and_privacy_notice_are_its_own_links_shown_to_its_people(
    client, other_client, staff, pmc, conn, app_conn
):
    saved = ok(save(client, terms_url=f" {TERMS} ", privacy_url=PRIVACY))
    assert (saved["terms_url"], saved["privacy_url"]) == (TERMS, PRIVACY)
    assert rows(conn, pmc) == 1
    ok(save(client, terms_url=TERMS, privacy_url=PRIVACY))
    assert rows(conn, pmc) == 1  # the same again writes nothing

    # Owners and tenants see them on their property manager's card.
    join_as_owner(other_client, app_conn, pmc)
    card = ok(other_client.get("/api/company/card"))
    assert (card["terms_url"], card["privacy_url"]) == (TERMS, PRIVACY)
    other_client.post("/api/sign-out")
    join_as_tenant(other_client, conn, app_conn, pmc)
    assert ok(other_client.get("/api/company/card"))["privacy_url"] == PRIVACY

    # Taken off, a new row; the card has none.
    assert ok(save(client))["terms_url"] is None
    assert rows(conn, pmc) == 2
    assert ok(other_client.get("/api/company/card"))["terms_url"] is None


def test_the_database_keeps_only_an_https_link(conn, pmc):
    for bad in ("http://example.com/terms", "https://example.com/two words", "ftp://x"):
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO trust_company_profiles (pmc_id, terms_url) VALUES (%s, %s)",
                (pmc.pmc_id, bad),
            )


def test_staff_rename_it_keep_its_address_and_default_fees_and_a_same_save_writes_nothing(
    client, staff, pmc, conn
):
    terms = {"grace_days": 3, "flat_fee": "25.00", "percent": "5", "maximum": "100.00"}
    saved = ok(save(client, address=ADDRESS, late_fees=terms, nsf_fee="35.00"))

    assert saved["name"] == "Synthetic PMC 1"
    assert saved["address"] == ADDRESS
    assert saved["late_fees"] == {
        "grace_days": 3,
        "flat_fee": "25.00",
        "percent": "5",
        "maximum": "100.00",
    }
    assert saved["nsf_fee"] == "35.00"
    assert ok(client.get("/api/me"))["pmc_name"] == "Synthetic PMC 1"
    assert rows(conn, pmc) == 1
    ok(save(client, address=ADDRESS, late_fees=terms, nsf_fee="35.00"))
    assert rows(conn, pmc) == 1
    # Clearing them is a change too, and the audit log names who made it.
    cleared = ok(save(client))
    assert (cleared["address"], cleared["late_fees"], cleared["nsf_fee"]) == (None, None, None)
    assert rows(conn, pmc) == 2
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_company_profiles'"
        " AND staff_id = %s",
        (staff.person_id,),
    ).fetchone() == (2,)


def test_a_new_property_starts_with_the_default_fees_and_one_there_keeps_its_own(
    client, staff, pmc, conn
):
    ok(
        save(
            client,
            late_fees={"grace_days": 5, "flat_fee": "50.00"},
            nsf_fee="35.00",
        )
    )
    made = ok(
        client.post(
            "/api/setup/properties",
            json={
                "name": "Property 9",
                "owner_id": str(pmc.owners[0].owner_id),
                "bank_account_id": str(pmc.operating_bank_id),
            },
        ),
        201,
    )["id"]

    page = ok(client.get(f"/api/properties/{made}"))
    assert page["late_fees"] == {
        "starts_on": str(TODAY),
        "grace_days": 5,
        "flat_fee": "50.00",
        "percent": "0",
        "maximum": None,
    }
    assert page["nsf_fee"] == {"starts_on": str(TODAY), "fee": "35.00"}
    there = ok(client.get(f"/api/properties/{pmc.owners[0].property_id}"))
    assert (there["late_fees"], there["nsf_fee"]) == (None, None)


def test_a_property_added_without_defaults_has_no_fee_terms(client, staff, pmc):
    made = ok(
        client.post(
            "/api/setup/properties",
            json={
                "name": "Property 9",
                "owner_id": str(pmc.owners[0].owner_id),
                "bank_account_id": str(pmc.operating_bank_id),
            },
        ),
        201,
    )["id"]
    page = ok(client.get(f"/api/properties/{made}"))
    assert (page["late_fees"], page["nsf_fee"]) == (None, None)


def test_the_logo_is_kept_shown_to_owners_and_tenants_and_removed(
    client, other_client, staff, pmc, conn, app_conn
):
    made = ok(logo(client), 201)
    assert made["content_type"] == "image/png"
    assert ok(client.get("/api/company"))["logo"]["id"] == made["id"]
    shown = client.get("/api/company/logo")
    assert (shown.status_code, shown.headers["content-type"], shown.content) == (
        200,
        "image/png",
        PNG,
    )
    # Not a file on the Files page.
    assert ok(client.get("/api/documents/files"))["total"] == 0
    assert ok(client.get("/api/documents"))["documents"] == []

    ok(save(client, address=ADDRESS))
    join_as_owner(other_client, app_conn, pmc)
    assert ok(other_client.get("/api/company/card")) == {
        "name": "Synthetic PMC 1",
        "address": ADDRESS,
        "logo": True,
        "terms_url": None,
        "privacy_url": None,
    }
    assert other_client.get("/api/company/logo").content == PNG
    other_client.post("/api/sign-out")
    join_as_tenant(other_client, conn, app_conn, pmc)
    assert ok(other_client.get("/api/company/card"))["logo"] is True

    # A new logo takes the last one's place; removed, there is none.
    newer = ok(logo(client), 201)
    assert ok(client.get("/api/company"))["logo"]["id"] == newer["id"]
    ok(client.post("/api/company/logo/remove", json={}), 204)
    assert ok(client.get("/api/company"))["logo"]["id"] == made["id"]
    ok(client.post("/api/company/logo/remove", json={}), 204)
    assert ok(client.get("/api/company"))["logo"] is None
    assert client.get("/api/company/logo").status_code == 404
    assert client.post("/api/company/logo/remove", json={}).status_code == 404
    assert ok(other_client.get("/api/company/card"))["logo"] is False


def test_another_companys_logo_and_profile_are_not_this_ones(client, staff, pmc, conn, app_conn):
    theirs = make_pmc(conn)
    conn.execute(
        "INSERT INTO trust_company_profiles (pmc_id, address_line_1, city, state, postal_code)"
        " VALUES (%s, '9 Example Road', 'Elsewhere', 'ZZ', '00000')",
        (theirs.pmc_id,),
    )
    assert ok(client.get("/api/company"))["address"] is None
    assert client.get("/api/company/logo").status_code == 404


@pytest.mark.parametrize("caller", ["nobody", "owner"])
def test_only_staff_see_and_change_the_company(client, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    assert client.get("/api/company").status_code == status
    assert save(client).status_code == status
    assert logo(client).status_code == status
    assert client.post("/api/company/logo/remove", json={}).status_code == status


def test_nobody_signed_out_sees_the_card_or_logo(client, pmc):
    assert client.get("/api/company/card").status_code == 401
    assert client.get("/api/company/logo").status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "N" * 201},
        {"address": {**ADDRESS, "city": None}},
        {"address": {"address_line_1": "100 Example Street"}},
        {"late_fees": {"grace_days": 366}},
        {"late_fees": {"grace_days": 3, "flat_fee": "-1"}},
        {"late_fees": {"grace_days": 3, "percent": "100.01"}},
        {"late_fees": {"flat_fee": "25.00"}},
        {"nsf_fee": "1.001"},
        {"reply_to": "not an email"},
        {"reply_to": "x" * 250 + "@example.com"},
        {"terms_url": "http://example.com/terms"},
        {"terms_url": "javascript:alert(1)"},
        {"privacy_url": "https://example.com/a privacy notice"},
        {"privacy_url": "https://example.com/" + "p" * 500},
        {"time_zone": "Europe/London"},
        {"time_zone": "America/Nowhere"},
        {"time_zone": ""},
    ],
)
def test_a_bad_company_is_refused(client, staff, pmc, conn, body):
    assert client.post("/api/company", json={"name": "Synthetic PMC 1", **body}).status_code == 422
    assert rows(conn, pmc) == 0


@pytest.mark.parametrize(
    ("content", "content_type", "detail"),
    [
        (PDF, "image/png", "the file isn't a PNG"),
        (PNG, "image/jpeg", "the file isn't a JPG"),
        (b"", "image/png", None),
    ],
)
def test_a_logo_is_a_png_or_jpeg_it_says_it_is(client, staff, content, content_type, detail):
    answer = logo(client, content, content_type)
    assert answer.status_code == 422, answer.text
    if detail:
        assert answer.json()["detail"] == detail


def test_a_logo_is_an_image(client, staff):
    body = {"content_type": "application/pdf", "content_base64": base64.b64encode(PDF).decode()}
    assert client.post("/api/company/logo", json=body).status_code == 422
    body = {"content_type": "image/png", "content_base64": "not base64!"}
    assert client.post("/api/company/logo", json=body).status_code == 422
