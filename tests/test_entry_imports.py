"""Each command the server runs (python -m ...) imports in a fresh interpreter, whatever order its
modules load in. The test run imports the API's modules in another order, so an import cycle
among them (a module reaching, through others, one only half loaded) shows only here."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
COMMANDS = [
    "pms.api",
    "pms.nightly",
    "pms.demo",
    "pms.admin",
    "pms.email",
    "pms.exports",
    "pms.sealed",
]


@pytest.mark.parametrize("module", COMMANDS)
def test_a_command_imports_on_its_own(module):
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    done = subprocess.run(  # noqa: S603 (this interpreter, a fixed module name)
        [sys.executable, "-c", f"import {module}"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
