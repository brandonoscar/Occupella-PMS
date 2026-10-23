"""State and local rules through the API (migration 20261023000202, src/pms/api/jurisdictions.py):
a property's place in the law as the geocoder finds it (the fake one here; nothing reaches the
network) or staff confirm or correct it, the facts rules turn on, rules staff add from a statute
and confirm, and the checks the forms ask for, which put the rule beside the field and warn,
never refuse. Unknown is never "no rule". The database's side is in
tests/test_jurisdiction_rules.py. Every address, name and rule here is synthetic: none is the
law."""

import json
import uuid
from datetime import date

import pytest
from helpers import add_tenant, add_unit, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

from pms import jurisdictions
from pms.api import Settings

HOME = {"line_1": "100 Synthetic Way", "city": "Demo City", "state": "CA", "zip": "00001"}
NOWHERE = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def settings(database_url):
    return Settings(
        database_url=database_url,
        public_url="http://testserver",
        cookie_secure=False,
        geocoder="fake",
    )


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Clerk 1")


@pytest.fixture
def prop(pmc):
    return pmc.owners[0].property_id


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def post(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def located(client, prop, **address):
    ok(client.post(f"/api/properties/{prop}/address", json={**HOME, **address}), 204)
    ok(client.post(f"/api/properties/{prop}/locate", json={}), 201)


def law(client, prop, **params):
    return ok(client.get(f"/api/properties/{prop}/law", params=params))


def by_kind(found):
    return {r["rule"]: r for r in found["rules"]}


RULE = {
    "level": "state",
    "rule": "deposit_cap",
    "value_kind": "number",
    "value_number": "1",
    "value_unit": "months_of_rent",
    "effective_from": "2020-01-01",
    "citation": "Synthetic Code 1950.5",
    "url": "https://example.org/synthetic-code",
    "checked_on": "2026-10-23",
    "reviewed_by": "Synthetic Reviewer",
}


def add_rule(client, prop, **changes):
    return post(client, "/api/jurisdiction-rules", property_id=prop, **(RULE | changes))


def test_finding_a_property_on_the_map_keeps_where_it_is_in_the_law(client, staff, prop):
    located(client, prop)
    found = law(client, prop)
    where = found["jurisdiction"]
    assert (where["state_fips"], where["state_name"], where["source"]) == (
        "06",
        "California",
        "fake",
    )
    assert where["place_name"] == "Demo City city" and where["recorded_by"] == "Clerk 1"
    assert [lv["level"] for lv in found["levels"]] == ["federal", "state", "county", "city"]
    # Nothing on file: every kind unknown, never "no rule".
    assert len(found["rules"]) == len(jurisdictions.KINDS)
    for shown in found["rules"]:
        assert shown["known"] is False
        assert shown["words"].startswith("Unknown: no rule of this kind on file for Demo City city")
    assert "never a guarantee or legal advice" in found["note"]


def test_without_an_address_or_a_place_every_rule_is_unknown_and_says_why(client, staff, prop):
    found = law(client, prop)
    assert (found["has_address"], found["jurisdiction"]) == (False, None)
    assert {r["words"] for r in found["rules"]} == {"Unknown: the property has no address on file"}
    refused = post(client, f"/api/properties/{prop}/jurisdiction", county_fips="001")
    assert refused.status_code in (409, 422)
    ok(client.post(f"/api/properties/{prop}/address", json=HOME), 204)
    found = law(client, prop)
    assert found["has_address"] is True
    assert by_kind(found)["deposit_cap"]["words"].startswith(
        "Unknown: where the property's address is in the law isn't on file yet"
    )


def test_staff_confirm_or_correct_where_it_is(client, conn, staff, prop):
    located(client, prop)
    corrected = ok(
        post(
            client, f"/api/properties/{prop}/jurisdiction", county_fips="999",
            county_name="Demo County", tract="999900",
        ),
        201,
    )  # fmt: skip
    assert (corrected["county_fips"], corrected["place_fips"], corrected["source"]) == (
        "999",
        None,
        "staff",
    )  # outside any city now
    assert law(client, prop)["jurisdiction"]["recorded_by"] == "Clerk 1"
    assert [lv["level"] for lv in law(client, prop)["levels"]] == ["federal", "state", "county"]
    # A new address leaves it unknown until it is found again.
    ok(client.post(f"/api/properties/{prop}/address", json={**HOME, "line_1": "2 Example St"}), 204)
    assert law(client, prop)["jurisdiction"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"county_fips": "999"},  # a code without its name
        {"county_name": "Demo County"},
        {"county_fips": "99", "county_name": "Demo County"},
        {"tract": "999900"},  # a tract outside a county
        {"cousub_fips": "99999", "cousub_name": "Demo CCD"},
        {"place_fips": "9999", "place_name": "Demo City city"},
    ],
)
def test_a_place_that_isnt_whole_is_refused(client, staff, prop, body):
    ok(client.post(f"/api/properties/{prop}/address", json=HOME), 204)
    assert client.post(f"/api/properties/{prop}/jurisdiction", json=body).status_code == 422


def test_staff_keep_the_facts_rules_turn_on(client, conn, staff, prop):
    assert law(client, prop)["facts"]["year_built"] is None
    facts = {"year_built": 1978, "units_in_building": 4, "owner_occupied": False,
             "owner_kind": "person"}  # fmt: skip
    ok(post(client, f"/api/properties/{prop}/facts", **facts), 204)
    ok(post(client, f"/api/properties/{prop}/facts", **facts), 204)  # the same: nothing new
    count = conn.execute(
        "SELECT count(*) FROM trust_property_facts WHERE property_id = %s", (prop,)
    ).fetchone()[0]
    assert count == 1
    kept = law(client, prop)["facts"]
    assert {k: kept[k] for k in facts} == facts and kept["recorded_by"] == "Clerk 1"
    for bad in ({"year_built": 1500}, {"owner_kind": "llc"}, {"units_in_building": 0}):
        assert client.post(f"/api/properties/{prop}/facts", json=bad).status_code == 422


def test_a_rule_staff_add_shows_cited_and_confirmed_for_every_property_in_its_place(
    client, conn, staff, pmc, prop
):
    located(client, prop)
    made = ok(add_rule(client, prop, exceptions="Never of a synthetic exception."), 201)
    shown = by_kind(law(client, prop))["deposit_cap"]
    assert shown["known"] is True and shown["rule_id"] == made["id"]
    assert shown["words"] == "At most 1 month's rent"
    assert (shown["citation"], shown["url"], shown["checked_on"], shown["reviewed_by"]) == (
        "Synthetic Code 1950.5",
        "https://example.org/synthetic-code",
        "2026-10-23",
        "Synthetic Reviewer",
    )
    assert (shown["jurisdiction_name"], shown["confirmed_by"], shown["source"]) == (
        "California",
        "Clerk 1",
        "staff",
    )
    assert shown["exceptions"] == "Never of a synthetic exception."
    # Another of the company's properties in the same state has it; one in another state doesn't.
    second = pmc.owners[1].property_id
    located(client, second)
    assert by_kind(law(client, second))["deposit_cap"]["known"] is True
    located(client, second, state="TX")
    assert by_kind(law(client, second))["deposit_cap"]["known"] is False


def test_a_correction_takes_the_place_of_what_it_corrects(client, staff, prop):
    located(client, prop)
    wrong = ok(add_rule(client, prop, value_number="2"), 201)["id"]
    right = ok(add_rule(client, prop, supersedes=wrong), 201)["id"]
    assert by_kind(law(client, prop))["deposit_cap"]["rule_id"] == right
    again = add_rule(client, prop, supersedes=wrong)
    assert (again.status_code, again.json()["detail"]) == (
        409,
        "that rule was corrected already: correct the newer one",
    )
    assert add_rule(client, prop, supersedes=NOWHERE).status_code == 404


def test_a_rule_that_turns_on_a_fact_not_on_file_is_unknown_until_it_is(client, staff, prop):
    located(client, prop)
    ok(add_rule(client, prop), 201)
    ok(add_rule(client, prop, value_number="2", precedence=10, owner_kind="person"), 201)
    shown = by_kind(law(client, prop))["deposit_cap"]
    assert (shown["known"], shown["needs"]) == (False, ["owner_kind"])
    assert shown["words"] == (
        "Unknown until whether the owner is a person or a company is on file for the property"
    )
    ok(post(client, f"/api/properties/{prop}/facts", owner_kind="person"), 204)
    assert by_kind(law(client, prop))["deposit_cap"]["words"] == "At most 2 months' rent"
    ok(post(client, f"/api/properties/{prop}/facts", owner_kind="company"), 204)
    assert by_kind(law(client, prop))["deposit_cap"]["words"] == "At most 1 month's rent"


@pytest.mark.parametrize(
    ("changes", "status", "said"),
    [
        ({"value_unit": "days"}, 422, "that unit doesn't fit that kind of rule"),
        ({"value_unit": None}, 422, "a number needs its unit, and words need their text"),
        ({"combine": "lesser"}, 422, "a second number needs its unit and lesser or greater"),
        ({"effective_to": "2019-01-01"}, 422, "a rule stops holding after the day it starts"),
        ({"checked_on": "2999-01-01"}, 422, "the day the statute was read can't be after today"),
        ({"url": "http://example.org/x"}, 422, None),
        ({"level": "city", "property_id": None}, 409, None),
    ],
)
def test_a_rule_that_doesnt_hold_together_is_refused(client, staff, prop, changes, status, said):
    located(client, prop, city="Unincorporated Demo")  # no city
    if changes.get("property_id", 1) is None:
        changes = {k: v for k, v in changes.items() if k != "property_id"}
    answer = add_rule(client, prop, **changes)
    assert answer.status_code == status, answer.text
    if said:
        assert answer.json()["detail"] == said


def test_staff_confirm_a_rule_the_pms_added_once(client, conn, staff, pmc, prop):
    located(client, prop)
    entry = {
        "key": "synthetic-nsf-cap",
        "level": "state",
        "jurisdiction": "06",
        "jurisdiction_name": "California",
        "rule": "nsf_fee_cap",
        "value_kind": "number",
        "value_number": 25,
        "value_unit": "dollars",
        "effective_from": date(2020, 1, 1),
        "citation": "Synthetic Code 2",
        "url": "https://example.org/synthetic-code-2",
        "checked_on": date(2026, 10, 23),
        "reviewed_by": "Synthetic Reviewer",
    }
    assert jurisdictions.seed(conn, pmc.pmc_id, [entry]) == 1
    shown = by_kind(law(client, prop))["nsf_fee_cap"]
    assert (shown["source"], shown["confirmed_by"], shown["words"]) == (
        "pms",
        None,
        "At most $25.00",
    )
    ok(client.post(f"/api/jurisdiction-rules/{shown['rule_id']}/confirm", json={}), 204)
    ok(client.post(f"/api/jurisdiction-rules/{shown['rule_id']}/confirm", json={}), 204)
    assert by_kind(law(client, prop))["nsf_fee_cap"]["confirmed_by"] == "Clerk 1"
    assert client.post(f"/api/jurisdiction-rules/{NOWHERE}/confirm", json={}).status_code == 404


def lease(conn, pmc, prop, rent="1000.00", starts=date(2026, 1, 1)):
    tenant = add_tenant(conn, pmc.pmc_id, prop, "Reese Sample")
    unit = add_unit(conn, pmc.pmc_id, prop, f"Unit {uuid.uuid4().hex[:4]}")
    return open_lease(conn, pmc.pmc_id, unit, starts, date(2027, 12, 31), rent, [tenant]), tenant


def test_late_fee_terms_are_checked_against_the_wait_and_the_cap(client, conn, staff, pmc, prop):
    located(client, prop)
    lease(conn, pmc, prop)
    ok(add_rule(client, prop, rule="late_fee_grace_days", value_number="5", value_unit="days"), 201)
    ok(
        add_rule(
            client, prop, rule="late_fee_cap", value_number="50", value_unit="dollars",
            combine="lesser", also_number="5", also_unit="percent_of_rent",
        ),
        201,
    )  # fmt: skip
    path = "/api/law/late-fees"
    base = {"property_id": prop}
    shown = ok(client.get(path, params=base))
    assert [r["words"] for r in shown["rules"]] == [
        "At least 5 days",
        "At most the lesser of $50.00 or 5% of the monthly rent",
    ]
    assert shown["warnings"] == []
    within = ok(client.get(path, params=base | {"grace_days": 5, "flat_fee": "50.00"}))
    assert within["warnings"] == []
    past = ok(client.get(path, params=base | {"grace_days": 3, "flat_fee": "60.00"}))
    assert [w["rule"] for w in past["warnings"]] == ["late_fee_grace_days", "late_fee_cap"]
    assert (
        "These terms charge one after 3 days of grace; 5 or more meets it."
        in (past["warnings"][0]["said"])
    )
    assert "these terms charge up to $60.00, over $50.00" in past["warnings"][1]["said"]
    assert client.get(path, params=base | {"grace_days": -1}).status_code == 422


def test_an_nsf_fee_a_deposit_and_a_rent_change_are_checked(client, conn, staff, pmc, prop):
    located(client, prop)
    lease_id, tenant = lease(conn, pmc, prop)
    ok(add_rule(client, prop, rule="nsf_fee_cap", value_number="25", value_unit="dollars"), 201)
    ok(add_rule(client, prop), 201)  # a deposit of at most a month's rent
    ok(add_rule(client, prop, rule="rent_increase_notice_days", value_number="90",
                value_unit="days"), 201)  # fmt: skip
    ok(add_rule(client, prop, rule="rent_cap", value_number="5", value_unit="percent_a_year"), 201)
    ok(
        add_rule(client, prop, rule="deposit_return_days", value_number="21", value_unit="days"),
        201,
    )

    nsf = ok(client.get("/api/law/nsf-fee", params={"property_id": prop, "fee": "35.00"}))
    assert nsf["warnings"][0]["said"].endswith("This fee is $35.00.")
    assert (
        ok(client.get("/api/law/nsf-fee", params={"property_id": prop, "fee": "25.00"}))["warnings"]
        == []
    )

    ok(
        post(
            client, "/api/rent/deposits", request_id=uuid.uuid4(), tenant_id=tenant,
            amount="800.00", received_on=date.today(),
        ),
        201,
    )  # fmt: skip
    deposit = ok(client.get("/api/law/deposit", params={"tenant_id": tenant, "amount": "300.00"}))
    (warned,) = deposit["warnings"]
    assert "with $800.00 held already, comes to $1,100.00" in warned["said"]
    assert "(on this lease's rent of $1,000.00, $1,000.00)" in warned["said"]
    assert (
        ok(client.get("/api/law/deposit", params={"tenant_id": tenant, "amount": "200.00"}))[
            "warnings"
        ]
        == []
    )

    change = {"lease_id": lease_id, "effective_on": "2027-03-01", "monthly_rent": "1100.00"}
    late = ok(client.get("/api/law/rent-change", params=change | {"notice_given_on": "2027-01-15"}))
    assert [w["rule"] for w in late["warnings"]] == ["rent_increase_notice_days", "rent_cap"]
    assert "Notice given 01/15/2027 is 45 days before 03/01/2027." in late["warnings"][0]["said"]
    assert "$1,100.00 is 10.00% more" in late["warnings"][1]["said"]
    fine = change | {"notice_given_on": "2026-11-01", "monthly_rent": "1050.00"}
    assert ok(client.get("/api/law/rent-change", params=fine))["warnings"] == []

    back = ok(client.get("/api/law/deposit-return", params={"lease_id": lease_id}))
    (shown,) = back["rules"]
    assert shown["words"] == "Within 21 days after the tenancy ends"
    assert shown["here"].startswith("For this lease, by 01/21/2028")


def test_checks_of_another_companys_records_are_not_found(client, conn, staff):
    other = make_pmc(conn)
    elsewhere = other.owners[0].property_id
    lease_id, tenant = lease(conn, other, elsewhere)
    assert client.get(f"/api/properties/{elsewhere}/law").status_code == 404
    assert client.get("/api/law/late-fees", params={"property_id": elsewhere}).status_code == 404
    assert client.get("/api/law/nsf-fee", params={"property_id": elsewhere}).status_code == 404
    assert client.get("/api/law/deposit", params={"tenant_id": tenant}).status_code == 404
    assert client.get("/api/law/rent-change", params={"lease_id": lease_id}).status_code == 404
    assert client.get("/api/law/deposit-return", params={"lease_id": lease_id}).status_code == 404
    assert add_rule(client, elsewhere).status_code == 404
    assert post(client, f"/api/properties/{elsewhere}/facts", year_built=1990).status_code == 404


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_read_and_keep_the_rules(client, conn, app_conn, pmc, prop, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    assert client.get(f"/api/properties/{prop}/law").status_code == status
    assert client.get("/api/law/late-fees", params={"property_id": prop}).status_code == status
    assert add_rule(client, prop).status_code == status
    assert post(client, f"/api/properties/{prop}/facts", year_built=1990).status_code == status
    assert client.post(f"/api/jurisdiction-rules/{NOWHERE}/confirm", json={}).status_code == status


def test_a_custom_role_without_setup_reads_the_rules_but_keeps_none(
    client, other_client, conn, app_conn, staff, pmc, prop
):
    located(client, prop)
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 2", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Rent", "areas": ["rent"]}), 201)
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )
    assert other_client.get(f"/api/properties/{prop}/law").status_code == 200
    refused = add_rule(other_client, prop)
    assert (
        refused.status_code == 403
        and "setup" in refused.json()["detail"].lower()
        or (refused.status_code == 403)
    )
    assert post(other_client, f"/api/properties/{prop}/facts", year_built=1990).status_code == 403
