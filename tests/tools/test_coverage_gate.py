"""tools/coverage_gate.py: money floors, the ratchet, and the command line."""

import copy
import json

import pytest

from tools import coverage_gate

PYTHON_XML = """<?xml version="1.0" ?>
<coverage line-rate="0.75">
  <packages><package name="tools"><classes>
    <class filename="tools/money.py">
      <lines>
        <line number="1" hits="1"/>
        <line number="2" hits="1" branch="true" condition-coverage="50% (1/2)"/>
        <line number="3" hits="0"/>
        <line number="4" hits="1"/>
      </lines>
    </class>
  </classes></package></packages>
</coverage>
"""

THRESHOLDS = {
    "coverage": {
        "changed_lines_min": 90.0,
        "money": {
            "statements_min": 95.0,
            "branches_min": 95.0,
            "functions": ["guard"],
            "python_paths": [],
        },
        "baseline": {"python_lines": 75.0, "sql_statements": 100.0, "sql_branches": 50.0},
    }
}


def function(name, hit_raise):
    return {
        "name": name,
        "args": "",
        "source": "x",
        "branch_ratio": 1.0 if hit_raise else 0.5,
        "statements": [
            {
                "stmtid": 0,
                "parent_stmtid": None,
                "parent_note": None,
                "lineno": 1,
                "exec_stmts": 1,
                "stmtname": "IF",
            },
            {
                "stmtid": 1,
                "parent_stmtid": 0,
                "parent_note": "then body",
                "lineno": 2,
                "exec_stmts": 1 if hit_raise else 0,
                "stmtname": "RAISE",
            },
        ],
    }


def test_python_rates_reads_lines_and_branches():
    rates = coverage_gate.python_rates(PYTHON_XML)
    assert rates["tools/money.py"] == (4, 3, 2, 1)
    assert rates["*"] == (4, 3, 2, 1)


def test_floor1_rounds_down_to_a_tenth():
    assert coverage_gate.floor1(97.36) == 97.3
    assert coverage_gate.floor1(97.3) == 97.3


def test_meeting_every_floor_and_baseline_passes():
    thresholds = copy.deepcopy(THRESHOLDS)
    thresholds["coverage"]["baseline"]["sql_branches"] = 100.0
    result = coverage_gate.evaluate([function("guard", True)], PYTHON_XML, thresholds)
    assert result.failures == []


def test_drop_below_baseline_fails():
    thresholds = copy.deepcopy(THRESHOLDS)
    thresholds["coverage"]["baseline"]["python_lines"] = 80.0
    thresholds["coverage"]["baseline"]["sql_branches"] = 100.0
    result = coverage_gate.evaluate([function("guard", True)], PYTHON_XML, thresholds)
    assert any("python_lines coverage dropped" in f for f in result.failures)


def test_rise_without_raising_the_baseline_fails():
    result = coverage_gate.evaluate([function("guard", True)], PYTHON_XML, THRESHOLDS)
    assert any("Raise [coverage.baseline] sql_branches" in f for f in result.failures)


def test_money_module_below_its_floor_fails():
    result = coverage_gate.evaluate([function("guard", False)], PYTHON_XML, THRESHOLDS)
    assert any("money module statements" in f for f in result.failures)
    assert any("money module branches" in f for f in result.failures)


def test_listed_money_function_must_exist():
    thresholds = copy.deepcopy(THRESHOLDS)
    thresholds["coverage"]["money"]["functions"] = ["guard", "gone"]
    result = coverage_gate.evaluate([function("guard", True)], PYTHON_XML, thresholds)
    assert any("money function gone" in f for f in result.failures)


def test_python_money_paths_need_their_floors():
    thresholds = copy.deepcopy(THRESHOLDS)
    thresholds["coverage"]["money"]["python_paths"] = ["tools/money.py", "tools/absent.py"]
    result = coverage_gate.evaluate([function("guard", True)], PYTHON_XML, thresholds)
    assert any("tools/absent.py has no coverage data" in f for f in result.failures)
    assert any("tools/money.py lines 75.00%" in f for f in result.failures)
    assert any("tools/money.py branches 50.00%" in f for f in result.failures)


def test_print_reads_a_threshold(capsys):
    assert coverage_gate.main(["--print", "coverage.changed_lines_min"]) == 0
    assert capsys.readouterr().out.strip() == "90.0"


def test_main_writes_the_sql_report_and_summary(tmp_path, capsys):
    thresholds = tmp_path / "thresholds.toml"
    thresholds.write_text(
        "[coverage]\nchanged_lines_min = 90.0\n"
        "[coverage.money]\nstatements_min = 95.0\nbranches_min = 95.0\n"
        'functions = ["guard"]\npython_paths = []\n'
        "[coverage.baseline]\npython_lines = 75.0\nsql_statements = 100.0\nsql_branches = 100.0\n"
    )
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    raw = tmp_path / "sql-raw.json"
    raw.write_text(json.dumps([function("guard", True)]))
    python_xml = tmp_path / "python.xml"
    python_xml.write_text(PYTHON_XML)
    summary = tmp_path / "summary.md"
    out = tmp_path / "out/sql.xml"

    args = [
        "--thresholds",
        str(thresholds),
        "--migrations",
        str(migrations),
        "--sql-raw",
        str(raw),
        "--python-xml",
        str(python_xml),
        "--sql-xml-out",
        str(out),
        "--summary",
        str(summary),
    ]
    assert coverage_gate.main(args) == 0
    assert out.exists()
    assert "## Coverage" in summary.read_text()

    raw.write_text(json.dumps([function("guard", False)]))
    assert coverage_gate.main(args) == 1
    assert "### Failed" in capsys.readouterr().out


def test_main_needs_its_inputs(capsys):
    with pytest.raises(SystemExit) as exit_:
        coverage_gate.main([])
    assert exit_.value.code == 2
    assert "required" in capsys.readouterr().err
