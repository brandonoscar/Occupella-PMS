"""Tax credit (LIHTC) compliance through the API (src/pms/api/tax_credit.py, migration 0123; the
founder's review q143): a property's income limits and a unit's designation, each from a day, a
household's certification recorded as an assisted lease's is, and where the lease stands. Every
name and figure is synthetic."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from helpers import add_tenant, add_unit, make_pmc, open_lease
from people import join_as_owner, join_as_staff, join_as_tenant

TODAY = datetime.now(UTC).date()
START = TODAY - timedelta(days=100)
LIMITS = ["30000", "34300", "38600", "42850", "46300", "49750", "53150", "56600"]


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Manager 1")


@pytest.fixture
def unit(conn, pmc):
    return add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")


@pytest.fixture
def lease(conn, pmc, unit):
    tenant = add_tenant(conn, pmc.pmc_id, pmc.owners[0].property_id, "Tenant 9")
    return open_lease(conn, pmc.pmc_id, unit, START, None, "900.00", [tenant])


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert detail in str(answer.json()["detail"]), answer.text


def set_limits(client, pmc, limits=LIMITS, starts_on=START):
    return client.post(
        "/api/tax-credit/limits",
        json={
            "property_id": str(pmc.owners[0].property_id),
            "starts_on": str(starts_on),
            "limits": limits,
        },
    )


def designate(client, unit, percent=60, bedrooms=1, allowance="50.00", starts_on=START):
    return client.post(
        "/api/tax-credit/units",
        json={
            "unit_id": str(unit),
            "starts_on": str(starts_on),
            "income_percent": percent,
            "bedrooms": bedrooms,
            "utility_allowance": allowance,
        },
    )


def certify(client, lease, income, size=2):
    return client.post(
        f"/api/assistance/leases/{lease}/certifications",
        json={
            "kind": "move-in",
            "effective_on": str(START),
            "household_size": size,
            "annual_income": income,
        },
    )


def test_staff_set_the_limits_and_a_units_designation_and_read_them_back(client, staff, pmc, unit):
    ok(set_limits(client, pmc), 201)
    assert ok(client.get(f"/api/tax-credit/properties/{pmc.owners[0].property_id}")) == [
        {"starts_on": str(START), "limits": LIMITS}
    ]
    ok(designate(client, unit), 201)
    shown = ok(client.get(f"/api/tax-credit/units/{unit}"))
    in_force = {
        "starts_on": str(START),
        "income_percent": 60,
        "bedrooms": 1,
        "utility_allowance": "50.00",
    }
    # One bedroom: one and a half people at 60%, (36000 + 41160) / 2 x 30% / 12 = 964.50.
    assert shown == {"in_force": in_force, "max_gross_rent": "964", "designations": [in_force]}
    # From tomorrow it is not a tax credit unit: today it still is.
    ok(designate(client, unit, None, None, None, TODAY + timedelta(days=1)), 201)
    later = ok(client.get(f"/api/tax-credit/units/{unit}"))
    assert later["in_force"] == in_force
    assert later["designations"][0]["income_percent"] is None


def test_a_lease_at_a_tax_credit_unit_reads_its_tests_and_takes_its_certification(
    client, staff, pmc, unit, lease
):
    # Not a tax credit unit: no certification, nothing to show.
    refused(certify(client, lease, "30000.00"), 409, "not a tax credit unit")
    assert ok(client.get(f"/api/tax-credit/leases/{lease}")) is None
    ok(set_limits(client, pmc), 201)
    ok(designate(client, unit), 201)
    assert ok(client.get(f"/api/tax-credit/leases/{lease}")) == {
        "income_percent": 60,
        "bedrooms": 1,
        "certification": "none on file",
        "income_test": "no certification on file",
        "max_gross_rent": "964",
        "gross_rent": "950.00",
        "rent_test": "within the limit",
    }
    # Two people over 41160 (34300 x 60/50) at move-in.
    ok(certify(client, lease, "45000.00"), 201)
    shown = ok(client.get(f"/api/tax-credit/leases/{lease}"))
    assert (shown["certification"], shown["income_test"]) == (
        "current",
        "over the limit at move-in",
    )


def test_what_is_refused(client, conn, staff, pmc, unit):
    refused(set_limits(client, pmc, LIMITS[:7]), 422)
    refused(set_limits(client, pmc, ["0", *LIMITS[1:]]), 422)
    ok(set_limits(client, pmc), 201)
    refused(set_limits(client, pmc), 409, "change on that day already")
    refused(designate(client, unit, 65), 422)
    refused(designate(client, unit, 60, 7), 422)
    refused(designate(client, unit, 60, None, "0"), 422, "names its percent of median")
    refused(designate(client, unit, None, 1, None), 422, "ending one names none")
    ok(designate(client, unit), 201)
    refused(designate(client, unit), 409, "changes on that day already")
    other = make_pmc(conn)
    theirs = add_unit(conn, other.pmc_id, other.owners[0].property_id, "Theirs")
    refused(designate(client, theirs), 404)
    refused(set_limits(client, other), 404)
    refused(client.get(f"/api/tax-credit/units/{theirs}"), 404)
    refused(client.get(f"/api/tax-credit/properties/{other.owners[0].property_id}"), 404)
    refused(client.get(f"/api/tax-credit/leases/{uuid.uuid4()}"), 404)


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_keep_or_read_it(client, conn, app_conn, pmc, unit, lease, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    assert set_limits(client, pmc).status_code == status
    assert designate(client, unit).status_code == status
    assert client.get(f"/api/tax-credit/units/{unit}").status_code == status
    assert client.get(f"/api/tax-credit/leases/{lease}").status_code == status
    assert (
        client.get(f"/api/tax-credit/properties/{pmc.owners[0].property_id}").status_code == status
    )
