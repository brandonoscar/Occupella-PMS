"""Autopay (migration 0151, ADR 0009): a tenant's rent paid each month from a bank account they
saved at the server's payments partner (pms.payments), on the lease's rent due day.

Two steps, so a payment is never asked of the bank twice and never asked without a record:

1. `start_due`, in the nightly run's transaction (and an admin's Run rent now), after the month's
   rent is charged: each autopay whose lease's rent came due this month by the day, and that has
   no run for that due date, starts its payment (a row of trust_online_payments, as a payment the
   tenant starts by hand, with an id made from the autopay and the due date) or records why none:
   a payment hold in force on the due date, nothing owed, or the company not taking payments
   online. A fixed autopay pays its amount; a balance autopay what the lease owes by its due date,
   less what is on its way from payments online not in the books yet, up to its amount. It stops
   by itself, emailing the tenant, once the lease ended, the tenant left it, or two of its payments
   in a row didn't go through. While the server sends email, the tenant is told of each payment
   before the bank is debited.
2. `submit_waiting`, after that commits: each payment started and not yet accepted is sent to the
   partner, each in a transaction of its own, then recorded as accepted. The partner finds a
   payment it already made under the same id rather than making another (pms.payments), so a run
   that stopped between the two steps, or two runs at once, debit the tenant once. One the partner
   can't take now is tried again at the next run, for three days, and never once its autopay
   stopped (its stop email says nothing more will be taken).

A payment the partner never accepted, a day after it stopped being tried or once its autopay
stopped, is given up at the next run (`give_up`): marked expired, as a payment left unpaid on the
partner's page is, so it is no longer on its way to the lease, and the tenant is told.

What happens next is any payment online's (src/pms/api/online_payments.py): it reaches the books
only when the partner reports it paid. No money moves here, and the AI never starts one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid5

import psycopg

from pms import email, notices, payments
from pms.api import online_payments as op
from pms.api import rent

log = logging.getLogger("pms.autopay")

ZERO = Decimal(0)
# How long a payment the partner couldn't take is tried again.
RETRY_DAYS = 3

Why = Literal["tenant", "staff", "lease ended", "tenant left", "failed twice", "replaced"]
# What a stop by itself says to the tenant.
STOPPED_BY_ITSELF: dict[str, str] = {
    "lease ended": "your lease ended",
    "tenant left": "you left the lease",
    "failed twice": "two payments in a row didn't go through",
}


def payment_id_for(autopay_id: UUID, due_on: date) -> UUID:
    """The id of an autopay's payment for a due date: the same however often it is worked out, so
    the partner, asked twice, recognizes it."""
    return uuid5(autopay_id, due_on.isoformat())


# A PMC's autopays with a bank account saved, with their lease's rent due date in the day's month.
SAVED = """
SELECT a.id, a.lease_id, a.tenant_id, a.rule, a.amount, a.partner, acct.saved_at,
       trust_rent_due_on(a.lease_id, %(day)s), l.ends_on,
       acct.partner_customer, acct.partner_method, acct.partner_mandate, acct.label
FROM trust_autopay AS a
JOIN trust_autopay_accounts AS acct ON acct.autopay_id = a.id
JOIN trust_leases AS l ON l.id = a.lease_id
WHERE a.pmc_id = %(pmc)s
"""
# Those not stopped.
ACTIVE = (
    SAVED  # noqa: S608 (constant text)
    + """  AND NOT EXISTS (SELECT 1 FROM trust_autopay_stops AS s WHERE s.autopay_id = a.id)
ORDER BY a.created_at, a.id
"""
)


@dataclass(frozen=True)
class Active:
    id: UUID
    lease_id: UUID
    tenant_id: UUID
    rule: str
    amount: Decimal
    partner: str
    saved_at: datetime
    due_on: date | None
    ends_on: date | None
    customer: str
    method: str
    mandate: str | None
    label: str


def active(conn: psycopg.Connection[Any], pmc_id: UUID, day: date) -> list[Active]:
    return [Active(*row) for row in conn.execute(ACTIVE, {"pmc": pmc_id, "day": day}).fetchall()]


def saved(conn: psycopg.Connection[Any], pmc_id: UUID, autopay_id: UUID, day: date) -> Active:
    """One of the PMC's autopays with its account saved, stopped or not."""
    row = conn.execute(
        SAVED + "  AND a.id = %(id)s", {"pmc": pmc_id, "day": day, "id": autopay_id}
    ).fetchone()
    assert row is not None
    return Active(*row)


# What the lease owes by a due date: what is open of its charges due by then (credits counted).
OPEN_BY = """
SELECT coalesce(sum(o.open), 0)
FROM trust_open_charges(%(pmc)s, %(lease)s, 'infinity') AS o
JOIN trust_charges AS c ON c.id = o.charge_id
WHERE o.open > 0 AND c.due_on <= %(due)s
"""

# What is on its way to the lease from payments online not in the books yet: not failed, not come
# back (one paid during a payment hold may come back before staff recorded it), and not expired
# (left unpaid on the partner's page, or an autopay payment given up before it reached the partner).
ON_ITS_WAY = """
SELECT coalesce(sum(p.amount), 0)
FROM trust_online_payments AS p
WHERE p.pmc_id = %(pmc)s AND p.lease_id = %(lease)s
  AND NOT EXISTS (SELECT 1 FROM trust_online_payment_events AS e
                  WHERE e.payment_id = p.id AND e.kind IN ('failed', 'returned', 'expired'))
  AND NOT EXISTS (SELECT 1 FROM pgledger_transfers AS t
                  WHERE t.metadata ->> 'key' = 'payment/' || p.id::text)
"""


def to_pay(conn: psycopg.Connection[Any], pmc_id: UUID, autopay: Active, due_on: date) -> Decimal:
    """What an autopay pays for a due date: a fixed one its amount; a balance one what the lease
    owes by then less what is on its way, up to its amount (nothing below zero)."""
    if autopay.rule == "fixed":
        return autopay.amount
    about = {"pmc": pmc_id, "lease": autopay.lease_id, "due": due_on}
    owes = conn.execute(OPEN_BY, about).fetchone()
    coming = conn.execute(ON_ITS_WAY, about).fetchone()
    assert owes is not None and coming is not None
    return max(min(Decimal(owes[0]) - Decimal(coming[0]), autopay.amount), ZERO)


# The kinds of what the partner reported about each of an autopay's last two payments.
LAST_TWO = """
SELECT (SELECT array_agg(e.kind::text) FROM trust_online_payment_events AS e
        WHERE e.payment_id = r.payment_id)
FROM trust_autopay_runs AS r
WHERE r.autopay_id = %s AND r.payment_id IS NOT NULL
ORDER BY r.due_on DESC
LIMIT 2
"""


def failed_twice(conn: psycopg.Connection[Any], autopay_id: UUID) -> bool:
    """Whether the autopay's last two payments both didn't go through or came back."""
    kinds = [row[0] or [] for row in conn.execute(LAST_TWO, (autopay_id,)).fetchall()]
    return len(kinds) == 2 and all("failed" in k or "returned" in k for k in kinds)


def left_on(conn: psycopg.Connection[Any], autopay: Active, day: date) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM trust_lease_departures"
            " WHERE lease_id = %s AND tenant_id = %s AND left_on <= %s",
            (autopay.lease_id, autopay.tenant_id, day),
        ).fetchone()
        is not None
    )


def stop(conn: psycopg.Connection[Any], pmc_id: UUID, autopay_id: UUID, why: Why) -> bool:
    """Stops an autopay, once; False when it was stopped already. Staff's stop names them
    (trust.staff_id, set for the request)."""
    made = conn.execute(
        "INSERT INTO trust_autopay_stops (pmc_id, autopay_id, why) VALUES (%s, %s, %s)"
        " ON CONFLICT (autopay_id) DO NOTHING RETURNING id",
        (pmc_id, autopay_id, why),
    ).fetchone()
    return made is not None


def home_of(conn: psycopg.Connection[Any], lease_id: UUID) -> tuple[str, str]:
    """The tenant's name for their home: the property and the unit."""
    row = conn.execute(
        "SELECT p.display_name, u.display_name FROM trust_leases AS l"
        " JOIN trust_units AS u ON u.id = l.unit_id"
        " JOIN trust_properties AS p ON p.id = u.property_id WHERE l.id = %s",
        (lease_id,),
    ).fetchone()
    assert row is not None
    return str(row[0]), str(row[1])


def tenant_name(conn: psycopg.Connection[Any], tenant_id: UUID) -> str:
    row = conn.execute("SELECT display_name FROM trust_tenants WHERE id = %s", (tenant_id,))
    found = row.fetchone()
    assert found is not None
    return str(found[0])


def write_email(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    autopay: Active,
    kind: Literal["autopay_payment", "autopay_stopped", "autopay_not_sent"],
    public_url: str,
    due_on: date | None = None,
    amount: Decimal = ZERO,
    fee: Decimal = ZERO,
    why: str = "",
) -> int:
    """Tells the tenant of a payment their autopay started, before the bank is debited, of one it
    gave up sending, or that it stopped by itself; once each, to their contact email, else their
    login's."""
    to = notices.address(conn, pmc_id, "tenant", autopay.tenant_id)
    if to is None:
        return 0
    company = notices.company_of(conn, pmc_id)
    prop, unit = home_of(conn, autopay.lease_id)
    name = tenant_name(conn, autopay.tenant_id)
    signs_in = notices.can_sign_in(conn, pmc_id, "tenant", autopay.tenant_id)
    portal = notices.portal_line(signs_in, public_url, "Turn autopay off or see its payments")
    if kind == "autopay_payment":
        assert due_on is not None
        with_fee = f", plus a {email.dollars(fee)} processing fee" if fee else ""
        subject = f"Autopay: {email.dollars(amount)} from your bank for {prop}, {unit}"
        body = (
            f"Hello {name},\n\n"
            f"Autopay is paying {email.dollars(amount)}{with_fee} toward what is due "
            f"{notices.us(due_on)} for {prop}, {unit}, from {autopay.label}. Your bank takes a few "
            f"business days; the payment shows on your lease once your bank pays it.\n\n"
            f"{portal}{company}\n"
        )
    elif kind == "autopay_not_sent":
        assert due_on is not None
        subject = f"Autopay: your payment for {prop}, {unit} was not sent"
        body = (
            f"Hello {name},\n\n"
            f"Autopay couldn't send its {email.dollars(amount + fee)} payment toward what is due "
            f"{notices.us(due_on)} for {prop}, {unit} to your bank, so nothing was taken from "
            f"{autopay.label} for it. Please pay what is due another way.\n\n"
            f"{portal}{company}\n"
        )
    else:
        subject = f"Autopay stopped for {prop}, {unit}"
        body = (
            f"Hello {name},\n\n"
            f"Autopay of your rent for {prop}, {unit} stopped: {why}. Nothing more will be taken "
            f"from {autopay.label}.\n\n{portal}{company}\n"
        )
    about: dict[str, Any] = {
        "tenant_id": autopay.tenant_id,
        "lease_id": autopay.lease_id,
        "autopay_id": autopay.id,
        "to_address": to,
        "subject": notices.one_line(subject),
        "body": body,
    }
    if due_on is not None:
        about["period"] = due_on
    return int(notices._insert(conn, pmc_id, kind, about))


@dataclass
class Started:
    payments: int = 0
    skipped: int = 0
    stopped: int = 0
    emails: int = 0
    given_up: int = 0


# The PMS's own word for an autopay payment it gave up sending, kept where a partner's report is.
GAVE_UP = "pms-not-sent"

# A PMC's autopay payments the partner never accepted and has said nothing of, started before the
# cutoff or of an autopay that stopped.
NOT_SENT = """
SELECT r.autopay_id, r.payment_id, r.due_on, p.amount, p.tenant_fee, p.partner
FROM trust_autopay_runs AS r
JOIN trust_online_payments AS p ON p.id = r.payment_id
WHERE r.pmc_id = %(pmc)s
  AND NOT EXISTS (SELECT 1 FROM trust_autopay_submissions AS s WHERE s.payment_id = r.payment_id)
  AND NOT EXISTS (SELECT 1 FROM trust_online_payment_events AS e WHERE e.payment_id = r.payment_id)
  AND (r.created_at < %(cutoff)s
       OR EXISTS (SELECT 1 FROM trust_autopay_stops AS st WHERE st.autopay_id = r.autopay_id))
ORDER BY r.created_at, r.payment_id
"""


def give_up(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    day: date,
    now: datetime,
    sending: bool,
    public_url: str,
    done: Started,
) -> None:
    """Gives up the autopay payments the partner never accepted: tried for RETRY_DAYS and a day
    more (so a submit still trying them has finished), or of an autopay that stopped. Each is
    marked expired, so it is no longer on its way to the lease (should the partner have taken it
    after all, its report that the bank paid still counts), and the tenant is told, once."""
    cutoff = now - timedelta(days=RETRY_DAYS + 1)
    rows = conn.execute(NOT_SENT, {"pmc": pmc_id, "cutoff": cutoff}).fetchall()
    for autopay_id, payment, due_on, amount, fee, partner in rows:
        op.apply(conn, payments.Event(payment, "expired", GAVE_UP, now), partner)
        done.given_up += 1
        if sending:
            done.emails += write_email(
                conn,
                pmc_id,
                saved(conn, pmc_id, autopay_id, day),
                "autopay_not_sent",
                public_url,
                due_on,
                Decimal(amount),
                Decimal(fee),
            )


def start_due(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    day: date,
    partner: str | None,
    sending: bool = False,
    public_url: str = "",
    now: datetime | None = None,
) -> Started:
    """Starts each autopay payment of the PMC due by the day (see the module's docstring), in the
    caller's transaction, after giving up those never sent. With no partner on this server, it
    starts nothing and records nothing."""
    done = Started()
    if partner is None:
        return done
    give_up(conn, pmc_id, day, now or datetime.now(UTC), sending, public_url, done)
    chosen, _ = op.settings_of(conn, pmc_id)
    for autopay in active(conn, pmc_id, day):
        why: Why | None = None
        if autopay.ends_on is not None and autopay.ends_on < day:
            why = "lease ended"
        elif left_on(conn, autopay, day):
            why = "tenant left"
        elif failed_twice(conn, autopay.id):
            why = "failed twice"
        if why is not None:
            if stop(conn, pmc_id, autopay.id, why):
                done.stopped += 1
                if sending:
                    done.emails += write_email(
                        conn,
                        pmc_id,
                        autopay,
                        "autopay_stopped",
                        public_url,
                        why=STOPPED_BY_ITSELF[why],
                    )
            continue
        due = autopay.due_on
        # This month's due date, come by the day, and after the account was saved: an autopay set
        # up after this month's due date pays from next month.
        if due is None or due > day or due < autopay.saved_at.astimezone(UTC).date():
            continue
        if autopay.partner != partner:
            continue  # saved at another partner than this server's: it can't be charged here
        rent.lock_lease(conn, autopay.lease_id)
        if conn.execute(
            "SELECT 1 FROM trust_autopay_runs WHERE autopay_id = %s AND due_on = %s",
            (autopay.id, due),
        ).fetchone():
            continue
        skipped = None
        amount = ZERO
        if not chosen.online:
            skipped = "online payments off"
        elif conn.execute(
            rent.IN_FORCE, {"pmc": pmc_id, "lease": autopay.lease_id, "day": due}
        ).fetchone():
            skipped = "payment hold"
        else:
            amount = to_pay(conn, pmc_id, autopay, due)
            if amount <= 0:
                skipped = "nothing owed"
        if skipped is not None:
            conn.execute(
                "INSERT INTO trust_autopay_runs (pmc_id, autopay_id, due_on, skipped)"
                " VALUES (%s, %s, %s, %s)",
                (pmc_id, autopay.id, due, skipped),
            )
            done.skipped += 1
            continue
        fee = op.fee_for(chosen, amount)
        payment = payment_id_for(autopay.id, due)
        conn.execute(
            "INSERT INTO trust_online_payments (id, pmc_id, lease_id, tenant_id, amount,"
            " tenant_fee, fee_payer, method, partner, partner_ref)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, 'ach', %s, %s)",
            (
                payment,
                pmc_id,
                autopay.lease_id,
                autopay.tenant_id,
                amount,
                fee,
                chosen.ach_fee_payer,
                partner,
                f"autopay_{payment.hex}",
            ),
        )
        conn.execute(
            "INSERT INTO trust_autopay_runs (pmc_id, autopay_id, due_on, payment_id)"
            " VALUES (%s, %s, %s, %s)",
            (pmc_id, autopay.id, due, payment),
        )
        done.payments += 1
        if sending:
            done.emails += write_email(
                conn, pmc_id, autopay, "autopay_payment", public_url, due, amount, fee
            )
    return done


# Whether an autopay payment is still to be sent: not accepted by the partner, nothing reported of
# it (given up, say), and its autopay not stopped.
UNSENT = """
WHERE NOT EXISTS (SELECT 1 FROM trust_autopay_submissions AS s WHERE s.payment_id = r.payment_id)
  AND NOT EXISTS (SELECT 1 FROM trust_online_payment_events AS e WHERE e.payment_id = r.payment_id)
  AND NOT EXISTS (SELECT 1 FROM trust_autopay_stops AS st WHERE st.autopay_id = r.autopay_id)
"""

# Autopay payments still to be sent, started in the last few days.
WAITING = (
    """
SELECT r.pmc_id, r.payment_id, p.amount + p.tenant_fee, p.lease_id, p.partner,
       acct.partner_customer, acct.partner_method, acct.partner_mandate
FROM trust_autopay_runs AS r
JOIN trust_online_payments AS p ON p.id = r.payment_id
JOIN trust_autopay_accounts AS acct ON acct.autopay_id = r.autopay_id"""
    + UNSENT
    + """  AND r.created_at >= %(since)s
  AND (%(pmc)s::uuid IS NULL OR r.pmc_id = %(pmc)s)
ORDER BY r.created_at, r.payment_id
"""
)
STILL_UNSENT = "SELECT 1 FROM trust_autopay_runs AS r" + UNSENT + "  AND r.payment_id = %s"  # noqa: S608


def submit_waiting(
    conn: psycopg.Connection[Any],
    partner: payments.Partner,
    pmc_id: UUID | None = None,
    now: datetime | None = None,
) -> int:
    """Sends each autopay payment started and not yet accepted to the partner (of one PMC, or all),
    each in a transaction of its own; the number accepted. One the partner can't take now waits
    for the next run, for RETRY_DAYS."""
    since = (now or datetime.now(UTC)) - timedelta(days=RETRY_DAYS)
    with conn.transaction():
        waiting = conn.execute(WAITING, {"since": since, "pmc": pmc_id}).fetchall()
    accepted = 0
    for pmc, payment, total, lease, name, customer, method, mandate in waiting:
        if name != partner.name:
            continue
        with conn.transaction():
            # Stopped (or given up) since it was listed: never sent.
            if conn.execute(STILL_UNSENT, (payment,)).fetchone() is None:
                continue
            memo = rent.lease_memo(conn, lease)
        try:
            ref = partner.charge(
                payment,
                Decimal(total),
                f"Rent – {memo}",
                payments.Account(customer, method, mandate),
            )
        except payments.PartnerFailed:
            log.exception(
                "the payments partner didn't take autopay payment %s; tried again later", payment
            )
            continue
        with conn.transaction():
            made = conn.execute(
                "INSERT INTO trust_autopay_submissions (payment_id, pmc_id, partner_ref)"
                " VALUES (%s, %s, %s) ON CONFLICT (payment_id) DO NOTHING RETURNING payment_id",
                (payment, pmc, ref[:200]),
            ).fetchone()
        accepted += made is not None
    return accepted
