"""Upgrade check from many earlier points of main: a database holding money, made and filled by
an old version, upgrades to this branch and keeps its money.

A company that self-hosts may upgrade from a version months old, skipping many merges, while
db.yml's upgrade check (tools/check_upgrade.py) starts only from the PR's base. This runs that same
check, unchanged, from a sample of earlier points of main, each night:

  - a point is a commit on main's first-parent line (each merge or squash merge as it landed)
    that changed db/migrations, up to where this branch left main; the tip itself is left out,
    as an upgrade from it applies nothing;
  - points are taken from the oldest whose tree has the self-host seed the check fills the old
    schema with (selfhost/smoke.py). The ones before it predate the self-host build, so no
    company that self-hosts can be on one, and the run says how many it left out;
  - each night checks the oldest of them, the newest that landed by 7, 30 and 90 days before the
    night (UTC), and one in N of all of them, the kth from the oldest and every Nth after it,
    where k is the night (days since 1970) mod N and N is as small as keeps the night within
    its cap (ci/thresholds.toml [upgrade_history] nightly_bases). So, while N stays the same,
    every point is checked once in N nights;
  - a failure from any point fails the run, which prints that upgrade's output and names the
    point; the other points still run.

usage: python -m tools.check_upgrade_history [--main origin/main] [--bases N] [--today YYYY-MM-DD]
                                             [--list]
       (uses $DATABASE_URL and dbmate on PATH, as tools/check_upgrade.py does)
"""

from __future__ import annotations

import argparse
import contextlib
import io
import math
import os
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from tools import check_upgrade
from tools.check_upgrade import MIGRATIONS, SEED, git

ROOT = Path(__file__).resolve().parent.parent
THRESHOLDS = "ci/thresholds.toml"
DAYS_BACK = (7, 30, 90)
EPOCH = date(1970, 1, 1)


@dataclass(frozen=True)
class Point:
    """A commit on main's first-parent line that changed the migrations."""

    sha: str
    landed: datetime  # its committer time: when it landed on main
    subject: str
    seeded: bool  # its tree has the self-host seed

    def label(self) -> str:
        return f"{self.sha[:12]} ({self.landed:%Y-%m-%d}, {self.subject})"


def points(root: Path, main: str, head: str = "HEAD") -> list[Point]:
    """Every point of `main` up to where `head` left it, `head` itself left out; oldest first."""
    fork = git(root, "merge-base", head, main).strip()
    tip = git(root, "rev-parse", head).strip()
    log = git(root, "log", "--first-parent", "--format=%H%x00%ct%x00%s", fork, "--", MIGRATIONS)
    rows = [line.split("\0") for line in log.splitlines() if line]
    rows = [row for row in rows if row[0] != tip]
    if not rows:
        return []
    asked = "".join(f"{sha}:{SEED}\n" for sha, _, _ in rows)
    answers = subprocess.run(
        ["git", "cat-file", "--batch-check"],
        cwd=root,
        input=asked,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [
        Point(
            sha, datetime.fromtimestamp(int(landed), UTC), subject, not answer.endswith(" missing")
        )
        for (sha, landed, subject), answer in zip(reversed(rows), reversed(answers), strict=True)
    ]


def seedable(found: list[Point]) -> tuple[list[Point], list[Point]]:
    """The points from the oldest that has the seed and no point after it without one, and the
    points before those."""
    cut = max((i + 1 for i, point in enumerate(found) if not point.seeded), default=0)
    return found[cut:], found[:cut]


def select(found: list[Point], today: date, cap: int) -> list[tuple[Point, str]]:
    """Tonight's points of `found` (oldest first), at most `cap`, each with why it was picked."""
    if not found:
        return []
    why: dict[Point, list[str]] = {found[0]: ["the oldest"]}
    for days in DAYS_BACK:
        # Landed by the end of the day `days` before tonight (UTC).
        by = datetime(today.year, today.month, today.day, tzinfo=UTC) - timedelta(days=days - 1)
        landed = [point for point in found if point.landed < by]
        if landed:
            why.setdefault(landed[-1], []).append(f"{days} days back")
        else:
            why[found[0]].append(f"{days} days back (main is younger)")
    picked = dict(list(why.items())[:cap])
    room = cap - len(picked)
    if room > 0:
        step = math.ceil(len(found) / room)
        night = (today - EPOCH).days % step
        reason = "every point" if step == 1 else f"one in {step}, night {night + 1} of {step}"
        for point in found[night::step]:
            picked.setdefault(point, []).append(reason)
    return [(point, "; ".join(picked[point])) for point in found if point in picked]


def nightly_bases(root: Path) -> int:
    return int(tomllib.loads((root / THRESHOLDS).read_text())["upgrade_history"]["nightly_bases"])


def check_all(
    root: Path, picks: list[tuple[Point, str]], server_url: str, dbmate: str
) -> list[Point]:
    """Run the upgrade check from each point; the points it failed from."""
    failed = []
    for point, _ in picks:
        started = time.monotonic()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            try:
                problems = check_upgrade.check(root, point.sha, server_url, dbmate)
            except Exception as error:  # one point's crash fails that point, not the others
                problems = [f"the check stopped: {type(error).__name__}: {error}"]
        took = time.monotonic() - started
        if problems:
            print(output.getvalue(), end="")
            for problem in problems:
                print(f"FAIL: from {point.label()}: {problem}")
            failed.append(point)
        else:
            print(f"ok    from {point.label()}, {took:.1f} s")
    return failed


def positive(text: str) -> int:
    number = int(text)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--main", default="origin/main", help="main's ref (origin/main)")
    parser.add_argument(
        "--bases",
        type=positive,
        help=f"points to check tonight (default: {THRESHOLDS} [upgrade_history] nightly_bases)",
    )
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        help="the night to pick points for, YYYY-MM-DD (default: today, UTC), to repeat a run",
    )
    parser.add_argument("--list", action="store_true", help="say which points, check none")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    cap = args.bases or nightly_bases(args.root)
    today = args.today or datetime.now(UTC).date()

    found, before = seedable(points(args.root, args.main))
    picks = select(found, today, cap)
    if not found:
        print(f"FAIL: no earlier point of {args.main} has {SEED}, so the check proves nothing.")
        return 1
    print(
        f"Earlier points of {args.main} that changed the migrations, from the oldest that has "
        f"{SEED}: {len(found)}, the oldest {found[0].label()}."
    )
    if before:
        print(f"Not checked, as older than that: {len(before)}, the oldest {before[0].label()}.")
    print(
        f"Tonight ({today}, at most {cap}): {len(picks)}. "
        f"Repeat it with --today {today} --bases {cap}."
    )
    for point, why in picks:
        print(f"  {point.label()}: {why}")
    if args.list:
        return 0

    url = os.environ.get("DATABASE_URL")
    dbmate = shutil.which("dbmate")
    if not url or not dbmate:
        print("FAIL: needs DATABASE_URL and dbmate on PATH.")
        return 1
    started = time.monotonic()
    failed = check_all(args.root, picks, url, dbmate)
    took = time.monotonic() - started
    if failed:
        print(
            f"FAIL: {len(failed)} of {len(picks)} earlier points of main did not upgrade to this "
            f"branch with their money intact: {', '.join(point.sha[:12] for point in failed)}."
        )
        return 1
    print(
        f"ok: from every earlier point of main checked ({len(picks)}), a database holding money "
        f"upgrades to this branch with its money intact ({took:.0f} s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
