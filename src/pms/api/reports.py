"""A report on screen (the founder's punch list, ADR 0006, step 11): any report's rows as text to
preview before Export, and a report narrowed to a property group or one property.

Narrowing keeps the rows of the report's `property` column that are in the scope (by the names
the report printed) and the header lines before the first of them; every other line without a
property (a subtotal, a total across all properties) is left out rather than shown beside rows it
no longer adds up, and a last line says what the report was narrowed to. A report with no
property column is not narrowed. The report itself runs whole, under the caller's role, as every
download does; nothing here moves money.
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
) -> exports.Table:
    """The table's rows of the properties in the scope, its header lines, and a line saying
    what it was narrowed to; the table as it is when nothing is chosen."""
    if group is None and property_id is None:
        return table
    if "property" not in table.columns:
        raise exports.ExportError("this report is not by property; choose All properties")
    params = {"pmc": pmc_id, **scope.params(group, property_id)}
    names = {
        name
        for (name,) in conn.execute(
            "SELECT p.display_name FROM trust_properties AS p WHERE p.pmc_id = %(pmc)s"
            f" AND {scope.within('p.id')}",
            params,
        ).fetchall()
    }
    label = (
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
    if label is None:
        raise exports.ExportError("no such property group or property")
    at = table.columns.index("property")
    rows: list[tuple[Any, ...]] = []
    started = False
    for row in table.rows:
        if row[at] is not None:
            started = True
            if row[at] in names:
                rows.append(row)
        elif not started:
            rows.append(row)
    note: list[Any] = [None] * len(table.columns)
    note[table.columns.index("item") if "item" in table.columns else at] = (
        f"narrowed to {label[0]}: lines across all properties left out"
    )
    return exports.Table(f"{table.title}, {label[0]}", table.columns, (*rows, tuple(note)))
