"""Card disputes through Stripe Connect (docs/adr/0014-stripe-connect.md, src/pms/connect.py): a
dispute is recorded the way a bank return is, and a dispute the company wins puts the payment back.

- Opened (Stripe's charge.dispute.created, which also reports the payment returned): the payment
  is undone as any return is (online_payments.settle: a bounce with the property's NSF fee, a
  tenant's fee back out of the company's account; an owner's contribution taken back out), and
  the dispute fee Stripe took from the company's balance leaves trust from the company's fee
  account, never an owner's, once that account holds it (it waits on Banking until then).
- Won (funds reinstated): the money came back, so the payment is recorded again as it first was
  (a tenant's through rent.record_payment, matching the charges the bounce opened again; an
  owner's as their contribution), the tenant's fee comes back into the company's account, and
  what Stripe gave back of the dispute fee does too. The NSF fee the bounce charged stays on the
  lease: staff waive it if the company chooses (docs/edge-cases.md P-171).
- Lost: nothing more moves; it is kept to say so.

Each is recorded once (`trust_connect_disputes`, unique per payment and kind), each posting under
a key of its own, so Stripe reporting twice moves nothing twice. Only payments through Connect
are read here; a company's own keys' disputes keep online_payments' path.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid5

import psycopg
from fastapi import HTTPException

from pms import connect
from pms.api import online_payments as op
from pms.api import owner_payments, rent

ZERO = Decimal(0)
SEND_AGAIN = "this dispute isn't open in the books yet, so it can't close: send this again later"


def dispute_fee_key(payment: UUID) -> str:
    return f"dispute-fee/{payment}"


def dispute_fee_back_key(payment: UUID) -> str:
    return f"dispute-fee-back/{payment}"


def tenant_fee_again_key(payment: UUID) -> str:
    return f"online-fee-again/{payment}"


def reinstated_request(payment: UUID) -> UUID:
    """The request id a won dispute's payment is recorded again under: the same however often."""
    return uuid5(payment, "dispute won")


def reinstated_key(payment: UUID) -> str:
    return f"payment/{reinstated_request(payment)}"


def owner_reinstated_key(payment: UUID) -> str:
    return f"owner-payment-reinstated/{payment}"


Column = Literal["online_payment_id", "owner_payment_id"]


def found(
    conn: psycopg.Connection[Any], column: Column, payment: UUID
) -> dict[str, tuple[Decimal | None, datetime]]:
    """A payment's dispute reports, by kind: the fee each names, and when."""
    return {
        kind: (None if fee is None else Decimal(fee), happened)
        for kind, fee, happened in conn.execute(
            f"SELECT kind, fee, happened_at FROM trust_connect_disputes WHERE {column} = %s",  # noqa: S608
            (payment,),
        ).fetchall()
    }


def fee_owed(disputes: dict[str, tuple[Decimal | None, datetime]]) -> Decimal:
    """What the company pays of the dispute fee: what was taken, less what came back once it was
    won (when the fee wasn't paid out of trust yet)."""
    taken = disputes["opened"][0] or ZERO
    back = (disputes["won"][0] or ZERO) if "won" in disputes else ZERO
    return max(taken - back, ZERO)


def posted_amount(conn: psycopg.Connection[Any], pmc_id: UUID, key: str) -> Decimal | None:
    """What moved under `key`, or None when nothing did."""
    row = conn.execute(
        "SELECT sum(t.amount) FROM pgledger_transfers AS t"
        " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id"
        " WHERE a.pmc_id = %s AND t.metadata ->> 'key' = %s",
        (pmc_id, key),
    ).fetchone()
    return None if row is None or row[0] is None else Decimal(row[0])


def into_company(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    bank: UUID,
    key: str,
    amount: Decimal,
    day: date,
    memo: str,
) -> None:
    """`amount` that came back into the trust bank account's cash, the company's: into its fee
    account there, under `key`, once."""
    if amount <= 0 or op.posted(conn, pmc_id, key):
        return
    rent.post(
        conn,
        pmc_id,
        key,
        [(rent.cash_of(conn, bank), op.company_account(conn, pmc_id, bank), amount)],
        day,
        {"key": key, "kind": "online payment cost", "memo": memo},
    )


def company_pays(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    bank: UUID,
    key: str,
    amount: Decimal,
    day: date,
    memo: str,
) -> bool:
    """`amount` out of trust from the company's fee account in the trust bank account, under
    `key`, once that account holds it (online_payments.from_company, for any payment). False
    while it doesn't."""
    if amount <= 0 or op.posted(conn, pmc_id, key):
        return True
    company = op.company_account(conn, pmc_id, bank)
    if op.held(conn, company) < amount:
        return False
    return op.attempt(
        conn,
        lambda: rent.post(
            conn,
            pmc_id,
            key,
            [(company, rent.cash_of(conn, bank), amount)],
            day,
            {"key": key, "kind": "online payment cost", "memo": memo},
        ),
    )


# --- a tenant's payment -----------------------------------------------------------------------


def original_account(conn: psycopg.Connection[Any], row: op.Row) -> str | None:
    """The owner's account the payment first went to, when it went to one alone."""
    accounts = conn.execute(
        "SELECT DISTINCT t.to_account_id FROM pgledger_transfers AS t"
        " JOIN trust_ledger_accounts AS a ON a.ledger_account_id = t.to_account_id"
        " WHERE a.pmc_id = %s AND a.kind = 'owner_property' AND t.metadata ->> 'key' = %s",
        (row.pmc_id, op.payment_key(row.id)),
    ).fetchall()
    return str(accounts[0][0]) if len(accounts) == 1 else None


def reinstate(conn: psycopg.Connection[Any], row: op.Row, day: date) -> None:
    """The payment recorded again as it first was: the property's owners as then, or the owner's
    account it went to when staff chose one (docs/edge-cases.md P-040)."""

    def again(account: str | None) -> None:
        rent.record_payment(
            conn,
            row.pmc_id,
            row.lease_id,
            reinstated_request(row.id),
            row.amount,
            op.open_day(conn, row, day),
            reference=row.partner_ref,
            method="online",
            account=account,
        )

    try:
        with conn.transaction():
            again(None)
    except HTTPException as refused:
        chosen = original_account(conn, row)
        if refused.status_code != 422 or chosen is None:
            raise
        again(chosen)


def settle_tenant(conn: psycopg.Connection[Any], row: op.Row, on: date | None = None) -> None:
    """What a tenant's payment's dispute calls for that isn't in the books yet, as far as they
    take it now (online_payments.settle calls it last). The company's part is dated `on` when
    staff cover it later, else the day of the report."""
    if row.partner not in connect.PARTNERS:
        return
    disputes = found(conn, "online_payment_id", row.id)
    if "opened" not in disputes or not op.posted(conn, row.pmc_id, op.bounce_key(row.id)):
        return  # the return itself waits first
    opened_on = op.day_of(conn, row.pmc_id, disputes["opened"][1])
    bank = op.bank_of(conn, row, opened_on)
    memo = rent.lease_memo(conn, row.lease_id)
    company_pays(
        conn,
        row.pmc_id,
        bank,
        dispute_fee_key(row.id),
        fee_owed(disputes),
        on or opened_on,
        f"Card dispute fee – {memo}",
    )
    if "won" not in disputes:
        return
    won_on = op.day_of(conn, row.pmc_id, disputes["won"][1])
    if not op.posted(conn, row.pmc_id, reinstated_key(row.id)) and not op.attempt(
        conn, lambda: reinstate(conn, row, won_on)
    ):
        return  # the books refuse it for now (a payment hold): it waits for staff
    day = op.open_day(conn, row, won_on)
    if op.posted(conn, row.pmc_id, op.tenant_fee_back_key(row.id)):
        into_company(
            conn,
            row.pmc_id,
            bank,
            tenant_fee_again_key(row.id),
            row.tenant_fee,
            day,
            f"Processing fee back with a dispute won – {memo}",
        )
    # What left trust of the dispute fee beyond what is owed once won came back with it.
    paid = posted_amount(conn, row.pmc_id, dispute_fee_key(row.id))
    if paid is not None and paid > fee_owed(disputes):
        into_company(
            conn,
            row.pmc_id,
            bank,
            dispute_fee_back_key(row.id),
            paid - fee_owed(disputes),
            day,
            f"Card dispute fee back with a dispute won – {memo}",
        )


# --- an owner's payment -----------------------------------------------------------------------


def settle_owner(
    conn: psycopg.Connection[Any], row: owner_payments.Row, on: date | None = None
) -> None:
    """What an owner's payment's dispute calls for (owner_payments.settle calls it last): the
    dispute fee from the company, and once won, the contribution again."""
    if row.partner not in connect.PARTNERS:
        return
    disputes = found(conn, "owner_payment_id", row.id)
    if "opened" not in disputes or not op.posted(
        conn, row.pmc_id, owner_payments.return_key(row.id)
    ):
        return
    place = owner_payments.where(conn, row.account)
    opened_on = op.day_of(conn, row.pmc_id, disputes["opened"][1])
    company_pays(
        conn,
        row.pmc_id,
        place.bank,
        dispute_fee_key(row.id),
        fee_owed(disputes),
        owner_payments.open_day(conn, place.bank, on or opened_on),
        f"Card dispute fee on an owner's payment – {place.memo}",
    )
    if "won" not in disputes:
        return
    won_on = owner_payments.open_day(
        conn, place.bank, op.day_of(conn, row.pmc_id, disputes["won"][1])
    )
    key = owner_reinstated_key(row.id)
    if not owner_reinstated(conn, row) and not op.attempt(
        conn,
        lambda: conn.execute(
            "SELECT trust_record_owner_contribution(%s, %s, %s, %s, %s, %s)",
            (row.pmc_id, row.account, key, row.amount, rent.at(won_on), "Dispute won: paid back"),
        ),
    ):
        return
    paid = posted_amount(conn, row.pmc_id, dispute_fee_key(row.id))
    if paid is not None and paid > fee_owed(disputes):
        into_company(
            conn,
            row.pmc_id,
            place.bank,
            dispute_fee_back_key(row.id),
            paid - fee_owed(disputes),
            won_on,
            f"Card dispute fee back with a dispute won – {place.memo}",
        )


def owner_reinstated(conn: psycopg.Connection[Any], row: owner_payments.Row) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM trust_owner_contributions WHERE pmc_id = %s AND request_key = %s",
            (row.pmc_id, owner_reinstated_key(row.id)),
        ).fetchone()
        is not None
    )


# --- recording what Stripe reports ------------------------------------------------------------


def record(conn: psycopg.Connection[Any], dispute: connect.Dispute) -> bool:
    """Records a dispute's report about a payment through Connect (once per payment and kind)
    and settles what it calls for. False when the payment isn't one through Connect. A close
    reported before its opening is refused for now (409), so Stripe sends it again."""
    tenant = op.row_of(conn, dispute.payment_id)
    owner = None if tenant is not None else owner_payments.row_of(conn, dispute.payment_id)
    row = tenant or owner
    if row is None or row.partner not in connect.PARTNERS:
        return False
    column: Column = "online_payment_id" if tenant is not None else "owner_payment_id"
    if tenant is not None:
        rent.lock_lease(conn, tenant.lease_id)
    else:
        owner_payments.lock(conn, dispute.payment_id)
    try:
        with conn.transaction():
            conn.execute(
                f"INSERT INTO trust_connect_disputes (pmc_id, {column}, dispute_id, kind, fee,"  # noqa: S608
                " partner_event, happened_at) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (
                    row.pmc_id,
                    row.id,
                    dispute.dispute_id,
                    dispute.kind,
                    dispute.fee,
                    dispute.event_id,
                    dispute.happened_at,
                ),
            )
    except psycopg.errors.CheckViolation:
        if dispute.kind != "opened" and "opened" not in found(conn, column, row.id):
            raise HTTPException(409, SEND_AGAIN) from None
        return True  # won after lost, say: kept as it first came
    if tenant is not None:
        op.settle(conn, tenant)
    else:
        assert owner is not None
        owner_payments.settle(conn, owner)
    return True


# --- what waits for staff ---------------------------------------------------------------------

DISPUTED = """
SELECT d.online_payment_id, d.owner_payment_id FROM trust_connect_disputes AS d
WHERE d.pmc_id = %s AND d.kind = 'opened'
ORDER BY d.created_at, d.id
"""


def waiting(conn: psycopg.Connection[Any], pmc_id: UUID) -> list[op.Waiting]:
    """Disputes the books haven't taken all of yet, as Banking lists them with the payments online
    waiting: a dispute fee the company's fee account can't cover yet, and a won dispute's payment
    the books refuse for now (a payment hold)."""
    items: list[op.Waiting] = []
    for tenant_payment, owner_payment in conn.execute(DISPUTED, (pmc_id,)).fetchall():
        if tenant_payment is not None:
            row = op.row_of(conn, tenant_payment)
            assert row is not None
            disputes = found(conn, "online_payment_id", row.id)
            if not op.posted(conn, pmc_id, op.bounce_key(row.id)):
                continue  # its return waits first, listed as one
            payment, amount = row.id, row.amount
            bank = op.bank_of(conn, row, op.day_of(conn, pmc_id, disputes["opened"][1]))
            back_in = op.posted(conn, pmc_id, reinstated_key(row.id))
            lease_id, owner_id = row.lease_id, None
            memo = rent.lease_memo(conn, row.lease_id)
        else:
            owners = owner_payments.row_of(conn, owner_payment)
            assert owners is not None
            disputes = found(conn, "owner_payment_id", owners.id)
            if not op.posted(conn, pmc_id, owner_payments.return_key(owners.id)):
                continue
            payment, amount = owners.id, owners.amount
            place = owner_payments.where(conn, owners.account)
            bank = place.bank
            back_in = owner_reinstated(conn, owners)
            lease_id, owner_id, memo = None, place.owner_id, place.memo
        what: list[tuple[Literal["dispute fee", "dispute won"], Decimal | None]] = []
        owes = fee_owed(disputes)
        if owes > 0 and not op.posted(conn, pmc_id, dispute_fee_key(payment)):
            what.append(("dispute fee", owes))
        if "won" in disputes and not back_in:
            what.append(("dispute won", None))
        holds = rent.cents(op.held(conn, op.company_account(conn, pmc_id, bank)))
        items += [
            op.Waiting(
                payment_id=payment,
                lease_id=lease_id,
                owner_id=owner_id,
                memo=memo,
                amount=rent.cents(amount),
                what=kind,
                company_owes=None if owes_now is None else rent.cents(owes_now),
                bank_account_id=bank,
                company_holds=holds,
            )
            for kind, owes_now in what
        ]
    return items
