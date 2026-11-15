"""Every tool a workflow downloads (dbmate, gitleaks) is fetched with retries and checked against
its pinned SHA-256: one dropped connection to GitHub's release host failed a whole run before
(issue #233), and a retry must never stand in for the checksum."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))
RETRY = "--retry 5 --retry-all-errors --retry-delay 5"


def downloads() -> list[tuple[str, str, str]]:
    """Each curl that writes a file (-o), with the workflow it is in and the lines after it."""
    found = []
    for workflow in WORKFLOWS:
        lines = workflow.read_text().splitlines()
        for i, line in enumerate(lines):
            if re.search(r"\bcurl\b.*\s-o\s", line):
                found.append((workflow.name, line, "\n".join(lines[i + 1 : i + 4])))
    return found


def test_the_downloads_are_found():
    names = {name for name, _, _ in downloads()}
    assert {"ci.yml", "db.yml", "security.yml", "daily-scan.yml"} <= names


def test_every_download_retries_and_is_checked_against_its_checksum():
    for name, line, after in downloads():
        assert RETRY in line, f"{name}: {line.strip()}"
        assert "sha256sum --check" in after, f"{name}: {line.strip()} is not checked"
