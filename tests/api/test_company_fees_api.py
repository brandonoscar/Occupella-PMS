"""The company's earned fees taken out of trust through the API (src/pms/api/payables.py):
what each trust bank account holds of them and since when, taking them out once, and Today
listing them while they wait."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from helpers import balance, make_pmc, post
from people import join_as_owner, join_as_staff

NOW = datetime.now(UTC)
TODAY = NOW.date()
DAY = timedelta(days=1)


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json()


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def take(client, bank, **body):
    body = {"request_id": uuid.uuid4(), "bank_account_id": bank, "on": TODAY} | body
    return client.post("/api/payables/company-fees", json=json.loads(json.dumps(body, default=str)))


def earn(conn, pmc, amount, days_ago):
    """A fee the company took from Owner 1's account some days ago (rent paid in first)."""
    when = NOW - days_ago * DAY
    owner = pmc.owners[0].account
    post(conn, pmc.pmc_id, f"rent-{uuid.uuid4()}", [(pmc.operating_cash, owner, amount)], when)
    post(conn, pmc.pmc_id, f"fee-{uuid.uuid4()}", [(owner, pmc.pmc_income, amount)], when)


def held(client, on=None):
    found = ok(client.get("/api/payables/company-fees", params={"on": on} if on else {}))
    return {f["bank_account"]: (f["held"], f["since"], f["days_held"], f["take_by"]) for f in found}


OPERATING = "Synthetic operating trust account"


def test_staff_take_the_companys_fees_out_oldest_first_and_once(client, conn, staff, pmc):
    earn(conn, pmc, Decimal("100.00"), 20)
    earn(conn, pmc, Decimal("50.00"), 5)
    # Taken out within 25 days of the oldest, they meet the strictest deadline found.
    assert held(client)[OPERATING] == ("150.00", str(TODAY - 20 * DAY), 20, str(TODAY + 5 * DAY))
    # As of a day before the second fee, only the first was there.
    assert held(client, str(TODAY - 10 * DAY))[OPERATING] == (
        "100.00",
        str(TODAY - 20 * DAY),
        10,
        str(TODAY + 5 * DAY),
    )

    request = uuid.uuid4()
    first = ok(take(client, pmc.operating_bank_id, request_id=request, amount="100.00"), 201)
    again = ok(take(client, pmc.operating_bank_id, request_id=request, amount="100.00"), 201)
    assert first == again == {"amount": "100.00"}
    # What came out was the oldest fee: what waits now was earned five days ago.
    assert held(client)[OPERATING] == ("50.00", str(TODAY - 5 * DAY), 5, str(TODAY + 20 * DAY))
    today = ok(client.get("/api/today"))
    assert [(f["bank_account"], f["held"]) for f in today["company_fees"]] == [(OPERATING, "50.00")]

    assert ok(take(client, pmc.operating_bank_id), 201) == {"amount": "50.00"}
    assert held(client)[OPERATING] == ("0.00", None, None, None)
    assert ok(client.get("/api/today"))["company_fees"] == []
    assert balance(conn, pmc.pmc_income) == 0


def test_taking_more_than_is_there_is_refused_in_words(client, conn, staff, pmc):
    refused(
        take(client, pmc.operating_bank_id),
        409,
        "none of the company's fees is in this trust bank account to take",
    )
    earn(conn, pmc, Decimal("150.00"), 3)
    refused(
        take(client, pmc.operating_bank_id, amount="150.01"),
        409,
        "only $150.00 of the company's fees is in this trust bank account;"
        " $150.01 is more than that",
    )
    request = uuid.uuid4()
    ok(take(client, pmc.operating_bank_id, request_id=request, amount="10.00"), 201)
    refused(
        take(client, pmc.operating_bank_id, request_id=request, amount="20.00"),
        409,
        "this request already took $10.00 of the company's fees: start a new one",
    )
    assert balance(conn, pmc.pmc_income) == Decimal("140.00")


def test_only_a_trust_bank_account_of_the_company_holding_its_fees(client, conn, staff, pmc):
    other = make_pmc(conn)
    for bank in (pmc.deposit_bank_id, other.operating_bank_id, uuid.uuid4()):
        refused(
            take(client, bank),
            404,
            "no trust bank account of the company holds its fees by that id",
        )
    # Another PMC's fees are not listed.
    earn(conn, other, Decimal("5.00"), 1)
    assert list(held(client)) == [OPERATING]


def test_taking_the_companys_fees_takes_the_disburse_role(
    client, other_client, conn, app_conn, staff, pmc
):
    earn(conn, pmc, Decimal("150.00"), 3)
    join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 1", roles=[])

    refused(
        take(other_client, pmc.operating_bank_id),
        403,
        "Clerk 1 may not pay out money (the disburse role)",
    )
    assert balance(conn, pmc.pmc_income) == Decimal("150.00")


@pytest.mark.parametrize("caller", ["nobody", "owner"])
def test_only_staff_see_or_take_the_companys_fees(client, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    status = 401 if caller == "nobody" else 403

    refused(client.get("/api/payables/company-fees"), status)
    refused(take(client, pmc.operating_bank_id), status)


@pytest.mark.parametrize(
    "change",
    [
        {"amount": "0"},
        {"amount": "-1.00"},
        {"amount": "1.001"},
        {"request_id": "x"},
        {"bank_account_id": "x"},
        {"on": "soon"},
        {"on": str(TODAY + DAY)},
    ],
)
def test_bad_input_is_refused(client, conn, staff, pmc, change):
    earn(conn, pmc, Decimal("150.00"), 3)
    refused(take(client, pmc.operating_bank_id, **change), 422)
    refused(client.get("/api/payables/company-fees", params={"on": "soon"}), 422)
    assert balance(conn, pmc.pmc_income) == Decimal("150.00")
