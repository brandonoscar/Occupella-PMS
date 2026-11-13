"""Autopays saved at another payments account (migration 20261113000200, docs/edge-cases.md P-065;
the founder, 2026-11-13: stop them when the account differs).

A tenant's bank account is saved at one Stripe account, and only that account can charge it. The
account an autopay's was saved at is noted once (trust_autopay_account_homes): when the partner
reports it saved, the account whose keys the report came through; for one saved before this, the
company's account at the first nightly look after. Each night (and an admin's Run rent now),
before any autopay payment starts, an autopay noted at another account than the company's
partner's now stops by itself ('account changed'), its tenant is emailed to set it up again, and
the Dashboard lists it for 30 days. New keys of the same account (rolled, or the same account
linked through Stripe Connect) change nothing. When the partner can't say which account it is
(down, or keys refused), nothing is stopped or noted that night.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from uuid import UUID

import psycopg

from pms import autopay, payments

log = logging.getLogger(__name__)


def account_of(partner: Any) -> str | None:
    """The account a partner acts for; None when it can't say (or isn't one that can)."""
    if not isinstance(partner, payments.KnowsAccount):
        return None
    try:
        return partner.account()
    except payments.PartnerFailed as failed:
        log.warning("the payments partner didn't say which account it acts for: %s", failed)
        return None


def note(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    autopay_id: UUID,
    account: str,
    noted: Literal["saved", "first look"],
) -> None:
    conn.execute(
        "INSERT INTO trust_autopay_account_homes (autopay_id, pmc_id, partner_account, noted)"
        " VALUES (%s, %s, %s, %s) ON CONFLICT (autopay_id) DO NOTHING",
        (autopay_id, pmc_id, account[:200], noted),
    )


def note_saved(conn: psycopg.Connection[Any], autopay_id: UUID, partner: Any) -> None:
    """Notes the account a bank account was just saved at: the partner whose report said so."""
    account = account_of(partner)
    found = conn.execute("SELECT pmc_id FROM trust_autopay WHERE id = %s", (autopay_id,)).fetchone()
    if account is not None and found is not None:
        note(conn, found[0], autopay_id, account, "saved")


@dataclass
class Moved:
    stopped: int = 0
    emails: int = 0


def stop_moved(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    partner: Any,
    account: str | None,
    day: date,
    sending: bool = False,
    public_url: str = "",
) -> Moved:
    """Stops each of the company's autopays saved through `partner` (by its name) at another
    account than `account`, the account the partner acts for now, emailing its tenant while the
    server sends email; notes `account` for one with none noted yet. Nothing without an account."""
    done = Moved()
    if partner is None or account is None:
        return done
    homes = dict(
        conn.execute(
            "SELECT autopay_id, partner_account FROM trust_autopay_account_homes WHERE pmc_id = %s",
            (pmc_id,),
        ).fetchall()
    )
    for saved in autopay.active(conn, pmc_id, day):
        if saved.partner != partner.name:
            continue  # another partner's: start_due leaves it alone
        home = homes.get(saved.id)
        if home is None:
            note(conn, pmc_id, saved.id, account, "first look")
        elif home != account and autopay.stop(conn, pmc_id, saved.id, "account changed"):
            done.stopped += 1
            if sending:
                done.emails += autopay.write_email(
                    conn,
                    pmc_id,
                    saved,
                    "autopay_stopped",
                    public_url,
                    why=autopay.STOPPED_BY_ITSELF["account changed"],
                )
    if done.stopped:
        log.info(
            "company %s: %s autopays saved at another payments account stopped",
            pmc_id,
            done.stopped,
        )
    return done
