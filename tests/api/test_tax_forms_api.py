"""1099 forms from the API (src/pms/api/tax_forms.py, migration 20261022000162): the year's
recipients with what each form needs, taxpayer IDs kept sealed (staff see the last four digits),
each recipient's copy, and the filing list, which only an admin downloads. Every name, address and
number here is synthetic; taxpayer IDs are never-issued ones."""

import csv
import io
from datetime import UTC, datetime

import pytest
from helpers import approve_bill, enter_bill, make_pmc, pay_bill, set_aside_bill, transfer_batch
from people import join_as_owner, join_as_staff, join_as_tenant
from pypdf import PdfReader

from pms import sealed

NOWHERE = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def staff(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc, name="Clerk 1")


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json() if answer.content else None


def keep(client, about, about_id, tin, name, tin_type="SSN", exempt=False):
    body = {"about": about, "tin_type": tin_type, "tin": tin, "legal_name": name, "exempt": exempt}
    if about_id is not None:
        body["about_id"] = str(about_id)
    return client.post("/api/tax-forms/taxpayer-id", json=body)


def paid_vendor(conn, pmc, amount="2500.00"):
    when = datetime(2026, 4, 1, 12, tzinfo=UTC)
    conn.execute(
        "INSERT INTO trust_vendor_profiles (pmc_id, vendor_id, gets_1099) VALUES (%s, %s, true)",
        (pmc.pmc_id, pmc.vendor_id),
    )
    account = pmc.owners[0].account
    transfer_batch(conn, [(pmc.operating_cash, account, amount)], when)
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, account, "INV-1", when.date(), when.date(), amount
    )
    approve_bill(conn, pmc.pmc_id, bill, when.date(), "Owner A")
    set_aside_bill(conn, pmc.pmc_id, bill, when)
    pay_bill(conn, pmc.pmc_id, bill, when)


def ready(client, conn, pmc):
    """The company and its 1099 vendor with everything a 1099-NEC needs."""
    paid_vendor(conn, pmc)
    ok(keep(client, "company", None, "00-1234567", "Synthetic PMC LLC", "EIN"), 201)
    name = ok(client.get("/api/company"))["name"]
    address = {"address_line_1": "9 Office Row", "city": "Springfield", "state": "ZZ",
               "postal_code": "00000"}  # fmt: skip
    ok(client.post("/api/company", json={"name": name, "address": address}))
    ok(keep(client, "vendor", pmc.vendor_id, "000-12-1234", "Synthetic Repairs"), 201)
    ok(
        client.post(
            f"/api/vendors/{pmc.vendor_id}/contact",
            json={"address_line_1": "1 Synthetic Way", "city": "Springfield", "state": "ZZ",
                  "postal_code": "00000"},
        ),
        204,
    )  # fmt: skip


def test_staff_see_who_gets_a_1099_and_what_each_form_still_needs(client, conn, staff, pmc):
    paid_vendor(conn, pmc)
    page = ok(client.get("/api/tax-forms", params={"year": 2026}))
    assert (page["year"], page["threshold"], page["sealing"]) == (2026, "2000.00", True)
    assert page["payer"]["kind"] == "company"
    assert page["payer"]["missing"] == ["taxpayer ID", "mailing address"]
    (vendor,) = page["recipients"]
    assert {k: vendor[k] for k in ("kind", "id", "form", "amount", "missing")} == {
        "kind": "vendor",
        "id": str(pmc.vendor_id),
        "form": "1099-NEC",
        "amount": "2500.00",
        "missing": ["taxpayer ID", "mailing address"],
    }
    assert ok(client.get("/api/tax-forms", params={"year": 2025}))["recipients"] == []


def test_a_taxpayer_id_is_kept_sealed_and_shown_by_its_last_four_digits(client, conn, staff, pmc):
    kept = ok(keep(client, "vendor", pmc.vendor_id, "000-12-1234", "Synthetic Repairs"), 201)
    assert {
        k: kept[k] for k in ("tin_type", "tin_last4", "legal_name", "exempt", "recorded_by")
    } == {
        "tin_type": "SSN",
        "tin_last4": "1234",
        "legal_name": "Synthetic Repairs",
        "exempt": False,
        "recorded_by": "Clerk 1",
    }
    assert "000121234" not in str(kept)
    owner = pmc.owners[0].owner_id
    ok(keep(client, "owner", owner, "000 12 5678", "Owner One", exempt=True), 201)
    paid_vendor(conn, pmc)
    (vendor,) = ok(client.get("/api/tax-forms", params={"year": 2026}))["recipients"]
    assert vendor["taxpayer_id"]["tin_last4"] == "1234"
    assert vendor["missing"] == ["mailing address"]


@pytest.mark.parametrize(
    ("body", "status", "said"),
    [
        ({"about": "vendor", "tin": "000-12-123"}, 422, "nine digits"),
        ({"about": "vendor", "tin": "000-12-123X"}, 422, "nine digits"),
        ({"about": "company", "tin": "000-12-1234"}, 422, "under its EIN"),
        ({"about": "vendor", "exempt": True}, 422, "only an owner is marked exempt"),
        ({"about": "vendor", "about_id": NOWHERE}, 404, "no such vendor"),
        ({"about": "vendor", "about_id": None}, 404, "no such vendor"),
        ({"about": "company", "about_id": NOWHERE}, 422, "names no one"),
    ],
)
def test_a_bad_taxpayer_id_is_refused(client, conn, staff, pmc, body, status, said):
    full = {"about_id": str(pmc.vendor_id), "tin_type": "SSN", "tin": "000-12-1234",
            "legal_name": "Synthetic Repairs", **body}  # fmt: skip
    if full["about"] == "company" and "about_id" not in body:
        full.pop("about_id")
    if full.get("about_id") is None:
        full.pop("about_id", None)
    answer = client.post("/api/tax-forms/taxpayer-id", json=full)
    assert answer.status_code == status and said in answer.json()["detail"]


def test_bad_input_is_refused(client, staff, pmc):
    base = {"about": "vendor", "about_id": str(pmc.vendor_id), "tin_type": "SSN",
            "tin": "000-12-1234", "legal_name": "Synthetic Repairs"}  # fmt: skip
    for bad in ({"tin_type": "TIN"}, {"legal_name": ""}, {"legal_name": "x" * 81},
                {"about": "tenant"}, {"tin": "1" * 12}):  # fmt: skip
        assert client.post("/api/tax-forms/taxpayer-id", json={**base, **bad}).status_code == 422
    assert client.get("/api/tax-forms", params={"year": 1999}).status_code == 422
    assert client.get("/api/tax-forms").status_code == 422
    assert client.get("/api/tax-forms/2026/copies.pdf", params={"form": "w2"}).status_code == 422


def test_without_a_key_a_taxpayer_id_isnt_kept(client, conn, staff, pmc, monkeypatch):
    monkeypatch.delenv(sealed.ENV)
    answer = keep(client, "vendor", pmc.vendor_id, "000-12-1234", "Synthetic Repairs")
    assert answer.status_code == 409 and "PMS_FIELD_KEY" in answer.json()["detail"]
    assert ok(client.get("/api/tax-forms", params={"year": 2026}))["sealing"] is False


def test_staff_download_each_recipients_copy_and_an_admin_the_filing_list(
    client, other_client, conn, app_conn, staff, pmc
):
    ready(client, conn, pmc)
    copies = client.get("/api/tax-forms/2026/copies.pdf", params={"form": "nec"})
    assert copies.status_code == 200 and copies.headers["content-type"] == "application/pdf"
    assert 'filename="1099-NEC-2026-recipient-copies.pdf"' in copies.headers["content-disposition"]
    text = " ".join(PdfReader(io.BytesIO(copies.content)).pages[0].extract_text().split())
    assert "XXX-XX-1234" in text and "00-1234567" in text and "$2,500.00" in text
    listed = client.get("/api/tax-forms/2026/filing.csv", params={"form": "nec"})
    assert listed.status_code == 200
    (row,) = list(csv.DictReader(io.StringIO(listed.text)))
    assert (row["Recipient TIN"], row["Payer TIN"], row["Box 1 amount"]) == (
        "000-12-1234",
        "00-1234567",
        "2500.00",
    )
    # A staff member without admin reads the copies but not the list of whole numbers.
    join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 2", roles=[])
    assert (
        other_client.get("/api/tax-forms/2026/copies.pdf", params={"form": "nec"}).status_code
        == 200
    )
    refused = other_client.get("/api/tax-forms/2026/filing.csv", params={"form": "nec"})
    assert refused.status_code == 403 and "only an admin" in refused.json()["detail"]


def test_no_copies_until_the_company_has_its_taxpayer_id_and_address(client, conn, staff, pmc):
    paid_vendor(conn, pmc)
    for path in ("copies.pdf", "filing.csv"):
        answer = client.get(f"/api/tax-forms/2026/{path}", params={"form": "nec"})
        assert answer.status_code == 409
        assert answer.json()["detail"] == (
            "the company still needs its taxpayer ID and mailing address for its 1099s"
        )


@pytest.mark.parametrize("caller", ["nobody", "owner", "tenant"])
def test_only_staff_read_or_keep_1099s(client, conn, app_conn, pmc, caller):
    if caller == "owner":
        join_as_owner(client, app_conn, pmc)
    elif caller == "tenant":
        join_as_tenant(client, conn, app_conn, pmc)
    status = 401 if caller == "nobody" else 403
    assert client.get("/api/tax-forms", params={"year": 2026}).status_code == status
    assert (
        keep(client, "company", None, "00-1234567", "Synthetic PMC LLC", "EIN").status_code
        == status
    )
    for path in ("copies.pdf", "filing.csv"):
        assert (
            client.get(f"/api/tax-forms/2026/{path}", params={"form": "nec"}).status_code == status
        )


def test_a_custom_role_without_setup_may_not_keep_a_taxpayer_id(
    client, other_client, conn, app_conn, staff, pmc
):
    clerk = join_as_staff(other_client, conn, app_conn, pmc, name="Clerk 2", roles=[])
    role = ok(client.post("/api/custom-roles", json={"name": "Books", "areas": ["rent"]}), 201)
    ok(
        client.post(
            f"/api/custom-roles/{role['id']}/staff",
            json={"staff_id": str(clerk.person_id), "granted": True},
        ),
        204,
    )
    refused = keep(other_client, "vendor", pmc.vendor_id, "000-12-1234", "Synthetic Repairs")
    assert refused.status_code == 403


def test_another_companys_vendor_is_not_found(client, conn, staff):
    other = make_pmc(conn)
    answer = keep(client, "vendor", other.vendor_id, "000-12-1234", "Synthetic Repairs")
    assert answer.status_code == 404
