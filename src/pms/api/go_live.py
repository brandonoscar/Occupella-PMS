"""What a company still needs before it goes live (the One Occupella plan of 11/05, Phase 1 item 3,
its wireframe W6): each step worked out from the company's own records, never ticked by hand, so
the list is right however a step was done. The Dashboard shows it until every required step is
done. Read only: it changes nothing and moves no money.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from pydantic import BaseModel

from pms import days
from pms.api import signers
from pms.api.deps import Conn, Staff

router = APIRouter(prefix="/api/go-live")


class Step(BaseModel):
    key: str
    label: str
    # What the step is, in a line, under its label.
    detail: str
    done: bool
    # A step a company can go live without (it may want it later).
    required: bool
    # The page where it is done.
    href: str


class Checklist(BaseModel):
    steps: list[Step]
    # Every required step is done.
    ready: bool


def _yes(conn: psycopg.Connection[Any], query: str, pmc_id: UUID) -> bool:
    row = conn.execute(f"SELECT EXISTS ({query})", (pmc_id,)).fetchone()
    return bool(row and row[0])


def steps_for(conn: psycopg.Connection[Any], pmc_id: UUID, sends_email: bool) -> list[Step]:
    address = conn.execute(
        "SELECT address_line_1 IS NOT NULL FROM trust_company_profiles WHERE pmc_id = %s"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (pmc_id,),
    ).fetchone()
    kinds = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT kind FROM trust_bank_accounts WHERE pmc_id = %s", (pmc_id,)
        )
    }
    accounts = signers.signers_on(conn, pmc_id, days.today(conn, pmc_id))
    brokers = bool(accounts) and all(
        account.signers and account.signers[0].role == "broker" for account in accounts
    )
    staff_logins = conn.execute(
        "SELECT count(*) FROM trust_logins"
        " WHERE pmc_id = %s AND kind = 'staff' AND disabled_at IS NULL",
        (pmc_id,),
    ).fetchone()
    online = conn.execute(
        "SELECT online FROM trust_payment_settings WHERE pmc_id = %s"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (pmc_id,),
    ).fetchone()
    return [
        Step(
            key="company",
            label="Company details",
            detail="Your company's mailing address, on letters, statements and receipts",
            done=bool(address and address[0]),
            required=True,
            href="/settings/setup",
        ),
        Step(
            key="bank_accounts",
            label="Trust bank accounts",
            detail="An operating account and a security deposit account",
            done={"operating", "security_deposit"} <= kinds,
            required=True,
            href="/banking",
        ),
        Step(
            key="brokers",
            label="A broker for each trust account",
            detail="Who is responsible for each account, and who may sign",
            done=brokers,
            required=True,
            href="/settings/staff",
        ),
        Step(
            key="properties",
            label="Properties and owners",
            detail="At least one property, with its owner",
            done=_yes(conn, "SELECT 1 FROM trust_properties WHERE pmc_id = %s", pmc_id),
            required=True,
            href="/properties",
        ),
        Step(
            key="leases",
            label="Leases in place",
            detail="Leases and their tenants, brought over or added",
            done=_yes(conn, "SELECT 1 FROM trust_leases WHERE pmc_id = %s", pmc_id),
            required=True,
            href="/rent-roll",
        ),
        Step(
            key="team",
            label="A second person for month-end",
            detail="One person prepares the month-end and another approves it",
            done=bool(staff_logins and staff_logins[0] >= 2),
            required=True,
            href="/settings/staff",
        ),
        Step(
            key="email",
            label="Email sending",
            detail="Invites, receipts and notices reach people (set by whoever runs the server)",
            done=sends_email,
            required=True,
            href="/settings/setup",
        ),
        Step(
            key="portals",
            label="Owners and tenants invited",
            detail="Their portals, to read statements, pay rent and ask for repairs",
            done=_yes(
                conn,
                "SELECT 1 FROM trust_logins WHERE pmc_id = %s AND kind IN ('owner', 'tenant')",
                pmc_id,
            ),
            required=False,
            href="/owners",
        ),
        Step(
            key="online_rent",
            label="Rent paid online",
            detail="Tenants pay by bank from their portal, with autopay",
            done=bool(online and online[0]),
            required=False,
            href="/settings/setup",
        ),
    ]


@router.get("")
def checklist(session: Staff, conn: Conn, request: Request) -> Checklist:
    """The company's go-live steps, each done or not by its own records."""
    found = steps_for(conn, session.pmc_id, request.app.state.email is not None)
    return Checklist(steps=found, ready=all(step.done for step in found if step.required))
