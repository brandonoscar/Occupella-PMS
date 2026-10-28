"""tools/check_upgrade_history.py: a database holding money upgrades from many earlier points of
main, not just the PR's base."""

import tomllib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tools import check_upgrade, check_upgrade_history
from tools.check_upgrade_history import Point, main, points, seedable, select

ROOT = Path(__file__).resolve().parents[2]
SEED = "selfhost/smoke.py"


def commit_on(repo, monkeypatch, when, message):
    """Commit everything, landing on main at `when` (UTC)."""
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    monkeypatch.setenv("GIT_AUTHOR_DATE", stamp)
    monkeypatch.setenv("GIT_COMMITTER_DATE", stamp)
    return repo.commit(message)


def migration(repo, number, text="SELECT 1;"):
    repo.write(
        f"db/migrations/2027010100{number:04d}_m.sql", f"-- migrate:up\n{text}\n-- migrate:down\n"
    )


def day(n, hour=12):
    return datetime(2027, 1, 1, hour, tzinfo=UTC) + timedelta(days=n)


def history(count):
    """`count` seeded points, one a day from 2027-01-01, oldest first."""
    return [Point(f"{i:040x}", day(i), f"point {i}", True) for i in range(count)]


def picked(picks):
    return [point for point, _ in picks]


# --- Which commits are points ------------------------------------------------------------


def test_points_are_mains_first_parent_commits_that_changed_the_migrations(repo, monkeypatch):
    repo.write("README.md", "start\n")
    commit_on(repo, monkeypatch, day(0), "no migrations yet")
    migration(repo, 1)
    repo.write(SEED, "")
    first = commit_on(repo, monkeypatch, day(1), "first migration")
    repo.git("checkout", "-q", "-b", "side")
    migration(repo, 2)
    commit_on(repo, monkeypatch, day(2), "side: a migration")
    migration(repo, 3)
    commit_on(repo, monkeypatch, day(2, 13), "side: another")
    repo.git("checkout", "-q", "main")
    monkeypatch.setenv("GIT_COMMITTER_DATE", day(3).isoformat())
    repo.git("merge", "--no-ff", "-q", "-m", "Merge side", "side")
    merged = repo.git("rev-parse", "HEAD")
    repo.write("README.md", "docs only\n")
    commit_on(repo, monkeypatch, day(4), "docs only")
    migration(repo, 4)
    commit_on(repo, monkeypatch, day(5), "the tip")

    found = points(repo.path, "main")

    # The merge counts once, as it landed; the side branch's own commits, the commits that left
    # the migrations alone and the tip itself don't.
    assert [(p.sha, p.subject, p.seeded) for p in found] == [
        (first, "first migration", True),
        (merged, "Merge side", True),
    ]
    assert found[0].landed == day(1)


def test_points_stop_where_the_branch_left_main(repo, monkeypatch):
    migration(repo, 1)
    repo.write(SEED, "")
    first = commit_on(repo, monkeypatch, day(0), "first")
    migration(repo, 2)
    left = commit_on(repo, monkeypatch, day(1), "where the branch left")
    repo.git("checkout", "-q", "-b", "branch")
    migration(repo, 5)
    commit_on(repo, monkeypatch, day(3), "the branch's own migration")
    repo.git("checkout", "-q", "main")
    migration(repo, 3)
    commit_on(repo, monkeypatch, day(2), "main moved on")

    # Main's later migrations aren't on the branch, so no upgrade from them to it can work.
    assert [p.sha for p in points(repo.path, "main", head="branch")] == [first, left]


def test_points_before_the_oldest_with_the_seed_are_left_out(repo, monkeypatch):
    migration(repo, 1)
    no_seed = commit_on(repo, monkeypatch, day(0), "before the self-host build")
    migration(repo, 2)
    repo.write(SEED, "")
    seeded = commit_on(repo, monkeypatch, day(1), "the self-host build")
    migration(repo, 3)
    commit_on(repo, monkeypatch, day(2), "the tip")

    found = points(repo.path, "main")

    assert [(p.sha, p.seeded) for p in found] == [(no_seed, False), (seeded, True)]
    assert seedable(found) == ([found[1]], [found[0]])


def test_a_tip_that_brings_the_first_migrations_has_no_earlier_points(repo, monkeypatch):
    repo.write("README.md", "start\n")
    commit_on(repo, monkeypatch, day(0), "no migrations yet")
    migration(repo, 1)
    commit_on(repo, monkeypatch, day(1), "the tip")

    assert points(repo.path, "main") == []


def test_a_point_without_the_seed_stops_the_points_there():
    found = history(5)
    found[1] = Point(found[1].sha, found[1].landed, "the seed was taken out", False)

    assert seedable(found) == (found[2:], found[:2])
    assert seedable(history(3)) == (history(3), [])


# --- Which points a night checks -----------------------------------------------------------


def test_a_short_history_is_checked_whole():
    found = history(5)

    picks = select(found, date(2027, 1, 6), cap=10)

    assert picked(picks) == found
    # 7 days before 01/06 is 12/30, before the first point landed, so the oldest stands in.
    assert picks[0][1] == (
        "the oldest; 7 days back (main is younger); 30 days back (main is younger); "
        "90 days back (main is younger); every point"
    )
    assert [why for _, why in picks[1:]] == ["every point"] * 4


def test_days_back_take_the_newest_point_landed_by_the_end_of_that_day():
    found = [
        Point("a" * 40, datetime(2027, 3, 1, 12, tzinfo=UTC), "oldest", True),
        Point("b" * 40, datetime(2027, 3, 3, 23, 59, tzinfo=UTC), "late on 03/03", True),
        Point("c" * 40, datetime(2027, 3, 4, 0, 0, tzinfo=UTC), "first thing on 03/04", True),
        Point("d" * 40, datetime(2027, 3, 9, tzinfo=UTC), "newest", True),
    ]

    picks = dict(select(found, date(2027, 3, 10), cap=2))

    # 7 days before 03/10 is 03/03: the newest landed by its end.
    assert picks == {found[0]: picks[found[0]], found[1]: "7 days back"}


def test_the_cap_keeps_the_oldest_then_the_days_back_first():
    found = history(200)
    today = date(2027, 7, 20)  # day 200

    assert picked(select(found, today, cap=1)) == [found[0]]
    assert dict(select(found, today, cap=4)) == {
        found[0]: "the oldest",
        found[110]: "90 days back",
        found[170]: "30 days back",
        found[193]: "7 days back",
    }


def test_the_rest_rotate_by_night_so_each_point_comes_round():
    found = history(100)
    first_night = date(2027, 4, 11)
    nights = [first_night + timedelta(days=n) for n in range(17)]

    each = [select(found, night, cap=10) for night in nights]

    # 4 picked by age leave 6 places: one point in 17 a night, so each comes round in 17 nights.
    assert all(len(picks) <= 10 for picks in each)
    assert all(found[0] in picked(picks) for picks in each)
    assert set().union(*(picked(picks) for picks in each)) == set(found)
    assert "one in 17, night " in dict(each[0])[found[(first_night - date(1970, 1, 1)).days % 17]]
    assert all(picked(picks) == sorted(picked(picks), key=found.index) for picks in each)


def test_nothing_to_pick_from_picks_nothing():
    assert select([], date(2027, 1, 1), cap=10) == []


def test_the_nightly_cap_comes_from_the_thresholds():
    thresholds = tomllib.loads((ROOT / "ci/thresholds.toml").read_text())

    assert (
        check_upgrade_history.nightly_bases(ROOT)
        == (thresholds["upgrade_history"]["nightly_bases"])
    )
    assert check_upgrade_history.nightly_bases(ROOT) >= 1


# --- The run --------------------------------------------------------------------------------


@pytest.fixture
def three_points(repo, monkeypatch):
    """A main with three seeded points before its tip, and a stand-in for the database."""
    repo.write(SEED, "")
    shas = []
    for n in range(3):
        migration(repo, n)
        shas.append(commit_on(repo, monkeypatch, day(n), f"point {n}"))
    migration(repo, 9)
    commit_on(repo, monkeypatch, day(5), "the tip")
    monkeypatch.setenv("DATABASE_URL", "postgres://unused")
    monkeypatch.setattr(check_upgrade_history.shutil, "which", lambda name: "/bin/dbmate")
    return shas


def run(repo, *extra):
    return main(["--main", "main", "--root", str(repo.path), "--today", "2027-01-09", *extra])


def test_a_failure_from_any_point_fails_the_run_and_names_it(
    repo, three_points, monkeypatch, capsys
):
    checked = []

    def fake_check(root, base, server_url, dbmate):
        checked.append(base)
        print(f"output of the upgrade from {base[:7]}")
        return ["the upgrade changed money"] if base == three_points[1] else []

    monkeypatch.setattr(check_upgrade, "check", fake_check)

    code = run(repo, "--bases", "10")

    assert code == 1
    assert checked == three_points  # every point, oldest first, the failure didn't stop them
    text = capsys.readouterr().out
    assert f"FAIL: from {three_points[1][:12]} (2027-01-02, point 1): the upgrade" in text
    assert f"output of the upgrade from {three_points[1][:7]}" in text  # its output, kept
    assert f"output of the upgrade from {three_points[0][:7]}" not in text  # passing: one line
    assert f"ok    from {three_points[0][:12]} (2027-01-01, point 0)," in text
    assert text.splitlines()[-1] == (
        f"FAIL: 1 of 3 earlier points of main did not upgrade to this branch with their money "
        f"intact: {three_points[1][:12]}."
    )


def test_a_crash_from_one_point_fails_that_point_only(repo, three_points, monkeypatch, capsys):
    def fake_check(root, base, server_url, dbmate):
        if base == three_points[0]:
            raise RuntimeError("the server went away")
        return []

    monkeypatch.setattr(check_upgrade, "check", fake_check)

    assert run(repo, "--bases", "10") == 1
    out = capsys.readouterr().out
    assert (
        f"FAIL: from {three_points[0][:12]} (2027-01-01, point 0): the check stopped: "
        "RuntimeError: the server went away"
    ) in out
    assert f"ok    from {three_points[2][:12]}" in out
    assert out.rstrip().endswith(f"{three_points[0][:12]}.")


def test_every_point_passing_passes(repo, three_points, monkeypatch, capsys):
    monkeypatch.setattr(check_upgrade, "check", lambda *args: [])

    assert run(repo, "--bases", "10") == 0
    out = capsys.readouterr().out
    assert (
        "Tonight (2027-01-09, at most 10): 3. Repeat it with --today 2027-01-09 --bases 10." in out
    )
    assert (
        "ok: from every earlier point of main checked (3), a database holding money upgrades to "
        "this branch with its money intact (" in out
    )


def test_list_says_which_points_and_checks_none(repo, three_points, monkeypatch, capsys):
    monkeypatch.setattr(check_upgrade, "check", lambda *args: pytest.fail("checked a point"))
    repo.write("ci/thresholds.toml", "[upgrade_history]\nnightly_bases = 2\n")

    assert run(repo, "--list") == 0
    out = capsys.readouterr().out
    assert (
        f"Earlier points of main that changed the migrations, from the oldest that has {SEED}: "
        f"3, the oldest {three_points[0][:12]} (2027-01-01, point 0)." in out
    )
    assert "Tonight (2027-01-09, at most 2): 2." in out  # the cap from the thresholds file
    assert f"  {three_points[0][:12]} (2027-01-01, point 0): the oldest; " in out
    assert f"  {three_points[1][:12]} (2027-01-02, point 1): 7 days back" in out


def test_points_before_the_seed_are_counted_and_named(repo, monkeypatch, capsys):
    migration(repo, 1)
    old = commit_on(repo, monkeypatch, day(0), "phase 0")
    migration(repo, 2)
    repo.write(SEED, "")
    commit_on(repo, monkeypatch, day(1), "self-host")
    migration(repo, 3)
    commit_on(repo, monkeypatch, day(2), "the tip")

    assert run(repo, "--list", "--bases", "5") == 0
    assert (
        f"Not checked, as older than that: 1, the oldest {old[:12]} (2027-01-01, phase 0)."
        in capsys.readouterr().out
    )


def test_no_point_with_the_seed_fails(repo, monkeypatch, capsys):
    migration(repo, 1)
    commit_on(repo, monkeypatch, day(0), "no seed")
    migration(repo, 2)
    commit_on(repo, monkeypatch, day(1), "the tip")

    assert run(repo, "--bases", "5") == 1
    assert (
        f"FAIL: no earlier point of main has {SEED}, so the check proves nothing."
        in capsys.readouterr().out
    )


def test_main_needs_a_database_and_dbmate(repo, three_points, monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL")

    assert run(repo, "--bases", "1") == 1
    assert "needs DATABASE_URL and dbmate" in capsys.readouterr().out


def test_bases_must_be_at_least_one(repo, three_points, capsys):
    with pytest.raises(SystemExit):
        run(repo, "--bases", "0")
    assert "must be at least 1" in capsys.readouterr().err


# --- For real, on the database ---------------------------------------------------------------


def test_for_real_an_old_point_that_a_later_edit_never_reaches_fails_alone(
    repo, monkeypatch, database_url, capsys
):
    """Point 1 has a migration that point 2 edited (as a slip that reached main would): a
    database built at point 1 applied the old text and never runs the new one, so the upgrade
    from point 1 fails and names it, while the one from point 2 runs for real and passes."""
    for path in [
        *ROOT.glob("db/migrations/*.sql"),
        ROOT / "selfhost/__init__.py",
        ROOT / SEED,
        *ROOT.glob("src/**/*.py"),
    ]:
        repo.write(str(path.relative_to(ROOT)), path.read_text())
    repo.write(
        "db/migrations/20991231000001_note.sql", "-- migrate:up\nSELECT 1;\n-- migrate:down\n"
    )
    before_edit = commit_on(repo, monkeypatch, day(0), "a migration")
    repo.write(
        "db/migrations/20991231000001_note.sql",
        "-- migrate:up\nALTER TABLE trust_owners ADD COLUMN note text;\n-- migrate:down\n",
    )
    edited = commit_on(repo, monkeypatch, day(1), "the migration edited")
    repo.write(
        "db/migrations/20991231000002_more.sql",
        "-- migrate:up\nALTER TABLE trust_owners ADD COLUMN more text;\n-- migrate:down\n",
    )
    commit_on(repo, monkeypatch, day(2), "the tip")

    code = main(
        ["--main", "main", "--root", str(repo.path), "--bases", "5", "--today", "2027-01-04"]
    )

    out = capsys.readouterr().out
    assert code == 1
    assert (
        f"FAIL: from {before_edit[:12]} (2027-01-01, a migration): 20991231000001_note.sql is on "
        "the base branch but was edited." in out
    )
    assert f"ok    from {edited[:12]} (2027-01-02, the migration edited)," in out
    assert out.rstrip().endswith(f"intact: {before_edit[:12]}.")
