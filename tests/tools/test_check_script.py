"""scripts/check.sh runs every command the PR workflows run, so it can't fall behind CI.

Reads both sides: each tool, script and linter a pull_request workflow invokes in a `run:` step
must be invoked by check.sh too (comment lines don't count).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = [
    re.compile(r"-m (tools\.\w+)"),  # python -m, coverage run -m
    re.compile(r"python (scripts/[\w.]+)"),
    re.compile(r"(scripts/[\w.]+\.sh)\b"),
    re.compile(r"\b(ruff check|ruff format --check|mypy|squawk|shellcheck|diff-cover|pytest)\b"),
]


def code_lines(text):
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def run_steps(workflow):
    """The commands in a workflow's run steps, block scalars included."""
    lines, inside = [], None
    for line in code_lines(workflow):
        indent = len(line) - len(line.lstrip())
        if inside is not None and line.strip() and indent <= inside:
            inside = None
        if inside is not None:
            lines.append(line)
        match = re.match(r"(\s*)(- )?run: ?(.*)$", line)
        if match:
            rest = match.group(3)
            if rest in ("|", ">", "|-", ">-"):
                inside = len(match.group(1))
            else:
                lines.append(rest)
    return "\n".join(lines)


def invoked(text):
    return {found for pattern in COMMANDS for found in pattern.findall(text)}


def pull_request_workflows():
    return [
        path
        for path in sorted((ROOT / ".github/workflows").glob("*.yml"))
        if re.search(r"^  pull_request:", path.read_text(), re.MULTILINE)
    ]


def test_run_steps_reads_inline_and_block_commands():
    workflow = """
jobs:
  x:
    steps:
      - run: ruff check .
      - name: y
        run: |
          python -m tools.check_a --base "$X"
          coverage run -a -m tools.check_c
          python scripts/b.py
      - uses: some/action@abc
        with:
          args: mypy
"""
    assert invoked(run_steps(workflow)) == {
        "ruff check",
        "tools.check_a",
        "tools.check_c",
        "scripts/b.py",
    }


def test_check_script_runs_everything_the_pr_workflows_run():
    script = invoked("\n".join(code_lines((ROOT / "scripts/check.sh").read_text())))
    workflows = pull_request_workflows()
    assert {path.name for path in workflows} >= {"ci.yml", "db.yml", "coverage.yml"}

    in_ci = set()
    for path in workflows:
        in_ci |= invoked(run_steps(path.read_text()))

    assert in_ci, "found no commands in the workflows; the parser is wrong"
    assert in_ci - script == set(), "add these to scripts/check.sh"
