"""tools/check_gitleaks_ignore.py passes an ignored finding only when its line is example text.

Each test builds a small git repository of its own, so it runs without the project's history.
The real .gitleaksignore is checked against the whole history where the scans run (security.yml,
daily-scan.yml); here, its form.
"""

import contextlib
import io
import subprocess
from pathlib import Path

from tools import check_gitleaks_ignore as check

ROOT = Path(__file__).resolve().parents[2]
EIN, SSN = check.EXAMPLES["us-ein"], check.EXAMPLES["us-ssn"]
# Shaped like real numbers, in parts so this file holds none (a test of the check, not data).
OTHER_EIN = "-".join(("12", "3456780"))
OTHER_SSN = "-".join(("123", "45", "6780"))
# A placeholder key, in parts too, so this file is not a finding of gitleaks' generic rule.
PLACEHOLDER = "-".join(("xx", "deposit", "cap", "2025", "01", "01"))


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def commit(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "-m",
        name,
    )
    return git(repo, "rev-parse", "HEAD")


def run(tmp_path: Path, *lines: str) -> tuple[int, str]:
    ignore = tmp_path / "ignore"
    ignore.write_text("# a comment\n\n" + "\n".join(lines) + "\n")
    config, where = str(ROOT / ".gitleaks.toml"), str(tmp_path / "r")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = check.main(["--ignore", str(ignore), "--config", config, "--repo", where])
    return code, out.getvalue()


def repo(tmp_path: Path) -> Path:
    where = tmp_path / "r"
    where.mkdir()
    git(where, "init", "-q")
    return where


def test_the_format_examples_pass(tmp_path):
    r = repo(tmp_path)
    sha = commit(r, "help.py", f'HELP = "nine digits: {EIN} for an EIN, {SSN} for an SSN"\n')

    code, out = run(tmp_path, f"{sha}:help.py:us-ein:1", f"{sha}:help.py:us-ssn:1")

    assert code == 0, out
    assert "ok: 2 findings passed" in out


def test_a_number_that_is_not_the_example_fails_even_beside_it(tmp_path):
    r = repo(tmp_path)
    sha = commit(r, "help.py", f'x = "{EIN} {OTHER_EIN}"\ny = "{OTHER_SSN}"\n')

    code, out = run(tmp_path, f"{sha}:help.py:us-ein:1", f"{sha}:help.py:us-ssn:2")

    assert code == 1
    assert "isn't the us-ein format example" in out
    assert "isn't the us-ssn format example" in out


def test_a_line_with_no_number_of_the_rule_fails(tmp_path):
    r = repo(tmp_path)
    sha = commit(r, "help.py", f'x = "{SSN}"\n')

    code, out = run(tmp_path, f"{sha}:help.py:us-ein:1")

    assert code == 1
    assert "holds no number the rule us-ein matches" in out


def test_only_a_comment_with_placeholder_keys_passes_the_generic_rule(tmp_path):
    r = repo(tmp_path)
    sha = commit(
        r,
        "rules.toml",
        f'#   key = "{PLACEHOLDER}"  # an example\n'
        f'key = "{PLACEHOLDER}"\n'
        '#   key = "xx-a"  token = "not-a-placeholder"\n',
    )

    code, out = run(
        tmp_path,
        f"{sha}:rules.toml:generic-api-key:1",
        f"{sha}:rules.toml:generic-api-key:2",
        f"{sha}:rules.toml:generic-api-key:3",
    )

    assert code == 1
    assert f"{sha}:rules.toml:generic-api-key:1" not in out
    assert f"FAIL: {sha}:rules.toml:generic-api-key:2: only a comment" in out
    assert f"FAIL: {sha}:rules.toml:generic-api-key:3: a quoted value" in out


def test_what_git_does_not_have_or_another_rule_or_form_fails(tmp_path):
    r = repo(tmp_path)
    sha = commit(r, "help.py", f'x = "{SSN}"\n')
    missing = "0" * 40

    code, out = run(
        tmp_path,
        f"{missing}:help.py:us-ssn:1",
        f"{sha}:other.py:us-ssn:1",
        f"{sha}:help.py:us-ssn:9",
        f"{sha}:help.py:aws-access-token:1",
        "help.py:us-ssn:1",
    )

    assert code == 1
    assert out.count("git has no such commit, file or line") == 3
    assert "the rule aws-access-token is not one this check knows how to pass" in out
    assert "'help.py:us-ssn:1' is not commit:file:rule:line" in out


def test_the_projects_ignore_list_is_fingerprints_with_their_reasons():
    passed = check.fingerprints(ROOT / ".gitleaksignore")

    assert passed, "an empty ignore list needs no file"
    assert all(check.FINGERPRINT.match(line) for line in passed)
    assert len(set(passed)) == len(passed)
    assert {check.FINGERPRINT.match(line)["rule"] for line in passed} <= {
        "us-ein",
        "us-ssn",
        "generic-api-key",
    }


def test_the_scans_of_every_commit_check_the_ignore_list_first():
    for workflow in ("security.yml", "daily-scan.yml"):
        text = (ROOT / ".github/workflows" / workflow).read_text()
        scan = 'gitleaks" git --config .gitleaks.toml --redact --no-banner .\n'
        assert text.index("python3 -m tools.check_gitleaks_ignore") < text.index(scan)
