"""gitleaks' ignore list (.gitleaksignore) passes only lines checked to hold example text.

gitleaks scans every commit (security.yml, daily-scan.yml), and history is never rewritten, so a
finding in an old commit that was never a secret would fail every scan for good. Such a finding
goes in .gitleaksignore as its fingerprint, `commit:file:rule:line`, which names that one line of
that one commit: the same text in any other commit is still a finding. This check reads each
fingerprint's line back from git and passes it only if it holds nothing but example text:

  us-ein, us-ssn    every tax-ID-shaped number on the line (by .gitleaks.toml's own rule) is the
                    format example EXAMPLES names, the one help text shows; a real-looking
                    number on the line, or none, fails
  generic-api-key   the line is a comment, and every quoted value on it is a placeholder key
                    starting "xx-" (no state's code: tools/check_synthetic_data.py, docs/law.md)

Anything else fails: a line that isn't a fingerprint, another rule, or a commit, file or line git
doesn't have. It needs the whole history (the scans' checkout, fetch-depth 0).

usage: python -m tools.check_gitleaks_ignore [--ignore FILE] [--config FILE] [--repo DIR]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The format examples help text showed (EIN, SSN), written in parts so this file is not itself a
# finding of the rules it checks.
EXAMPLES = {
    "us-ein": "-".join(("12", "3456789")),
    "us-ssn": "-".join(("123", "45", "6789")),
}
PLACEHOLDER_KEY = re.compile(r'"(xx-[^"]*)"')
QUOTED = re.compile(r'"([^"]*)"')
FINGERPRINT = re.compile(
    r"^(?P<commit>[0-9a-f]{40}):(?P<file>.+):(?P<rule>[a-z0-9-]+):(?P<line>\d+)$"
)


def rule_patterns(config: Path) -> dict[str, re.Pattern[str]]:
    """The tax-ID rules' regexes, as .gitleaks.toml writes them (Go's syntax, Python's too)."""
    rules = tomllib.loads(config.read_text())["rules"]
    return {rule["id"]: re.compile(rule["regex"]) for rule in rules if rule["id"] in EXAMPLES}


def line_at(repo: Path, commit: str, file: str, number: int) -> str | None:
    shown = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{file}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if shown.returncode != 0:
        return None
    lines = shown.stdout.splitlines()
    return lines[number - 1] if 0 < number <= len(lines) else None


def problem(fingerprint: str, repo: Path, patterns: dict[str, re.Pattern[str]]) -> str | None:
    found = FINGERPRINT.match(fingerprint)
    if found is None:
        return f"{fingerprint!r} is not commit:file:rule:line"
    commit, file, rule = found["commit"], found["file"], found["rule"]
    text = line_at(repo, commit, file, int(found["line"]))
    if text is None:
        return f"{fingerprint}: git has no such commit, file or line"
    if rule in patterns:
        numbers = patterns[rule].findall(text)
        if not numbers:
            return f"{fingerprint}: the line holds no number the rule {rule} matches"
        if any(number != EXAMPLES[rule] for number in numbers):
            return f"{fingerprint}: the line holds a number that isn't the {rule} format example"
        return None
    if rule == "generic-api-key":
        values = QUOTED.findall(text)
        if not text.lstrip().startswith("#") or not values:
            return f"{fingerprint}: only a comment showing a placeholder key may be passed"
        if len(PLACEHOLDER_KEY.findall(text)) != len(values):
            return f"{fingerprint}: a quoted value on the line isn't a placeholder key (xx-...)"
        return None
    return f"{fingerprint}: the rule {rule} is not one this check knows how to pass"


def fingerprints(ignore: Path) -> list[str]:
    lines = (line.strip() for line in ignore.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ignore", type=Path, default=ROOT / ".gitleaksignore")
    parser.add_argument("--config", type=Path, default=ROOT / ".gitleaks.toml")
    parser.add_argument("--repo", type=Path, default=ROOT)
    options = parser.parse_args(argv)

    patterns = rule_patterns(options.config)
    passed = fingerprints(options.ignore)
    problems = [p for p in (problem(f, options.repo, patterns) for f in passed) if p]
    for found in problems:
        print(f"FAIL: {found}")
    if not problems:
        print(f"ok: {len(passed)} findings passed, each line holding only example text.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
