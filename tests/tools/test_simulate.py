"""scripts/simulate.ps1, its stack (compose.e2e.yaml) and its workflow (simulate.yml) keep to what
the browser tests and the repo's rules need. CI's simulate job runs the script itself; these read
the files, as the other workflow tests do, so a drift fails every PR, not only the next run.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / "scripts/simulate.ps1").read_text(encoding="utf-8")
STACK = (ROOT / "compose.e2e.yaml").read_text()
WORKFLOW = (ROOT / ".github/workflows/simulate.yml").read_text()
E2E = (ROOT / ".github/workflows/e2e.yml").read_text()
CONFIG = (ROOT / "frontend/playwright.config.ts").read_text()


def block(text, key, indent):
    """The lines under `key:` at `indent` spaces, until the next key at that indent or less."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line == " " * indent + key + ":")
    found = []
    for line in lines[start + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        found.append(line)
    return "\n".join(found)


def settings(text):
    """KEY: value pairs of an environment block, quotes taken off."""
    return {
        m.group(1): m.group(2).strip('"')
        for m in re.finditer(r"^\s+([A-Z_]+): (.+)$", text, re.MULTILINE)
    }


def test_the_api_runs_with_the_settings_the_browser_tests_get_in_ci():
    serve = E2E[E2E.index("name: Serve the portal") :]
    serve = serve[: serve.index("nohup python -m pms.api")]
    ci = dict(re.findall(r"\b(PMS_[A-Z_]+)=(\S+)", serve))
    app = block(STACK, "app", 2)
    here = settings(block(app, "environment", 4))
    # compose.yaml's app gives the rest: plain http inside the network, the API on port 8000 of
    # the image (published below), the portal from the image's own build.
    base = settings(block(block((ROOT / "compose.yaml").read_text(), "app", 2), "environment", 4))
    merged = {**base, **here}
    for name in ("PMS_PORT", "PMS_STATIC_DIR"):
        ci.pop(name)
    assert {name: merged.get(name) for name in ci} == ci


def test_the_pms_is_published_on_this_machine_only_where_the_tests_look():
    assert re.findall(r"^\s+- (\S+:\d+:\d+)$", STACK, re.MULTILINE) == ["127.0.0.1:8123:8000"]
    assert '"http://127.0.0.1:8123"' in CONFIG
    assert "$BaseUrl = 'http://127.0.0.1:8123'" in SCRIPT


def test_the_seed_writes_only_next_to_the_tests_where_git_ignores_it():
    seed = block(STACK, "seed", 2)
    assert re.findall(r"^\s+- (\S+)$", block(seed, "volumes", 4), re.MULTILINE) == [
        "./frontend/e2e:/e2e"
    ]
    assert "python /e2e/seed.py > /e2e/.seed.json" in seed
    assert "profiles: [seed]" in seed  # `up` never seeds by itself
    ignored = (ROOT / "frontend/.gitignore").read_text().splitlines()
    assert {"e2e/.seed.json", "playwright-report/", "test-results/"} <= set(ignored)
    assert "playwright-report/simulate/" in SCRIPT and "test-results/simulate/" in SCRIPT


def test_windows_powershell_reads_the_script_as_written():
    # Windows PowerShell 5.1 reads a file without a byte order mark as the machine's ANSI code
    # page, so anything beyond ASCII would turn into other characters there.
    assert SCRIPT.isascii()
    assert "Set-StrictMode -Version 2" in SCRIPT
    # Nothing PowerShell 7 alone has: the ternary, null-coalescing and pipeline-chain operators.
    code = "\n".join(line for line in SCRIPT.splitlines() if not line.lstrip().startswith("#"))
    for newer in (" ?? ", " ? ", " && ", " || ", "?."):
        assert newer not in code, newer


def test_programs_run_where_their_exit_code_decides():
    # Windows PowerShell 5.1 turns a redirected stderr into errors, fatal under 'Stop' before the
    # exit code is read: every program runs through one of the two helpers, under 'Continue'.
    assert SCRIPT.count("$ErrorActionPreference = 'Continue'") >= 3
    for line in SCRIPT.splitlines():
        if "*> $null" in line:
            assert "& $Command *> $null" in line, line


def test_the_browsers_are_playwrights_and_edge_and_chrome_are_never_reinstalled():
    allowed = re.search(r"\[ValidateSet\(([^)]*)\)\]", SCRIPT).group(1)
    assert re.findall(r"'(\w+)'", allowed) == ["msedge", "chrome", "chromium", "firefox", "webkit"]
    # `playwright install msedge` installs over the computer's own Edge.
    assert "$Channels = @('msedge', 'chrome')" in SCRIPT
    assert "$Channels -notcontains $_" in SCRIPT
    defaults = re.search(r"\$Browsers = @\(([^)]*)\)", SCRIPT).group(1)
    assert re.findall(r"'(\w+)'", defaults) == ["msedge", "firefox", "chromium"]


def test_what_the_script_sets_is_what_the_config_reads():
    for name in ("E2E_BASE_URL", "PW_CHANNEL", "PW_TIMEZONE"):
        assert f"$env:{name} =" in SCRIPT
        assert f"process.env.{name}" in CONFIG
    zones = re.search(r"\$TimeZones = @\(([^)]*)\)", SCRIPT).group(1)
    assert re.findall(r"'([\w/]+)'", zones) == ["UTC", "America/Los_Angeles", "Pacific/Kiritimati"]


def test_every_run_starts_from_empty_books_and_the_last_is_removed():
    assert (
        SCRIPT.index("docker compose -f $Compose down -v")
        < SCRIPT.index("docker compose -f $Compose up -d --wait app")
        < SCRIPT.index("docker compose -f $Compose run --rm seed")
    )
    teardown = SCRIPT[SCRIPT.index("} finally {\n    foreach ($name in $saved.Keys)") :]
    assert "if ($KeepRunning)" in teardown and "down -v --remove-orphans" in teardown


def test_the_workflow_runs_the_script_on_its_own_prs_with_read_access_only():
    assert "pull_request_target" not in WORKFLOW
    assert "permissions: {}" in WORKFLOW and "contents: read" in WORKFLOW
    on_pr = block(block(WORKFLOW, "pull_request", 2), "paths", 4)
    paths = re.findall(r"^      - (\S+)$", on_pr, re.MULTILINE)
    assert paths == [
        "scripts/simulate.ps1",
        "compose.e2e.yaml",
        "frontend/playwright.config.ts",
        ".github/workflows/simulate.yml",
    ]
    assert "BROWSERS: ${{ inputs.browsers || 'chromium' }}" in WORKFLOW
    assert "TIME_ZONES: ${{ inputs.time_zones || 'UTC' }}" in WORKFLOW
    assert re.search(r"PSSCRIPTANALYZER_VERSION: \d+\.\d+\.\d+$", WORKFLOW, re.MULTILINE)
    assert "-EnableExit" in WORKFLOW
