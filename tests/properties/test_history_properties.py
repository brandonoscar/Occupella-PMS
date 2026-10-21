"""Property-based tests for the old system's history (migration 20261021000107; the founder's
review q129).

Hypothesis writes random history files of tenants' and vendors' lines (a few people, past days,
any amounts to the cent) and brings each in twice through the switching import. A model of what
each person's lines are says what the database must hold: every line of each file once, under its
person, the same file again adding nothing, each person's lines newest first as the history page
reads them. Nothing may move in the ledger or be charged.
Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

from datetime import date, timedelta
from decimal import Decimal

import psycopg
from helpers import make_pmc
from hypothesis import given
from hypothesis import strategies as st

from pms.api import switching

PEOPLE = ["Tenant A", "Tenant B", "Vendor 1", "Vendor 2"]
LINE = st.tuples(
    st.sampled_from(PEOPLE),
    st.integers(0, 900),  # days after FIRST: all of them past
    st.integers(-500_000, 500_000).map(lambda cents: Decimal(cents) / 100),
    st.sampled_from(["Charge", "Payment", "Credit"]),
)
FILES = st.lists(st.lists(LINE, min_size=1, max_size=6), min_size=1, max_size=4)
FIRST = date(2023, 1, 1)


def content(kind: str, lines: list) -> str:
    if kind == "tenant":
        head = "Property,Tenant,Date,Type,Description,Amount\n"
        body = "".join(
            f"Property 1,{who},{FIRST + timedelta(days=day)},{word},Line {n},{amount}\n"
            for n, (who, day, amount, word) in enumerate(lines)
        )
    else:
        head = "Vendor,Date,Type,Description,Amount\n"
        body = "".join(
            f"{who},{FIRST + timedelta(days=day)},{word},Line {n},{amount}\n"
            for n, (who, day, amount, word) in enumerate(lines)
        )
    return head + body


@given(files=FILES)
def check(conn, files):
    pmc = make_pmc(conn)
    moved = conn.execute("SELECT count(*) FROM pgledger_transfers").fetchone()[0]
    expected: dict[str, list[tuple]] = {}
    for lines in files:
        for kind in ("tenant", "vendor"):
            mine = [line for line in lines if line[0].startswith(kind.capitalize())]
            if not mine:
                continue
            text = content(kind, mine)
            names = switching.Names(conn, pmc.pmc_id)
            parsed = switching.rows(text, switching.NEEDS[f"{kind} history"])
            first = switching.import_history(names, kind, text, parsed)
            again = switching.import_history(switching.Names(conn, pmc.pmc_id), kind, text, parsed)
            if first.added:
                for i, (who, day, amount, word) in enumerate(mine):
                    expected.setdefault(who, []).append(
                        (FIRST + timedelta(days=day), word, f"Line {i}", amount)
                    )
            assert again.added == 0 and again.kept == len(mine)
            # None when a file of the very same text came in already.
            assert first.added in (0, len(mine))

    for who in PEOPLE:
        column = "tenant_id" if who.startswith("Tenant") else "vendor_id"
        table = "trust_tenants" if column == "tenant_id" else "trust_vendors"
        got = conn.execute(
            "SELECT h.posted_on, h.kind, h.description, h.amount FROM trust_history_lines AS h"
            f" JOIN {table} AS p ON p.id = h.{column}"  # noqa: S608
            " WHERE h.pmc_id = %s AND p.display_name = %s"
            " ORDER BY h.posted_on DESC, h.file_id, h.line_no DESC",
            (pmc.pmc_id, who),
        ).fetchall()
        assert sorted(got) == sorted(expected.get(who, [])), who
        assert [row[0] for row in got] == sorted((row[0] for row in got), reverse=True)
    assert conn.execute("SELECT count(*) FROM pgledger_transfers").fetchone()[0] == moved
    assert conn.execute(
        "SELECT count(*) FROM trust_charges AS c JOIN trust_leases AS l ON l.id = c.lease_id"
        " WHERE l.pmc_id = %s",
        (pmc.pmc_id,),
    ).fetchone() == (0,)


def test_history_holds_each_files_lines_once_under_their_person_and_moves_nothing(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn)
