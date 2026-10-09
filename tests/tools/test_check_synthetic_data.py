"""tools/check_synthetic_data.py: real-looking personal and bank data is flagged, never printed.

The check scans this file too, so every value with a real shape is joined from parts at run
time and never sits here whole. None of them is anyone's.
"""

import re
import tomllib
from pathlib import Path

import pytest

from tools import check_synthetic_data as synthetic

ROOT = Path(__file__).resolve().parents[2]

ROUTING = "1200" + "00003"  # Fed prefix 12, valid checksum (worked by hand: 3+14+3 = 20)
SSN = "123-" + "45-6788"
ITIN = "912-" + "70-1234"
EIN = "12-" + "3456789"
EMAIL = "jo@" + "realmail.co"
PHONE = "(212) " + "867-5309"
PHONE_DOTS = "+1 212." + "867.5309"
PHONE_555 = "212-555-" + "0200"  # 555, but outside the fictional 0100-0199 lines


def kinds(text):
    return [finding.kind for finding in synthetic.scan("f.txt", text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Routing numbers: a real Fed prefix and a valid checksum is flagged.
        (f"routing {ROUTING}", ["routing number"]),
        ("routing 123456789", []),  # checksum fails
        ("routing 001000013", []),  # 00 is never a bank's prefix
        (f"id a{ROUTING}b {ROUTING}9", []),  # inside a longer token, or 10 digits
        # SSNs and ITINs: only never-issued forms pass.
        (f"ssn {SSN}", ["SSN or ITIN"]),
        (f"itin {ITIN}", ["SSN or ITIN"]),
        ("ssn 000-12-3456 666-12-3456 123-00-4567 123-45-0000", []),
        # EINs: a prefix the IRS assigns is flagged.
        (f"ein {EIN}", ["EIN"]),
        ("ein 00-1234567 07-1234567", []),
        ("date 2026-10-09", []),
        # Email: only reserved domains pass.
        (f"mail {EMAIL}", ["email address"]),
        ("mail jo@Example.COM ci@example.invalid a@b.test x@host.example y@box.localhost", []),
        # Phones: only the fictional 555-0100 to 555-0199 range passes.
        (f"call {PHONE}", ["phone number"]),
        (f"call {PHONE_DOTS}", ["phone number"]),
        ("call (212) 555-0142 or 212-555-0199", []),
        (f"call {PHONE_555}", ["phone number"]),
    ],
)
def test_detectors(text, expected):
    assert kinds(text) == expected


def test_findings_name_the_line_but_never_the_value():
    findings = synthetic.scan("tests/fixtures/x.csv", f"a\nb {SSN}\n")

    assert [(f.path, f.line) for f in findings] == [("tests/fixtures/x.csv", 2)]
    report = findings[0].report()
    assert "tests/fixtures/x.csv:2: looks like a real SSN or ITIN" in report
    assert SSN not in report


def test_check_scans_tracked_and_new_files_and_skips_binaries(repo):
    repo.write("tracked.txt", f"owner {EMAIL}\n")
    repo.commit()
    repo.write("untracked.txt", f"call {PHONE}\n")
    repo.write(".gitignore", "ignored.txt\n")
    repo.write("ignored.txt", f"ein {EIN}\n")
    (repo.path / "image.bin").write_bytes(b"\x00\x01 " + EIN.encode())
    (repo.path / "latin1.txt").write_bytes(f"caf\xe9 {EIN}".encode("latin-1"))

    problems, scanned, skipped = synthetic.check(repo.path)

    assert [problem.split(":")[0] for problem in problems] == ["tracked.txt", "untracked.txt"]
    assert (scanned, skipped) == (3, 2)  # .gitignore, tracked.txt, untracked.txt


def test_allowlist_needs_a_reason_and_must_still_match(repo):
    repo.write("docs/a.md", f"routing {ROUTING}\n")
    repo.write(
        synthetic.ALLOWLIST,
        f"""
[[allow]]
path = "docs/a.md"
value = "{ROUTING}"
reason = "made up for this test"

[[allow]]
path = "docs/gone.md"
value = "{EIN}"
reason = "the file was deleted"

[[allow]]
path = "docs/a.md"
value = "{SSN}"
""",
    )

    problems, _, _ = synthetic.check(repo.path)

    assert problems == [
        f"{synthetic.ALLOWLIST}: entry 3 needs a path, a value and a reason.",
        f"{synthetic.ALLOWLIST}: the allowance for a value in docs/a.md matches nothing any "
        "more; remove it.",
        f"{synthetic.ALLOWLIST}: the allowance for a value in docs/gone.md matches nothing any "
        "more; remove it.",
    ]


def test_main_reports_and_exits(repo, capsys):
    repo.write("ok.txt", "jo@example.com\n")
    assert synthetic.main(["--root", str(repo.path)]) == 0
    assert "ok: 1 files scanned (0 binary skipped)" in capsys.readouterr().out

    repo.write("bad.txt", f"{EMAIL}\n")
    assert synthetic.main(["--root", str(repo.path)]) == 1
    assert "FAIL: bad.txt:1: looks like a real email address" in capsys.readouterr().out


def gitleaks_allows(rule_id, value):
    """Whether .gitleaks.toml's allowlists for a rule let `value` through (same regex syntax)."""
    config = tomllib.loads((ROOT / ".gitleaks.toml").read_text())
    (rule,) = [rule for rule in config["rules"] if rule["id"] == rule_id]
    assert re.fullmatch(rule["regex"].replace(r"\b", ""), value), "sample doesn't fit the rule"
    return any(
        re.search(pattern, value)
        for allowlist in rule["allowlists"]
        for pattern in allowlist["regexes"]
    )


def test_gitleaks_and_this_check_accept_the_same_tax_ids():
    # The two read different files (gitleaks: commits; this check: the working tree), so they
    # must agree on what a synthetic tax ID looks like, or a fixture passes one and fails CI.
    for area in ("000", "666", "123", "912"):
        for group in ("00", "45"):
            for serial in ("0000", "6788"):
                ssn = "-".join((area, group, serial))
                assert gitleaks_allows("us-ssn", ssn) is (kinds(ssn) == []), ssn
    for prefix in range(100):
        ein = f"{prefix:02d}-" + "1234567"
        assert gitleaks_allows("us-ein", ein) is (kinds(ein) == []), ein
