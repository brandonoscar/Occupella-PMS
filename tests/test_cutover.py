"""Cutover: a trust bank account opens with the balances it held in another system, first and
once, and nothing is dated before it (migration 20261010000022).

What tenants owe at the cutover stays out of the ledger: their unpaid charges are entered with
their own due dates. The last tests walk through that.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from helpers import (
    add_late_fee_policy,
    add_unit,
    apply_payment,
    approve,
    assess_late_fees,
    charge,
    in_background,
    make_pmc,
    open_lease,
    open_with_balances,
    post,
    snapshot,
    transfer_batch,
    wait_until_blocked,
)

CUTOVER = date(2026, 1, 1)
OPENED_AT = datetime(2026, 1, 1, tzinfo=UTC)
TICK = timedelta(microseconds=1)  # timestamptz's resolution
FEB = datetime(2026, 2, 1, tzinfo=UTC)
COMES_FIRST = "opening balances come first"
BEFORE_CUTOVER = "with balances carried over"


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def operating_balances(pmc):
    """What each account of the operating trust account held in the old system: 2525.00."""
    return {
        pmc.owners[0].account: "1200.00",
        pmc.owners[1].account: "800.00",
        pmc.prepaid_rent: "150.00",
        pmc.vendor_payable: "300.00",
        pmc.pmc_income: "75.00",
    }


def open_operating(conn, pmc, **changes):
    arguments = {
        "opened_on": CUTOVER,
        "balances": operating_balances(pmc),
        "book_cash": "2525.00",
        "entered_by": "Preparer 1",
        **changes,
    }
    return open_with_balances(conn, pmc.pmc_id, pmc.operating_bank_id, **arguments)


def trial_balance(conn, pmc, as_of):
    """(account, debit, credit) of each account line."""
    return [
        row[1:]
        for row in conn.execute(
            "SELECT item, account, debit, credit FROM trust_report_trial_balance(%s, %s)",
            (pmc.pmc_id, as_of),
        )
        if row[0] == "account"
    ]


def test_opening_balances_are_posted_from_cash_at_the_start_of_the_cutover_date(
    conn, app_conn, pmc
):
    transfers = open_operating(app_conn, pmc)

    rows = conn.execute(
        "SELECT t.id, t.from_account_id, t.to_account_id, t.amount, t.event_at,"
        " t.metadata ->> 'memo' FROM pgledger_transfers t WHERE t.id = ANY(%s)",
        (transfers,),
    ).fetchall()
    assert sorted((r[2], r[3]) for r in rows) == sorted(
        (account, Decimal(amount)) for account, amount in operating_balances(pmc).items()
    )
    assert {(r[1], r[4], r[5]) for r in rows} == {
        (pmc.operating_cash, OPENED_AT, "Opening balance")
    }
    # One transfer per account, in byte order of the account id.
    assert [r[2] for r in sorted(rows, key=lambda r: transfers.index(r[0]))] == sorted(
        operating_balances(pmc), key=lambda account: account.encode()
    )
    assert conn.execute(
        "SELECT balance FROM pgledger_accounts WHERE id = %s", (pmc.operating_cash,)
    ).fetchone()[0] == Decimal("-2525.00")


def test_the_opening_is_recorded_and_dates_the_trust_bank_account(conn, app_conn, pmc):
    transfers = open_operating(app_conn, pmc, entered_by="Preparer 2")

    record = conn.execute(
        "SELECT pmc_id, opened_on, balances, book_cash, entered_by, transfer_ids"
        " FROM trust_opening_balances WHERE bank_account_id = %s",
        (pmc.operating_bank_id,),
    ).fetchone()
    assert record == (
        pmc.pmc_id,
        CUTOVER,
        {account: float(amount) for account, amount in operating_balances(pmc).items()},
        Decimal("2525.00"),
        "Preparer 2",
        transfers,
    )
    opened_at = "SELECT opened_at FROM trust_bank_accounts WHERE id = %s"
    assert conn.execute(opened_at, (pmc.operating_bank_id,)).fetchone()[0] == OPENED_AT
    assert conn.execute(opened_at, (pmc.deposit_bank_id,)).fetchone()[0] is None


def test_the_trial_balance_shows_the_opening_from_the_cutover_date(conn, app_conn, pmc):
    open_operating(app_conn, pmc)

    assert trial_balance(conn, pmc, CUTOVER - timedelta(days=1)) == [
        ("book cash", Decimal("0.00"), Decimal("0.00")),
        ("book cash", Decimal("0.00"), Decimal("0.00")),
    ]
    opened = trial_balance(conn, pmc, CUTOVER)
    assert ("book cash", Decimal("2525.00"), Decimal("0.00")) in opened
    assert sum(line[2] for line in opened) == Decimal("2525.00")
    assert len(opened) == 2 + len(operating_balances(pmc))


def test_deposits_open_in_the_security_deposit_account(conn, app_conn, pmc):
    open_with_balances(
        app_conn, pmc.pmc_id, pmc.deposit_bank_id, CUTOVER, {pmc.tenant_deposit: "1500"}, "1500"
    )

    held = conn.execute(
        "SELECT item, held FROM trust_report_security_deposits(%s, %s)", (pmc.pmc_id, CUTOVER)
    ).fetchall()
    assert ("deposit", Decimal("1500.00")) in held
    assert ("book cash", Decimal("1500.00")) in held


@pytest.mark.parametrize(
    ("mistake", "change", "message"),
    [
        ("no date", {"opened_on": None}, "need the cutover date"),
        ("no book cash", {"book_cash": None}, "need the cutover date"),
        ("no one entered them", {"entered_by": ""}, "need the cutover date"),
        ("nobody named", {"entered_by": None}, "need the cutover date"),
        ("a list", {"balances": ["1200.00"]}, "a JSON object"),
        ("nothing", {"balances": {}}, "a JSON object"),
        ("a total that differs", {"book_cash": "2525.01"}, "add up to 2525.00, not"),
    ],
)
def test_an_opening_with_a_missing_or_wrong_part_is_refused(
    conn, app_conn, pmc, mistake, change, message
):
    before = snapshot(conn, pmc.accounts())

    with pytest.raises(psycopg.errors.Error, match=message):
        open_operating(app_conn, pmc, **change)

    assert snapshot(conn, pmc.accounts()) == before


@pytest.mark.parametrize(
    ("mistake", "amount", "message"),
    [
        ("zero", "0", "must be positive and in cents"),
        ("negative", "-5.00", "must be positive and in cents"),
        ("a fraction of a cent", "10.005", "must be positive and in cents"),
        ("no amount", None, "must be positive and in cents"),
        ("not a number", "ten", "invalid input syntax for type numeric"),
    ],
)
def test_each_balance_is_a_positive_amount_in_cents(conn, app_conn, pmc, mistake, amount, message):
    balances = {**operating_balances(pmc), pmc.owners[1].account: amount}

    with pytest.raises(psycopg.errors.Error, match=message):
        open_operating(app_conn, pmc, balances=balances, book_cash="1725.00")

    assert not conn.execute(
        "SELECT 1 FROM trust_opening_balances WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchall()


def test_a_number_and_its_text_are_the_same_balance(conn, app_conn, pmc):
    balances = {pmc.owners[0].account: 1200, pmc.owners[1].account: "800.5"}

    open_operating(app_conn, pmc, balances=balances, book_cash="2000.50")

    assert conn.execute(
        "SELECT balance FROM pgledger_accounts WHERE id = %s", (pmc.owners[1].account,)
    ).fetchone()[0] == Decimal("800.5")


@pytest.mark.parametrize(
    "account",
    ["the cash account", "a deposit held in the other trust account", "no such account"],
)
def test_every_balance_is_of_an_account_held_in_the_trust_bank_account(
    conn, app_conn, pmc, account
):
    stray = {
        "the cash account": pmc.operating_cash,
        "a deposit held in the other trust account": pmc.tenant_deposit,
        "no such account": "acct_00000000000000000000000000",
    }[account]
    balances = {pmc.owners[0].account: "1200.00", stray: "5.00"}

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="is not an account held in"):
        open_operating(app_conn, pmc, balances=balances, book_cash="1205.00")


@pytest.mark.parametrize("mistake", ["unknown PMC", "another PMC's bank account"])
def test_the_trust_bank_account_must_be_the_pmcs(conn, app_conn, pmc, mistake):
    other = make_pmc(conn)
    pmc_id = uuid.uuid4() if mistake == "unknown PMC" else other.pmc_id

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="no trust bank account"):
        open_with_balances(
            app_conn, pmc_id, pmc.operating_bank_id, CUTOVER, operating_balances(pmc), "2525.00"
        )


def test_a_trust_bank_account_without_its_cash_account_cant_open(conn, app_conn, pmc):
    bank = conn.execute(
        "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
        " VALUES (%s, 'operating', 'Synthetic second operating account') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="open the cash account"):
        open_with_balances(app_conn, pmc.pmc_id, bank, CUTOVER, {"acct_x": "1.00"}, "1.00")


def test_a_cutover_date_still_to_come_is_refused_and_today_is_not(conn, app_conn, pmc):
    today = datetime.now(UTC).date()

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="has not come yet"):
        open_operating(app_conn, pmc, opened_on=today + timedelta(days=2))
    open_operating(app_conn, pmc, opened_on=today)


def test_the_same_opening_again_returns_the_original_transfers(conn, app_conn, pmc):
    first = open_operating(app_conn, pmc)
    before = snapshot(conn, pmc.accounts())

    # 1200.000 is the same balance as 1200.00.
    again = {account: amount + "0" for account, amount in operating_balances(pmc).items()}
    retried = open_operating(app_conn, pmc, balances=again)

    assert retried == first
    assert snapshot(conn, pmc.accounts()) == before


@pytest.mark.parametrize("change", ["another date", "someone else", "other balances"])
def test_a_different_opening_of_the_same_trust_bank_account_is_refused(conn, app_conn, pmc, change):
    open_operating(app_conn, pmc)
    before = snapshot(conn, pmc.accounts())
    arguments = {
        "another date": {"opened_on": CUTOVER - timedelta(days=1)},
        "someone else": {"entered_by": "Preparer 2"},
        "other balances": {
            "balances": {pmc.owners[0].account: "1300.00"},
            "book_cash": "1300.00",
        },
    }[change]

    with pytest.raises(psycopg.errors.UniqueViolation, match="already opened on 2026-01-01"):
        open_operating(app_conn, pmc, **arguments)

    assert snapshot(conn, pmc.accounts()) == before


def test_opening_balances_come_before_any_posting(conn, app_conn, pmc):
    post(app_conn, pmc.pmc_id, "rent", [(pmc.operating_cash, pmc.owners[0].account, "10.00")])

    with pytest.raises(psycopg.errors.CheckViolation, match=COMES_FIRST):
        open_operating(app_conn, pmc)

    # The other trust bank account has no postings and still opens.
    open_with_balances(
        app_conn, pmc.pmc_id, pmc.deposit_bank_id, CUTOVER, {pmc.tenant_deposit: "900"}, "900"
    )


def test_opening_balances_come_before_any_reconciliation(conn, app_conn, pmc):
    approve(app_conn, pmc.pmc_id, pmc.operating_bank_id, OPENED_AT - timedelta(days=31), OPENED_AT)

    with pytest.raises(psycopg.errors.CheckViolation, match=COMES_FIRST):
        open_operating(app_conn, pmc)


@pytest.mark.parametrize("role", ["owner", "app"])
def test_nothing_is_dated_before_the_cutover_once_opened(conn, app_conn, pmc, role):
    open_operating(app_conn, pmc)
    before = snapshot(conn, pmc.accounts())
    rent = [(pmc.operating_cash, pmc.owners[0].account, "10.00")]

    with pytest.raises(psycopg.errors.CheckViolation, match=BEFORE_CUTOVER):
        if role == "owner":
            transfer_batch(conn, rent, OPENED_AT - TICK)
        else:
            post(app_conn, pmc.pmc_id, "late", rent, event_at=OPENED_AT - TICK)

    assert snapshot(conn, pmc.accounts()) == before
    post(app_conn, pmc.pmc_id, "on time", rent, event_at=OPENED_AT)


def test_a_trust_bank_account_that_did_not_open_with_balances_takes_any_date(conn, app_conn, pmc):
    open_operating(app_conn, pmc)

    post(
        app_conn,
        pmc.pmc_id,
        "old deposit",
        [(pmc.deposit_cash, pmc.tenant_deposit, "900.00")],
        event_at=OPENED_AT - timedelta(days=400),
    )


def test_the_first_reconciliation_starts_at_the_cutover(conn, app_conn, pmc):
    open_operating(app_conn, pmc)

    with pytest.raises(psycopg.errors.InvalidParameterValue, match="starts at 2026-01-01"):
        approve(app_conn, pmc.pmc_id, pmc.operating_bank_id, OPENED_AT - TICK, FEB)

    approve(app_conn, pmc.pmc_id, pmc.operating_bank_id, OPENED_AT, FEB, "2525.00")
    book = conn.execute(
        "SELECT book_balance FROM trust_reconciliations WHERE bank_account_id = %s",
        (pmc.operating_bank_id,),
    ).fetchone()[0]
    assert book == Decimal("2525.00")
    # The next follows on from the first, as before.
    with pytest.raises(psycopg.errors.InvalidParameterValue, match="starts at 2026-02-01"):
        approve(app_conn, pmc.pmc_id, pmc.operating_bank_id, OPENED_AT, FEB + timedelta(days=28))


def test_no_role_sets_or_moves_the_cutover_by_hand(conn, app_conn, pmc):
    with pytest.raises(psycopg.errors.RestrictViolation, match="start of its cutover date"):
        conn.execute(
            "UPDATE trust_bank_accounts SET opened_at = %s WHERE id = %s",
            (OPENED_AT, pmc.operating_bank_id),
        )
    with pytest.raises(psycopg.errors.RestrictViolation, match="start of its cutover date"):
        conn.execute(
            "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name, opened_at)"
            " VALUES (%s, 'operating', 'Synthetic account', %s)",
            (pmc.pmc_id, OPENED_AT),
        )

    open_operating(app_conn, pmc)
    with pytest.raises(psycopg.errors.RestrictViolation, match="start of its cutover date"):
        conn.execute(
            "UPDATE trust_bank_accounts SET opened_at = NULL WHERE id = %s",
            (pmc.operating_bank_id,),
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute(
            "UPDATE trust_bank_accounts SET opened_at = NULL WHERE id = %s",
            (pmc.operating_bank_id,),
        )
    # Renaming the account leaves its cutover alone.
    conn.execute(
        "UPDATE trust_bank_accounts SET display_name = 'Synthetic renamed' WHERE id = %s",
        (pmc.operating_bank_id,),
    )


@pytest.mark.parametrize(
    "statement",
    ["UPDATE trust_opening_balances SET book_cash = 1", "DELETE FROM trust_opening_balances"],
)
def test_the_record_of_an_opening_never_changes(conn, app_conn, pmc, statement):
    open_operating(app_conn, pmc)

    with pytest.raises(psycopg.errors.Error, match="append-only|history"):
        conn.execute(statement)


def test_an_opening_is_posted_at_read_committed_only(database_url, pmc):
    with psycopg.connect(database_url) as session:
        session.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        with pytest.raises(psycopg.errors.InvalidTransactionState, match="READ COMMITTED"):
            open_operating(session, pmc)


@pytest.mark.timing
def test_a_posting_waits_for_an_opening_in_progress_and_is_then_refused(conn, database_url, pmc):
    rent = [(pmc.operating_cash, pmc.owners[0].account, "10.00")]

    with psycopg.connect(database_url) as opening:
        open_operating(opening, pmc)  # not committed yet
        thread, pid, outcome = in_background(
            database_url,
            lambda worker: post(worker, pmc.pmc_id, "late", rent, event_at=OPENED_AT - TICK),
        )
        wait_until_blocked(conn, pid)
        opening.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert BEFORE_CUTOVER in str(outcome["error"])


@pytest.mark.timing
def test_an_opening_waits_for_a_posting_in_flight_and_is_then_refused(conn, database_url, pmc):
    rent = [(pmc.operating_cash, pmc.owners[0].account, "10.00")]

    with psycopg.connect(database_url) as posting:
        transfer_batch(posting, rent, FEB)  # not committed yet
        thread, pid, outcome = in_background(
            database_url, lambda worker: open_operating(worker, pmc)
        )
        wait_until_blocked(conn, pid)
        posting.commit()
    thread.join(timeout=30)

    assert isinstance(outcome.get("error"), psycopg.errors.CheckViolation), outcome
    assert COMES_FIRST in str(outcome["error"])


def test_tenants_unpaid_charges_carry_over_with_their_own_due_dates(conn, app_conn, pmc):
    """The tenant owed December's rent at the cutover and had paid 150.00 ahead. Late fee terms
    start at the cutover, so the old system's rent gets no second late fee here."""
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.prepaid_rent,),
    ).fetchone()[0]
    lease = open_lease(conn, pmc.pmc_id, unit, date(2025, 6, 1), None, "1500.00", [tenant])
    december = charge(app_conn, pmc.pmc_id, lease, date(2025, 12, 1), "1500.00")
    open_operating(app_conn, pmc)
    add_late_fee_policy(app_conn, pmc.pmc_id, pmc.owners[0].property_id, CUTOVER, 5, "50")

    assert assess_late_fees(app_conn, pmc.pmc_id, date(2026, 1, 31)) == 0
    owed = conn.execute(
        "SELECT days_31_60, total FROM trust_report_delinquency(%s, %s) WHERE item = 'lease'",
        (pmc.pmc_id, date(2026, 1, 15)),
    ).fetchone()
    assert owed == (Decimal("1500.00"), Decimal("1500.00"))

    # The rent paid ahead in the old system pays part of February's rent here.
    february = charge(app_conn, pmc.pmc_id, lease, date(2026, 2, 1), "1500.00")
    (moved,) = post(
        app_conn,
        pmc.pmc_id,
        "prepaid to february",
        [(pmc.prepaid_rent, pmc.owners[0].account, "150.00")],
        event_at=FEB,
    )
    assert apply_payment(app_conn, pmc.pmc_id, february, moved, "150.00") == Decimal("150.00")
    assert december != february
