"""Logins a host app signs in (migration 20261107000200, pms.external,
docs/adr/0013-sign-in-through-a-host.md): what the database holds them to, and the pages and
emails that ask whether a login signs in.

A login linked this way has no password (no trust_login_secrets row), so every query that reads
"can sign in" from the password table must count a host's link too. The guard at the end reads
every query of src/pms for that.
"""

from __future__ import annotations

import ast
import uuid
from datetime import date
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from helpers import acting_as, add_agreement, add_staff, enter_bill, make_pmc

from pms import accounts, email, external

ROOT = Path(__file__).resolve().parents[1]
ISSUER = "occupella"
JAN_1, JAN_10, JAN_20 = date(2026, 1, 1), date(2026, 1, 10), date(2026, 1, 20)


def person(email_address: str, staff_of=None, second_step: bool = True) -> external.Person:
    return external.Person(
        subject=f"user-{uuid.uuid4().hex}",
        email=email_address,
        name="Staff A",
        staff_of=staff_of or {},
        second_step=second_step,
    )


def email_for(word: str) -> str:
    return f"{word}.{uuid.uuid4().hex[:8]}@example.com"


def admin(company: str = "Company A") -> external.Membership:
    return external.Membership(admin=True, company_name=company)


def staff(company: str = "Company A") -> external.Membership:
    return external.Membership(admin=False, company_name=company)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def linked_staff(app_conn, pmc_id, membership=None) -> UUID:
    """A staff member who signed in through the host: their staff id."""
    signed = external.sign_in(
        app_conn, ISSUER, person(email_for("staff"), {pmc_id: membership or staff()}), pmc_id, None
    )
    return signed.session.person_id


# --- what the database holds a link to ------------------------------------------------------


def test_a_link_is_never_rewritten(conn, app_conn, pmc):
    linked_staff(app_conn, pmc.pmc_id)
    for statement in (
        "UPDATE trust_external_identities SET subject = 'someone else' WHERE pmc_id = %s",
        "DELETE FROM trust_external_identities WHERE pmc_id = %s",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(statement, (pmc.pmc_id,))


def login_of_staff(conn, staff_id) -> UUID:
    return conn.execute("SELECT id FROM trust_logins WHERE staff_id = %s", (staff_id,)).fetchone()[
        0
    ]


@pytest.mark.parametrize(
    "issuer, subject",
    [("Occupella", "s"), ("9host", "s"), ("h" * 33, "s"), (ISSUER, ""), (ISSUER, "s" * 256)],
)
def test_a_link_names_its_host_and_person_in_the_forms_they_take(conn, pmc, issuer, subject):
    staff_id = add_staff(conn, pmc.pmc_id, "Staff B")
    accounts.create_invite(conn, pmc.pmc_id, "staff", staff_id, email_for("staff"), None)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_external_identities (pmc_id, login_id, issuer, subject)"
            " VALUES (%s, %s, %s, %s)",
            (pmc.pmc_id, login_of_staff(conn, staff_id), issuer, subject),
        )


def test_a_person_is_linked_once_per_company_and_a_login_once_per_host(conn, pmc):
    logins = []
    for name in ("Staff C", "Staff D"):
        staff_id = add_staff(conn, pmc.pmc_id, name)
        accounts.create_invite(conn, pmc.pmc_id, "staff", staff_id, email_for("staff"), None)
        logins.append(login_of_staff(conn, staff_id))
    insert = (
        "INSERT INTO trust_external_identities (pmc_id, login_id, issuer, subject)"
        " VALUES (%s, %s, %s, %s)"
    )
    conn.execute(insert, (pmc.pmc_id, logins[0], ISSUER, "person-1"))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(insert, (pmc.pmc_id, logins[1], ISSUER, "person-1"))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(insert, (pmc.pmc_id, logins[0], ISSUER, "person-2"))
    # Another host may link the same login.
    conn.execute(insert, (pmc.pmc_id, logins[0], "another-host", "person-1"))


def test_a_link_is_to_a_login_of_its_own_company(conn, pmc):
    other = make_pmc(conn)
    staff_id = add_staff(conn, other.pmc_id, "Staff E")
    accounts.create_invite(conn, other.pmc_id, "staff", staff_id, email_for("staff"), None)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO trust_external_identities (pmc_id, login_id, issuer, subject)"
            " VALUES (%s, %s, %s, 'person-x')",
            (pmc.pmc_id, login_of_staff(conn, staff_id), ISSUER),
        )


# --- the host's role changes ----------------------------------------------------------------

ROLE = (
    "INSERT INTO trust_staff_roles (pmc_id, staff_id, role, granted, host_issuer)"
    " VALUES (%s, %s, %s, %s, %s)"
)


def test_a_host_changes_the_admin_role_only_of_staff_it_signs_in(conn, app_conn, pmc):
    stranger = add_staff(conn, pmc.pmc_id, "Staff F")
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="signs in no login"):
        app_conn.execute(ROLE, (pmc.pmc_id, stranger, "admin", True, ISSUER))
    linked = linked_staff(app_conn, pmc.pmc_id)
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="signs in no login"):
        app_conn.execute(ROLE, (pmc.pmc_id, linked, "admin", True, "another-host"))
    app_conn.execute(ROLE, (pmc.pmc_id, linked, "admin", True, ISSUER))
    assert app_conn.execute(
        "SELECT trust_staff_holds(%s, %s, 'admin')", (pmc.pmc_id, linked)
    ).fetchone()[0]


def test_a_host_changes_no_other_role_and_names_no_staff_member(conn, app_conn, pmc):
    linked = linked_staff(app_conn, pmc.pmc_id, admin())
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="only the admin role"):
        app_conn.execute(ROLE, (pmc.pmc_id, linked, "disburse", True, ISSUER))
    other = linked_staff(app_conn, pmc.pmc_id)
    with (
        acting_as(app_conn, linked),
        pytest.raises(psycopg.errors.InsufficientPrivilege, match="only the admin role"),
    ):
        app_conn.execute(ROLE, (pmc.pmc_id, other, "admin", True, ISSUER))


def test_a_host_never_takes_a_companys_last_admin(conn, app_conn):
    company = uuid.uuid4()
    only = linked_staff(app_conn, company, admin())
    with pytest.raises(psycopg.errors.CheckViolation, match="last admin"):
        app_conn.execute(ROLE, (company, only, "admin", False, ISSUER))


# --- emails to a login the host signs in ----------------------------------------------------


def test_an_owner_the_host_signs_in_is_emailed_their_bill_to_approve(conn, app_conn, pmc):
    add_agreement(conn, pmc.pmc_id, pmc.owners[0].account, JAN_1, approval_limit="500")
    owner_email = email_for("owner")
    invite = accounts.create_invite(
        conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, owner_email, None
    )
    external.sign_in(app_conn, ISSUER, person(owner_email), None, invite.token)
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, "ROOF-9", JAN_10, JAN_20, "600.00"
    )

    assert email.write_bill_approval(conn, pmc.pmc_id, bill, "https://pms.example") == "written"
    assert (
        conn.execute(
            "SELECT to_address FROM trust_emails WHERE pmc_id = %s AND kind = 'bill_approval'",
            (pmc.pmc_id,),
        ).fetchone()[0]
        == owner_email
    )


def test_an_admin_the_host_signs_in_is_told_what_failed(conn, app_conn):
    company = uuid.uuid4()
    admin_email = email_for("admin")
    external.sign_in(app_conn, ISSUER, person(admin_email, {company: admin()}), company, None)

    written = email.write_admin_alert(conn, company, "nightly_failed", JAN_10, "Broken")

    assert written == 1
    assert (
        conn.execute(
            "SELECT to_address FROM trust_emails WHERE pmc_id = %s AND kind = 'admin_alert'",
            (company,),
        ).fetchone()[0]
        == admin_email
    )


# --- every query that asks whether a login signs in -----------------------------------------

# The password code itself, and the export that leaves the secrets out.
PASSWORD_CODE = {"src/pms/accounts.py", "src/pms/exports.py"}


def queries_reading_passwords() -> list[tuple[str, int, str]]:
    found = []
    for path in sorted((ROOT / "src/pms").rglob("*.py")):
        name = str(path.relative_to(ROOT))
        if name in PASSWORD_CODE:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "trust_login_secrets" in node.value
            ):
                found.append((name, node.lineno, node.value))
    return found


def test_every_query_that_reads_a_password_counts_a_hosts_sign_in_too():
    found = queries_reading_passwords()
    # A scan that finds nothing is a claim to check: these read it today.
    assert {name for name, _, _ in found} >= {
        "src/pms/api/people.py",
        "src/pms/api/rentals.py",
        "src/pms/api/directory.py",
        "src/pms/api/maintenance.py",
        "src/pms/email.py",
    }
    missing = [
        f"{name}:{line}" for name, line, text in found if "trust_external_identities" not in text
    ]
    assert not missing, (
        "a login a host app signs in has no password: count trust_external_identities too, at"
        f" {missing}"
    )
