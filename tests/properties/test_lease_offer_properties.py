"""Property-based tests for lease offers and completing them (migration 20261011000031), with
drafts' monthly charges and voids (migration 20261016000071), signing on the PMS's own page
(migration 20261020000097, ADR 0008) and renewals that follow the lease they renew (migration
20261022000152).

Hypothesis approves applications for one unit, offers them leases (and renewals of the leases
those became) with random terms, adds monthly charges to offers, sends (through a partner or to
sign here), makes signers' links, agrees, opens, asks for paper and signs through them, signs and
voids offers, ends leases early, and completes offers, in random order. A Python model predicts
every step: which leases the unit has, from and to when, with whose names and which recurring
charges; the database must accept exactly what the model accepts and refuse the rest, writing
nothing.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - the unit's leases are the model's, and never overlap;
  - an offer became a lease only once everyone signed, and an application or a lease took at
    most one completed offer;
  - a new lease's tenants are its offer's signers; a renewal keeps the tenants of the lease it
    renews, which ends the day before the renewal starts;
  - an offer's charges are written only before it is sent, and the lease it becomes owes exactly
    them as recurring charges from its first day; a void offer is never sent, signed or made a
    lease, and an offer is voided only before it is a lease (migration 20261016000071);
  - an offer sent to sign here is signed only through its signer's own link, once the signer
    agreed to sign electronically and then opened the lease, never after asking for paper through
    it, with their name as it is on the offer and the SHA-256 of the lease sent; a link is made
    and used only while its offer is neither void nor a lease and its signer hasn't signed
    (migration 20261020000097);
  - a renewal not yet a lease is never sent, given a link, acted on or signed through one, signed
    or made the lease once the lease it renews ends before the day before it starts, however that
    end came (a renewal of the same lease made the lease, or staff ending it early), and a lease
    ended early stays ended (migration 20261022000152).

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date, timedelta
from uuid import uuid4

import psycopg
import pytest
from helpers import (
    add_staff,
    add_tenant,
    add_unit,
    complete_offer,
    make_pmc,
    offer_lease,
    open_application_link,
    open_lease,
    record_decision,
    send_offer,
    sign_offer,
    submit_application,
)
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from pms.leasing import esign

BASE = date(2027, 1, 1)
STARTS = st.integers(min_value=0, max_value=36).map(
    lambda months: BASE + timedelta(days=30 * months)
)
LENGTHS = st.one_of(st.none(), st.integers(min_value=0, max_value=400))


def overlaps(a_start, a_end, b_start, b_end):
    far = date.max
    return a_start <= (b_end or far) and b_start <= (a_end or far)


class SigningOnThePage:
    """Signing an offer sent to sign here (migration 20261020000097): its signers' links, what
    they do through them, and their signatures, against the model kept in self.offers (each
    with "signers", "signed" and "here"), self.voided, self.completed and self.links."""

    def send_here(self, offer):
        self.conn.execute(
            "INSERT INTO trust_signature_requests (pmc_id, offer_id, partner, partner_reference,"
            " sent_by) VALUES (%s, %s, %s, %s, %s)",
            (self.pmc_id, offer, esign.PARTNER, esign.reference(offer), self.staff),
        )

    def closed(self, offer):
        """trust_renewal_closed, as the model sees it: a renewal not yet a lease whose lease ends
        before the day before it starts (no tenant moves and no deposit is returned here)."""
        model = self.offers[offer]
        if model.get("renews") is None or offer in self.completed:
            return False
        ends = self.leases[model["renews"]][1]
        return ends is not None and ends < model["starts"] - timedelta(days=1)

    def link_signs(self, link):
        """trust_signing_link_open, as the model sees it (a link here is never 30 days old)."""
        offer, position = self.links[link]["offer"], self.links[link]["position"]
        return (
            offer not in self.voided
            and offer not in self.completed
            and position not in self.offers[offer]["signed"]
            and not self.closed(offer)
        )

    def new_link(self, offer, position):
        return self.conn.execute(
            "INSERT INTO trust_signing_links (pmc_id, offer_id, position, token_sha256)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (self.pmc_id, offer, position, esign.token_digest(str(uuid4()))),
        ).fetchone()[0]

    @precondition(lambda self: any(v["here"] for v in self.offers.values()))
    @rule(data=st.data())
    def make_another_link(self, data):
        offer = data.draw(st.sampled_from([o for o, v in self.offers.items() if v["here"]]))
        model = self.offers[offer]
        position = data.draw(st.integers(1, len(model["signers"])))

        def make():
            return self.new_link(offer, position)

        if offer in self.voided:
            message = "was voided"
        elif self.closed(offer):
            message = "can no longer be sent, signed or made a lease: the lease it renews"
        elif offer in self.completed:
            message = "a lease already"
        elif position in model["signed"]:
            message = "signed already"
        else:
            self.links[make()] = {"offer": offer, "position": position, "events": set()}
            return
        with pytest.raises(psycopg.errors.CheckViolation, match=message), self.conn.transaction():
            make()

    @precondition(lambda self: self.links)
    @rule(data=st.data(), kind=st.sampled_from(["consented", "opened", "paper"]))
    def act_through_a_link(self, data, kind):
        """Agree to sign electronically, open the lease, or ask to sign on paper."""
        self.act(data.draw(st.sampled_from(list(self.links))), kind)

    def act(self, link, kind):
        events = self.links[link]["events"]

        def act():
            self.conn.execute(
                "INSERT INTO trust_signing_events (link_id, kind, pmc_id, disclosure, ip,"
                " user_agent) VALUES (%s, %s, %s, %s, '192.0.2.10', 'A browser')",
                (link, kind, self.pmc_id, "The disclosures." if kind == "consented" else None),
            )

        if not self.link_signs(link):
            refusal = (psycopg.errors.CheckViolation, "no longer signs")
        elif kind != "paper" and "paper" in events:
            refusal = (psycopg.errors.CheckViolation, "on paper")
        elif kind == "opened" and "consented" not in events:
            refusal = (psycopg.errors.CheckViolation, "before opening")
        elif kind in events:
            refusal = (psycopg.errors.UniqueViolation, None)
        else:
            act()
            events.add(kind)
            return
        error, message = refusal
        with pytest.raises(error, match=message), self.conn.transaction():
            act()

    @precondition(lambda self: self.links)
    @rule(
        data=st.data(),
        # What the signer does through the link first: most read, agree and open, as the page
        # leads them; some skip a step, go out of order or ask for paper.
        first=st.sampled_from(
            [
                ("consented", "opened"),
                ("consented", "opened"),
                ("consented", "opened"),
                (),
                ("consented",),
                ("opened",),
                ("opened", "consented"),
                ("consented", "paper"),
                ("consented", "opened", "paper"),
            ]
        ),
        name=st.sampled_from(["as is", "case and spaces", "another"]),
        lease=st.sampled_from(["sent", "sent", "another"]),
    )
    def sign_through_a_link(self, data, first, name, lease):
        link = data.draw(st.sampled_from(list(self.links)))
        for kind in first:
            self.act(link, kind)
        offer, position = self.links[link]["offer"], self.links[link]["position"]
        events = self.links[link]["events"]
        signer = self.offers[offer]["signers"][position - 1]
        typed = {"as is": signer, "case and spaces": f"  {signer.upper()} "}.get(name, "Nobody")

        def sign():
            self.conn.execute(
                "INSERT INTO trust_signature_evidence (pmc_id, offer_id, position, link_id,"
                " typed_name, document_sha256, ip, user_agent)"
                " SELECT %s, o.id, %s, %s, %s,"
                "  CASE WHEN %s THEN sha256(o.document)"
                "  ELSE sha256(convert_to('Another lease', 'UTF8')) END,"
                "  '192.0.2.10', 'A browser'"
                " FROM trust_lease_offers AS o WHERE o.id = %s",
                (self.pmc_id, position, link, typed, lease == "sent", offer),
            )

        if not self.link_signs(link):
            message = "no longer signs"
        elif "paper" in events:
            message = "on paper"
        elif not {"consented", "opened"} <= events:
            message = "opens the lease first"
        elif name == "another":
            message = "type your name"
        elif lease == "another":
            message = "not the lease sent"
        else:
            sign()
            self.offers[offer]["signed"].add(position)
            return
        with pytest.raises(psycopg.errors.CheckViolation, match=message), self.conn.transaction():
            sign()

    @invariant()
    def a_signature_here_is_the_signers_own_through_a_link_they_agreed_and_opened(self):
        here = [o for o, v in self.offers.items() if v["here"]]
        rows = self.conn.execute(
            "SELECT g.offer_id, g.position, e.typed_name,"
            " e.document_sha256 = sha256(o.document), l.offer_id = g.offer_id"
            " AND l.position = g.position,"
            " (SELECT array_agg(v.kind ORDER BY v.created_at) FROM trust_signing_events AS v"
            "  WHERE v.link_id = l.id AND v.created_at <= e.signed_at)"
            " FROM trust_signatures AS g"
            " JOIN trust_lease_offers AS o ON o.id = g.offer_id"
            " LEFT JOIN trust_signature_evidence AS e"
            "  ON e.offer_id = g.offer_id AND e.position = g.position"
            " LEFT JOIN trust_signing_links AS l ON l.id = e.link_id"
            " WHERE g.offer_id = ANY(%s)",
            (here,),
        ).fetchall()
        assert {(o, p) for o, p, *_ in rows} == {
            (o, p) for o in here for p in self.offers[o]["signed"]
        }
        for offer, position, typed, same_lease, own_link, done in rows:
            signer = self.offers[offer]["signers"][position - 1]
            assert typed is not None and typed.strip().lower() == signer.lower()
            assert same_lease and own_link
            assert done == ["consented", "opened"]
        made = self.conn.execute(
            "SELECT l.id, l.offer_id, l.position, coalesce((SELECT array_agg(v.kind)"
            "  FROM trust_signing_events AS v WHERE v.link_id = l.id), '{}')"
            " FROM trust_signing_links AS l WHERE l.pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert {
            link: {"offer": o, "position": p, "events": set(kinds)} for link, o, p, kinds in made
        } == self.links


class LeaseOfferMachine(SigningOnThePage, RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        pmc = make_pmc(conn)
        self.pmc_id = pmc.pmc_id
        self.property = pmc.owners[0].property_id
        self.unit = add_unit(conn, self.pmc_id, self.property, "Unit 1")
        self.staff = add_staff(conn, self.pmc_id)
        self.link = open_application_link(conn, self.pmc_id, self.unit, self.staff)
        # Model, in the order things happened so replays pick the same ones.
        self.applications: list = []
        # offer -> its application or the lease it renews, terms, signers, sent?, signed places
        self.offers: dict = {}
        self.leases: dict = {}  # lease -> [starts, ends, tenant names]
        self.completed: dict = {}  # offer -> lease
        self.voided: set = set()
        self.links: dict = {}  # link -> {"offer", "position", "events": kinds done through it}

    @initialize(short=st.integers(min_value=0, max_value=60))
    def a_lease_in_place(self, short):
        """Tenant 0's lease of the unit for the year before the offers, ending up to 60 days
        short of it: renewals of it start before, on or after the day after it ends."""
        starts = BASE - timedelta(days=365)
        ends = BASE - timedelta(days=1 + short)
        tenant = add_tenant(self.conn, self.pmc_id, self.property, "Tenant 0")
        lease = open_lease(self.conn, self.pmc_id, self.unit, starts, ends, "1400.00", [tenant])
        self.leases[lease] = [starts, ends, ["Tenant 0"]]

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
            "here": False,  # sent to sign on the PMS's own page
            "signed": set(),
            "charges": [],  # (kind, amount, memo), in position order
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

    @precondition(lambda self: self.offers)
    @rule(
        data=st.data(),
        kind=st.sampled_from(["fee", "credit"]),
        cents=st.integers(min_value=1, max_value=50_000),
    )
    def add_a_charge(self, data, kind, cents):
        """A monthly fee or credit on the offer: only while it is a draft (not sent)."""
        offer = data.draw(st.sampled_from(list(self.offers)))
        model = self.offers[offer]
        amount = f"{cents // 100}.{cents % 100:02d}"
        position = len(model["charges"]) + 1
        if position > 10:
            return
        charge = (
            "INSERT INTO trust_lease_offer_charges (offer_id, position, pmc_id, kind, amount,"
            " memo) VALUES (%s, %s, %s, %s, %s, %s)"
        )
        memo = f"Charge {position}"
        if model["sent"]:
            refused = pytest.raises(psycopg.errors.CheckViolation, match="before it is sent")
            with refused, self.conn.transaction():
                self.conn.execute(charge, (offer, position, self.pmc_id, kind, amount, memo))
            return
        self.conn.execute(charge, (offer, position, self.pmc_id, kind, amount, memo))
        model["charges"].append((kind, amount, memo))

    @precondition(lambda self: any(not o["sent"] for o in self.offers.values()))
    @rule(data=st.data(), here=st.booleans())
    def send(self, data, here):
        offer = data.draw(st.sampled_from([o for o, v in self.offers.items() if not v["sent"]]))
        sending = (
            self.send_here
            if here
            else (lambda o: send_offer(self.conn, self.pmc_id, o, self.staff))
        )
        if offer in self.voided or self.closed(offer):
            message = "was voided" if offer in self.voided else "can no longer be sent"
            refused = pytest.raises(psycopg.errors.CheckViolation, match=message)
            with refused, self.conn.transaction():
                sending(offer)
            return
        sending(offer)
        self.offers[offer].update(sent=True, here=here)
        if here:  # as staff do, a link for each signer
            for position in range(1, len(self.offers[offer]["signers"]) + 1):
                self.links[self.new_link(offer, position)] = {
                    "offer": offer,
                    "position": position,
                    "events": set(),
                }

    @precondition(lambda self: self.offers)
    @rule(data=st.data())
    def void(self, data):
        """Once, and only before the offer is a lease."""
        offer = data.draw(st.sampled_from(list(self.offers)))
        voiding = (
            "INSERT INTO trust_lease_offer_voids (offer_id, pmc_id, reason) VALUES (%s, %s, %s)"
        )
        if offer in self.completed or offer in self.voided:
            refusal = (
                psycopg.errors.CheckViolation
                if offer in self.completed
                else psycopg.errors.UniqueViolation
            )
            with pytest.raises(refusal), self.conn.transaction():
                self.conn.execute(voiding, (offer, self.pmc_id, "Terms changed"))
            return
        self.conn.execute(voiding, (offer, self.pmc_id, "Terms changed"))
        self.voided.add(offer)

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
        if offer in self.voided or self.closed(offer) or model["here"]:
            # An offer sent to sign here takes a signature only through its signer's link.
            message = (
                "was voided"
                if offer in self.voided
                else "can no longer be sent, signed"
                if self.closed(offer)
                else "through the signer's link"
            )
            refused = pytest.raises(psycopg.errors.CheckViolation, match=message)
            with refused, self.conn.transaction():
                sign_offer(self.conn, self.pmc_id, offer, position)
            return
        sign_offer(self.conn, self.pmc_id, offer, position)
        model["signed"].add(position)

    @precondition(lambda self: self.leases)
    @rule(data=st.data(), days=st.integers(min_value=0, max_value=400))
    def end_a_lease_early(self, data, days):
        """Staff end a lease early, or one with no end (H7): never past its end, so it runs into
        no other lease. A renewal of it starting more than a day after takes nothing more."""
        lease = data.draw(st.sampled_from(list(self.leases)))
        starts, ends, _ = self.leases[lease]
        new_end = starts + timedelta(days=days)
        if ends is not None and new_end >= ends:
            return
        self.end_lease(lease, new_end)

    def end_lease(self, lease, ends):
        self.conn.execute("SELECT trust_end_lease(%s, %s, %s)", (self.pmc_id, lease, ends))
        self.leases[lease][1] = ends

    @precondition(
        lambda self: any(
            v["renews"] is not None and o not in self.completed for o, v in self.offers.items()
        )
    )
    @rule(data=st.data(), gap=st.integers(min_value=0, max_value=60))
    def end_a_renewed_lease_early(self, data, gap):
        """H7: staff end a lease while a renewal of it waits (a draft, sent or signed), on the day
        before it starts (a gap of 0) or earlier."""
        offer = data.draw(
            st.sampled_from(
                [
                    o
                    for o, v in self.offers.items()
                    if v["renews"] is not None and o not in self.completed
                ]
            )
        )
        lease = self.offers[offer]["renews"]
        starts, ends, _ = self.leases[lease]
        new_end = self.offers[offer]["starts"] - timedelta(days=1 + gap)
        if new_end < starts or (ends is not None and new_end >= ends):
            return
        self.end_lease(lease, new_end)

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
        if offer in self.voided:
            # Whatever the database finds first (a missing signature, an overlap, the void),
            # a void offer is made no lease, and nothing is written.
            refusals = (psycopg.errors.CheckViolation, psycopg.errors.ExclusionViolation)
            with pytest.raises(refusals):
                attempt()
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
        elif self.closed(offer):
            refusal = (psycopg.errors.CheckViolation, "can no longer be sent, signed or made")
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
    def each_lease_owes_its_offers_charges_and_no_void_offer_moved_on(self):
        for offer, lease in self.completed.items():
            rows = self.conn.execute(
                # One statement writes them all, so they share their time: compared in any order.
                "SELECT kind, amount::text, memo, starts_on FROM trust_recurring_charges"
                " WHERE lease_id = %s",
                (lease,),
            ).fetchall()
            model = self.offers[offer]
            assert sorted(rows) == sorted(
                (k, a, m, model["starts"]) for k, a, m in model["charges"]
            )
        (moved,) = self.conn.execute(
            "SELECT count(*) FROM trust_lease_offer_voids AS v WHERE v.pmc_id = %s AND ("
            " EXISTS (SELECT 1 FROM trust_lease_offer_completions AS c"
            "  WHERE c.offer_id = v.offer_id)"
            " OR EXISTS (SELECT 1 FROM trust_signature_requests AS r WHERE r.offer_id = v.offer_id"
            "  AND r.sent_at > v.recorded_at)"
            " OR EXISTS (SELECT 1 FROM trust_signatures AS g WHERE g.offer_id = v.offer_id"
            "  AND g.recorded_at > v.recorded_at))",
            (self.pmc_id,),
        ).fetchone()
        assert moved == 0
        voided = self.conn.execute(
            "SELECT offer_id FROM trust_lease_offer_voids WHERE pmc_id = %s", (self.pmc_id,)
        ).fetchall()
        assert {v for (v,) in voided} == self.voided
        # A charge refused once the offer was sent wrote nothing: each offer has the model's.
        for offer, model in self.offers.items():
            rows = self.conn.execute(
                "SELECT kind, amount::text, memo FROM trust_lease_offer_charges"
                " WHERE offer_id = %s ORDER BY position",
                (offer,),
            ).fetchall()
            assert rows == model["charges"]

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


class SigningMachine(SigningOnThePage, RuleBasedStateMachine):
    """One offer sent to sign here, its signers' links worked at random: agreeing, opening,
    asking for paper and signing in any order, with their name or another and the lease sent or
    another, more links, and a void or the lease made of it."""

    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        pmc = make_pmc(conn)
        self.pmc_id = pmc.pmc_id
        self.unit = add_unit(conn, self.pmc_id, pmc.owners[0].property_id, "Unit 1")
        self.staff = add_staff(conn, self.pmc_id)
        link = open_application_link(conn, self.pmc_id, self.unit, self.staff)
        self.application = submit_application(
            conn, self.pmc_id, link, applicants=[("Applicant 1", None)]
        )
        record_decision(conn, self.pmc_id, self.application, self.staff, "approved")
        self.offers: dict = {}
        self.voided: set = set()
        self.completed: dict = {}
        self.links: dict = {}

    @initialize(people=st.integers(1, 3))
    def send_an_offer_to_sign_here(self, people):
        signers = tuple((f"Signer {k}", f"signer{k}@example.com") for k in range(1, people + 1))
        self.offer = offer_lease(
            self.conn,
            self.pmc_id,
            self.unit,
            self.staff,
            BASE,
            None,
            application_id=self.application,
            signers=signers,
        )
        self.send_here(self.offer)
        self.offers[self.offer] = {
            "signers": [name for name, _ in signers],
            "signed": set(),
            "here": True,
        }
        for position in range(1, people + 1):
            self.links[self.new_link(self.offer, position)] = {
                "offer": self.offer,
                "position": position,
                "events": set(),
            }

    @rule()
    def void(self):
        voiding = (
            "INSERT INTO trust_lease_offer_voids (offer_id, pmc_id, reason) VALUES (%s, %s, %s)"
        )
        if self.completed or self.voided:
            refusal = (
                psycopg.errors.CheckViolation if self.completed else psycopg.errors.UniqueViolation
            )
            with pytest.raises(refusal), self.conn.transaction():
                self.conn.execute(voiding, (self.offer, self.pmc_id, "Terms changed"))
            return
        self.conn.execute(voiding, (self.offer, self.pmc_id, "Terms changed"))
        self.voided.add(self.offer)

    @rule()
    def complete(self):
        def attempt():
            with self.conn.transaction():
                return complete_offer(self.conn, self.pmc_id, self.offer, self.staff)

        model = self.offers[self.offer]
        if self.completed:
            assert attempt() == self.completed[self.offer]
        elif self.voided or len(model["signed"]) < len(model["signers"]):
            with pytest.raises(psycopg.errors.CheckViolation):
                attempt()
        else:
            self.completed[self.offer] = attempt()


def test_signing_on_the_page_holds_under_random_steps_through_the_links(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: SigningMachine(conn))
