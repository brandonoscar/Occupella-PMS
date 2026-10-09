"""Golden-file checks for reports: owner statements, three-way reconciliation, rent roll.

A report is a SQL function named trust_report_<name>. Its golden cases live in
tests/golden/cases/<name>/<case>.sql: statements that build a fixed synthetic fixture, then the
report query as the last statement. The case runs in a transaction that is always rolled back.
Its output, normalized, must equal <case>.expected.txt byte for byte.

Updating an expected file is deliberate: run `pytest tests/golden --update-goldens`, then commit
the new file, which CODEOWNERS sends to review. CI never writes expected files.
"""

from __future__ import annotations

import difflib
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

LEDGER_ID = re.compile(r"\bpgl[a-z]_[0-9A-HJKMNP-TV-Z]{26}\b")
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
REPORTS = """
SELECT p.proname
FROM pg_proc p
WHERE p.pronamespace = 'public'::regnamespace AND p.proname LIKE 'trust\\_report\\_%'
ORDER BY 1
"""


def normalize(text: str) -> str:
    """Ids are random on every run; replace each with a stable placeholder, numbered in order of
    first appearance, so the same row keeps the same name throughout the output."""
    for pattern, label in ((LEDGER_ID, "id"), (UUID, "uuid")):
        seen: dict[str, str] = {}

        def stable(match: re.Match[str], seen: dict[str, str] = seen, label: str = label) -> str:
            return seen.setdefault(match.group(0), f"<{label}{len(seen) + 1}>")

        text = pattern.sub(stable, text)
    return text


def cell(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def render(columns: list[str], rows: list[tuple[Any, ...]]) -> str:
    lines = ["\t".join(columns)]
    lines += ["\t".join(cell(value) for value in row) for row in rows]
    return "\n".join(lines) + "\n"


def statements(sql_text: str) -> list[str]:
    """Split a case file on semicolons that end a line. Cases are plain SQL: no $$ bodies."""
    parts = re.split(r";\s*\n", sql_text.strip().rstrip(";") + "\n")
    return [part.strip() for part in parts if part.strip()]


def run_case(conn: psycopg.Connection[Any], case: Path) -> str:
    *setup, query = statements(case.read_text())
    with conn.transaction(force_rollback=True), conn.cursor() as cursor:
        for statement in setup:
            cursor.execute(statement.encode())
        cursor.execute(query.encode())
        columns = [column.name for column in cursor.description or []]
        return normalize(render(columns, cursor.fetchall()))


def expected_path(case: Path) -> Path:
    return case.with_suffix(".expected.txt")


def compare(case: Path, actual: str, update: bool) -> str | None:
    """None when it matches (or was just updated); otherwise a unified diff."""
    expected_file = expected_path(case)
    if update:
        expected_file.write_text(actual)
        return None
    expected = expected_file.read_text() if expected_file.exists() else ""
    if expected == actual:
        return None
    return "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=str(expected_file),
            tofile="actual",
        )
    )


def report_functions(conn: psycopg.Connection[Any]) -> list[str]:
    return [row[0] for row in conn.execute(REPORTS).fetchall()]


def cases(directory: Path) -> list[Path]:
    return sorted(directory.glob("*/*.sql"))


def missing_cases(functions: list[str], directory: Path) -> list[str]:
    """Report functions that have no golden case."""
    covered = {case.parent.name for case in cases(directory)}
    return [name for name in functions if name not in covered]
