"""Property-based tests for announcements and staff to-dos (migration 20261023000203;
src/pms/api/announcements.py, src/pms/api/todos.py).

Hypothesis gives a company's tenants leases at two properties (living there today, moved out, or
not moved in yet), each an email from a small pool or none (so some share an address, in any
case), and announces to every tenant of the company or of one property. A model says who it goes
to: each address of a tenant living in the place today once, and the names of those with none;
nobody else, and one outbox email per address.

Then it reassigns, moves and finishes to-dos in any order: the latest assignment always holds, a
to-do done keeps its last assignment and takes no other, and the Dashboard shows exactly the
open ones of the staff member due within a week.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import psycopg
from fastapi import HTTPException
from helpers import add_staff, add_tenant, add_unit, make_pmc, open_lease
from hypothesis import event, given
from hypothesis import strategies as st

from pms.api import announcements, todos

TODAY = datetime.now(UTC).date()
POOL = [None, "a@example.com", "A@EXAMPLE.COM", "b@example.com", "c@example.com"]

tenants = st.lists(
    st.fixed_dictionaries(
        {
            "property": st.integers(0, 1),
            "living": st.sampled_from(["today", "moved out", "to come"]),
            "email": st.sampled_from(POOL),
        }
    ),
    min_size=1,
    max_size=6,
)


@given(plan=tenants, where=st.sampled_from([None, 0, 1]))
def check_announcement(conn, plan, where):
    pmc = make_pmc(conn)
    staff = add_staff(conn, pmc.pmc_id)
    session = SimpleNamespace(pmc_id=pmc.pmc_id, person_id=staff)
    living: list[tuple[str, str | None]] = []
    for n, person in enumerate(plan):
        prop = pmc.owners[person["property"]].property_id
        name = f"Tenant {n}"
        tenant = add_tenant(conn, pmc.pmc_id, prop, name)
        if person["email"]:
            conn.execute(
                "INSERT INTO trust_contacts (pmc_id, tenant_id, email) VALUES (%s, %s, %s)",
                (pmc.pmc_id, tenant, person["email"]),
            )
        starts, ends = {
            "today": (TODAY - timedelta(days=30), None),
            "moved out": (TODAY - timedelta(days=400), TODAY - timedelta(days=2)),
            "to come": (TODAY + timedelta(days=10), None),
        }[person["living"]]
        unit = add_unit(conn, pmc.pmc_id, prop, f"Unit {n}")
        open_lease(conn, pmc.pmc_id, unit, starts, ends, "900.00", [tenant])
        if person["living"] == "today" and (where is None or where == person["property"]):
            living.append((name, person["email"]))

    expected_to: list[str] = []
    for _, address in sorted(living):
        if address and address.lower() not in {a.lower() for a in expected_to}:
            expected_to.append(address)
    expected_without = sorted(name for name, address in living if not address)
    new = announcements.NewAnnouncement(
        audience="tenants",
        property_id=None if where is None else pmc.owners[where].property_id,
        subject="Water off",
        body="Tuesday from 9 to 11.",
    )
    try:
        sent = announcements.announce(session, conn, new)
    except HTTPException as refused:
        assert not living and refused.status_code == 422
        event("no one there")
        return
    assert [r.to for r in sent.emailed] == expected_to
    assert sorted(sent.without_email) == expected_without
    written = conn.execute(
        "SELECT to_address FROM trust_emails WHERE announcement_id = %s ORDER BY to_address",
        (sent.id,),
    ).fetchall()
    assert sorted(row[0] for row in written) == sorted(expected_to)
    event(f"emails: {len(expected_to)}, none on file: {len(expected_without)}")


def test_an_announcement_reaches_each_address_living_there_once(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_announcement(conn)


steps = st.lists(
    st.one_of(
        st.tuples(st.just("assign"), st.integers(0, 2), st.integers(0, 2), st.integers(-5, 12)),
        st.tuples(st.just("done"), st.integers(0, 2), st.integers(0, 2), st.just(0)),
    ),
    max_size=12,
)


@given(plan=steps)
def check_todos(conn, plan):
    pmc = make_pmc(conn)
    staff = [add_staff(conn, pmc.pmc_id, f"Staff {n}") for n in range(3)]
    session = SimpleNamespace(pmc_id=pmc.pmc_id, person_id=staff[0])
    made = []
    for n in range(3):
        with conn.transaction():  # as a request is: the to-do and its assignment together
            new = todos.NewTodo(title=f"Job {n}", assigned_to=staff[0], due_on=TODAY + timedelta(n))
            made.append(todos.add(session, conn, new).id)
    model = {
        made[n]: {"to": staff[0], "due": TODAY + timedelta(n), "done": False} for n in range(3)
    }
    for action, which, who, offset in plan:
        todo = made[which]
        if action == "assign":
            change = todos.Assignment(assigned_to=staff[who], due_on=TODAY + timedelta(offset))
            try:
                todos.assign(session, conn, todo, change)
            except HTTPException as refused:
                assert model[todo]["done"] and refused.status_code == 409
                continue
            assert not model[todo]["done"]
            model[todo] |= {"to": staff[who], "due": change.due_on}
        else:
            todos.done(SimpleNamespace(pmc_id=pmc.pmc_id, person_id=staff[who]), conn, todo)
            model[todo]["done"] = True
    for todo, want in model.items():
        got = todos.one(conn, pmc.pmc_id, todo)
        assert (got.assigned_to_id, got.due_on, got.done_at is not None) == (
            want["to"],
            want["due"],
            want["done"],
        )
    for n, person in enumerate(staff):
        soon = todos.mine_soon(conn, pmc.pmc_id, person, TODAY)
        assert {t.id for t in soon} == {
            todo
            for todo, want in model.items()
            if want["to"] == person and not want["done"] and want["due"] <= TODAY + timedelta(7)
        }
        assert [t.due_on for t in soon] == sorted(t.due_on for t in soon)
        event(f"staff {n} soon: {len(soon)}")


def test_the_latest_assignment_holds_and_a_done_todo_takes_no_other(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check_todos(conn)
