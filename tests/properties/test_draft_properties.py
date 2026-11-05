"""Property-based tests for drafts (ADR 0011, migration 20261105000300, src/pms/api/drafts.py).

Hypothesis proposes a few drafts with an API key (notes, which take the documents area, and work
orders opened, which take maintenance), then has two staff members try to approve them (as
proposed or edited), and decline them, in any order: one holds every area, the other only
maintenance.

Invariants (ci/registry.toml maps them to this test):
  - a draft is decided once: the first decision that goes through holds, and every later one is
    refused, changing nothing;
  - a draft is approved exactly when the PMS did it: an approved one has what was done and made
    exactly one record, as approved (edited or not); a waiting or declined one made none;
  - approving takes the area of what the draft does: refused, nothing is decided or made;
  - no draft moves money: the ledger, the charges and the bills never change.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import uuid

import psycopg
from fastapi import HTTPException
from helpers import acting_as, add_staff, admin_of, make_pmc
from hypothesis import event, given
from hypothesis import strategies as st

from pms import accounts
from pms.api import ai_keys, drafts

KINDS = st.lists(st.sampled_from(["note", "work_order_open"]), min_size=1, max_size=4)
TRIES = st.lists(
    st.tuples(
        st.integers(0, 3),  # which draft
        st.sampled_from(["everyone", "repairs"]),  # who tries
        st.sampled_from(["approve", "edit", "decline"]),
    ),
    min_size=1,
    max_size=10,
)


def money(conn, pmc_id):
    return conn.execute(
        "SELECT (SELECT count(*) FROM pgledger_transfers), (SELECT count(*) FROM trust_charges"
        " WHERE pmc_id = %(p)s), (SELECT count(*) FROM trust_bills WHERE pmc_id = %(p)s)",
        {"p": pmc_id},
    ).fetchone()


def made(conn, pmc_id, kind, record):
    """How many of a kind's records carry the draft's words."""
    if kind == "note":
        sql = "SELECT count(*) FROM trust_notes WHERE pmc_id = %s AND body = %s"
    else:
        sql = "SELECT count(*) FROM trust_work_orders WHERE pmc_id = %s AND summary = %s"
    return conn.execute(sql, (pmc_id, record)).fetchone()[0]


def staff_session(conn, pmc_id, name, areas=None):
    """A staff member, with a custom role allowing only `areas` when given."""
    staff = add_staff(conn, pmc_id, name)
    if areas is not None:
        with acting_as(conn, admin_of(conn, pmc_id)):
            role = conn.execute(
                "INSERT INTO trust_custom_roles (pmc_id, name) VALUES (%s, %s) RETURNING id",
                (pmc_id, f"Only {name}"),
            ).fetchone()[0]
            for area in areas:
                conn.execute(
                    "INSERT INTO trust_custom_role_areas (pmc_id, role_id, area, granted)"
                    " VALUES (%s, %s, %s, true)",
                    (pmc_id, role, area),
                )
            conn.execute(
                "INSERT INTO trust_staff_custom_roles (pmc_id, staff_id, role_id, granted)"
                " VALUES (%s, %s, %s, true)",
                (pmc_id, staff, role),
            )
    return accounts.Session(uuid.uuid4(), pmc_id, "staff", staff, f"{name}@example.com")


def key_for(conn, pmc_id):
    with acting_as(conn, admin_of(conn, pmc_id)):
        made_key = conn.execute(
            "INSERT INTO trust_api_keys (pmc_id, name, prefix, secret_sha256)"
            " VALUES (%s, 'Assistant', %s, %s) RETURNING id",
            (pmc_id, "occ_" + uuid.uuid4().hex[:8], uuid.uuid4().bytes * 2),
        ).fetchone()[0]
    return ai_keys.Key(made_key, pmc_id, "Assistant", "Company")


def proposal(pmc, kind, words):
    if kind == "note":
        return drafts.NoteDraft(
            kind="note", about="owner", about_id=pmc.owners[0].owner_id, body=words
        )
    return drafts.OpenDraft(
        kind="work_order_open", property_id=pmc.owners[0].property_id, summary=words
    )


@given(kinds=KINDS, tries=TRIES)
def check(conn, app_conn, ai_conn, kinds, tries):
    pmc = make_pmc(conn)
    key = key_for(conn, pmc.pmc_id)
    people = {
        "everyone": staff_session(conn, pmc.pmc_id, "Staff 1"),
        "repairs": staff_session(conn, pmc.pmc_id, "Repairs 1", ["maintenance"]),
    }
    proposed = []
    for n, kind in enumerate(kinds):
        words = f"Draft {n} {uuid.uuid4().hex[:6]}"
        with ai_conn.transaction():
            ai_conn.execute("SET LOCAL ROLE trust_ai_agent")
            draft = drafts.propose(
                key, ai_conn, drafts.NewDraft(proposal=proposal(pmc, kind, words))
            ).id
        proposed.append((draft, kind, words))
    before = money(conn, pmc.pmc_id)
    # What each draft became: None while it waits, else (decision, the words done).
    expected: dict[uuid.UUID, tuple[str, str | None] | None] = {d: None for d, _, _ in proposed}

    for which, who, how in tries:
        draft, kind, words = proposed[which % len(proposed)]
        session = people[who]
        allowed = who == "everyone" or kind == "work_order_open"
        edited = f"{words} edited"
        try:
            with app_conn.transaction():
                app_conn.execute(
                    "SELECT set_config('trust.staff_id', %s, true)", (str(session.person_id),)
                )
                if how == "decline":
                    drafts.decline(session, app_conn, draft, drafts.Decline(reason="Not now"))
                else:
                    changed = proposal(pmc, kind, edited) if how == "edit" else None
                    drafts.approve(session, app_conn, draft, drafts.Approval(proposal=changed))
        except HTTPException as refused:
            event(f"refused {refused.status_code}")
            assert expected[draft] is not None or (how != "decline" and not allowed)
            assert refused.status_code == (409 if expected[draft] is not None else 403)
        else:
            event(f"{how} went through")
            assert expected[draft] is None and (how == "decline" or allowed)
            if how == "decline":
                expected[draft] = ("declined", None)
            else:
                expected[draft] = ("approved", edited if how == "edit" else words)

        for d, k, w in proposed:
            decided = app_conn.execute(
                "SELECT x.decision, x.edited, a.acted, a.result FROM trust_draft_decisions AS x"
                " LEFT JOIN trust_draft_actions AS a ON a.draft_id = x.draft_id"
                " WHERE x.draft_id = %s",
                (d,),
            ).fetchone()
            if expected[d] is None:
                assert decided is None
                assert made(conn, pmc.pmc_id, k, w) == 0
                continue
            decision, done = expected[d]
            assert decided is not None and decided[0] == decision
            if decision == "declined":
                assert decided[1:] == (None, None, None)
                assert made(conn, pmc.pmc_id, k, w) == 0
            else:
                assert decided[1] is (done != w)
                field = "body" if k == "note" else "summary"
                assert decided[2][field] == done
                assert made(conn, pmc.pmc_id, k, done) == 1
                assert decided[3]  # what it made, by id
    assert money(conn, pmc.pmc_id) == before


def test_a_draft_is_decided_once_done_exactly_when_approved_by_staff_who_may(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
        psycopg.connect(database_url, autocommit=True) as ai_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        ai_conn.execute("SET ROLE trust_app")
        check(conn, app_conn, ai_conn)
