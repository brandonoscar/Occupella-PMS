"""Fail when a PR weakens a check: lowers a number in ci/thresholds.toml or drops an entry.

Every number in ci/thresholds.toml is a floor, so a PR may raise it, never lower it. A key may
not disappear. A list may lose an item only when the thing it names is gone: a money function
no longer in db/schema.sql, or a Python path no longer in the repo.

usage: python -m tools.check_thresholds --base origin/main
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FILE = "ci/thresholds.toml"


def flatten(table: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in table.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(flatten(value, dotted + "."))
        else:
            flat[dotted] = value
    return flat


def removable(key: str, item: str, root: Path) -> bool:
    if key == "coverage.money.functions":
        schema = (root / "db/schema.sql").read_text()
        return f"FUNCTION public.{item}(" not in schema
    if key == "coverage.money.python_paths":
        return not (root / item).exists()
    return False


def compare(base: dict[str, Any], head: dict[str, Any], root: Path) -> list[str]:
    problems = []
    old, new = flatten(base), flatten(head)
    for key, before in old.items():
        if key not in new:
            problems.append(f"{key} was removed from {FILE}.")
            continue
        after = new[key]
        if isinstance(before, bool) or not isinstance(before, int | float | list):
            continue
        if isinstance(before, list):
            for item in before:
                if item not in after and not removable(key, item, root):
                    problems.append(f"{key} lost {item!r}, which still exists.")
        elif not isinstance(after, int | float) or after < before:
            problems.append(f"{key} was lowered from {before} to {after}.")
    return problems


def base_text(base: str, root: Path) -> str | None:
    shown = subprocess.run(
        ["git", "show", f"{base}:{FILE}"], cwd=root, capture_output=True, text=True
    )
    return shown.stdout if shown.returncode == 0 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="git ref of the PR's base")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    text = base_text(args.base, args.root)
    if text is None:
        print(f"{FILE} does not exist at {args.base}; nothing to compare.")
        return 0
    problems = compare(
        tomllib.loads(text), tomllib.loads((args.root / FILE).read_text()), args.root
    )
    for problem in problems:
        print(f"FAIL: {problem}")
    if not problems:
        print(f"ok: no threshold in {FILE} was lowered or removed.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
