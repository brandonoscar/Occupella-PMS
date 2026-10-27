"""Owners' and vendors' tax IDs through the API (ADR 0010 phase 2, part F; src/pms/api/tax_ids.py,
migration 0155): sealed with the server's key before they are kept, the last four shown to every
staff member, the whole number only to an admin (Reveal, and the year's 1099 export), each time
recorded naming them. Synthetic tax IDs only: the never-issued forms 000-dd-dddd and 00-ddddddd
(tools/check_synthetic_data.py)."""

import base64
import csv
import io
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_agreement,
    add_tenant,
    add_unit,
    apply_payment,
    carry_over_vendor_payments,
    charge,
    enter_bill,
    make_pmc,
    open_lease,
    pay_bill,
    post,
    set_aside_bill,
)
from people import join_as_owner, join_as_staff

from pms import sealed

SSN = "000-12-3456"
EIN = "00-1234567"


@pytest.fixture(autouse=True)
def key(monkeypatch):
    data_key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("PMS_DATA_KEY", data_key)
    return base64.b64decode(data_key)


@pytest.fixture
def admin(client, conn, app_conn, pmc):
    return join_as_staff(client, conn, app_conn, pmc)


def ok(answer, status=200):
    assert answer.status_code == status, answer.text
    return answer.json()


def refused(answer, status, detail=None):
    assert answer.status_code == status, answer.text
    if detail is not None:
        assert answer.json()["detail"] == detail


def keep(client, who, payee, kind="SSN", number=SSN):
    return client.post(f"/api/tax-ids/{who}s/{payee}", json={"kind": kind, "number": number})


def rows_of(conn, pmc):
    return conn.execute(
        "SELECT id, kind, last4, tax_id_sealed, recorded_by FROM trust_tax_ids WHERE pmc_id = %s"
        " ORDER BY created_at",
        (pmc.pmc_id,),
    ).fetchall()


def reveals(conn, pmc):
    return conn.execute(
        "SELECT tax_id, purpose, recorded_by FROM trust_tax_id_reveals WHERE pmc_id = %s"
        " ORDER BY created_at",
        (pmc.pmc_id,),
    ).fetchall()


def test_staff_keep_a_tax_id_sealed_and_see_its_last_four(client, conn, pmc, admin, key):
    owner = pmc.owners[0].owner_id
    assert ok(client.get(f"/api/tax-ids/owners/{owner}")) == {"tax_id": None, "may_reveal": True}

    kept = ok(keep(client, "owner", owner), 201)
    assert kept["tax_id"]["kind"] == "SSN"
    assert kept["tax_id"]["last4"] == "3456"
    assert kept["tax_id"]["recorded_by"] == "Staff 1"
    assert "000123456" not in str(kept) and SSN not in str(kept)
    assert ok(client.get(f"/api/tax-ids/owners/{owner}")) == kept

    [(row_id, kind, last4, kept_sealed, by)] = rows_of(conn, pmc)
    assert (kind, last4, by) == ("SSN", "3456", admin.person_id)
    # Sealed: the digits aren't in the row, and they open only under the server's key and row.
    assert b"000123456" not in bytes(kept_sealed)
    assert sealed.unseal(bytes(kept_sealed), str(row_id), key) == "000123456"
    with pytest.raises(sealed.Unreadable):
        sealed.unseal(bytes(kept_sealed), str(uuid.uuid4()), key)
    # Never in the audit log in the clear.
    logged = conn.execute(
        "SELECT row_data::text FROM trust_audit_log WHERE pmc_id = %s"
        " AND table_name = 'trust_tax_ids'",
        (pmc.pmc_id,),
    ).fetchall()
    assert len(logged) == 1 and "000123456" not in logged[0][0] and SSN not in logged[0][0]


def test_the_same_again_writes_nothing_and_a_new_one_holds(client, conn, pmc, admin):
    vendor = pmc.vendor_id
    ok(keep(client, "vendor", vendor, "EIN", EIN), 201)
    # Written another way, the same nine digits: nothing new.
    ok(keep(client, "vendor", vendor, "EIN", " 001234567 "), 201)
    assert len(rows_of(conn, pmc)) == 1
    # The same last four but other digits, or another kind: a new row that holds.
    ok(keep(client, "vendor", vendor, "EIN", "00-7654567"), 201)
    now = ok(keep(client, "vendor", vendor, "SSN", "000-76-4567"), 201)
    assert len(rows_of(conn, pmc)) == 3
    assert (now["tax_id"]["kind"], now["tax_id"]["last4"]) == ("SSN", "4567")
    assert ok(client.get(f"/api/tax-ids/vendors/{vendor}"))["tax_id"]["kind"] == "SSN"


def test_an_admin_sees_one_whole_and_each_time_is_recorded(client, conn, pmc, admin):
    kept = ok(keep(client, "vendor", pmc.vendor_id, "EIN", EIN), 201)["tax_id"]
    whole = ok(client.get(f"/api/tax-ids/{kept['id']}/whole"))
    assert whole == {"kind": "EIN", "number": EIN}
    ok(client.get(f"/api/tax-ids/{kept['id']}/whole"))
    assert reveals(conn, pmc) == [
        (uuid.UUID(kept["id"]), "reveal", admin.person_id),
        (uuid.UUID(kept["id"]), "reveal", admin.person_id),
    ]
    ssn = ok(keep(client, "owner", pmc.owners[0].owner_id), 201)["tax_id"]
    assert ok(client.get(f"/api/tax-ids/{ssn['id']}/whole")) == {"kind": "SSN", "number": SSN}


def test_staff_who_are_not_admins_see_the_last_four_only(client, other_client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc)
    kept = ok(keep(client, "owner", pmc.owners[0].owner_id), 201)["tax_id"]
    clerk = join_as_staff(other_client, conn, app_conn, pmc, "Clerk 1", ("approve", "disburse"))
    seen = ok(other_client.get(f"/api/tax-ids/owners/{pmc.owners[0].owner_id}"))
    assert seen["may_reveal"] is False and seen["tax_id"]["last4"] == "3456"
    refused(
        other_client.get(f"/api/tax-ids/{kept['id']}/whole"),
        403,
        "only an admin sees a whole tax ID",
    )
    refused(
        other_client.get("/api/tax-ids/1099", params={"year": 2025}),
        403,
        "only an admin exports the year's 1099s",
    )
    assert reveals(conn, pmc) == []
    # A clerk still keeps one: setting it is the setup area's, not an admin's.
    ok(keep(other_client, "vendor", pmc.vendor_id, "EIN", EIN), 201)
    assert rows_of(conn, pmc)[-1][4] == clerk.person_id


def test_the_database_shows_a_whole_tax_id_only_to_an_admin(client, conn, app_conn, pmc):
    join_as_staff(client, conn, app_conn, pmc)
    kept = ok(keep(client, "owner", pmc.owners[0].owner_id), 201)["tax_id"]
    clerk = join_as_staff(client, conn, app_conn, pmc, "Clerk 1", ("approve",))
    with app_conn.transaction():
        app_conn.execute("SELECT set_config('trust.staff_id', %s, true)", (str(clerk.person_id),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="see a whole tax ID"):
            app_conn.execute(
                "INSERT INTO trust_tax_id_reveals (pmc_id, tax_id, purpose) VALUES (%s, %s, %s)",
                (pmc.pmc_id, kept["id"], "reveal"),
            )
    # Nobody named: refused too, as no one saw it.
    with pytest.raises(psycopg.errors.NotNullViolation):
        app_conn.execute(
            "INSERT INTO trust_tax_id_reveals (pmc_id, tax_id, purpose) VALUES (%s, %s, %s)",
            (pmc.pmc_id, kept["id"], "reveal"),
        )


def test_bad_tax_ids_are_refused_without_repeating_them(client, pmc, admin):
    owner = pmc.owners[0].owner_id
    for number in ("12345", "000-12-34567", "000-12-345a", "000 12 3456 7"):
        answer = keep(client, "owner", owner, "SSN", number)
        refused(
            answer, 422, "a tax ID is nine digits: XX-XXXXXXX for an EIN, XXX-XX-XXXX for an SSN"
        )
        assert number not in answer.text
    refused(keep(client, "owner", owner, "ITIN", SSN), 422)
    refused(client.post(f"/api/tax-ids/owners/{owner}", json={"kind": "SSN"}), 422)
    refused(keep(client, "owner", uuid.uuid4()), 404, "no such owner")
    refused(client.get(f"/api/tax-ids/vendors/{uuid.uuid4()}"), 404, "no such vendor")
    refused(client.get(f"/api/tax-ids/{uuid.uuid4()}/whole"), 404, "no such tax ID")
    refused(client.get("/api/tax-ids/1099", params={"year": 1800}), 422)


def test_another_companys_owner_and_tax_id_read_as_not_found(client, conn, app_conn, pmc):
    other = make_pmc(conn)
    join_as_staff(client, conn, app_conn, other)
    kept = ok(keep(client, "owner", other.owners[0].owner_id), 201)["tax_id"]
    join_as_staff(client, conn, app_conn, pmc)
    refused(keep(client, "owner", other.owners[0].owner_id), 404, "no such owner")
    refused(client.get(f"/api/tax-ids/owners/{other.owners[0].owner_id}"), 404)
    refused(client.get(f"/api/tax-ids/{kept['id']}/whole"), 404, "no such tax ID")


def test_only_staff_signed_in_reach_tax_ids(client, conn, app_conn, pmc):
    owner = pmc.owners[0].owner_id
    refused(client.get(f"/api/tax-ids/owners/{owner}"), 401)
    refused(keep(client, "owner", owner), 401)
    join_as_owner(client, app_conn, pmc)
    refused(client.get(f"/api/tax-ids/owners/{owner}"), 403)
    refused(keep(client, "owner", owner), 403)
    refused(client.get("/api/tax-ids/1099", params={"year": 2025}), 403)


def test_a_server_without_a_key_keeps_no_tax_ids(client, conn, pmc, admin, monkeypatch):
    kept = ok(keep(client, "owner", pmc.owners[0].owner_id), 201)["tax_id"]
    monkeypatch.delenv("PMS_DATA_KEY")
    no_key = "this server keeps no tax IDs: it has no PMS_DATA_KEY; ask whoever runs it to set one"
    refused(keep(client, "vendor", pmc.vendor_id, "EIN", EIN), 409, no_key)
    refused(client.get(f"/api/tax-ids/{kept['id']}/whole"), 409, no_key)
    # The last four still show.
    assert ok(client.get(f"/api/tax-ids/owners/{pmc.owners[0].owner_id}"))["tax_id"]["last4"]
    # Under another key it doesn't open, and nothing is recorded as seen.
    monkeypatch.setenv("PMS_DATA_KEY", base64.b64encode(os.urandom(32)).decode())
    refused(
        client.get(f"/api/tax-ids/{kept['id']}/whole"),
        409,
        "that tax ID doesn't open under this server's key",
    )
    assert reveals(conn, pmc) == []


# --- the year's 1099 export --------------------------------------------------------------------

# A past year, whose threshold is known: $600 (IRS, payments through 2025).
YEAR = 2025
IN_YEAR = datetime(YEAR, 6, 15, 12, tzinfo=UTC)


def vendor(conn, pmc, name, gets_1099=True):
    vendor_id = conn.execute(
        "INSERT INTO trust_vendors (pmc_id, display_name) VALUES (%s, %s) RETURNING id",
        (pmc.pmc_id, name),
    ).fetchone()[0]
    if gets_1099 is not None:
        mark(conn, pmc, vendor_id, gets_1099)
    return vendor_id


def mark(conn, pmc, vendor_id, gets_1099=True):
    conn.execute(
        "INSERT INTO trust_vendor_profiles (pmc_id, vendor_id, gets_1099) VALUES (%s, %s, %s)",
        (pmc.pmc_id, vendor_id, gets_1099),
    )


def year_of_payments(conn, pmc):
    """Vendor 1 paid a $500 bill in the year and $200 before the cutover; Vendor 2 $650 before it
    (no tax ID); Vendor 3 $100 (under the threshold); Vendor 4 $5,000 (not marked for a 1099).
    Owner 1's tenants paid $800 of rent; Owner 2's nothing."""
    first = pmc.owners[0]
    add_agreement(conn, pmc.pmc_id, first.account, date(YEAR, 1, 1), approval_limit="5000")
    post(conn, pmc.pmc_id, "fund", [(pmc.operating_cash, first.account, Decimal(900))], IN_YEAR)
    mark(conn, pmc, pmc.vendor_id)
    bill = enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        first.account,
        "INV-1",
        date(YEAR, 6, 1),
        date(YEAR, 6, 30),
        "500.00",
    )
    set_aside_bill(conn, pmc.pmc_id, bill, IN_YEAR)
    pay_bill(conn, pmc.pmc_id, bill, IN_YEAR + timedelta(days=1))
    carry_over_vendor_payments(conn, pmc.pmc_id, pmc.vendor_id, YEAR, date(YEAR, 3, 1), "200.00")
    for name, amount, gets in (
        ("Vendor 2", "650.00", True),
        ("Vendor 3", "100.00", True),
        ("Vendor 4", "5000.00", False),
    ):
        carry_over_vendor_payments(
            conn, pmc.pmc_id, vendor(conn, pmc, name, gets), YEAR, date(YEAR, 3, 1), amount
        )
    tenant = add_tenant(conn, pmc.pmc_id, first.property_id, "Tenant 2")
    unit = add_unit(conn, pmc.pmc_id, first.property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, date(YEAR, 1, 1), None, "400.00", [tenant])
    for month in (1, 2):
        due = charge(conn, pmc.pmc_id, lease, date(YEAR, month, 1), "400.00")
        [paid] = post(
            conn,
            pmc.pmc_id,
            f"rent-{month}",
            [(pmc.operating_cash, first.account, Decimal(400))],
            datetime(YEAR, month, 2, tzinfo=UTC),
        )
        apply_payment(conn, pmc.pmc_id, due, paid, Decimal(400))


def test_the_1099_export_gives_each_payee_their_tax_id_whole_and_records_it(
    client, conn, pmc, admin
):
    year_of_payments(conn, pmc)
    vendor_tin = ok(keep(client, "vendor", pmc.vendor_id, "EIN", EIN), 201)["tax_id"]["id"]
    owner_tin = ok(keep(client, "owner", pmc.owners[0].owner_id), 201)["tax_id"]["id"]
    ok(keep(client, "owner", pmc.owners[1].owner_id, "SSN", "000-99-8888"), 201)

    answer = client.get("/api/tax-ids/1099", params={"year": YEAR})
    assert answer.status_code == 200, answer.text
    assert answer.headers["content-disposition"] == f'attachment; filename="1099s-{YEAR}.csv"'
    assert answer.headers["cache-control"] == "no-store"
    lines = list(csv.reader(io.StringIO(answer.text)))
    assert lines == [
        ["payee_type", "payee", "form", "amount", "tin_type", "tin", "note"],
        ["vendor", "Vendor 1", "1099-NEC or 1099-MISC", "700.00", "EIN", EIN, ""],
        [
            "vendor",
            "Vendor 2",
            "1099-NEC or 1099-MISC",
            "650.00",
            "",
            "",
            "no tax ID on file: ask them for a W-9",
        ],
        ["owner", "Owner 1", "1099-MISC box 1 (rents)", "800.00", "SSN", SSN, ""],
    ]
    # Each tax ID written into it is recorded as seen by the admin; Owner 2's isn't in it.
    assert sorted(reveals(conn, pmc)) == sorted(
        [
            (uuid.UUID(vendor_tin), "1099 export", admin.person_id),
            (uuid.UUID(owner_tin), "1099 export", admin.person_id),
        ]
    )


def test_the_1099_export_counts_as_the_reports_do(client, conn, pmc, admin):
    """The export's amounts are the vendor payments report's 1099 vendors and the owner year-end
    package's 1099-MISC rents: one count, read from both sides."""
    year_of_payments(conn, pmc)
    exported = {
        (row["payee_type"], row["payee"]): row["amount"]
        for row in csv.DictReader(io.StringIO(client.get(f"/api/tax-ids/1099?year={YEAR}").text))
    }
    vendors = conn.execute(
        "SELECT vendor, paid FROM trust_report_vendor_payments(%s, %s) WHERE item = 'vendor'",
        (pmc.pmc_id, YEAR),
    ).fetchall()
    owners = {
        ("owner", name): conn.execute(
            "SELECT amount FROM trust_report_owner_year_end(%s, %s, %s)"
            " WHERE item = '1099-MISC rents (box 1)'",
            (pmc.pmc_id, owner.owner_id, YEAR),
        ).fetchone()[0]
        for owner, name in zip(pmc.owners, ("Owner 1", "Owner 2"), strict=True)
    }
    expected = {("vendor", name): f"{paid:.2f}" for name, paid in vendors}
    expected |= {who: f"{rents:.2f}" for who, rents in owners.items() if rents >= Decimal(600)}
    assert exported == expected


def test_a_1099_export_with_no_payees_is_its_header(client, pmc, admin):
    answer = client.get("/api/tax-ids/1099", params={"year": YEAR})
    assert answer.text == "payee_type,payee,form,amount,tin_type,tin,note\r\n"
