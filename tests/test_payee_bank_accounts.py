"""A vendor's or an owner's bank account (migration 20261022000155): trust_payee_bank_accounts
rows, whole or a removal, each one payee's, its routing number passing the ABA checksum;
append-only and audited; a named staff member needs disburse; the AI role reads none of it. The
API's side is in tests/api/test_bank_details_api.py. Routing numbers here start 00, which no bank
holds (tools/check_synthetic_data.py)."""

import psycopg
import pytest
from helpers import ROLES, add_staff, give_roles, make_pmc

from pms import sealed

ROUTING = "001000009"  # 00 prefix, ABA checksum holds


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def add(conn, pmc, removed=False, routing=ROUTING, **values):
    columns = {
        "pmc_id": pmc.pmc_id,
        "vendor_id": pmc.vendor_id,
        "removed": removed,
        **(
            {}
            if removed
            else {
                "holder_name": "Demo Roofing Co",
                "routing_number": routing,
                "account_type": "checking",
                "account_number_sealed": sealed.seal("000123456789", "context"),
                "account_last4": "6789",
            }
        ),
        **values,
    }
    names = ", ".join(columns)
    marks = ", ".join(["%s"] * len(columns))
    conn.execute(
        f"INSERT INTO trust_payee_bank_accounts ({names}) VALUES ({marks})",  # noqa: S608
        list(columns.values()),
    )


@pytest.mark.parametrize(
    ("routing", "ok"),
    [("001000009", True), ("000000000", True), ("001000008", False), ("12345678", False),
     ("00100000a", False), ("0010000091", False)],
)  # fmt: skip
def test_a_routing_number_is_nine_digits_whose_checksum_holds(conn, routing, ok):
    assert conn.execute("SELECT trust_aba_routing_ok(%s)", (routing,)).fetchone() == (ok,)


def test_an_account_is_whole_or_a_removal_and_one_payees(conn, pmc):
    add(conn, pmc)
    add(conn, pmc, removed=True)
    for bad in (
        {"routing": "001000008"},
        {"account_last4": "67890"},
        {"holder_name": "x" * 23},
        {"account_type": "brokerage"},
        {"account_number_sealed": b"short"},
        {"owner_id": pmc.owners[0].owner_id},
    ):
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            add(conn, pmc, **bad)
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        add(conn, pmc, removed=True, holder_name="Demo Roofing Co")
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        add(conn, pmc, holder_name=None)


def test_rows_are_never_changed_and_every_one_is_audited(conn, pmc):
    add(conn, pmc)
    with pytest.raises(psycopg.errors.RestrictViolation), conn.transaction():
        conn.execute("UPDATE trust_payee_bank_accounts SET account_last4 = '0000'")
    with pytest.raises(psycopg.errors.RestrictViolation), conn.transaction():
        conn.execute("DELETE FROM trust_payee_bank_accounts")
    assert conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE table_name = 'trust_payee_bank_accounts'"
        " AND pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchone() == (1,)


def test_a_named_staff_member_needs_disburse_to_change_where_money_goes(conn, pmc):
    clerk = add_staff(conn, pmc.pmc_id, "Clerk")
    give_roles(conn, pmc.pmc_id, clerk, [r for r in ROLES if r != "disburse"])
    payer = add_staff(conn, pmc.pmc_id, "Payer")
    give_roles(conn, pmc.pmc_id, payer, ["disburse"])

    with conn.transaction():
        conn.execute("SELECT set_config('trust.staff_id', %s, true)", (str(clerk),))
        with (
            pytest.raises(psycopg.errors.InsufficientPrivilege, match="disburse"),
            conn.transaction(),
        ):
            add(conn, pmc)
    with conn.transaction():
        conn.execute("SELECT set_config('trust.staff_id', %s, true)", (str(payer),))
        add(conn, pmc)
    assert conn.execute(
        "SELECT recorded_by FROM trust_payee_bank_accounts WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchall() == [(payer,)]


def test_the_ai_role_reads_none_of_it(conn, ai_conn, pmc):
    add(conn, pmc)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ai_conn.execute("SELECT * FROM trust_payee_bank_accounts").fetchall()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ai_conn.execute("SELECT trust_aba_routing_ok('001000009')").fetchall()
