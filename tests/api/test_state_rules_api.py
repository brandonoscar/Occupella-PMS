"""State rules as warnings through the API (q142, src/pms/api/state_rules.py): the table, and the
checks of late fee terms, a rent change and a visit against the state on the property's address.
The checks run on made-up rules put in place of California's and New York's (tests/
test_state_rules.py tests the rules' logic and the real table's shape), so these tests don't move
when the table is revised. A check only warns: nothing here is refused but a bad request."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import (
    add_unit,
    add_work_order,
    complete_offer,
    file_request,
    link_work_order,
    make_pmc,
    offer_lease,
    open_lease,
    send_offer,
    sign_offer,
    step_work_order,
)
from people import join_as_owner, join_as_staff, join_as_tenant

from pms import state_rules
from pms.state_rules import Bigger, EntryRule, LateFeeRule, RentNoticeRule, Source, StateRules

TODAY = datetime.now(UTC).date()
SOURCE = Source("Test Code 1-1", "https://example.org/test-code", "excerpt")
RULES = {
    "CA": StateRules(
        "California",
        late_fee=LateFeeRule(
            5,
            "rent is unpaid five days after its due date",
            "the lesser of $50 or 5% of the monthly rent",
            state_rules.lesser_of_rent("50", "5"),
            SOURCE,
        ),
        rent_notice=RentNoticeRule(
            30,
            "a rent increase needs 30 days' written notice",
            SOURCE,
            bigger=Bigger(Decimal(10), 90),
        ),
        entry=EntryRule(48, "at least 48 hours' notice before entering", SOURCE),
    ),
    "NY": StateRules("New York"),
    "VA": StateRules(
        "Virginia",
        entry=EntryRule(72, "72 hours' notice before routine maintenance", SOURCE, True),
    ),
}
HOME = {"line_1": "100 Synthetic Way", "city": "Demo City", "zip": "00001"}


def months_from(day, n):
    year, month = divmod(day.year * 12 + day.month - 1 + n, 12)
    return date(year, month + 1, 1)


IN_TWO = months_from(TODAY, 2)


@pytest.fixture(autouse=True)
def made_up_rules(monkeypatch):
    monkeypatch.setattr(state_rules, "RULES", RULES)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def prop(pmc):
    return pmc.owners[0].property_id


@pytest.fixture
def unit(conn, pmc, prop):
    return add_unit(conn, pmc.pmc_id, prop)


@pytest.fixture
def lease(conn, pmc, unit):
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    return open_lease(conn, pmc.pmc_id, unit, months_from(TODAY, -3), None, "1500.00", [tenant])


def in_state(client, prop, state):
    saved = client.post(f"/api/properties/{prop}/address", json={**HOME, "state": state})
    assert saved.status_code == 204, saved.text


def ok(answer):
    assert answer.status_code == 200, answer.text
    return answer.json()


def said(answer):
    return [w["said"] for w in ok(answer)["warnings"]]


def test_the_table_lists_each_state_with_its_statute_and_says_no_attorney_reviewed_it(
    client, staff, monkeypatch
):
    monkeypatch.undo()
    table = ok(client.get("/api/state-rules"))

    assert table["note"].startswith("No attorney has reviewed these state rules.")
    assert table["checked_on"] == str(state_rules.CHECKED_ON)
    codes = [row["state"] for row in table["states"]]
    assert codes == sorted(state_rules.RULES)
    for row in table["states"]:
        assert row["name"] == state_rules.RULES[row["state"]].name
        for rule in (row["late_fee"], row["rent_increase"], row["entry"]):
            assert rule is None or (rule["cite"] and rule["url"].startswith("https://"))


def test_the_table_in_words(client, staff):
    [ca, ny, va] = ok(client.get("/api/state-rules"))["states"]

    assert ca == {
        "state": "CA",
        "name": "California",
        "late_fee": {
            "words": "no late fee until rent is unpaid five days after its due date; at most the"
            " lesser of $50 or 5% of the monthly rent",
            "cite": "Test Code 1-1",
            "url": "https://example.org/test-code",
            "how": "excerpt",
        },
        "rent_increase": {
            "words": "a rent increase needs 30 days' written notice; an increase of more than 10%"
            " within 12 months needs 90 days' written notice",
            "cite": "Test Code 1-1",
            "url": "https://example.org/test-code",
            "how": "excerpt",
        },
        "entry": {
            "words": "at least 48 hours' notice before entering",
            "cite": "Test Code 1-1",
            "url": "https://example.org/test-code",
            "how": "excerpt",
        },
    }
    assert ny == {
        "state": "NY",
        "name": "New York",
        "late_fee": None,
        "rent_increase": None,
        "entry": None,
    }
    assert va["entry"]["words"] == "72 hours' notice before routine maintenance"


def late_fees(client, prop, **terms):
    query = "&".join(f"{k}={v}" for k, v in terms.items())
    return client.get(f"/api/state-rules/late-fees?property_id={prop}&{query}")


def test_late_fee_terms_as_typed_warn_of_the_wait_and_the_cap_at_each_leases_rent(
    client, staff, prop, lease
):
    in_state(client, prop, "CA")

    found = ok(late_fees(client, prop, grace_days=3, flat_fee="60.00", percent="1"))

    assert (found["state"], found["rules"]) == ("CA", True)
    assert found["note"] == state_rules.NOT_ADVICE
    assert [(w["rule"], w["cite"], w["how"]) for w in found["warnings"]] == [
        ("late fee wait", "Test Code 1-1", "excerpt"),
        ("late fee cap", "Test Code 1-1", "excerpt"),
    ]
    assert found["warnings"][1]["said"] == (
        "California: a late fee is at most the lesser of $50 or 5% of the monthly rent (Test Code"
        " 1-1). On Unit 1's rent of $1,500.00 these terms charge up to $75.00, over $50.00."
    )
    assert said(late_fees(client, prop, grace_days=5, flat_fee="60.00", maximum="50.00")) == []


def test_the_terms_in_force_are_checked_when_none_are_given(client, staff, prop, lease):
    in_state(client, prop, "CA")
    nothing = ok(late_fees(client, prop))
    assert (nothing["rules"], nothing["warnings"]) == (True, [])

    set_terms = client.post(
        "/api/setup/late-fees",
        json={"property_id": str(prop), "starts_on": str(TODAY), "grace_days": 2},
    )

    assert set_terms.status_code == 201, set_terms.text
    assert said(late_fees(client, prop)) == [
        "California: no late fee until rent is unpaid five days after its due date (Test Code"
        " 1-1). These terms charge one after 2 days of grace; 5 or more meets it."
    ]


def test_no_address_or_a_state_without_rules_checks_nothing(client, staff, prop, lease):
    none = ok(late_fees(client, prop, grace_days=0, flat_fee="900.00"))
    assert (none["state"], none["rules"], none["warnings"]) == (None, False, [])

    in_state(client, prop, "NY")
    found = ok(late_fees(client, prop, grace_days=0, flat_fee="900.00"))
    assert (found["state"], found["rules"], found["warnings"]) == ("NY", False, [])


def rent_change(client, lease, **change):
    query = "&".join(f"{k}={v}" for k, v in change.items())
    return client.get(f"/api/state-rules/rent-change?lease_id={lease}&{query}")


def test_a_rent_change_as_typed_and_on_file(client, staff, prop, lease):
    in_state(client, prop, "CA")
    big = {"effective_on": IN_TWO, "notice_given_on": TODAY, "monthly_rent": "1700.00"}
    given = (IN_TWO - TODAY).days

    assert said(rent_change(client, lease, **big)) == [
        "California: an increase of more than 10% within 12 months (from $1,500.00) needs 90"
        " days' written notice (Test Code 1-1). Notice given"
        f" {TODAY:%m/%d/%Y} for {IN_TWO:%m/%d/%Y} is {given} days."
    ]
    assert said(rent_change(client, lease, **big | {"monthly_rent": "1650.00"})) == []
    assert ok(rent_change(client, lease))["warnings"] == []

    recorded = client.post(
        f"/api/rent/leases/{lease}/rent-changes",
        json={
            "effective_on": str(IN_TWO),
            "monthly_rent": "1700.00",
            "notice_given_on": str(TODAY),
            "served_by": "mail",
        },
    )

    # The PMS records it all the same: a warning, not a refusal.
    assert recorded.status_code == 201, recorded.text
    assert said(rent_change(client, lease)) == said(rent_change(client, lease, **big))


def test_a_renewal_carries_the_tenancy_back(client, conn, staff, pmc, prop, unit):
    """A lease renewed from another counts the rents of the one it renewed: an increase is
    measured from the lowest rent of the 12 months before, whichever lease charged it."""
    in_state(client, prop, "CA")
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    first = open_lease(conn, pmc.pmc_id, unit, months_from(TODAY, -8), None, "1000.00", [tenant])
    offer = offer_lease(
        conn, pmc.pmc_id, unit, staff.person_id, months_from(TODAY, 1), rent="1080.00",
        renews_lease_id=first,
    )  # fmt: skip
    send_offer(conn, pmc.pmc_id, offer, staff.person_id)
    sign_offer(conn, pmc.pmc_id, offer, 1)
    renewal = complete_offer(conn, pmc.pmc_id, offer, staff.person_id)
    change = {"effective_on": IN_TWO, "notice_given_on": TODAY, "monthly_rent": "1150.00"}

    # $1,080.00 to $1,150.00 is 6.5%, but $1,000.00 to $1,150.00 is 15% within 12 months.
    [warning] = said(rent_change(client, renewal, **change))
    assert "more than 10% within 12 months (from $1,000.00)" in warning
    assert said(rent_change(client, renewal, **change | {"monthly_rent": "1100.00"})) == []


def entry(client, order, day=None):
    query = "" if day is None else f"&day={day}"
    return client.get(f"/api/state-rules/entry?work_order_id={order}{query}")


def test_a_visit_into_a_lived_in_home_set_too_soon_warns(
    client, conn, staff, pmc, prop, unit, lease
):
    in_state(client, prop, "CA")
    order = add_work_order(conn, pmc.pmc_id, prop, unit, "Leaky tap", datetime.now(UTC))
    whole = add_work_order(conn, pmc.pmc_id, prop, None, "Gutters", datetime.now(UTC))
    tomorrow = TODAY + timedelta(days=1)

    assert said(entry(client, order, tomorrow)) == [
        "California asks at least 48 hours' notice before entering (Test Code 1-1). This visit"
        f" on {tomorrow:%m/%d/%Y} was set on {TODAY:%m/%d/%Y}."
    ]
    assert said(entry(client, order, TODAY + timedelta(days=2))) == []
    # A job at no unit enters no one's home.
    assert said(entry(client, whole, TODAY)) == []
    assert ok(entry(client, order)) == ok(entry(client, whole)) | {"warnings": []}

    step_work_order(conn, pmc.pmc_id, order, "assigned", datetime.now(UTC), vendor_id=pmc.vendor_id)
    scheduled = client.post(
        f"/api/maintenance/work-orders/{order}/steps",
        json={"step": "scheduled", "scheduled_for": str(tomorrow)},
    )

    assert scheduled.status_code == 201, scheduled.text
    assert said(entry(client, order)) == said(entry(client, order, tomorrow))


def test_a_repair_the_tenant_asked_for_where_the_rule_leaves_it_out(
    client, conn, staff, pmc, prop, unit, lease
):
    in_state(client, prop, "VA")
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    asked = add_work_order(conn, pmc.pmc_id, prop, unit, "Leaky tap", datetime.now(UTC))
    routine = add_work_order(conn, pmc.pmc_id, prop, unit, "Filters", datetime.now(UTC))
    request = file_request(conn, pmc.pmc_id, lease, tenant, "The tap leaks")
    link_work_order(conn, pmc.pmc_id, request, asked)

    assert said(entry(client, asked, TODAY + timedelta(days=1))) == []
    assert len(said(entry(client, routine, TODAY + timedelta(days=1)))) == 1


def test_a_visit_to_an_empty_unit_or_no_visit_warns_of_nothing(
    client, conn, staff, pmc, prop, unit
):
    in_state(client, prop, "CA")
    order = add_work_order(conn, pmc.pmc_id, prop, unit, "Paint", datetime.now(UTC))

    found = ok(entry(client, order, TODAY))
    assert (found["state"], found["rules"], found["warnings"]) == ("CA", True, [])
    assert ok(entry(client, order))["warnings"] == []


def test_only_staff_check(client, conn, app_conn, pmc, prop, lease):
    order = add_work_order(conn, pmc.pmc_id, prop, None, "Gutters", datetime.now(UTC))
    paths = [
        "/api/state-rules",
        f"/api/state-rules/late-fees?property_id={prop}",
        f"/api/state-rules/rent-change?lease_id={lease}",
        f"/api/state-rules/entry?work_order_id={order}",
    ]
    for path in paths:
        assert client.get(path).status_code == 401, path

    join_as_owner(client, app_conn, pmc)
    for path in paths:
        assert client.get(path).status_code == 403, path
    client.cookies.clear()
    join_as_tenant(client, conn, app_conn, pmc)
    for path in paths:
        assert client.get(path).status_code == 403, path


def test_another_companys_records_are_not_found(client, conn, app_conn, pmc, staff):
    other = make_pmc(conn)
    their_unit = add_unit(conn, other.pmc_id, other.owners[0].property_id)
    their_tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (other.prepaid_rent,),
    ).fetchone()[0]
    their_lease = open_lease(conn, other.pmc_id, their_unit, TODAY, None, "900.00", [their_tenant])
    their_order = add_work_order(
        conn, other.pmc_id, other.owners[0].property_id, their_unit, "Tap", datetime.now(UTC)
    )

    assert late_fees(client, other.owners[0].property_id).status_code == 404
    assert rent_change(client, their_lease).status_code == 404
    assert entry(client, their_order).status_code == 404


def test_bad_input_is_refused(client, staff, prop, lease):
    for answer in (
        late_fees(client, prop, grace_days=-1),
        late_fees(client, prop, grace_days=5, percent="abc"),
        late_fees(client, prop, grace_days=5, flat_fee="1.234"),
        late_fees(client, prop, flat_fee="10.00"),
        client.get("/api/state-rules/late-fees"),
        client.get("/api/state-rules/late-fees?property_id=not-a-uuid"),
        rent_change(client, lease, effective_on=IN_TWO),
        rent_change(client, lease, effective_on=IN_TWO, notice_given_on="soon", monthly_rent=1),
        client.get("/api/state-rules/rent-change"),
        client.get(f"/api/state-rules/entry?work_order_id={prop}&day=tomorrow"),
    ):
        assert answer.status_code == 422, answer.text
    assert late_fees(client, prop, flat_fee="10.00").json()["detail"] == (
        "terms to check give their grace days"
    )
    assert rent_change(client, lease, effective_on=IN_TWO).json()["detail"] == (
        "a rent change to check gives its day, notice and rent"
    )
