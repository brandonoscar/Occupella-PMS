"""Application fees (migration 20261022000160; the founder, 2026-10-23): a company asks each
applicant a fee, set in Company setup (area leasing), which an application's applicants pay online
through the payment processor once they apply, with their application's status link (they have no
login). The processor pays it into the company's trust bank account with the rent, so once it says
the money came the PMS records it there as the company's own fee: from the cash of the trust bank
account the unit's owners keep their money in, to its fee account, taken out of trust as every
company fee is. An application's fee is paid once. No state's cap on the amount is built in
(docs/law.md).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from pms import accounts, exports
from pms.api import outbox
from pms.api.deps import Conn, Staff
from pms.payments import processor
from pms.payments.processor import Event, Method

router = APIRouter()

CENT = Decimal("0.01")
Token = Annotated[str, Field(min_length=20, max_length=100)]


class FeeSettings(BaseModel):
    per_applicant: str
    recorded_by: str | None
    recorded_at: datetime | None


class NewFeeSettings(BaseModel):
    per_applicant: Annotated[Decimal, Field(ge=0, le=1000, decimal_places=2)]


class FeeState(BaseModel):
    """An application's fee as its applicants see it."""

    # What is still to pay (each applicant's fee times the applicants), or None.
    due: str | None
    paid: str | None
    # How it may be paid online now: what the company takes, while the server has a processor.
    methods: list[Method]


class PayFee(BaseModel):
    status_token: Token
    method: Method


class FeeStarted(BaseModel):
    url: str


class FakeOutcome(BaseModel):
    outcome: Literal["succeeded", "failed"]


def cents(value: Decimal) -> str:
    return exports.text(Decimal(value).quantize(CENT))


def settings_of(conn: psycopg.Connection[Any], pmc_id: UUID) -> FeeSettings:
    row = conn.execute(
        "SELECT f.per_applicant, s.display_name, f.created_at"
        " FROM trust_application_fee_settings AS f"
        " LEFT JOIN trust_staff AS s ON s.id = f.recorded_by"
        " WHERE f.pmc_id = %s ORDER BY f.created_at DESC, f.id DESC LIMIT 1",
        (pmc_id,),
    ).fetchone()
    if row is None:
        return FeeSettings(per_applicant="0.00", recorded_by=None, recorded_at=None)
    return FeeSettings(per_applicant=cents(row[0]), recorded_by=row[1], recorded_at=row[2])


@router.get("/api/leasing/application-fee")
def fee_settings(session: Staff, conn: Conn) -> FeeSettings:
    """What each applicant pays when they apply (0.00: nothing)."""
    return settings_of(conn, session.pmc_id)


@router.post("/api/leasing/application-fee", status_code=201)
def keep_fee_settings(session: Staff, conn: Conn, new: NewFeeSettings) -> FeeSettings:
    """What each applicant pays from now on; applications already in keep what they were asked."""
    conn.execute(
        "INSERT INTO trust_application_fee_settings (pmc_id, per_applicant) VALUES (%s, %s)",
        (session.pmc_id, new.per_applicant),
    )
    return settings_of(conn, session.pmc_id)


def paid_of(conn: psycopg.Connection[Any], application_id: UUID) -> Decimal | None:
    row = conn.execute(
        "SELECT f.amount FROM trust_application_fees AS f"
        " JOIN trust_application_fee_steps AS s ON s.fee_id = f.id AND s.step = 'succeeded'"
        " WHERE f.application_id = %s",
        (application_id,),
    ).fetchone()
    return None if row is None else Decimal(row[0])


def asked(conn: psycopg.Connection[Any], pmc_id: UUID, application_id: UUID) -> Decimal:
    """The fee an application owes: each applicant's fee in force when it came, times them."""
    row = conn.execute(
        "SELECT coalesce((SELECT f.per_applicant FROM trust_application_fee_settings AS f"
        "                 WHERE f.pmc_id = a.pmc_id AND f.created_at <= a.submitted_at"
        "                 ORDER BY f.created_at DESC, f.id DESC LIMIT 1), 0)"
        "       * (SELECT count(*) FROM trust_applicants AS p WHERE p.application_id = a.id)"
        " FROM trust_applications AS a WHERE a.pmc_id = %s AND a.id = %s",
        (pmc_id, application_id),
    ).fetchone()
    return Decimal(row[0]) if row else Decimal(0)


def state(conn: psycopg.Connection[Any], pmc_id: UUID, application_id: UUID) -> FeeState:
    """An application's fee: what is due, what was paid and how it may be paid now."""
    from pms.api import online_payments  # it reaches leasing, which builds on this module

    owed, paid = asked(conn, pmc_id, application_id), paid_of(conn, application_id)
    found = processor.CURRENT.get()
    return FeeState(
        due=None if owed == 0 or paid is not None else cents(owed),
        paid=None if paid is None else cents(paid),
        methods=online_payments.methods_of(online_payments.settings_of(conn, pmc_id), found)
        if owed > 0 and paid is None
        else [],
    )


def application_by(conn: psycopg.Connection[Any], token: str) -> tuple[UUID, UUID, str]:
    """(PMC, application, "Property, Unit") of a status token, or 404."""
    row = conn.execute(
        "SELECT a.pmc_id, a.id, p.display_name || ', ' || u.display_name"
        " FROM trust_applications AS a"
        " JOIN trust_application_links AS l ON l.id = a.link_id"
        " JOIN trust_units AS u ON u.id = l.unit_id"
        " JOIN trust_properties AS p ON p.id = u.property_id"
        " WHERE a.status_token_sha256 = %s",
        (accounts.digest(token),),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no application has this link")
    return row[0], row[1], row[2]


@router.post("/api/application/fee", status_code=201)
def pay_fee(conn: Conn, pay: PayFee) -> FeeStarted:
    """Starts paying an application's fee, for whoever holds its status token: on the processor's
    page."""
    pmc_id, application_id, home = application_by(conn, pay.status_token)
    now = state(conn, pmc_id, application_id)
    if now.due is None:
        raise HTTPException(409, "this application has no fee to pay")
    if pay.method not in now.methods:
        raise HTTPException(409, f"its fee isn't taken by {pay.method} online")
    found = processor.CURRENT.get()
    assert found is not None
    row = conn.execute(
        "INSERT INTO trust_application_fees (pmc_id, application_id, amount, method, processor)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (pmc_id, application_id, Decimal(now.due), pay.method, found.name),
    ).fetchone()
    assert row is not None
    checkout = found.start(
        str(row[0]),
        Decimal(now.due),
        pay.method,
        f"Application fee – {home}",
        f"{outbox.PUBLIC_URL.get()}/application",
        kind="fee",
    )
    conn.execute(
        "INSERT INTO trust_application_fee_steps (pmc_id, fee_id, step, reference)"
        " VALUES (%s, %s, 'started', %s)",
        (pmc_id, row[0], checkout.reference),
    )
    return FeeStarted(url=checkout.url)


# The trust bank account a unit's owners keep their money in (its oldest owner account's), with
# its cash and fee accounts.
ACCOUNTS = """
SELECT cash.ledger_account_id, fee.ledger_account_id, fee.bank_account_id
FROM trust_application_fees AS f
JOIN trust_applications AS a ON a.id = f.application_id
JOIN trust_application_links AS l ON l.id = a.link_id
JOIN trust_units AS u ON u.id = l.unit_id
JOIN LATERAL (
    SELECT o.bank_account_id FROM trust_ledger_accounts AS o
    WHERE o.pmc_id = f.pmc_id AND o.property_id = u.property_id AND o.kind = 'owner_property'
    ORDER BY o.created_at, o.ledger_account_id LIMIT 1
) AS bank ON true
JOIN trust_ledger_accounts AS cash
    ON cash.bank_account_id = bank.bank_account_id AND cash.kind = 'bank_cash'
LEFT JOIN trust_ledger_accounts AS fee
    ON fee.bank_account_id = bank.bank_account_id AND fee.kind = 'pmc_income'
WHERE f.id = %s
"""


def take(conn: psycopg.Connection[Any], name: str, event: Event) -> bool:
    """Takes a processor's word on an application fee's payment once: records the fee that came
    as the company's in trust, or keeps one that failed. False when it was taken before or names
    no fee of this server's."""
    if (
        event.outcome not in ("succeeded", "failed")
        or conn.execute(
            "SELECT 1 FROM trust_application_fee_steps WHERE processor_event = %s", (event.id,)
        ).fetchone()
    ):
        return False
    row = conn.execute(
        "SELECT f.pmc_id, f.id, f.amount, f.application_id FROM trust_application_fee_steps AS s"
        " JOIN trust_application_fees AS f ON f.id = s.fee_id"
        " WHERE s.step = 'started' AND s.reference = %s AND f.processor = %s",
        (event.reference, name),
    ).fetchone()
    if (
        row is None
        or conn.execute(
            "SELECT 1 FROM trust_application_fee_steps WHERE fee_id = %s AND step <> 'started'",
            (row[1],),
        ).fetchone()
    ):
        return False
    pmc_id, fee_id, amount, application_id = row
    if event.outcome == "failed" or paid_of(conn, application_id) is not None:
        reason = event.reason or "it didn't go through"
        if event.outcome == "succeeded":
            reason = "the fee was paid already: refund this one at the payment processor"
        conn.execute(
            "INSERT INTO trust_application_fee_steps (pmc_id, fee_id, step, processor_event,"
            " reason) VALUES (%s, %s, 'failed', %s, %s)",
            (pmc_id, fee_id, event.id, reason[:500]),
        )
        return True
    accounts_row = conn.execute(ACCOUNTS, (fee_id,)).fetchone()
    assert accounts_row is not None, "a unit's property has an owner's account in trust"
    cash, fee_account, bank = accounts_row
    if fee_account is None:
        opened = conn.execute(
            "SELECT trust_open_ledger_account(%s, %s, 'pmc_income', NULL, NULL, NULL)",
            (pmc_id, bank),
        ).fetchone()
        assert opened is not None
        fee_account = opened[0]
    posted = conn.execute(
        "SELECT id FROM trust_post_transfers(%s, %s,"
        " ARRAY[(%s, %s, %s::numeric)::transfer_request], %s, %s)",
        (
            pmc_id,
            f"application-fee/{fee_id}",
            cash,
            fee_account,
            amount,
            datetime.now(UTC),
            Jsonb(
                {
                    "kind": "application fee",
                    "application_id": str(application_id),
                    "memo": "Application fee",
                    "reference": event.processor_payment,
                }
            ),
        ),
    ).fetchone()
    assert posted is not None
    conn.execute(
        "INSERT INTO trust_application_fee_steps (pmc_id, fee_id, step, processor_event,"
        " transfer_id, reference) VALUES (%s, %s, 'succeeded', %s, %s, %s)",
        (pmc_id, fee_id, event.id, posted[0], event.processor_payment),
    )
    return True


@router.post("/api/application/fee/fake/{reference}", status_code=204)
def fake_checkout(conn: Conn, reference: str, said: FakeOutcome) -> None:
    """The fake processor's page for an application fee: whoever was sent to it (its reference is
    the secret) says what happened. Only on a server whose processor is the fake one."""
    found = processor.CURRENT.get()
    if found is None or found.name != "fake":
        raise HTTPException(404, "this server's payment processor is not the fake one")
    event = Event(
        id=f"fake-event-{reference}",
        outcome=said.outcome,
        reference=reference,
        processor_payment=f"fake-payment-{reference}" if said.outcome == "succeeded" else None,
        reason=None if said.outcome == "succeeded" else "you said it didn't go through",
    )
    if not take(conn, "fake", event):
        raise HTTPException(404, "no such fee waiting to be paid")
