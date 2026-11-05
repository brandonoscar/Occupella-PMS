"""Property-based tests for events (migration 20261105000500, src/pms/api/events.py).

Hypothesis runs a few transactions side by side on connections of their own, each writing events
(a work order opened) and then finishing (committed or rolled back), in any interleaving, while a
reader reads after the last event it got, as `GET /api/events?after=` does.

Invariants (ci/registry.toml maps them to this test):
  - the reader gets every event of every committed transaction exactly once, never one of a
    transaction rolled back, and only once its transaction finished;
  - it gets them in the order their transactions began writing, each transaction's together and
    in the order it wrote them: one is read only once every transaction that began writing
    before it has finished, so a reader that asks after the last it got never skips one;
  - another company's events never.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import time
from datetime import UTC, datetime

import psycopg
from helpers import add_work_order, make_pmc
from hypothesis import event as note
from hypothesis import given
from hypothesis import strategies as st

from pms.api import events

WRITERS = 3
STEPS = st.lists(
    st.tuples(
        st.integers(0, WRITERS - 1),
        st.sampled_from(["write", "write", "commit", "rollback", "read"]),
    ),
    min_size=1,
    max_size=25,
)


@given(steps=STEPS)
def check(conn, writers, reader, steps):
    pmc, other = make_pmc(conn), make_pmc(conn)
    prop = pmc.owners[0].property_id
    start = reader.execute(
        "SELECT max(id) FROM trust_events WHERE pmc_id = %s", (pmc.pmc_id,)
    ).fetchone()[0]
    open_orders: dict[int, list[str]] = {n: [] for n in range(WRITERS)}
    began: dict[int, int] = {}  # each open transaction's place among first writes
    batches: list[tuple[int, list[str]]] = []  # each committed transaction: its place, its events
    committed: list[str] = []
    rolled_back: set[str] = set()
    got: list[str] = []
    after = start

    def read():
        nonlocal after
        # The endpoint's own read (GET /api/events), a page at a time.
        while found := events.events_after(reader, pmc.pmc_id, after, 2):
            for each in found:
                got.append(each.subject["work_order_id"])
                after = each.id
        # Only what committed, each once.
        assert set(got) <= set(committed)
        assert len(got) == len(set(got))

    for i, (n, step) in enumerate(steps):
        writer = writers[n]
        if step == "write":
            order = add_work_order(writer, pmc.pmc_id, prop, None, f"W{n}", datetime.now(UTC))
            began.setdefault(n, i)  # its first write gives it its place (its xid)
            open_orders[n].append(str(order))
            # Another company's write beside it, never read.
            add_work_order(
                conn, other.pmc_id, other.owners[0].property_id, None, "Theirs", datetime.now(UTC)
            )
        elif step in ("commit", "rollback"):
            note(step)
            if step == "commit":
                writer.commit()
                committed.extend(open_orders[n])
                if open_orders[n]:
                    batches.append((began[n], open_orders[n]))
            else:
                writer.rollback()
                rolled_back.update(open_orders[n])
            open_orders[n] = []
            began.pop(n, None)
        else:
            read()
    for n, writer in enumerate(writers):
        writer.commit()
        committed.extend(open_orders[n])
        if open_orders[n]:
            batches.append((began[n], open_orders[n]))
    # Every writer here has finished; an event still waits only for a writing transaction
    # elsewhere on the server, begun before it (another test run beside this one, say), so the
    # reader asks again until that one finishes too, as a client polling would.
    deadline = time.monotonic() + 60
    read()
    while len(got) < len(committed) and time.monotonic() < deadline:
        time.sleep(0.05)
        read()

    assert sorted(got) == sorted(committed)
    assert not set(got) & rolled_back
    # Transaction by transaction, in the order each began writing, each in the order it wrote.
    assert got == [order for _, orders in sorted(batches, key=lambda b: b[0]) for order in orders]


def test_a_reader_gets_every_committed_event_once_never_skipping_a_slower_transaction(
    database_url,
):
    with psycopg.connect(database_url, autocommit=True) as conn:
        writers = [psycopg.connect(database_url) for _ in range(WRITERS)]
        try:
            with psycopg.connect(database_url, autocommit=True) as reader:
                reader.execute("SET ROLE trust_ai_agent")
                check(conn, writers, reader)
        finally:
            for writer in writers:
                writer.close()
