"""Property-based tests for a company's own Stripe keys (migration 20261104000800,
src/pms/payments.py's Resolver, src/pms/api/online_payments.py's company webhook).

Hypothesis gives two companies keys of their own, replaces them and takes them away, starts
payments under whatever keys each holds, and sends Stripe's signed report that the bank paid, for
any payment, to either company's webhook, signed with any secret either company ever held or one
neither did, as often as it likes. A model predicts each answer.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - a company's webhook answers only once it has held keys, and takes a report only signed with a
    secret it has held, the latest or one before;
  - a payment is in the books only once a report reached its own company's webhook, at most
    once however often it is sent: another company's webhook never records it;
  - each company's payments go through its latest keys, or the server's partner once it has
    none: never another company's, never keys since taken away;
  - on a live server (docs/edge-cases.md P-048) a company's test keys take no payment and no
    report, and a report whose livemode isn't its keys' mode is never booked.

Key sets alternate test (even) and live (odd), the server may go live at any step, and each
report says one mode or the other.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import psycopg
from fastapi import HTTPException
from helpers import add_unit, charge, give_roles, make_pmc, open_lease, staff_named
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from pms import payments
from pms.api import online_payments as op

TODAY = datetime.now(UTC).date()
# Made-up keys, spelled in pieces so no secret scanner mistakes them for real ones: key set i.
KEY_SETS = 4


def live(i: int) -> bool:
    """Key set i's mode: odd sets are live keys, even sets test keys."""
    return i % 2 == 1


def secret_key(i: int) -> str:
    return "sk_" + ("live_" if live(i) else "test_") + f"Synthetic{i:016d}Key{i}"


def webhook_secret(i: int) -> str:
    return "whsec_" + f"Synthetic{i:016d}Hook{i}"


@dataclass
class Company:
    pmc_id: uuid.UUID
    admin: uuid.UUID
    lease: uuid.UUID
    tenant: uuid.UUID
    keys: int | None = None  # the key set in force, or none
    held: set[int] = field(default_factory=set)  # every key set it ever held


@dataclass
class Paying:
    id: uuid.UUID
    company: int
    session: str
    paid: bool = False


class PaymentKeysMachine(RuleBasedStateMachine):
    def __init__(self, conn, data_key: bytes):
        super().__init__()
        self.conn = conn
        self.data_key = data_key
        self.resolver = payments.Resolver(None)
        self.companies = [self.company(conn) for _ in range(2)]
        self.paying: list[Paying] = []

    @staticmethod
    def company(conn) -> Company:
        pmc = make_pmc(conn, owners=1)
        admin = staff_named(conn, pmc.pmc_id, "Admin 1")
        give_roles(conn, pmc.pmc_id, admin, ("admin",))
        unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
        tenant = conn.execute(
            "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
            (pmc.prepaid_rent,),
        ).fetchone()[0]
        lease = open_lease(
            conn, pmc.pmc_id, unit, TODAY - timedelta(days=30), None, "1000.00", [tenant]
        )
        # Enough owed that every payment pays the owner.
        charge(conn, pmc.pmc_id, lease, TODAY - timedelta(days=30), Decimal("10000000.00"))
        return Company(pmc.pmc_id, admin, lease, tenant)

    def as_admin(self, c: Company, sql: str, args: tuple) -> None:
        with self.conn.transaction():
            self.conn.execute("SELECT set_config('trust.staff_id', %s, true)", (str(c.admin),))
            self.conn.execute(sql, args)

    # --- rules --------------------------------------------------------------------------------

    @rule(which=st.integers(min_value=0, max_value=1), keys=st.integers(0, KEY_SETS - 1))
    def enter_keys(self, which, keys):
        c = self.companies[which]
        row = uuid.uuid4()
        sealed_secret, sealed_webhook = payments.seal_keys(
            row, secret_key(keys), webhook_secret(keys), self.data_key
        )
        self.as_admin(
            c,
            "INSERT INTO trust_payment_keys (id, pmc_id, secret_key_sealed, webhook_secret_sealed,"
            " secret_key_last4, mode) VALUES (%s, %s, %s, %s, %s, %s)",
            (
                row,
                c.pmc_id,
                sealed_secret,
                sealed_webhook,
                secret_key(keys)[-4:],
                "live" if live(keys) else "test",
            ),
        )
        c.keys = keys
        c.held.add(keys)

    @precondition(lambda self: not self.resolver.live)
    @rule()
    def go_live(self):
        """The server moves to an https address of its own (deployment.is_live)."""
        self.resolver.live = True

    def usable(self, keys: int | None) -> bool:
        """Whether a key set takes payments and reports on the server as it is now."""
        return keys is not None and (live(keys) or not self.resolver.live)

    @rule(which=st.integers(min_value=0, max_value=1))
    def take_keys_away(self, which):
        c = self.companies[which]
        self.as_admin(c, "INSERT INTO trust_payment_keys (pmc_id) VALUES (%s)", (c.pmc_id,))
        c.keys = None

    @precondition(lambda self: any(c.keys is not None for c in self.companies))
    @rule(which=st.integers(min_value=0, max_value=1))
    def start(self, which):
        c = self.companies[which]
        if not self.usable(c.keys):
            return  # no partner (or test keys on a live server): no payment starts
        payment, session = uuid.uuid4(), f"cs_{uuid.uuid4().hex}"
        self.conn.execute(
            "INSERT INTO trust_online_payments (id, pmc_id, lease_id, tenant_id, amount,"
            " tenant_fee, fee_payer, method, partner, partner_ref)"
            " VALUES (%s, %s, %s, %s, 100.00, 0, 'company', 'ach', 'stripe', %s)",
            (payment, c.pmc_id, c.lease, c.tenant, session),
        )
        self.paying.append(Paying(payment, which, session))

    @precondition(lambda self: self.paying)
    @rule(
        which=st.integers(min_value=0),
        to=st.integers(min_value=0, max_value=1),
        signed_with=st.integers(min_value=0, max_value=KEY_SETS),  # KEY_SETS: no one's
        livemode=st.booleans(),
    )
    def report(self, which, to, signed_with, livemode):
        p = self.paying[which % len(self.paying)]
        target = self.companies[to]
        now = int(time.time())
        body = json.dumps(
            {
                "id": f"evt_{uuid.uuid4().hex}",
                "type": "checkout.session.async_payment_succeeded",
                "created": now,
                "livemode": livemode,
                "data": {
                    "object": {
                        "id": p.session,
                        "client_reference_id": str(p.id),
                        "payment_intent": f"pi_{p.id.hex}",
                    }
                },
            }
        ).encode()
        secret = webhook_secret(signed_with)
        mac = hmac.new(secret.encode(), f"{now}.".encode() + body, hashlib.sha256).hexdigest()
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(payments=self.resolver)),
            headers={"stripe-signature": f"t={now},v1={mac}"},
        )
        usable = {k for k in target.held if self.usable(k)}
        try:
            with self.conn.transaction():
                answer = op.company_webhook(request, self.conn, "stripe", target.pmc_id, body)
        except HTTPException as refused:
            if not usable:
                assert refused.status_code == 404
                event("a webhook of a company with no keys it may use")
            else:
                assert signed_with not in usable and refused.status_code == 400
                event("a report signed with a secret the company can't take one with")
            return
        assert signed_with in usable
        same_mode = livemode is live(signed_with)
        assert answer.recorded is (p.company == to and same_mode)
        if not same_mode:
            event("a report from the other mode, booked nowhere")
        elif p.company == to:
            event("a report sent again" if p.paid else "a payment paid")
            p.paid = True
        else:
            event("another company's payment, recorded nothing")

    # --- invariants ---------------------------------------------------------------------------

    @invariant()
    def a_payment_is_in_the_books_once_its_own_company_heard(self):
        for p in self.paying:
            found = self.conn.execute(
                "SELECT count(*) FROM trust_online_payment_events"
                " WHERE payment_id = %s AND kind = 'paid'",
                (p.id,),
            ).fetchone()
            assert found == (1 if p.paid else 0,)

    @invariant()
    def each_company_pays_through_its_latest_keys(self):
        for c in self.companies:
            with self.conn.transaction():
                partner = self.resolver.for_pmc(self.conn, c.pmc_id)
            if not self.usable(c.keys):
                # No keys, or test keys on a live server; the server has no partner here.
                assert partner is None
            else:
                assert isinstance(partner, payments.StripePartner)
                assert partner._key == secret_key(c.keys)
                assert partner._webhook_secret == webhook_secret(c.keys)


def test_a_companys_keys_take_only_its_own_payments_and_its_latest_keys_hold(
    database_url, monkeypatch
):
    data_key = os.urandom(32)
    monkeypatch.setenv("PMS_DATA_KEY", base64.b64encode(data_key).decode())
    # Stripe's fee on a payment: none, so nothing waits for the company to cover.
    monkeypatch.setattr(
        payments.StripePartner,
        "_call",
        lambda self, method, path, form, key="": {
            "latest_charge": {"balance_transaction": {"fee": 0}}
        },
    )
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: PaymentKeysMachine(conn, data_key))
