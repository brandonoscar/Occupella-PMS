"""The score of Python mutation testing (tools/python_mutation.py): caught over all mutants
scored, a mutant no test reaches counting as not caught, against the floor in
ci/thresholds.toml."""

import json
import tomllib
from pathlib import Path

from tools import python_mutation

ROOT = Path(__file__).resolve().parents[2]


def stats(**changes):
    base = {
        "killed": 70,
        "survived": 20,
        "total": 100,
        "no_tests": 8,
        "skipped": 0,
        "suspicious": 0,
        "timeout": 2,
        "check_was_interrupted_by_user": 0,
        "segfault": 0,
    }
    return {**base, **changes}


def test_a_mutant_no_test_reaches_counts_as_not_caught():
    assert python_mutation.score(stats()) == (72.0, 72, 100)
    assert python_mutation.score(stats(skipped=10)) == (80.0, 72, 90)
    assert python_mutation.score(stats(total=0, killed=0, timeout=0)) == (100.0, 0, 0)


def test_a_score_below_the_baseline_fails_and_lists_what_survived():
    text, ok = python_mutation.report(stats(), 72.5, "pms.x.f__mutmut_1: survived\n")

    assert not ok
    assert "Score: **72.0%** (72 of 100 caught: 70 killed, 2 hung; 20 survived, 8" in text
    assert "pms.x.f__mutmut_1: survived" in text
    assert "FAIL: score 72.0% is below the baseline 72.5%." in text


def test_a_score_above_the_baseline_asks_for_the_floor_to_rise():
    text, ok = python_mutation.report(stats(), 70.0, "")

    assert ok
    assert "Not caught" not in text
    assert "Raise [mutation] python_baseline_score in ci/thresholds.toml to 72.0" in text
    level, ok = python_mutation.report(stats(), 72.0, "")
    assert ok and "Raise" not in level and "FAIL" not in level


def test_a_run_that_crashed_scores_nothing():
    for broken in (stats(segfault=1), stats(check_was_interrupted_by_user=1)):
        text, ok = python_mutation.report(broken, 0.0, "")
        assert not ok
        assert "crashed or was interrupted" in text


def test_the_command_reads_the_floor_and_writes_the_summary(tmp_path, capsys):
    floor = tomllib.loads((ROOT / "ci/thresholds.toml").read_text())["mutation"]
    caught = int(floor["python_baseline_score"]) + 1
    path = tmp_path / "stats.json"
    path.write_text(json.dumps(stats(killed=caught, timeout=0, survived=100 - caught)))
    survivors = tmp_path / "survivors.txt"
    survivors.write_text("pms.y.g__mutmut_2: survived\n")
    summary = tmp_path / "summary.md"

    assert (
        python_mutation.main([str(path), "--survivors", str(survivors), "--summary", str(summary)])
        == 0
    )
    assert "pms.y.g__mutmut_2" in summary.read_text()
    assert "## Python mutation testing" in capsys.readouterr().out

    path.write_text(json.dumps(stats(killed=0, timeout=0, survived=100)))
    assert python_mutation.main([str(path)]) == 1
