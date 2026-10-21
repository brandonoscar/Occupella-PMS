# Releases

How a change reaches a company that runs the PMS on its own server. There is no hosted database,
no hosted PMS and no deploy (CLAUDE.md, rule 4): every company builds and runs its own copy next
to its own Postgres 16, so a release is something each company takes, when it chooses, and it
can't be taken back once its migrations have run and money has moved on them.

What the repo does today: every change reaches `main` through a PR whose checks are green
(docs/CI.md); `Dockerfile` and `compose.yaml` build the self-host image from the source; the
image's default command applies the migrations (`dbmate --wait up`). Not yet: no version has
been tagged, there is no `CHANGELOG.md`, no image is published to a registry, and branch
protection on `main` is off (below). The first release starts the tags and the changelog.

## A release

1. **Pick a commit on `main`** whose required checks passed (docs/CI.md, "Required checks") and
   that the night's runs ran on and passed: the property tests at nightly depth, the timing tests,
   the API fuzz, the many-staff and speed runs (ledger-invariants.yml), mutation testing
   (mutation.yml), the self-host build and smoke with Grype (selfhost.yml), and the daily scan
   (daily-scan.yml). A commit that landed after the night's runs waits for the next night.
2. **Write its changelog entry** at the top of `CHANGELOG.md`, from the PRs merged since the last
   tag: what changed for staff, owners and tenants; each new migration, and which change trust
   tables; anything a company must do (a new setting in the environment); and every security fix
   with its advisory. Merge it through a PR like any change.
3. **Tag it** on that commit, an annotated tag named for the year, the month and the release's
   number within the month, from 0: `git tag -a v2026.11.0 -m "v2026.11.0" <commit>`, then
   `git push origin v2026.11.0`. A tag is never moved or deleted: a fix is the next tag.

## The self-host image

Each company builds its image from the tag, on its own machine, with the pinned base images the
`Dockerfile` names: `git checkout v2026.11.0 && docker compose build`. The repo publishes no
image: publishing one needs a registry and a signing key, a secret, and Phase 0 has none
(CLAUDE.md, rule 5). selfhost.yml builds the same image every night and on every PR that
touches it, lists what it holds (Syft) and fails on a known high or critical vulnerability with
a fix (Grype); the daily scan does the same for `main` every morning.

## Migrations

Migrations are plain SQL files in `db/migrations/`, applied in order by dbmate, which records
each in `schema_migrations` and runs each in a transaction of its own: one that fails rolls
itself back and stops the run, and the ones before it stay applied. A committed migration is
never edited or deleted: a database that applied it never runs it again, so an edit would never
reach it (`tools/check_upgrade.py` fails the PR). A correction is a new migration. Every PR
proves its migrations upgrade a database that already holds money and leave that money untouched,
and that the result backs up and restores whole (docs/CI.md, db.yml).

## Upgrading a company

1. Read the changelog entries between the version you run and the one you are taking.
2. Get the code: `git fetch --tags && git checkout v2026.11.0 && docker compose build`.
3. **Back up** (docs/backup.md), and keep that backup apart: it is the only way back if step 5
   fails.
4. Stop the app: `docker compose stop app`.
5. Apply the migrations: `docker compose run --rm migrate`. It lists each one it applied.
6. Start the app: `docker compose up -d --wait app`. It waits until `/health` answers. Then open
   the Dashboard and the trial balance as staff.

Never run `docker compose run --rm smoke` against a company's books: the smoke check seeds a
synthetic 50-door company into whatever database it is given.

## No rollback: a bad release is fixed forward

Once a release's migrations have run and anyone has posted money on them (a payment, the nightly
rent run, a bill paid), there is no going back:
- the trust tables are append-only, and ledger migrations refuse to roll back (db.yml checks it):
  a down migration would drop or rewrite trust records;
- the backup from before the upgrade doesn't have what was posted since;
- the previous release's code was never tested against the newer schema.

So a bad release is fixed forward: a new migration and code that correct it, through a PR like
any other, then a new tag that companies take. A mistake in the money itself is corrected the
way the ledger corrects anything: with a new, reversing transfer, never by editing a row.

The only way back is before anything was posted: when step 5 failed, or the app wouldn't start
after it. Then keep the app stopped, restore the backup from step 3 into a new database
(docs/backup.md, "Getting the books back"), check out the tag you ran before, rebuild, and start
it. Then report what failed.

## Branch protection for `main`

**Off today**: GitHub's API reports `main` as not protected (checked 2026-10-21). Nothing stops a
push to `main` that skips the checks, a force push or the branch's deletion. An admin of the
repository turns it on; these are the settings.

| Setting | Value |
|---|---|
| Branch name pattern | `main` |
| Require a pull request before merging | on |
| Require approvals | off: the repo has one maintainer, and GitHub doesn't let an author approve their own PR. Turn it on (1) once a second person reviews |
| Require status checks to pass before merging | on |
| Require branches to be up to date before merging | on (recommended): a PR's checks then ran against `main` as it is, its migrations after every one already merged. The cost: after each merge, every open PR merges `main` in and runs its checks again (some 30 minutes) |
| Required status checks | `ci`, `email (Mailpit)`, `migrations`, `properties`, `golden`, `coverage`, `codeql`, `deps-and-secrets`, `licenses`, `e2e / e2e` |
| Do not allow bypassing the above settings | on: admins follow them too |
| Allow force pushes | off |
| Allow deletions | off |

The required checks are the jobs docs/CI.md lists, by the names their check runs carry on a PR
(read from PR #140's checks on 2026-10-21). Not required, since they don't run on every PR:
`selfhost (PR) / selfhost` (only when the self-host build changes) and `daily scan` (only when
`daily-scan.yml` changes).

The clicks:
1. On GitHub, open the repository, then **Settings**, then **Branches** (under "Code and
   automation").
2. **Add classic branch protection rule**. (A ruleset, **Add branch ruleset**, holds the same
   settings under other names; the classic rule is shown here.)
3. **Branch name pattern**: `main`.
4. Tick **Require a pull request before merging**. Under it, untick **Require approvals**.
5. Tick **Require status checks to pass before merging**, and under it **Require branches to be
   up to date before merging**. In the search box, add each check from the table, one at a time:
   `ci`, `email (Mailpit)`, `migrations`, `properties`, `golden`, `coverage`, `codeql`,
   `deps-and-secrets`, `licenses`, `e2e / e2e`. Pick the ones GitHub Actions runs: code scanning
   also posts a check named `CodeQL`, which is not the job. GitHub only offers a check that ran on
   the repository in the last week.
6. Tick **Do not allow bypassing the above settings**.
7. Leave **Allow force pushes** and **Allow deletions** unticked.
8. **Create**, and confirm with your password or passkey if asked.

To check it took: **Settings**, **Branches** lists the rule for `main`; a direct `git push` to
`main` is refused; and a PR's merge button stays grey until the ten checks pass.
