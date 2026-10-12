"""Staff roles: who changes roles (admin), approves a reconciliation (approve) and pays out money
(disburse), and month-end prepared by one staff member and approved by another. Migration
20261012000034.

The rules hold for the staff member the application names (trust.staff_id), as the API does on
every staff request; a write that names nobody (the server's command line) is not held to them.
"""

import shutil
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    MIGRATIONS,
    acting_as,
    add_agreement,
    add_staff,
    admin_of,
    approve,
    approve_prepared,
    change_role,
    draw_owner,
    enter_bill,
    give_roles,
    import_statement,
    in_background,
    make_pmc,
    match_lines,
    pay_bill,
    post,
    prepare_bank,
    roles_of,
    set_aside_bill,
    transfer_batch,
    unmatch_lines,
    wait_until_blocked,
)

from pms import accounts
from tools.check_rollback import Dbmate
from tools.scratch_db import scratch_database

JAN_1, JAN_15, JAN_31 = date(2026, 1, 1), date(2026, 1, 15), date(2026, 1, 31)
FEB_1, FEB_28 = date(2026, 2, 1), date(2026, 2, 28)
TODAY = datetime.now(UTC).date()


def at(day, hour=12):
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def refused(match, error=psycopg.errors.CheckViolation):
    return pytest.raises(error, match=match)


def not_allowed(match):
    return refused(match, psycopg.errors.InsufficientPrivilege)


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


@pytest.fixture
def admin(conn, pmc):
    """The PMC's first admin, granted with nobody named."""
    staff_id = add_staff(conn, pmc.pmc_id, "Admin 1")
    change_role(conn, pmc.pmc_id, staff_id, "admin")
    return staff_id


def changes(conn, staff_id):
    return conn.execute(
        "SELECT role, granted, changed_by FROM trust_staff_roles WHERE staff_id = %s"
        " ORDER BY created_at",
        (staff_id,),
    ).fetchall()


# --- who holds a role ----------------------------------------------------------------------


def test_every_staff_member_holds_every_role_after_the_upgrade(database_url, tmp_path):
    migrations = sorted(MIGRATIONS.glob("*.sql"))
    roles = next(m for m in migrations if m.name.startswith("20261012000034"))
    for migration in migrations[: migrations.index(roles)]:
        shutil.copy(migration, tmp_path)

    with scratch_database(database_url, "pms_staff_roles") as url:
        dbmate = Dbmate(shutil.which("dbmate"), tmp_path, url)
        assert dbmate.run("--no-dump-schema", "up").returncode == 0
        with psycopg.connect(url, autocommit=True) as conn:
            pmc = make_pmc(conn)
            staff = [add_staff(conn, pmc.pmc_id, f"Staff {n}") for n in (1, 2)]
        shutil.copy(roles, tmp_path)
        applied = dbmate.run("--no-dump-schema", "up")
        assert applied.returncode == 0, applied.stderr
        with psycopg.connect(url, autocommit=True) as conn:
            assert [roles_of(conn, pmc.pmc_id, s) for s in staff] == [
                {"admin", "approve", "disburse"}
            ] * 2


def test_a_pmcs_first_admin_is_granted_with_nobody_named(conn, pmc):
    first, second = add_staff(conn, pmc.pmc_id, "Staff 1"), add_staff(conn, pmc.pmc_id, "Staff 2")
    # Nobody named grants nothing but a first admin.
    with not_allowed("name the admin who changes a role"):
        change_role(conn, pmc.pmc_id, first, "approve")

    change_role(conn, pmc.pmc_id, first, "admin")

    with not_allowed("name the admin who changes a role"):
        change_role(conn, pmc.pmc_id, second, "admin")
    assert changes(conn, first) == [("admin", True, None)]
    assert roles_of(conn, pmc.pmc_id, first) == {"admin"}
    assert roles_of(conn, pmc.pmc_id, second) == set()


def test_only_an_admin_changes_a_role_and_the_change_records_who_and_when(
    conn, app_conn, pmc, admin
):
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    other = make_pmc(conn)
    their_admin = admin_of(conn, other.pmc_id)

    for who in (clerk, their_admin):
        with not_allowed("may not change a role"):
            change_role(app_conn, pmc.pmc_id, clerk, "disburse", by=who)

    before = datetime.now(UTC)
    change_role(app_conn, pmc.pmc_id, clerk, "disburse", by=admin)
    # The owner can't put words in the log either: the trigger says who and when.
    with acting_as(conn, admin):
        conn.execute(
            "INSERT INTO trust_staff_roles (pmc_id, staff_id, role, granted, changed_by,"
            " created_at) VALUES (%s, %s, 'approve', true, %s, '2020-01-01')",
            (pmc.pmc_id, clerk, clerk),
        )
    (created_at,) = conn.execute(
        "SELECT max(created_at) FROM trust_staff_roles WHERE staff_id = %s", (clerk,)
    ).fetchone()
    assert created_at > before
    assert changes(conn, clerk) == [("disburse", True, admin), ("approve", True, admin)]
    assert roles_of(conn, pmc.pmc_id, clerk) == {"approve", "disburse"}


def test_a_role_is_one_of_three_for_a_staff_member_of_the_pmc(conn, pmc, admin):
    with refused("trust_staff_roles_role_check"):
        change_role(conn, pmc.pmc_id, admin, "owner", by=admin)
    theirs = add_staff(conn, make_pmc(conn).pmc_id)
    with refused("foreign key", psycopg.errors.ForeignKeyViolation):
        change_role(conn, pmc.pmc_id, theirs, "approve", by=admin)


def test_nobody_named_takes_no_role_away(conn, pmc):
    staff_id = add_staff(conn, pmc.pmc_id)
    with not_allowed("name the admin who changes a role"):
        change_role(conn, pmc.pmc_id, staff_id, "admin", granted=False)


def test_a_change_changes_something_and_the_latest_counts(conn, pmc, admin):
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    with refused("does not hold the approve role already"):
        change_role(conn, pmc.pmc_id, clerk, "approve", granted=False, by=admin)
    change_role(conn, pmc.pmc_id, clerk, "approve", by=admin)
    with refused("holds the approve role already"):
        change_role(conn, pmc.pmc_id, clerk, "approve", by=admin)

    change_role(conn, pmc.pmc_id, clerk, "approve", granted=False, by=admin)
    assert roles_of(conn, pmc.pmc_id, clerk) == set()
    change_role(conn, pmc.pmc_id, clerk, "approve", by=admin)
    assert roles_of(conn, pmc.pmc_id, clerk) == {"approve"}


def test_a_pmc_keeps_an_admin(conn, pmc, admin):
    with refused("is the PMC's last admin"):
        change_role(conn, pmc.pmc_id, admin, "admin", granted=False, by=admin)

    second = add_staff(conn, pmc.pmc_id, "Admin 2")
    change_role(conn, pmc.pmc_id, second, "admin", by=admin)
    change_role(conn, pmc.pmc_id, admin, "admin", granted=False, by=admin)

    assert roles_of(conn, pmc.pmc_id, admin) == set()
    with not_allowed("Admin 1 may not change a role"):
        change_role(conn, pmc.pmc_id, admin, "admin", by=admin)
    with refused("is the PMC's last admin"):
        change_role(conn, pmc.pmc_id, second, "admin", granted=False, by=second)


@pytest.mark.timing
def test_two_admins_taking_each_others_role_leave_one(conn, database_url, pmc, admin):
    second = add_staff(conn, pmc.pmc_id, "Admin 2")
    change_role(conn, pmc.pmc_id, second, "admin", by=admin)
    with psycopg.connect(database_url) as first:
        change_role(first, pmc.pmc_id, second, "admin", granted=False, by=admin)
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: change_role(worker, pmc.pmc_id, admin, "admin", False, by=second),
        )
        wait_until_blocked(conn, pid)
        first.commit()
    thread.join()

    assert "Admin 2 may not change a role" in str(outcome["error"])
    assert roles_of(conn, pmc.pmc_id, admin) == {"admin"}
    assert roles_of(conn, pmc.pmc_id, second) == set()


def test_roles_are_append_only(conn, pmc, admin):
    for query in (
        "UPDATE trust_staff_roles SET granted = false",
        "DELETE FROM trust_staff_roles",
    ):
        with refused("append-only", psycopg.errors.RestrictViolation):
            conn.execute(query)


# --- paying out money ----------------------------------------------------------------------


@pytest.fixture
def payable(conn, pmc):
    """Owner 1's account holding 1000.00 and a 250.00 bill set aside from it."""
    owner = pmc.owners[0].account
    add_agreement(conn, pmc.pmc_id, owner, JAN_1, approval_limit="500")
    transfer_batch(conn, [(pmc.operating_cash, owner, "1000.00")], at(JAN_1))
    bill = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, owner, "INV-1", JAN_15, FEB_1, Decimal("250.00")
    )
    return bill


def paid(conn, bill):
    return conn.execute(
        "SELECT step FROM trust_bill_payments WHERE bill_id = %s ORDER BY created_at", (bill,)
    ).fetchall()


def test_paying_a_bill_takes_a_staff_member_who_may_disburse(conn, app_conn, pmc, payable, admin):
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    with acting_as(app_conn, clerk):
        # Setting it aside pays nobody yet.
        set_aside_bill(app_conn, pmc.pmc_id, payable, at(JAN_15))
        with not_allowed(r"Clerk 1 may not pay out money \(the disburse role\)"):
            pay_bill(app_conn, pmc.pmc_id, payable, at(JAN_15, 13))
    assert paid(conn, payable) == [("set_aside",)]

    change_role(conn, pmc.pmc_id, clerk, "disburse", by=admin)
    with acting_as(app_conn, clerk):
        pay_bill(app_conn, pmc.pmc_id, payable, at(JAN_15, 13))
    assert paid(conn, payable) == [("set_aside",), ("paid",)]


def test_an_owner_draw_takes_a_staff_member_who_may_disburse(conn, app_conn, pmc, payable, admin):
    owner = pmc.owners[0].account
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    give_roles(conn, pmc.pmc_id, clerk, ["disburse"])
    with acting_as(app_conn, clerk):
        draw_owner(app_conn, pmc.pmc_id, owner, "draw-1", "100.00", at(JAN_15))

    change_role(conn, pmc.pmc_id, clerk, "disburse", granted=False, by=admin)
    with acting_as(app_conn, clerk), not_allowed("Clerk 1 may not pay out money"):
        draw_owner(app_conn, pmc.pmc_id, owner, "draw-2", "100.00", at(JAN_15))
    # Another PMC's staff member is nobody here (the audit log refuses them).
    other = make_pmc(conn)
    with (
        acting_as(conn, admin_of(conn, other.pmc_id)),
        refused("is not a staff member of PMC", psycopg.errors.InvalidParameterValue),
    ):
        draw_owner(conn, pmc.pmc_id, owner, "draw-3", "100.00", at(JAN_15))

    assert conn.execute(
        "SELECT request_key FROM trust_owner_draws WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchall() == [("draw-1",)]


def test_with_nobody_named_money_is_not_held_to_a_role(conn, app_conn, pmc, payable):
    set_aside_bill(app_conn, pmc.pmc_id, payable, at(JAN_15))
    pay_bill(app_conn, pmc.pmc_id, payable, at(JAN_15, 13))
    draw_owner(app_conn, pmc.pmc_id, pmc.owners[0].account, "draw-1", None, at(JAN_15, 14))

    assert paid(conn, payable) == [("set_aside",), ("paid",)]


def test_a_reconciliation_takes_a_staff_member_who_may_approve(conn, pmc, admin):
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    other = make_pmc(conn)
    theirs = add_staff(conn, other.pmc_id, "Approver 9")
    give_roles(conn, other.pmc_id, theirs, ["approve"])
    for who, name in ((clerk, "Clerk 1"), (admin, "Admin 1"), (theirs, "Approver 9")):
        with acting_as(conn, who), not_allowed(f"{name} may not approve a reconciliation"):
            approve(conn, pmc.pmc_id, pmc.operating_bank_id, at(JAN_1, 0), at(FEB_1, 0))

    change_role(conn, pmc.pmc_id, clerk, "approve", by=admin)
    with acting_as(conn, clerk):
        approve(conn, pmc.pmc_id, pmc.operating_bank_id, at(JAN_1, 0), at(FEB_1, 0))


# --- staff logins --------------------------------------------------------------------------


def test_only_an_admin_invites_or_disables_a_staff_login(conn, app_conn, pmc, admin):
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    newcomer = add_staff(conn, pmc.pmc_id, "Staff 2")
    with acting_as(app_conn, clerk):
        with not_allowed("Clerk 1 may not invite a staff member"):
            accounts.create_invite(
                app_conn, pmc.pmc_id, "staff", newcomer, "staff2@example.com", clerk
            )
        # Owners and tenants are anyone's to invite.
        accounts.create_invite(
            app_conn, pmc.pmc_id, "owner", pmc.owners[0].owner_id, "owner1@example.com", clerk
        )
    with acting_as(app_conn, admin):
        accounts.create_invite(app_conn, pmc.pmc_id, "staff", newcomer, "staff2@example.com", admin)
    login = app_conn.execute(
        "SELECT id FROM trust_logins WHERE staff_id = %s", (newcomer,)
    ).fetchone()[0]
    owner_login = app_conn.execute(
        "SELECT id FROM trust_logins WHERE owner_id = %s", (pmc.owners[0].owner_id,)
    ).fetchone()[0]

    with acting_as(app_conn, clerk):
        with not_allowed("Clerk 1 may not disable a staff login"):
            accounts.disable_login(app_conn, pmc.pmc_id, login)
        accounts.disable_login(app_conn, pmc.pmc_id, owner_login)
    with acting_as(app_conn, admin):
        accounts.disable_login(app_conn, pmc.pmc_id, login)


# --- month-end: prepared by one, approved by another ---------------------------------------


@pytest.fixture
def january(conn, pmc, admin):
    """January's statement, its one line matched to the books, and a deposit in transit."""
    paid_in = post(
        conn,
        pmc.pmc_id,
        "rent-1",
        [(pmc.operating_cash, pmc.owners[0].account, "1500.00")],
        at(date(2026, 1, 5)),
    )[0]
    post(
        conn,
        pmc.pmc_id,
        "rent-2",
        [(pmc.operating_cash, pmc.owners[0].account, "700.00")],
        at(JAN_31),
    )
    _, (line,) = import_statement(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        admin,
        JAN_1,
        JAN_31,
        [(date(2026, 1, 6), "1500.00", "Deposit", None)],
    )
    match_lines(conn, pmc.pmc_id, pmc.operating_bank_id, [line], [paid_in], admin)


@pytest.fixture
def people(conn, pmc, admin):
    """A preparer who may approve too, and two approvers."""
    names = ("Preparer 1", "Approver 1", "Approver 2")
    staff = [add_staff(conn, pmc.pmc_id, name) for name in names]
    for staff_id in staff:
        change_role(conn, pmc.pmc_id, staff_id, "approve", by=admin)
    return staff


def test_month_end_is_prepared_by_one_staff_member_and_approved_by_another(
    conn, app_conn, pmc, january, people
):
    preparer, approver, _ = people
    prepared = prepare_bank(app_conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31, preparer)
    reconciliation = approve_prepared(app_conn, pmc.pmc_id, prepared, approver)

    assert conn.execute(
        "SELECT through, bank_balance, book_cash, prepared_by FROM"
        " trust_reconciliation_preparations WHERE id = %s",
        (prepared,),
    ).fetchone() == (JAN_31, Decimal("1500.00"), Decimal("2200.00"), preparer)
    assert conn.execute(
        "SELECT period_start, period_end, statement_balance, book_balance, prepared_by,"
        " approved_by FROM trust_reconciliations WHERE id = %s",
        (reconciliation,),
    ).fetchone() == (
        at(JAN_1, 0),
        at(FEB_1, 0),
        Decimal("1500.00"),
        Decimal("2200.00"),
        "Preparer 1",
        "Approver 1",
    )
    with refused("is approved through 2026-01-31 already"):
        approve_prepared(app_conn, pmc.pmc_id, prepared, people[2])


def test_whoever_prepared_it_does_not_approve_it(conn, app_conn, pmc, january, people, admin):
    preparer = people[0]
    prepared = prepare_bank(app_conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31, preparer)
    with not_allowed("who prepared a reconciliation does not approve it"):
        approve_prepared(app_conn, pmc.pmc_id, prepared, preparer)
    clerk = add_staff(conn, pmc.pmc_id, "Clerk 1")
    with not_allowed("Clerk 1 may not approve a reconciliation"):
        approve_prepared(app_conn, pmc.pmc_id, prepared, clerk)
    # Preparing takes no role.
    prepare_bank(app_conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31, clerk)

    assert conn.execute(
        "SELECT closed_through FROM trust_bank_accounts WHERE id = %s", (pmc.operating_bank_id,)
    ).fetchone() == (None,)


def test_preparing_and_approving_name_the_staff_member(conn, app_conn, pmc, january, people):
    with refused("name the staff member who prepares", psycopg.errors.InvalidParameterValue):
        prepare_bank(app_conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31, None)
    prepared = prepare_bank(app_conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31, people[0])
    with refused("name the staff member who approves", psycopg.errors.InvalidParameterValue):
        approve_prepared(app_conn, pmc.pmc_id, prepared, None)


def test_the_latest_preparation_is_the_one_approved(conn, app_conn, pmc, january, people):
    first = prepare_bank(app_conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31, people[0])
    latest = prepare_bank(app_conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31, people[2])

    with refused("was prepared again since; approve the latest"):
        approve_prepared(app_conn, pmc.pmc_id, first, people[1])
    approve_prepared(app_conn, pmc.pmc_id, latest, people[1])


def test_lines_the_books_lack_since_it_was_prepared_are_not_approved(
    conn, app_conn, pmc, january, people, admin
):
    prepared = prepare_bank(app_conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31, people[0])
    (match,) = conn.execute(
        "SELECT id FROM trust_bank_matches WHERE bank_account_id = %s", (pmc.operating_bank_id,)
    ).fetchone()
    unmatch_lines(conn, pmc.pmc_id, match, admin)  # same bank balance and books, a line unmatched

    with refused("come to 1500.00 that the books lack by the end of 2026-01-31"):
        approve_prepared(app_conn, pmc.pmc_id, prepared, people[1])


def test_figures_that_changed_since_they_were_prepared_are_not_approved(
    conn, app_conn, pmc, january, people
):
    prepared = prepare_bank(app_conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31, people[0])
    # A late posting in the open period: in transit, so nothing is missing, but the books moved.
    post(
        conn,
        pmc.pmc_id,
        "rent-3",
        [(pmc.operating_cash, pmc.owners[1].account, "40.00")],
        at(JAN_31),
    )

    with refused(
        "the books through 2026-01-31 changed since they were prepared: 2240.00 "
        "\\(prepared 2200.00\\)"
    ):
        approve_prepared(app_conn, pmc.pmc_id, prepared, people[1])


def test_lines_the_books_lack_are_refused_when_prepared(conn, app_conn, pmc, admin, people):
    import_statement(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        admin,
        JAN_1,
        JAN_31,
        [(date(2026, 1, 25), "-5.00", "Service fee", None)],
    )
    with refused("come to -5.00 that the books lack by the end of 2026-01-31"):
        prepare_bank(app_conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31, people[0])


def test_a_preparation_is_of_a_period_that_ended_and_follows_the_last(
    conn, app_conn, pmc, january, people
):
    with refused("has not ended yet", psycopg.errors.InvalidParameterValue):
        prepare_bank(app_conn, pmc.pmc_id, pmc.operating_bank_id, TODAY, people[0])
    prepared = prepare_bank(app_conn, pmc.pmc_id, pmc.operating_bank_id, JAN_15, people[0])
    approve_prepared(app_conn, pmc.pmc_id, prepared, people[1])

    for day in (date(2026, 1, 10), JAN_15):
        with refused(
            "the next period of trust bank account .* starts at 2026-01-16",
            psycopg.errors.InvalidParameterValue,
        ):
            prepare_bank(app_conn, pmc.pmc_id, pmc.operating_bank_id, day, people[0])


def test_a_preparation_is_of_the_pmcs_own_account(conn, app_conn, pmc, january, people):
    other = make_pmc(conn)
    # Even for a day before its next period: nothing is said of another PMC's account.
    for day in (JAN_31, date(2025, 12, 31)):
        with refused("no trust bank account", psycopg.errors.InvalidParameterValue):
            prepare_bank(app_conn, other.pmc_id, pmc.operating_bank_id, day, people[0])
    prepared = prepare_bank(app_conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31, people[0])
    with refused("no reconciliation prepared as", psycopg.errors.InvalidParameterValue):
        approve_prepared(app_conn, other.pmc_id, prepared, people[1])


def test_preparations_are_append_only_and_written_only_by_the_function(
    conn, app_conn, pmc, january, people
):
    prepare_bank(app_conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31, people[0])
    for query in (
        "UPDATE trust_reconciliation_preparations SET book_cash = 0",
        "DELETE FROM trust_reconciliation_preparations",
    ):
        with refused("append-only", psycopg.errors.RestrictViolation):
            conn.execute(query)
    with refused("permission denied", psycopg.errors.InsufficientPrivilege):
        app_conn.execute(
            "INSERT INTO trust_reconciliation_preparations (pmc_id, bank_account_id, through,"
            " bank_balance, book_cash, prepared_by) VALUES (%s, %s, %s, 0, 0, %s)",
            (pmc.pmc_id, pmc.operating_bank_id, JAN_31, people[0]),
        )


@pytest.mark.timing
def test_a_match_waits_for_an_approval_in_flight(conn, database_url, pmc, january, people, admin):
    late = post(
        conn,
        pmc.pmc_id,
        "rent-late",
        [(pmc.operating_cash, pmc.owners[1].account, "9.00")],
        at(FEB_1),
    )[0]
    _, (line,) = import_statement(
        conn,
        pmc.pmc_id,
        pmc.operating_bank_id,
        admin,
        FEB_1,
        FEB_28,
        [(FEB_1, "9.00", "Deposit", None)],
        opening="1500.00",
    )
    prepared = prepare_bank(conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31, people[0])
    with psycopg.connect(database_url) as approving:
        approve_prepared(approving, pmc.pmc_id, prepared, people[1])
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: match_lines(
                worker, pmc.pmc_id, pmc.operating_bank_id, [line], [late], admin
            ),
        )
        wait_until_blocked(conn, pid)
        approving.commit()
    thread.join()

    assert "error" not in outcome, outcome
    assert conn.execute(
        "SELECT closed_through FROM trust_bank_accounts WHERE id = %s", (pmc.operating_bank_id,)
    ).fetchone() == (at(FEB_1, 0),)


def test_the_ai_reads_roles_and_preparations_and_changes_nothing(
    conn, ai_conn, pmc, january, people
):
    prepared = prepare_bank(conn, pmc.pmc_id, pmc.operating_bank_id, JAN_31, people[0])
    assert ai_conn.execute(
        "SELECT count(*) FROM trust_staff_roles WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone() == (4,)
    for query, params in (
        ("SELECT trust_approve_bank_reconciliation(%s, %s)", (pmc.pmc_id, prepared)),
        (
            "SELECT trust_prepare_bank_reconciliation(%s, %s, %s)",
            (pmc.pmc_id, pmc.operating_bank_id, JAN_31),
        ),
        (
            "INSERT INTO trust_staff_roles (pmc_id, staff_id, role, granted)"
            " VALUES (%s, %s, 'admin', true)",
            (pmc.pmc_id, people[0]),
        ),
    ):
        with refused("permission denied", psycopg.errors.InsufficientPrivilege):
            ai_conn.execute(query, params)
