"""Bank files through the API (src/pms/api/ach.py, migration 20261022000156): a trust bank
account's ACH settings, the EFT payouts waiting for a file, a file made of those picked, its
download as the bank takes it, and a void freeing its payouts. Routing numbers start 00, which no
bank holds; account numbers are made up."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from helpers import (
    ROLES,
    approve_bill,
    contribute,
    draw_owner,
    enter_bill,
    make_pmc,
    pay_bill,
    set_aside_bill,
)
from people import join_as_owner, join_as_staff, join_as_tenant

from pms import sealed

SETTINGS = {
    "bank_routing": "001000009",
    "bank_name": "Demo Trust Bank",
    "origin": "1000000001",
    "company_name": "Demo Property Co",
    "company_id": "1000000001",
}


def account(name, number):
    return {
        "holder_name": name,
        "routing_number": "002000008",
        "account_type": "checking",
        "account_number": number,
        "account_number_again": number,
    }


def weekday():
    day = datetime.now(UTC).date() + timedelta(days=1)
    while day.isoweekday() > 5:
        day += timedelta(days=1)
    return day.isoformat()


def method(conn, pmc, transfer, how="EFT"):
    conn.execute(
        "INSERT INTO trust_payout_methods (pmc_id, transfer_id, method) VALUES (%s, %s, %s)",
        (pmc.pmc_id, transfer, how),
    )
    return transfer


def a_draw(conn, pmc, amount="40.00", how="EFT"):
    owner = pmc.owners[0].account
    contribute(conn, pmc.pmc_id, owner, f"in/{uuid.uuid4()}", "500.00")
    key = f"draw/{uuid.uuid4()}"
    draw_owner(conn, pmc.pmc_id, owner, key, amount, datetime.now(UTC))
    transfer = conn.execute(
        "SELECT transfer_id FROM trust_owner_draws WHERE pmc_id = %s AND request_key = %s",
        (pmc.pmc_id, key),
    ).fetchone()[0]
    return method(conn, pmc, transfer, how)


def a_bill_paid(conn, pmc, reference="ROOF-17", amount="1000.00"):
    today = datetime.now(UTC).date()
    contribute(conn, pmc.pmc_id, pmc.owners[0].account, f"in/{uuid.uuid4()}", amount)
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, reference, today, today, amount
    )
    approve_bill(conn, pmc.pmc_id, bill, today, "Owner, by phone")
    set_aside_bill(conn, pmc.pmc_id, bill, datetime.now(UTC))
    return method(conn, pmc, pay_bill(conn, pmc.pmc_id, bill, datetime.now(UTC)))


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


@pytest.fixture
def ready(client, conn, staff, pmc):
    """Settings, both payees' bank accounts, an EFT draw and an EFT bill paid."""
    assert (
        client.post(f"/api/bank-files/{pmc.operating_bank_id}/settings", json=SETTINGS).status_code
        == 201
    )
    client.post(
        f"/api/owners/{pmc.owners[0].owner_id}/bank-account",
        json=account("Demo Owner A", "000123456789"),
    )
    client.post(
        f"/api/vendors/{pmc.vendor_id}/bank-account", json=account("Demo Roofing Co", "0000555501")
    )
    return a_draw(conn, pmc, "125.50"), a_bill_paid(conn, pmc)  # fmt: skip


def page(client, pmc):
    return client.get(f"/api/bank-files/{pmc.operating_bank_id}")


def test_staff_make_a_file_of_eft_payouts_and_download_it_as_the_bank_takes_it(
    client, conn, pmc, ready
):
    draw, bill = ready
    a_draw(conn, pmc, "9.00", how="check")  # by check: never in a bank file

    shown = page(client, pmc).json()
    assert shown["settings"] | {"recorded_at": None} == SETTINGS | {
        "recorded_by": "Staff 1",
        "recorded_at": None,
    }
    assert [(p["transfer_id"], p["payee"], p["what"], p["amount"], p["bank_account"])
            for p in shown["waiting"]] == [
        (draw, "owner", f"Owner draw, {owner_property(conn, pmc)}", "125.50", "Checking ••••6789"),
        (bill, "vendor", "Bill ROOF-17", "1000.00", "Checking ••••5501"),
    ]  # fmt: skip
    assert shown["files"] == []

    made = client.post(
        f"/api/bank-files/{pmc.operating_bank_id}",
        json={"effective_on": weekday(), "transfer_ids": [bill, draw]},
    )

    assert made.status_code == 201, made.text
    file = made.json()
    assert (file["modifier"], file["entries"], file["total"], file["made_by"]) == (
        "A",
        2,
        "1125.50",
        "Staff 1",
    )
    # The owner's draw first (its PPD batch), then the vendor's bill (CCD).
    assert [(i["what"], i["amount"], i["bank_account"]) for i in file["items"]] == [
        (f"Owner draw, {owner_property(conn, pmc)}", "125.50", "Checking ••••6789"),
        ("Bill ROOF-17", "1000.00", "Checking ••••5501"),
    ]
    assert page(client, pmc).json()["waiting"] == []
    assert "000123456789" not in made.text

    got = client.get(f"/api/bank-files/files/{file['id']}.ach")
    assert got.status_code == 200
    assert got.headers["content-type"].startswith("text/plain")
    assert got.headers["content-disposition"].endswith('-A.ach"')
    lines = got.text.split("\r\n")[:-1]
    assert len(lines) == 10 and all(len(line) == 94 for line in lines)
    entries = [line for line in lines if line.startswith("6")]
    assert [e[3:12] + "|" + e[12:29].rstrip() + "|" + e[29:39] for e in entries] == [
        "002000008|000123456789|0000012550",
        "002000008|0000555501|0000100000",
    ]
    assert lines[0].startswith("101 001000009")
    assert lines[7][43:55] == "000000112550"  # the total of credits
    # The same file each time it is downloaded.
    assert client.get(f"/api/bank-files/files/{file['id']}.ach").text == got.text


def owner_property(conn, pmc):
    return conn.execute(
        "SELECT display_name FROM trust_properties WHERE id = %s", (pmc.owners[0].property_id,)
    ).fetchone()[0]


def test_a_void_frees_the_files_payouts_for_another(client, conn, pmc, ready):
    draw, bill = ready
    path = f"/api/bank-files/{pmc.operating_bank_id}"
    first = client.post(path, json={"effective_on": weekday(), "transfer_ids": [draw]}).json()

    again = client.post(path, json={"effective_on": weekday(), "transfer_ids": [draw]})
    assert again.status_code == 409
    assert again.json()["detail"] == f"{draw} is not an EFT payout waiting for a bank file"

    voided = client.post(f"/api/bank-files/files/{first['id']}/void", json={"reason": "Wrong day"})
    twice = client.post(f"/api/bank-files/files/{first['id']}/void", json={"reason": "Again"})
    second = client.post(path, json={"effective_on": weekday(), "transfer_ids": [draw, bill]})

    assert voided.status_code == 204
    assert twice.status_code == 409
    assert second.status_code == 201
    assert second.json()["modifier"] == "B"
    files = page(client, pmc).json()["files"]
    assert [(f["modifier"], f["voided"] and f["voided"]["reason"]) for f in files] == [
        ("B", None),
        ("A", "Wrong day"),
    ]


@pytest.mark.parametrize(
    ("body", "status", "detail"),
    [
        ({"effective_on": "2020-01-06", "transfer_ids": ["draw"]}, 409,
         "a bank file's effective day is not before the day it is made"),
        ({"effective_on": "weekend", "transfer_ids": ["draw"]}, 409,
         "a bank file's effective day is a weekday, when banks settle"),
        ({"effective_on": "next", "transfer_ids": ["draw", "draw"]}, 422, "pick each payout once"),
        ({"effective_on": "next", "transfer_ids": []}, 422, None),
        ({"effective_on": "next", "transfer_ids": ["pglt_none"]}, 409,
         "pglt_none is not an EFT payout waiting for a bank file"),
        ({"effective_on": "someday", "transfer_ids": ["draw"]}, 422, None),
    ],
)  # fmt: skip
def test_bad_files_are_refused_and_make_nothing(client, conn, pmc, ready, body, status, detail):
    draw, _ = ready
    saturday = datetime.now(UTC).date() + timedelta(days=1)
    while saturday.isoweekday() != 6:
        saturday += timedelta(days=1)
    effective = {"weekend": saturday.isoformat(), "next": weekday()}.get(
        body["effective_on"], body["effective_on"]
    )
    ids = [draw if t == "draw" else t for t in body["transfer_ids"]]

    answer = client.post(
        f"/api/bank-files/{pmc.operating_bank_id}",
        json={"effective_on": effective, "transfer_ids": ids},
    )

    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail
    assert page(client, pmc).json()["files"] == []


def test_a_file_needs_settings_a_payees_bank_account_and_the_servers_key(
    client, conn, staff, pmc, monkeypatch
):
    path = f"/api/bank-files/{pmc.operating_bank_id}"
    draw = a_draw(conn, pmc)
    body = {"effective_on": weekday(), "transfer_ids": [draw]}

    none = client.post(path, json=body)
    client.post(f"{path}/settings", json=SETTINGS)
    no_account = client.post(path, json=body)
    client.post(
        f"/api/owners/{pmc.owners[0].owner_id}/bank-account",
        json=account("Demo Owner A", "000123456789"),
    )
    made = client.post(path, json=body).json()
    monkeypatch.delenv(sealed.ENV)
    no_key = client.get(f"/api/bank-files/files/{made['id']}.ach")
    monkeypatch.setenv(sealed.ENV, sealed.new_key())
    other_key = client.get(f"/api/bank-files/files/{made['id']}.ach")

    assert (none.status_code, none.json()["detail"]) == (
        409,
        "add this account's ACH settings from its bank first",
    )
    assert no_account.status_code == 409
    assert no_account.json()["detail"].endswith(
        "has no bank account on file: add it on their page first"
    )
    assert no_key.status_code == 503
    assert no_key.json()["detail"] == (
        "this server has no key to open bank account numbers with (PMS_FIELD_KEY)"
    )
    assert other_key.status_code == 503
    assert "is it the key they were kept with?" in other_key.json()["detail"]


@pytest.mark.parametrize(
    "changes",
    [
        {"bank_routing": "001000008"},
        {"bank_routing": "00100000"},
        {"bank_name": "x" * 24},
        {"bank_name": "Bank; DROP"},
        {"origin": "12345678"},
        {"company_name": "Demo Property Company"},
        {"company_id": "12345678901"},
    ],
)
def test_bad_settings_are_refused(client, conn, staff, pmc, changes):
    answer = client.post(
        f"/api/bank-files/{pmc.operating_bank_id}/settings", json=SETTINGS | changes
    )

    assert answer.status_code == 422, answer.text
    assert page(client, pmc).json()["settings"] is None


def test_another_companys_account_or_file_is_not_found(client, conn, pmc, ready):
    other = make_pmc(conn)
    for path in (f"/api/bank-files/{other.operating_bank_id}", f"/api/bank-files/{uuid.uuid4()}"):
        assert client.get(path).status_code == 404
        assert client.post(f"{path}/settings", json=SETTINGS).status_code == 404
    missing = uuid.uuid4()
    assert client.get(f"/api/bank-files/files/{missing}.ach").status_code == 404
    voided = client.post(f"/api/bank-files/files/{missing}/void", json={"reason": "x"})
    assert voided.status_code == 404


def test_staff_who_may_not_disburse_see_the_page_but_make_download_and_void_nothing(
    client, other_client, conn, app_conn, pmc, ready
):
    draw, _ = ready
    path = f"/api/bank-files/{pmc.operating_bank_id}"
    made = client.post(path, json={"effective_on": weekday(), "transfer_ids": [draw]}).json()
    join_as_staff(
        other_client, conn, app_conn, pmc, name="Clerk", roles=[r for r in ROLES if r != "disburse"]
    )

    assert other_client.get(path).status_code == 200
    for answer in (
        other_client.post(f"{path}/settings", json=SETTINGS),
        other_client.post(path, json={"effective_on": weekday(), "transfer_ids": [ready[1]]}),
        other_client.get(f"/api/bank-files/files/{made['id']}.ach"),
        other_client.post(f"/api/bank-files/files/{made['id']}/void", json={"reason": "x"}),
    ):
        assert answer.status_code == 403
        assert "disburse" in answer.json()["detail"]


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_reach_bank_files(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    path = f"/api/bank-files/{pmc.operating_bank_id}"
    file_id = uuid.uuid4()
    for answer in (
        client.get(path),
        client.post(f"{path}/settings", json=SETTINGS),
        client.post(path, json={"effective_on": weekday(), "transfer_ids": ["x"]}),
        client.get(f"/api/bank-files/files/{file_id}.ach"),
        client.post(f"/api/bank-files/files/{file_id}/void", json={"reason": "x"}),
    ):
        assert answer.status_code == status
