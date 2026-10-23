"""1099 forms (migration 20261022000162, src/pms/tax_forms.py): taxpayer IDs as their W-9s give
them, the number sealed (append-only and audited, the latest holding; the company's own an EIN, an
owner alone marked exempt); who gets a 1099-NEC (vendors marked for one, paid the year's threshold)
and a 1099-MISC (owners' rents at the threshold, unless exempt), what each form still needs, each
recipient's copy (Copy B) with their number cut to its last four digits, and the filing list with
whole numbers. Every name, address and number here is synthetic; taxpayer IDs are never-issued
ones (SSNs starting 000, EINs starting 00)."""

import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_staff,
    add_tenant,
    add_unit,
    apply_payment,
    approve_bill,
    charge,
    enter_bill,
    make_pmc,
    open_lease,
    pay_bill,
    set_aside_bill,
    transfer_batch,
)
from pypdf import PdfReader

from pms import sealed, tax_forms

# Never issued: an EIN's prefix 00, an SSN's area 000.
COMPANY_EIN = "001234567"
VENDOR_SSN = "000121234"
OWNER_SSN = "000125678"


def at(month, day, year=2026):
    return datetime(year, month, day, 12, tzinfo=UTC)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def taxpayer(conn, pmc, kind, person, tin, name, tin_type="SSN", exempt=False):
    tax_forms.record_taxpayer_id(conn, pmc.pmc_id, kind, person, tin_type, tin, name, exempt)


def address(conn, pmc, column, person, line="1 Synthetic Way"):
    conn.execute(
        f"INSERT INTO trust_contacts (pmc_id, {column}, address_line_1, city, state, postal_code)"  # noqa: S608
        " VALUES (%s, %s, %s, 'Springfield', 'ZZ', '00000')",
        (pmc.pmc_id, person, line),
    )


def company_ready(conn, pmc):
    taxpayer(conn, pmc, "company", None, COMPANY_EIN, "Synthetic PMC LLC", "EIN")
    conn.execute(
        "INSERT INTO trust_company_profiles (pmc_id, address_line_1, city, state, postal_code)"
        " VALUES (%s, '9 Office Row', 'Springfield', 'ZZ', '00000')",
        (pmc.pmc_id,),
    )


def vendor_paid(conn, pmc, amount, when, marked=True, reference="INV-1"):
    if marked is not None:
        conn.execute(
            "INSERT INTO trust_vendor_profiles (pmc_id, vendor_id, gets_1099) VALUES (%s, %s, %s)",
            (pmc.pmc_id, pmc.vendor_id, marked),
        )
    account = pmc.owners[0].account
    transfer_batch(conn, [(pmc.operating_cash, account, amount)], when)
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, account, reference, when.date(), when.date(), amount
    )
    approve_bill(conn, pmc.pmc_id, bill, when.date(), "Owner A")
    set_aside_bill(conn, pmc.pmc_id, bill, when)
    pay_bill(conn, pmc.pmc_id, bill, when)


def rents_collected(conn, pmc, amount, when, owner=0):
    place = pmc.owners[owner].property_id
    tenant = add_tenant(conn, pmc.pmc_id, place, f"Tenant {when:%m%d}{owner}")
    unit = add_unit(conn, pmc.pmc_id, place, f"Unit {when:%m%d}")
    lease = open_lease(conn, pmc.pmc_id, unit, date(2025, 1, 1), None, amount, [tenant])
    charged = charge(conn, pmc.pmc_id, lease, when.date(), amount)
    (moved,) = transfer_batch(conn, [(pmc.operating_cash, pmc.owners[owner].account, amount)], when)
    apply_payment(conn, pmc.pmc_id, charged, moved, amount)


# --- the taxpayer ID ------------------------------------------------------------------------


def test_a_taxpayer_id_is_sealed_with_its_last_four_digits_and_the_latest_holds(conn, pmc):
    staff = add_staff(conn, pmc.pmc_id)
    conn.execute("SELECT set_config('trust.staff_id', %s, false)", (str(staff),))
    try:
        taxpayer(conn, pmc, "vendor", pmc.vendor_id, VENDOR_SSN, "Synthetic Repairs")
        taxpayer(conn, pmc, "vendor", pmc.vendor_id, "001239999", "Synthetic Repairs Inc", "EIN")
    finally:
        conn.execute("SELECT set_config('trust.staff_id', '', false)")
    found = tax_forms.taxpayer_id_of(conn, pmc.pmc_id, "vendor", pmc.vendor_id)
    assert (found.tin_type, found.tin_last4, found.legal_name, found.recorded_by) == (
        "EIN",
        "9999",
        "Synthetic Repairs Inc",
        "Staff 1",
    )
    assert VENDOR_SSN.encode() not in found.tin_sealed and b"001239999" not in found.tin_sealed
    context = tax_forms.context(pmc.pmc_id, "vendor", pmc.vendor_id)
    assert sealed.open_sealed(found.tin_sealed, context) == "001239999"
    # Copied onto another person's row, it doesn't open.
    with pytest.raises(sealed.CannotOpen):
        sealed.open_sealed(found.tin_sealed, tax_forms.context(pmc.pmc_id, "owner", pmc.vendor_id))
    assert tax_forms.taxpayer_id_of(conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id) is None


def test_a_taxpayer_id_is_one_persons_or_the_companys_own_ein(conn, pmc):
    owner = pmc.owners[0].owner_id
    taxpayer(conn, pmc, "owner", owner, OWNER_SSN, "Owner One", exempt=True)
    taxpayer(conn, pmc, "company", None, COMPANY_EIN, "Synthetic PMC LLC", "EIN")
    blob = sealed.seal(VENDOR_SSN, "anything")
    bad = [
        # vendor, owner, type, last4, name, exempt
        (pmc.vendor_id, owner, "EIN", "1234", "Both", False),  # two people
        (None, None, "SSN", "1234", "Company", False),  # the company's is an EIN
        (pmc.vendor_id, None, "EIN", "1234", "Vendor", True),  # only an owner is exempt
        (pmc.vendor_id, None, "TIN", "1234", "Vendor", False),
        (pmc.vendor_id, None, "EIN", "12345", "Vendor", False),
        (pmc.vendor_id, None, "EIN", "1234", " Vendor", False),
        (pmc.vendor_id, None, "EIN", "1234", "x" * 81, False),
    ]
    for vendor, own, kind, last4, name, exempt in bad:
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO trust_taxpayer_ids (pmc_id, vendor_id, owner_id, tin_type, tin_sealed,"
                " tin_last4, legal_name, exempt) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (pmc.pmc_id, vendor, own, kind, blob, last4, name, exempt),
            )


def test_taxpayer_ids_are_append_only_and_audited(conn, pmc):
    taxpayer(conn, pmc, "vendor", pmc.vendor_id, VENDOR_SSN, "Synthetic Repairs")
    for change in (
        "UPDATE trust_taxpayer_ids SET exempt = false",
        "DELETE FROM trust_taxpayer_ids",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation), conn.transaction():
            conn.execute(change)
    logged = conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE pmc_id = %s"
        " AND table_name = 'trust_taxpayer_ids'",
        (pmc.pmc_id,),
    ).fetchone()[0]
    assert logged == 1


@pytest.mark.parametrize(
    ("kind", "digits", "shown", "cut"),
    [
        ("EIN", "001234567", "00-1234567", "XX-XXX4567"),
        ("SSN", "000121234", "000-12-1234", "XXX-XX-1234"),
        ("ITIN", "000125678", "000-12-5678", "XXX-XX-5678"),
    ],
)
def test_a_number_reads_as_its_kind_writes_it(kind, digits, shown, cut):
    assert tax_forms.tin_shown(kind, digits) == shown
    assert tax_forms.tin_cut(kind, digits[-4:]) == cut


def test_the_threshold_is_the_1099_summarys():
    assert tax_forms.threshold(2025) == Decimal("600.00")
    assert tax_forms.threshold(2026) == Decimal("2000.00")
    assert tax_forms.threshold(2030) == Decimal("2000.00")


# --- who gets which form -------------------------------------------------------------------------


def test_a_vendor_marked_for_a_1099_and_paid_the_threshold_gets_a_1099_nec(conn, pmc):
    vendor_paid(conn, pmc, "2000.00", at(3, 5))
    vendor_paid(conn, pmc, "150.00", at(1, 5, 2027), marked=None, reference="INV-2")  # next year
    (found,) = tax_forms.recipients(conn, pmc.pmc_id, 2026)
    assert (found.form, found.party.kind, found.party.id, found.amount) == (
        "1099-NEC",
        "vendor",
        pmc.vendor_id,
        Decimal("2000.00"),
    )
    assert found.party.missing() == ["taxpayer ID", "mailing address"]
    assert tax_forms.recipients(conn, pmc.pmc_id, 2027) == []  # under the threshold


def test_a_vendor_under_the_threshold_or_not_marked_gets_none(conn, pmc):
    vendor_paid(conn, pmc, "1999.99", at(3, 5))
    assert tax_forms.recipients(conn, pmc.pmc_id, 2026) == []
    other = make_pmc(conn)
    vendor_paid(conn, other, "5000.00", at(3, 5), marked=False)
    assert tax_forms.recipients(conn, other.pmc_id, 2026) == []
    # In 2025 the threshold was 600.00.
    late = make_pmc(conn)
    vendor_paid(conn, late, "600.00", at(3, 5, 2025))
    assert [r.amount for r in tax_forms.recipients(conn, late.pmc_id, 2025)] == [Decimal("600.00")]


def test_an_owner_whose_rents_reach_the_threshold_gets_a_1099_misc_unless_exempt(conn, pmc):
    rents_collected(conn, pmc, "1500.00", at(1, 3))
    rents_collected(conn, pmc, "1500.00", at(2, 3))
    rents_collected(conn, pmc, "1900.00", at(2, 3), owner=1)  # under the threshold
    (found,) = tax_forms.recipients(conn, pmc.pmc_id, 2026)
    assert (found.form, found.party.id, found.amount) == (
        "1099-MISC",
        pmc.owners[0].owner_id,
        Decimal("3000.00"),
    )
    taxpayer(conn, pmc, "owner", pmc.owners[0].owner_id, OWNER_SSN, "Owner One", exempt=True)
    assert tax_forms.recipients(conn, pmc.pmc_id, 2026) == []


# --- the copies and the filing list --------------------------------------------------------------


def ready_pmc(conn):
    pmc = make_pmc(conn)
    company_ready(conn, pmc)
    vendor_paid(conn, pmc, "2500.00", at(4, 1))
    rents_collected(conn, pmc, "2400.00", at(5, 3))
    owner = pmc.owners[0].owner_id
    taxpayer(conn, pmc, "vendor", pmc.vendor_id, VENDOR_SSN, "=Synthetic Repairs")
    address(conn, pmc, "vendor_id", pmc.vendor_id)
    taxpayer(conn, pmc, "owner", owner, OWNER_SSN, "Owner One")
    address(conn, pmc, "owner_id", owner, "2 Synthetic Way")
    return pmc


def test_each_recipient_gets_a_copy_with_their_number_cut_and_the_payers_whole(conn):
    pmc = ready_pmc(conn)
    payer = tax_forms.payer_of(conn, pmc.pmc_id)
    found = tax_forms.recipients(conn, pmc.pmc_id, 2026)
    pages = PdfReader(io.BytesIO(tax_forms.copies_pdf(payer, found, "1099-NEC", 2026))).pages
    (page,) = pages
    text = page.extract_text()
    for shown in (
        "Form 1099-NEC 2026",
        "Copy B For Recipient",
        "Synthetic PMC LLC",
        "9 Office Row",
        "00-1234567",
        "=Synthetic Repairs",
        "1 Synthetic Way",
        "XXX-XX-1234",
        "NONEMPLOYEE COMPENSATION",
        "$2,500.00",
        "important tax information",
    ):
        assert shown in " ".join(text.split()), shown
    assert "000-12-1234" not in text and "000121234" not in text
    misc = PdfReader(io.BytesIO(tax_forms.copies_pdf(payer, found, "1099-MISC", 2026))).pages
    assert "RENTS" in misc[0].extract_text() and "$2,400.00" in misc[0].extract_text()


def test_the_filing_list_has_whole_numbers_and_no_formula(conn):
    pmc = ready_pmc(conn)
    payer = tax_forms.payer_of(conn, pmc.pmc_id)
    found = tax_forms.recipients(conn, pmc.pmc_id, 2026)
    rows = list(
        csv.reader(io.StringIO(tax_forms.filing_csv(payer, found, "1099-NEC", 2026).decode()))
    )
    assert rows[0] == list(tax_forms.LIST_COLUMNS)
    (row,) = rows[1:]
    assert dict(zip(rows[0], row, strict=True)) == {
        "Form": "1099-NEC",
        "Tax year": "2026",
        "Payer TIN type": "EIN",
        "Payer TIN": "00-1234567",
        "Payer name": "Synthetic PMC LLC",
        "Payer address": "9 Office Row",
        "Payer address 2": "",
        "Payer city": "Springfield",
        "Payer state": "ZZ",
        "Payer ZIP": "00000",
        "Recipient TIN type": "SSN",
        "Recipient TIN": "000-12-1234",
        "Recipient name": "'=Synthetic Repairs",
        "Recipient address": "1 Synthetic Way",
        "Recipient address 2": "",
        "Recipient city": "Springfield",
        "Recipient state": "ZZ",
        "Recipient ZIP": "00000",
        "Box 1": "Nonemployee compensation",
        "Box 1 amount": "2500.00",
    }


def test_a_recipient_missing_anything_is_left_out_and_so_is_everyone_while_the_payer_is(conn):
    pmc = ready_pmc(conn)
    owner = pmc.owners[0].owner_id
    conn.execute(
        "INSERT INTO trust_contacts (pmc_id, owner_id, phone) VALUES (%s, %s, '555-0100')",
        (pmc.pmc_id, owner),
    )  # their address taken away
    payer = tax_forms.payer_of(conn, pmc.pmc_id)
    found = tax_forms.recipients(conn, pmc.pmc_id, 2026)
    assert [r.party.missing() for r in found] == [[], ["mailing address"]]
    assert [r.party.id for r in tax_forms.ready(payer, found, "1099-NEC")] == [pmc.vendor_id]
    assert tax_forms.ready(payer, found, "1099-MISC") == []
    bare = make_pmc(conn)
    assert tax_forms.payer_of(conn, bare.pmc_id).missing() == ["taxpayer ID", "mailing address"]
    assert tax_forms.ready(tax_forms.payer_of(conn, bare.pmc_id), found, "1099-NEC") == []
