"""The ZAP scan (zap.yml): the session it scans as and the schema it attacks
(tools/zap_session.py), and how its findings are judged (tools/zap_alerts.py)."""

import json
import re
import stat
from pathlib import Path

import httpx2
import pytest

from tools import speed, zap_alerts, zap_session

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github/workflows/zap.yml").read_text()


@pytest.mark.timing
def test_the_scan_signs_in_as_staff_and_gets_every_operation_but_its_own_sign_out(
    database_url, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("DATABASE_URL", database_url)
    cookie, schema = tmp_path / "cookie", tmp_path / "openapi.json"
    with speed.serving(database_url) as server:
        assert (
            zap_session.main(
                [
                    "--origin",
                    server.origin,
                    "--cookie-file",
                    str(cookie),
                    "--schema-file",
                    str(schema),
                ]
            )
            == 0
        )
        # Only its owner may read the session, and it is a staff member's of the demo PMC.
        assert stat.S_IMODE(cookie.stat().st_mode) == 0o600
        header = cookie.read_text()
        assert header.startswith("pms_session=") and "\n" not in header
        me = httpx2.get(f"{server.origin}/api/me", headers={"Cookie": header})
        assert me.status_code == 200
        assert me.json()["kind"] == "staff"

        made = json.loads(schema.read_text())
        assert made["servers"] == [{"url": server.origin}]
        assert "post" not in made["paths"].get("/api/sign-out", {})
        assert "post" not in made["paths"].get("/api/logins/{login_id}/disable", {})
        operations = sum(len(ops) for ops in made["paths"].values())
        assert operations > 200
        assert f"{operations} operations" in capsys.readouterr().out

        # An operation left out that the app no longer has is said, not silently skipped.
        monkeypatch.setitem(zap_session.LEFT_OUT, ("post", "/api/no-such-thing"), "gone")
        with pytest.raises(RuntimeError, match="isn't in the schema"):
            zap_session.schema(server.origin)


@pytest.mark.timing
def test_on_its_own_the_session_tool_migrates_serves_and_signs_in(
    database_url, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("DATABASE_URL", database_url)
    files = ["--cookie-file", str(tmp_path / "c"), "--schema-file", str(tmp_path / "s")]

    assert zap_session.main(files) == 0
    assert "signed in to the demo PMC at a throwaway database" in capsys.readouterr().out

    monkeypatch.setattr(zap_session.speed, "MIGRATIONS", tmp_path / "none")
    (tmp_path / "none").mkdir()
    (tmp_path / "none" / "20260101000000_broken.sql").write_text(
        "-- migrate:up\nSELECT no_such_function();\n-- migrate:down\n"
    )
    assert zap_session.main(files) == 1
    assert "FAIL: dbmate up failed" in capsys.readouterr().out


def test_the_session_tool_needs_a_database(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    args = ["--origin", "http://127.0.0.1:1", "--cookie-file", str(tmp_path / "c")]

    assert zap_session.main([*args, "--schema-file", str(tmp_path / "s")]) == 1
    assert "needs DATABASE_URL" in capsys.readouterr().out


def test_a_private_file_is_its_owners_alone_even_if_it_was_there(tmp_path):
    path = tmp_path / "cookie"
    path.write_text("old")
    path.chmod(0o644)

    zap_session.write_private(path, "pms_session=x")

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text() == "pms_session=x"


def report(*alerts):
    return {
        "site": [
            {
                "@name": "http://127.0.0.1:8123",
                "alerts": [
                    {
                        "pluginid": rule,
                        "name": name,
                        "riskcode": str(risk),
                        "instances": [
                            {"uri": f"http://127.0.0.1:8123/{n}", "method": "GET"} for n in range(5)
                        ],
                    }
                    for rule, name, risk in alerts
                ],
            }
        ]
    }


def test_low_risk_and_up_fails_unless_ignored_with_why_and_informational_is_listed():
    found = zap_alerts.alerts(
        report(
            ("10109", "Modern Web Application", 0),
            ("10038", "Content Security Policy Header Not Set", 2),
            ("40018", "SQL Injection", 3),
            ("10049", "Storable and Cacheable Content", 1),
        )
    )

    failing, listed = zap_alerts.judge(found, {"10049": "every answer says no-store"})

    assert [line.split(":")[0] for line in failing] == [
        "High [40018] SQL Injection",
        "Medium [10038] Content Security Policy Header Not Set",
    ]
    # The first three places each was seen.
    assert failing[0].endswith("GET http://127.0.0.1:8123/2")
    assert (
        listed[0].startswith("Low [10049]") and "(ignored: every answer says no-store)" in listed[0]
    )
    assert listed[1].startswith("Informational [10109]")


def test_an_ignored_rule_needs_its_reason(tmp_path):
    path = tmp_path / "ignore.toml"
    path.write_text('[[ignore]]\nrule = "10049"\nreason = "  "\n')
    with pytest.raises(ValueError, match="needs its rule and a reason"):
        zap_alerts.ignored(path)

    path.write_text('[[ignore]]\nrule = "10049"\nreason = "no-store everywhere"\n')
    assert zap_alerts.ignored(path) == {"10049": "no-store everywhere"}
    # The repo's own: only ZAP's report on its own version, about the scanner, not the PMS.
    assert list(zap_alerts.ignored()) == ["10116"]


def test_the_judge_fails_on_what_isnt_ignored(tmp_path, monkeypatch, capsys):
    clean, dirty = tmp_path / "clean.json", tmp_path / "dirty.json"
    clean.write_text(json.dumps(report(("10109", "Modern Web Application", 0))))
    dirty.write_text(json.dumps(report(("10020", "Missing Anti-clickjacking Header", 2))))

    assert zap_alerts.main([str(clean)]) == 0
    assert "ok: 1 alerts" in capsys.readouterr().out
    assert zap_alerts.main([str(clean), str(dirty)]) == 1
    assert "FAIL: Medium [10020]" in capsys.readouterr().out
    assert zap_alerts.main([]) == 2
    assert zap_alerts.main(["--check"]) == 0
    assert "ok: 1 rules ignored" in capsys.readouterr().out

    bad = tmp_path / "ignore.toml"
    bad.write_text('[[ignore]]\nrule = "10020"\n')
    monkeypatch.setattr(zap_alerts, "IGNORES", bad)
    assert zap_alerts.main(["--check"]) == 1
    assert "needs its rule and a reason" in capsys.readouterr().out


def test_the_workflow_pins_its_images_and_never_puts_the_session_on_a_command_line():
    for image in re.findall(r"^  [A-Z]+_IMAGE: (\S+)$", WORKFLOW, re.M):
        assert re.search(r":[\w.-]+@sha256:[0-9a-f]{64}$", image), image
    # The cookie reaches ZAP only through the environment: `-e NAME` with no value.
    assert "-e ZAP_AUTH_HEADER_VALUE " in WORKFLOW
    assert not re.search(r"-e ZAP_AUTH_HEADER_VALUE=", WORKFLOW)
    assert not re.search(r"zap-api-scan\.py[^\n]*cookie", WORKFLOW, re.I)
    # A PR that changes the scan or its tools runs it.
    for path in ("tools/zap_session.py", "tools/zap_alerts.py", "ci/zap-ignore.toml"):
        assert f"      - {path}\n" in WORKFLOW
    assert "pull_request_target" not in WORKFLOW


def test_the_pms_keeps_bank_numbers_and_tax_ids_in_both_scans_as_in_e2e():
    # Without a key the PMS keeps no account number or tax ID, so their pages show none and
    # their routes refuse: each scan's server gets a throwaway one, made as e2e.yml makes it.
    made = (
        "PMS_DATA_KEY=\"$(python -c 'import base64, os;"
        " print(base64.b64encode(os.urandom(32)).decode())')\""
    )
    assert made in (ROOT / ".github/workflows/e2e.yml").read_text()
    serves = WORKFLOW.split("nohup python -m pms.api")[:-1]
    assert len(serves) == 2
    for before in serves:
        step = before[before.rindex("- name:") :]
        assert made in step and "export PMS_DATA_KEY" in step
