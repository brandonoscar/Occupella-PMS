"""Property-based tests for lease offers and completing them (migration 20261011000031).

Hypothesis approves applications for one unit, offers them leases (and renewals of the leases
those became) with random terms, sends and signs offers, and completes them, in random order. A
Python model predicts every step: which leases the unit has, from and to when, with whose names;
the database must accept exactly what the model accepts and refuse the rest, writing nothing.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - the unit's leases are the model's, and never overlap;
  - an offer became a lease only once everyone signed, and an application or a lease took at
    most one completed offer;
  - a new lease's tenants are its offer's signers; a renewal keeps the tenants of the lease it
    renews, which ends the day before the renewal starts.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date, timedelta

import psycopg
import pytest
from helpers import (
    add_staff,
    add_unit,
    complete_offer,
    make_pmc,
    offer_lease,
    open_application_link,
    record_decision,
    send_offer,
    sign_offer,
    submit_application,
)
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

BASE = date(2027, 1, 1)
STARTS = st.integers(min_value=0, max_value=36).map(
    lambda months: BASE + timedelta(days=30 * months)
)
LENGTHS = st.one_of(st.none(), st.integers(min_value=0, max_value=400))


def overlaps(a_start, a_end, b_start, b_end):
    far = date.max
    return a_start <= (b_end or far) and b_start <= (a_end or far)


class LeaseOfferMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        pmc = make_pmc(conn)
        self.pmc_id = pmc.pmc_id
        self.unit = add_unit(conn, self.pmc_id, pmc.owners[0].property_id, "Unit 1")
        self.staff = add_staff(conn, self.pmc_id)
        self.link = open_application_link(conn, self.pmc_id, self.unit, self.staff)
        # Model, in the order things happened so replays pick the same ones.
        self.applications: list = []
        # offer -> its application or the lease it renews, terms, signers, sent?, signed places
        self.offers: dict = {}
        self.leases: dict = {}  # lease -> [starts, ends, tenant names]
        self.completed: dict = {}  # offer -> lease

    # --- applications and offers ---------------------------------------------------------

    @rule()
    def approve_an_application(self):
        n = len(self.applications) + 1
        with self.conn.transaction():
            application = submit_application(
                self.conn, self.pmc_id, self.link, applicants=[(f"Applicant {n}", None)]
            )
            record_decision(self.conn, self.pmc_id, application, self.staff, "approved")
        self.applications.append(application)

    def make_offer(self, starts, length, signers, application=None, renews=None):
        ends = None if length is None else starts + timedelta(days=length)
        offer = offer_lease(
            self.conn,
            self.pmc_id,
            self.unit,
            self.staff,
            starts,
            ends,
            application_id=application,
            renews_lease_id=renews,
            signers=signers,
        )
        self.offers[offer] = {
            "application": application,
            "renews": renews,
            "starts": starts,
            "ends": ends,
            "signers": [name for name, _ in signers],
            "sent": False,
            "signed": set(),
        }

    @precondition(lambda self: self.applications)
    @rule(data=st.data(), starts=STARTS, length=LENGTHS, people=st.integers(1, 2))
    def offer_a_lease(self, data, starts, length, people):
        application = data.draw(st.sampled_from(self.applications))
        n = len(self.offers) + 1
        signers = tuple((f"Signer {n}.{k}", f"signer{n}.{k}@example.com") for k in range(people))
        with self.conn.transaction():
            self.make_offer(starts, length, signers, application=application)

    @precondition(lambda self: self.leases)
    @rule(data=st.data(), later=st.integers(min_value=-5, max_value=400), length=LENGTHS)
    def offer_a_renewal(self, data, later, length):
        lease = data.draw(st.sampled_from(list(self.leases)))
        starts = self.leases[lease][0] + timedelta(days=later)
        n = len(self.offers) + 1
        signers = ((f"Renewer {n}", f"renewer{n}@example.com"),)
        if later <= 0:
            refused = pytest.raises(psycopg.errors.CheckViolation, match="starts after")
            with refused, self.conn.transaction():
                self.make_offer(starts, length, signers, renews=lease)
            return
        with self.conn.transaction():
            self.make_offer(starts, length, signers, renews=lease)

    @precondition(lambda self: any(not o["sent"] for o in self.offers.values()))
    @rule(data=st.data())
    def send(self, data):
        offer = data.draw(st.sampled_from([o for o, v in self.offers.items() if not v["sent"]]))
        send_offer(self.conn, self.pmc_id, offer, self.staff)
        self.offers[offer]["sent"] = True

    @precondition(
        lambda self: any(
            v["sent"] and len(v["signed"]) < len(v["signers"]) for v in self.offers.values()
        )
    )
    @rule(data=st.data())
    def sign(self, data):
        offer = data.draw(
            st.sampled_from(
                [
                    o
                    for o, v in self.offers.items()
                    if v["sent"] and len(v["signed"]) < len(v["signers"])
                ]
            )
        )
        model = self.offers[offer]
        position = min(set(range(1, len(model["signers"]) + 1)) - model["signed"])
        sign_offer(self.conn, self.pmc_id, offer, position)
        model["signed"].add(position)

    # --- completing ----------------------------------------------------------------------

    @precondition(lambda self: self.offers)
    @rule(data=st.data())
    def complete(self, data):
        offer = data.draw(st.sampled_from(list(self.offers)))
        model = self.offers[offer]

        def attempt():
            with self.conn.transaction():
                return complete_offer(self.conn, self.pmc_id, offer, self.staff)

        if offer in self.completed:
            assert attempt() == self.completed[offer]
            return
        target = model["application"] or model["renews"]
        taken = any(
            (self.offers[o]["application"] or self.offers[o]["renews"]) == target
            for o in self.completed
        )
        old = self.leases.get(model["renews"])
        ends_old = old is not None and (old[1] is None or old[1] >= model["starts"])
        others = [(s, e) for lease, (s, e, _) in self.leases.items() if lease != model["renews"]]
        if len(model["signed"]) < len(model["signers"]):
            refusal = (psycopg.errors.CheckViolation, "not signed by everyone")
        elif taken:
            refusal = (psycopg.errors.CheckViolation, "became its lease already")
        elif any(overlaps(model["starts"], model["ends"], s, e) for s, e in others):
            refusal = (psycopg.errors.ExclusionViolation, "would overlap")
        else:
            lease = attempt()
            if old is not None and ends_old:
                old[1] = model["starts"] - timedelta(days=1)
            tenants = old[2] if old is not None else sorted(model["signers"])
            self.leases[lease] = [model["starts"], model["ends"], tenants]
            self.completed[offer] = lease
            return
        error, message = refusal
        with pytest.raises(error, match=message):
            attempt()

    # --- invariants ----------------------------------------------------------------------

    @invariant()
    def the_units_leases_are_the_models(self):
        rows = self.conn.execute(
            "SELECT l.id, l.starts_on, l.ends_on,"
            " array(SELECT t.display_name FROM trust_lease_tenants AS lt"
            "       JOIN trust_tenants AS t ON t.id = lt.tenant_id"
            '       WHERE lt.lease_id = l.id ORDER BY t.display_name COLLATE "C")'
            " FROM trust_leases AS l WHERE l.unit_id = %s",
            (self.unit,),
        ).fetchall()
        assert {i: [s, e, names] for i, s, e, names in rows} == self.leases
        spans = sorted((s, e) for _, s, e, _ in rows)
        for (s1, e1), (s2, e2) in zip(spans, spans[1:], strict=False):
            assert not overlaps(s1, e1, s2, e2)

    @invariant()
    def completions_are_the_models_and_follow_every_signature(self):
        rows = self.conn.execute(
            "SELECT c.offer_id, c.lease_id,"
            " (SELECT count(*) FROM trust_lease_offer_signers AS s WHERE s.offer_id = c.offer_id),"
            " (SELECT count(*) FROM trust_signatures AS g WHERE g.offer_id = c.offer_id)"
            " FROM trust_lease_offer_completions AS c WHERE c.pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert {offer: lease for offer, lease, _, _ in rows} == self.completed
        assert all(signers == signed > 0 for _, _, signers, signed in rows)


def test_lease_offer_invariants_hold_under_random_offers_signatures_and_completions(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: LeaseOfferMachine(conn))
