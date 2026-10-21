"""A housing authority's part of a lease's rent through the API (src/pms/api/assistance.py,
migration 20261020000095, the founder's review q034): staff keep the company's housing
authorities, set a lease's assistance from a month, charge or credit the authority by hand and
record its payments, which pay what it owes into the property's owners' accounts. The database's
side is in tests/test_housing_assistance.py. Every name here is synthetic."""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import (
    add_tenant,
    add_unit,
    balance,
    charge_rent_due,
    co_own,
    make_pmc,
    open_lease,
    set_ownership,
)
from people import join_as_owner, join_as_staff, tenant_of

from pms.api.assistance import a_year_after

TODAY = datetime.now(UTC).date()
FIRST = TODAY.replace(day=1)
STARTED = (FIRST - timedelta(days=40)).replace(day=1)
NEXT = (FIRST + timedelta(days=32)).replace(day=1)
HA = "Synthetic Housing Authority"
NONE_ON_FILE = {
    "recertify_by": None,
    "recertification": "none on file",
    "inspect_by": None,
    "inspection": "none on file",
    "certifications": [],
    "inspections": [],
}


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def lease_at(conn, pmc, n=0, rent="1000.00"):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[n].property_id)
    tenant = add_tenant(conn, pmc.pmc_id, pmc.owners[n].property_id, f"Tenant {n + 1}")
    return open_lease(conn, pmc.pmc_id, unit, STARTED, None, rent, [tenant])


@pytest.fixture
def lease(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    return open_lease(conn, pmc.pmc_id, unit, STARTED, None, "1000.00", [tenant_of(conn, pmc)])


def post(client, path, **body):
    return client.post(path, json=json.loads(json.dumps(body, default=str)))


def ok(answer, status=201):
    assert answer.status_code == status, answer.text
    return answer.json()


def refused(answer, status, said=None):
    assert answer.status_code == status, answer.text
    if said is not None:
        assert said in str(answer.json()["detail"]), answer.text


def add_authority(client, name=HA):
    return ok(post(client, "/api/assistance/authorities", name=name))["id"]


def terms(client, lease, authority, amount="800.00", starts_on=FIRST, **more):
    return post(
        client,
        f"/api/assistance/leases/{lease}/terms",
        housing_authority_id=authority,
        starts_on=starts_on,
        amount=amount,
        **more,
    )


def charge(client, lease, kind="charge", amount="50.00", **more):
    return post(
        client,
        f"/api/assistance/leases/{lease}/charges",
        kind=kind,
        amount=amount,
        due_on=FIRST,
        memo="A month before the assistance was on file",
        **more,
    )


def pay(client, authority, lines, request_id=None, **more):
    return post(
        client,
        "/api/assistance/payments",
        request_id=request_id or uuid.uuid4(),
        housing_authority_id=authority,
        received_on=TODAY,
        lines=lines,
        **more,
    )


def page_of(client, lease):
    return ok(client.get(f"/api/assistance/leases/{lease}"), 200)


def tenant_rent(conn, lease):
    return [
        amount
        for (amount,) in conn.execute(
            "SELECT amount FROM trust_charges WHERE lease_id = %s AND kind = 'rent'"
            " ORDER BY due_on",
            (lease,),
        ).fetchall()
    ]


# --- the authorities ---------------------------------------------------------------------------


def test_staff_add_the_companys_housing_authorities(client, staff):
    assert ok(client.get("/api/assistance/authorities"), 200) == []

    first = add_authority(client)
    second = add_authority(client, "Demo County Housing")

    assert ok(client.get("/api/assistance/authorities"), 200) == [
        {"id": second, "name": "Demo County Housing", "leases": 0, "owed": "0.00"},
        {"id": first, "name": HA, "leases": 0, "owed": "0.00"},
    ]
    page = ok(client.get(f"/api/assistance/authorities/{first}"), 200)
    assert page == {"id": first, "name": HA, "leases": [], "owed": "0.00"}


def test_an_authority_is_on_file_once_and_has_a_name(client, staff):
    add_authority(client)

    refused(
        post(client, "/api/assistance/authorities", name=HA.upper()),
        409,
        "a housing authority by that name is on file already",
    )
    refused(post(client, "/api/assistance/authorities", name="  "), 422)
    refused(client.get(f"/api/assistance/authorities/{uuid.uuid4()}"), 404)


# --- a lease's assistance ----------------------------------------------------------------------


def test_a_leases_assistance_splits_its_rent_and_its_page_shows_it(client, conn, pmc, staff, lease):
    authority = add_authority(client)
    assert page_of(client, lease) == {
        "in_force": None,
        "monthly_rent": "1000.00",
        "tenant_part": "1000.00",
        "terms": [],
        "charges": [],
        "owed": "0.00",
        "compliance": NONE_ON_FILE,
    }

    made = ok(terms(client, lease, authority, "800.00", contract="HAP-0001"))["id"]
    charge_rent_due(conn, pmc.pmc_id, FIRST)

    assert tenant_rent(conn, lease) == [Decimal("200.00")]
    page = page_of(client, lease)
    held = {
        "id": made,
        "authority_id": authority,
        "authority": HA,
        "starts_on": FIRST.isoformat(),
        "amount": "800.00",
        "contract": "HAP-0001",
        "recorded_by": "Staff 1",
    }
    assert page["in_force"] == held
    assert page["terms"] == [held]
    assert (page["monthly_rent"], page["tenant_part"], page["owed"]) == (
        "1000.00",
        "200.00",
        "800.00",
    )
    (owed,) = page["charges"]
    assert owed | {"id": None} == {
        "id": None,
        "lease_id": str(lease),
        "authority_id": authority,
        "due_on": FIRST.isoformat(),
        "memo": f"Rent due {FIRST.isoformat()}",
        "source": "rent",
        "charged": "800.00",
        "adjusted": "0.00",
        "paid": "0.00",
        "open": "800.00",
    }
    assert ok(client.get("/api/assistance/authorities"), 200)[0] | {"id": None} == {
        "id": None,
        "name": HA,
        "leases": 1,
        "owed": "800.00",
    }


def test_assistance_of_0_ends_it(client, staff, lease):
    authority = add_authority(client)
    ok(terms(client, lease, authority, "800.00", starts_on=STARTED))
    ok(terms(client, lease, authority, "0", starts_on=FIRST))

    page = page_of(client, lease)
    assert page["in_force"] is None
    assert page["tenant_part"] == "1000.00"
    # Ended: the authority assists no lease today.
    assert ok(client.get("/api/assistance/authorities"), 200)[0]["leases"] == 0
    assert [(t["starts_on"], t["amount"]) for t in page["terms"]] == [
        (FIRST.isoformat(), "0.00"),
        (STARTED.isoformat(), "800.00"),
    ]


def test_assistance_starts_on_the_first_of_a_month_not_yet_charged_and_within_the_rent(
    client, conn, pmc, staff, lease
):
    authority = add_authority(client)

    refused(
        terms(client, lease, authority, starts_on=FIRST + timedelta(days=1)),
        422,
        "housing assistance starts on the first of a month",
    )
    refused(terms(client, lease, authority, "1000.01"), 409, "no more than the rent")
    refused(terms(client, lease, authority, "-1.00"), 422)
    refused(terms(client, uuid.uuid4(), authority), 404)
    refused(terms(client, lease, uuid.uuid4()), 404, "no such housing authority")

    ok(terms(client, lease, authority, starts_on=NEXT))
    refused(
        terms(client, lease, authority, "700.00", starts_on=NEXT),
        409,
        "the lease has assistance from that month already",
    )
    charge_rent_due(conn, pmc.pmc_id, FIRST)
    refused(terms(client, lease, authority, starts_on=FIRST), 409, "charged already")


def test_rent_lowered_below_the_assistance_leaves_the_tenant_nothing(
    client, conn, pmc, staff, lease
):
    authority = add_authority(client)
    ok(terms(client, lease, authority, "800.00", starts_on=STARTED))
    conn.execute(
        "INSERT INTO trust_rent_changes (pmc_id, lease_id, effective_on, monthly_rent,"
        " notice_given_on, served_by) VALUES (%s, %s, %s, 700, %s, 'mail')",
        (pmc.pmc_id, lease, FIRST, (FIRST - timedelta(days=1)).replace(day=1)),
    )

    page = page_of(client, lease)
    assert (page["monthly_rent"], page["tenant_part"]) == ("700.00", "0.00")


def test_an_authority_and_a_lease_of_another_company_are_not_found(client, conn, staff):
    other = make_pmc(conn)
    elsewhere = lease_at(conn, other)

    refused(client.get(f"/api/assistance/leases/{elsewhere}"), 404)
    refused(post(client, "/api/assistance/authorities", name=""), 422)


# --- charging the authority by hand ------------------------------------------------------------


def test_staff_charge_the_authority_and_credit_no_more_than_is_open(client, staff, lease):
    authority = add_authority(client)

    made = ok(charge(client, lease, housing_authority_id=authority))["id"]
    ok(charge(client, lease, "credit", "20.00", charge_id=made))

    (owed,) = page_of(client, lease)["charges"]
    assert (owed["id"], owed["source"], owed["charged"], owed["adjusted"], owed["open"]) == (
        made,
        "staff",
        "50.00",
        "-20.00",
        "30.00",
    )
    refused(charge(client, lease, "credit", "30.01", charge_id=made), 409, "30.00")
    assert page_of(client, lease)["owed"] == "30.00"


@pytest.mark.parametrize(
    ("asked", "status", "said"),
    [
        ("a charge of no authority", 422, "a charge names its housing authority, and no charge"),
        ("a charge naming a charge", 422, "a charge names its housing authority, and no charge"),
        ("a credit of no charge", 422, "a credit names the authority's charge it takes off"),
        ("a credit of an unknown charge", 404, "no such charge"),
        ("a credit of another lease's charge", 404, "no such charge"),
        ("a charge of an unknown authority", 404, "no such housing authority"),
        ("a charge of nothing", 422, None),
    ],
)
def test_a_charge_names_its_authority_and_a_credit_its_charge(
    client, conn, pmc, staff, lease, asked, status, said
):
    authority = add_authority(client)
    made = ok(charge(client, lease, housing_authority_id=authority))["id"]
    other = lease_at(conn, pmc, 1)
    elsewhere = ok(charge(client, other, housing_authority_id=authority))["id"]
    body = {
        "a charge of no authority": {},
        "a charge naming a charge": {"housing_authority_id": authority, "charge_id": made},
        "a credit of no charge": {"kind": "credit"},
        "a credit of an unknown charge": {"kind": "credit", "charge_id": uuid.uuid4()},
        "a credit of another lease's charge": {"kind": "credit", "charge_id": elsewhere},
        "a charge of an unknown authority": {"housing_authority_id": uuid.uuid4()},
        "a charge of nothing": {"housing_authority_id": authority, "amount": "0"},
    }[asked]

    refused(charge(client, lease, **body), status, said)


# --- the authority's payments ------------------------------------------------------------------


def test_an_authority_pays_for_several_leases_into_each_propertys_owner(client, conn, pmc, staff):
    authority = add_authority(client)
    first, second = lease_at(conn, pmc, 0), lease_at(conn, pmc, 1, "900.00")
    ok(terms(client, first, authority, "800.00"))
    ok(terms(client, second, authority, "500.00"))
    charge_rent_due(conn, pmc.pmc_id, FIRST)
    before = [balance(conn, owner.account) for owner in pmc.owners[:2]]

    paid = ok(
        pay(
            client,
            authority,
            [{"lease_id": first, "amount": "800.00"}, {"lease_id": second, "amount": "300.00"}],
            reference="HAP 1001",
            method="EFT",
        )
    )

    assert paid["paid"] == "1100.00" and len(paid["transfers"]) == 2
    assert [balance(conn, owner.account) for owner in pmc.owners[:2]] == [
        before[0] + Decimal("800.00"),
        before[1] + Decimal("300.00"),
    ]
    assert page_of(client, first)["owed"] == "0.00"
    assert page_of(client, second)["charges"][0]["paid"] == "300.00"
    page = ok(client.get(f"/api/assistance/authorities/{authority}"), 200)
    assert page["owed"] == "200.00"
    assert {
        (row["home"].split(", ")[0], row["monthly"], row["owed"], len(row["open_charges"]))
        for row in page["leases"]
    } == {("Tenant 1", "800.00", "0.00", 0), ("Tenant 2", "500.00", "200.00", 1)}
    # The tenants' rent is theirs alone, and still open.
    assert tenant_rent(conn, first) == [Decimal("200.00")]
    owed = conn.execute("SELECT trust_lease_owed(%s)", (first,)).fetchone()[0]
    assert owed == Decimal("200.00")


def test_the_same_payment_again_records_nothing_new(client, conn, pmc, staff, lease):
    authority = add_authority(client)
    ok(terms(client, lease, authority))
    charge_rent_due(conn, pmc.pmc_id, FIRST)
    request = uuid.uuid4()
    lines = [{"lease_id": lease, "amount": "500.00"}]
    first = ok(pay(client, authority, lines, request, reference="HAP 1001"))
    after = balance(conn, pmc.owners[0].account)

    # Sent again as it was, or with its check number corrected: the same payment.
    assert ok(pay(client, authority, lines, request, reference="HAP 1001")) == first
    assert ok(pay(client, authority, lines, request, reference="HAP 1002")) == first
    assert balance(conn, pmc.owners[0].account) == after
    assert page_of(client, lease)["owed"] == "300.00"
    # Another payment under its request id is refused.
    refused(
        pay(client, authority, [{"lease_id": lease, "amount": "200.00"}], request),
        409,
        "this request id was used for a different payment",
    )


def test_a_payment_splits_by_the_propertys_ownership(client, conn, pmc, staff, lease):
    second = co_own(conn, pmc)
    set_ownership(
        conn,
        pmc.pmc_id,
        pmc.owners[0].property_id,
        STARTED,
        [(pmc.owners[0].account, "60"), (second, "40")],
    )
    authority = add_authority(client)
    ok(terms(client, lease, authority))
    charge_rent_due(conn, pmc.pmc_id, FIRST)
    before = balance(conn, pmc.owners[0].account), balance(conn, second)

    ok(pay(client, authority, [{"lease_id": lease, "amount": "700.01"}]))

    # 60% of 700.01 is 420.006, 40% 280.004: the cent left goes to the larger remainder.
    assert (balance(conn, pmc.owners[0].account), balance(conn, second)) == (
        before[0] + Decimal("420.01"),
        before[1] + Decimal("280.00"),
    )
    (owed,) = page_of(client, lease)["charges"]
    assert (owed["paid"], owed["open"]) == ("700.01", "99.99")


def test_a_property_with_several_owners_and_no_shares_names_the_account(
    client, conn, pmc, staff, lease
):
    second = co_own(conn, pmc)
    authority = add_authority(client)
    ok(terms(client, lease, authority))
    charge_rent_due(conn, pmc.pmc_id, FIRST)

    refused(pay(client, authority, [{"lease_id": lease, "amount": "100.00"}]), 422, "choose one")
    ok(pay(client, authority, [{"lease_id": lease, "amount": "100.00", "account": second}]))
    assert page_of(client, lease)["owed"] == "700.00"


def test_an_authority_pays_no_more_than_it_owes_and_all_or_none(client, conn, pmc, staff, lease):
    authority = add_authority(client)
    other = lease_at(conn, pmc, 1)
    ok(terms(client, lease, authority))
    charge_rent_due(conn, pmc.pmc_id, FIRST)
    before = balance(conn, pmc.owners[0].account)

    refused(
        pay(
            client,
            authority,
            [{"lease_id": lease, "amount": "800.00"}, {"lease_id": other, "amount": "0.01"}],
        ),
        422,
        f"0.01 is more than {HA} owes on Tenant 2",
    )
    refused(pay(client, authority, [{"lease_id": lease, "amount": "800.01"}]), 422, ": 800.00")
    assert balance(conn, pmc.owners[0].account) == before
    assert page_of(client, lease)["owed"] == "800.00"


@pytest.mark.parametrize(
    ("change", "status", "said"),
    [
        ("twice", 422, "each lease is in the payment once"),
        ("tomorrow", 422, None),
        ("no lines", 422, None),
        ("unknown authority", 404, "no such housing authority"),
        ("unknown lease", 404, None),
    ],
)
def test_a_payment_names_each_lease_once_and_an_authority_of_the_company(
    client, conn, pmc, staff, lease, change, status, said
):
    authority = add_authority(client)
    line = {"lease_id": lease, "amount": "10.00"}
    lines = {
        "twice": [line, line],
        "no lines": [],
        "unknown lease": [{"lease_id": uuid.uuid4(), "amount": "10.00"}],
    }.get(change, [line])
    answer = post(
        client,
        "/api/assistance/payments",
        request_id=uuid.uuid4(),
        housing_authority_id=uuid.uuid4() if change == "unknown authority" else authority,
        received_on=TODAY + timedelta(days=1) if change == "tomorrow" else TODAY,
        lines=lines,
    )

    refused(answer, status, said)


# --- keeping the lease in compliance (migration 0108, q143) -----------------------------------


def certify(client, lease, kind="annual", effective_on=STARTED, **more):
    return post(
        client,
        f"/api/assistance/leases/{lease}/certifications",
        kind=kind,
        effective_on=effective_on,
        household_size=more.pop("household_size", 3),
        annual_income=more.pop("annual_income", "18000.00"),
        **more,
    )


def inspect(client, lease, result="passed", inspected_on=TODAY, **more):
    return post(
        client,
        f"/api/assistance/leases/{lease}/inspections",
        result=result,
        inspected_on=inspected_on,
        **more,
    )


@pytest.fixture
def assisted(client, staff, lease):
    """The lease, its housing authority assisting it from its first day."""
    authority = add_authority(client)
    ok(terms(client, lease, authority, starts_on=STARTED))
    return authority


def test_the_lease_page_keeps_certifications_and_inspections_and_says_where_they_stand(
    client, lease, assisted
):
    assert page_of(client, lease)["compliance"] == NONE_ON_FILE

    made = ok(certify(client, lease, "move-in"))["id"]
    seen = ok(inspect(client, lease))["id"]

    assert page_of(client, lease)["compliance"] == {
        # A year on and two years on, unless given (24 CFR 982.516(a), 982.405(a)).
        "recertify_by": a_year_after(STARTED).isoformat(),
        "recertification": "current",
        "inspect_by": a_year_after(TODAY, 2).isoformat(),
        "inspection": "current",
        "certifications": [
            {
                "id": made,
                "kind": "move-in",
                "effective_on": STARTED.isoformat(),
                "household_size": 3,
                "annual_income": "18000.00",
                "next_due_on": a_year_after(STARTED).isoformat(),
                "recorded_by": "Staff 1",
            }
        ],
        "inspections": [
            {
                "id": seen,
                "authority_id": assisted,
                "authority": HA,
                "inspected_on": TODAY.isoformat(),
                "result": "passed",
                "due_on": a_year_after(TODAY, 2).isoformat(),
                "recorded_by": "Staff 1",
            }
        ],
    }


def test_an_interim_certification_keeps_the_due_day_and_one_due_soon_says_so(
    client, lease, assisted
):
    refused(certify(client, lease, "interim"), 422, "follows a move-in or annual one")
    due = TODAY + timedelta(days=30)
    ok(certify(client, lease, "move-in", next_due_on=due))
    ok(certify(client, lease, "interim", effective_on=TODAY, annual_income="9000.00"))

    compliance = page_of(client, lease)["compliance"]

    assert [(c["kind"], c["next_due_on"]) for c in compliance["certifications"]] == [
        ("interim", due.isoformat()),
        ("move-in", due.isoformat()),
    ]
    assert (compliance["recertify_by"], compliance["recertification"]) == (
        due.isoformat(),
        "due within 120 days",
    )


def test_a_failed_inspection_gives_its_reinspection_day_and_falls_overdue_after_it(
    client, lease, assisted
):
    refused(inspect(client, lease, "failed"), 422, "the day to pass a reinspection by")
    by = STARTED + timedelta(days=14)
    ok(inspect(client, lease, "failed", inspected_on=STARTED, due_on=by))

    compliance = page_of(client, lease)["compliance"]

    assert (compliance["inspect_by"], compliance["inspection"]) == (
        by.isoformat(),
        "failed, reinspection overdue",
    )
    # Inspected for the authority assisting the lease that day, unless staff name another.
    assert compliance["inspections"][0]["authority_id"] == assisted


def test_a_move_in_inspection_before_the_assistance_starts_is_for_its_authority(
    client, staff, lease
):
    add_authority(client)
    starting = add_authority(client, "Demo County Housing")
    ok(terms(client, lease, starting, starts_on=NEXT))

    ok(inspect(client, lease))
    ok(certify(client, lease, "move-in", effective_on=TODAY))

    compliance = page_of(client, lease)["compliance"]
    assert compliance["inspections"][0]["authority"] == "Demo County Housing"
    assert (compliance["recertification"], compliance["inspection"]) == ("current", "current")


@pytest.mark.parametrize(
    ("change", "status", "said"),
    [
        ("certified before the lease", 409, "is outside the lease"),
        ("due before it took effect", 422, "is due after this one"),
        ("no household", 422, None),
        ("negative income", 422, None),
        ("unknown kind", 422, None),
        ("the same certification twice", 409, "on file already"),
        ("inspected tomorrow", 422, "once it happened"),
        ("inspection due before it", 422, "is due after this one"),
        ("the same inspection twice", 409, "on file already"),
        ("an authority of another company", 404, "no such housing authority"),
    ],
)
def test_certifications_and_inspections_refuse_bad_input(
    client, lease, assisted, change, status, said
):
    before = (STARTED - timedelta(days=1)).isoformat()
    if change == "the same certification twice":
        ok(certify(client, lease))
    if change == "the same inspection twice":
        ok(inspect(client, lease))
    answer = {
        "certified before the lease": lambda: certify(client, lease, effective_on=before),
        "due before it took effect": lambda: certify(client, lease, next_due_on=STARTED),
        "no household": lambda: certify(client, lease, household_size=0),
        "negative income": lambda: certify(client, lease, annual_income="-1.00"),
        "unknown kind": lambda: certify(client, lease, kind="biennial"),
        "the same certification twice": lambda: certify(client, lease),
        "inspected tomorrow": lambda: inspect(client, lease, inspected_on=TODAY + timedelta(1)),
        "inspection due before it": lambda: inspect(client, lease, due_on=TODAY),
        "the same inspection twice": lambda: inspect(client, lease),
        "an authority of another company": lambda: inspect(
            client, lease, housing_authority_id=uuid.uuid4()
        ),
    }[change]()

    refused(answer, status, said)
    if change.startswith("the same"):
        compliance = page_of(client, lease)["compliance"]
        assert len(compliance["certifications"]) + len(compliance["inspections"]) == 1


def test_records_are_of_a_lease_with_assistance_on_file_of_the_company(client, conn, staff, lease):
    authority = add_authority(client)
    elsewhere = lease_at(conn, make_pmc(conn))

    refused(certify(client, lease), 409, "no housing assistance on file")
    refused(inspect(client, lease), 422, "no housing authority assists the lease that day")
    refused(inspect(client, lease, housing_authority_id=authority), 409, "no housing assistance")
    refused(certify(client, elsewhere), 404)
    refused(inspect(client, elsewhere), 404)
    assert page_of(client, lease)["compliance"] == NONE_ON_FILE


# --- who may ------------------------------------------------------------------------------------


def test_housing_assistance_takes_staff(client, other_client, app_conn, pmc, lease):
    assert client.get("/api/assistance/authorities").status_code == 401
    assert post(client, "/api/assistance/authorities", name=HA).status_code == 401

    assert certify(client, lease).status_code == 401
    assert inspect(client, lease).status_code == 401

    join_as_owner(other_client, app_conn, pmc)
    assert other_client.get("/api/assistance/authorities").status_code == 403
    assert other_client.get(f"/api/assistance/leases/{lease}").status_code == 403
    assert post(other_client, "/api/assistance/authorities", name=HA).status_code == 403
    assert certify(other_client, lease).status_code == 403
    assert inspect(other_client, lease).status_code == 403


def test_a_custom_role_without_rent_sees_assistance_and_changes_none(
    client, other_client, conn, app_conn, pmc, staff, lease
):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Bills", "areas": ["bills"]}))["id"]
    answer = client.post(
        f"/api/custom-roles/{role}/staff",
        json={"staff_id": str(clerk.person_id), "granted": True},
    )
    assert answer.status_code == 204, answer.text

    refused(
        post(other_client, "/api/assistance/authorities", name=HA),
        403,
        "your roles don't let you change",
    )
    refused(certify(other_client, lease), 403, "your roles don't let you change")
    refused(inspect(other_client, lease), 403, "your roles don't let you change")
    assert ok(other_client.get(f"/api/assistance/leases/{lease}"), 200)["terms"] == []


@pytest.mark.parametrize(
    ("day", "later", "on"),
    [
        (date(2026, 3, 1), 1, date(2027, 3, 1)),
        (date(2028, 2, 29), 1, date(2029, 2, 28)),
        (date(2028, 2, 29), 4, date(2032, 2, 29)),
    ],
)
def test_a_year_on_is_the_same_day_or_february_28_for_a_29th(day, later, on):
    assert a_year_after(day, later) == on
