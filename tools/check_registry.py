"""Every required invariant, report and self-host step is either tested or tracked.

ci/registry.toml lists what the ledger must guarantee. Each entry names the tests that prove
it, or a `pending` link to the open issue that will, or both when only part is built. This
check fails when:
  - a named test doesn't exist (found by parsing the test file, never by text search);
  - an entry has neither tests nor a pending issue, or the link isn't an issue in this repo;
  - with --check-issues: a pending issue is closed (the work is done, so the test must exist);
  - with --base: an entry that existed on the base branch is gone, or lost its tests.

usage: python -m tools.check_registry [--check-issues] [--base origin/main]
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FILE = "ci/registry.toml"
SECTIONS = ("invariant", "report", "selfhost")
ISSUE = re.compile(r"^https://github\.com/brandonoscar/Occupella-PMS/issues/(\d+)$")
API = "https://api.github.com/repos/brandonoscar/Occupella-PMS/issues/{number}"


def node_exists(node_id: str, root: Path) -> bool:
    path, _, name = node_id.partition("::")
    file = root / path
    if not name or not file.is_file():
        return False
    tree = ast.parse(file.read_text(), filename=str(file))
    parts = name.split("::")
    scope: list[ast.stmt] = tree.body
    for i, part in enumerate(parts):
        found: ast.stmt | None = None
        for node in scope:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                if node.name == part:
                    found = node
            elif (
                isinstance(node, ast.Assign)
                and i == len(parts) - 1
                and any(isinstance(t, ast.Name) and t.id == part for t in node.targets)
            ):
                found = node
        if found is None:
            return False
        scope = found.body if isinstance(found, ast.ClassDef) else []
    return True


def entries(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        f"{section}:{entry['id']}": entry
        for section in SECTIONS
        for entry in registry.get(section, [])
    }


def check(registry: dict[str, Any], root: Path) -> list[str]:
    problems = []
    for key, entry in entries(registry).items():
        tests = entry.get("tests", [])
        pending = entry.get("pending")
        if not entry.get("statement"):
            problems.append(f"{key}: missing `statement`.")
        if not tests and not pending:
            problems.append(f"{key}: needs `tests`, a `pending` issue link, or both.")
        if pending and not ISSUE.match(pending):
            problems.append(f"{key}: pending must be an issue URL in this repo, got {pending!r}.")
        for node_id in tests:
            if not node_exists(node_id, root):
                problems.append(f"{key}: test {node_id} does not exist.")
    return problems


def issue_state(number: str, token: str | None) -> str:
    request = urllib.request.Request(API.format(number=number))
    request.add_header("Accept", "application/vnd.github+json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read())
    if "pull_request" in data:
        return "pull request"
    return str(data["state"])


def check_issues(
    registry: dict[str, Any],
    token: str | None,
    fetch: Callable[[str, str | None], str] | None = None,
) -> list[str]:
    fetch = fetch or issue_state
    problems = []
    for key, entry in entries(registry).items():
        match = ISSUE.match(entry.get("pending") or "")
        if match:
            state = fetch(match.group(1), token)
            if state != "open":
                problems.append(f"{key}: pending issue {entry['pending']} is {state}.")
    return problems


def compare(base: dict[str, Any], head: dict[str, Any]) -> list[str]:
    problems = []
    new = entries(head)
    for key, entry in entries(base).items():
        if key not in new:
            problems.append(f"{key} was removed from {FILE}.")
        elif set(entry.get("tests", [])) - set(new[key].get("tests", [])):
            lost = sorted(set(entry.get("tests", [])) - set(new[key].get("tests", [])))
            problems.append(f"{key} lost tests {lost}.")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check-issues", action="store_true")
    parser.add_argument("--base")
    args = parser.parse_args(argv)

    registry = tomllib.loads((args.root / FILE).read_text())
    problems = check(registry, args.root)
    if args.check_issues:
        problems += check_issues(registry, os.environ.get("GITHUB_TOKEN"))
    if args.base:
        shown = subprocess.run(
            ["git", "show", f"{args.base}:{FILE}"], cwd=args.root, capture_output=True, text=True
        )
        if shown.returncode == 0:
            problems += compare(tomllib.loads(shown.stdout), registry)

    for problem in problems:
        print(f"FAIL: {problem}")
    if not problems:
        tested = sum(1 for e in entries(registry).values() if e.get("tests"))
        pending = sum(1 for e in entries(registry).values() if e.get("pending"))
        print(f"ok: {len(entries(registry))} entries, {tested} with tests, {pending} pending.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
