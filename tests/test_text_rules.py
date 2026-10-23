"""Text messages in the database (migration 20261023000204): a person's word on texts at a
US mobile number (append-only and audited, the latest holding; the person's own in the portal,
staff's naming them, a STOP reply saying no and a START reply taking back only a STOP), and the
outbox, which takes a text only to the number its person last agreed to while they agree, and the
PMS's own once each. The AI role reads none of it but the settings. The module's side is in
tests/test_texts.py, the API's in tests/api/test_texts_api.py. Every number is a fictional
555-01xx one."""

from datetime import UTC, date, datetime
from uuid import uuid4

import psycopg
import pytest
from helpers import add_staff, add_unit, charge, make_pmc, open_lease

ONE = "+12025550123"
TWO = "+12025550145"


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def tenant_of(conn, pmc):
    return conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]


def consent(conn, pmc, tenant, phone=ONE, consented=True, how="in writing", by_person=False):
    return conn.execute(
        "INSERT INTO trust_text_consents (pmc_id, tenant_id, phone, consented, how, by_person)"
        " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (pmc.pmc_id, tenant, phone, consented, how, by_person),
    ).fetchone()[0]


def text(conn, pmc, tenant, consent_id, phone=ONE, kind="message", sent_by=None, **about):
    columns = ", ".join(["pmc_id", "kind", "tenant_id", "consent_id", "to_phone", "body",
                         "sent_by", "not_before", *about])  # fmt: skip
    values = ", ".join(["%s"] * (8 + len(about)))
    return conn.execute(
        f"INSERT INTO trust_texts ({columns}) VALUES ({values}) RETURNING id",  # noqa: S608
        (pmc.pmc_id, kind, tenant, consent_id, phone, "Synthetic PMC: hello. Reply STOP to stop.",
         sent_by, datetime.now(UTC), *about.values()),
    ).fetchone()[0]  # fmt: skip


def test_a_number_is_a_us_mobile_one_as_plus_one_and_ten_digits(conn, pmc):
    tenant = tenant_of(conn, pmc)
    consent(conn, pmc, tenant, "+12025550199")
    for bad in ("2025550123", "+1 202 555 0123", "+11025550123", "+12021550123", "+442025550123"):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            consent(conn, pmc, tenant, bad)


def test_how_a_word_was_given_fits_who_gave_it(conn, pmc):
    tenant = tenant_of(conn, pmc)
    consent(conn, pmc, tenant, how="portal", by_person=True)
    consent(conn, pmc, tenant, consented=False, how="verbally")
    for how, by_person, consented in (
        ("portal", False, True),  # the portal is the person's own word
        ("in writing", True, True),  # and only the portal is
        ("stop reply", False, True),  # STOP says no
        ("start reply", False, False),  # START says yes
        ("by carrier pigeon", False, True),
    ):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            consent(conn, pmc, tenant, consented=consented, how=how, by_person=by_person)
    # A vendor has no portal.
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO trust_text_consents (pmc_id, vendor_id, phone, consented, how, by_person)"
            " VALUES (%s, %s, %s, true, 'portal', true)",
            (pmc.pmc_id, pmc.vendor_id, ONE),
        )
    # A word is one person's.
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO trust_text_consents (pmc_id, tenant_id, vendor_id, phone, consented, how)"
            " VALUES (%s, %s, %s, %s, true, 'in writing')",
            (pmc.pmc_id, tenant, pmc.vendor_id, ONE),
        )


def test_staff_who_record_a_word_are_named_and_the_persons_own_names_nobody(conn, pmc):
    tenant = tenant_of(conn, pmc)
    staff = add_staff(conn, pmc.pmc_id)
    conn.execute("SELECT set_config('trust.staff_id', %s, false)", (str(staff),))
    try:
        written = consent(conn, pmc, tenant)
        own = consent(conn, pmc, tenant, TWO, how="portal", by_person=True)
    finally:
        conn.execute("SELECT set_config('trust.staff_id', '', false)")
    by = dict(
        conn.execute(
            "SELECT id, recorded_by FROM trust_text_consents WHERE id IN (%s, %s)", (written, own)
        ).fetchall()
    )
    assert by == {written: staff, own: None}


def test_start_takes_back_only_a_stop_from_the_same_number(conn, pmc):
    tenant = tenant_of(conn, pmc)
    consent(conn, pmc, tenant)
    with pytest.raises(psycopg.errors.CheckViolation, match="START"), conn.transaction():
        consent(conn, pmc, tenant, how="start reply")  # nothing stopped
    consent(conn, pmc, tenant, consented=False, how="stop reply")
    with pytest.raises(psycopg.errors.CheckViolation, match="START"), conn.transaction():
        consent(conn, pmc, tenant, TWO, how="start reply")  # another number
    consent(conn, pmc, tenant, how="start reply")
    consent(conn, pmc, tenant, consented=False, how="verbally")
    with pytest.raises(psycopg.errors.CheckViolation, match="START"), conn.transaction():
        consent(conn, pmc, tenant, how="start reply")  # they told staff, not a STOP


def test_a_text_goes_only_to_the_number_its_person_last_agreed_to_while_they_agree(conn, pmc):
    tenant = tenant_of(conn, pmc)
    staff = add_staff(conn, pmc.pmc_id)
    refused = "last agreed to be texted at"
    with pytest.raises(psycopg.errors.CheckViolation, match=refused), conn.transaction():
        text(conn, pmc, tenant, uuid4(), sent_by=staff)  # no word at all
    first = consent(conn, pmc, tenant)
    text(conn, pmc, tenant, first, sent_by=staff)
    with pytest.raises(psycopg.errors.CheckViolation, match=refused), conn.transaction():
        text(conn, pmc, tenant, first, TWO, sent_by=staff)  # not its number
    second = consent(conn, pmc, tenant, TWO)
    with pytest.raises(psycopg.errors.CheckViolation, match=refused), conn.transaction():
        text(conn, pmc, tenant, first, sent_by=staff)  # an earlier word
    text(conn, pmc, tenant, second, TWO, sent_by=staff)
    stopped = consent(conn, pmc, tenant, TWO, consented=False, how="stop reply")
    for word in (second, stopped):
        with pytest.raises(psycopg.errors.CheckViolation, match=refused), conn.transaction():
            text(conn, pmc, tenant, word, TWO, sent_by=staff)


def test_a_message_names_who_wrote_it_and_the_pmss_own_name_what_they_are_about(conn, pmc):
    tenant = tenant_of(conn, pmc)
    word = consent(conn, pmc, tenant)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "1500.00", [tenant])
    rent = charge(conn, pmc.pmc_id, lease, date(2026, 2, 1), "1500.00")
    for kind, sent_by, about in (
        ("message", None, {}),
        (
            "rent_reminder",
            add_staff(conn, pmc.pmc_id),
            {"lease_id": lease, "period": date(2026, 2, 1)},
        ),
        ("rent_reminder", None, {"lease_id": lease}),
        ("late_notice", None, {"lease_id": lease}),
    ):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            text(conn, pmc, tenant, word, kind=kind, sent_by=sent_by, **about)
    text(conn, pmc, tenant, word, kind="rent_reminder", lease_id=lease, period=date(2026, 2, 1))
    text(conn, pmc, tenant, word, kind="late_notice", lease_id=lease, charge_id=rent)
    # Each of the PMS's own once.
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        text(conn, pmc, tenant, word, kind="rent_reminder", lease_id=lease, period=date(2026, 2, 1))
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        text(conn, pmc, tenant, word, kind="late_notice", lease_id=lease, charge_id=rent)


def test_a_text_is_at_most_480_characters(conn, pmc):
    tenant = tenant_of(conn, pmc)
    word = consent(conn, pmc, tenant)
    staff = add_staff(conn, pmc.pmc_id)
    for body in ("", "x" * 481):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO trust_texts (pmc_id, kind, tenant_id, consent_id, to_phone, body,"
                " sent_by, not_before) VALUES (%s, 'message', %s, %s, %s, %s, %s, now())",
                (pmc.pmc_id, tenant, word, ONE, body, staff),
            )


def test_words_texts_settings_and_deliveries_are_append_only(conn, pmc):
    tenant = tenant_of(conn, pmc)
    word = consent(conn, pmc, tenant)
    sent = text(conn, pmc, tenant, word, sent_by=add_staff(conn, pmc.pmc_id))
    conn.execute(
        "INSERT INTO trust_text_settings (pmc_id, rent_reminder_days, late_notices)"
        " VALUES (%s, 3, true)",
        (pmc.pmc_id,),
    )
    conn.execute(
        "INSERT INTO trust_text_deliveries (text_id, pmc_id, sender, provider_id)"
        " VALUES (%s, %s, 'fake', 'fake-1')",
        (sent, pmc.pmc_id),
    )
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO trust_text_deliveries (text_id, pmc_id, sender, provider_id, error)"
            " VALUES (%s, %s, 'fake', 'fake-2', 'it failed')",
            (sent, pmc.pmc_id),
        )
    for table in ("trust_text_consents", "trust_text_settings", "trust_texts",
                  "trust_text_deliveries"):  # fmt: skip
        for change in (f"UPDATE {table} SET pmc_id = pmc_id", f"DELETE FROM {table}"):
            with pytest.raises(psycopg.errors.RestrictViolation), conn.transaction():
                conn.execute(change)


def test_words_and_settings_are_audited_but_texts_are_not(conn, pmc):
    tenant = tenant_of(conn, pmc)
    word = consent(conn, pmc, tenant)
    text(conn, pmc, tenant, word, sent_by=add_staff(conn, pmc.pmc_id))
    conn.execute(
        "INSERT INTO trust_text_settings (pmc_id, rent_reminder_days, late_notices)"
        " VALUES (%s, NULL, true)",
        (pmc.pmc_id,),
    )
    logged = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT table_name FROM trust_audit_log WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchall()
    }
    assert {"trust_text_consents", "trust_text_settings"} <= logged
    assert not {"trust_texts", "trust_text_deliveries"} & logged


def balance_notice(conn, pmc, lease, staff):
    """A balance notice of the lease (migration 20261023000201), as the API makes one."""
    template = conn.execute(
        "INSERT INTO trust_notice_templates (pmc_id) VALUES (%s) RETURNING id", (pmc.pmc_id,)
    ).fetchone()[0]
    version = conn.execute(
        "INSERT INTO trust_notice_template_versions (pmc_id, template_id, name, body)"
        " VALUES (%s, %s, 'Reminder', 'Hello.') RETURNING id",
        (pmc.pmc_id, template),
    ).fetchone()[0]
    run = conn.execute(
        "INSERT INTO trust_balance_notice_runs (pmc_id, template_version_id, dated_on)"
        " VALUES (%s, %s, '2026-10-23') RETURNING id",
        (pmc.pmc_id, version),
    ).fetchone()[0]
    notice = conn.execute(
        "INSERT INTO trust_notices (pmc_id, lease_id, template_version_id, dated_on, title,"
        " letterhead, body) VALUES (%s, %s, %s, '2026-10-23', 'Reminder', 'Synthetic PMC',"
        " 'Hello.') RETURNING id",
        (pmc.pmc_id, lease, version),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO trust_balance_notices (notice_id, pmc_id, run_id, lease_id, owed)"
        " VALUES (%s, %s, %s, %s, '125.00')",
        (notice, pmc.pmc_id, run, lease),
    )
    return notice


def visit(conn, pmc, unit, step="scheduled"):
    """A work order at the unit and a step of it."""
    order = conn.execute(
        "INSERT INTO trust_work_orders (pmc_id, property_id, unit_id, summary, opened_at)"
        " VALUES (%s, %s, %s, 'Leaking faucet', now()) RETURNING id",
        (pmc.pmc_id, pmc.owners[0].property_id, unit),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO trust_work_order_steps (pmc_id, work_order_id, step, vendor_id, taken_at)"
        " VALUES (%s, %s, 'assigned', %s, now())",
        (pmc.pmc_id, order, pmc.vendor_id),
    )
    return conn.execute(
        "INSERT INTO trust_work_order_steps (pmc_id, work_order_id, step, scheduled_for, taken_at)"
        " VALUES (%s, %s, %s, %s, now()) RETURNING id",
        (pmc.pmc_id, order, step, date(2026, 10, 25) if step == "scheduled" else None),
    ).fetchone()[0]


def test_a_balance_notices_text_is_of_its_lease_once_per_tenant(conn, pmc):
    tenant = tenant_of(conn, pmc)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "900.00", [tenant])
    other = open_lease(
        conn, pmc.pmc_id, add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2"),
        date(2026, 1, 1), None, "900.00", [tenant],
    )  # fmt: skip
    notice = balance_notice(conn, pmc, lease, add_staff(conn, pmc.pmc_id))
    agreed = consent(conn, pmc, tenant)
    text(conn, pmc, tenant, agreed, kind="balance_notice", lease_id=lease, notice_id=notice)
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        text(conn, pmc, tenant, agreed, kind="balance_notice", lease_id=lease, notice_id=notice)
    with pytest.raises(psycopg.errors.CheckViolation, match="notice or scheduled visit"):
        text(conn, pmc, tenant, agreed, kind="balance_notice", lease_id=other, notice_id=notice)
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        text(conn, pmc, tenant, agreed, kind="balance_notice", lease_id=lease)  # names no notice


def test_a_visits_text_is_of_a_scheduled_step_of_the_companys_once_per_tenant(conn, pmc):
    tenant = tenant_of(conn, pmc)
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    agreed = consent(conn, pmc, tenant)
    step = visit(conn, pmc, unit)
    text(conn, pmc, tenant, agreed, kind="visit", work_order_step_id=step)
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        text(conn, pmc, tenant, agreed, kind="visit", work_order_step_id=step)
    done = visit(conn, pmc, unit, step="completed")
    with pytest.raises(psycopg.errors.CheckViolation, match="notice or scheduled visit"):
        text(conn, pmc, tenant, agreed, kind="visit", work_order_step_id=done)
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        text(conn, pmc, tenant, agreed, kind="message", sent_by=add_staff(conn, pmc.pmc_id),
             work_order_step_id=step)  # fmt: skip
