"""scripts/backup_daily.sh, compose.selfhost.yaml's `backup` service: each day's backup is
scripts/backup.sh dump under its day's name, and only the newest PMS_BACKUP_KEEP are kept."""

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools import check_backup
from tools.check_rollback import Dbmate
from tools.scratch_db import scratch_database

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/backup_daily.sh"


def run(args, env):
    return subprocess.run([str(SCRIPT), *args], env=env, capture_output=True, text=True)


def test_a_backup_now_is_kept_under_its_day_and_only_the_newest_are_kept(database_url, tmp_path):
    for day in ("2026-01-01", "2026-01-02", "2026-01-03"):
        (tmp_path / f"pms-{day}.dump").write_bytes(b"an older backup")
    (tmp_path / "notes.txt").write_text("not a backup: never deleted")
    with scratch_database(database_url, "pms_backup_daily") as url:
        built = Dbmate(shutil.which("dbmate"), ROOT / "db/migrations", url).run(
            "--no-dump-schema", "up"
        )
        assert built.returncode == 0, built.stderr
        env = check_backup.libpq_env(url) | {"PMS_BACKUP_KEEP": "2"}

        made = run([str(tmp_path), "--now"], env)

    assert made.returncode == 0, made.stderr
    today = tmp_path / f"pms-{datetime.now(UTC):%Y-%m-%d}.dump"
    # The newest two: today's, which pg_restore reads back, and the latest before it.
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [today.name, "pms-2026-01-03.dump", "notes.txt"]
    )
    listed = subprocess.run(["pg_restore", "--list", str(today)], capture_output=True, text=True)
    assert listed.returncode == 0 and "trust_tenants" in listed.stdout
    assert f"backed up: {today}" in made.stderr
    assert "pms-2026-01-01.dump, past the newest 2" in made.stderr
    assert not any(p.name.endswith(".partial") for p in tmp_path.iterdir())


@pytest.mark.parametrize(
    ("args", "keep", "says"),
    [
        ([], "14", "usage:"),
        (["DIR", "--later"], "14", "usage:"),
        (["DIR", "--now", "more"], "14", "usage:"),
        (["DIR", "--now"], "0", "1 or more"),
        (["DIR", "--now"], "two", "1 or more"),
    ],
)
def test_bad_arguments_are_refused_in_words_before_anything_runs(tmp_path, args, keep, says):
    env = dict(os.environ) | {"PMS_BACKUP_KEEP": keep}
    args = [str(tmp_path / "out") if a == "DIR" else a for a in args]

    refused = run(args, env)

    assert refused.returncode == 2
    assert says in refused.stderr
    assert not (tmp_path / "out").exists()
