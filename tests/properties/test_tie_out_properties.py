"""Property-based tests for the tie-out at commit (migration 20261025000210): it checks only the
transfers that can untie a trust bank account, and refuses exactly what it refused before.

Hypothesis gives a synthetic PMC three trust bank accounts (two operating, one for deposits), each
with its cash and money held in it, then throws transactions at them: each a batch of transfers
between any of their accounts, the same bank account's or two of them, some carrying held money
to another bank account with its cash and some without, sometimes with the tie-out forced
midway (SET CONSTRAINTS ... IMMEDIATE) and more transfers after. A model works out every balance;
the database must agree:
  - a transaction is refused exactly when the model leaves a trust bank account untied at its
    end, or midway when the check was forced there, or an account other than a bank's cash
    below zero; one refused leaves nothing behind;
  - one that commits leaves every balance as the model says, and every trust bank account ties
    out (its accounts' balances sum to zero), however many transfers within one bank account rode
    with the transfers across them.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from decimal import Decimal

import psycopg
import pytest
from helpers import balance, bank_sums, make_pmc, open_account, transfer_batch
from hypothesis import event, given
from hypothesis import strategies as st

HELD = Decimal("10000.00")
MONEY = st.integers(min_value=1, max_value=20_000).map(lambda c: Decimal(c) / 100)
# Now and then more than any account holds, so an account other than cash would go below zero.
AMOUNTS = st.integers(min_value=0, max_value=39).flatmap(
    lambda n: st.just(Decimal("25000.00")) if n == 0 else MONEY
)


def accounts_of(conn, pmc):
    """Each account by name, with its trust bank account and kind; a second operating bank
    account holds the first owner's money too."""
    second = conn.execute(
        "INSERT INTO trust_bank_accounts (pmc_id, kind, display_name)"
        " VALUES (%s, 'operating', 'Synthetic second operating trust account') RETURNING id",
        (pmc.pmc_id,),
    ).fetchone()[0]
    first = pmc.owners[0]
    named = {
        "cash A": (pmc.operating_cash, "A", "bank_cash"),
        "fees A": (pmc.pmc_income, "A", "pmc_income"),
        "prepaid A": (pmc.prepaid_rent, "A", "prepaid_rent"),
        "vendor A": (pmc.vendor_payable, "A", "vendor_payable"),
        "cash D": (pmc.deposit_cash, "D", "bank_cash"),
        "deposit D": (pmc.tenant_deposit, "D", "tenant_deposit"),
        "cash B": (open_account(conn, pmc.pmc_id, second, "bank_cash"), "B", "bank_cash"),
        "owner B": (
            open_account(
                conn, pmc.pmc_id, second, "owner_property", first.owner_id, first.property_id
            ),
            "B",
            "owner_property",
        ),
    }
    for n, owner in enumerate(pmc.owners):
        named[f"owner {n} A"] = (owner.account, "A", "owner_property")
    return named


NAMES = [
    "cash A",
    "fees A",
    "prepaid A",
    "vendor A",
    "cash D",
    "deposit D",
    "cash B",
    "owner B",
    "owner 0 A",
    "owner 1 A",
]
CASH = {"A": "cash A", "B": "cash B", "D": "cash D"}
BANK = {name: name.split()[-1] for name in NAMES}


def one_transfer(draw):
    """Mostly within one trust bank account; now and then between any two accounts."""
    if draw(st.integers(min_value=0, max_value=3)) == 0:
        pool = NAMES
    else:
        bank = draw(st.sampled_from(sorted(set(BANK.values()))))
        pool = [n for n in NAMES if BANK[n] == bank]
    source, target = draw(st.lists(st.sampled_from(pool), min_size=2, max_size=2, unique=True))
    return [(source, target, draw(AMOUNTS))]


def with_its_cash(draw):
    """Held money carried to another trust bank account with its cash: both tie out."""
    source = draw(st.sampled_from([n for n in NAMES if not n.startswith("cash")]))
    target = draw(
        st.sampled_from([n for n in NAMES if not n.startswith("cash") and BANK[n] != BANK[source]])
    )
    amount = draw(MONEY)
    return [(source, target, amount), (CASH[BANK[target]], CASH[BANK[source]], amount)]


@st.composite
def batches(draw):
    moves = []
    for _ in range(draw(st.integers(min_value=1, max_value=8))):
        moves += (with_its_cash if draw(st.booleans()) else one_transfer)(draw)
    midway = draw(st.one_of(st.none(), st.integers(min_value=1, max_value=len(moves))))
    return moves, midway


def untied(model):
    return any(sum(model[n] for n in NAMES if BANK[n] == bank) != 0 for bank in set(BANK.values()))


def predict(model, moves, midway):
    """The refusal the model expects ('below zero', 'does not tie out' or None) and the balances
    after."""
    after = dict(model)
    for n, (source, target, amount) in enumerate(moves, start=1):
        after[source] -= amount
        after[target] += amount
        if not source.startswith("cash") and after[source] < 0:
            return "below zero", model
        if n == midway and untied(after):
            return "does not tie out", model
    if untied(after):
        return "does not tie out", model
    return None, after


def send(conn, ids, moves, midway):
    with conn.transaction():
        cut = len(moves) if midway is None else midway
        transfer_batch(conn, [(ids[s], ids[t], a) for s, t, a in moves[:cut]])
        if midway is not None:
            conn.execute("SET CONSTRAINTS trust_bank_tie_out IMMEDIATE")
            conn.execute("SET CONSTRAINTS trust_bank_tie_out DEFERRED")
            if moves[cut:]:
                transfer_batch(conn, [(ids[s], ids[t], a) for s, t, a in moves[cut:]])


@given(transactions=st.lists(batches(), min_size=1, max_size=4))
def check(conn, transactions):
    pmc = make_pmc(conn)
    named = accounts_of(conn, pmc)
    ids = {name: account for name, (account, _, _) in named.items()}
    # Every held account starts with money, from its bank account's cash.
    held = [n for n in NAMES if not n.startswith("cash")]
    transfer_batch(conn, [(ids[CASH[BANK[n]]], ids[n], HELD) for n in held])
    model = {n: Decimal(0) for n in NAMES}
    for n in held:
        model[n] = HELD
        model[CASH[BANK[n]]] -= HELD

    for moves, midway in transactions:
        expected, after = predict(model, moves, midway)
        crossing = sum(1 for s, t, _ in moves if BANK[s] != BANK[t])
        event(f"refused: {expected}" if expected else f"committed, {crossing} across")
        if expected is None:
            send(conn, ids, moves, midway)
            model = after
        else:
            with pytest.raises(psycopg.errors.CheckViolation, match=expected):
                send(conn, ids, moves, midway)
        assert {n: balance(conn, ids[n]) for n in NAMES} == model
        assert set(bank_sums(conn, pmc.pmc_id).values()) == {0}


def test_the_tie_out_refuses_exactly_what_leaves_a_trust_bank_account_untied(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
