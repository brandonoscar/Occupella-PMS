"""A report on screen (the founder's punch list, ADR 0006, step 11): any report's rows as text to
preview before Export, and a report narrowed to a property group, one property or an owner's
properties (the founder's review q109).

Narrowing keeps the rows of the report's `property` column that are in the scope (by the names
the report printed) and its heading lines (the company, the day or days); every other line
without a property (a subtotal, a total across all properties, cash a trust bank account holds
for everyone) is left out rather than shown beside rows it no longer adds up, and a last line
says what the report was narrowed to. A report with no property column is not narrowed. The
report itself runs whole, under the caller's role, as every download does; nothing here moves
money.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from pydantic import BaseModel

from pms import exports
from pms.api import scope

# The most rows a preview sends; Export has them all.
PREVIEW_ROWS = 500
# A report's heading lines, by their item: kept whatever it is narrowed to.
HEADINGS = frozenset({"PMC", "as of", "period"})


class Preview(BaseModel):
    title: str
    columns: list[str]
    rows: list[list[str]]
    # Every row the report has; the preview sends at most PREVIEW_ROWS of them.
    total_rows: int


def preview(table: exports.Table) -> Preview:
    """The table as text, as CSV writes each cell, the first PREVIEW_ROWS rows."""
    return Preview(
        title=table.title,
        columns=list(table.columns),
        rows=[[exports.text(cell) for cell in row] for row in table.rows[:PREVIEW_ROWS]],
        total_rows=len(table.rows),
    )


def narrowed(
    conn: psycopg.Connection[Any],
    pmc_id: UUID,
    table: exports.Table,
    group: UUID | None,
    property_id: UUID | None,
    owner_id: UUID | None = None,
) -> exports.Table:
    """The table's rows of the properties in the scope (and, with an owner, of theirs), its
    heading lines, and a line saying what it was narrowed to; the table as it is when nothing is
    chosen."""
    if group is None and property_id is None and owner_id is None:
        return table
    if "property" not in table.columns:
        raise exports.ExportError("this report is not by property; choose All properties")
    params = {"pmc": pmc_id, "owner": owner_id, **scope.params(group, property_id)}
    names = {
        name
        for (name,) in conn.execute(
            "SELECT p.display_name FROM trust_properties AS p WHERE p.pmc_id = %(pmc)s"  # noqa: S608
            f" AND {scope.within('p.id')}"
            " AND (%(owner)s::uuid IS NULL OR p.id IN (SELECT t.property_id"
            "  FROM trust_ledger_accounts AS t WHERE t.pmc_id = %(pmc)s"
            "  AND t.kind = 'owner_property' AND t.owner_id = %(owner)s::uuid))",
            params,
        ).fetchall()
    }
    labels = []
    if group is not None or property_id is not None:
        found = (
            conn.execute(
                "SELECT g.display_name FROM trust_property_groups AS g WHERE g.pmc_id = %(pmc)s"
                " AND g.id = %(scope_group)s",
                params,
            ).fetchone()
            if group is not None
            else conn.execute(
                "SELECT p.display_name FROM trust_properties AS p WHERE p.pmc_id = %(pmc)s"
                " AND p.id = %(scope_property)s",
                params,
            ).fetchone()
        )
        if found is None:
            raise exports.ExportError("no such property group or property")
        labels.append(found[0])
    if owner_id is not None:
        found = conn.execute(
            "SELECT o.display_name FROM trust_owners AS o WHERE o.pmc_id = %(pmc)s"
            " AND o.id = %(owner)s",
            params,
        ).fetchone()
        if found is None:
            raise exports.ExportError("no such owner")
        labels.append(f"{found[0]}'s properties")
    label = ", ".join(labels)
    at = table.columns.index("property")
    item = table.columns.index("item") if "item" in table.columns else None
    rows = [
        row
        for row in table.rows
        if row[at] in names or (item is not None and row[item] in HEADINGS)
    ]
    note: list[Any] = [None] * len(table.columns)
    note[at if item is None else item] = (
        f"narrowed to {label}: lines across all properties left out"
    )
    return exports.Table(f"{table.title}, {label}", table.columns, (*rows, tuple(note)))
