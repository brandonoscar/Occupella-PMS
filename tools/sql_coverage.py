"""PL/pgSQL coverage, measured with the plpgsql_check extension's profiler.

The money logic lives in PL/pgSQL functions and triggers, not in Python, so Python coverage
alone would say nothing about it. plpgsql_check counts how often each statement ran and which
branches were taken. It is used only in the coverage job's throwaway test database; the schema
itself never depends on it.

The server must load the extension at start (shared_preload_libraries = 'plpgsql_check') so
that every test connection adds to one shared profile.

Two halves:
  start() / write_raw()  run inside the test session (tests/conftest.py) and dump the raw
                         per-statement counts to JSON;
  everything else        is pure: it turns that JSON into totals and a Cobertura XML report
                         whose lines point into db/migrations/*.sql, so diff-cover can check
                         the lines a PR changed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import psycopg
from psycopg import sql

FUNCTIONS = """
SELECT p.oid::int AS oid, p.proname AS name,
       pg_get_function_identity_arguments(p.oid) AS args, p.prosrc AS source,
       plpgsql_coverage_branches(p.oid) AS branch_ratio
FROM pg_proc p
JOIN pg_language l ON l.oid = p.prolang
WHERE l.lanname = 'plpgsql'
  AND p.pronamespace = 'public'::regnamespace
  AND NOT EXISTS (
      SELECT 1 FROM pg_depend d
      WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'
  )
ORDER BY p.proname, args
"""

STATEMENTS = """
SELECT stmtid, parent_stmtid, parent_note, lineno, exec_stmts, stmtname
FROM plpgsql_profiler_function_statements_tb(%s)
ORDER BY stmtid
"""

LOOPS = frozenset(
    {
        "LOOP",
        "WHILE",
        "FOR with integer loop variable",
        "FOR over SELECT rows",
        "FOR over cursor",
        "FOR over EXECUTE statement",
        "FOREACH over array",
    }
)


def start(conn: psycopg.Connection[Any], database: str) -> None:
    """Turn the profiler on for every new session in `database` and clear old counts."""
    preload = conn.execute("SHOW shared_preload_libraries").fetchone()
    if preload is None or "plpgsql_check" not in str(preload[0]):
        raise RuntimeError(
            "PL/pgSQL coverage needs the server started with "
            "shared_preload_libraries = 'plpgsql_check'."
        )
    conn.execute("CREATE EXTENSION IF NOT EXISTS plpgsql_check")
    conn.execute(
        sql.SQL("ALTER DATABASE {} SET plpgsql_check.profiler = on").format(
            sql.Identifier(database)
        )
    )
    conn.execute("SELECT plpgsql_profiler_reset_all()")


def collect(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    functions = []
    for oid, name, args, source, branch_ratio in conn.execute(FUNCTIONS).fetchall():
        statements = [
            {
                "stmtid": row[0],
                "parent_stmtid": row[1],
                "parent_note": row[2],
                "lineno": row[3],
                "exec_stmts": row[4],
                "stmtname": row[5],
            }
            for row in conn.execute(STATEMENTS, (oid,)).fetchall()
        ]
        functions.append(
            {
                "name": name,
                "args": args,
                "source": source,
                "branch_ratio": branch_ratio,
                "statements": statements,
            }
        )
    return functions


def write_raw(conn: psycopg.Connection[Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(collect(conn), indent=1))


@dataclass(frozen=True)
class FunctionCoverage:
    name: str
    args: str
    statements: int
    statements_hit: int
    branches: int
    branches_hit: int


def branch_count(statements: list[dict[str, Any]]) -> int:
    """Branches the way plpgsql_check counts them: an IF has its THEN, each ELSIF and an ELSE
    (implicit when not written); a CASE has each WHEN and an ELSE; a loop is one branch."""
    total = 0
    for stmt in statements:
        name = stmt["stmtname"]
        if name in ("IF", "CASE"):
            notes = {
                child["parent_note"]
                for child in statements
                if child["parent_stmtid"] == stmt["stmtid"]
            }
            arms = {note for note in notes if note and not note.startswith("else")}
            total += max(len(arms), 1) + 1
        elif name in LOOPS:
            total += 1
    return total


def summarize(function: dict[str, Any]) -> FunctionCoverage:
    statements = function["statements"]
    branches = branch_count(statements)
    exact = function["branch_ratio"] * branches
    branches_hit = round(exact)
    if abs(exact - branches_hit) > 1e-6:
        raise ValueError(
            f"{function['name']}({function['args']}): branch ratio {function['branch_ratio']} "
            f"does not fit {branches} branches; update branch_count()"
        )
    return FunctionCoverage(
        name=function["name"],
        args=function["args"],
        statements=len(statements),
        statements_hit=sum(1 for s in statements if s["exec_stmts"] > 0),
        branches=branches,
        branches_hit=branches_hit,
    )


def percent(hit: int, total: int) -> float:
    return 100.0 if total == 0 else 100.0 * hit / total


def locate(source: str, files: dict[str, str]) -> tuple[str, int] | None:
    """The migration file that defines this function body, and the line its `$$` is on.

    A later migration that redefines a function wins, so files are searched newest first."""
    for path in sorted(files, reverse=True):
        offset = files[path].find(source)
        if offset >= 0:
            return path, files[path].count("\n", 0, offset) + 1
    return None


def line_hits(function: dict[str, Any], files: dict[str, str]) -> tuple[str, dict[int, int]] | None:
    """Map each statement to its line in the migration file, with the times it ran."""
    found = locate(function["source"], files)
    if found is None:
        return None
    path, first_line = found
    hits: dict[int, int] = {}
    for stmt in function["statements"]:
        line = first_line + stmt["lineno"] - 1
        hits[line] = max(hits.get(line, 0), stmt["exec_stmts"])
    return path, hits


def cobertura(functions: list[dict[str, Any]], files: dict[str, str]) -> str:
    """A Cobertura XML report that diff-cover can read, one <class> per migration file."""
    per_file: dict[str, dict[int, int]] = {}
    for function in functions:
        mapped = line_hits(function, files)
        if mapped is None:
            continue
        path, hits = mapped
        lines = per_file.setdefault(path, {})
        for line, count in hits.items():
            lines[line] = max(lines.get(line, 0), count)

    all_lines = [count for lines in per_file.values() for count in lines.values()]
    root = ElementTree.Element(
        "coverage",
        {
            "version": "1",
            "line-rate": f"{percent(sum(1 for c in all_lines if c), len(all_lines)) / 100:.4f}",
            "branch-rate": "0",
        },
    )
    ElementTree.SubElement(ElementTree.SubElement(root, "sources"), "source").text = "."
    classes = ElementTree.SubElement(
        ElementTree.SubElement(ElementTree.SubElement(root, "packages"), "package", name="sql"),
        "classes",
    )
    for path in sorted(per_file):
        lines = per_file[path]
        hit = sum(1 for count in lines.values() if count)
        cls = ElementTree.SubElement(
            classes,
            "class",
            {
                "name": Path(path).name,
                "filename": path,
                "line-rate": f"{percent(hit, len(lines)) / 100:.4f}",
                "branch-rate": "0",
            },
        )
        line_parent = ElementTree.SubElement(cls, "lines")
        for line in sorted(lines):
            ElementTree.SubElement(line_parent, "line", number=str(line), hits=str(lines[line]))
    return ElementTree.tostring(root, encoding="unicode")


def read_migrations(directory: Path, root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(directory.glob("*.sql"))
    }
