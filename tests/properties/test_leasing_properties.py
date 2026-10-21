"""Property-based tests for rental applications (migration 20261011000030), vacancy listings
(migration 20261013000047) and withdrawals (migration 20261016000070).

Hypothesis opens and closes application links on two units, lists links on the PMC's public page
and takes listings off, applies through open and closed links, records screenings, decides
applications, each decision with or without a screening and with or without an adverse action
notice, and records applicants withdrawing. A Python model predicts every step: the database
must accept exactly what the model accepts and refuse the rest, writing nothing.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - each link's applications are the model's: none came through a link after it closed;
  - each application has at most one decision, the model's, resting only on a screening of
    its own;
  - a declined or conditional decision that rests on a screening has its adverse action notice,
    and no other decision has one;
  - nothing once written changes;
  - the PMC's public vacancy page shows each listing not taken off whose link is open, and an
    applicant reaches a link through exactly those listings (migration 20261013000047);
  - each withdrawal is the model's: once per application, never of a declined one, and nothing
    (no screening, no decision) recorded for an application after it (migration
    20261016000070);
  - a decision says why exactly when it goes against the recommendation of its application's
    latest screening (migration 20261018000089).

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import psycopg
import pytest
from fastapi import HTTPException
from helpers import (
    add_staff,
    add_unit,
    make_pmc,
    open_application_link,
    record_adverse_action,
    record_decision,
    record_screening,
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

from pms.api import leasing as leasing_api
from pms.api import listings as listings_api

RECOMMENDATIONS = {"approve": [], "conditional": ["Income"], "decline": ["Income"]}
# The decision that goes with each recommendation; any other goes against it.
AGREES = {"approve": "approved", "conditional": "conditional", "decline": "declined"}


class LeasingMachine(RuleBasedStateMachine):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        pmc = make_pmc(conn)
        self.pmc_id = pmc.pmc_id
        self.units = [
            add_unit(conn, self.pmc_id, pmc.owners[0].property_id, f"Unit {n}") for n in (1, 2)
        ]
        self.staff = add_staff(conn, self.pmc_id)
        # In the order they happened, so a replayed example picks the same ones.
        self.links: dict = {}  # link -> open?
        self.applications: dict = {}  # application -> its link
        self.screenings: dict = {}  # screening -> its application
        # screening -> (when it was reported, its recommendation), as the database recorded it
        self.reported: dict = {}
        self.decisions: dict = {}  # application -> (decision, screening or None, notice?)
        self.overridden: set = set()  # applications decided against their latest screening
        self.listings: dict = {}  # listing -> (its link, taken off?)
        self.withdrawn: set = set()  # applications withdrawn
        self.snapshot: list = []

    # --- links ---------------------------------------------------------------------------

    @rule(n=st.integers(min_value=0, max_value=1))
    def open_a_link(self, n):
        self.links[open_application_link(self.conn, self.pmc_id, self.units[n], self.staff)] = True

    @precondition(lambda self: self.links)
    @rule(data=st.data())
    def close_a_link(self, data):
        link = data.draw(st.sampled_from(list(self.links)))
        closing = (
            "INSERT INTO trust_application_link_closings (pmc_id, link_id, closed_by)"
            " VALUES (%s, %s, %s)"
        )
        if not self.links[link]:
            with pytest.raises(psycopg.errors.UniqueViolation):
                self.conn.execute(closing, (self.pmc_id, link, self.staff))
            return
        self.conn.execute(closing, (self.pmc_id, link, self.staff))
        self.links[link] = False

    # --- listings ------------------------------------------------------------------------

    @precondition(lambda self: self.links)
    @rule(data=st.data())
    def list_a_link(self, data):
        """Any link, open or closed (the page shows only open ones); a link takes one listing."""
        link = data.draw(st.sampled_from(list(self.links)))
        listing = (
            "INSERT INTO trust_listings (pmc_id, link_id, listed_by) VALUES (%s, %s, %s)"
            " RETURNING id"
        )
        if any(listed == link for listed, _ in self.listings.values()):
            with pytest.raises(psycopg.errors.UniqueViolation):
                self.conn.execute(listing, (self.pmc_id, link, self.staff))
            return
        made = self.conn.execute(listing, (self.pmc_id, link, self.staff)).fetchone()[0]
        self.listings[made] = (link, False)

    @precondition(lambda self: self.listings)
    @rule(data=st.data())
    def take_a_listing_off(self, data):
        listing = data.draw(st.sampled_from(list(self.listings)))
        removal = (
            "INSERT INTO trust_listing_removals (listing_id, pmc_id, removed_by)"
            " VALUES (%s, %s, %s)"
        )
        link, removed = self.listings[listing]
        if removed:
            with pytest.raises(psycopg.errors.UniqueViolation):
                self.conn.execute(removal, (listing, self.pmc_id, self.staff))
            return
        self.conn.execute(removal, (listing, self.pmc_id, self.staff))
        self.listings[listing] = (link, True)

    # --- applications --------------------------------------------------------------------

    @precondition(lambda self: self.links)
    @rule(data=st.data(), people=st.integers(min_value=1, max_value=3))
    def apply(self, data, people):
        link = data.draw(st.sampled_from(list(self.links)))
        applicants = [(f"Applicant {n}", None) for n in range(people)]
        if not self.links[link]:
            refused = pytest.raises(psycopg.errors.CheckViolation, match="is closed")
            with refused, self.conn.transaction():
                submit_application(self.conn, self.pmc_id, link, applicants=applicants)
            return
        with self.conn.transaction():
            application = submit_application(self.conn, self.pmc_id, link, applicants=applicants)
        self.applications[application] = link

    @precondition(lambda self: self.applications)
    @rule(data=st.data(), recommendation=st.sampled_from(list(RECOMMENDATIONS)))
    def screen(self, data, recommendation):
        application = data.draw(st.sampled_from(list(self.applications)))
        if application in self.withdrawn:
            refused = pytest.raises(psycopg.errors.CheckViolation, match="withdrew")
            with refused, self.conn.transaction():
                record_screening(
                    self.conn,
                    self.pmc_id,
                    application,
                    self.staff,
                    recommendation,
                    RECOMMENDATIONS[recommendation],
                )
            return
        screening = record_screening(
            self.conn,
            self.pmc_id,
            application,
            self.staff,
            recommendation,
            RECOMMENDATIONS[recommendation],
        )
        self.screenings[screening] = application
        (at,) = self.conn.execute(
            "SELECT reported_at FROM trust_screenings WHERE id = %s", (screening,)
        ).fetchone()
        self.reported[screening] = (at, recommendation)

    @precondition(lambda self: self.applications)
    @rule(
        data=st.data(),
        decision=st.sampled_from(["approved", "conditional", "declined"]),
        on_a_screening=st.booleans(),
        own_screening=st.booleans(),
        with_notice=st.booleans(),
        with_reason=st.booleans(),
    )
    def decide(self, data, decision, on_a_screening, own_screening, with_notice, with_reason):
        application = data.draw(st.sampled_from(list(self.applications)))
        mine = [s for s, a in self.screenings.items() if a == application]
        others = [s for s, a in self.screenings.items() if a != application]
        pool = mine if own_screening else others
        screening = data.draw(st.sampled_from(pool)) if on_a_screening and pool else None
        conditions = "Extra deposit" if decision == "conditional" else None

        def do():
            with self.conn.transaction():
                record_decision(
                    self.conn,
                    self.pmc_id,
                    application,
                    self.staff,
                    decision,
                    screening,
                    conditions,
                    "The owner's own rule" if with_reason else None,
                )
                if with_notice:
                    record_adverse_action(self.conn, self.pmc_id, application, self.staff)

        needs_notice = decision != "approved" and screening is not None
        # Against the latest screening's recommendation (latest by when it was reported, then
        # id, as the database orders them), a decision says why; no other does.
        latest = max(((self.reported[s][0], s) for s in mine), default=None)
        against = latest is not None and decision != AGREES[self.reported[latest[1]][1]]
        # In the order the database checks: the screening, a withdrawal and the reason (BEFORE
        # triggers), then one decision per application (the key), then the notice (at commit).
        if (
            application in self.withdrawn
            or (screening is not None and screening not in mine)
            or against != with_reason
        ):
            refusal = psycopg.errors.CheckViolation
        elif application in self.decisions:
            refusal = psycopg.errors.UniqueViolation
        elif needs_notice != with_notice:
            refusal = psycopg.errors.CheckViolation
        else:
            do()
            self.decisions[application] = (decision, screening, with_notice)
            if with_reason:
                self.overridden.add(application)
            return
        with pytest.raises(refusal):
            do()

    @precondition(lambda self: self.decisions)
    @rule(data=st.data())
    def give_a_notice_later(self, data):
        """A notice in a transaction of its own, after the decision: refused either way, since
        a decision that needs one got it with the decision."""
        application = data.draw(st.sampled_from(list(self.decisions)))
        _, _, noticed = self.decisions[application]
        refusal = psycopg.errors.UniqueViolation if noticed else psycopg.errors.CheckViolation
        with pytest.raises(refusal), self.conn.transaction():
            record_adverse_action(self.conn, self.pmc_id, application, self.staff)

    @precondition(lambda self: self.applications)
    @rule(data=st.data(), why=st.sampled_from([None, "Found another home"]))
    def withdraw(self, data, why):
        """Once per application, and never of a declined one (no offer here becomes a lease)."""
        application = data.draw(st.sampled_from(list(self.applications)))
        withdrawal = (
            "INSERT INTO trust_application_withdrawals (application_id, pmc_id, reason)"
            " VALUES (%s, %s, %s)"
        )
        declined = self.decisions.get(application, ("",))[0] == "declined"
        if declined or application in self.withdrawn:
            # The BEFORE trigger refuses a declined one before the key refuses a second.
            refusal = psycopg.errors.CheckViolation if declined else psycopg.errors.UniqueViolation
            with pytest.raises(refusal), self.conn.transaction():
                self.conn.execute(withdrawal, (application, self.pmc_id, why))
            return
        self.conn.execute(withdrawal, (application, self.pmc_id, why))
        self.withdrawn.add(application)

    # --- invariants ----------------------------------------------------------------------

    @invariant()
    def withdrawals_are_the_models(self):
        rows = self.conn.execute(
            "SELECT w.application_id, d.decision,"
            " (SELECT count(*) FROM trust_screenings AS s WHERE s.application_id = w.application_id"
            "  AND s.reported_at > w.recorded_at)"
            " FROM trust_application_withdrawals AS w"
            " LEFT JOIN trust_application_decisions AS d USING (application_id)"
            " WHERE w.pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert {application for application, _, _ in rows} == self.withdrawn
        for _, decision, screened_after in rows:
            assert decision != "declined"
            assert screened_after == 0

    @invariant()
    def applications_are_the_models(self):
        rows = self.conn.execute(
            "SELECT id, link_id FROM trust_applications WHERE pmc_id = %s", (self.pmc_id,)
        ).fetchall()
        assert dict(rows) == self.applications

    @invariant()
    def decisions_and_notices_are_the_models(self):
        rows = self.conn.execute(
            "SELECT d.application_id, d.decision, d.screening_id, a.application_id IS NOT NULL,"
            " s.application_id"
            " FROM trust_application_decisions AS d"
            " LEFT JOIN trust_adverse_actions AS a USING (application_id)"
            " LEFT JOIN trust_screenings AS s ON s.id = d.screening_id"
            " WHERE d.pmc_id = %s",
            (self.pmc_id,),
        ).fetchall()
        assert {row[0]: row[1:4] for row in rows} == self.decisions
        (overridden,) = self.conn.execute(
            "SELECT array_agg(application_id) FROM trust_application_decisions"
            " WHERE pmc_id = %s AND override_reason IS NOT NULL",
            (self.pmc_id,),
        ).fetchone()
        assert set(overridden or []) == self.overridden
        for application, decision, screening, notice, screened in rows:
            assert screening is None or screened == application
            assert notice == (decision != "approved" and screening is not None)
        (stray,) = self.conn.execute(
            "SELECT count(*) FROM trust_adverse_actions AS a WHERE a.pmc_id = %s AND NOT EXISTS"
            " (SELECT 1 FROM trust_application_decisions AS d"
            "  WHERE d.application_id = a.application_id)",
            (self.pmc_id,),
        ).fetchone()
        assert stray == 0

    @invariant()
    def the_public_page_is_the_models(self):
        """The page shows each listing not taken off whose link is open, and an applicant
        reaches a link through exactly those listings."""
        shown = {x.id for x in listings_api.vacancies(self.conn, self.pmc_id).listings}
        live = {s for s, (link, off) in self.listings.items() if not off and self.links[link]}
        assert shown == live
        for listing, (link, _) in self.listings.items():
            try:
                reached = leasing_api.open_link(self.conn, leasing_api.LinkToken(listing=listing))
            except HTTPException as refused:
                assert refused.status_code == 404
                reached = None
            assert (reached is not None and reached[0] == link) == (listing in live)

    @invariant()
    def nothing_written_changes(self):
        rows = self.conn.execute(
            "SELECT table_name, row_data FROM trust_audit_log WHERE pmc_id = %s"
            " AND table_name IN ('trust_application_links', 'trust_application_link_closings',"
            " 'trust_applications', 'trust_applicants', 'trust_screenings',"
            " 'trust_application_decisions', 'trust_adverse_actions', 'trust_listings',"
            " 'trust_listing_removals', 'trust_application_withdrawals') ORDER BY id",
            (self.pmc_id,),
        ).fetchall()
        live = {
            table: self.conn.execute(
                f"SELECT count(*) FROM {table} WHERE pmc_id = %s", (self.pmc_id,)
            ).fetchone()[0]
            for table in {table for table, _ in rows}
        }
        # Every logged insert is still there, and the log holds nothing but inserts' rows.
        assert rows[: len(self.snapshot)] == self.snapshot
        for table, count in live.items():
            assert count == sum(1 for logged, _ in rows if logged == table)
        self.snapshot = rows


def test_leasing_invariants_hold_under_random_links_applications_and_decisions(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        run_state_machine_as_test(lambda: LeasingMachine(conn))
