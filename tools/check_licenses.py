"""Fail if an installed dependency's license is not on the allowlist in ci/licenses.toml.

Reads every installed package (runtime and dev) through pip-licenses, so transitive
dependencies are checked too, not only the ones a PR names.

usage: python -m tools.check_licenses
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def unwrap(text: str) -> str:
    text = text.strip()
    while text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    return text


def alternatives(license_text: str) -> list[list[str]]:
    """'A OR B' / 'A; B' are alternatives; 'A AND B' needs both."""
    options = []
    for option in re.split(r"\s+OR\s+|;\s*", unwrap(license_text)):
        option = unwrap(option)
        if option:
            options.append([unwrap(part) for part in re.split(r"\s+AND\s+", option)])
    return options


def verdict(name: str, license_text: str, policy: dict[str, Any]) -> str | None:
    """None when allowed, else the reason it fails."""
    override = policy.get("packages", {}).get(name)
    if override:
        license_text = override["license"]
    aliases = policy.get("aliases", {})
    allowed = set(policy["allowed"])
    for pattern in policy.get("denied_patterns", []):
        if re.search(rf"(?<![A-Za-z]){re.escape(pattern)}(?![A-Za-z])", license_text):
            return f"{name}: license {license_text!r} is denied ({pattern})."
    for option in alternatives(license_text):
        if all(aliases.get(part, part) in allowed for part in option):
            return None
    return (
        f"{name}: license {license_text!r} is not on the allowlist in ci/licenses.toml. "
        "Check it is compatible with Apache-2.0 before adding it."
    )


def installed() -> list[dict[str, str]]:
    output = subprocess.run(
        [sys.executable, "-m", "piplicenses", "--format=json", "--from=mixed"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    packages: list[dict[str, str]] = json.loads(output)
    return packages


def main(argv: list[str] | None = None, packages: list[dict[str, str]] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--policy", type=Path, default=ROOT / "ci/licenses.toml")
    args = parser.parse_args(argv)
    policy = tomllib.loads(args.policy.read_text())

    found = installed() if packages is None else packages
    problems = [
        problem
        for package in found
        if (problem := verdict(package["Name"], package["License"], policy)) is not None
    ]
    for package in found:
        print(f"{package['Name']:24} {package['Version']:12} {package['License']}")
    for problem in problems:
        print(f"FAIL: {problem}")
    if not problems:
        print(f"ok: {len(found)} packages, every license allowed.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
