"""Synthetic data only (CLAUDE.md hard rule 2): flag values that look like real personal data.

gitleaks scans commits for secrets and, with the two rules in .gitleaks.toml, for SSN and EIN
shapes; it doesn't look for routing numbers, emails or phones. This scans every tracked file,
plus new files not yet added, for the shapes real records leave, and accepts only forms that are
never issued (the same tax-ID forms .gitleaks.toml lets through; a test holds the two together):

  kind             flagged                                           write this instead
  routing number   9 digits with a real Fed prefix and a valid ABA    9 digits that fail the
                   checksum                                          checksum, e.g. 123456789
  SSN / ITIN       ddd-dd-dddd that could be issued                  000-dd-dddd
  EIN              dd-ddddddd with a prefix the IRS assigns          00-ddddddd
  email            any domain but the reserved ones                  name@example.com, .test,
                                                                     .example, .invalid
  phone (US)       any number outside the fictional range            555-0100 to 555-0199

Real names and street addresses have no shape to match, so they aren't checked; review them.

Findings name the file, line and kind but never print the value, because CI logs on a public
repo are public. A value that looks real but isn't (a published example, say) can be allowed in
ci/synthetic_data.toml with a reason; an allowance that no longer matches anything fails too.

usage: python -m tools.check_synthetic_data
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = "ci/synthetic_data.toml"

# Fed routing symbols: 01-12 (Federal Reserve banks), 21-32 (thrifts), 61-72 (electronic), 80
# (traveler's checks). 00 is the U.S. government's and never a bank's.
ROUTING_PREFIXES = {*range(1, 13), *range(21, 33), *range(61, 73), 80}
# EIN prefixes the IRS has never assigned.
UNASSIGNED_EIN_PREFIXES = {"00", "07", "08", "09", "17", "18", "19", "28", "29", "49", "69"}
UNASSIGNED_EIN_PREFIXES |= {"70", "78", "79", "89", "96", "97"}
RESERVED_EMAIL_DOMAINS = {"example.com", "example.net", "example.org"}
RESERVED_EMAIL_SUFFIXES = (".example", ".test", ".invalid", ".localhost")

NINE_DIGITS = re.compile(r"(?<![\w.\-/])\d{9}(?![\w.\-/])")
SSN = re.compile(r"(?<![\w-])(\d{3})-(\d{2})-(\d{4})(?![\w-])")
EIN = re.compile(r"(?<![\w-])(\d{2})-\d{7}(?![\w-])")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
PHONE = re.compile(r"(?<!\w)(?:\+?1[ .-]?)?\(?[2-9]\d{2}\)?[ .-](\d{3})[ .-](\d{4})(?!\w)")


def passes_aba_checksum(digits: str) -> bool:
    d = [int(c) for c in digits]
    return (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + d[2] + d[5] + d[8]) % 10 == 0


def real_routing_number(match: re.Match[str]) -> bool:
    digits = match.group(0)
    return int(digits[:2]) in ROUTING_PREFIXES and passes_aba_checksum(digits)


def issuable_ssn(match: re.Match[str]) -> bool:
    area, group, serial = match.groups()
    return area not in ("000", "666") and group != "00" and serial != "0000"


def assigned_ein(match: re.Match[str]) -> bool:
    return match.group(1) not in UNASSIGNED_EIN_PREFIXES


def real_email(match: re.Match[str]) -> bool:
    domain = match.group(1).lower()
    return domain not in RESERVED_EMAIL_DOMAINS and not domain.endswith(RESERVED_EMAIL_SUFFIXES)


def real_phone(match: re.Match[str]) -> bool:
    exchange, line = match.groups()
    return not (exchange == "555" and 100 <= int(line) <= 199)


DETECTORS: dict[str, tuple[re.Pattern[str], Callable[[re.Match[str]], bool], str]] = {
    "routing number": (NINE_DIGITS, real_routing_number, "9 digits that fail the ABA checksum"),
    "SSN or ITIN": (SSN, issuable_ssn, "000-dd-dddd"),
    "EIN": (EIN, assigned_ein, "00-ddddddd"),
    "email address": (EMAIL, real_email, "an address at example.com, .test or .invalid"),
    "phone number": (PHONE, real_phone, "a 555-0100 to 555-0199 number"),
}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    value: str

    def report(self) -> str:
        advice = DETECTORS[self.kind][2]
        return (
            f"{self.path}:{self.line}: looks like a real {self.kind}. Use {advice}, or allow "
            f"it in {ALLOWLIST} with a reason."
        )


def scan(path: str, text: str) -> list[Finding]:
    findings = []
    for kind, (pattern, looks_real, _) in DETECTORS.items():
        for match in pattern.finditer(text):
            if looks_real(match):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(Finding(path, line, kind, match.group(0)))
    return sorted(findings, key=lambda f: (f.path, f.line, f.kind))


def files(root: Path) -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted({name for name in listed.split("\0") if name and (root / name).is_file()})


def read_text(path: Path) -> str | None:
    data = path.read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def load_allowlist(root: Path) -> tuple[list[dict[str, str]], list[str]]:
    path = root / ALLOWLIST
    entries = tomllib.loads(path.read_text())["allow"] if path.exists() else []
    problems = [
        f"{ALLOWLIST}: entry {n} needs a path, a value and a reason."
        for n, entry in enumerate(entries, start=1)
        if not all(str(entry.get(key, "")).strip() for key in ("path", "value", "reason"))
    ]
    return entries, problems


def check(root: Path) -> tuple[list[str], int, int]:
    """Problems, files scanned, binary files skipped."""
    entries, problems = load_allowlist(root)
    allowed = {(entry.get("path"), entry.get("value")) for entry in entries}
    used = set()
    scanned = skipped = 0
    for name in files(root):
        if name == ALLOWLIST:  # its values are allowed by definition
            continue
        text = read_text(root / name)
        if text is None:
            skipped += 1
            continue
        scanned += 1
        for finding in scan(name, text):
            if (finding.path, finding.value) in allowed:
                used.add((finding.path, finding.value))
            else:
                problems.append(finding.report())
    problems += [
        f"{ALLOWLIST}: the allowance for a value in {path} matches nothing any more; remove it."
        for path, value in sorted(allowed - used, key=str)
    ]
    return problems, scanned, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Flag real-looking personal and bank data")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    problems, scanned, skipped = check(args.root)
    for problem in problems:
        print(f"FAIL: {problem}")
    if not problems:
        print(f"ok: {scanned} files scanned ({skipped} binary skipped); nothing looks real.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
