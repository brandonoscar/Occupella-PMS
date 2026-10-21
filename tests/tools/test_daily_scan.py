"""The daily scan of main (daily-scan.yml) runs the same scanners, at the same pinned versions,
as the workflows that already run them, so a bump in one can't leave the other behind.

gitleaks and pip-audit come from security.yml, Syft and Grype from selfhost.yml, and the Node
image npm audit runs in from e2e.yml (the one the portal is built with).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
SCAN = WORKFLOWS / "daily-scan.yml"


def text(name: str) -> str:
    return (WORKFLOWS / name).read_text()


def env_value(workflow: str, key: str) -> str:
    (value,) = re.findall(rf"^\s*{key}:\s*(\S+)", workflow, re.MULTILINE)
    return value


def go_installs(workflow: str) -> list[str]:
    return sorted(re.findall(r"go install (\S+@v[\d.]+)", workflow))


def test_gitleaks_and_pip_audit_are_security_ymls():
    scan, security = SCAN.read_text(), text("security.yml")

    for key in ("GITLEAKS_VERSION", "GITLEAKS_SHA256"):
        assert env_value(scan, key) == env_value(security, key), key
    assert re.findall(r"pip-audit==[\d.]+", scan) == sorted(
        set(re.findall(r"pip-audit==[\d.]+", security))
    )


def test_syft_and_grype_are_selfhost_ymls():
    scan = SCAN.read_text()

    assert go_installs(scan) == go_installs(text("selfhost.yml"))
    assert {tool.split("@")[0].rsplit("/", 1)[-1] for tool in go_installs(scan)} == {
        "syft",
        "grype",
    }


def test_npm_audit_runs_in_the_node_image_the_portal_is_built_with():
    assert env_value(SCAN.read_text(), "NODE_IMAGE") == env_value(text("e2e.yml"), "NODE_IMAGE")


def test_each_scan_fails_the_job_on_what_it_finds():
    scan = SCAN.read_text()

    # gitleaks over every commit: no --log-opts narrowing it to a range.
    assert re.search(
        r"gitleaks\" git --config \.gitleaks\.toml --redact --no-banner \.$", scan, re.M
    )
    assert "--log-opts" not in scan
    assert 'pip-audit" --desc -r requirements.txt -r requirements-dev.txt' in scan
    assert "npm audit --omit=dev" in scan
    assert "--only-fixed --fail-on high" in scan
    # Nothing may turn a finding into a pass.
    assert "continue-on-error" not in scan
    assert "|| true" not in scan


def test_it_runs_daily_and_by_hand_with_no_more_than_read_access():
    scan = SCAN.read_text()

    assert re.search(r"^  schedule:\n    - cron: \"\d+ \d+ \* \* \*\"", scan, re.MULTILINE)
    assert re.search(r"^  workflow_dispatch:", scan, re.MULTILINE)
    assert "pull_request_target" not in scan
    assert re.search(r"^permissions: \{\}$", scan, re.MULTILINE)
    assert re.findall(r"^\s+(\S+): (?:read|write)$", scan, re.MULTILINE) == ["contents"]
    assert "secrets." not in scan
