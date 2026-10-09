"""The trust rules the database enforces on top of pgledger: no held account below zero (Cal. Reg.
2832.1), every transfer inside one PMC, and the app role writing only through ledger functions."""

import psycopg
import pytest
from helpers import balance, make_pmc, post, snapshot, transfer, transfer_batch


def all_accounts(pmc):
    return pmc.accounts()


def test_transfer_that_would_take_owner_account_below_zero_is_refused_and_nothing_written(conn):
    pmc = make_pmc(conn)
    owner = pmc.owners[0].account
    transfer(conn, pmc.operating_cash, owner, "100.00")
    before = snapshot(conn, all_accounts(pmc))

    with pytest.raises(psycopg.errors.CheckViolation, match="owner_property account .* below zero"):
        transfer(conn, owner, pmc.operating_cash, "100.01")

    assert snapshot(conn, all_accounts(pmc)) == before
    assert balance(conn, owner) == 100


def test_batch_with_one_overdraft_is_refused_whole(conn):
    pmc = make_pmc(conn)
    first, second = (owner.account for owner in pmc.owners)
    transfer(conn, pmc.operating_cash, first, "50.00")
    before = snapshot(conn, all_accounts(pmc))

    with pytest.raises(psycopg.errors.CheckViolation):
        transfer_batch(
            conn,
            [
                (pmc.operating_cash, second, "10.00"),  # fine on its own
                (first, pmc.pmc_income, "50.01"),  # overdraws the first owner
            ],
        )

    assert snapshot(conn, all_accounts(pmc)) == before


def test_no_negative_rule_does_not_rely_on_pgledgers_own_flag(conn):
    pmc = make_pmc(conn)
    owner = pmc.owners[0].account
    transfer(conn, pmc.operating_cash, owner, "20.00")
    # Switch off pgledger's own check for this account. Only our trigger is left.
    conn.execute(
        "UPDATE pgledger_accounts SET allow_negative_balance = true WHERE id = %s", (owner,)
    )
    before = snapshot(conn, all_accounts(pmc))

    with pytest.raises(psycopg.errors.CheckViolation):
        transfer(conn, owner, pmc.operating_cash, "20.01")

    assert snapshot(conn, all_accounts(pmc)) == before


def test_editing_an_owner_balance_below_zero_directly_is_refused(conn):
    pmc = make_pmc(conn)
    owner = pmc.owners[0].account

    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE pgledger_accounts SET balance = -1 WHERE id = %s", (owner,))

    assert balance(conn, owner) == 0


@pytest.mark.parametrize("kind", ["tenant_deposit", "pmc_income", "prepaid_rent", "vendor_payable"])
def test_other_held_accounts_cannot_go_below_zero_either(conn, kind):
    pmc = make_pmc(conn)
    account = getattr(pmc, kind)
    before = snapshot(conn, all_accounts(pmc))

    with pytest.raises(psycopg.errors.CheckViolation, match=f"{kind} account"):
        transfer(conn, account, pmc.cash_for(account), "0.01")

    assert snapshot(conn, all_accounts(pmc)) == before


def test_bank_cash_runs_negative_as_money_arrives(conn):
    pmc = make_pmc(conn)
    transfer(conn, pmc.deposit_cash, pmc.tenant_deposit, "1800.00")

    assert balance(conn, pmc.deposit_cash) == -1800
    assert balance(conn, pmc.tenant_deposit) == 1800


def test_transfer_across_pmcs_is_refused(conn):
    first, second = make_pmc(conn), make_pmc(conn)
    transfer(conn, first.operating_cash, first.owners[0].account, "10.00")
    accounts = all_accounts(first) + all_accounts(second)
    before = snapshot(conn, accounts)

    with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="crosses PMCs"):
        transfer(conn, first.owners[0].account, second.owners[0].account, "5.00")

    assert snapshot(conn, accounts) == before


def test_transfer_touching_an_account_with_no_trust_kind_is_refused(conn):
    pmc = make_pmc(conn)
    owner = pmc.owners[0].account
    transfer(conn, pmc.operating_cash, owner, "10.00")
    stray = conn.execute("SELECT id FROM pgledger_create_account('stray', 'USD')").fetchone()[0]
    accounts = [*all_accounts(pmc), stray]
    before = snapshot(conn, accounts)

    # Owner money paid out to an account outside the trust model.
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="no trust kind"):
        transfer(conn, owner, stray, "5.00")

    assert snapshot(conn, accounts) == before


def test_app_role_sets_up_and_posts_through_functions_only(app_conn):
    # The normal path works with only trust_app's grants: every posting carries a key.
    pmc = make_pmc(app_conn)
    owner = pmc.owners[0].account
    post(app_conn, pmc.pmc_id, "rent", [(pmc.operating_cash, owner, "75.00")])
    assert balance(app_conn, owner) == 75

    # Every direct write to the ledger is refused for lack of a grant.
    direct_writes = [
        ("UPDATE pgledger_accounts SET balance = balance + 1 WHERE id = %s", (owner,)),
        (
            "INSERT INTO pgledger_transfers"
            " (from_account_id, to_account_id, amount, created_at, event_at)"
            " VALUES (%s, %s, 1, now(), now())",
            (pmc.operating_cash, owner),
        ),
        (
            "INSERT INTO trust_ledger_accounts (ledger_account_id, pmc_id, bank_account_id, kind)"
            " VALUES ('pgla_x', %s, %s, 'bank_cash')",
            (pmc.pmc_id, pmc.operating_bank_id),
        ),
        ("SELECT pgledger_create_account('stray', 'USD')", ()),
        # pgledger's own posting functions take no key, so a retried request could post twice.
        ("SELECT pgledger_create_transfer(%s, %s, 1)", (pmc.operating_cash, owner)),
        (
            "SELECT pgledger_create_transfers(ARRAY[(%s, %s, 1)::transfer_request])",
            (pmc.operating_cash, owner),
        ),
        (
            "SELECT pgledger_create_transfers(ARRAY[(%s, %s, 1)::transfer_request], now(), NULL)",
            (pmc.operating_cash, owner),
        ),
        ("DELETE FROM trust_owners WHERE id = %s", (pmc.owners[0].owner_id,)),
    ]
    for query, params in direct_writes:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app_conn.execute(query, params)

    assert balance(app_conn, owner) == 75
