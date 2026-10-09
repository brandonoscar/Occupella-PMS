"""pgledger's own validation. It is upstream code, but it runs on every posting, so each refusal
path is tested here: a bad request must be refused and leave the ledger untouched."""

from decimal import Decimal

import psycopg
import pytest
from helpers import balance, make_pmc, snapshot, transfer


def all_accounts(pmc):
    return [
        pmc.operating_cash,
        pmc.deposit_cash,
        pmc.pmc_income,
        pmc.tenant_deposit,
        *(owner.account for owner in pmc.owners),
    ]


def raw_account(conn, currency="USD", allow_negative=True, allow_positive=True):
    """An account made directly with pgledger, outside the trust model (owner only)."""
    return conn.execute(
        "SELECT id FROM pgledger_create_account('raw', %s, %s, %s)",
        (currency, allow_negative, allow_positive),
    ).fetchone()[0]


@pytest.mark.parametrize(
    "amount", [Decimal("0"), Decimal("-5.00"), None, Decimal("NaN"), Decimal("Infinity")]
)
def test_amount_must_be_a_positive_finite_number(conn, amount):
    pmc = make_pmc(conn)
    before = snapshot(conn, all_accounts(pmc))

    with pytest.raises(psycopg.errors.RaiseException, match="must be a positive finite number"):
        conn.execute(
            "SELECT pgledger_create_transfer(%s, %s, %s)",
            (pmc.operating_cash, pmc.owners[0].account, amount),
        )

    assert snapshot(conn, all_accounts(pmc)) == before


def test_null_request_list_is_refused(conn):
    with pytest.raises(psycopg.errors.RaiseException, match="must not be null"):
        conn.execute("SELECT pgledger_create_transfers(NULL::transfer_request[])")


@pytest.mark.parametrize("side", ["from", "to"])
def test_null_account_id_is_refused(conn, side):
    pmc = make_pmc(conn)
    ids = [pmc.operating_cash, pmc.owners[0].account]
    ids[0 if side == "from" else 1] = None

    with pytest.raises(psycopg.errors.RaiseException, match="must not be null"):
        conn.execute("SELECT pgledger_create_transfer(%s, %s, 1)", ids)


def test_transfer_to_the_same_account_is_refused(conn):
    pmc = make_pmc(conn)

    with pytest.raises(psycopg.errors.RaiseException, match="same account"):
        transfer(conn, pmc.operating_cash, pmc.operating_cash, "1.00")


@pytest.mark.parametrize("side", ["from", "to"])
def test_unknown_account_is_refused(conn, side):
    pmc = make_pmc(conn)
    before = snapshot(conn, all_accounts(pmc))
    ids = [pmc.operating_cash, pmc.owners[0].account]
    ids[0 if side == "from" else 1] = "pgla_00000000000000000000000000"

    with pytest.raises(psycopg.errors.RaiseException, match="does not exist"):
        transfer(conn, ids[0], ids[1], "1.00")

    assert snapshot(conn, all_accounts(pmc)) == before


def test_transfer_between_currencies_is_refused(conn):
    pmc = make_pmc(conn)
    euro = raw_account(conn, currency="EUR")

    with pytest.raises(psycopg.errors.RaiseException, match="different currencies"):
        transfer(conn, pmc.operating_cash, euro, "1.00")

    assert balance(conn, pmc.operating_cash) == 0


def test_pgledger_refuses_a_positive_balance_where_disallowed(conn):
    pmc = make_pmc(conn)
    capped = raw_account(conn, allow_positive=False)

    with pytest.raises(psycopg.errors.RaiseException, match="does not allow positive balance"):
        transfer(conn, pmc.operating_cash, capped, "1.00")


def test_pgledger_lets_a_no_positive_account_come_back_to_zero(conn):
    pmc = make_pmc(conn)
    owner = pmc.owners[0].account
    conn.execute(
        "UPDATE pgledger_accounts SET allow_positive_balance = false WHERE id = %s",
        (pmc.operating_cash,),
    )
    transfer(conn, pmc.operating_cash, owner, "10.00")
    transfer(conn, owner, pmc.operating_cash, "10.00")

    assert balance(conn, pmc.operating_cash) == 0


def test_pgledger_refuses_a_negative_balance_where_disallowed(conn):
    # Our trigger lets bank_cash go negative, so this is the one place pgledger's own check
    # is the guard: switch the flag off on a bank_cash account and pgledger refuses.
    pmc = make_pmc(conn)
    conn.execute(
        "UPDATE pgledger_accounts SET allow_negative_balance = false WHERE id = %s",
        (pmc.operating_cash,),
    )

    with pytest.raises(psycopg.errors.RaiseException, match="does not allow negative balance"):
        transfer(conn, pmc.operating_cash, pmc.owners[0].account, "1.00")


def test_variadic_form_posts_every_transfer(conn):
    pmc = make_pmc(conn)
    first, second = (owner.account for owner in pmc.owners)

    rows = conn.execute(
        "SELECT id FROM pgledger_create_transfers("
        "(%s, %s, 5)::transfer_request, (%s, %s, 2)::transfer_request)",
        (pmc.operating_cash, first, first, second),
    ).fetchall()

    assert len(rows) == 2
    assert balance(conn, first) == 3
    assert balance(conn, second) == 2
