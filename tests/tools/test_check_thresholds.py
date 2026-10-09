"""tools/check_thresholds.py: a PR may raise a floor, never lower or drop one."""

from tools import check_thresholds

BASE = {
    "coverage": {
        "changed_lines_min": 90.0,
        "money": {"functions": ["kept", "gone"], "python_paths": ["tools/old.py"]},
    },
    "properties": {"pr_examples": 200, "label": "text is ignored", "strict": True},
}


def head(**changes):
    copy = {
        "coverage": {
            "changed_lines_min": 90.0,
            "money": {"functions": ["kept", "gone"], "python_paths": ["tools/old.py"]},
        },
        "properties": {"pr_examples": 200, "label": "other text", "strict": False},
    }
    for dotted, value in changes.items():
        table = copy
        *parents, last = dotted.split("__")
        for part in parents:
            table = table[part]
        if value is None:
            del table[last]
        else:
            table[last] = value
    return copy


def schema(tmp_path, functions):
    (tmp_path / "db").mkdir(exist_ok=True)
    (tmp_path / "db/schema.sql").write_text(
        "".join(f"CREATE FUNCTION public.{name}() ...\n" for name in functions)
    )


def test_flatten_uses_dotted_keys():
    assert check_thresholds.flatten({"a": {"b": 1}, "c": 2}) == {"a.b": 1, "c": 2}


def test_unchanged_or_raised_passes(tmp_path):
    schema(tmp_path, ["kept", "gone"])
    assert check_thresholds.compare(BASE, head(), tmp_path) == []
    assert check_thresholds.compare(BASE, head(properties__pr_examples=500), tmp_path) == []


def test_lowered_number_fails(tmp_path):
    schema(tmp_path, ["kept", "gone"])
    problems = check_thresholds.compare(BASE, head(coverage__changed_lines_min=80.0), tmp_path)
    assert problems == ["coverage.changed_lines_min was lowered from 90.0 to 80.0."]


def test_number_replaced_by_text_fails(tmp_path):
    schema(tmp_path, ["kept", "gone"])
    problems = check_thresholds.compare(BASE, head(properties__pr_examples="many"), tmp_path)
    assert "properties.pr_examples was lowered" in problems[0]


def test_removed_key_fails(tmp_path):
    schema(tmp_path, ["kept", "gone"])
    problems = check_thresholds.compare(BASE, head(properties__pr_examples=None), tmp_path)
    assert problems == ["properties.pr_examples was removed from ci/thresholds.toml."]


def test_money_function_leaves_only_when_dropped_from_the_schema(tmp_path):
    shrunk = head(coverage__money__functions=["kept"])
    schema(tmp_path, ["kept", "gone"])
    assert check_thresholds.compare(BASE, shrunk, tmp_path) == [
        "coverage.money.functions lost 'gone', which still exists."
    ]
    schema(tmp_path, ["kept"])
    assert check_thresholds.compare(BASE, shrunk, tmp_path) == []


def test_python_path_leaves_only_when_the_file_is_gone(tmp_path):
    schema(tmp_path, ["kept", "gone"])
    shrunk = head(coverage__money__python_paths=[])
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/old.py").write_text("")
    assert len(check_thresholds.compare(BASE, shrunk, tmp_path)) == 1
    (tmp_path / "tools/old.py").unlink()
    assert check_thresholds.compare(BASE, shrunk, tmp_path) == []


def test_other_lists_never_lose_items(tmp_path):
    base = {"mutation": {"equivalent": ["a", "b"]}}
    assert check_thresholds.removable("mutation.equivalent", "a", tmp_path) is False
    assert check_thresholds.compare(base, {"mutation": {"equivalent": ["a"]}}, tmp_path) == [
        "mutation.equivalent lost 'b', which still exists."
    ]


def test_main_compares_with_the_base_commit(repo, capsys):
    repo.write("db/schema.sql", "")
    repo.write("ci/thresholds.toml", "[coverage]\nchanged_lines_min = 90.0\n")
    base = repo.commit("base")

    repo.write("ci/thresholds.toml", "[coverage]\nchanged_lines_min = 95.0\n")
    assert check_thresholds.main(["--base", base, "--root", str(repo.path)]) == 0
    assert "ok:" in capsys.readouterr().out

    repo.write("ci/thresholds.toml", "[coverage]\nchanged_lines_min = 85.0\n")
    assert check_thresholds.main(["--base", base, "--root", str(repo.path)]) == 1
    assert "FAIL: coverage.changed_lines_min was lowered" in capsys.readouterr().out


def test_main_passes_when_the_base_has_no_thresholds_file(repo, capsys):
    base = repo.commit("empty")
    repo.write("ci/thresholds.toml", "[coverage]\nchanged_lines_min = 90.0\n")
    assert check_thresholds.main(["--base", base, "--root", str(repo.path)]) == 0
    assert "does not exist" in capsys.readouterr().out
