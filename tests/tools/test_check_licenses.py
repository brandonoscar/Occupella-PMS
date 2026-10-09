"""tools/check_licenses.py: only allowlisted licenses, never non-commercial or source-available."""

import pytest

from tools import check_licenses

POLICY = {
    "allowed": ["MIT", "Apache-2.0", "BSD-3-Clause", "LGPL-3.0-only"],
    "denied_patterns": ["SSPL", "BUSL", "NC", "Commons Clause"],
    "aliases": {"MIT License": "MIT", "Apache Software License": "Apache-2.0"},
    "packages": {"oddball": {"license": "MIT", "reason": "metadata says UNKNOWN"}},
}


def test_unwrap_and_alternatives():
    assert check_licenses.unwrap(" ((MIT)) ") == "MIT"
    assert check_licenses.alternatives("(Apache-2.0 OR MIT)") == [["Apache-2.0"], ["MIT"]]
    assert check_licenses.alternatives("A; B") == [["A"], ["B"]]
    assert check_licenses.alternatives("MIT AND BSD-3-Clause") == [["MIT", "BSD-3-Clause"]]
    assert check_licenses.alternatives("Mozilla Public License 2.0 (MPL 2.0)") == [
        ["Mozilla Public License 2.0 (MPL 2.0)"]
    ]


@pytest.mark.parametrize(
    "license_text",
    [
        "MIT",
        "MIT License",
        "Apache Software License; MIT License",
        "GPL-3.0-only OR MIT",
        "MIT AND BSD-3-Clause",
        "LGPL-3.0-only",
    ],
)
def test_allowed(license_text):
    assert check_licenses.verdict("pkg", license_text, POLICY) is None


@pytest.mark.parametrize(
    "license_text, reason",
    [
        ("SSPL-1.0", "denied (SSPL)"),
        ("BUSL-1.1", "denied (BUSL)"),
        ("CC-BY-NC-4.0", "denied (NC)"),
        ("Apache-2.0 with Commons Clause", "denied (Commons Clause)"),
        ("GPL-3.0-only", "not on the allowlist"),
        ("MIT AND GPL-3.0-only", "not on the allowlist"),
        ("UNKNOWN", "not on the allowlist"),
    ],
)
def test_refused(license_text, reason):
    assert reason in check_licenses.verdict("pkg", license_text, POLICY)


def test_override_records_the_real_license():
    assert check_licenses.verdict("oddball", "UNKNOWN", POLICY) is None


def test_main_reports_each_problem(tmp_path, capsys):
    policy = tmp_path / "licenses.toml"
    policy.write_text('allowed = ["MIT"]\n')
    packages = [
        {"Name": "good", "Version": "1", "License": "MIT"},
        {"Name": "bad", "Version": "2", "License": "SSPL-1.0"},
    ]
    assert check_licenses.main(["--policy", str(policy)], packages) == 1
    assert "FAIL: bad: license 'SSPL-1.0' is not on the allowlist" in capsys.readouterr().out
    assert check_licenses.main(["--policy", str(policy)], packages[:1]) == 0


def test_installed_packages_pass_the_real_policy(capsys):
    assert check_licenses.main([]) == 0
    assert "every license allowed" in capsys.readouterr().out
