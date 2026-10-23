"""Property-based tests for 1099 forms (src/pms/tax_forms.py, migration 20261022000162).

Hypothesis pays a vendor any bills in any of two years, marks them for a 1099 or not, keeps any
taxpayer IDs (any nine digits, any kind) and addresses, and makes the forms for each year:
  - a vendor gets a 1099-NEC for a year exactly when they are marked and the year's bills paid
    come to its threshold, box 1 being that sum to the cent;
  - a sealed taxpayer ID opens only as its own person's, to the digits kept, and only its last four
    digits reach a recipient's copy;
  - the filing list and the copies hold exactly the recipients with everything a form needs,
    nobody while the company lacks anything.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import approve_bill, enter_bill, make_pmc, pay_bill, set_aside_bill, transfer_batch
from hypothesis import event, given
from hypothesis import strategies as st
from pypdf import PdfReader

from pms import sealed, tax_forms

YEARS = (2025, 2026)

runs = st.fixed_dictionaries(
    {
        "bills": st.lists(
            # From a dollar to $3,000, often hundreds: around either year's threshold.
            st.tuples(
                st.sampled_from(YEARS),
                st.integers(1, 9),
                st.one_of(st.integers(100, 60_000), st.integers(60_000, 300_000)),
            ),
            min_size=1,
            max_size=4,
        ),
        "marked": st.booleans(),
        "tin": st.one_of(
            st.none(), st.tuples(st.sampled_from(["EIN", "SSN", "ITIN"]), st.integers(0, 10**9 - 1))
        ),  # fmt: skip
        "vendor_address": st.booleans(),
        "company_ready": st.booleans(),
    }
)


@given(run=runs)
def check_forms(conn, run):
    pmc = make_pmc(conn)
    account = pmc.owners[0].account
    paid: dict[int, Decimal] = dict.fromkeys(YEARS, Decimal(0))
    conn.execute(
        "INSERT INTO trust_vendor_profiles (pmc_id, vendor_id, gets_1099) VALUES (%s, %s, %s)",
        (pmc.pmc_id, pmc.vendor_id, run["marked"]),
    )
    for n, (year, month, cents) in enumerate(run["bills"]):
        when = datetime(year, month, 10, 12, tzinfo=UTC)
        amount = Decimal(cents) / 100
        transfer_batch(conn, [(pmc.operating_cash, account, amount)], when)
        bill = enter_bill(
            conn, pmc.pmc_id, pmc.vendor_id, account, f"INV-{n}", when.date(), when.date(), amount
        )
        approve_bill(conn, pmc.pmc_id, bill, when.date(), "Owner A")
        set_aside_bill(conn, pmc.pmc_id, bill, when)
        pay_bill(conn, pmc.pmc_id, bill, when)
        paid[year] += amount
    digits = None
    if run["tin"] is not None:
        kind, number = run["tin"]
        digits = f"{number:09d}"
        tax_forms.record_taxpayer_id(
            conn, pmc.pmc_id, "vendor", pmc.vendor_id, kind, digits, "Synthetic Repairs", False
        )
        kept = tax_forms.taxpayer_id_of(conn, pmc.pmc_id, "vendor", pmc.vendor_id)
        assert kept.tin_last4 == digits[-4:]
        own = tax_forms.context(pmc.pmc_id, "vendor", pmc.vendor_id)
        assert sealed.open_sealed(kept.tin_sealed, own) == digits
        with pytest.raises(sealed.CannotOpen):
            sealed.open_sealed(kept.tin_sealed, tax_forms.context(pmc.pmc_id, "company", None))
    if run["vendor_address"]:
        conn.execute(
            "INSERT INTO trust_contacts (pmc_id, vendor_id, address_line_1, city, state,"
            " postal_code) VALUES (%s, %s, '1 Synthetic Way', 'Springfield', 'ZZ', '00000')",
            (pmc.pmc_id, pmc.vendor_id),
        )
    if run["company_ready"]:
        tax_forms.record_taxpayer_id(
            conn, pmc.pmc_id, "company", None, "EIN", "001234567", "Synthetic PMC LLC", False
        )
        conn.execute(
            "INSERT INTO trust_company_profiles (pmc_id, address_line_1, city, state, postal_code)"
            " VALUES (%s, '9 Office Row', 'Springfield', 'ZZ', '00000')",
            (pmc.pmc_id,),
        )

    payer = tax_forms.payer_of(conn, pmc.pmc_id)
    for year in YEARS:
        found = [r for r in tax_forms.recipients(conn, pmc.pmc_id, year) if r.form == "1099-NEC"]
        gets = run["marked"] and paid[year] >= tax_forms.threshold(year)
        assert [r.amount for r in found] == ([paid[year]] if gets else [])
        complete = gets and digits is not None and run["vendor_address"]
        expected = 1 if complete and run["company_ready"] else 0
        event(f"forms ready: {expected}")
        listed = list(
            csv.reader(io.StringIO(tax_forms.filing_csv(payer, found, "1099-NEC", year).decode()))
        )
        assert len(listed) - 1 == expected
        copies = tax_forms.copies_pdf(payer, found, "1099-NEC", year)
        pages = PdfReader(io.BytesIO(copies)).pages if expected else []
        assert len(pages) == expected
        for page in pages:
            text = page.extract_text()
            assert digits is not None and digits[-4:] in text
            whole = tax_forms.tin_shown(run["tin"][0], digits)
            assert whole not in text or whole == "00-1234567"  # the payer's own may show
        if expected:
            assert listed[1][11] == tax_forms.tin_shown(run["tin"][0], digits)


def test_a_1099_goes_to_whom_and_for_what_the_year_says(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_forms(conn)
