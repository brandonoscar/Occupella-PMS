"""Asking the payments partner about payments it sent no report of (docs/edge-cases.md C-090).

Stripe gives up on a webhook after three days of failed deliveries (the PMS down, its endpoint
misconfigured, a secret changed), and a bank payment it never reported never reached the books:
staff saw it on Banking after NO_WORD_DAYS with nothing to do but look it up. Once a day for each
company, the nightly run asks the partner about each payment online, a tenant's or an owner's,
started more than LOOK_AFTER_DAYS ago (a bank payment settles within four business days, and
Stripe retries a report for three) and less than LOOK_FOR_DAYS, that has no report and isn't in
the books. What the partner says goes in as its report would have
(online_payments.apply, owner_payments.apply): once per payment and kind, each posting under a
key of its own, so a report that comes after records nothing twice, and one that came before
leaves the payment off the list. The partner is asked outside any transaction; each answer is
recorded in a transaction of its own.

A payment the company's keys don't open (keys taken away, a server without PMS_DATA_KEY) is
asked of each set of keys the company ever entered that opens here, then of the server's partner;
one none of them holds stays on Banking's list. Returns after a payment was reported paid aren't
looked for here (docs/edge-cases.md).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg

from pms import payments
from pms.api import online_payments, outbox, owner_payments

log = logging.getLogger(__name__)

# A payment with no report this long after it started is asked about: a bank payment settles in
# four business days, and Stripe retries a report it couldn't deliver for three days.
LOOK_AFTER_DAYS = 7
# ...until this long after: one still with no word then is left to staff (Banking's list).
LOOK_FOR_DAYS = 60

# Each payment online with no report and not in the books, started in the window: a tenant's
# (an autopay's once sent, under the reference the partner gave it) and an owner's.
WAITING = """
SELECT 'tenant' AS whose, p.id, p.pmc_id, p.partner, coalesce(s.partner_ref, p.partner_ref),
       p.created_at
FROM trust_online_payments AS p
LEFT JOIN trust_autopay_submissions AS s ON s.payment_id = p.id
WHERE p.partner <> 'fake' AND p.created_at < %(before)s AND p.created_at >= %(since)s
  AND (%(pmcs)s::uuid[] IS NULL OR p.pmc_id = ANY (%(pmcs)s::uuid[]))
  AND (p.partner_ref NOT LIKE 'autopay\\_%%' OR s.payment_id IS NOT NULL)
  AND NOT EXISTS (SELECT 1 FROM trust_online_payment_events AS e WHERE e.payment_id = p.id)
  AND NOT EXISTS (SELECT 1 FROM pgledger_transfers AS t
                  WHERE t.metadata ->> 'key' = 'payment/' || p.id::text)
UNION ALL
SELECT 'owner', p.id, p.pmc_id, p.partner, p.partner_ref, p.created_at
FROM trust_owner_online_payments AS p
WHERE p.partner <> 'fake' AND p.created_at < %(before)s AND p.created_at >= %(since)s
  AND (%(pmcs)s::uuid[] IS NULL OR p.pmc_id = ANY (%(pmcs)s::uuid[]))
  AND NOT EXISTS (SELECT 1 FROM trust_owner_online_payment_events AS e
                  WHERE e.payment_id = p.id)
  AND NOT EXISTS (SELECT 1 FROM trust_owner_contributions AS c
                  WHERE c.pmc_id = p.pmc_id AND c.request_key = 'owner-payment/' || p.id::text)
ORDER BY 6, 2
"""


@dataclass
class Looked:
    asked: int = 0  # payments the partner was asked about
    recorded: int = 0  # answers recorded as reports: paid, failed or expired
    unanswered: int = 0  # payments no partner here could say anything of


def partners_of(
    conn: psycopg.Connection[Any], resolver: payments.Resolver, pmc_id: UUID, name: str
) -> list[payments.LooksUp]:
    """Who may know of a company's payment: each set of keys it entered that opens here, newest
    first, then the server's partner where it serves the company; only those named `name`."""
    chosen: list[Any] = [*resolver.every_own(conn, pmc_id), resolver.server_for(conn)]
    return [p for p in chosen if isinstance(p, payments.LooksUp) and p.name == name]


def ask(
    partners: list[payments.LooksUp], payment_id: UUID, ref: str, now: datetime
) -> tuple[payments.LooksUp, payments.Event | None] | None:
    """The first partner holding the payment and what it says (None while on its way); None when
    none holds it or none answered."""
    for partner in partners:
        try:
            return partner, partner.look_up(payment_id, ref, now)
        except payments.PartnerFailed as failed:
            # Another account's payment (404), or the partner down: the next set of keys.
            log.warning("asking the payments partner about payment %s: %s", payment_id, failed)
    return None


def look_up_waiting(
    conn: psycopg.Connection[Any],
    partner: payments.Partner | payments.Resolver | None,
    pmcs: Iterable[UUID] | None = None,
    now: datetime | None = None,
    sending: bool = False,
    public_url: str | None = None,
) -> Looked:
    """Asks the partner about each payment online with no word (of the companies named, or all)
    and records what it says. `conn` is in autocommit mode; each answer goes in a transaction of
    its own. While the server sends email, a payment recorded sends its receipt as a report's
    would."""
    done = Looked()
    if partner is None:
        return done
    resolver = payments.Resolver.of(partner)
    now = now or datetime.now(UTC)
    params = {
        "before": now - timedelta(days=LOOK_AFTER_DAYS),
        "since": now - timedelta(days=LOOK_FOR_DAYS),
        "pmcs": None if pmcs is None else list(pmcs),
    }
    if params["pmcs"] == []:
        return done
    with conn.transaction():
        waiting = conn.execute(WAITING, params).fetchall()
    known: dict[tuple[UUID, str], list[payments.LooksUp]] = {}
    sending_was = outbox.SENDING.set(sending)
    url_was = outbox.PUBLIC_URL.set(public_url or outbox.PUBLIC_URL.get())
    try:
        for whose, payment_id, pmc_id, name, ref, _started in waiting:
            if (pmc_id, name) not in known:
                with conn.transaction():
                    known[(pmc_id, name)] = partners_of(conn, resolver, pmc_id, name)
            done.asked += 1
            answer = ask(known[(pmc_id, name)], payment_id, ref, now)
            if answer is None:
                done.unanswered += 1
                continue
            asked, event = answer
            if event is None:
                continue  # still on its way: asked again tomorrow
            try:
                with conn.transaction():
                    apply = online_payments.apply if whose == "tenant" else owner_payments.apply
                    done.recorded += apply(conn, event, asked.name)
            except Exception:
                log.exception("recording what the partner said of payment %s failed", payment_id)
    finally:
        outbox.PUBLIC_URL.reset(url_was)
        outbox.SENDING.reset(sending_was)
    if done.asked:
        log.info(
            "asked the payments partner about %s payments with no word: %s recorded, %s unanswered",
            done.asked,
            done.recorded,
            done.unanswered,
        )
    return done
