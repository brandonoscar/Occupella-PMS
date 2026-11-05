"""Drafts in the database (ADR 0011, migration 20261105000300): the AI's role proposes a draft, its
one write; a draft's key must stand and its company be active; a staff member decides it once,
and an approved draft has what the PMS did for it, checked at commit; all of it append-only.
Every name here is synthetic."""

import hashlib
import os

import psycopg
import pytest
from helpers import acting_as, add_staff, admin_of, make_pmc
from psycopg.types.json import Jsonb

NOTE = {"kind": "note", "about": "owner", "about_id": None, "body": "Hi"}


@pytest.fixture
def pmc(conn):
    return make_pmc(conn)


def key_of(conn, pmc_id, name="Assistant"):
    secret = "occ_" + os.urandom(16).hex()
    with acting_as(conn, admin_of(conn, pmc_id)):
        return conn.execute(
            "INSERT INTO trust_api_keys (pmc_id, name, prefix, secret_sha256)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (pmc_id, name, secret[:12], hashlib.sha256(secret.encode()).digest()),
        ).fetchone()[0]


def as_ai(conn):
    """The connection as an API key's request runs: the app's role become the AI's."""
    conn.execute("SET ROLE trust_app")
    conn.execute("SET ROLE trust_ai_agent")


def propose(conn, pmc, key, proposal=None):
    proposal = proposal or {**NOTE, "about_id": str(pmc.owners[0].owner_id)}
    return conn.execute(
        "INSERT INTO trust_drafts (pmc_id, api_key_id, kind, proposal)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (pmc.pmc_id, key, proposal["kind"], Jsonb(proposal)),
    ).fetchone()[0]


def decide(conn, pmc, draft, staff, decision="approved", acted=True):
    with acting_as(conn, staff), conn.transaction():
        if decision == "approved":
            conn.execute(
                "INSERT INTO trust_draft_decisions (draft_id, pmc_id, decision, edited)"
                " VALUES (%s, %s, 'approved', false)",
                (draft, pmc.pmc_id),
            )
        else:
            conn.execute(
                "INSERT INTO trust_draft_decisions (draft_id, pmc_id, decision, reason)"
                " VALUES (%s, %s, 'declined', 'Not needed')",
                (draft, pmc.pmc_id),
            )
        if acted:
            conn.execute(
                "INSERT INTO trust_draft_actions (draft_id, pmc_id, acted, result)"
                " VALUES (%s, %s, '{}', '{}')",
                (draft, pmc.pmc_id),
            )


def test_the_ai_role_proposes_a_draft_and_nothing_else(database_url, conn, pmc):
    key = key_of(conn, pmc.pmc_id)
    with psycopg.connect(database_url, autocommit=True) as ai:
        as_ai(ai)
        draft = propose(ai, pmc, key)
        assert ai.execute("SELECT kind FROM trust_drafts WHERE id = %s", (draft,)).fetchone() == (
            "note",
        )
        staff = add_staff(conn, pmc.pmc_id, "Staff 1")
        for statement in (
            "INSERT INTO trust_draft_decisions (draft_id, pmc_id, decision, reason, recorded_by)"
            f" VALUES ('{draft}', '{pmc.pmc_id}', 'declined', 'No', '{staff}')",
            f"INSERT INTO trust_draft_actions (draft_id, pmc_id, acted, result) VALUES ('{draft}',"
            f" '{pmc.pmc_id}', '{{}}', '{{}}')",
            "SELECT count(*) FROM trust_draft_actions",
            f"UPDATE trust_drafts SET why = 'changed' WHERE id = '{draft}'",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                ai.execute(statement)


def test_the_app_never_proposes_a_draft(app_conn, conn, pmc):
    key = key_of(conn, pmc.pmc_id)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        propose(app_conn, pmc, key)


def test_a_draft_needs_a_standing_key_of_an_active_company(database_url, conn, pmc):
    key = key_of(conn, pmc.pmc_id)
    other = key_of(conn, pmc.pmc_id, "Other")
    elsewhere = make_pmc(conn)
    with psycopg.connect(database_url, autocommit=True) as ai:
        as_ai(ai)
        # A key proposes to its own company only.
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            propose(ai, elsewhere, other)

        with acting_as(conn, admin_of(conn, pmc.pmc_id)):
            conn.execute(
                "INSERT INTO trust_api_key_revocations (key_id, pmc_id) VALUES (%s, %s)",
                (key, pmc.pmc_id),
            )
        with pytest.raises(psycopg.errors.CheckViolation, match="was taken back"):
            propose(ai, pmc, key)

        conn.execute(
            "INSERT INTO trust_company_statuses (pmc_id, status, reason)"
            " VALUES (%s, 'suspended', 'synthetic')",
            (pmc.pmc_id,),
        )
        with pytest.raises(psycopg.errors.CheckViolation, match="is not active"):
            propose(ai, pmc, other)


def test_a_drafts_kind_is_one_that_moves_no_money_and_its_proposal_says_it(conn, pmc):
    key = key_of(conn, pmc.pmc_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        propose(conn, pmc, key, {"kind": "payment", "amount": "100.00"})
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO trust_drafts (pmc_id, api_key_id, kind, proposal)"
            " VALUES (%s, %s, 'email', %s)",
            (pmc.pmc_id, key, Jsonb({"kind": "note"})),
        )


def test_a_draft_is_decided_once_by_a_named_staff_member_and_approved_means_done(conn, pmc):
    key = key_of(conn, pmc.pmc_id)
    staff = add_staff(conn, pmc.pmc_id, "Staff 1")

    approved = propose(conn, pmc, key)
    decide(conn, pmc, approved, staff)
    with pytest.raises(psycopg.errors.UniqueViolation):
        decide(conn, pmc, approved, staff, "declined", acted=False)

    # Approved with nothing done, or declined with something done, is refused at commit.
    for decision, acted in (("approved", False), ("declined", True)):
        draft = propose(conn, pmc, key)
        with pytest.raises(psycopg.errors.CheckViolation, match="approved but"):
            decide(conn, pmc, draft, staff, decision, acted)
    # Done with no decision at all.
    draft = propose(conn, pmc, key)
    with pytest.raises(psycopg.errors.CheckViolation, match="was not approved"), conn.transaction():
        conn.execute(
            "INSERT INTO trust_draft_actions (draft_id, pmc_id, acted, result)"
            " VALUES (%s, %s, '{}', '{}')",
            (draft, pmc.pmc_id),
        )
    # Nobody named decides nothing (the command line can't decide a draft).
    with pytest.raises(psycopg.errors.NotNullViolation):
        decide(conn, pmc, draft, None, "declined", acted=False)
    decide(conn, pmc, draft, staff, "declined", acted=False)


@pytest.mark.parametrize("table", ["trust_drafts", "trust_draft_decisions", "trust_draft_actions"])
@pytest.mark.parametrize("statement", ["UPDATE {} SET pmc_id = pmc_id", "DELETE FROM {}"])
def test_drafts_and_their_decisions_never_change(conn, pmc, table, statement):
    key = key_of(conn, pmc.pmc_id)
    decide(conn, pmc, propose(conn, pmc, key), add_staff(conn, pmc.pmc_id, "Staff 1"))
    with pytest.raises(psycopg.errors.RestrictViolation):
        conn.execute(statement.format(table))
