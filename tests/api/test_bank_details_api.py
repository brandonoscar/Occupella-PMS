"""A vendor's or an owner's bank account through the API (src/pms/api/bank_details.py,
migration 20261022000155): staff keep it on the payee's page, see only its last four digits, and
take it away; the whole number is sealed before it reaches the database. Routing numbers start
00, which no bank holds."""

import json
import uuid

import pytest
from helpers import ROLES, make_pmc
from people import join_as_owner, join_as_staff, join_as_tenant

from pms import sealed
from pms.api import bank_details

ROUTING = "001000009"
NUMBER = "000123456789"


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def body(**changes):
    return {
        "holder_name": "Demo Roofing Co",
        "routing_number": ROUTING,
        "account_type": "checking",
        "account_number": NUMBER,
        "account_number_again": NUMBER,
    } | changes


def paths(pmc):
    return {
        "vendor": f"/api/vendors/{pmc.vendor_id}/bank-account",
        "owner": f"/api/owners/{pmc.owners[0].owner_id}/bank-account",
    }


@pytest.mark.parametrize("payee", ["vendor", "owner"])
def test_staff_keep_a_payees_bank_account_and_see_its_last_four_digits(
    client, conn, staff, pmc, payee
):
    path = paths(pmc)[payee]
    assert client.get(path).json() is None

    kept = client.post(path, json=body())
    shown = client.get(path)

    assert kept.status_code == 201, kept.text
    assert shown.json() == kept.json()
    assert shown.json() | {"recorded_at": None} == {
        "holder_name": "Demo Roofing Co",
        "routing_number": ROUTING,
        "account_type": "checking",
        "account_last4": "6789",
        "recorded_by": "Staff 1",
        "recorded_at": None,
    }
    # The whole number is in no answer and nowhere in the database but sealed.
    assert NUMBER not in kept.text and NUMBER not in shown.text
    (blob,) = conn.execute(
        "SELECT account_number_sealed FROM trust_payee_bank_accounts WHERE pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchone()
    assert NUMBER.encode() not in bytes(blob)
    person = pmc.vendor_id if payee == "vendor" else pmc.owners[0].owner_id
    assert bank_details.account_number(conn, pmc.pmc_id, payee, person) == NUMBER
    audit = conn.execute(
        "SELECT row_data::text FROM trust_audit_log WHERE table_name = 'trust_payee_bank_accounts'"
        " AND pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchall()
    assert len(audit) == 1 and NUMBER not in audit[0][0]


def test_the_latest_holds_a_removal_takes_it_away_and_a_copy_on_another_payee_doesnt_open(
    client, conn, staff, pmc
):
    vendor, owner = paths(pmc)["vendor"], paths(pmc)["owner"]
    client.post(vendor, json=body())
    changed = client.post(vendor, json=body(account_type="savings", account_number="0000555501",
                                            account_number_again="0000555501"))  # fmt: skip

    assert changed.json()["account_type"] == "savings"
    assert changed.json()["account_last4"] == "5501"
    assert client.get(vendor).json()["account_last4"] == "5501"

    removed = client.post(f"{vendor}/remove", json={})
    again = client.post(f"{vendor}/remove", json={})

    assert removed.status_code == 204
    assert client.get(vendor).json() is None
    assert again.status_code == 409
    assert again.json()["detail"] == "this vendor has no bank account on file"
    # A sealed number is bound to its payee: copied onto the owner's row, it doesn't open.
    client.post(owner, json=body())
    (blob,) = conn.execute(
        "SELECT account_number_sealed FROM trust_payee_bank_accounts"
        " WHERE pmc_id = %s AND vendor_id IS NOT NULL AND NOT removed ORDER BY created_at LIMIT 1",
        (pmc.pmc_id,),
    ).fetchone()
    with pytest.raises(sealed.CannotOpen):
        sealed.open_sealed(
            bytes(blob), bank_details.context(pmc.pmc_id, "owner", pmc.owners[0].owner_id)
        )


@pytest.mark.parametrize(
    ("changes", "detail"),
    [
        ({"account_number_again": "000123456780"}, "its second typing differ"),
        ({"routing_number": "001000008"}, "check digit is wrong: look at it again"),
        ({"routing_number": "00100000"}, None),
        ({"account_number": "12a4", "account_number_again": "12a4"}, None),
        ({"account_number": "123", "account_number_again": "123"}, None),
        ({"holder_name": "x" * 23}, None),
        ({"account_type": "brokerage"}, None),
    ],
)  # fmt: skip
def test_bad_input_is_refused_and_keeps_nothing(client, conn, staff, pmc, changes, detail):
    answer = client.post(paths(pmc)["vendor"], json=body(**changes))

    assert answer.status_code == 422, answer.text
    if detail is not None:
        shown = answer.json()["detail"]
        assert detail in (shown if isinstance(shown, str) else json.dumps(shown))
    assert client.get(paths(pmc)["vendor"]).json() is None


def test_a_payee_of_another_company_or_none_is_not_found(client, conn, staff, pmc):
    other = make_pmc(conn)
    for path in (
        f"/api/vendors/{other.vendor_id}/bank-account",
        f"/api/owners/{other.owners[0].owner_id}/bank-account",
        f"/api/vendors/{uuid.uuid4()}/bank-account",
    ):
        assert client.get(path).status_code == 404, path
        assert client.post(path, json=body()).status_code == 404, path


def test_without_the_servers_key_nothing_is_kept(client, conn, staff, pmc, monkeypatch):
    monkeypatch.delenv(sealed.ENV)

    answer = client.post(paths(pmc)["vendor"], json=body())

    assert answer.status_code == 503
    assert answer.json()["detail"] == (
        "this server has no key to keep bank account numbers with (PMS_FIELD_KEY)"
    )
    assert client.get(paths(pmc)["vendor"]).json() is None


def test_only_staff_who_may_disburse_change_where_money_goes(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc, roles=[r for r in ROLES if r != "disburse"])

    answer = client.post(paths(pmc)["vendor"], json=body())

    assert answer.status_code == 403
    assert "disburse" in answer.json()["detail"]
    # Reading it is open to every staff member.
    assert client.get(paths(pmc)["vendor"]).status_code == 200


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_see_or_change_a_bank_account(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    for path in paths(pmc).values():
        assert client.get(path).status_code == status, path
        assert client.post(path, json=body()).status_code == status, path
        assert client.post(f"{path}/remove", json={}).status_code == status, path
