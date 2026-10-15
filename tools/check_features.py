"""Every feature of the PMS is verified in a browser, and stays verified.

ci/features.toml lists each feature: what it does, the API operations it rests on, and the
browser tests (frontend/e2e) that use it as a person would. This check fails when:
  - an operation the API serves is claimed by no feature and isn't listed under [outside] with
    why, or the file names an operation the API doesn't serve (the API's own OpenAPI schema,
    with the fake partners on, says what it serves);
  - a feature has no statement, no operation or no browser test, or two features share an id;
  - with --browser-tests: a browser test the file names doesn't exist, or one that exists is
    named by no feature (the list comes from Playwright itself:
    `npx playwright test --list --reporter=json`);
  - with --api-log: an operation a feature claims was never called while the browser tests ran,
    or a request was answered with a server error (the API server's access log of that run);
  - with --base: a feature that existed on the base branch is gone, or has fewer browser tests.

usage: python -m tools.check_features [--browser-tests list.json] [--api-log api.log]
                                      [--base origin/main]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FILE = "ci/features.toml"
# Uvicorn's access log: `INFO:     127.0.0.1:43782 - "GET /api/rent?as_of=... HTTP/1.1" 200 OK`.
ACCESS = re.compile(r'"([A-Z]+) (\S+) HTTP/[\d.]+" (\d{3})')
PARAMETER = re.compile(r"\{[^}]+\}")


def served(root: Path) -> set[str]:
    """Every operation the API serves, as "METHOD /path", with every fake partner on so their
    routes count too. Building the app opens no connection."""
    sys.path.insert(0, str(root / "src"))
    from pms.api.app import Settings, create_app

    app = create_app(
        Settings(
            database_url="postgresql://nobody@127.0.0.1:1/none",
            screening_partner="fake",
            esign_partner="fake",
            email_sender="fake",
        )
    )
    paths: dict[str, dict[str, Any]] = app.openapi()["paths"]
    return {f"{method.upper()} {path}" for path, item in paths.items() for method in item}


def features(registry: dict[str, Any]) -> list[dict[str, Any]]:
    return list(registry.get("feature", []))


def check(registry: dict[str, Any], operations: set[str]) -> list[str]:
    problems = []
    outside: dict[str, str] = registry.get("outside", {})
    claimed: set[str] = set()
    seen: set[str] = set()
    for feature in features(registry):
        name = feature.get("id") or "(no id)"
        if name in seen:
            problems.append(f"{name}: two features have this id.")
        seen.add(name)
        if not feature.get("statement"):
            problems.append(f"{name}: missing `statement`.")
        if not feature.get("operations"):
            problems.append(f"{name}: names no API operation.")
        if not feature.get("browser"):
            problems.append(f"{name}: names no browser test.")
        for operation in feature.get("operations", []):
            if operation not in operations:
                problems.append(f"{name}: the API serves no {operation}.")
            claimed.add(operation)
    for operation, why in outside.items():
        if operation not in operations:
            problems.append(f"[outside]: the API serves no {operation}.")
        if not why:
            problems.append(f"[outside]: {operation} needs a reason.")
        if operation in claimed:
            problems.append(f"[outside]: {operation} is claimed by a feature too.")
    for operation in sorted(operations - claimed - set(outside)):
        problems.append(f"{operation} is served but no feature claims it.")
    return problems


def browser_tests(listing: dict[str, Any]) -> set[str]:
    """Each test in Playwright's JSON list as "<spec file> › <describe> › <title>"."""

    def walk(suite: dict[str, Any], titles: list[str]) -> Iterable[str]:
        for spec in suite.get("specs", []):
            yield " › ".join([*titles, spec["title"]])
        for inner in suite.get("suites", []):
            yield from walk(inner, [*titles, inner["title"]])

    return {test for suite in listing.get("suites", []) for test in walk(suite, [suite["title"]])}


def check_browser(registry: dict[str, Any], tests: set[str]) -> list[str]:
    problems = []
    named: set[str] = set()
    for feature in features(registry):
        for test in feature.get("browser", []):
            named.add(test)
            if test not in tests:
                problems.append(f"{feature.get('id')}: browser test {test!r} does not exist.")
    for test in sorted(tests - named):
        problems.append(f"browser test {test!r} is named by no feature.")
    return problems


def operation_of(method: str, path: str, operations: set[str]) -> str | None:
    """The operation a request was for, as the router would pick it: a literal segment before
    a parameter."""
    path = path.split("?", 1)[0]
    candidates = []
    for operation in operations:
        verb, template = operation.split(" ", 1)
        if verb != method:
            continue
        pattern = "[^/]+".join(re.escape(literal) for literal in PARAMETER.split(template))
        if re.fullmatch(pattern, path):
            candidates.append((len(PARAMETER.findall(template)), operation))
    return min(candidates)[1] if candidates else None


def check_log(registry: dict[str, Any], operations: set[str], log: str) -> list[str]:
    problems = []
    called: set[str] = set()
    for line in log.splitlines():
        found = ACCESS.search(line)
        if found is None:
            continue
        method, path, status = found.group(1), found.group(2), int(found.group(3))
        if status >= 500:
            problems.append(f"{method} {path.split('?', 1)[0]} was answered {status}.")
        operation = operation_of(method, path, operations)
        if operation is not None:
            called.add(operation)
    for feature in features(registry):
        for operation in feature.get("operations", []):
            if operation not in called:
                problems.append(
                    f"{feature.get('id')}: {operation} was never called by the browser tests."
                )
    return problems


def compare(base: dict[str, Any], head: dict[str, Any]) -> list[str]:
    problems = []
    now = {feature.get("id"): feature for feature in features(head)}
    for feature in features(base):
        name = feature.get("id")
        if name not in now:
            problems.append(f"{name} was removed from {FILE}.")
        elif len(now[name].get("browser", [])) < len(feature.get("browser", [])):
            problems.append(f"{name} has fewer browser tests than on the base branch.")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--browser-tests", type=Path)
    parser.add_argument("--api-log", type=Path)
    parser.add_argument("--base")
    args = parser.parse_args(argv)

    registry = tomllib.loads((args.root / FILE).read_text())
    operations = served(args.root)
    problems = check(registry, operations)
    if args.browser_tests:
        listing = json.loads(args.browser_tests.read_text())
        problems += check_browser(registry, browser_tests(listing))
    if args.api_log:
        problems += check_log(registry, operations, args.api_log.read_text(errors="replace"))
    if args.base:
        shown = subprocess.run(
            ["git", "show", f"{args.base}:{FILE}"], cwd=args.root, capture_output=True, text=True
        )
        if shown.returncode == 0:
            problems += compare(tomllib.loads(shown.stdout), registry)

    for problem in problems:
        print(f"FAIL: {problem}")
    if not problems:
        ran = [
            name
            for name, given in (("browser tests", args.browser_tests), ("API log", args.api_log))
            if given
        ]
        outside = len(registry.get("outside", {}))
        print(
            f"ok: {len(features(registry))} features claim {len(operations) - outside} of the"
            f" API's {len(operations)} operations, {outside} outside them"
            + (f"; checked against the {' and the '.join(ran)}." if ran else ".")
        )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
