"""The chart of accounts in the database (migrations 20261106000200 to 20261106000230): every
account's code, unique among a company's codes today and given the next free one of its range
as an account is added; renaming and archiving expense accounts and charge types, a new row each
time; and archiving refused while an open bill or a running monthly charge uses one.
"""

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from helpers import (
    add_recurring_charge,
    add_unit,
    approve_bill,
    decline_bill,
    enter_bill,
    make_pmc,
    open_lease,
    pay_bill,
    post,
    set_aside_bill,
    stop_recurring_charge,
)

from tools.check_rollback import Dbmate
from tools.scratch_db import scratch_database

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(UTC)
TODAY = NOW.date()
DAY = timedelta(days=1)


def one(conn, query, params=()):
    return conn.execute(query, params).fetchone()[0]


def charge_type(conn, pmc_id, name):
    return one(
        conn,
        "INSERT INTO trust_charge_types (pmc_id, name) VALUES (%s, %s) RETURNING id",
        (pmc_id, name),
    )


def expense_account(conn, pmc_id, name, line):
    return one(
        conn,
        "INSERT INTO trust_expense_accounts (pmc_id, name, schedule_e_line)"
        " VALUES (%s, %s, %s) RETURNING id",
        (pmc_id, name, line),
    )


def codes(conn, pmc_id):
    """Each account's code today: fixed lines by their key, the rest by id."""
    return {
        line or charge or expense: code
        for line, charge, expense, code in conn.execute(
            "SELECT * FROM trust_account_codes_of(%s)", (pmc_id,)
        ).fetchall()
    }


def give(conn, pmc_id, code, line=None, charge=None, expense=None):
    conn.execute(
        "INSERT INTO trust_account_codes (pmc_id, line, charge_type_id, expense_account_id, code)"
        " VALUES (%s, %s, %s, %s, %s)",
        (pmc_id, line, charge, expense, code),
    )


def report(conn, pmc_id):
    return conn.execute(
        "SELECT section, code, name, account_type, schedule_e, kept_by, status"
        " FROM trust_report_chart_of_accounts(%s)",
        (pmc_id,),
    ).fetchall()


# --- codes ------------------------------------------------------------------------------------


def test_every_fixed_line_starts_with_its_code(conn):
    pmc = make_pmc(conn)

    assert {k: v for k, v in codes(conn, pmc.pmc_id).items() if isinstance(k, str)} == {
        "bank_cash": "1000",
        "owner_property": "2000",
        "tenant_deposit": "2100",
        "pet_deposit": "2110",
        "key_deposit": "2120",
        "prepaid_rent": "2200",
        "vendor_payable": "2300",
        "pmc_income": "2400",
        # The second pass's (migration 20261106000243).
        "owner_held_deposits": "2150",
        "unapplied_cash": "2250",
        "concessions": "4010",
        "rent": "4000",
        "recurring_charges": "4100",
        "late_fees": "4200",
        "nsf_fees": "4300",
        "other_charges": "4400",
        "unassigned_bills": "5000",
        "leasing_fees": "5800",
        "management_fees": "6100",
        "deposit_interest": "6900",
    }


def test_an_account_added_takes_the_next_free_code_of_its_range(conn):
    pmc = make_pmc(conn).pmc_id
    first = charge_type(conn, pmc, "Pet rent")
    second = charge_type(conn, pmc, "Parking")
    plumbing = expense_account(conn, pmc, "Plumbing", 14)
    electric = expense_account(conn, pmc, "Electrical", 14)
    ads = expense_account(conn, pmc, "Ads", 5)
    give(conn, pmc, "4503", charge=second)  # staff leave a gap
    third = charge_type(conn, pmc, "Admin fee")

    found = codes(conn, pmc)

    assert [found[x] for x in (first, second, third)] == ["4501", "4503", "4502"]
    assert [found[x] for x in (plumbing, electric, ads)] == ["6401", "6402", "5501"]


def test_a_full_block_gives_the_next_free_expense_code(conn):
    pmc = make_pmc(conn).pmc_id
    accounts = [expense_account(conn, pmc, f"Repair {n}", 14) for n in range(99)]
    spill = expense_account(conn, pmc, "Repair 100", 14)

    found = codes(conn, pmc)

    assert sorted(found[a] for a in accounts) == [str(6401 + n) for n in range(99)]
    assert found[spill] == "5001"


def test_codes_are_unique_in_a_company_but_not_across_companies(conn):
    mine, theirs = make_pmc(conn).pmc_id, make_pmc(conn).pmc_id
    pets = charge_type(conn, mine, "Pet rent")
    charge_type(conn, theirs, "Pet rent")
    give(conn, theirs, "4001", line="rent")

    with pytest.raises(psycopg.errors.UniqueViolation, match="code 4000 is Rent's already"):
        give(conn, mine, "4000", charge=pets)
    with pytest.raises(psycopg.errors.UniqueViolation, match="code 4501 is Pet rent's already"):
        give(conn, mine, "4501", line="rent")
    # Another company's codes are its own; and a code given up is free again.
    give(conn, mine, "4001", line="other_charges")
    give(conn, mine, "4002", line="other_charges")
    give(conn, mine, "4001", charge=pets)

    assert codes(conn, mine)["other_charges"] == "4002"
    assert codes(conn, mine)[pets] == "4001"
    assert codes(conn, theirs)["rent"] == "4001"


@pytest.mark.parametrize(
    ("line", "code", "why"),
    [
        ("bank_cash", "2000", "an asset account's code is from 1000 to 1999, not 2000"),
        ("prepaid_rent", "1100", "a liability account's code is from 2000 to 2999, not 1100"),
        ("rent", "5100", "an income account's code is from 4000 to 4999, not 5100"),
        ("management_fees", "7000", "an expense account's code is from 5000 to 6999, not 7000"),
        ("rent", "4000", "its code is 4000 already"),
    ],
)
def test_a_code_outside_its_section_or_unchanged_is_refused(conn, line, code, why):
    pmc = make_pmc(conn).pmc_id

    with pytest.raises(psycopg.errors.CheckViolation, match=why):
        give(conn, pmc, code, line=line)


@pytest.mark.parametrize(
    ("line", "code", "why"),
    [
        ("owner_property", "2150", r"code 2150 is Owner-held deposits \(memo\)'s already"),
        ("prepaid_rent", "2250", "code 2250 is Unapplied cash's already"),
        ("rent", "4010", "code 4010 is Concessions's already"),
    ],
)
def test_the_codes_kept_for_the_second_pass_are_its_lines_now(conn, line, code, why):
    # Kept for later in the first pass, they are owner-held deposits', unapplied cash's and
    # concessions' since migration 20261106000243: another account takes one as any code taken.
    pmc = make_pmc(conn).pmc_id

    with pytest.raises(psycopg.errors.UniqueViolation, match=why):
        give(conn, pmc, code, line=line)


@pytest.mark.parametrize("code", ["999", "10000", "0123", "41a0", ""])
def test_a_code_is_four_digits(conn, code):
    pmc = make_pmc(conn).pmc_id

    with pytest.raises(psycopg.errors.CheckViolation):
        give(conn, pmc, code, line="rent")


def test_a_code_changed_leaves_every_transfer_and_entry_as_it_was(conn):
    pmc = make_pmc(conn)
    post(
        conn,
        pmc.pmc_id,
        f"rent-{uuid.uuid4()}",
        [(pmc.operating_cash, pmc.owners[0].account, Decimal("900.00"))],
    )
    post(
        conn,
        pmc.pmc_id,
        f"fee-{uuid.uuid4()}",
        [(pmc.owners[0].account, pmc.pmc_income, Decimal("90.00"))],
    )
    pets = charge_type(conn, pmc.pmc_id, "Pet rent")
    repairs = expense_account(conn, pmc.pmc_id, "Repairs", 14)
    ledger = """
        SELECT (SELECT array_agg(t ORDER BY t.id) FROM pgledger_transfers AS t),
               (SELECT array_agg(e ORDER BY e.id) FROM pgledger_entries AS e),
               (SELECT array_agg(a ORDER BY a.id) FROM pgledger_accounts AS a),
               (SELECT array_agg(l ORDER BY l.ledger_account_id) FROM trust_ledger_accounts AS l)
    """
    before = conn.execute(ledger).fetchone()

    for line, code in (("bank_cash", "1010"), ("owner_property", "2010"), ("pmc_income", "2410")):
        give(conn, pmc.pmc_id, code, line=line)
    give(conn, pmc.pmc_id, "4599", charge=pets)
    give(conn, pmc.pmc_id, "6499", expense=repairs)

    assert conn.execute(ledger).fetchone() == before
    # The old codes are kept as rows of their own; the latest holds.
    assert conn.execute(
        "SELECT array_agg(code ORDER BY id) FROM trust_account_codes WHERE line = 'bank_cash'"
        " AND pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchone() == (["1010"],)
    assert codes(conn, pmc.pmc_id)["bank_cash"] == "1010"


def test_codes_are_never_rewritten(conn):
    pmc = make_pmc(conn).pmc_id
    charge_type(conn, pmc, "Pet rent")

    for statement in (
        "UPDATE trust_account_codes SET code = '4999'",
        "DELETE FROM trust_account_codes",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(statement)


def test_the_app_names_who_gave_a_code(conn, app_conn):
    pmc = make_pmc(conn).pmc_id
    staff = one(
        conn,
        "INSERT INTO trust_staff (pmc_id, display_name) VALUES (%s, 'Staff 1') RETURNING id",
        (pmc,),
    )
    with app_conn.transaction():
        app_conn.execute("SELECT set_config('trust.staff_id', %s, true)", (str(staff),))
        give(app_conn, pmc, "4005", line="rent")

    assert conn.execute(
        "SELECT recorded_by FROM trust_account_codes WHERE pmc_id = %s AND line = 'rent'", (pmc,)
    ).fetchone() == (staff,)


# --- names and archiving ----------------------------------------------------------------------


def test_a_name_is_once_among_the_names_today_whatever_its_case(conn):
    pmc = make_pmc(conn).pmc_id
    ads = expense_account(conn, pmc, "Ads", 5)
    pets = charge_type(conn, pmc, "Pets")

    conn.execute(
        "INSERT INTO trust_expense_account_names (pmc_id, expense_account_id, name)"
        " VALUES (%s, %s, 'Advertising')",
        (pmc, ads),
    )
    conn.execute(
        "INSERT INTO trust_charge_type_names (pmc_id, charge_type_id, name)"
        " VALUES (%s, %s, 'Pet rent')",
        (pmc, pets),
    )
    # The names they had are free again; the names they have are not, whatever the case.
    expense_account(conn, pmc, "Ads", 5)
    charge_type(conn, pmc, "Pets")
    with pytest.raises(psycopg.errors.UniqueViolation, match="expense account named ADVERTISING"):
        expense_account(conn, pmc, "ADVERTISING", 5)
    with pytest.raises(psycopg.errors.UniqueViolation, match="charge type named pet rent"):
        charge_type(conn, pmc, "pet rent")
    with pytest.raises(psycopg.errors.CheckViolation, match="named Advertising already"):
        conn.execute(
            "INSERT INTO trust_expense_account_names (pmc_id, expense_account_id, name)"
            " VALUES (%s, %s, 'Advertising')",
            (pmc, ads),
        )
    # A charge type is never named as one of the income statement's own lines.
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_charge_type_names (pmc_id, charge_type_id, name)"
            " VALUES (%s, %s, 'Late fees')",
            (pmc, pets),
        )

    assert one(conn, "SELECT trust_expense_account_name(%s)", (ads,)) == "Advertising"
    assert one(conn, "SELECT trust_charge_type_name(%s)", (pets,)) == "Pet rent"


def bill_in(conn, pmc, account, reference, amount="100.00", split=None):
    """A bill of the first owner's in an expense account, maybe split with another."""
    bill = enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        pmc.owners[0].account,
        reference,
        TODAY - 2 * DAY,
        TODAY,
        amount,
    )
    head = one(
        conn,
        "INSERT INTO trust_bill_expense_accounts (pmc_id, bill_id, expense_account_id)"
        " VALUES (%s, %s, %s) RETURNING id",
        (pmc.pmc_id, bill, account),
    )
    if split is not None:
        with conn.transaction():
            for each, part in (
                (account, Decimal(amount) - Decimal("10.00")),
                (split, Decimal("10.00")),
            ):
                conn.execute(
                    "INSERT INTO trust_bill_splits"
                    " (pmc_id, bill_id, categorized_id, expense_account_id, amount)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (pmc.pmc_id, bill, head, each, part),
                )
    return bill


def archive(conn, pmc_id, expense=None, charge=None, archived=True):
    if expense is not None:
        conn.execute(
            "INSERT INTO trust_expense_account_archivals (pmc_id, expense_account_id, archived)"
            " VALUES (%s, %s, %s)",
            (pmc_id, expense, archived),
        )
    else:
        conn.execute(
            "INSERT INTO trust_charge_type_archivals (pmc_id, charge_type_id, archived)"
            " VALUES (%s, %s, %s)",
            (pmc_id, charge, archived),
        )


def test_an_expense_account_is_archived_only_once_no_open_bill_is_in_it(conn):
    pmc = make_pmc(conn)
    repairs = expense_account(conn, pmc.pmc_id, "Repairs", 14)
    water = expense_account(conn, pmc.pmc_id, "Water", 17)
    post(
        conn,
        pmc.pmc_id,
        f"fund-{uuid.uuid4()}",
        [(pmc.operating_cash, pmc.owners[0].account, Decimal("1000.00"))],
    )
    paid = bill_in(conn, pmc, repairs, "R-1")
    open_one = bill_in(conn, pmc, repairs, "R-2", split=water)
    approve_bill(conn, pmc.pmc_id, paid, TODAY, "Owner 1")
    set_aside_bill(conn, pmc.pmc_id, paid, NOW)
    pay_bill(conn, pmc.pmc_id, paid, NOW)

    # Open: in Repairs by its latest row, and in Water by a split line of it.
    for account, name in ((repairs, "Repairs"), (water, "Water")):
        with pytest.raises(
            psycopg.errors.CheckViolation, match=f"{name} has a bill not paid in it"
        ):
            archive(conn, pmc.pmc_id, expense=account)
    # Moved out of both, the bill no longer holds either; a paid one never does.
    other = expense_account(conn, pmc.pmc_id, "Other", 19)
    conn.execute(
        "INSERT INTO trust_bill_expense_accounts (pmc_id, bill_id, expense_account_id)"
        " VALUES (%s, %s, %s)",
        (pmc.pmc_id, open_one, other),
    )
    archive(conn, pmc.pmc_id, expense=repairs)
    archive(conn, pmc.pmc_id, expense=water)
    with pytest.raises(psycopg.errors.CheckViolation, match="Other has a bill not paid in it"):
        archive(conn, pmc.pmc_id, expense=other)
    # Declined, it holds nothing either.
    decline_bill(conn, pmc.pmc_id, open_one, TODAY, "Owner 1", "Not ours")
    archive(conn, pmc.pmc_id, expense=other)
    with pytest.raises(psycopg.errors.CheckViolation, match="Other is archived already"):
        archive(conn, pmc.pmc_id, expense=other)
    # Brought back, always.
    archive(conn, pmc.pmc_id, expense=repairs, archived=False)

    assert [
        one(conn, "SELECT trust_expense_account_archived(%s)", (x,))
        for x in (repairs, water, other)
    ] == [False, True, True]


def test_an_archived_expense_account_takes_no_new_bill_but_keeps_its_past(conn):
    pmc = make_pmc(conn)
    repairs = expense_account(conn, pmc.pmc_id, "Repairs", 14)
    water = expense_account(conn, pmc.pmc_id, "Water", 17)
    post(
        conn,
        pmc.pmc_id,
        f"fund-{uuid.uuid4()}",
        [(pmc.operating_cash, pmc.owners[0].account, Decimal("1000.00"))],
    )
    paid = bill_in(conn, pmc, water, "W-1", "40.00")
    approve_bill(conn, pmc.pmc_id, paid, TODAY, "Owner 1")
    set_aside_bill(conn, pmc.pmc_id, paid, NOW)
    pay_bill(conn, pmc.pmc_id, paid, NOW)
    archive(conn, pmc.pmc_id, expense=water)
    later = enter_bill(
        conn, pmc.pmc_id, pmc.vendor_id, pmc.owners[0].account, "W-2", TODAY - DAY, TODAY, "50.00"
    )

    with pytest.raises(psycopg.errors.CheckViolation, match="Water is archived"):
        conn.execute(
            "INSERT INTO trust_bill_expense_accounts (pmc_id, bill_id, expense_account_id)"
            " VALUES (%s, %s, %s)",
            (pmc.pmc_id, later, water),
        )
    with pytest.raises(psycopg.errors.CheckViolation, match="Water is archived"):
        bill_in(conn, pmc, repairs, "R-9", split=water)
    statement = conn.execute(
        "SELECT item, amount FROM trust_report_income_statement(%s, %s, %s, %s)"
        " WHERE property IS NOT NULL AND item LIKE 'expense: %%'",
        (pmc.pmc_id, pmc.owners[0].owner_id, TODAY - 30 * DAY, TODAY),
    ).fetchall()

    # Still on the report for the days it had money, and on the chart as archived.
    assert ("expense: Water", Decimal("40.00")) in statement
    assert ("Expenses", "6701", "Water", "Expense account", 17, None, "archived") in report(
        conn, pmc.pmc_id
    )


def test_a_charge_type_is_archived_only_once_no_monthly_charge_of_it_runs(conn):
    pmc = make_pmc(conn)
    tenant = one(
        conn,
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    )
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, TODAY - 40 * DAY, None, "1000.00", [tenant])
    parking = charge_type(conn, pmc.pmc_id, "Parking")
    # A monthly charge of no type holds no type back.
    add_recurring_charge(conn, pmc.pmc_id, lease, "25.00", TODAY - 40 * DAY, "Space 4")
    typed = one(
        conn,
        "INSERT INTO trust_recurring_charges (pmc_id, lease_id, kind, amount, memo, starts_on,"
        " charge_type_id) VALUES (%s, %s, 'fee', 30.00, 'Space 5', %s, %s) RETURNING id",
        (pmc.pmc_id, lease, TODAY - 40 * DAY, parking),
    )

    with pytest.raises(
        psycopg.errors.CheckViolation, match="Parking has a monthly charge still running"
    ):
        archive(conn, pmc.pmc_id, charge=parking)
    stop_recurring_charge(conn, pmc.pmc_id, typed, TODAY)
    archive(conn, pmc.pmc_id, charge=parking)

    # Archived, no new fee or monthly charge takes it.
    with pytest.raises(psycopg.errors.CheckViolation, match="Parking is archived"):
        conn.execute(
            "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo,"
            " charge_type_id) VALUES (%s, %s, %s, 'fee', 10.00, 'Visitor', %s)",
            (pmc.pmc_id, lease, TODAY, parking),
        )
    with pytest.raises(psycopg.errors.CheckViolation, match="Parking is archived"):
        conn.execute(
            "INSERT INTO trust_recurring_charges (pmc_id, lease_id, kind, amount, memo, starts_on,"
            " charge_type_id) VALUES (%s, %s, 'fee', 30.00, 'Space 6', %s, %s)",
            (pmc.pmc_id, lease, TODAY, parking),
        )
    archive(conn, pmc.pmc_id, charge=parking, archived=False)
    conn.execute(
        "INSERT INTO trust_charges (pmc_id, lease_id, due_on, kind, amount, memo, charge_type_id)"
        " VALUES (%s, %s, %s, 'fee', 10.00, 'Visitor', %s)",
        (pmc.pmc_id, lease, TODAY, parking),
    )


def test_archiving_and_names_are_never_rewritten(conn):
    pmc = make_pmc(conn).pmc_id
    ads = expense_account(conn, pmc, "Ads", 5)
    archive(conn, pmc, expense=ads)

    for table in (
        "trust_expense_account_archivals",
        "trust_expense_account_names",
        "trust_charge_type_archivals",
        "trust_charge_type_names",
        "trust_charge_type_payees",
    ):
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            conn.execute(f"DELETE FROM {table}")  # noqa: S608


# --- the migration on a database with books in it ---------------------------------------------


def test_the_migration_gives_every_account_a_company_has_a_code(database_url, tmp_path):
    migrations = sorted((ROOT / "db/migrations").glob("*.sql"))
    codes_migration = next(m for m in migrations if m.name.startswith("20261106000200"))
    for migration in migrations[: migrations.index(codes_migration)]:
        shutil.copy(migration, tmp_path)

    with scratch_database(database_url, "pms_chart_codes") as url:
        dbmate = Dbmate(shutil.which("dbmate"), tmp_path, url)
        assert dbmate.run("--no-dump-schema", "up").returncode == 0
        with psycopg.connect(url, autocommit=True) as conn:
            # Two companies with books: money moved, charge types (one named as a starting fee
            # type is, whatever its case) and expense accounts on many lines.
            books = [make_pmc(conn), make_pmc(conn)]
            for pmc in books:
                post(
                    conn,
                    pmc.pmc_id,
                    f"rent-{uuid.uuid4()}",
                    [(pmc.operating_cash, pmc.owners[0].account, Decimal("1200.00"))],
                )
                for name in ("Application fee", "PET RENT", "Cleaning"):
                    charge_type(conn, pmc.pmc_id, name)
                for name, line in (("Repairs", 14), ("Plumbing", 14), ("Water", 17), ("Ads", 5)):
                    expense_account(conn, pmc.pmc_id, name, line)
            ledger = conn.execute(
                "SELECT array_agg(t ORDER BY t.id) FROM pgledger_transfers AS t"
            ).fetchone()
            entries = conn.execute(
                "SELECT array_agg(e ORDER BY e.id) FROM pgledger_entries AS e"
            ).fetchone()
            balances = conn.execute(
                "SELECT array_agg((a.id, a.balance, a.version) ORDER BY a.id)"
                " FROM pgledger_accounts AS a"
            ).fetchone()
        for migration in migrations[migrations.index(codes_migration) :]:
            shutil.copy(migration, tmp_path)

        applied = dbmate.run("--no-dump-schema", "up")
        assert applied.returncode == 0, applied.stderr

        with psycopg.connect(url, autocommit=True) as conn:
            # No transfer, entry or balance changed, through the last migration of the pass.
            assert (
                conn.execute(
                    "SELECT array_agg(t ORDER BY t.id) FROM pgledger_transfers AS t"
                ).fetchone()
                == ledger
            )
            assert (
                conn.execute(
                    "SELECT array_agg(e ORDER BY e.id) FROM pgledger_entries AS e"
                ).fetchone()
                == entries
            )
            assert (
                conn.execute(
                    "SELECT array_agg((a.id, a.balance, a.version) ORDER BY a.id)"
                    " FROM pgledger_accounts AS a"
                ).fetchone()
                == balances
            )
            for pmc in books:
                rows = conn.execute(
                    "SELECT section, code, name, kept_by FROM trust_report_chart_of_accounts(%s)",
                    (pmc.pmc_id,),
                ).fetchall()
                found = {name: code for _, code, name, _ in rows}
                # Every account has a code, each once.
                assert all(code is not None for _, code, _, _ in rows)
                assert len({code for _, code, _, _ in rows}) == len(rows)
                # The company's own first, oldest first; the starting fee types after them, but
                # Pet rent, which it has already in its own spelling.
                assert [found[n] for n in ("Application fee", "PET RENT", "Cleaning")] == [
                    "4501",
                    "4502",
                    "4503",
                ]
                assert "Pet rent" not in found
                assert found["Housing assistance rent"] == "4504"
                assert found["Deposit forfeited to owner"] == "4514"
                assert {n: k for _, _, n, k in rows if n in ("Admin fee", "Lease break fee")} == {
                    "Admin fee": "company",
                    "Lease break fee": "owner",
                }
                assert [found[n] for n in ("Repairs", "Plumbing", "Water", "Ads")] == [
                    "6401",
                    "6402",
                    "6701",
                    "5501",
                ]
                # The fixed lines (twenty since the second pass: owner-held deposits 2150,
                # unapplied cash 2250 and concessions 4010), its three charge types, the eleven
                # starting kinds of fee it lacked and its four expense accounts.
                assert [found[n] for n in ("Owner-held deposits (memo)", "Unapplied cash")] == [
                    "2150",
                    "2250",
                ]
                assert found["Concessions"] == "4010"
                assert len(rows) == 20 + 3 + 11 + 4
