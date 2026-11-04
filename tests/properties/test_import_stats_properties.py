"""Property-based test for analyzing after a switch-in import (migration 20261103000200,
src/pms/api/switching.py).

Hypothesis brings a random number of tenants and units into a new company in one transaction as
the app, as the switching tool's import does, and calls trust_analyze_after_import() before the
transaction commits. However many came in, the planner then knows how many rows each table
holds, the import's own included: its row count (pg_class.reltuples) is within a tenth of what
the table holds, never the unknown (-1) of a table it never sampled. Unanalyzed, it took a
200-unit company's 2,700 charges for 8 and planned reports row by row (PR #184).
Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import psycopg
from helpers import make_pmc
from hypothesis import event, given
from hypothesis import strategies as st

TABLES = ("trust_tenants", "trust_units")


def counts(conn):
    held = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}  # noqa: S608
    known = dict(
        conn.execute(
            "SELECT relname, reltuples FROM pg_class WHERE relname = ANY(%s)", (list(TABLES),)
        ).fetchall()
    )
    return held, known


@given(tenants=st.integers(0, 400), units=st.integers(0, 120))
def check(conn, database_url, tenants, units):
    pmc = make_pmc(conn)
    prop = pmc.owners[0].property_id
    with psycopg.connect(database_url) as app:  # one transaction, as an import's request is
        app.execute("SET ROLE trust_app")
        app.execute(
            "INSERT INTO trust_tenants (pmc_id, property_id, display_name)"
            " SELECT %s, %s, 'Imported tenant ' || n FROM generate_series(1, %s) AS n",
            (pmc.pmc_id, prop, tenants),
        )
        app.execute(
            "INSERT INTO trust_units (pmc_id, property_id, display_name)"
            " SELECT %s, %s, 'Imported unit ' || n FROM generate_series(1, %s) AS n",
            (pmc.pmc_id, prop, units),
        )
        app.execute("SELECT trust_analyze_after_import()")
    event("nothing came in" if tenants + units == 0 else "rows came in")
    held, known = counts(conn)
    for table in TABLES:
        assert known[table] >= 0, table  # sampled, not unknown
        assert abs(known[table] - held[table]) <= max(1, held[table] / 10), (table, held, known)


def test_after_an_import_the_planner_knows_how_many_rows_each_table_holds(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        check(conn, database_url)
