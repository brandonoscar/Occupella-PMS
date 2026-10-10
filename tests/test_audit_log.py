"""Staff and the audit log: every write to a trust record or the ledger is recorded with when,
what, through which database role and, when the application names one, which staff member
(migration 20261010000024).

The golden case under tests/golden/cases/ pins the report's layout; these check the rules.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from helpers import (
    NOT_FROM_AN_EXTENSION,
    acting_as,
    add_staff,
    add_unit,
    approve,
    charge,
    make_pmc,
    open_lease,
    post,
    transfer_batch,
)

JAN = datetime(2026, 1, 1, tzinfo=UTC)
FEB = datetime(2026, 2, 1, tzinfo=UTC)
# Not audited, each for a reason: the log itself; idempotency keys, written only with the
# transfers they record (which are); a balance and two entries change with every transfer, and
# the transfer row says it all.
NOT_AUDITED = {
    "trust_audit_log",
    "trust_idempotency_keys",
    "pgledger_accounts",
    "pgledger_entries",
    "schema_migrations",
}


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def log(conn, pmc, after=0):
    """(table, action, staff, role) of the PMC's audit rows past id `after`, in order."""
    return conn.execute(
        "SELECT table_name, action, staff_id, db_role FROM trust_audit_log"
        " WHERE pmc_id = %s AND id > %s ORDER BY id",
        (pmc.pmc_id, after),
    ).fetchall()


def last_id(conn):
    return conn.execute("SELECT coalesce(max(id), 0) FROM trust_audit_log").fetchone()[0]


def today():
    return datetime.now(UTC).date()


def report(conn, pmc, start=None, end=None):
    start = start or today() - timedelta(days=1)
    return conn.execute(
        "SELECT line, item, at, staff, db_role, action, record, detail"
        " FROM trust_report_audit_log(%s, %s, %s)",
        (pmc.pmc_id, start, end or today() + timedelta(days=1)),
    ).fetchall()


def test_every_table_with_trust_records_is_audited(conn):
    tables = {
        row[0]
        for row in conn.execute(
            f"""
            SELECT c.relname FROM pg_class c
            WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
              AND {NOT_FROM_AN_EXTENSION.format(catalog="'pg_class'", oid="c.oid")}
            """
        )
    }
    audited = {
        row[0]
        for row in conn.execute(
            "SELECT c.relname FROM pg_trigger g JOIN pg_class c ON c.oid = g.tgrelid"
            " WHERE g.tgname = 'trust_audit' AND g.tgfoid = 'trust_audit()'::regprocedure"
            # AFTER, for each row, on INSERT, DELETE and UPDATE (pg_trigger.tgtype bits).
            " AND g.tgtype & (1 | 2 | 4 | 8 | 16) = 1 | 4 | 8 | 16"
        )
    }

    assert audited == tables - NOT_AUDITED
    assert "pgledger_transfers" in audited


def test_a_posting_records_the_staff_member_and_the_role_behind_it(conn, app_conn, pmc):
    staff = add_staff(conn, pmc.pmc_id, "Bookkeeper 1")
    before = last_id(conn)

    with acting_as(app_conn, staff):
        (paid,) = post(
            app_conn, pmc.pmc_id, "rent", [(pmc.operating_cash, pmc.owners[0].account, "10.00")]
        )

    assert log(conn, pmc, before) == [("pgledger_transfers", "insert", staff, "trust_app")]
    row = conn.execute(
        "SELECT row_data ->> 'id', row_data ->> 'amount', before FROM trust_audit_log"
        " WHERE id > %s",
        (before,),
    ).fetchone()
    assert row == (paid, "10.00", None)


def test_a_write_path_that_runs_as_the_owner_records_its_caller(conn, app_conn, pmc):
    staff = add_staff(conn, pmc.pmc_id)
    before = last_id(conn)

    with acting_as(app_conn, staff):
        approve(app_conn, pmc.pmc_id, pmc.operating_bank_id, JAN, FEB)

    assert log(conn, pmc, before) == [
        ("trust_reconciliations", "insert", staff, "trust_app"),
        ("trust_bank_accounts", "update", staff, "trust_app"),
    ]


def test_a_write_with_no_staff_named_is_recorded_as_not_recorded(conn, app_conn, pmc):
    before = last_id(conn)

    add_unit(app_conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 9")

    assert log(conn, pmc, before) == [("trust_units", "insert", None, "trust_app")]
    assert report(conn, pmc)[-2][3:7] == ("not recorded", "trust_app", "insert", "units")


@pytest.mark.parametrize(
    ("whom", "error"),
    [
        ("staff of another PMC", psycopg.errors.InvalidParameterValue),
        ("no staff member", psycopg.errors.InvalidParameterValue),
        ("not an id", psycopg.errors.InvalidTextRepresentation),
    ],
)
def test_a_write_naming_someone_not_on_the_pmcs_staff_is_refused(conn, app_conn, pmc, whom, error):
    other = make_pmc(conn)
    staff = {
        "staff of another PMC": add_staff(conn, other.pmc_id),
        "no staff member": uuid.uuid4(),
        "not an id": "Bookkeeper 1",
    }[whom]
    before = last_id(conn)

    with acting_as(app_conn, staff), pytest.raises(error):
        add_unit(app_conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 9")

    assert last_id(conn) == before
    assert not conn.execute("SELECT 1 FROM trust_units WHERE pmc_id = %s", (pmc.pmc_id,)).fetchall()


def test_staff_named_for_one_transaction_only(conn, database_url, pmc):
    staff = add_staff(conn, pmc.pmc_id)
    before = last_id(conn)

    with psycopg.connect(database_url) as session:
        session.execute("SELECT set_config('trust.staff_id', %s, true)", (str(staff),))
        add_unit(session, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
        session.commit()
        add_unit(session, pmc.pmc_id, pmc.owners[0].property_id, "Unit 2")
        session.commit()

    assert [row[2] for row in log(conn, pmc, before)] == [staff, None]


def test_an_update_keeps_the_row_before_and_after(conn, app_conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    before = last_id(conn)

    app_conn.execute("UPDATE trust_units SET display_name = 'Unit 1A' WHERE id = %s", (unit,))

    row = conn.execute(
        "SELECT action, before ->> 'display_name', row_data ->> 'display_name'"
        " FROM trust_audit_log WHERE id > %s",
        (before,),
    ).fetchone()
    assert row == ("update", "Unit 1", "Unit 1A")
    assert report(conn, pmc)[-2][5:] == ("update", "units", "display_name: Unit 1 to Unit 1A")


def test_a_delete_is_recorded_too(conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id, "Unit 1")
    before = last_id(conn)

    conn.execute("DELETE FROM trust_units WHERE id = %s", (unit,))

    assert log(conn, pmc, before) == [("trust_units", "delete", None, "postgres")]


def test_times_in_the_log_are_utc_whatever_the_writers_time_zone(conn, database_url, pmc):
    with psycopg.connect(database_url, autocommit=True) as session:
        session.execute("SET TIME ZONE 'Pacific/Auckland'")
        transfer_batch(session, [(pmc.operating_cash, pmc.owners[0].account, "1.00")], JAN)

    event_at = conn.execute(
        "SELECT row_data ->> 'event_at' FROM trust_audit_log"
        " WHERE pmc_id = %s AND table_name = 'pgledger_transfers'",
        (pmc.pmc_id,),
    ).fetchone()[0]
    assert event_at == "2026-01-01T00:00:00+00:00"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE trust_audit_log SET db_role = 'nobody'",
        "DELETE FROM trust_audit_log",
        "TRUNCATE trust_audit_log",
    ],
)
def test_the_log_never_changes_even_for_the_owner(conn, pmc, statement):
    with pytest.raises(psycopg.errors.Error, match="append-only|history"):
        conn.execute(statement)


def test_only_the_trigger_writes_the_log(conn, app_conn, pmc):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute(
            "INSERT INTO trust_audit_log (pmc_id, db_role, action, table_name, row_data)"
            " VALUES (%s, 'trust_app', 'insert', 'trust_units', '{}')",
            (pmc.pmc_id,),
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute("SELECT trust_audit()")


def test_every_transfer_has_exactly_one_audit_row(conn, app_conn, pmc):
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    lease = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "900.00", [tenant])
    charge(app_conn, pmc.pmc_id, lease, date(2026, 1, 1), "900.00")
    post(
        app_conn,
        pmc.pmc_id,
        "two",
        [
            (pmc.operating_cash, pmc.owners[0].account, "900.00"),
            (pmc.deposit_cash, pmc.tenant_deposit, "900.00"),
        ],
    )

    unaudited = conn.execute(
        "SELECT count(*) FROM pgledger_transfers tr"
        " JOIN trust_ledger_accounts t ON t.ledger_account_id = tr.from_account_id"
        " WHERE t.pmc_id = %s AND (SELECT count(*) FROM trust_audit_log a"
        "   WHERE a.table_name = 'pgledger_transfers' AND a.row_data ->> 'id' = tr.id) <> 1",
        (pmc.pmc_id,),
    ).fetchone()[0]
    assert unaudited == 0
    assert ("trust_leases", "insert", None, "postgres") in log(conn, pmc)
    assert ("trust_lease_tenants", "insert", None, "postgres") in log(conn, pmc)


# --- the report ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end"),
    [(None, date(2026, 1, 31)), (date(2026, 1, 1), None), (date(2026, 1, 2), date(2026, 1, 1))],
)
def test_the_report_needs_a_period_that_runs_forward(conn, pmc, start, end):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="runs from a day"):
        conn.execute("SELECT * FROM trust_report_audit_log(%s, %s, %s)", (pmc.pmc_id, start, end))


def test_the_report_refuses_an_unknown_pmc(conn):
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no PMC"):
        conn.execute(
            "SELECT * FROM trust_report_audit_log(%s, %s, %s)", (uuid.uuid4(), today(), today())
        )


def test_the_report_lists_each_change_with_who_made_it(conn, app_conn, pmc):
    staff = add_staff(conn, pmc.pmc_id, "Bookkeeper 1")
    before = last_id(conn)
    with acting_as(app_conn, staff):
        post(
            app_conn,
            pmc.pmc_id,
            "rent",
            [(pmc.operating_cash, pmc.owners[0].account, "1500.00")],
            JAN,
            {"memo": "January rent"},
        )

    rows = report(conn, pmc)
    changes = [r for r in rows if r[1] == "change"]

    assert [row[0] for row in rows] == list(range(1, len(rows) + 1))
    assert rows[0][1] == "PMC" and rows[1][1] == "period" and rows[-1][1] == "total"
    assert rows[-1][-1] == f"{len(changes)} changes"
    assert changes[-1][3:] == (
        "Bookkeeper 1",
        "trust_app",
        "insert",
        "transfers",
        "1500.00 from bank_cash: Synthetic operating trust account to owner_property:"
        " Owner 1 / Property 1 dated 2026-01-01T00:00:00+00:00 - January rent",
    )
    count = conn.execute(
        "SELECT count(*) FROM trust_audit_log WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]
    assert len(changes) == count and before < last_id(conn)


def test_the_report_holds_the_period_and_no_other_pmcs_changes(conn, pmc):
    other = make_pmc(conn)
    add_unit(conn, other.pmc_id, other.owners[0].property_id)

    assert report(conn, pmc, today() + timedelta(days=1), today() + timedelta(days=3))[2:] == [
        (3, "total", None, None, None, None, None, "0 changes")
    ]
    assert (
        len(report(conn, pmc))
        == 3
        + conn.execute(
            "SELECT count(*) FROM trust_audit_log WHERE pmc_id = %s", (pmc.pmc_id,)
        ).fetchone()[0]
    )


def test_the_report_reads_the_same_in_any_time_zone_and_for_every_role(
    conn, app_conn, ai_conn, database_url, pmc
):
    readings = [report(app_conn, pmc), report(ai_conn, pmc)]
    for zone in ("UTC", "America/Los_Angeles", "Pacific/Auckland"):
        with psycopg.connect(database_url, autocommit=True) as session:
            session.execute(f"SET TIME ZONE '{zone}'")
            readings.append(report(session, pmc))

    assert all(reading == readings[0] for reading in readings)
