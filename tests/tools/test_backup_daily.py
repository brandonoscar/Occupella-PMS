"""scripts/backup-daily.sh, the self-host install's daily backup (compose.selfhost.yaml).

Each test runs the script for real, through the real scripts/backup.sh, against stand-ins for
pg_dump and pg_restore 16 put first on PATH: what is tested here is the script's own part (the
file's name, its mode, what is kept and what goes, a failed dump). That a real dump of the
install's database restores and ties out is selfhost.yml's install step, with Docker.

Days are taken from the dump the script made, never from this test's clock, and the old dumps
sit days away from each limit, so a run crossing midnight UTC reads the same.
"""

import os
import stat
import subprocess
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/backup-daily.sh"

PG_DUMP = """#!/usr/bin/env bash
if [ "$1" = --version ]; then echo "pg_dump (PostgreSQL) 16.9"; exit 0; fi
for arg in "$@"; do case "$arg" in --file=*) out=${arg#--file=} ;; esac; done
echo "a dump of $PGDATABASE" > "$out"
"""
PG_RESTORE = """#!/usr/bin/env bash
if [ "$1" = --version ]; then echo "pg_restore (PostgreSQL) 16.9"; exit 0; fi
[ -z "$FAKE_UNREADABLE" ] && grep -q "^a dump of" "$2"
"""


def run(tmp_path: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, text in (("pg_dump", PG_DUMP), ("pg_restore", PG_RESTORE)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    return subprocess.run(
        [str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "PGDATABASE": "pms",
            "PMS_BACKUP_DIR": str(tmp_path / "backups"),
            **env,
        },
    )


def dumps(where: Path) -> list[str]:
    return sorted(path.name for path in where.iterdir())


def made_today(where: Path) -> date:
    """The day the script dated its dump: the newest name."""
    return date.fromisoformat(dumps(where)[-1].removeprefix("pms-").removesuffix(".dump"))


def test_once_dumps_today_readable_by_its_owner_only(tmp_path):
    done = run(tmp_path, "once")

    assert done.returncode == 0, done.stderr
    backups = tmp_path / "backups"
    (name,) = dumps(backups)
    assert name == f"pms-{made_today(backups).isoformat()}.dump"
    assert (backups / name).read_text() == "a dump of pms\n"
    assert stat.S_IMODE((backups / name).stat().st_mode) == 0o600
    assert stat.S_IMODE(backups.stat().st_mode) == 0o700


def test_once_keeps_35_days_and_every_months_first(tmp_path):
    backups = tmp_path / "backups"
    run(tmp_path, "once")
    today = made_today(backups)
    (backups / f"pms-{today.isoformat()}.dump").unlink()

    def day(days_ago: int) -> date:
        return today - timedelta(days=days_ago)

    old_first = (today.replace(day=1) - timedelta(days=70)).replace(day=1)
    recent, past = day(30), day(40)
    if past.day == 1:  # a month's first is kept for good: take the day after
        past = day(41)
    for kept in (recent, past, old_first):
        (backups / f"pms-{kept.isoformat()}.dump").write_text("old")
    (backups / f"pms-{day(2).isoformat()}.dump.partial").write_text("half")
    (backups / "notes.txt").write_text("not a dump")

    done = run(tmp_path, "once")

    assert done.returncode == 0, done.stderr
    assert dumps(backups) == sorted(
        [
            f"pms-{old_first.isoformat()}.dump",
            f"pms-{recent.isoformat()}.dump",
            f"pms-{today.isoformat()}.dump",
            "notes.txt",
        ]
    )
    assert f"removed: {backups}/pms-{past.isoformat()}.dump" in done.stderr


def test_a_dump_that_cant_be_read_back_fails_and_removes_nothing(tmp_path):
    backups = tmp_path / "backups"
    run(tmp_path, "once")
    today = made_today(backups)
    ancient = today - timedelta(days=90)
    if ancient.day == 1:
        ancient -= timedelta(days=1)
    (backups / f"pms-{ancient.isoformat()}.dump").write_text("old")
    (backups / f"pms-{today.isoformat()}.dump").unlink()

    done = run(tmp_path, "once", FAKE_UNREADABLE="1")

    assert done.returncode != 0
    assert dumps(backups) == [
        f"pms-{ancient.isoformat()}.dump",
        f"pms-{today.isoformat()}.dump.partial",
    ]


def test_the_next_dump_is_within_a_day(tmp_path):
    for at in ("00:00", "07:00", "23:59"):
        done = run(tmp_path, "next", PMS_BACKUP_AT=at)

        assert done.returncode == 0, done.stderr
        assert 0 < int(done.stdout) <= 24 * 60 * 60


def test_a_time_of_day_that_isnt_hh_mm_is_refused(tmp_path):
    for at in ("7:00", "24:00", "07:60", "seven"):
        done = run(tmp_path, "next", PMS_BACKUP_AT=at)

        assert done.returncode == 2, at
        assert "PMS_BACKUP_AT is a time of day in UTC" in done.stderr


def test_the_install_runs_it_each_day_into_a_folder_git_ignores():
    compose = (ROOT / "compose.selfhost.yaml").read_text()

    assert '["bash", "/scripts/backup-daily.sh"]' in compose
    assert "./scripts:/scripts:ro" in compose
    assert "/backups/" in (ROOT / ".gitignore").read_text().splitlines()
